"""Customer-facing account reads: billing history + usage rollups (Phase 4 #2).

The customer dashboard's window onto the same `invoices` table the admin site
reads cross-org, plus per-day usage aggregated from `audit_logs` itself. Auth is resolve_org (dashboard session cookie OR
SDK Bearer key) — both paths set the RLS GUC, and every query still carries an
explicit WHERE org_id because the app-level filter is the load-bearing tenant
isolation (the docker superuser role bypasses RLS).
"""

from __future__ import annotations

import json
import logging
import pathlib
import secrets
import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import (
    APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import Date, func, select
from sqlalchemy.orm import Session

from .. import account_audit, billing_state, ip_allow
from ..auth import require_role, require_step_up_user, require_user, resolve_org
from ..config import get_settings
from ..db import get_db
from ..models import (
    AccountAction, ApiKey, AuditLog, ChainAnchor, ExportJob, Invoice, Notification,
    Organization, OrgPolicy, User,
)
from .logs import limiter          # the app's single Limiter instance

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
    """Per-day usage straight from audit_logs, plus quota headroom against
    organizations.monthly_log_quota.

    This used to read the worker-maintained `usage_daily` rollup, which
    recomputes only a rolling 48-hour window (usage.py `_ROLLUP_SQL`). That is
    correct for the day it was designed around and wrong for everything else:
    any day older than ~2 days keeps whatever partial counts were true when the
    worker last touched it, so a 30- or 90-day chart understated real history —
    silently, and always downwards. `used_this_month` below already read
    audit_logs for exactly this reason, so the same endpoint was serving a
    correct quota number beside an understated chart.

    audit_logs is append-only and org-scoped, so aggregating it is the source of
    truth. The weekly digest reached the same conclusion independently
    (user_notifications.py `_WEEK_TOTALS_SQL`); this reuses that approach with
    the rollup's own expressions, so the numbers agree by construction."""
    today = date.today()
    since = today - timedelta(days=days - 1)
    since_dt = datetime.combine(since, datetime.min.time(), tzinfo=timezone.utc)

    day_col = func.date_trunc("day", AuditLog.created_at).cast(Date).label("day")
    rows = db.execute(
        select(
            day_col,
            func.count().label("logs_count"),
            func.coalesce(func.sum(AuditLog.token_count), 0).label("tokens_sum"),
            func.count().filter(
                AuditLog.gemini_verdict["policy_breach"].astext == "true"
            ).label("breach_count"),
            func.count().filter(
                AuditLog.grading_status == "graded").label("graded_count"),
            func.count().filter(
                AuditLog.grading_status == "failed").label("failed_count"),
            func.count().filter(
                AuditLog.grading_status == "pending").label("pending_count"),
        )
        .where(AuditLog.org_id == org.id, AuditLog.created_at >= since_dt)
        .group_by(day_col)
        .order_by(day_col.asc())
    ).all()

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
    # Same source of truth as the gate that actually rejects the write (D1), so
    # this widget can no longer say "capturing" while /v1/logs returns 402.
    capture_blocked = billing_state.capture_block(org, now)
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
        capture_blocked is not None
        or (quota is not None and used >= quota)
    )
    evaluation = None
    if org.evaluation_offer_id and org.evaluation_credit_limit is not None:
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
            evaluation=evaluation,
        ),
        days=[
            UsageDay(
                day=r.day.isoformat(), logs_count=r.logs_count,
                tokens_sum=int(r.tokens_sum),
                breach_count=r.breach_count, graded_count=r.graded_count,
                failed_count=r.failed_count, pending_count=r.pending_count,
            )
            for r in rows
        ],
    )


class DeleteWorkspaceRequest(BaseModel):
    confirm_name: str


@router.post("/v1/account/delete", dependencies=[Depends(require_step_up_user)])
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


@router.post("/v1/account/org-id", dependencies=[Depends(require_step_up_user)])
def reveal_org_id(user: User = Depends(require_user), db: Session = Depends(get_db)):
    """The workspace's organisation ID, behind an emailed step-up code (P3 §7.1).

    This is the ONLY place a session can learn its org id. `/v1/auth/me` — and
    login, MFA and the desktop handoff — deliberately no longer carry it. The
    old design shipped the id in the `me` payload and masked it in the DOM,
    which protected nothing: devtools showed it in two clicks. A reveal control
    over a value the browser already holds is decoration, and on a product that
    sells tamper-evidence a decorative security control is worse than none.

    A read, not a mutation, so it is POST purely because that is what
    `require_step_up_user` guards elsewhere in this file — and it is audited
    like the other step-up actions, because "who un-masked the workspace id,
    and when" is exactly the kind of question the account trail exists for."""
    account_audit.record_account_action(
        db, org_id=user.org_id, actor_email=user.email,
        action="account.org_id_reveal")
    db.commit()
    return {"org_id": str(user.org_id)}


