"""Cross-org management for the internal admin site ("site 3").

Mounted under /admin, so paths resolve to /admin/v1/organizations*. Every handler
is guarded by require_platform_role (staff-only); reads are cross-org (no RLS GUC
is set — the docker `foxy` superuser bypasses FORCE RLS, so staff see all orgs).
Every state-changing action writes an admin_actions row in the same transaction.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..admin_audit import client_ip, record_admin_action
from ..auth import require_platform_role, set_org_scope_for_staff
from ..db import get_db
from ..models import ApiKey, AuditLog, Organization, StaffUser, User

router = APIRouter()


class OrgListItem(BaseModel):
    id: str
    name: str
    plan_tier: str | None = None
    subscription_status: str | None = None
    suspended: bool = False
    deleted: bool = False
    contact_email: str | None = None
    created_at: str | None = None


class OrgDetail(OrgListItem):
    suspended_reason: str | None = None
    monthly_log_quota: int | None = None
    user_count: int = 0
    api_key_count: int = 0
    total_logs: int = 0
    breaches: int = 0


class SuspendRequest(BaseModel):
    reason: str | None = None


def _list_item(o: Organization) -> OrgListItem:
    return OrgListItem(
        id=str(o.id), name=o.name, plan_tier=o.plan_tier,
        subscription_status=o.subscription_status, suspended=bool(o.suspended),
        deleted=o.deleted_at is not None, contact_email=o.contact_email,
        created_at=o.created_at.isoformat() if o.created_at else None,
    )


@router.get("/v1/organizations", response_model=list[OrgListItem])
def list_organizations(
    staff: StaffUser = Depends(require_platform_role("viewer")),
    db: Session = Depends(get_db),
    include_deleted: bool = False,
):
    """All tenants (staff cross-org view). Soft-deleted orgs are hidden unless
    include_deleted=true so staff can still audit an offboarded tenant."""
    stmt = select(Organization).order_by(Organization.created_at.desc())
    if not include_deleted:
        stmt = stmt.where(Organization.deleted_at.is_(None))
    return [_list_item(o) for o in db.execute(stmt).scalars().all()]


@router.get("/v1/organizations/{org_id}", response_model=OrgDetail)
def get_organization(
    org_id: str,
    staff: StaffUser = Depends(require_platform_role("viewer")),
    db: Session = Depends(get_db),
):
    try:
        oid = uuid.UUID(str(org_id))
    except ValueError:
        raise HTTPException(status_code=422, detail="invalid org id")
    org = db.get(Organization, oid)
    if org is None:
        raise HTTPException(status_code=404, detail="organization not found")

    user_count = db.execute(
        select(func.count()).select_from(User).where(User.org_id == oid)
    ).scalar_one()
    key_count = db.execute(
        select(func.count()).select_from(ApiKey).where(ApiKey.org_id == oid)
    ).scalar_one()

    # Drill into this ONE org's RLS-protected ledger via the deliberate scope
    # helper (correct even under a future non-superuser role).
    set_org_scope_for_staff(db, oid)
    total_logs = db.execute(
        select(func.count()).select_from(AuditLog).where(AuditLog.org_id == oid)
    ).scalar_one()
    breaches = db.execute(
        select(func.count()).select_from(AuditLog)
        .where(AuditLog.org_id == oid,
               AuditLog.gemini_verdict["policy_breach"].astext == "true")
    ).scalar_one()

    base = _list_item(org).model_dump()
    return OrgDetail(**base, suspended_reason=org.suspended_reason,
                     monthly_log_quota=org.monthly_log_quota, user_count=user_count,
                     api_key_count=key_count, total_logs=total_logs, breaches=breaches)


def _load_active_org(db: Session, org_id: str) -> Organization:
    try:
        oid = uuid.UUID(str(org_id))
    except ValueError:
        raise HTTPException(status_code=422, detail="invalid org id")
    org = db.get(Organization, oid)
    if org is None:
        raise HTTPException(status_code=404, detail="organization not found")
    return org


@router.post("/v1/organizations/{org_id}/suspend")
def suspend_organization(
    org_id: str,
    body: SuspendRequest,
    request: Request,
    staff: StaffUser = Depends(require_platform_role("operator")),
    db: Session = Depends(get_db),
):
    """Put a staff access-hold on an org (independent of Stripe status)."""
    org = _load_active_org(db, org_id)
    org.suspended = True
    org.suspended_reason = (body.reason or "").strip()[:255] or None
    record_admin_action(db, staff, "org.suspend", target_org_id=org.id,
                        target_type="organization", target_id=str(org.id),
                        detail={"reason": org.suspended_reason}, ip=client_ip(request))
    db.commit()
    return {"status": "suspended", "org_id": str(org.id)}


@router.post("/v1/organizations/{org_id}/enable")
def enable_organization(
    org_id: str,
    request: Request,
    staff: StaffUser = Depends(require_platform_role("operator")),
    db: Session = Depends(get_db),
):
    """Lift a staff access-hold."""
    org = _load_active_org(db, org_id)
    org.suspended = False
    org.suspended_reason = None
    record_admin_action(db, staff, "org.enable", target_org_id=org.id,
                        target_type="organization", target_id=str(org.id),
                        ip=client_ip(request))
    db.commit()
    return {"status": "enabled", "org_id": str(org.id)}
