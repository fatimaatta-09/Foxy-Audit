"""Cross-org management for the internal admin site ("site 3").

Mounted under /admin, so paths resolve to /admin/v1/organizations*. Every handler
is guarded by require_platform_role (staff-only); reads are cross-org (no RLS GUC
is set — the docker `foxy` superuser bypasses FORCE RLS, so staff see all orgs).
Every state-changing action writes an admin_actions row in the same transaction.
"""

from __future__ import annotations

import ipaddress
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..admin_audit import client_ip, record_admin_action
from ..anchor import head_of, latest_anchor
from .. import billing_state, notify
from ..auth import require_platform_role, require_step_up_dep, set_org_scope_for_staff
from ..config import get_settings
from ..platform_config import effective_quota
from ..db import get_db
from ..models import ApiKey, AuditLog, OrgPolicy, Organization, StaffUser, UsageDaily, User
from .verify import verify_chain

router = APIRouter()


def _iso(dt):
    return dt.isoformat() if dt else None


class OrgListItem(BaseModel):
    id: str
    name: str
    plan_tier: str | None = None
    subscription_status: str | None = None
    suspended: bool = False
    deleted: bool = False
    contact_email: str | None = None
    created_at: str | None = None
    #: M4a. NULL for every organisation that did not arrive through the demo
    #: route — which is every row older than migration 0064, and every org a
    #: purchase creates. "pending" is what M4c's approvals queue filters on.
    approval_status: str | None = None


class OrgDetail(OrgListItem):
    suspended_reason: str | None = None
    monthly_log_quota: int | None = None
    user_count: int = 0
    api_key_count: int = 0
    total_logs: int = 0
    breaches: int = 0


class SuspendRequest(BaseModel):
    reason: str | None = None


class PlanRequest(BaseModel):
    plan: str
    monthly_log_quota: int | None = None
    #: What the customer actually paid against, when they paid outside the
    #: payment processor — an invoice number, a payment-request id. Free text on
    #: purpose: it names somebody else's record, and we cannot validate the
    #: shape of every one of them. Recorded on the AdminAction, and — since M3f
    #: widened `invoices` to be processor-neutral — also as a customer-visible
    #: receipt with `provider="manual"`. It is NOT written to
    #: `stripe_invoice_id`, which still means a Stripe invoice and nothing else.
    payment_reference: str | None = Field(default=None, max_length=128)


