"""Human session auth for the web dashboard.

Email/password login over a signed cookie session (Starlette SessionMiddleware).
This is deliberately a SEPARATE channel from the SDK's `Authorization: Bearer`
machine key (see auth.require_org): browsers get a cookie, the SDK keeps its
header, and the two never collide.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import login_history, mfa, password_reset
from ..auth import require_role, require_user, resolve_org
from ..config import get_settings
from ..db import get_db
from ..models import AuthHandoffToken, LoginEvent, Organization, User
from .logs import limiter          # reuse the app's single Limiter instance

router = APIRouter()

_VALID_ROLES = {"admin", "member"}


def _bcrypt(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


class LoginRequest(BaseModel):
    email: str
    password: str


class MeResponse(BaseModel):
    email: str
    role: str
    org_id: str


class UserListItem(BaseModel):
    id: str
    email: str
    role: str
    disabled: bool


class CreateUserRequest(BaseModel):
    email: str
    role: str = "member"
    password: str | None = None      # omit to auto-generate a temp password


class CreateUserResponse(BaseModel):
    id: str
    email: str
    role: str
    temp_password: str | None = None
    invited: bool = False
    message: str = "Share this temporary password securely — it is shown once."


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


# A fixed bcrypt hash used to equalize login timing when no user matches the
# email, so an attacker can't distinguish "no such email" from "wrong password"
# by response time — both paths run exactly one bcrypt verify.
_DUMMY_HASH = b"$2b$12$Rz1JAD5efasLHu5D.kolz.QagN8aF7XSazm89wlVY8DJ/cjvNXsrm"


def _establish_session(request: Request, user: User) -> None:
    request.session.clear()   # rotate: drop any pre-existing (planted) session
    request.session["user_id"] = str(user.id)
    request.session["org_id"] = str(user.org_id)
    request.session["role"] = user.role


@router.post("/v1/auth/login")
@limiter.limit("10/minute")
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    email = payload.email.strip().lower()
    pw = payload.password.encode("utf-8")
    # Emails are unique only *within* an org (uq_user_org_email), so the same
    # address can exist in several tenants. Authenticate against every candidate
    # and let the PASSWORD select the org — never index-scan order, which would
    # let one tenant's login silently resolve to another tenant's account.
    candidates = db.execute(select(User).where(User.email == email)).scalars().all()
    disabled_match = False
    for user in candidates:
        if bcrypt.checkpw(pw, user.password_hash.encode("utf-8")):
            if user.disabled:
                disabled_match = True
                continue                      # keep looking for an active account
            org = db.get(Organization, user.org_id)
            if org is not None and org.deleted_at is not None:
                raise HTTPException(status_code=403, detail="This workspace has been deleted")
            if org is not None and org.suspended:
                raise HTTPException(status_code=403, detail="This workspace is suspended")
            # Opt-in email-OTP MFA: email a code and stop — no session until it's
            # verified at /v1/auth/mfa. (Phase 5 · 5B.5)
            if user.mfa_enabled:
                mfa.issue_code(db, user, user.email)
                return {"mfa_required": True, "email": user.email}
            _establish_session(request, user)
            login_history.record(db, request, email, True, user)
            return {"email": user.email, "role": user.role, "org_id": str(user.org_id)}
    if not candidates:
        bcrypt.checkpw(pw, _DUMMY_HASH)       # equalize timing vs. the valid-email path
    if disabled_match:
        raise HTTPException(status_code=403, detail="Account disabled")
    login_history.record(db, request, email, False)
    raise HTTPException(status_code=401, detail="Invalid email or password")


class MfaRequest(BaseModel):
    email: str
    code: str


@router.post("/v1/auth/mfa")
@limiter.limit("10/minute")
def mfa_verify(payload: MfaRequest, request: Request, db: Session = Depends(get_db)):
    """Step 2 of dashboard MFA: the code resolves the tenant (like the password
    did across same-email accounts), then the session is established."""
    email = payload.email.strip().lower()
    candidates = db.execute(select(User).where(User.email == email)).scalars().all()
    for user in candidates:
        if user.mfa_enabled and not user.disabled and mfa.code_valid(user, payload.code):
            org = db.get(Organization, user.org_id)
            if org is not None and org.suspended:
                raise HTTPException(status_code=403, detail="This workspace is suspended")
            mfa.clear_code(user)
            db.commit()
            _establish_session(request, user)
            login_history.record(db, request, user.email, True, user)
            return {"email": user.email, "role": user.role, "org_id": str(user.org_id)}
    raise HTTPException(status_code=401, detail="Invalid or expired code")


class ForgotPasswordRequest(BaseModel):
    email: str


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


@router.post("/v1/auth/forgot-password")
@limiter.limit("5/minute")
def forgot_password(payload: ForgotPasswordRequest, request: Request,
                    db: Session = Depends(get_db)):
    """Email a reset link to every ACTIVE account with this address. Always 200
    and silent — never reveals whether the email exists (enumeration-safe)."""
    email_addr = payload.email.strip().lower()
    users = db.execute(select(User).where(User.email == email_addr)).scalars().all()
    base = get_settings().dashboard_url
    for user in users:
        if not user.disabled:
            password_reset.issue_reset(db, user, user.email, base)
    return {"status": "ok"}


@router.post("/v1/auth/reset-password")
@limiter.limit("5/minute")
def reset_password(payload: ResetPasswordRequest, request: Request,
                   db: Session = Depends(get_db)):
    """Consume a single-use reset token and set the new password."""
    if len(payload.new_password) < 8:
        raise HTTPException(status_code=422,
                            detail="new password must be at least 8 characters")
    token_hash = password_reset.hash_token(payload.token)
    users = db.execute(
        select(User).where(User.reset_token_hash == token_hash)).scalars().all()
    for user in users:
        if not user.disabled and password_reset.token_valid(user, payload.token):
            user.password_hash = _bcrypt(payload.new_password)
            password_reset.clear_reset(user)
            db.commit()
            return {"status": "password_reset"}
    raise HTTPException(status_code=401, detail="Invalid or expired reset link")


@router.post("/v1/auth/logout")
def logout(request: Request):
    request.session.clear()
    return {"status": "logged_out"}


# ── Desktop pet → dashboard auto-login handoff (single-use, short-TTL token) ──
HANDOFF_TTL = timedelta(seconds=120)


class HandoffRedeemRequest(BaseModel):
    token: str


@router.post("/v1/auth/handoff")
def create_handoff(org: Organization = Depends(resolve_org), db: Session = Depends(get_db)):
    """Mint a single-use ~2-minute token for this org's admin. The desktop pet (which
    holds the org API key) calls this, then opens the dashboard with ?handoff=<token> so
    the person lands already logged in — no bare unauthenticated link, no retyped password."""
    admin = db.execute(
        select(User).where(User.org_id == org.id, User.role == "admin",
                           User.disabled.is_(False)).order_by(User.email)
    ).scalars().first()
    if admin is None:
        raise HTTPException(status_code=404, detail="no active admin user for this workspace")
    raw = secrets.token_urlsafe(32)
    db.add(AuthHandoffToken(
        token_hash=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        user_id=admin.id, org_id=org.id,
        expires_at=datetime.now(timezone.utc) + HANDOFF_TTL,
    ))
    db.commit()
    return {"token": raw, "expires_in": int(HANDOFF_TTL.total_seconds())}


@router.post("/v1/auth/handoff/redeem")
@limiter.limit("30/minute")
def redeem_handoff(payload: HandoffRedeemRequest, request: Request,
                   db: Session = Depends(get_db)):
    """Redeem a handoff token (unauthenticated — the token IS the credential): require it
    to be single-use + unexpired, mark it used, and establish the dashboard session."""
    token_hash = hashlib.sha256(payload.token.encode("utf-8")).hexdigest()
    row = db.execute(
        select(AuthHandoffToken).where(AuthHandoffToken.token_hash == token_hash)
    ).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if row is None or row.used_at is not None or row.expires_at < now:
        raise HTTPException(status_code=401, detail="Invalid or expired handoff")
    user = db.get(User, row.user_id)
    if user is None or user.disabled:
        raise HTTPException(status_code=401, detail="Invalid or expired handoff")
    org = db.get(Organization, user.org_id)
    if org is None or org.deleted_at is not None:
        raise HTTPException(status_code=403, detail="This workspace has been deleted")
    row.used_at = now
    db.commit()
    _establish_session(request, user)
    login_history.record(db, request, user.email, True, user)
    return {"email": user.email, "role": user.role, "org_id": str(user.org_id)}


@router.get("/v1/auth/me", response_model=MeResponse)
def me(user: User = Depends(require_user)):
    return MeResponse(email=user.email, role=user.role, org_id=str(user.org_id))


class LoginHistoryItem(BaseModel):
    email: str
    ip: str | None = None
    user_agent: str | None = None
    success: bool
    created_at: str | None = None


@router.get("/v1/auth/login-history", response_model=list[LoginHistoryItem])
def login_history_view(admin: User = Depends(require_role("admin")),
                       db: Session = Depends(get_db)):
    """Recent login attempts for the admin's org (successes + attributed failures)."""
    rows = db.execute(
        select(LoginEvent).where(LoginEvent.org_id == admin.org_id)
        .order_by(LoginEvent.created_at.desc()).limit(50)
    ).scalars().all()
    return [LoginHistoryItem(
        email=r.email, ip=r.ip, user_agent=r.user_agent, success=r.success,
        created_at=r.created_at.isoformat() if r.created_at else None) for r in rows]