class IpAllowlistRequest(BaseModel):
    allowlist: str = ""              # comma-separated IPs/CIDRs; empty clears the restriction


@router.post("/v1/account/ip-allowlist", dependencies=[Depends(require_step_up_user)])
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


@router.delete("/v1/account/badge", dependencies=[Depends(require_step_up_user)])
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
    # P3 §5 · the walkthrough's own state, kept separate from the checklist's
    # `dismissed`. Two fields rather than one status string because they answer
    # different questions: `completed` retires the tutorial for good, `skipped`
    # only defers it — and §5.3 requires a skipped tour to come BACK next login.
    tutorial_completed: bool | None = None
    tutorial_skipped: bool | None = None


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
        # P3 §5 · the walkthrough, ADDITIVE. Every key above is untouched: the
        # desktop console reads this same payload (dashboard.py `_on_onboarding`)
        # and a renamed or dropped key would break a shipped client.
        #
        # `enabled` is the server-side kill switch (§5.5) — the dashboard is a
        # static file and cannot read Settings, so the flag has to reach it on a
        # response it already fetches. `completed` is the per-user, per-account
        # record that survives a new device (§5.6); `skipped` is why a deferred
        # tour is offered again on the next login (§5.3).
        "tutorial": {
            "enabled": bool(get_settings().first_run_tutorial_enabled),
            "completed": bool(state.get("tutorial_completed")),
            "skipped": bool(state.get("tutorial_skipped")),
        },
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
    if body.tutorial_completed is not None:
        state["tutorial_completed"] = bool(body.tutorial_completed)
        # Finishing clears the deferral: a completed tour is not also a pending
        # one, and leaving both set would re-offer it for ever (§5.3).
        if body.tutorial_completed:
            state["tutorial_skipped"] = False
    if body.tutorial_skipped is not None:
        state["tutorial_skipped"] = bool(body.tutorial_skipped)
    user.onboarding_state = state
    resp = _onboarding_state(user, db)   # compute BEFORE commit — require_user's RLS GUC is still set
    db.commit()
    return resp


# ─────────────────────────── export history / jobs (P11) ─────────────────────
# "logs_bundle" is /v1/logs/export?format=bundle — the ledger plus the standalone
# verifier. ExportJob.type is String(32), so it needed no migration.
_EXPORT_TYPES = {"passport", "logs_csv", "logs_json", "logs_bundle"}


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


# ─────────────────────────── profile + preferences (P14) ─────────────────────
class ProfileUpdate(BaseModel):
    full_name: str | None = None


@router.put("/v1/account/profile")
def update_profile(body: ProfileUpdate, user: User = Depends(require_user),
                   db: Session = Depends(get_db)):
    """Set the signed-in user's display name (identity defaults to email; uses the name once set —
    drives the avatar initial + greeting). Audited via account_actions."""
    user.full_name = (body.full_name or "").strip()[:120] or None
    account_audit.record_account_action(
        db, org_id=user.org_id, actor_email=user.email, action="account.profile_update")
    db.commit()
    return {"full_name": user.full_name}


