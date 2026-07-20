"""Customer-facing account reads: billing history + usage rollups (Phase 4 #2).

The customer dashboard's window onto the same `invoices` / `usage_daily` tables
the admin site reads cross-org. Auth is resolve_org (dashboard session cookie OR
SDK Bearer key) — both paths set the RLS GUC, and every query still carries an
explicit WHERE org_id because the app-level filter is the load-bearing tenant
isolation (the docker superuser role bypasses RLS).
"""

from __future__ import annotations

import secrets
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import ip_allow
from ..auth import require_role, resolve_org
from ..db import get_db
from ..models import Invoice, Organization, UsageDaily, User

router = APIRouter()


class InvoiceItem(BaseModel):
    id: str
    stripe_invoice_id: str
    amount_cents: int
    currency: str
    status: str
    period_start: str | None = None
    period_end: str | None = None
    created_at: str | None = None


class UsageDay(BaseModel):
    day: str
    logs_count: int
    tokens_sum: int
    breach_count: int
    graded_count: int
    failed_count: int
    pending_count: int


class EvaluationAccess(BaseModel):
    label: str = "Premium judge access"
    active: bool
    capture_available: bool
    expires_at: str | None = None
    credits_total: int
    credits_used: int
    credits_remaining: int


class UsageQuota(BaseModel):
    plan_tier: str | None = None
    monthly_log_quota: int | None = None   # NULL = unlimited
    used_this_month: int = 0
    remaining: int | None = None           # NULL when unlimited
    usage_pct: int | None = None           # 0..100+, NULL when unlimited
    over_quota: bool = False               # soft flag — ingestion is NEVER blocked


    evaluation: EvaluationAccess | None = None


class UsageResponse(BaseModel):
    quota: UsageQuota
    days: list[UsageDay]


@router.get("/v1/invoices", response_model=list[InvoiceItem])
def list_invoices(
    org: Organization = Depends(resolve_org),
    db: Session = Depends(get_db),
    limit: int = Query(default=24, ge=1, le=100),
):
    """This org's billing history (populated by the Stripe webhook), newest first."""
    rows = db.execute(
        select(Invoice)
        .where(Invoice.org_id == org.id)
        .order_by(Invoice.created_at.desc())
        .limit(limit)
    ).scalars().all()
    return [
        InvoiceItem(
            id=str(i.id), stripe_invoice_id=i.stripe_invoice_id,
            amount_cents=i.amount_cents, currency=i.currency, status=i.status,
            period_start=i.period_start.isoformat() if i.period_start else None,
            period_end=i.period_end.isoformat() if i.period_end else None,
            created_at=i.created_at.isoformat() if i.created_at else None,
        )
        for i in rows
    ]


@router.get("/v1/usage", response_model=UsageResponse)
def get_usage(
    org: Organization = Depends(resolve_org),
    db: Session = Depends(get_db),
    days: int = Query(default=30, ge=1, le=90),
):
    """Per-day usage from the worker-maintained usage_daily rollup (never scans
    the raw ledger), plus quota headroom against organizations.monthly_log_quota."""
    today = date.today()
    since = today - timedelta(days=days - 1)

    rows = db.execute(
        select(UsageDaily)
        .where(UsageDaily.org_id == org.id, UsageDaily.day >= since)
        .order_by(UsageDaily.day.asc())
    ).scalars().all()

    month_start = today.replace(day=1)
    used_this_month = db.execute(
        select(func.coalesce(func.sum(UsageDaily.logs_count), 0))
        .where(UsageDaily.org_id == org.id, UsageDaily.day >= month_start)
    ).scalar_one()

    quota = org.monthly_log_quota
    used = int(used_this_month)
    evaluation = None
    if org.evaluation_offer_id and org.evaluation_credit_limit is not None:
        now = datetime.now(timezone.utc)
        active = not org.evaluation_ends_at or now < org.evaluation_ends_at
        credits_used = max(0, org.evaluation_credits_used)
        credits_remaining = (max(0, org.evaluation_credit_limit - credits_used)
                             if active else 0)
        evaluation = EvaluationAccess(
            active=active,
            capture_available=bool(active and credits_remaining > 0),
            expires_at=org.evaluation_ends_at.isoformat() if org.evaluation_ends_at else None,
            credits_total=org.evaluation_credit_limit,
            credits_used=credits_used,
            credits_remaining=credits_remaining,
        )
    return UsageResponse(
        quota=UsageQuota(
            plan_tier=org.plan_tier,
            monthly_log_quota=quota,
            used_this_month=used,
            remaining=None if quota is None else max(0, quota - used),
            usage_pct=None if not quota else round(used / quota * 100),
            over_quota=bool(quota is not None and used >= quota),
            evaluation=evaluation,
        ),
        days=[
            UsageDay(
                day=r.day.isoformat(), logs_count=r.logs_count, tokens_sum=r.tokens_sum,
                breach_count=r.breach_count, graded_count=r.graded_count,
                failed_count=r.failed_count, pending_count=r.pending_count,
            )
            for r in rows
        ],
    )


