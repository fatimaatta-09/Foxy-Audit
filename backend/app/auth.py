"""Bearer-key authentication + per-request tenant scoping (RLS).

Resolves the org from `Authorization: Bearer <key>` by hashing the key and
matching `organizations.api_key_hash`, then sets the `app.current_org` GUC for
the current transaction via `set_config(..., true)` (the function form of
`SET LOCAL`, which — unlike bare `SET` — accepts a bound parameter safely).
Every subsequent query on this session is then filtered by the RLS policy.
"""

from __future__ import annotations

import hashlib
import uuid

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .db import get_db
from .models import Organization, User


def _bearer_token(authorization: str) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing or malformed bearer token")
    return authorization.split(" ", 1)[1].strip()


def _scope_org(db: Session, org_id) -> None:
    """Set the RLS GUC so this transaction's queries are scoped to `org_id`.
    Shared by both auth paths (machine key + human session)."""
    db.execute(
        text("SELECT set_config('app.current_org', :oid, true)"),
        {"oid": str(org_id)},
    )


def require_org(
    authorization: str = Header(default=""),
    db: Session = Depends(get_db),
) -> Organization:
    """Machine auth for the SDK: `Authorization: Bearer <org_api_key>`."""
    token = _bearer_token(authorization)
    key_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()

    org = db.execute(
        select(Organization).where(Organization.api_key_hash == key_hash)
    ).scalar_one_or_none()
    if org is None:
        raise HTTPException(status_code=401, detail="Invalid API key")

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
        if user is not None:
            _scope_org(db, user.org_id)
            return db.get(Organization, user.org_id)
    raise HTTPException(status_code=401, detail="Not authenticated")