@router.get("/v1/auth/users", response_model=list[UserListItem])
def list_users(
    admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """List the org's dashboard users — admin only (drives the settings page)."""
    users = db.execute(
        select(User).where(User.org_id == admin.org_id).order_by(User.email)
    ).scalars().all()
    return [UserListItem(id=str(u.id), email=u.email, role=u.role, disabled=u.disabled)
            for u in users]


@router.post("/v1/auth/users", response_model=CreateUserResponse)
def create_user(
    payload: CreateUserRequest,
    admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Admin adds a dashboard user in their org. Without an explicit password the
    user is INVITED — created with an unusable random password and emailed a
    set-password link (5D.2). With a password, it's set directly (shown once)."""
    role = payload.role.strip().lower()
    if role not in _VALID_ROLES:
        raise HTTPException(status_code=422, detail=f"role must be one of {sorted(_VALID_ROLES)}")
    email = payload.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=422, detail="a valid email is required")
    org = db.get(Organization, admin.org_id)
    seat_limit = get_settings().seat_limit_for(org.plan_tier if org else None)
    if seat_limit is not None:
        active_seats = db.execute(
            select(func.count()).select_from(User).where(
                User.org_id == admin.org_id, User.disabled.is_(False))
        ).scalar_one()
        if int(active_seats) >= seat_limit:
            raise HTTPException(
                status_code=402,
                detail={"code": "seat_limit_reached", "message": "Your plan has no available dashboard seats. Upgrade to invite another employee.",
                        "used": int(active_seats), "included": seat_limit},
            )
    invited = not payload.password
    # An invited account gets an unguessable, unusable password until the invitee
    # sets their own via the emailed link; a supplied password is used directly.
    temp = payload.password or secrets.token_urlsafe(32)

    user = User(org_id=admin.org_id, email=email, password_hash=_bcrypt(temp), role=role)
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="a user with that email already exists")
    db.refresh(user)
    if invited:
        password_reset.issue_reset(db, user, user.email,
                                   get_settings().dashboard_url, invite=True)
        return CreateUserResponse(id=str(user.id), email=user.email, role=user.role,
                                  invited=True,
                                  message="An invite email with a set-password link was sent.")
    return CreateUserResponse(id=str(user.id), email=user.email, role=user.role,
                              temp_password=temp)


@router.post("/v1/auth/change-password")
def change_password(
    payload: ChangePasswordRequest,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Self-service password change (any logged-in user)."""
    if not bcrypt.checkpw(payload.current_password.encode("utf-8"),
                          user.password_hash.encode("utf-8")):
        raise HTTPException(status_code=403, detail="current password is incorrect")
    if len(payload.new_password) < 8:
        raise HTTPException(status_code=422, detail="new password must be at least 8 characters")
    user.password_hash = _bcrypt(payload.new_password)
    db.commit()
    return {"status": "password_changed"}


@router.post("/v1/auth/users/{user_id}/disable")
def disable_user(
    user_id: str,
    admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Admin disables a user in their org. Cannot disable yourself (lockout guard)."""
    try:
        uid = uuid.UUID(str(user_id))
    except ValueError:
        raise HTTPException(status_code=422, detail="invalid user id")
    if uid == admin.id:
        raise HTTPException(status_code=400, detail="you cannot disable your own account")
    target = db.execute(
        select(User).where(User.id == uid, User.org_id == admin.org_id)
    ).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="user not found")
    target.disabled = True
    db.commit()
    return {"status": "disabled", "id": str(target.id)}
