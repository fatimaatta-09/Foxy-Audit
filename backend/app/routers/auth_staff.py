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
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import (email, email_templates as et, login_history, mfa, password_reset,
                user_notifications)
from ..admin_audit import client_ip, record_admin_action
from ..auth import grant_step_up, hash_session_token, require_staff, require_step_up_dep
from ..config import get_settings
from ..db import get_db
from ..models import AdminAction, StaffSession, StaffUser

# Own limiter (registered on the admin sub-app in main.py). The customer login is
# rate-limited too (auth_human: 10/minute); the *admin* login keeps its own guard
# because a brute-force there matters most.
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
    mfa_enabled: bool = False
    full_name: str | None = None
    preferences: dict = Field(default_factory=dict)
    last_login_at: str | None = None


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
    html, plain = et.layout(
        title="Your staff sign-in code",
        preheader=f"Your Foxy Audit staff sign-in code is {code}",
        blocks=[
            et.paragraph("Use this one-time code to finish signing in to the Foxy Audit staff "
                         "console."),
            et.code_block(code),
            et.muted("It expires in 5 minutes. If you didn't try to sign in, ignore this email."),
        ],
        surface="staff",
    )
    email.send_email(to=staff.email, subject="Your Foxy Audit staff sign-in code",
                     html=html, text=plain)


def _establish_staff_session(request: Request, staff: StaffUser, db: Session) -> None:
    # Rotate the cookie session (drop any prior/planted state), stamp the identity under a
    # NAMESPACED key, and mint a DB session row (Phase E) whose opaque token lives in the signed
    # cookie. Also stamp last_login_at. NO remember-me — the 2h cookie max-age is unchanged.
    request.session.clear()
    token = secrets.token_urlsafe(32)
    ua = (request.headers.get("user-agent") or "")[:400] or None
    ip = client_ip(request)
    # Ask the new-device question BEFORE the row is added, or this session matches
    # itself and no device is ever new. Best-effort in every direction: a failed
    # lookup means no alert, never a failed sign-in.
    try:
        new_device = login_history.is_new_staff_device(db, staff.id, ua)
    except Exception:                       # noqa: BLE001 — never break a login
        db.rollback()
        new_device = False
    db.add(StaffSession(staff_user_id=staff.id, token_hash=hash_session_token(token),
                        ip=ip, user_agent=ua))
    staff.last_login_at = datetime.now(timezone.utc)
    db.commit()
    if new_device:
        # ENQUEUED only — sent from the notifications thread, so a slow or broken
        # mail provider can never delay or fail a staff sign-in.
        user_notifications.enqueue_staff_device_alert(
            staff_user_id=staff.id, email=staff.email, ip=ip, user_agent=ua)
    request.session["staff_user_id"] = str(staff.id)
    request.session["staff_role"] = staff.platform_role
    request.session["staff_session_token"] = token


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

    _establish_staff_session(request, staff, db)
    record_admin_action(db, staff, "staff.login", target_type="staff_user",
                        target_id=str(staff.id), detail={"mfa": False}, ip=client_ip(request))
    db.commit()
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
    record_admin_action(db, staff, "staff.login", target_type="staff_user",
                        target_id=str(staff.id), detail={"mfa": True}, ip=client_ip(request))
    db.commit()
    _establish_staff_session(request, staff, db)
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
        password_reset.issue_reset(db, staff, staff.email, get_settings().admin_url,
                                   surface="staff")
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
def staff_logout(request: Request, db: Session = Depends(get_db)):
    sid = request.session.get("staff_user_id")
    if sid:
        try:
            st = db.get(StaffUser, uuid.UUID(sid))
        except (ValueError, TypeError):
            st = None
        if st is not None:
            token = request.session.get("staff_session_token")
            if token:                                        # revoke this device's session row
                db.query(StaffSession).filter(
                    StaffSession.token_hash == hash_session_token(token),
                    StaffSession.revoked_at.is_(None),
                ).update({StaffSession.revoked_at: datetime.now(timezone.utc)},
                         synchronize_session=False)
            record_admin_action(db, st, "staff.logout", target_type="staff_user",
                                target_id=str(st.id), ip=client_ip(request))
            db.commit()
    request.session.clear()
    return {"status": "logged_out"}


@router.get("/v1/auth/me", response_model=StaffMeResponse)
def staff_me(staff: StaffUser = Depends(require_staff)):
    return StaffMeResponse(id=str(staff.id), email=staff.email,
                           platform_role=staff.platform_role, mfa_enabled=staff.mfa_enabled,
                           full_name=staff.full_name, preferences=staff.preferences or {},
                           last_login_at=staff.last_login_at.isoformat() if staff.last_login_at else None)


class UpdateProfileRequest(BaseModel):
    full_name: str | None = Field(default=None, max_length=120)


