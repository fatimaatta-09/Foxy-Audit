"""Admin visibility and recovery for grading dead-letter rows."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..admin_audit import client_ip, record_admin_action
from ..auth import require_platform_role, set_org_scope_for_staff
from ..db import get_db
from ..models import AuditLog, Organization, StaffUser

router = APIRouter()


def _uuid(value: str, label: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"invalid {label}") from exc


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _failure_detail(row: AuditLog) -> str | None:
    """Return only an explicitly recorded, bounded failure field."""
    verdict = row.gemini_verdict
    if not isinstance(verdict, dict):
        return None
    for key in ("failure_detail", "error", "detail", "reason"):
        value = verdict.get(key)
        if value:
            return str(value)[:500]
    return None


def _item(row: AuditLog, org_name: str | None) -> dict:
    return {
        "id": str(row.id),
        "org_id": str(row.org_id),
        "org_name": org_name,
        "seq": row.seq,
        "policy_tag": row.policy_tag,
        "grading_status": row.grading_status,
        "grading_attempts": row.grading_attempts,
        "grading_started_at": _iso(row.grading_started_at),
        "created_at": _iso(row.created_at),
        "graded_at": _iso(row.graded_at),
        "failure_detail": _failure_detail(row),
    }


@router.get("/v1/grading/deadletter")
def list_dead_letters(
    org_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
    staff=Depends(require_platform_role("viewer")),
    db: Session = Depends(get_db),
):
    limit = min(max(limit, 1), 500)
    offset = max(offset, 0)
    oid = _uuid(org_id, "org id") if org_id else None
    if oid is not None:
        # Deliberately reuse the tenant RLS path for a filtered drill-down.
        set_org_scope_for_staff(db, oid)

    base = select(AuditLog).where(AuditLog.grading_status == "failed")
    if oid is not None:
        base = base.where(AuditLog.org_id == oid)
    total = db.execute(
        select(func.count()).select_from(base.subquery())
    ).scalar_one()
    rows = db.execute(
        base.order_by(AuditLog.created_at.desc(), AuditLog.seq.desc())
        .offset(offset).limit(limit)
    ).scalars().all()
    org_ids = {row.org_id for row in rows}
    names = {}
    if org_ids:
        names = dict(db.execute(
            select(Organization.id, Organization.name)
            .where(Organization.id.in_(org_ids))
        ).all())
    return {
        "items": [_item(row, names.get(row.org_id)) for row in rows],
        "total": int(total),
        "limit": limit,
        "offset": offset,
    }


@router.post("/v1/grading/deadletter/{row_id}/requeue")
def requeue_dead_letter(
    row_id: str,
    request: Request,
    staff=Depends(require_platform_role("operator")),
    db: Session = Depends(get_db),
):
    row = db.get(AuditLog, _uuid(row_id, "grading row id"))
    if row is None:
        raise HTTPException(status_code=404, detail="grading row not found")
    if row.grading_status != "failed":
        raise HTTPException(status_code=409, detail="grading row is not in the dead-letter queue")

    attempts = row.grading_attempts
    row.grading_status = "pending"
    row.grading_started_at = None
    row.graded_at = None
    record_admin_action(
        db, staff, "grading.requeue", target_org_id=row.org_id,
        target_type="audit_log", target_id=str(row.id),
        detail={"previous_status": "failed", "grading_attempts": attempts},
        ip=client_ip(request),
    )
    db.commit()
    return {"status": "pending", "id": str(row.id)}
