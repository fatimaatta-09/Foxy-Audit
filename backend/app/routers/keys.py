"""API key management.

Two audiences share this router:

* The **dashboard** (human admin session) manages *named* keys — list / create /
  revoke — via ``GET|POST /v1/keys`` and ``DELETE /v1/keys/{id}``. New keys are
  stored as HMAC-SHA256(server pepper, key) in the ``api_keys`` table; the
  plaintext is returned exactly once.
 * The **SDK/CLI** (machine Bearer key) can ``POST /v1/keys/rotate`` to rotate
   only the credential used for that request. It never revokes unrelated
   service keys, limiting the blast radius if a machine credential is exposed.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import account_audit
from .. import email as email_mod
from .. import mfa
from ..auth import hash_key, require_org, require_role
from ..config import get_settings
from ..db import get_db
from ..models import ApiKey, Organization, User

log = logging.getLogger("foxy.keys")
router = APIRouter()


def _new_key() -> tuple[str, str, str]:
    """Return (plaintext, hmac_hash, display_prefix) for a fresh key."""
    key = "foxy_sk_" + secrets.token_hex(24)
    return key, hash_key(key), key[:11] + "…" + key[-4:]   # foxy_sk_1a2…7e02


# ─────────────────────────── dashboard (admin session) ───────────────────────

class KeyItem(BaseModel):
    id: str
    name: str
    key_prefix: str
    status: str
    created_at: str
    last_used_at: str | None = None
    expires_at: str | None = None
    expired: bool = False
    revoked_at: str | None = None


class CreateKeyRequest(BaseModel):
    name: str = "unnamed key"
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)  # None = never


class CreateKeyResponse(BaseModel):
    id: str
    name: str
    key_prefix: str
    api_key: str             # plaintext — shown once, never stored
    created_at: str
    message: str = "Copy this key now — it is shown once and cannot be recovered."


def _serialize(k: ApiKey) -> KeyItem:
    expired = bool(k.expires_at and k.expires_at < datetime.now(timezone.utc))
    return KeyItem(
        id=str(k.id), name=k.name, key_prefix=k.key_prefix, status=k.status,
        created_at=k.created_at.isoformat() if k.created_at else "",
        last_used_at=k.last_used_at.isoformat() if k.last_used_at else None,
        expires_at=k.expires_at.isoformat() if k.expires_at else None,
        expired=expired,
        revoked_at=k.revoked_at.isoformat() if k.revoked_at else None,
    )


@router.get("/v1/keys", response_model=list[KeyItem])
def list_keys(
    admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """List the org's API keys (active + revoked), newest first — admin only."""
    rows = db.execute(
        select(ApiKey).where(ApiKey.org_id == admin.org_id)
        .order_by(ApiKey.created_at.desc())
    ).scalars().all()
    return [_serialize(k) for k in rows]


