"""Bearer-key authentication + per-request tenant scoping (RLS).

Resolves the org from `Authorization: Bearer <key>` by hashing the key and
matching `organizations.api_key_hash`, then sets the `app.current_org` GUC for
the current transaction via `set_config(..., true)` (the function form of
`SET LOCAL`, which — unlike bare `SET` — accepts a bound parameter safely).
Every subsequent query on this session is then filtered by the RLS policy.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from . import billing_state, ip_allow
from .config import get_settings
from .db import get_db
from .models import ApiKey, Organization, StaffSession, StaffUser, User, UserSession


def _bearer_token(authorization: str) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing or malformed bearer token")
    return authorization.split(" ", 1)[1].strip()


def hash_key(token: str) -> str:
    """HMAC-SHA256(server pepper, key) — the way new keys are stored/matched.
    The pepper is a server-side secret (not in the DB), so a DB leak alone can't
    recover a usable key. Shared by require_org, routers/keys, and seed_org."""
    pepper = get_settings().api_key_pepper.encode("utf-8")
    return hmac.new(pepper, token.encode("utf-8"), hashlib.sha256).hexdigest()


def _ensure_org_access(org: Organization) -> None:
    """Apply workspace-level holds to every customer auth channel."""
    if org.deleted_at is not None:
        raise HTTPException(status_code=403, detail="This workspace has been deleted")
    if org.suspended:
        raise HTTPException(status_code=403, detail="This workspace is suspended")


def _scope_org(db: Session, org_id) -> None:
    """Scope this transaction to `org_id` for RLS. Shared by both auth paths
    (machine key + human session) and the staff drill-down.

    Two transaction-local (SET LOCAL) steps, so both reset when the transaction
    ends and never leak across pooled connections:
      1. Set the `app.current_org` GUC the RLS policies read.
      2. `SET LOCAL ROLE foxy_app` — drop from the superuser (which BYPASSES
         FORCE RLS) to a confined, NOBYPASSRLS role so the policies actually
         apply. Staff cross-org paths never call this, so they stay superuser.
    The GUC is set FIRST, while still superuser, so the role switch can never
    interfere with establishing the scope."""
    db.execute(
        text("SELECT set_config('app.current_org', :oid, true)"),
        {"oid": str(org_id)},
    )
    role = get_settings().db_app_role
    if role:
        # Role name comes from trusted config, never user input; still constrain
        # it to a plain identifier before interpolating (SET ROLE can't bind).
        if not role.replace("_", "").isalnum():
            raise RuntimeError(f"invalid db_app_role: {role!r}")
        db.execute(text(f'SET LOCAL ROLE "{role}"'))


def require_org(
    authorization: str = Header(default=""),
    db: Session = Depends(get_db),
) -> Organization:
    """Machine auth for the SDK: `Authorization: Bearer <org_api_key>`.

    Precedence: (1) HMAC-with-pepper match against an *active* api_keys row
    (the new peppered multi-key model), else (2) legacy plain-SHA256 fallback
    against organizations.api_key_hash so seeded/pre-A2 keys keep working. A
    key upgrades to the peppered path on the next rotate/create."""
    token = _bearer_token(authorization)

    # (1) Peppered multi-key path.
    key_row = db.execute(
        select(ApiKey).where(
            ApiKey.key_hash == hash_key(token), ApiKey.status == "active"
        )
    ).scalar_one_or_none()
    # An expired key authenticates no one — fall through to a 401 (never silently
    # succeed). Expiry is optional (NULL = never expires).
    if key_row is not None and key_row.expires_at is not None \
            and key_row.expires_at < datetime.now(timezone.utc):
        key_row = None
    if key_row is not None:
        org = db.get(Organization, key_row.org_id)
        if org is not None:
            _ensure_org_access(org)
            # Best-effort usage stamp. NOT committed here: committing would end
            # the transaction and clear the transaction-local RLS GUC set below.
            # It persists on write endpoints (which commit); reads may drop it.
            key_row.last_used_at = datetime.now(timezone.utc)
            _scope_org(db, org.id)
            return org

    # (2) Legacy plain-SHA256 fallback.
    legacy_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    org = db.execute(
        select(Organization).where(Organization.api_key_hash == legacy_hash)
    ).scalar_one_or_none()
    if org is None:
        raise HTTPException(status_code=401, detail="Invalid API key")
    _ensure_org_access(org)
    _scope_org(db, org.id)
    return org


# Paths that must stay reachable while the dashboard is locked (P3 §4.3).
# Getting this wrong bricks the product: lock somebody out of /v1/billing and they
# cannot add the card — or fix the payment — that would unlock them, and lock them
# out of /v1/auth and they cannot even sign out. These gates are DASHBOARD gates —
# SDK ingest authenticates with an API key and never passes through here, so
# neither a missing card nor a failed payment can cause a customer to silently
# lose audit evidence.
#
# P1 · THE RULE THIS TUPLE ENCODES, now that it has grown past "unbrick it":
#
#     A lock may withhold the SERVICE. It must never withhold the customer's
#     existing PROPERTY, or their means of removing the lock.
#
# The first three are the remedy half — a lock whose only cure sits behind the
# lock is a brick. The last three are the property half (register #49): evidence
# a customer has already recorded is theirs, and a billing state standing between
# them and their own ledger is the sharpest contradiction available on a product
# whose argument is *do not trust our dashboard, verify the evidence yourself*.
#
# Exactly what each new prefix catches, because this is `startswith` and a
# careless prefix is how a gate quietly disappears:
#
#   /v1/logs/export   ONE route, GET. It does NOT catch /v1/logs (the browsable
#                     list), /v1/logs/{seq}, /v1/logs/breaches, and — the one
#                     that matters — it does NOT catch POST /v1/logs/batch.
#                     Ingest never passed through here anyway: it authenticates
#                     with `require_org`, which does not call the gate, and its
#                     commercial gate is `capture_block` in logs.py, a different
#                     mechanism with a different answer. Both facts are guarded.
#   /v1/verify        TWO routes, both GET: /v1/verify and
#                     /v1/verify/hash/{chain_hash}. Recomputing your own chain is
#                     the product's central claim; a bill must not suspend it.
#                     Bounded at 50k rows inside `verify_chain` itself.
#   /v1/coverage      ONE route, GET. Without it "here is your evidence" is an
#                     unqualified claim — coverage is what says whether the
#                     export is complete.
#
# All three are pure reads; none writes a row. Nothing that mutates is exempt,
# and nothing that merely BROWSES is either — the ledger list, breaches, stats,
# analytics and usage stay behind the lock, because a convenience UI over the
# evidence is service, while leaving with the evidence and checking it without us
# is property.
#
# ⚠ All three authenticate through `resolve_org`, which takes the SDK Bearer key
# as well as the session cookie — and the Bearer path has never been gated. So a
# locked org could already reach every one of them with its own API key. What
# changes here is WHICH CREDENTIAL works, not what is reachable, which is why
# this widening is small: it removes an inconsistency rather than granting a new
# capability.
_GATE_EXEMPT = (
    # the remedy — without these the lock is a brick
    "/v1/auth/", "/v1/billing/", "/v1/account/preferences",
    # the property — the customer's own evidence, and proving it is intact
    "/v1/logs/export", "/v1/verify", "/v1/coverage",
)


def _enforce_dashboard_gates(request: Request, org: Organization) -> None:
    """402 when this org's dashboard is locked, saying which condition applies.

    Distinct status from 401/403 on purpose: the SPA needs to tell "sign in again"
    apart from "you are signed in, we just need paying" — and only the second one
    should render the locked state rather than bouncing to the login screen.

    TWO independent conditions — a failed subscription and a missing card — both
    resolved by `billing_state.dashboard_lock`, which also decides which one to
    report when an org is in both. D1: the subscription lock is NOT behind
    `require_card_on_file`. They are separate decisions with separate switches
    (`subscription_lock_enabled`), because one is about collecting cards and the
    other is about being paid.

    The exempt-path check comes AFTER working out the lock, and stays that way:
    the escape hatches must be reachable in every locked state, not just the card
    one.
    """
    lock = billing_state.dashboard_lock(org)
    if lock is None:
        return
    if request.url.path.startswith(_GATE_EXEMPT):
        return
    raise HTTPException(status_code=402,
                        detail={"code": lock.reason, "message": lock.message})


def require_user(request: Request, db: Session = Depends(get_db)) -> User:
    """Human auth for the dashboard: resolve the user from the signed session
    cookie. Sets the same RLS GUC as require_org so tenant scoping is identical."""
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = db.get(User, uuid.UUID(str(user_id)))
    if user is None:
        raise HTTPException(status_code=401, detail="Session user no longer exists")
    if user.disabled:
        raise HTTPException(status_code=401, detail="Account disabled")
    org = db.get(Organization, user.org_id)
    if org is None:
        raise HTTPException(status_code=403, detail="This workspace is unavailable")
    _ensure_org_access(org)
    _enforce_dashboard_gates(request, org)
    if org.ip_allowlist and not ip_allow.ip_allowed(
            ip_allow.client_ip(request), ip_allow.parse_allowlist(org.ip_allowlist)):
        raise HTTPException(status_code=403, detail="Access from this IP is not allowed")
    _scope_org(db, user.org_id)
    # P3: validate the DB-backed device session so revoke / log-out-everywhere take effect and the
    # remember-me (30d) vs default (12h) expiry is enforced. Cookies minted before P3 carry no token
    # → treated as valid (graceful). last_seen refresh is best-effort WITHOUT a commit here: a commit
    # would clear the SET LOCAL RLS GUC set just above (same pattern as the api_key last_used_at stamp).
    token = request.session.get("session_token")
    if token:
        sess = db.execute(
            select(UserSession).where(
                UserSession.token_hash == hash_session_token(token),
                UserSession.user_id == user.id,
                UserSession.revoked_at.is_(None),
            )
        ).scalar_one_or_none()
        now = datetime.now(timezone.utc)
        if sess is None or (sess.expires_at is not None and now >= sess.expires_at):
            raise HTTPException(status_code=401, detail="Session expired or revoked")
        sess.last_seen_at = now
    return user


def require_role(role: str):
    """Dependency factory — gate a route on the logged-in user's role."""
    def _dep(user: User = Depends(require_user)) -> User:
        if user.role != role:
            raise HTTPException(status_code=403, detail=f"requires '{role}' role")
        return user
    return _dep