def _list_item(o: Organization) -> OrgListItem:
    return OrgListItem(
        id=str(o.id), name=o.name, plan_tier=o.plan_tier,
        subscription_status=o.subscription_status, suspended=bool(o.suspended),
        deleted=o.deleted_at is not None, contact_email=o.contact_email,
        created_at=o.created_at.isoformat() if o.created_at else None,
        approval_status=o.approval_status,
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


@router.post("/v1/organizations/{org_id}/suspend", dependencies=[Depends(require_step_up_dep)])
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
    notify.broadcast(db, "system", f"Org suspended: {org.name}",
                     body=f"{staff.email} suspended {org.name}.", level="warning",
                     pref="notify_system", exclude_id=staff.id,
                     target_type="organization", target_id=str(org.id))
    db.commit()
    return {"status": "suspended", "org_id": str(org.id)}


@router.post("/v1/organizations/{org_id}/enable", dependencies=[Depends(require_step_up_dep)])
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


@router.post("/v1/organizations/{org_id}/plan", dependencies=[Depends(require_step_up_dep)])
def set_organization_plan(
    org_id: str,
    body: PlanRequest,
    request: Request,
    staff: StaffUser = Depends(require_platform_role("superadmin")),
    db: Session = Depends(get_db),
):
    """Apply a paid or custom contract after sales closes it.

    This is the path a customer who paid OUTSIDE the payment processor arrives
    on — an invoice settled by bank transfer or a payment request — so it has to
    do everything the processor's own webhook does, or the money changes nothing.

    Until M0 it did not. It set the tier, the quota, the trial and the
    subscription status, and left the four evaluation fields alone; and
    ``billing_state.dashboard_lock`` tries ``evaluation_lock`` FIRST. So an org
    whose evaluation window had expired — the single most likely first paying
    customer — was set to ``pro`` by staff and stayed exactly as locked, with
    capture still refused. ``end_evaluation`` is the fix, and it is the same
    helper the Stripe upgrade path calls.
    """
    org = _load_active_org(db, org_id)
    plan = get_settings().canonical_plan(body.plan)
    if plan not in {"free", "pro", "max", "premium"}:
        raise HTTPException(status_code=422, detail="plan must be free, pro, max, or premium")
    if body.monthly_log_quota is not None and body.monthly_log_quota < 0:
        raise HTTPException(status_code=422, detail="monthly_log_quota cannot be negative")
    org.plan_tier = plan
    org.monthly_log_quota = (
        body.monthly_log_quota
        if body.monthly_log_quota is not None
        else effective_quota(db, plan, get_settings())
    )
    if plan == "free":
        org.trial_ends_at = (datetime.now(timezone.utc)
                             + timedelta(days=get_settings().trial_days))
    else:
        # Behaviour-identical to the conditional this replaces. Routed through
        # the helper so "a paid tier has no trial" is stated in ONE place —
        # this path was the only one that got it right, and the two purchase
        # paths that got it wrong now call the same function (register #101).
        billing_state.end_trial(org)
    org.subscription_status = "active"
    org.past_due_since = None            # no stale clock left behind a staff reactivation
    # Audited on the CUSTOMER trail too. The AdminAction below answers "which
    # staff member did this"; the org's own history still needs to show that its
    # evaluation ended, whoever ended it. No-op when there was no offer.
    billing_state.end_evaluation_audited(db, org, via="staff_activation")
    detail = {"plan": plan, "monthly_log_quota": org.monthly_log_quota}
    reference = (body.payment_reference or "").strip()
    if reference:
        # Absent when nothing was typed, rather than present and empty. The audit
        # trail is the only record this payment has; a key holding "" would read
        # as "we looked and there was no reference", which is a different claim.
        detail["payment_reference"] = reference
        # And the customer sees it too (register #94 · M3f). Until now this
        # reference existed only on the AdminAction row — readable by STAFF
        # since M3c, and invisible to the person who actually paid, whose
        # billing page stayed empty. No amount is recorded because this form
        # has none: staff record WHICH payment they saw, not how much it was,
        # and a zero would tell the customer they paid nothing.
        billing_state.record_payment(db, org, provider="manual",
                                     reference=reference, status="paid")
    record_admin_action(
        db, staff, "org.plan.set", target_org_id=org.id,
        target_type="organization", target_id=str(org.id),
        detail=detail, ip=client_ip(request),
    )
    db.commit()
    return {"status": "updated", "org_id": str(org.id), "plan_tier": plan,
            "monthly_log_quota": org.monthly_log_quota}


# ============================ Org 360 (Phase 1) ============================
# Deep per-tenant read views for the Org 360 sub-page. All viewer-gated; every
# per-org read drills in through set_org_scope_for_staff. NEVER expose secrets
# (no password_hash, no key_hash) — only key_prefix is shown for API keys.


def _scoped_org(db: Session, org_id: str) -> Organization:
    """Load + validate a tenant (superuser), then scope RLS to just that org."""
    org = _load_active_org(db, org_id)
    set_org_scope_for_staff(db, org.id)
    return org


def _breach_filter():
    return AuditLog.gemini_verdict["policy_breach"].astext == "true"


class UsagePoint(BaseModel):
    day: str
    logs: int
    breaches: int
    tokens: int


class PolicySnapshot(BaseModel):
    pii_detection: bool
    prompt_injection: bool
    regulated_data_mode: bool
    max_token_threshold: int
    enforcement_mode: str
    confidence_threshold: str
    notify_on_breach: str
    notify_email: str | None = None
    notify_webhook_url: str | None = None


class OrgOverview(BaseModel):
    id: str
    name: str
    plan_tier: str | None = None
    subscription_status: str | None = None
    suspended: bool = False
    suspended_reason: str | None = None
    deleted: bool = False
    monthly_log_quota: int | None = None
    trial_ends_at: str | None = None
    contact_email: str | None = None
    created_at: str | None = None
    #: M4a. NULL for every organisation that did not arrive through the demo
    #: route — which is every row older than migration 0064, and every org a
    #: purchase creates. "pending" is what M4c's approvals queue filters on.
    approval_status: str | None = None
    ip_allowlist: list[str] = []
    user_count: int = 0
    api_key_count: int = 0
    active_key_count: int = 0
    ledger_height: int = 0
    ledger_head: str | None = None
    total_logs: int = 0
    breaches: int = 0
    last_anchor_status: str | None = None
    last_anchor_at: str | None = None
    usage: list[UsagePoint] = []
    policy: PolicySnapshot | None = None


@router.get("/v1/organizations/{org_id}/overview", response_model=OrgOverview)
def org_overview(
    org_id: str,
    staff: StaffUser = Depends(require_platform_role("viewer")),
    db: Session = Depends(get_db),
    days: int = Query(default=30, ge=1, le=365),
):
    """Full tenant snapshot: profile + quota/trial, usage_daily time-series, ledger
    head + verify counts, latest anchor status, and the org policy snapshot."""
    org = _scoped_org(db, org_id)

    user_count = db.execute(
        select(func.count()).select_from(User).where(User.org_id == org.id)
    ).scalar_one()
    key_count = db.execute(
        select(func.count()).select_from(ApiKey).where(ApiKey.org_id == org.id)
    ).scalar_one()
    active_keys = db.execute(
        select(func.count()).select_from(ApiKey)
        .where(ApiKey.org_id == org.id, ApiKey.status == "active")
    ).scalar_one()
    total_logs = db.execute(
        select(func.count()).select_from(AuditLog).where(AuditLog.org_id == org.id)
    ).scalar_one()
    breaches = db.execute(
        select(func.count()).select_from(AuditLog)
        .where(AuditLog.org_id == org.id, _breach_filter())
    ).scalar_one()

    since = datetime.now(timezone.utc).date() - timedelta(days=days - 1)
    usage = [
        UsagePoint(day=r.day.isoformat(), logs=int(r.logs_count),
                   breaches=int(r.breach_count), tokens=int(r.tokens_sum))
        for r in db.execute(
            select(UsageDaily.day, UsageDaily.logs_count, UsageDaily.breach_count,
                   UsageDaily.tokens_sum)
            .where(UsageDaily.org_id == org.id, UsageDaily.day >= since)
            .order_by(UsageDaily.day.asc())
        ).all()
    ]

    head_hash, last_seq = head_of(db, org.id)
    anchor = latest_anchor(db, org.id)
    anchor_at = (anchor.confirmed_at or anchor.anchored_at) if anchor else None

    pol = db.get(OrgPolicy, org.id)
    policy = None
    if pol is not None:
        policy = PolicySnapshot(
            pii_detection=pol.pii_detection, prompt_injection=pol.prompt_injection,
            regulated_data_mode=pol.regulated_data_mode,
            max_token_threshold=pol.max_token_threshold,
            enforcement_mode=pol.enforcement_mode,
            confidence_threshold=pol.confidence_threshold,
            notify_on_breach=pol.notify_on_breach,
            notify_email=pol.notify_email, notify_webhook_url=pol.notify_webhook_url,
        )

    allowlist = [ip.strip() for ip in (org.ip_allowlist or "").split(",") if ip.strip()]
    return OrgOverview(
        id=str(org.id), name=org.name, plan_tier=org.plan_tier,
        subscription_status=org.subscription_status, suspended=bool(org.suspended),
        suspended_reason=org.suspended_reason, deleted=org.deleted_at is not None,
        monthly_log_quota=org.monthly_log_quota, trial_ends_at=_iso(org.trial_ends_at),
        contact_email=org.contact_email, created_at=_iso(org.created_at),
        approval_status=org.approval_status,
        ip_allowlist=allowlist, user_count=user_count, api_key_count=key_count,
        active_key_count=active_keys, ledger_height=last_seq, ledger_head=head_hash,
        total_logs=total_logs, breaches=breaches,
        last_anchor_status=(anchor.status if anchor else None),
        last_anchor_at=_iso(anchor_at), usage=usage, policy=policy,
    )


class OrgUser(BaseModel):
    id: str
    email: str
    role: str
    disabled: bool = False
    mfa_enabled: bool = False
    google_linked: bool = False
    created_at: str | None = None


@router.get("/v1/organizations/{org_id}/users", response_model=list[OrgUser])
def org_users(
    org_id: str,
    staff: StaffUser = Depends(require_platform_role("viewer")),
    db: Session = Depends(get_db),
):
    """This tenant's dashboard users. password_hash is never serialized."""
    org = _scoped_org(db, org_id)
    rows = db.execute(
        select(User).where(User.org_id == org.id).order_by(User.created_at.asc())
    ).scalars().all()
    return [
        OrgUser(id=str(u.id), email=u.email, role=u.role, disabled=bool(u.disabled),
                mfa_enabled=bool(u.mfa_enabled), google_linked=u.google_sub is not None,
                created_at=_iso(u.created_at))
        for u in rows
    ]


class OrgKey(BaseModel):
    id: str
    name: str
    key_prefix: str
    status: str
    created_at: str | None = None
    last_used_at: str | None = None
    expires_at: str | None = None
    revoked_at: str | None = None


@router.get("/v1/organizations/{org_id}/keys", response_model=list[OrgKey])
def org_keys(
    org_id: str,
    staff: StaffUser = Depends(require_platform_role("viewer")),
    db: Session = Depends(get_db),
):
    """This tenant's SDK API keys. key_hash is never serialized (display prefix only)."""
    org = _scoped_org(db, org_id)
    rows = db.execute(
        select(ApiKey).where(ApiKey.org_id == org.id).order_by(ApiKey.created_at.desc())
    ).scalars().all()
    return [
        OrgKey(id=str(k.id), name=k.name, key_prefix=k.key_prefix, status=k.status,
               created_at=_iso(k.created_at), last_used_at=_iso(k.last_used_at),
               expires_at=_iso(k.expires_at), revoked_at=_iso(k.revoked_at))
        for k in rows
    ]


class LedgerRow(BaseModel):
    seq: int
    event_type: str | None = None
    policy_tag: str | None = None
    agent: str | None = None
    token_count: int | None = None
    grading_status: str | None = None
    breach: bool = False
    chain_hash: str | None = None
    occurred_at: str | None = None
    created_at: str | None = None


class LedgerPage(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[LedgerRow]


@router.get("/v1/organizations/{org_id}/logs", response_model=LedgerPage)
def org_logs(
    org_id: str,
    staff: StaffUser = Depends(require_platform_role("viewer")),
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    breaches_only: bool = False,
):
    """Staff-scoped ledger page (hashes only — no raw prompt/response is stored)."""
    org = _scoped_org(db, org_id)
    where = [AuditLog.org_id == org.id]
    if breaches_only:
        where.append(_breach_filter())
    total = db.execute(
        select(func.count()).select_from(AuditLog).where(*where)
    ).scalar_one()
    rows = db.execute(
        select(AuditLog).where(*where)
        .order_by(AuditLog.seq.desc()).limit(limit).offset(offset)
    ).scalars().all()
    items = [
        LedgerRow(
            seq=r.seq, event_type=r.event_type, policy_tag=r.policy_tag, agent=r.agent,
            token_count=r.token_count, grading_status=r.grading_status,
            breach=bool((r.gemini_verdict or {}).get("policy_breach")),
            chain_hash=r.chain_hash, occurred_at=_iso(r.occurred_at),
            created_at=_iso(r.created_at),
        )
        for r in rows
    ]
    return LedgerPage(total=total, limit=limit, offset=offset, items=items)


class OrgVerify(BaseModel):
    ok: bool
    detail: str
    first_broken_seq: int | None = None
    count: int = 0
    ledger_head: str | None = None
    last_seq: int = 0
    last_anchor_status: str | None = None
    last_anchor_at: str | None = None


@router.get("/v1/organizations/{org_id}/verify", response_model=OrgVerify)
def org_verify(
    org_id: str,
    staff: StaffUser = Depends(require_platform_role("viewer")),
    db: Session = Depends(get_db),
):
    """One-click integrity recompute for this tenant's ledger (reuses verify_chain)."""
    org = _scoped_org(db, org_id)
    ok, first_broken, detail = verify_chain(db, org.id)
    head_hash, last_seq = head_of(db, org.id)
    total = db.execute(
        select(func.count()).select_from(AuditLog).where(AuditLog.org_id == org.id)
    ).scalar_one()
    anchor = latest_anchor(db, org.id)
    anchor_at = (anchor.confirmed_at or anchor.anchored_at) if anchor else None
    return OrgVerify(
        ok=bool(ok) if ok is not None else False,
        detail=detail if ok is not None else f"{detail}, use partial window",
        first_broken_seq=first_broken, count=total,
        ledger_head=head_hash, last_seq=last_seq,
        last_anchor_status=(anchor.status if anchor else None),
        last_anchor_at=_iso(anchor_at),
    )


class BreachRow(BaseModel):
    seq: int
    policy_tag: str | None = None
    agent: str | None = None
    grading_status: str | None = None
    verdict: dict | None = None
    occurred_at: str | None = None
    created_at: str | None = None


class BreachPage(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[BreachRow]


@router.get("/v1/organizations/{org_id}/breaches", response_model=BreachPage)
def org_breaches(
    org_id: str,
    staff: StaffUser = Depends(require_platform_role("viewer")),
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """Policy breaches for this tenant (over audit_logs.gemini_verdict)."""
    org = _scoped_org(db, org_id)
    total = db.execute(
        select(func.count()).select_from(AuditLog)
        .where(AuditLog.org_id == org.id, _breach_filter())
    ).scalar_one()
    rows = db.execute(
        select(AuditLog).where(AuditLog.org_id == org.id, _breach_filter())
        .order_by(AuditLog.seq.desc()).limit(limit).offset(offset)
    ).scalars().all()
    items = [
        BreachRow(seq=r.seq, policy_tag=r.policy_tag, agent=r.agent,
                  grading_status=r.grading_status, verdict=r.gemini_verdict,
                  occurred_at=_iso(r.occurred_at), created_at=_iso(r.created_at))
        for r in rows
    ]
    return BreachPage(total=total, limit=limit, offset=offset, items=items)


# ======================= Org lifecycle actions (Phase 2, audited) =======================
# Writes replacing the old prompt()/confirm() flows. Gated operator (revoke/ip/trial)
# or superadmin (offboard). Every mutation records an admin_actions row in the same txn.


def _norm_allowlist(raw: str) -> list[str]:
    out: list[str] = []
    for part in (raw or "").replace("\n", ",").split(","):
        p = part.strip()
        if not p:
            continue
        try:
            ipaddress.ip_network(p, strict=False)   # accepts a bare IP or a CIDR
        except ValueError:
            raise HTTPException(status_code=422, detail=f"invalid IP or CIDR: {p}")
        out.append(p)
    return out


class IpAllowlistBody(BaseModel):
    entries: list[str] | None = None
    raw: str | None = None


@router.get("/v1/organizations/{org_id}/ip-allowlist")
def get_ip_allowlist(
    org_id: str,
    staff: StaffUser = Depends(require_platform_role("viewer")),
    db: Session = Depends(get_db),
):
    org = _load_active_org(db, org_id)
    entries = [ip.strip() for ip in (org.ip_allowlist or "").split(",") if ip.strip()]
    return {"org_id": str(org.id), "entries": entries}


@router.put("/v1/organizations/{org_id}/ip-allowlist", dependencies=[Depends(require_step_up_dep)])
def put_ip_allowlist(
    org_id: str,
    body: IpAllowlistBody,
    request: Request,
    staff: StaffUser = Depends(require_platform_role("operator")),
    db: Session = Depends(get_db),
):
    """Replace the org's dashboard IP allow-list (empty clears it → no restriction)."""
    org = _load_active_org(db, org_id)
    raw = body.raw if body.raw is not None else ",".join(body.entries or [])
    entries = _norm_allowlist(raw)
    org.ip_allowlist = ",".join(entries) if entries else None
    record_admin_action(db, staff, "org.ip_allowlist.set", target_org_id=org.id,
                        target_type="organization", target_id=str(org.id),
                        detail={"count": len(entries)}, ip=client_ip(request))
    db.commit()
    return {"status": "updated", "entries": entries}


@router.post("/v1/organizations/{org_id}/keys/{key_id}/revoke", dependencies=[Depends(require_step_up_dep)])
def revoke_key(
    org_id: str,
    key_id: str,
    request: Request,
    staff: StaffUser = Depends(require_platform_role("operator")),
    db: Session = Depends(get_db),
):
    """Revoke one of an org's SDK API keys (idempotent)."""
    org = _load_active_org(db, org_id)
    try:
        kid = uuid.UUID(str(key_id))
    except ValueError:
        raise HTTPException(status_code=422, detail="invalid key id")
    key = db.get(ApiKey, kid)
    if key is None or key.org_id != org.id:
        raise HTTPException(status_code=404, detail="api key not found for this organization")
    if key.status == "revoked":
        return {"status": "already_revoked", "key_id": str(key.id)}
    key.status = "revoked"
    key.revoked_at = datetime.now(timezone.utc)
    record_admin_action(db, staff, "org.key.revoke", target_org_id=org.id,
                        target_type="api_key", target_id=str(key.id),
                        detail={"name": key.name}, ip=client_ip(request))
    db.commit()
    return {"status": "revoked", "key_id": str(key.id)}


class TrialBody(BaseModel):
    days: int


@router.post("/v1/organizations/{org_id}/trial", dependencies=[Depends(require_step_up_dep)])
def extend_trial(
    org_id: str,
    body: TrialBody,
    request: Request,
    staff: StaffUser = Depends(require_platform_role("operator")),
    db: Session = Depends(get_db),
):
    """Extend (or shorten, with a negative value) a tenant's comp/trial window."""
    if body.days == 0 or abs(body.days) > 3650:
        raise HTTPException(status_code=422, detail="days must be a non-zero value within +/-3650")
    org = _load_active_org(db, org_id)
    now = datetime.now(timezone.utc)
    base = org.trial_ends_at if (org.trial_ends_at and org.trial_ends_at > now) else now
    new_end = base + timedelta(days=body.days)
    org.trial_ends_at = new_end
    record_admin_action(db, staff, "org.trial.extend", target_org_id=org.id,
                        target_type="organization", target_id=str(org.id),
                        detail={"days": body.days, "trial_ends_at": new_end.isoformat()},
                        ip=client_ip(request))
    db.commit()
    return {"status": "updated", "trial_ends_at": new_end.isoformat()}


@router.post("/v1/organizations/{org_id}/approve", dependencies=[Depends(require_step_up_dep)])
def approve_organization(
    org_id: str,
    request: Request,
    staff: StaffUser = Depends(require_platform_role("operator")),
    db: Session = Depends(get_db),
):
    """Approve a pending demo workspace and start its 7 days (M4a · register #94's sibling).

    Shipped in the same phase as the pending state on purpose. A state a signup
    can enter and nothing can leave is a trap door, and M4c — which builds the
    queue this button belongs to — would otherwise have to be a backend phase in
    disguise. The console can arrive later; the exit must not.

    Refuses anything that is not pending, rather than quietly stamping a fresh
    trial on it. ``approve`` on an already-approved org would restart the clock,
    and on an org that never came through the demo route (``approval_status`` is
    NULL — every organisation older than migration 0064) it would enrol a
    grandfathered customer into the trial lock they are exempt from. Both are
    silent damage, so both are a 409.

    A REFUSAL IS NOT EXPRESSED HERE. There is no ``rejected`` value: ``suspend``
    already refuses a workspace, on every auth channel, with its own message and
    its own audit action. Adding a second vocabulary for the same outcome would
    put a new reason string on three surfaces to say a word this one already says.
    """
    org = _load_active_org(db, org_id)
    if not billing_state.awaiting_approval(org):
        raise HTTPException(status_code=409, detail={
            "code": "not_pending",
            "message": "This workspace is not waiting for approval.",
        })
    billing_state.approve_demo(org)
    record_admin_action(db, staff, "org.approve", target_org_id=org.id,
                        target_type="organization", target_id=str(org.id),
                        detail={"trial_ends_at": org.trial_ends_at.isoformat()},
                        ip=client_ip(request))
    db.commit()
    return {"status": "approved", "org_id": str(org.id),
            "trial_ends_at": org.trial_ends_at.isoformat()}


@router.post("/v1/organizations/{org_id}/offboard", dependencies=[Depends(require_step_up_dep)])
def offboard_organization(
    org_id: str,
    request: Request,
    staff: StaffUser = Depends(require_platform_role("superadmin")),
    db: Session = Depends(get_db),
):
    """Soft-delete a tenant: mark deleted, hold access, and revoke every live key.
    Never hard-deletes — the org stays auditable via ?include_deleted."""
    org = _load_active_org(db, org_id)
    if org.deleted_at is not None:
        return {"status": "already_offboarded", "org_id": str(org.id)}
    now = datetime.now(timezone.utc)
    org.deleted_at = now
    org.suspended = True
    org.suspended_reason = org.suspended_reason or "offboarded"
    keys = db.execute(
        select(ApiKey).where(ApiKey.org_id == org.id, ApiKey.status == "active")
    ).scalars().all()
    for k in keys:
        k.status = "revoked"
        k.revoked_at = now
    record_admin_action(db, staff, "org.offboard", target_org_id=org.id,
                        target_type="organization", target_id=str(org.id),
                        detail={"keys_revoked": len(keys)}, ip=client_ip(request))
    notify.broadcast(db, "system", f"Org offboarded: {org.name}",
                     body=f"{staff.email} offboarded {org.name} ({len(keys)} keys revoked).",
                     level="warning", pref="notify_system", exclude_id=staff.id,
                     target_type="organization", target_id=str(org.id))
    db.commit()
    return {"status": "offboarded", "org_id": str(org.id), "keys_revoked": len(keys)}
