"""Bearer-key authentication + per-request tenant scoping (RLS).

Resolves the org from `Authorization: Bearer <key>` by hashing the key and
matching `organizations.api_key_hash`, then sets the `app.current_org` GUC for
the current transaction via `set_config(..., true)` (the function form of
`SET LOCAL`, which — unlike bare `SET` — accepts a bound parameter safely).
Every subsequent query on this session is then filtered by the RLS policy.
"""

from __future__ import annotations

import hashlib

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .db import get_db
from .models import Organization


def _bearer_token(authorization: str) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing or malformed bearer token")
    return authorization.split(" ", 1)[1].strip()


def require_org(
    authorization: str = Header(default=""),
    db: Session = Depends(get_db),
) -> Organization:
    token = _bearer_token(authorization)
    key_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()

    org = db.execute(
        select(Organization).where(Organization.api_key_hash == key_hash)
    ).scalar_one_or_none()
    if org is None:
        raise HTTPException(status_code=401, detail="Invalid API key")

    # Scope RLS to this org for the remainder of the transaction.
    db.execute(
        text("SELECT set_config('app.current_org', :oid, true)"),
        {"oid": str(org.id)},
    )
    return org