# ───────────────────────────── avatar (P6c) ──────────────────────────────────
# THE FIRST FILE-UPLOAD SURFACE IN THIS BACKEND. Everything below treats the
# request body as hostile, because it is the only endpoint here that accepts
# bytes a user chose rather than fields a schema shaped.
#
# The threat is not "someone uploads a big file". It is that an image upload is
# the classic way to get attacker-controlled bytes written to a server's disk
# and then served back to a browser. Four things stop that, in this order:
#
#   1. A SIZE CAP BEFORE THE READ. Content-Length is checked first because it is
#      free, then the read itself is bounded — a lying or absent header must not
#      be able to pull more than the cap into memory.
#   2. DECODE, DO NOT SNIFF. The filename and the client's Content-Type are both
#      attacker-controlled and neither is consulted. Pillow decoding the bytes is
#      the only thing that establishes this is an image, and `.load()` forces the
#      actual pixel work rather than just parsing a header.
#   3. RE-ENCODE ONTO A FRESH CANVAS. The output is a NEW Image the server drew,
#      not the input with its metadata rewritten. That is what strips EXIF, ICC
#      profiles, PNG text chunks and anything hiding after the image data — a
#      polyglot file that is both a valid PNG and a valid script does not survive
#      being redrawn as pixels.
#   4. THE SERVER NAMES THE FILE. `{user_id}.png` from the session, never from
#      the request. There is no path component the caller can influence, so
#      traversal is not defended against here — it is unreachable.
#
# Rate-limited because decode is CPU work: a 5 MB image is cheap to send and
# expensive to open, which is the shape of an asymmetric DoS.
MAX_AVATAR_BYTES = 5 * 1024 * 1024
AVATAR_PX = 256
# Formats accepted AFTER decoding, by what Pillow says the bytes are.
_AVATAR_FORMATS = {"PNG", "JPEG", "WEBP"}


def _avatar_path_for(user: User) -> pathlib.Path:
    return pathlib.Path(get_settings().avatar_dir) / f"{user.id}.png"


@router.post("/v1/account/avatar")
@limiter.limit("6/minute")
async def upload_avatar(request: Request, file: UploadFile = File(...),
                        user: User = Depends(require_user),
                        db: Session = Depends(get_db)):
    """Replace the signed-in user's avatar. Audited as account.avatar_set."""
    import io as _io

    # 1 · the cap, before anything is read. The header is a claim; the bounded
    # read below is what enforces it.
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_AVATAR_BYTES:
        raise HTTPException(status_code=413, detail="image must be 5 MB or smaller")
    raw = await file.read(MAX_AVATAR_BYTES + 1)
    if len(raw) > MAX_AVATAR_BYTES:
        raise HTTPException(status_code=413, detail="image must be 5 MB or smaller")
    if not raw:
        raise HTTPException(status_code=400, detail="no image was uploaded")

    # 2 · decode. Anything Pillow will not open is not an image, whatever it was
    # called or claimed to be. DecompressionBombError is caught by the same
    # blanket except on purpose — a 50000x50000 PNG is a refusal, not a 500.
    try:
        from PIL import Image
    except ImportError:                                    # pragma: no cover
        log.error("Pillow is not installed; avatar upload is unavailable")
        raise HTTPException(status_code=503, detail="image processing unavailable")
    try:
        with Image.open(_io.BytesIO(raw)) as probe:
            fmt = (probe.format or "").upper()
            probe.load()
    except Exception:
        raise HTTPException(status_code=400, detail="that file is not a readable image")
    if fmt not in _AVATAR_FORMATS:
        raise HTTPException(status_code=400,
                            detail="image must be a PNG, JPEG or WebP")

    # 3 · redraw. Centre-crop to a square first so a wide photo is not squashed,
    # then paste onto a canvas this process created. The new image inherits no
    # `info` dict, which is where EXIF, ICC and PNG text chunks would have been.
    try:
        with Image.open(_io.BytesIO(raw)) as src:
            src = src.convert("RGBA")
            w, h = src.size
            side = min(w, h)
            src = src.crop(((w - side) // 2, (h - side) // 2,
                            (w - side) // 2 + side, (h - side) // 2 + side))
            src = src.resize((AVATAR_PX, AVATAR_PX), Image.LANCZOS)
            canvas = Image.new("RGBA", (AVATAR_PX, AVATAR_PX), (0, 0, 0, 0))
            canvas.paste(src, (0, 0), src)
            buf = _io.BytesIO()
            canvas.save(buf, format="PNG", optimize=True)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="that image could not be processed")

    # 4 · write. The name comes from the session, so there is no caller-supplied
    # path component to sanitise. Written to a temp file and moved into place so
    # a crash mid-write cannot leave a half-PNG where a valid one used to be.
    dest = _avatar_path_for(user)
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".png.tmp")
        tmp.write_bytes(buf.getvalue())
        tmp.replace(dest)
    except OSError:
        log.exception("could not write avatar for user %s", user.id)
        raise HTTPException(status_code=500, detail="could not save the image")

    user.avatar_path = str(dest)
    user.avatar_updated_at = datetime.now(timezone.utc)
    account_audit.record_account_action(
        db, org_id=user.org_id, actor_email=user.email, action="account.avatar_set")
    db.commit()
    return {"has_avatar": True,
            "avatar_updated_at": user.avatar_updated_at.isoformat()}


