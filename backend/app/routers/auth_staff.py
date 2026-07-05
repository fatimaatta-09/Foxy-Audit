"""Platform-staff session auth for the internal admin site ("site 3").

A DELIBERATELY separate channel from customer auth (auth_human.py): staff log in
here over the `foxy_staff_session` cookie (a distinct SessionMiddleware on the
admin sub-app, signed with STAFF_SESSION_SECRET). This router is mounted under
`/admin`, so its paths resolve to `/admin/v1/auth/*`. It only ever queries
`staff_users`, so a customer `users` credential can never mint a staff session.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import email, password_reset
from ..auth import require_staff
from ..config import get_settings
from ..db import get_db
from ..models import StaffUser

# Own limiter (registered on the admin sub-app in main.py) — the customer login
# has none today; a brute-force guard on the *admin* login especially matters.
limiter = Limiter(key_func=get_remote_address)

router = APIRouter()

# Timing-equalization hash (same technique as auth_human): one bcrypt verify runs
# whether or not the email exists, so response time can't confirm a staff email.
_DUMMY_HASH = b"$2b$12$Rz1JAD5efasLHu5D.kolz.QagN8aF7XSazm89wlVY8DJ/cjvNXsrm"


class StaffLoginRequest(BaseModel):
    email: str
    password: str


class StaffMeResponse(BaseModel):
    id: str
    email: str
    platform_role: str


class StaffMfaRequest(BaseModel):
    email: str
    code: str


_MFA_TTL = timedelta(minutes=5)


def _new_otp() -> str:
    """A 6-digit numeric code (patched in tests for determinism)."""
    return f"{secrets.randbelow(1_000_000):06d}"


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _issue_mfa_code(db: Session, staff: StaffUser) -> None:
    code = _new_otp()
    staff.mfa_code_hash = _hash_code(code)
    staff.mfa_code_expires_at = datetime.now(timezone.utc) + _MFA_TTL
    db.commit()
    email.send_email(
        to=staff.email,
        subject="Your Foxy Audit sign-in code",
        html=(f"<p>Your staff sign-in code is <b>{code}</b>. It expires in 5 minutes. "
              f"If you didn't try to sign in, ignore this email.</p>"),
        text=f"Your Foxy Audit staff sign-in code is {code} (expires in 5 minutes).",
    )


def _establish_staff_session(request: Request, staff: StaffUser) -> None:
    # Rotate the session (drop any prior/planted state), then stamp the identity
    # under a NAMESPACED key a customer session never carries.
    request.session.clear()
    request.session["staff_user_id"] = str(staff.id)
    request.session["staff_role"] = staff.platform_role


@router.post("/v1/auth/login")
@limiter.limit("10/minute")
def staff_login(payload: StaffLoginRequest, request: Request, db: Session = Depends(get_db)):
    email = payload.email.strip().lower()
    pw = payload.password.encode("utf-8")
    staff = db.execute(
        select(StaffUser).where(StaffUser.email == email)
    ).scalar_one_or_none()
    if staff is None:
        bcrypt.checkpw(pw, _DUMMY_HASH)         # equalize timing vs. the valid-email path
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not bcrypt.checkpw(pw, staff.password_hash.encode("utf-8")):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if staff.disabled:
        raise HTTPException(status_code=403, detail="Account disabled")

    # Opt-in email-OTP MFA: email a code and stop here — NO session until the code
    # is verified at /v1/auth/mfa. (Phase 5 · 5B.5)
    if staff.mfa_enabled:
        _issue_mfa_code(db, staff)
        return {"mfa_required": True, "email": staff.email}

    _establish_staff_session(request, staff)
    return {"id": str(staff.id), "email": staff.email, "platform_role": staff.platform_role}


@router.post("/v1/auth/mfa")
@limiter.limit("10/minute")
def staff_mfa(payload: StaffMfaRequest, request: Request, db: Session = Depends(get_db)):
    """Step 2 of MFA login: verify the emailed code, then establish the session."""
    email = payload.email.strip().lower()
    staff = db.execute(
        select(StaffUser).where(StaffUser.email == email)
    ).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if (staff is None or not staff.mfa_enabled or not staff.mfa_code_hash
            or staff.mfa_code_expires_at is None or staff.mfa_code_expires_at < now
            or not hmac.compare_digest(staff.mfa_code_hash, _hash_code(payload.code))):
        raise HTTPException(status_code=401, detail="Invalid or expired code")
    if staff.disabled:
        raise HTTPException(status_code=403, detail="Account disabled")

    staff.mfa_code_hash = None            # single-use
    staff.mfa_code_expires_at = None
    db.commit()
    _establish_staff_session(request, staff)
    return {"id": str(staff.id), "email": staff.email, "platform_role": staff.platform_role}


class StaffForgotPasswordRequest(BaseModel):
    email: str


class StaffResetPasswordRequest(BaseModel):
    token: str
    new_password: str


@router.post("/v1/auth/forgot-password")
@limiter.limit("5/minute")
def staff_forgot_password(payload: StaffForgotPasswordRequest, request: Request,
                          db: Session = Depends(get_db)):
    """Email a reset link if the staff email exists. Always 200 (enumeration-safe)."""
    email_addr = payload.email.strip().lower()
    staff = db.execute(
        select(StaffUser).where(StaffUser.email == email_addr)).scalar_one_or_none()
    if staff is not None and not staff.disabled:
        password_reset.issue_reset(db, staff, staff.email, get_settings().admin_url)
    return {"status": "ok"}


@router.post("/v1/auth/reset-password")
@limiter.limit("5/minute")
def staff_reset_password(payload: StaffResetPasswordRequest, request: Request,
                         db: Session = Depends(get_db)):
    """Consume a single-use reset token and set the new staff password."""
    if len(payload.new_password) < 8:
        raise HTTPException(status_code=422,
                            detail="new password must be at least 8 characters")
    token_hash = password_reset.hash_token(payload.token)
    staff = db.execute(
        select(StaffUser).where(StaffUser.reset_token_hash == token_hash)).scalar_one_or_none()
    if staff is not None and not staff.disabled and password_reset.token_valid(staff, payload.token):
        staff.password_hash = bcrypt.hashpw(
            payload.new_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        password_reset.clear_reset(staff)
        db.commit()
        return {"status": "password_reset"}
    raise HTTPException(status_code=401, detail="Invalid or expired reset link")


@router.post("/v1/auth/logout")
def staff_logout(request: Request):
    request.session.clear()
    return {"status": "logged_out"}


@router.get("/v1/auth/me", response_model=StaffMeResponse)
def staff_me(staff: StaffUser = Depends(require_staff)):
    return StaffMeResponse(id=str(staff.id), email=staff.email,
                           platform_role=staff.platform_role)
