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
from datetime import datetime, timezone

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from . import ip_allow
from .config import get_settings
from .db import get_db
from .models import ApiKey, Organization, StaffUser, User


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
    if org.ip_allowlist and not ip_allow.ip_allowed(
            ip_allow.client_ip(request), ip_allow.parse_allowlist(org.ip_allowlist)):
        raise HTTPException(status_code=403, detail="Access from this IP is not allowed")
    _scope_org(db, user.org_id)
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
        return require_org(authorization, db)
    user_id = request.session.get("user_id")
    if user_id:
        user = db.get(User, uuid.UUID(str(user_id)))
        if user is not None and not user.disabled:
            org = db.get(Organization, user.org_id)
            if org is not None and org.deleted_at is None and not org.suspended:
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


def set_org_scope_for_staff(db: Session, org_id) -> None:
    """Deliberately scope RLS to ONE org for a staff drill-down query (reuses the
    same RLS path as customers instead of ad-hoc SQL). Only call when a staff
    endpoint intends to read a single org's RLS-protected rows."""
    _scope_org(db, org_id)