class DeleteWorkspaceRequest(BaseModel):
    confirm_name: str


@router.post("/v1/account/delete")
def delete_workspace(
    payload: DeleteWorkspaceRequest,
    request: Request,
    admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Soft-delete the caller's workspace: set organizations.deleted_at. Every auth
    path then refuses the org (SDK key, new logins, existing sessions), reversibly
    (data retained). confirm_name must match the workspace name (accident guard);
    admin-only via require_role."""
    org = db.get(Organization, admin.org_id)
    if org is None or org.deleted_at is not None:
        raise HTTPException(status_code=404, detail="workspace not found")
    if payload.confirm_name.strip() != org.name:
        raise HTTPException(status_code=400,
                            detail="confirm_name does not match the workspace name")
    org.deleted_at = datetime.now(timezone.utc)
    db.commit()
    request.session.clear()          # the admin's own session is now for a deleted org
    return {"status": "workspace_deleted", "org_id": str(org.id)}


class IpAllowlistRequest(BaseModel):
    allowlist: str = ""              # comma-separated IPs/CIDRs; empty clears the restriction


@router.post("/v1/account/ip-allowlist")
def set_ip_allowlist(
    payload: IpAllowlistRequest,
    request: Request,
    admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Set the org's DASHBOARD IP allow-list (admin-only). Refuses a non-empty
    list that wouldn't include the caller's own IP — a self-lockout guard."""
    entries = ip_allow.parse_allowlist(payload.allowlist)
    if entries and not ip_allow.ip_allowed(ip_allow.client_ip(request), entries):
        raise HTTPException(status_code=400,
                            detail="that allow-list would lock you out — include your current IP")
    org = db.get(Organization, admin.org_id)
    org.ip_allowlist = ", ".join(entries) if entries else None
    db.commit()
    return {"status": "ok", "ip_allowlist": org.ip_allowlist or ""}


@router.post("/v1/account/badge")
def mint_badge(
    request: Request,
    admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Mint (or return the existing) public trust-badge token for this org — admin
    only. Embed the returned SVG URL anywhere via <img>. Aggregate status only, so
    the badge never exposes tenant data. (Phase 6 · 6C)"""
    org = db.get(Organization, admin.org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="workspace not found")
    if not org.public_badge_token:
        org.public_badge_token = secrets.token_urlsafe(24)
        db.commit()
    return {"token": org.public_badge_token, "url": f"/v1/badge/{org.public_badge_token}.svg"}


@router.delete("/v1/account/badge")
def revoke_badge(
    request: Request,
    admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Revoke the org's public trust badge — the old token immediately 404s. (6C)"""
    org = db.get(Organization, admin.org_id)
    if org is not None and org.public_badge_token:
        org.public_badge_token = None
        db.commit()
    return {"status": "revoked"}