@router.delete("/v1/account/avatar")
def delete_avatar(user: User = Depends(require_user), db: Session = Depends(get_db)):
    """Remove the signed-in user's avatar. Audited as account.avatar_clear.

    The columns are cleared even if the unlink fails: the row is what the rest of
    the product reads, so a file left behind by a full disk or a permissions
    change is litter, while a row still claiming a photo is a broken image."""
    dest = _avatar_path_for(user)
    try:
        dest.unlink(missing_ok=True)
    except OSError:
        log.warning("could not unlink avatar for user %s", user.id)
    user.avatar_path = None
    user.avatar_updated_at = None
    account_audit.record_account_action(
        db, org_id=user.org_id, actor_email=user.email, action="account.avatar_clear")
    db.commit()
    return {"has_avatar": False}


@router.get("/v1/account/avatar")
def get_avatar(user: User = Depends(require_user)):
    """The signed-in user's OWN avatar, and only ever their own.

    There is no id parameter — not an ignored one, none at all. The path is
    derived from the session, so "let me see someone else's picture" is not a
    request this endpoint can express. That is deliberate: an avatar is not
    public here, and the cheapest way to never leak one across tenants is to
    give the caller no way to name a different file.

    Cached private + short: long enough that a page with the avatar in the top
    bar and the settings card does not fetch it twice, short enough that a
    removal is not still on screen minutes later. `private` keeps it out of any
    shared proxy — this is one user's photo, not a static asset."""
    dest = _avatar_path_for(user)
    if not user.avatar_path or not dest.is_file():
        raise HTTPException(status_code=404, detail="no avatar set")
    return FileResponse(dest, media_type="image/png",
                        headers={"Cache-Control": "private, max-age=60"})


# Every key here must be READ by something. notify_product_updates and
# notify_security_alerts were removed with their switches (P3 §6): nothing sent
# product updates, and after P3 §3 new-device alerts are deliberately not
# opt-out-able, so a switch offering to silence them would have been a lie.
# test_pref_switches_are_real.py fails if a key without a consumer is added back.
_ALLOWED_PREFS = {"hide_sensitive_metadata",
                  # Settings → Notifications (D-S). Consumed by app/user_notifications.py:
                  # breach alerts + weekly digest default ON (absent = deliver), key-rotation
                  # reminders are opt-in (absent = don't deliver) — matching the UI defaults.
                  "notify_breach_alerts", "notify_weekly_digest", "notify_key_rotation_reminders"}


class PreferencesUpdate(BaseModel):
    preferences: dict


@router.get("/v1/account/preferences")
def get_preferences(user: User = Depends(require_user)):
    return {"preferences": user.preferences or {}}


@router.put("/v1/account/preferences")
def update_preferences(body: PreferencesUpdate, user: User = Depends(require_user),
                       db: Session = Depends(get_db)):
    """Merge the allowed boolean preferences into the user's JSONB bag (unknown keys ignored)."""
    cur = dict(user.preferences or {})
    for k, v in (body.preferences or {}).items():
        if k in _ALLOWED_PREFS:
            cur[k] = bool(v)
    user.preferences = cur
    db.commit()
    return {"preferences": cur}


# ─────────────────────────── notifications center (P16) ──────────────────────
# Rows are GENERATED FROM REAL EVENTS via an idempotent sync-on-read (recent policy breaches, deduped
# by seq) — never fabricated. Bounded to the most recent breaches; new breaches notify on the next read.
_NOTIF_BREACH = (AuditLog.grading_status == "graded") & (
    AuditLog.gemini_verdict["policy_breach"].astext == "true")