def resolve_org(
    request: Request,
    authorization: str = Header(default=""),
    db: Session = Depends(get_db),
) -> Organization:
    """Read-endpoint auth accepting EITHER the SDK Bearer key OR a human dashboard
    session. Lets the web app read its data over the login cookie (so the BFF
    never holds the API key) while the SDK keeps using its key. Both paths set the
    same RLS GUC. Ingest stays Bearer-only via require_org."""
    if authorization:
        # SDK path. NEVER card-gated: a missing card must not make a customer
        # silently lose audit evidence (P3 §4.3).
        return require_org(authorization, db)
    user_id = request.session.get("user_id")
    if user_id:
        user = db.get(User, uuid.UUID(str(user_id)))
        if user is not None and not user.disabled:
            org = db.get(Organization, user.org_id)
            if org is not None and org.deleted_at is None and not org.suspended:
                # Dashboard path — same gates as require_user, or the lock would be
                # trivially bypassed by every read endpoint that accepts a cookie.
                _enforce_dashboard_gates(request, org)
                if org.ip_allowlist and not ip_allow.ip_allowed(
                        ip_allow.client_ip(request), ip_allow.parse_allowlist(org.ip_allowlist)):
                    raise HTTPException(status_code=403, detail="Access from this IP is not allowed")
                _scope_org(db, user.org_id)
                return org
    raise HTTPException(status_code=401, detail="Not authenticated")


