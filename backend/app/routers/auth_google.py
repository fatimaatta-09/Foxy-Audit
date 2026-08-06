"""Google SSO for the web dashboard + sales site — POST /v1/auth/google.

Unified login-or-provision over a Google ID token (Google Identity Services on the
frontend hands us a signed JWT; we verify it server-side). A verified Google
identity either logs into an existing account (matched by google_sub, else by a
single verified email) or provisions a fresh free-tier org. Issues the same signed
cookie session as password login — and NO email OTP, since a verified Google
sign-in is itself a strong, second-factor-capable identity.

Feature-flagged: if google_oauth_client_id is unset the endpoint returns 503.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import login_history
from ..auth import hash_key
from ..config import get_settings
from ..db import get_db
from ..models import ApiKey, MarketingLead, Organization, User
from .auth_human import _establish_session, _record_login_and_alert
from .logs import limiter

router = APIRouter()


class GoogleAuthRequest(BaseModel):
    credential: str          # the Google ID token (JWT) from Google Identity Services


@router.get("/v1/auth/google/config")
def google_config():
    """Public: the OAuth client id the frontend needs to render the Google button.
    Empty string = feature off, so the button hides itself."""
    return {"client_id": get_settings().google_oauth_client_id}


def _verify_google_token(credential: str, client_id: str) -> dict:
    """Verify a Google ID token and return its claims. Checks signature (Google
    JWKS), audience == client_id, issuer, and expiry. Raises on any failure.
    Isolated so tests can stub it without a real Google round-trip."""
    from google.auth.transport import requests as g_requests
    from google.oauth2 import id_token
    return id_token.verify_oauth2_token(credential, g_requests.Request(), client_id)


def _provision_google_org(db: Session, email: str, name: str | None, sub: str) -> User:
    """Create a fresh free-tier org + admin for a first-time Google user. Mirrors
    /v1/signup but passwordless + email pre-verified (no set-password email)."""
    plaintext_key = "foxy_sk_" + secrets.token_hex(24)
    # M4a — the SECOND free-signup door, and it has to answer the approval
    # question the same way `/v1/signup` does. A demo route that only queues the
    # email/password door is not a demo route: anyone refused there, or unwilling
    # to wait, signs in with Google and is provisioned instantly. Same flag, same
    # two moves — created pending, and the 7-day clock left unstarted until a
    # human approves.
    pending = get_settings().demo_approval_required
    org = Organization(
        name=((name or email).strip() or email)[:255],
        api_key_hash=hashlib.sha256(plaintext_key.encode("utf-8")).hexdigest(),
        plan_tier="free", contact_email=email,
        approval_status="pending" if pending else None,
        trial_ends_at=None if pending else (
            datetime.now(timezone.utc) + timedelta(days=get_settings().trial_days)),
        monthly_log_quota=get_settings().quota_for("free"))
    db.add(org)
    db.flush()
    db.add(ApiKey(org_id=org.id, name="primary",
                  key_prefix=plaintext_key[:11] + "…" + plaintext_key[-4:],
                  key_hash=hash_key(plaintext_key), status="active"))
    placeholder = bcrypt.hashpw(secrets.token_urlsafe(32).encode(), bcrypt.gensalt()).decode()
    admin = User(org_id=org.id, email=email, password_hash=placeholder,
                 role="admin", google_sub=sub)
    db.add(admin)
    lead = db.execute(select(MarketingLead).where(
        func.lower(MarketingLead.email) == email,
        MarketingLead.status != "churned")).scalars().first()
    if lead is not None:
        lead.status, lead.converted_org_id = "converted", org.id
    db.flush()
    return admin


@router.post("/v1/auth/google")
@limiter.limit("10/minute")
def google_auth(payload: GoogleAuthRequest, request: Request, db: Session = Depends(get_db)):
    client_id = get_settings().google_oauth_client_id
    if not client_id:
        raise HTTPException(status_code=503, detail="Google sign-in is not configured")
    try:
        claims = _verify_google_token(payload.credential, client_id)
    except Exception:                        # noqa: BLE001 — any verification failure = reject
        raise HTTPException(status_code=401, detail="Invalid Google token")

    email = (claims.get("email") or "").strip().lower()
    sub = claims.get("sub")
    verified = claims.get("email_verified")
    if not email or not sub or verified not in (True, "true"):
        raise HTTPException(status_code=401, detail="Google account email is not verified")

    # 1) already linked → unambiguous.
    user = db.execute(select(User).where(User.google_sub == sub)).scalars().first()
    if user is None:
        # 2) match by email. Emails are unique only *within* an org, so link only
        #    when exactly one usable account matches; refuse to guess otherwise.
        candidates = db.execute(select(User).where(User.email == email)).scalars().all()
        usable = [u for u in candidates if not u.disabled]
        if len(usable) == 1:
            user = usable[0]
            user.google_sub = sub                    # link Google to the existing account
        elif len(usable) > 1:
            raise HTTPException(status_code=409,
                                detail="Multiple accounts use this email — sign in with your password")
        elif candidates:                             # all matches disabled
            raise HTTPException(status_code=403, detail="Account disabled")
        else:
            # 3) brand-new Google user → provision a free org (self-serve signup).
            user = _provision_google_org(db, email, claims.get("name"), sub)

    if user.disabled:
        raise HTTPException(status_code=403, detail="Account disabled")
    org = db.get(Organization, user.org_id)
    if org is not None and org.deleted_at is not None:
        raise HTTPException(status_code=403, detail="This workspace has been deleted")
    if org is not None and org.suspended:
        raise HTTPException(status_code=403, detail="This workspace is suspended")

    db.commit()
    sid = _establish_session(request, user, db)      # no email OTP for verified Google
    _record_login_and_alert(request, user, db, email, sid)
    # No org_id — same rule as password login and MFA (P3 §7.1). Any sign-in
    # path that still handed it back would let the client cache it at sign-in
    # and make the step-up gate on POST /v1/account/org-id decorative.
    return {"email": user.email, "role": user.role}