def _sync_notifications(db: Session, user: User) -> None:
    """Stage (no commit) notification rows for recent breaches not yet notified. Runs under the
    require_user RLS GUC; the caller commits once so the GUC survives the surrounding read."""
    recent = db.execute(
        select(AuditLog.seq, AuditLog.policy_tag, AuditLog.gemini_verdict, AuditLog.created_at)
        .where(AuditLog.org_id == user.org_id, _NOTIF_BREACH)
        .order_by(AuditLog.seq.desc()).limit(20)
    ).all()
    if not recent:
        return
    seqs = [str(r.seq) for r in recent]
    seen = set(db.execute(
        select(Notification.target_id).where(
            Notification.org_id == user.org_id, Notification.kind == "breach",
            Notification.target_id.in_(seqs))
    ).scalars().all())
    for r in recent:
        tid = str(r.seq)
        if tid in seen:
            continue
        try:
            risk = int((r.gemini_verdict or {}).get("risk_score") or 0)
        except (TypeError, ValueError):
            risk = 0
        level = "critical" if risk >= 70 else ("warning" if risk >= 40 else "info")
        tag = r.policy_tag or "policy"
        db.add(Notification(
            org_id=user.org_id, user_id=None, kind="breach",
            title=f"Policy breach: {tag}",
            body=f"A '{tag}' interaction was flagged (risk {risk}). Review it under Threats.",
            level=level, target_type="ledger", target_id=tid,
            created_at=r.created_at or datetime.now(timezone.utc)))


def _notif_dict(n: Notification) -> dict:
    return {
        "id": str(n.id), "kind": n.kind, "title": n.title, "body": n.body, "level": n.level,
        "target_type": n.target_type, "target_id": n.target_id, "read": n.read_at is not None,
        "created_at": n.created_at.isoformat() if n.created_at else None,
    }


@router.get("/v1/notifications")
def list_notifications(user: User = Depends(require_user), db: Session = Depends(get_db),
                       limit: int = Query(default=30, ge=1, le=100),
                       page: int = Query(default=1, ge=1, description="1-indexed page"),
                       unread_only: bool = False):
    """The org's notifications (newest first), the unread count, and the total.

    `page` mirrors /v1/logs (1-indexed, paired with `limit`) because the
    dashboard's notifications PAGE has to reach the whole history, not just the
    newest `limit`. The top-bar panel keeps calling this with limit=30 and no
    page, which is page 1 — unchanged. `total` is additive; existing callers
    ignore it.

    Without this the page could only ever show the newest 100 rows, since limit
    is capped there, and an audit surface that silently stops at 100 is the same
    defect as one that silently stops at 30."""
    _sync_notifications(db, user)                 # stages rows (no commit)
    db.flush()                                    # autoflush is off — flush so the SELECT sees new rows
    scope = [Notification.org_id == user.org_id]
    if unread_only:
        scope.append(Notification.read_at.is_(None))
    # ONE aggregate for both counts. Two separate COUNTs would make this three
    # round trips where the endpoint used to take two, and every extra
    # statement holds the transaction — and its locks — open a little longer
    # against a test fixture that TRUNCATEs `users` and `organizations`
    # between cases.
    total_all, unread = db.execute(
        select(func.count(Notification.id),
               func.count(Notification.id).filter(Notification.read_at.is_(None)))
        .where(Notification.org_id == user.org_id)
    ).one()
    # when the caller asked for unread only, the filtered total IS the unread
    # count — otherwise the pager renders pages that cannot be reached
    total = int(unread if unread_only else total_all)
    unread = int(unread)
    rows = db.execute(
        select(Notification).where(*scope)
        .order_by(Notification.created_at.desc())
        .offset((page - 1) * limit).limit(limit)
    ).scalars().all()
    db.commit()                                   # persist the synced rows
    return {"unread": unread, "total": total, "items": [_notif_dict(n) for n in rows]}


@router.post("/v1/notifications/{note_id}/read")
def mark_notification_read(note_id: str, user: User = Depends(require_user),
                           db: Session = Depends(get_db)):
    try:
        nid = uuid.UUID(note_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="not found")
    n = db.get(Notification, nid)                 # RLS-scoped → cross-org invisible
    if n is None or n.org_id != user.org_id:
        raise HTTPException(status_code=404, detail="not found")
    if n.read_at is None:
        n.read_at = datetime.now(timezone.utc)
        db.commit()
    return {"status": "ok"}


@router.post("/v1/notifications/read-all")
def mark_all_notifications_read(user: User = Depends(require_user),
                                db: Session = Depends(get_db)):
    n = db.query(Notification).filter(
        Notification.org_id == user.org_id, Notification.read_at.is_(None)
    ).update({Notification.read_at: datetime.now(timezone.utc)}, synchronize_session=False)
    db.commit()
    return {"status": "ok", "read": int(n)}