@router.put("/v1/auth/profile")
def update_profile(payload: UpdateProfileRequest, request: Request,
                   staff: StaffUser = Depends(require_staff), db: Session = Depends(get_db)):
    """Set your own display name (drives the avatar initial + topbar name)."""
    name = (payload.full_name or "").strip() or None
    staff.full_name = name
    record_admin_action(db, staff, "staff.profile_update", target_type="staff_user",
                        target_id=str(staff.id), detail={"full_name": name},
                        ip=client_ip(request))
    db.commit()
    return {"status": "ok", "full_name": staff.full_name}


# Only these preference keys are accepted; anything else is dropped so a client
# can't stuff arbitrary JSON into the row.
_ALLOWED_PREF_KEYS = ("hide_sensitive_metadata", "notify_broadcasts",
                      "notify_targeted", "notify_system")


class UpdatePreferencesRequest(BaseModel):
    preferences: dict


@router.get("/v1/auth/preferences")
def get_preferences(staff: StaffUser = Depends(require_staff)):
    return {"preferences": staff.preferences or {}}


@router.put("/v1/auth/preferences")
def update_preferences(payload: UpdatePreferencesRequest, request: Request,
                       staff: StaffUser = Depends(require_staff), db: Session = Depends(get_db)):
    """Merge the known boolean preference keys (unknown keys are ignored)."""
    incoming = payload.preferences or {}
    merged = dict(staff.preferences or {})
    for key in _ALLOWED_PREF_KEYS:
        if key in incoming:
            merged[key] = bool(incoming[key])
    staff.preferences = merged            # reassign so SQLAlchemy flags the JSONB change
    record_admin_action(db, staff, "staff.preferences_update", target_type="staff_user",
                        target_id=str(staff.id), detail={"keys": sorted(merged.keys())},
                        ip=client_ip(request))
    db.commit()
    return {"status": "ok", "preferences": merged}


# ───────────────────────────── step-up (re-auth) ─────────────────────────────
# Danger mutations require a recent emailed code. request emails one (reusing the MFA
# email-OTP on the mfa_code_* columns — safe because step-up runs inside an established
# session, never during login); confirm verifies it and mints a ~10-min session grant that
# auth.require_step_up_dep consumes. (If a login-OTP and a step-up-OTP ever need to coexist
# without sharing columns, add a small staff_step_up_codes table instead.)
@router.post("/v1/auth/step-up/request")
def step_up_request(staff: StaffUser = Depends(require_staff), db: Session = Depends(get_db)):
    """Email a fresh 6-digit step-up code."""
    mfa.issue_code(db, staff, staff.email)        # commits + emails
    return {"status": "code_sent", "email": staff.email}


class StepUpConfirmRequest(BaseModel):
    code: str


@router.post("/v1/auth/step-up/confirm")
def step_up_confirm(payload: StepUpConfirmRequest, request: Request,
                    staff: StaffUser = Depends(require_staff), db: Session = Depends(get_db)):
    """Verify the emailed code and mint a ~10-min step-up grant for danger actions."""
    if not mfa.code_valid(staff, payload.code):
        raise HTTPException(status_code=400, detail="invalid or expired code")
    mfa.clear_code(staff)
    grant_step_up(request)
    record_admin_action(db, staff, "staff.step_up", target_type="staff_user",
                        target_id=str(staff.id), ip=client_ip(request))
    db.commit()
    return {"status": "step_up_granted"}



class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