# ───────────────────────── platform-staff auth (site 3) ──────────────────────
# The admin site runs on its OWN mounted sub-app with its OWN SessionMiddleware
# (cookie `foxy_staff_session`, a distinct secret). The two apps are SIBLINGS, so
# their `request.session` objects never collide. require_staff reads ONLY the
# staff session key and never falls back to the customer session — so a customer
# cookie can never satisfy a staff route, and vice-versa.

# Ordered privilege ladder. A higher role satisfies any lower requirement.
_PLATFORM_ROLES = {"viewer": 0, "operator": 1, "superadmin": 2}


# Staff sessions expire after 2h regardless of the cookie (no remember-me · Phase E).
_STAFF_SESSION_MAX_AGE = timedelta(hours=2)


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def require_staff(request: Request, db: Session = Depends(get_db)) -> StaffUser:
    """Resolve the platform-staff user from the staff session cookie.

    CRUCIAL: does NOT call `_scope_org` — it leaves `app.current_org` unset so a
    staff query reads across ALL orgs (the docker `foxy` superuser bypasses FORCE
    RLS). A staff endpoint that wants to drill into one org's RLS-protected rows
    calls `set_org_scope_for_staff` deliberately."""
    staff_id = request.session.get("staff_user_id")
    if not staff_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    staff = db.get(StaffUser, uuid.UUID(str(staff_id)))
    if staff is None:
        raise HTTPException(status_code=401, detail="Session user no longer exists")
    if staff.disabled:
        raise HTTPException(status_code=401, detail="Account disabled")
    # Phase E: validate the DB session row so revoke / log-out-everywhere take effect and enforce
    # the 2h cap. Cookies minted before this shipped carry no token → treated as valid (graceful).
    token = request.session.get("staff_session_token")
    if token:
        sess = db.execute(
            select(StaffSession).where(
                StaffSession.token_hash == hash_session_token(token),
                StaffSession.staff_user_id == staff.id,
                StaffSession.revoked_at.is_(None),
            )
        ).scalar_one_or_none()
        now = datetime.now(timezone.utc)
        if sess is None or (sess.created_at is not None
                            and now - sess.created_at > _STAFF_SESSION_MAX_AGE):
            raise HTTPException(status_code=401, detail="Session expired or revoked")
        # Refresh last_seen_at, throttled to at most once a minute to avoid a write per request.
        if sess.last_seen_at is None or (now - sess.last_seen_at) > timedelta(seconds=60):
            sess.last_seen_at = now
            db.commit()
    return staff


