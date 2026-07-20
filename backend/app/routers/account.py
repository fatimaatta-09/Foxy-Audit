"""Customer-facing account reads: billing history + usage rollups (Phase 4 #2).

The customer dashboard's window onto the same `invoices` / `usage_daily` tables
the admin site reads cross-org. Auth is resolve_org (dashboard session cookie OR
SDK Bearer key) — both paths set the RLS GUC, and every query still carries an
explicit WHERE org_id because the app-level filter is the load-bearing tenant
isolation (the docker superuser role bypasses RLS).
"""

from __future__ import annotations

import json
import logging
import secrets
import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import account_audit, ip_allow
from ..auth import require_role, require_user, resolve_org
from ..config import get_settings
from ..db import get_db
from ..models import (
    AccountAction, ApiKey, AuditLog, ChainAnchor, ExportJob, Invoice, Organization,
    OrgPolicy, UsageDaily, User,
)

log = logging.getLogger("foxy.account")
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


class UsageQuota(BaseModel):
    plan_tier: str | None = None
    monthly_log_quota: int | None = None   # NULL = unlimited
    used_this_month: int = 0
    remaining: int | None = None           # NULL when unlimited
    usage_pct: int | None = None           # 0..100+, NULL when unlimited
    over_quota: bool = False
    credit_unit: str = "audit_event"
    credits_included: int | None = None
    credits_used: int = 0
    credits_remaining: int | None = None
    trial_ends_at: str | None = None
    trial_active: bool = False
    seat_limit: int | None = None
    active_seats: int = 0
    api_key_limit: int | None = None
    active_api_keys: int = 0
    ingestion_blocked: bool = False


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
    month_start_dt = datetime.combine(month_start, datetime.min.time(), tzinfo=timezone.utc)
    used_this_month = db.execute(
        select(func.count()).select_from(AuditLog)
        .where(AuditLog.org_id == org.id, AuditLog.created_at >= month_start_dt)
    ).scalar_one()

    quota = org.monthly_log_quota
    used = int(used_this_month)
    now = datetime.now(timezone.utc)
    trial_active = bool(
        (org.plan_tier or "").lower() == "free"
        and org.trial_ends_at is not None
        and now < org.trial_ends_at
    )
    inactive_subscription = (
        (org.plan_tier or "").lower() not in {"", "free"}
        and org.subscription_status in {"cancelled", "unpaid"}
    )
    seat_limit = get_settings().seat_limit_for(org.plan_tier)
    api_key_limit = get_settings().api_key_limit_for(org.plan_tier)
    active_seats = db.execute(
        select(func.count()).select_from(User).where(
            User.org_id == org.id, User.disabled.is_(False))
    ).scalar_one()
    active_keys = db.execute(
        select(func.count()).select_from(ApiKey).where(
            ApiKey.org_id == org.id, ApiKey.status == "active")
    ).scalar_one()
    ingestion_blocked = bool(
        ((org.plan_tier or "").lower() == "free" and org.trial_ends_at is not None
         and now >= org.trial_ends_at)
        or inactive_subscription
        or (quota is not None and used >= quota)
    )
    return UsageResponse(
        quota=UsageQuota(
            plan_tier=org.plan_tier,
            monthly_log_quota=quota,
            used_this_month=used,
            remaining=None if quota is None else max(0, quota - used),
            usage_pct=None if not quota else round(used / quota * 100),
            over_quota=bool(quota is not None and used >= quota),
            credits_included=quota,
            credits_used=used,
            credits_remaining=None if quota is None else max(0, quota - used),
            trial_ends_at=org.trial_ends_at.isoformat() if org.trial_ends_at else None,
            trial_active=trial_active,
            seat_limit=seat_limit,
            active_seats=int(active_seats),
            api_key_limit=api_key_limit,
            active_api_keys=int(active_keys),
            ingestion_blocked=ingestion_blocked,
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


# ─────────────── account audit log (P2 · §D) ───────────────────────────────

class AccountActionItem(BaseModel):
    id: str
    actor_email: str | None = None
    action: str
    target: str | None = None
    detail: dict | None = None
    created_at: str | None = None


@router.get("/v1/account/audit", response_model=list[AccountActionItem])
def account_audit_log(
    admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=500),
):
    """The org's own account-action trail (key/policy/member/MFA changes),
    newest first — admin only, org-scoped by RLS."""
    rows = db.execute(
        select(AccountAction).where(AccountAction.org_id == admin.org_id)
        .order_by(AccountAction.created_at.desc()).limit(limit)
    ).scalars().all()
    return [AccountActionItem(
        id=str(a.id), actor_email=a.actor_email, action=a.action, target=a.target,
        detail=a.detail, created_at=a.created_at.isoformat() if a.created_at else None,
    ) for a in rows]


# ─────────────── full account / GDPR export (P2 · §H) ──────────────────────

@router.get("/v1/account/export")
def account_export(
    admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Self-serve, machine-readable export of everything this workspace holds —
    org profile, users, policy, keys (metadata only, never the secret), invoices,
    anchors, and the full hash-chain ledger. Admin only. Content-blind: the ledger
    carries only hashes + verdicts, never prompt/response text."""
    org = db.get(Organization, admin.org_id)
    users = db.execute(select(User).where(User.org_id == admin.org_id)).scalars().all()
    policy = db.get(OrgPolicy, admin.org_id)
    keys = db.execute(select(ApiKey).where(ApiKey.org_id == admin.org_id)).scalars().all()
    invoices = db.execute(select(Invoice).where(Invoice.org_id == admin.org_id)).scalars().all()
    anchors = db.execute(select(ChainAnchor).where(ChainAnchor.org_id == admin.org_id)).scalars().all()
    logs = db.execute(
        select(AuditLog).where(AuditLog.org_id == admin.org_id)
        .order_by(AuditLog.seq.asc())
    ).scalars().all()

    def _iso(v):
        return v.isoformat() if v else None

    bundle = {
        "organization": {
            "id": str(org.id), "name": org.name, "plan_tier": org.plan_tier,
            "subscription_status": org.subscription_status,
            "contact_email": org.contact_email,
            "monthly_log_quota": org.monthly_log_quota,
            "created_at": _iso(org.created_at),
        } if org else None,
        "users": [{"email": u.email, "role": u.role, "disabled": u.disabled,
                   "mfa_enabled": u.mfa_enabled} for u in users],
        "policy": {
            "pii_detection": policy.pii_detection, "prompt_injection": policy.prompt_injection,
            "regulated_data_mode": policy.regulated_data_mode,
            "max_token_threshold": policy.max_token_threshold,
            "enforcement_mode": policy.enforcement_mode,
            "notify_on_breach": policy.notify_on_breach,
        } if policy else None,
        "api_keys": [{"name": k.name, "key_prefix": k.key_prefix, "status": k.status,
                      "created_at": _iso(k.created_at), "last_used_at": _iso(k.last_used_at),
                      "expires_at": _iso(k.expires_at)} for k in keys],
        "invoices": [{"stripe_invoice_id": i.stripe_invoice_id, "amount_cents": i.amount_cents,
                      "currency": i.currency, "status": i.status,
                      "created_at": _iso(i.created_at)} for i in invoices],
        "anchors": [{"root_hash": a.root_hash, "last_seq": a.last_seq, "chain": a.chain,
                     "tx_hash": a.tx_hash, "status": a.status,
                     "anchored_at": _iso(a.anchored_at)} for a in anchors],
        "ledger": [{"seq": r.seq, "prompt_hash": r.prompt_hash, "response_hash": r.response_hash,
                    "policy_tag": r.policy_tag, "agent": r.agent, "chain_hash": r.chain_hash,
                    "grading_status": r.grading_status, "gemini_verdict": r.gemini_verdict,
                    "created_at": _iso(r.created_at)} for r in logs],
    }
    fname = f"foxy-account-export-{admin.org_id}.json"
    return Response(content=json.dumps(bundle, indent=2, default=str),
                    media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


# ─────────────── invoice PDF link (P2 · §E) ────────────────────────────────

@router.get("/v1/invoices/{invoice_id}/link")
def invoice_link(
    invoice_id: str,
    admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Resolve a Stripe hosted-invoice / PDF URL for one of the org's invoices.
    503 when billing isn't configured; 404 for an unknown invoice."""
    s = get_settings()
    if not s.stripe_secret_key:
        raise HTTPException(status_code=503, detail="billing not configured")
    try:
        iid = uuid.UUID(str(invoice_id))
    except ValueError:
        raise HTTPException(status_code=422, detail="invalid invoice id")
    inv = db.execute(
        select(Invoice).where(Invoice.id == iid, Invoice.org_id == admin.org_id)
    ).scalar_one_or_none()
    if inv is None:
        raise HTTPException(status_code=404, detail="invoice not found")
    try:
        import stripe
        stripe.api_key = s.stripe_secret_key
        obj = stripe.Invoice.retrieve(inv.stripe_invoice_id)
        url = obj.get("hosted_invoice_url") or obj.get("invoice_pdf")
        if not url:
            raise HTTPException(status_code=404, detail="no hosted invoice available")
        return {"url": url}
    except HTTPException:
        raise
    except Exception as exc:                 # noqa: BLE001
        log.warning("invoice link failed: %s", exc)
        raise HTTPException(status_code=502, detail="could not fetch invoice link")


# ─────────────────────────── onboarding checklist (P4) ───────────────────────
class OnboardingUpdate(BaseModel):
    dismissed: bool | None = None


def _onboarding_state(user: User, db: Session) -> dict:
    """Compute the checklist live from real data (active key / first logged call / team>1) and merge
    the persisted dismissal. Never fabricated — every step reflects the org's actual data."""
    has_key = db.execute(
        select(func.count()).select_from(ApiKey)
        .where(ApiKey.org_id == user.org_id, ApiKey.status == "active")
    ).scalar_one() > 0
    logged = db.execute(
        select(func.count()).select_from(AuditLog).where(AuditLog.org_id == user.org_id)
    ).scalar_one() > 0
    team = db.execute(
        select(func.count()).select_from(User).where(User.org_id == user.org_id)
    ).scalar_one() > 1
    steps = [
        {"key": "api_key", "done": has_key, "title": "Create an API key",
         "desc": "Mint a key for your app or SDK — shown once.",
         "page": "keys", "label": "Create key"},
        {"key": "first_log", "done": logged, "title": "Log your first AI interaction",
         "desc": "Wrap a call with the @foxy.audit SDK; only hashes leave your machine.",
         "page": "keys", "label": "Get started"},
        {"key": "invite_team", "done": team, "title": "Invite your team",
         "desc": "Add a teammate so they can review the audit trail.",
         "page": "settings", "label": "Invite"},
    ]
    state = user.onboarding_state or {}
    return {
        "steps": steps,
        "done": sum(1 for s in steps if s["done"]),
        "total": len(steps),
        "complete": has_key and logged,          # essentials live → the checklist retires
        "dismissed": bool(state.get("dismissed")),
    }


@router.get("/v1/onboarding")
def get_onboarding(user: User = Depends(require_user), db: Session = Depends(get_db)):
    """The onboarding checklist (live completion + persisted dismissal)."""
    return _onboarding_state(user, db)


@router.put("/v1/onboarding")
def put_onboarding(body: OnboardingUpdate, user: User = Depends(require_user),
                   db: Session = Depends(get_db)):
    """Persist the onboarding state (currently: dismissal). Per-user UI preference."""
    state = dict(user.onboarding_state or {})
    if body.dismissed is not None:
        state["dismissed"] = bool(body.dismissed)
    user.onboarding_state = state
    resp = _onboarding_state(user, db)   # compute BEFORE commit — require_user's RLS GUC is still set
    db.commit()
    return resp


# ─────────────────────────── export history / jobs (P11) ─────────────────────
_EXPORT_TYPES = {"passport", "logs_csv", "logs_json"}


class ExportCreate(BaseModel):
    type: str
    params: dict | None = None


def _export_dict(j: ExportJob) -> dict:
    return {
        "id": str(j.id), "type": j.type, "params": j.params or {}, "status": j.status,
        "requested_by": j.requested_by,
        "created_at": j.created_at.isoformat() if j.created_at else None,
        "completed_at": j.completed_at.isoformat() if j.completed_at else None,
    }


@router.post("/v1/exports")
def create_export(body: ExportCreate, user: User = Depends(require_user),
                  db: Session = Depends(get_db)):
    """Record a compliance export in the history/audit trail. The file itself is produced by the
    existing /v1/logs/export or /v1/passport endpoints — the server keeps NO archive, so this is the
    who/what/when record (also mirrored into account_actions)."""
    t = (body.type or "").strip()
    if t not in _EXPORT_TYPES:
        raise HTTPException(status_code=422, detail="unknown export type")
    now = datetime.now(timezone.utc)
    job = ExportJob(id=uuid.uuid4(), org_id=user.org_id, requested_by=user.email, type=t,
                    params=(body.params or {}), status="completed",
                    created_at=now, completed_at=now)
    db.add(job)
    account_audit.record_account_action(
        db, org_id=user.org_id, actor_email=user.email, action="export.create", target=t)
    db.commit()
    return _export_dict(job)


@router.get("/v1/exports")
def list_exports(user: User = Depends(require_user), db: Session = Depends(get_db)):
    """The org's recent export history (newest first)."""
    rows = db.execute(
        select(ExportJob).where(ExportJob.org_id == user.org_id)
        .order_by(ExportJob.created_at.desc()).limit(100)
    ).scalars().all()
    return {"items": [_export_dict(j) for j in rows]}


@router.get("/v1/exports/{export_id}")
def get_export(export_id: str, user: User = Depends(require_user), db: Session = Depends(get_db)):
    try:
        eid = uuid.UUID(export_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="not found")
    j = db.get(ExportJob, eid)          # RLS-scoped by require_user → cross-org rows are invisible
    if j is None or j.org_id != user.org_id:
        raise HTTPException(status_code=404, detail="not found")
    return _export_dict(j)