@router.post("/v1/auth/change-password")
def change_password(payload: ChangePasswordRequest, request: Request,
                    staff: StaffUser = Depends(require_staff), db: Session = Depends(get_db)):
    """Change your own staff password (verifies the current one)."""
    if not bcrypt.checkpw(payload.current_password.encode("utf-8"),
                          staff.password_hash.encode("utf-8")):
        raise HTTPException(status_code=400, detail="current password is incorrect")
    # Only after the current-password check: a wrong current password must never
    # leak whether the new one happens to match the stored hash.
    #
    # The wording avoids the word "current" on purpose. The admin modal routes a
    # server error to the field it is about with `/current/i.test(m)`, so a reuse
    # message carrying that word would focus the current-password field while
    # being about the new one. Do not reintroduce it here; P3 replaces the
    # substring test with something copy cannot break.
    if bcrypt.checkpw(payload.new_password.encode("utf-8"),
                      staff.password_hash.encode("utf-8")):
        raise HTTPException(status_code=400,
                            detail="new password must be different from your previous one")
    staff.password_hash = bcrypt.hashpw(
        payload.new_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    record_admin_action(db, staff, "staff.password_change", target_type="staff_user",
                        target_id=str(staff.id), ip=client_ip(request))
    db.commit()
    return {"status": "password_changed"}


@router.post("/v1/auth/mfa/enable")
def mfa_enable(request: Request, staff: StaffUser = Depends(require_staff),
               db: Session = Depends(get_db)):
    """Start self-enrolment: email a code; confirm at /mfa/confirm to switch it on."""
    mfa.issue_code(db, staff, staff.email)     # commits + emails the code
    return {"status": "code_sent", "email": staff.email}


class MfaConfirmRequest(BaseModel):
    code: str


@router.post("/v1/auth/mfa/confirm")
def mfa_confirm(payload: MfaConfirmRequest, request: Request,
                staff: StaffUser = Depends(require_staff), db: Session = Depends(get_db)):
    if not mfa.code_valid(staff, payload.code):
        raise HTTPException(status_code=400, detail="invalid or expired code")
    staff.mfa_enabled = True
    mfa.clear_code(staff)
    record_admin_action(db, staff, "staff.mfa_enable", target_type="staff_user",
                        target_id=str(staff.id), ip=client_ip(request))
    db.commit()
    return {"status": "mfa_enabled"}


class MfaDisableRequest(BaseModel):
    password: str


@router.post("/v1/auth/mfa/disable")
def mfa_disable(payload: MfaDisableRequest, request: Request,
                staff: StaffUser = Depends(require_staff), db: Session = Depends(get_db)):
    if not bcrypt.checkpw(payload.password.encode("utf-8"),
                          staff.password_hash.encode("utf-8")):
        raise HTTPException(status_code=400, detail="password is incorrect")
    staff.mfa_enabled = False
    mfa.clear_code(staff)
    record_admin_action(db, staff, "staff.mfa_disable", target_type="staff_user",
                        target_id=str(staff.id), ip=client_ip(request))
    db.commit()
    return {"status": "mfa_disabled"}


@router.get("/v1/auth/activity")
def my_activity(staff: StaffUser = Depends(require_staff), db: Session = Depends(get_db),
                limit: int = Query(default=30, ge=1, le=100)):
    """The signed-in staff member's own recent audited actions (sessions + changes)."""
    rows = db.execute(
        select(AdminAction).where(AdminAction.staff_user_id == staff.id)
        .order_by(AdminAction.created_at.desc()).limit(limit)
    ).scalars().all()
    return {"items": [
        {"action": a.action, "target_type": a.target_type, "target_id": a.target_id,
         "detail": a.detail, "ip": a.ip,
         "at": a.created_at.isoformat() if a.created_at else None}
        for a in rows
    ]}


# ─────────────────────────── device sessions (Phase E) ───────────────────────
@router.get("/v1/auth/sessions")
def list_sessions(request: Request, staff: StaffUser = Depends(require_staff),
                  db: Session = Depends(get_db)):
    """Your active device sessions (this-device flagged). token_hash is NEVER serialized."""
    cur = request.session.get("staff_session_token")
    cur_hash = hash_session_token(cur) if cur else None
    now = datetime.now(timezone.utc)
    rows = db.execute(
        select(StaffSession).where(
            StaffSession.staff_user_id == staff.id, StaffSession.revoked_at.is_(None)
        ).order_by(StaffSession.created_at.desc())
    ).scalars().all()
    items = []
    for s in rows:
        if s.created_at is not None and now - s.created_at > timedelta(hours=2):
            continue                             # expired (kept in table, just not shown as active)
        items.append({
            "id": str(s.id), "ip": s.ip, "user_agent": s.user_agent,
            "created_at": s.created_at.isoformat() if s.created_at else None,
            "last_seen_at": s.last_seen_at.isoformat() if s.last_seen_at else None,
            "current": cur_hash is not None and s.token_hash == cur_hash,
        })
    return {"items": items,
            "last_login_at": staff.last_login_at.isoformat() if staff.last_login_at else None}


@router.post("/v1/auth/sessions/{session_id}/revoke",
             dependencies=[Depends(require_step_up_dep)])
def revoke_session(session_id: str, request: Request,
                   staff: StaffUser = Depends(require_staff), db: Session = Depends(get_db)):
    """Revoke one of your sessions (step-up gated)."""
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="not found")
    s = db.get(StaffSession, sid)
    if s is None or s.staff_user_id != staff.id:
        raise HTTPException(status_code=404, detail="not found")
    if s.revoked_at is None:
        s.revoked_at = datetime.now(timezone.utc)
        record_admin_action(db, staff, "staff.session_revoke", target_type="staff_session",
                            target_id=str(s.id), ip=client_ip(request))
        db.commit()
    return {"status": "revoked", "id": str(s.id)}


@router.post("/v1/auth/logout-all", dependencies=[Depends(require_step_up_dep)])
def logout_all(request: Request, staff: StaffUser = Depends(require_staff),
               db: Session = Depends(get_db)):
    """Revoke ALL your sessions (including this device) — log out everywhere (step-up gated)."""
    n = db.query(StaffSession).filter(
        StaffSession.staff_user_id == staff.id, StaffSession.revoked_at.is_(None)
    ).update({StaffSession.revoked_at: datetime.now(timezone.utc)}, synchronize_session=False)
    record_admin_action(db, staff, "staff.logout_all", target_type="staff_user",
                        target_id=str(staff.id), detail={"revoked": int(n)}, ip=client_ip(request))
    db.commit()
    request.session.clear()
    return {"status": "logged_out_everywhere", "revoked": int(n)}