def require_platform_role(min_role: str):
    """Dependency factory — gate a staff route on a MINIMUM platform role, using
    the viewer < operator < superadmin ladder (so superadmin passes everything)."""
    need = _PLATFORM_ROLES[min_role]

    def _dep(staff: StaffUser = Depends(require_staff)) -> StaffUser:
        have = _PLATFORM_ROLES.get(staff.platform_role, -1)
        if have < need:
            raise HTTPException(status_code=403, detail=f"requires '{min_role}' platform role")
        return staff

    return _dep


# ───────────────────────── step-up (re-auth) for danger actions ───────────────
# Every AUDITED danger mutation additionally requires a recent emailed code. Confirming
# one mints a short-lived grant in the staff session, so a burst of sensitive actions
# needs a single code (not one per click). The grant lives in the signed staff cookie.
STEP_UP_TTL = timedelta(minutes=10)
_STEP_UP_KEY = "step_up_until"


def grant_step_up(request: Request) -> None:
    """Mint a ~10-min step-up grant after an emailed code is confirmed."""
    request.session[_STEP_UP_KEY] = (datetime.now(timezone.utc) + STEP_UP_TTL).isoformat()


def step_up_active(request: Request) -> bool:
    raw = request.session.get(_STEP_UP_KEY)
    if not raw:
        return False
    try:
        exp = datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        return False
    return exp >= datetime.now(timezone.utc)


def require_step_up_dep(request: Request, staff: StaffUser = Depends(require_staff)) -> StaffUser:
    """Route dependency for audited danger mutations — add via `dependencies=[Depends(...)]`.
    A missing/expired grant raises 403 `step_up_required`, which the SPA turns into an
    emailed-code prompt and then retries the request."""
    if not step_up_active(request):
        raise HTTPException(status_code=403, detail="step_up_required")
    return staff


def require_step_up_user(request: Request, user: User = Depends(require_user)) -> User:
    """Customer-side step-up gate (P15) — the SAME grant model as staff, but on the customer session.
    Add via `dependencies=[Depends(require_step_up_user)]` to audited danger mutations. A missing or
    expired grant raises 403 `step_up_required`, which the SPA turns into an emailed-code prompt and
    then retries the original request."""
    if not step_up_active(request):
        raise HTTPException(status_code=403, detail="step_up_required")
    return user


def set_org_scope_for_staff(db: Session, org_id) -> None:
    """Deliberately scope RLS to ONE org for a staff drill-down query (reuses the
    same RLS path as customers instead of ad-hoc SQL). Only call when a staff
    endpoint intends to read a single org's RLS-protected rows."""
    _scope_org(db, org_id)