@router.post("/v1/keys", response_model=CreateKeyResponse)
def create_key(
    body: CreateKeyRequest,
    admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Create a named API key. Returns the plaintext once - admin only."""
    org = db.get(Organization, admin.org_id)
    key_limit = get_settings().api_key_limit_for(org.plan_tier if org else None)
    if key_limit is not None:
        active_keys = db.execute(
            select(func.count()).select_from(ApiKey).where(
                ApiKey.org_id == admin.org_id, ApiKey.status == "active")
        ).scalar_one()
        if int(active_keys) >= key_limit:
            raise HTTPException(
                status_code=402,
                detail={"code": "api_key_limit_reached", "message": "Your plan has no available active API-key slots. Upgrade to add another environment or service.",
                        "used": int(active_keys), "included": key_limit},
            )
    key, key_hash, prefix = _new_key()
    name = ((body.name or "").strip() or "unnamed key")[:120]  # whitespace-only -> default
    expires_at = (datetime.now(timezone.utc) + timedelta(days=body.expires_in_days)
                  if body.expires_in_days else None)
    row = ApiKey(org_id=admin.org_id, name=name, key_prefix=prefix, key_hash=key_hash,
                 status="active", expires_at=expires_at)
    db.add(row)
    db.flush()
    account_audit.record_account_action(
        db, org_id=admin.org_id, actor_email=admin.email, action="key.create",
        target=name, detail={"expires_in_days": body.expires_in_days})
    db.commit()
    db.refresh(row)
    log.info("Created API key %s for org %s", row.id, admin.org_id)
    return CreateKeyResponse(
        id=str(row.id), name=row.name, key_prefix=row.key_prefix,
        api_key=key, created_at=row.created_at.isoformat() if row.created_at else "",
    )


@router.delete("/v1/keys/{key_id}")
def revoke_key(
    key_id: uuid.UUID,     # FastAPI validates the path -> 422 (not 500) on a non-UUID
    admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Revoke one of the org's keys — admin only. Idempotent on an already-revoked key."""
    row = db.execute(
        select(ApiKey).where(ApiKey.id == key_id, ApiKey.org_id == admin.org_id)
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Key not found")
    if row.status != "revoked":
        row.status = "revoked"
        row.revoked_at = datetime.now(timezone.utc)
        account_audit.record_account_action(
            db, org_id=admin.org_id, actor_email=admin.email, action="key.revoke",
            target=row.name)
        db.commit()
    log.info("Revoked API key %s for org %s", row.id, admin.org_id)
    return {"status": "revoked", "id": str(row.id)}


# ─────────────────────────── SDK/CLI (machine Bearer) ────────────────────────

class RotateResponse(BaseModel):
    org_id: str
    api_key: str             # plaintext — shown once, never stored
    rotated_at: str
    message: str = "Key rotated. Copy the new key now — the old key is permanently invalid."


def _rotate_org_key(db: Session, org: Organization) -> tuple[str, str]:
    """Revoke the org's active keys, mint a fresh peppered one, and kill the legacy
    plain-SHA256 hash. Returns (plaintext_key, rotated_at_iso). Caller commits."""
    now = datetime.now(timezone.utc)
    key, key_hash, prefix = _new_key()
    for k in db.execute(
        select(ApiKey).where(ApiKey.org_id == org.id, ApiKey.status == "active")
    ).scalars().all():
        k.status = "revoked"
        k.revoked_at = now
    db.add(ApiKey(org_id=org.id, name="primary (rotated)", key_prefix=prefix,
                  key_hash=key_hash, status="active"))
    # Point the legacy hash at the new key too, so the old key is dead but the
    # NOT NULL column stays satisfied (the peppered path matches first anyway).
    org.api_key_hash = hashlib.sha256(key.encode("utf-8")).hexdigest()
    org.key_rotated_at = now
    return key, now.isoformat()


def _rotate_presented_key(db: Session, org: Organization, token: str) -> tuple[str, str, str]:
    """Replace only the authenticated machine key, leaving sibling keys usable."""
    now = datetime.now(timezone.utc)
    key, key_hash, prefix = _new_key()
    presented_legacy_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    current = db.execute(
        select(ApiKey).where(
            ApiKey.org_id == org.id,
            ApiKey.key_hash == hash_key(token),
            ApiKey.status == "active",
        )
    ).scalar_one_or_none()

    if current is not None:
        current.status = "revoked"
        current.revoked_at = now
        name = current.name
        expires_at = current.expires_at
    else:
        # require_org already verified this is the organization legacy key. Keep
        # the compatibility hash only for the replacement, not for the old key.
        name = "legacy primary"
        expires_at = None

    db.add(ApiKey(
        org_id=org.id,
        name=f"{name} (rotated)",
        key_prefix=prefix,
        key_hash=key_hash,
        status="active",
        expires_at=expires_at,
    ))
    if org.api_key_hash and hmac.compare_digest(org.api_key_hash, presented_legacy_hash):
        org.api_key_hash = hashlib.sha256(key.encode("utf-8")).hexdigest()
    org.key_rotated_at = now
    return key, now.isoformat(), name


@router.post("/v1/keys/rotate", response_model=RotateResponse)
def rotate_key(
    authorization: str = Header(default=""),
    org: Organization = Depends(require_org),
    db: Session = Depends(get_db),
):
    """Rotate the presented machine key without affecting sibling credentials."""
    token = authorization.split(" ", 1)[1].strip()
    key, rotated_at, key_name = _rotate_presented_key(db, org, token)
    account_audit.record_account_action(
        db,
        org_id=org.id,
        actor_email="api_key",
        action="key.rotate",
        target=key_name,
        detail={"mode": "self_service"},
    )
    db.commit()
    log.info("Rotated one API key for org %s", org.id)
    return RotateResponse(org_id=str(org.id), api_key=key, rotated_at=rotated_at)


# ─────────────── dashboard: regenerate key behind an emailed 2FA code ─────────
# Keys are stored hashed and shown once, so "reveal" isn't possible — instead we
# mint a FRESH key (old one revoked), gated by a one-time code emailed to the
# logged-in admin. Keeps hash-only security; the admin always has a usable key.

class RegenConfirmRequest(BaseModel):
    code: str


@router.post("/v1/keys/regenerate/request")
def regenerate_request(
    admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Step 1: email a one-time code to the admin's address (2FA even inside a
    logged-in session)."""
    code = mfa.new_otp()
    admin.mfa_code_hash = mfa.hash_code(code)
    admin.mfa_code_expires_at = datetime.now(timezone.utc) + mfa.TTL
    db.commit()
    email_mod.send_email(
        to=admin.email, subject="Your Foxy Audit key-regeneration code",
        html=(f"<p>Your code to regenerate your API key is <b>{code}</b>. It expires in "
              "5 minutes.</p><p>If you didn't request this, ignore this email and change "
              "your password.</p>"),
        text=f"Your Foxy Audit key-regeneration code is {code} (expires in 5 minutes).")
    return {"status": "code_sent", "email": admin.email}


@router.post("/v1/keys/regenerate/confirm", response_model=RotateResponse)
def regenerate_confirm(
    body: RegenConfirmRequest,
    admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Step 2: verify the code, then rotate the org's key and return the new
    plaintext once. Wrong/expired code → 401."""
    if not mfa.code_valid(admin, (body.code or "").strip()):
        raise HTTPException(status_code=401, detail="Invalid or expired code")
    mfa.clear_code(admin)
    org = db.get(Organization, admin.org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    key, rotated_at = _rotate_org_key(db, org)
    db.commit()
    log.info("Regenerated API key (2FA) for org %s by %s", org.id, admin.email)
    return RotateResponse(org_id=str(org.id), api_key=key, rotated_at=rotated_at)
