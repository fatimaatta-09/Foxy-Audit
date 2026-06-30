"""POST /v1/keys/rotate — rotate the calling org's API key.

The caller authenticates with their current Bearer key.  A new key is generated,
the old key hash is overwritten (one-way — the old key is immediately invalid),
and the new plaintext key is returned **once**.  The caller must store it; the
backend only keeps the SHA-256 hash.

This mirrors the ``seed_org.py`` key-generation pattern but is accessible at
runtime from the dashboard or CLI without direct database access.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..auth import require_org
from ..db import get_db
from ..models import Organization

log = logging.getLogger("foxy.keys")
router = APIRouter()


class RotateResponse(BaseModel):
    org_id: str
    api_key: str             # plaintext — shown once, never stored
    rotated_at: str
    message: str = "Key rotated. Copy the new key now — the old key is permanently invalid."


@router.post("/v1/keys/rotate", response_model=RotateResponse)
def rotate_key(
    org: Organization = Depends(require_org),
    db: Session = Depends(get_db),
):
    new_key = "foxy_sk_" + secrets.token_hex(24)
    new_hash = hashlib.sha256(new_key.encode("utf-8")).hexdigest()

    now = datetime.now(timezone.utc)

    org.api_key_hash = new_hash
    org.key_rotated_at = now
    db.commit()

    log.info("Rotated API key for org %s", org.id)
    return RotateResponse(
        org_id=str(org.id),
        api_key=new_key,
        rotated_at=now.isoformat(),
    )
