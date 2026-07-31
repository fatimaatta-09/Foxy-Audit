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

from .. import (account_audit, email as email_mod, email_templates as et, login_history, mfa,
                password_reset, user_notifications)
from ..auth import (_scope_org, grant_step_up, hash_session_token, require_role,
                    require_step_up_user, require_user, resolve_org)
from ..config import get_settings
from ..db import get_db
from ..ip_allow import client_ip
from ..models import (AuthHandoffToken, LoginEvent, Organization, User, UserSession,
                      VerificationCode)
from .logs import limiter          # reuse the app's single Limiter instance

router = APIRouter()

_VALID_ROLES = {"admin", "member"}


def _bcrypt(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


class LoginRequest(BaseModel):
    email: str
    password: str
    remember_me: bool = False     # P3: mint a 30-day device session vs the 12h default


class MeResponse(BaseModel):
    email: str
    role: str
    # NO org_id. P3 §7.1: the workspace id is a sensitive reveal, and it used to
    # ride along here while the dashboard merely masked it in the DOM — the
    # value was already on the wire, so the mask protected nothing. It now comes
    # only from POST /v1/account/org-id, behind an emailed step-up code. The
    # login, MFA and handoff responses dropped it for the same reason: leaving
    # it on any un-gated path would let a client cache it at sign-in and make
    # the gate theatre again.
    mfa_enabled: bool = False
    full_name: str | None = None       # P14: identity — avatar/greeting use this once set
    preferences: dict | None = None    # P14: hide_sensitive_metadata + notification prefs
    # P6c. A BOOLEAN, not the bytes and not a path. The dashboard only needs to
    # know whether to render <img> or the initial fallback; the image itself comes
    # from GET /v1/account/avatar, which is session-scoped and cacheable. Putting
    # base64 here would put a picture on every /me poll and in every log line that
    # ever captures a response body.
    has_avatar: bool = False
    # The ?v= cache-buster the dashboard appends to the image URL, so a fresh
    # upload replaces the old picture at once. A timestamp, not a secret.
    avatar_updated_at: str | None = None


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


def _establish_session(request: Request, user: User, db: Session,
                       remember: bool = False) -> uuid.UUID:
    request.session.clear()   # rotate: drop any pre-existing (planted) session
    request.session["user_id"] = str(user.id)
    request.session["org_id"] = str(user.org_id)
    request.session["role"] = user.role
    # P3: mint a DB-backed device session (remember-me + active-devices + revoke). Scope RLS to the
    # user's org FIRST so the org_isolation WITH CHECK passes on INSERT; expires_at is the real gate.
    settings = get_settings()
    ttl = settings.session_remember_max_age if remember else settings.session_max_age
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    _scope_org(db, user.org_id)
    session_id = uuid.uuid4()
    db.add(UserSession(
        id=session_id, user_id=user.id, org_id=user.org_id,
        token_hash=hash_session_token(token),
        ip=(client_ip(request) or "")[:64] or None,
        user_agent=(request.headers.get("user-agent") or "")[:400] or None,
        created_at=now, last_seen_at=now, expires_at=now + timedelta(seconds=ttl),
    ))
    db.commit()
    request.session["session_token"] = token
    return session_id


def _record_login_and_alert(request: Request, user: User, db: Session, email: str,
                            session_id: uuid.UUID | None = None) -> None:
    """Record the successful attempt, then queue a new-device alert if warranted.

    The new-device question is asked BEFORE the attempt is recorded — otherwise
    this login is already in the history and matches itself, and no device is
    ever new. The alert is only ENQUEUED here; it is sent from the notifications
    thread, so a slow mail provider cannot delay or fail a sign-in (P3 §3)."""
    ua = (request.headers.get("user-agent") or "")[:256]
    try:
        new_device = login_history.is_new_device(db, user, ua)
    except Exception:                       # noqa: BLE001 — never break a login
        db.rollback()
        new_device = False
    login_history.record(db, request, email, True, user)
    if new_device:
        user_notifications.enqueue_new_device_alert(
            user_id=user.id, org_id=user.org_id, email=user.email,
            ip=client_ip(request), user_agent=ua, session_id=session_id)


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
    blocked_org: str | None = None
    for user in candidates:
        if bcrypt.checkpw(pw, user.password_hash.encode("utf-8")):
            if user.disabled:
                disabled_match = True
                continue                      # keep looking for an active account
            org = db.get(Organization, user.org_id)
            # A dead or suspended workspace must NOT shadow a healthy account on the
            # same address: remember why it was refused and keep scanning, exactly as
            # the `disabled` branch above does. Raising here locked a user out of a
            # good account because an unrelated stale org happened to match first.
            if org is not None and org.deleted_at is not None:
                blocked_org = blocked_org or "This workspace has been deleted"
                continue
            if org is not None and org.suspended:
                blocked_org = blocked_org or "This workspace is suspended"
                continue
            # Opt-in email-OTP MFA: email a code and stop — no session until it's
            # verified at /v1/auth/mfa. (Phase 5 · 5B.5)
            if user.mfa_enabled:
                mfa.issue_code(db, user, user.email)
                return {"mfa_required": True, "email": user.email}
            sid = _establish_session(request, user, db, payload.remember_me)
            _record_login_and_alert(request, user, db, email, sid)
            return {"email": user.email, "role": user.role}
    if not candidates:
        bcrypt.checkpw(pw, _DUMMY_HASH)       # equalize timing vs. the valid-email path
    if blocked_org:
        raise HTTPException(status_code=403, detail=blocked_org)
    if disabled_match:
        raise HTTPException(status_code=403, detail="Account disabled")
    # Attribute the failure to the org when the address resolves to exactly ONE
    # account. Failures were always recorded with org_id NULL, so the admin
    # login-history view — scoped to the org — never showed a single failed
    # attempt, which is the half of login history worth reading. Left unattributed
    # when the address exists in several tenants: guessing which one someone was
    # trying to reach would leak the existence of the other.
    attributed = candidates[0] if len(candidates) == 1 else None
    login_history.record(db, request, email, False, attributed)
    raise HTTPException(status_code=401, detail="Invalid email or password")


class MfaRequest(BaseModel):
    email: str
    code: str
    remember_me: bool = False     # P3: carry the remember-me choice through the MFA step


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
            sid = _establish_session(request, user, db, payload.remember_me)
            _record_login_and_alert(request, user, db, user.email, sid)
            return {"email": user.email, "role": user.role}
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
def logout(request: Request, db: Session = Depends(get_db)):
    # P3: revoke this device's DB session row so the opaque token can't be reused, then drop the cookie.
    token = request.session.get("session_token")
    org_id = request.session.get("org_id")
    if token and org_id:
        try:
            _scope_org(db, uuid.UUID(str(org_id)))
            db.query(UserSession).filter(
                UserSession.token_hash == hash_session_token(token),
                UserSession.revoked_at.is_(None),
            ).update({UserSession.revoked_at: datetime.now(timezone.utc)},
                     synchronize_session=False)
            db.commit()
        except Exception:
            db.rollback()
    request.session.clear()
    return {"status": "logged_out"}


# ─────────────────────────── device sessions (P3) ───────────────────────
@router.get("/v1/auth/sessions")
def list_sessions(request: Request, user: User = Depends(require_user),
                  db: Session = Depends(get_db)):
    """Your active device sessions (this-device flagged). token_hash is NEVER serialized."""
    cur = request.session.get("session_token")
    cur_hash = hash_session_token(cur) if cur else None
    now = datetime.now(timezone.utc)
    rows = db.execute(
        select(UserSession).where(
            UserSession.user_id == user.id, UserSession.revoked_at.is_(None)
        ).order_by(UserSession.created_at.desc())
    ).scalars().all()
    items = []
    for s in rows:
        if s.expires_at is not None and now >= s.expires_at:
            continue                             # expired (kept in table, just not shown as active)
        items.append({
            "id": str(s.id), "ip": s.ip, "user_agent": s.user_agent,
            "created_at": s.created_at.isoformat() if s.created_at else None,
            "last_seen_at": s.last_seen_at.isoformat() if s.last_seen_at else None,
            "expires_at": s.expires_at.isoformat() if s.expires_at else None,
            "current": cur_hash is not None and s.token_hash == cur_hash,
        })
    return {"items": items}


@router.post("/v1/auth/sessions/{session_id}/revoke")
def revoke_session(session_id: str, request: Request,
                   user: User = Depends(require_user), db: Session = Depends(get_db)):
    """Revoke one of your device sessions (active-devices list). Audited via account_actions."""
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="not found")
    s = db.get(UserSession, sid)          # RLS-scoped by require_user → cross-org rows are invisible
    if s is None or s.user_id != user.id:
        raise HTTPException(status_code=404, detail="not found")
    if s.revoked_at is None:
        s.revoked_at = datetime.now(timezone.utc)
        account_audit.record_account_action(
            db, org_id=user.org_id, actor_email=user.email,
            action="auth.session_revoke", target=str(s.id))
        db.commit()
    return {"status": "revoked", "id": str(s.id)}


@router.post("/v1/auth/logout-all")
def logout_all(request: Request, user: User = Depends(require_user),
               db: Session = Depends(get_db)):
    """Revoke ALL your device sessions (including this one) — log out everywhere. Audited."""
    n = db.query(UserSession).filter(
        UserSession.user_id == user.id, UserSession.revoked_at.is_(None)
    ).update({UserSession.revoked_at: datetime.now(timezone.utc)}, synchronize_session=False)
    account_audit.record_account_action(
        db, org_id=user.org_id, actor_email=user.email,
        action="auth.logout_all", target=str(user.id), detail={"revoked": int(n)})
    db.commit()
    request.session.clear()
    return {"status": "logged_out_everywhere", "revoked": int(n)}


# ─────────────────────────── step-up (re-auth) for danger actions (P15) ───────
STEP_UP_CODE_TTL = timedelta(minutes=5)


@router.post("/v1/auth/step-up/request")
@limiter.limit("6/minute")
def step_up_request(request: Request, user: User = Depends(require_user),
                    db: Session = Depends(get_db)):
    """Email a fresh 6-digit step-up code (stored hashed in verification_codes — NEVER on the login-MFA
    columns, so step-up can't collide with email-OTP login). Invalidates prior unconsumed codes."""
    code = mfa.new_otp()
    now = datetime.now(timezone.utc)
    _scope_org(db, user.org_id)
    db.query(VerificationCode).filter(
        VerificationCode.user_id == user.id, VerificationCode.purpose == "step_up",
        VerificationCode.consumed_at.is_(None),
    ).update({VerificationCode.consumed_at: now}, synchronize_session=False)
    db.add(VerificationCode(id=uuid.uuid4(), user_id=user.id, org_id=user.org_id, purpose="step_up",
                            code_hash=mfa.hash_code(code), expires_at=now + STEP_UP_CODE_TTL))
    db.commit()
    html, plain = et.layout(
        title="Confirm it's you",
        preheader=f"Your Foxy Audit security code is {code}",
        blocks=[
            et.paragraph("Use this one-time code to confirm a sensitive change to your account."),
            et.code_block(code),
            et.muted("It expires in 5 minutes. If you didn't request this, ignore this email and "
                     "change your password."),
        ],
    )
    email_mod.send_email(to=user.email, subject="Your Foxy Audit security code", html=html, text=plain)
    return {"status": "code_sent", "email": user.email}


class StepUpConfirmRequest(BaseModel):
    code: str


@router.post("/v1/auth/step-up/confirm")
@limiter.limit("10/minute")
def step_up_confirm(payload: StepUpConfirmRequest, request: Request,
                    user: User = Depends(require_user), db: Session = Depends(get_db)):
    """Verify the emailed code and mint a ~10-min step-up grant for danger actions (a burst then needs
    a single code, not one per click). Wrong/expired code → 400. Audited via account_actions."""
    code = (payload.code or "").strip()
    now = datetime.now(timezone.utc)
    _scope_org(db, user.org_id)
    row = db.execute(
        select(VerificationCode).where(
            VerificationCode.user_id == user.id, VerificationCode.purpose == "step_up",
            VerificationCode.consumed_at.is_(None), VerificationCode.expires_at >= now,
        ).order_by(VerificationCode.created_at.desc())
    ).scalars().first()
    if row is None or not code or row.code_hash != mfa.hash_code(code):
        raise HTTPException(status_code=400, detail="invalid or expired code")
    row.consumed_at = now
    grant_step_up(request)
    account_audit.record_account_action(
        db, org_id=user.org_id, actor_email=user.email, action="account.step_up")
    db.commit()
    return {"status": "step_up_granted"}


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
    sid = _establish_session(request, user, db)
    _record_login_and_alert(request, user, db, user.email, sid)
    return {"email": user.email, "role": user.role}


@router.get("/v1/auth/me", response_model=MeResponse)
def me(user: User = Depends(require_user)):
    return MeResponse(email=user.email, role=user.role,
                      mfa_enabled=bool(user.mfa_enabled),
                      full_name=user.full_name, preferences=user.preferences or {},
                      has_avatar=bool(user.avatar_path),
                      avatar_updated_at=(user.avatar_updated_at.isoformat()
                                         if user.avatar_updated_at else None))


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


@router.get("/v1/auth/login-history/me", response_model=list[LoginHistoryItem])
def my_login_history(user: User = Depends(require_user), db: Session = Depends(get_db)):
    """YOUR OWN recent sign-in attempts (P3 §7.3).

    The org-wide view above is admin-only, and rightly so — one member should not
    read another's IP addresses. But that left a member with no way to check their
    own account for a sign-in they do not recognise, which is the entire point of
    surfacing login history. Scoped to this user's own events only."""
    rows = db.execute(
        select(LoginEvent).where(LoginEvent.user_id == user.id)
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


@router.post("/v1/auth/change-password", dependencies=[Depends(require_step_up_user)])
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
    account_audit.record_account_action(
        db, org_id=admin.org_id, actor_email=admin.email, action="member.disable",
        target=target.email)
    db.commit()
    return {"status": "disabled", "id": str(target.id)}


# ─────────────────────── member management (P1 · §D) ────────────────────────

def _target_user(user_id: str, admin: User, db: Session) -> User:
    try:
        uid = uuid.UUID(str(user_id))
    except ValueError:
        raise HTTPException(status_code=422, detail="invalid user id")
    target = db.execute(
        select(User).where(User.id == uid, User.org_id == admin.org_id)
    ).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="user not found")
    return target


def _active_admin_count(org_id, db: Session, exclude=None) -> int:
    q = select(func.count()).select_from(User).where(
        User.org_id == org_id, User.role == "admin", User.disabled.is_(False))
    if exclude is not None:
        q = q.where(User.id != exclude)
    return db.execute(q).scalar_one()


class RoleChangeRequest(BaseModel):
    role: str


@router.post("/v1/auth/users/{user_id}/role", dependencies=[Depends(require_step_up_user)])
def change_role(
    user_id: str,
    payload: RoleChangeRequest,
    admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Change a member's role (admin ↔ member). Refuses to demote the last active
    admin, so an org can never lock itself out of admin access."""
    role = payload.role.strip().lower()
    if role not in _VALID_ROLES:
        raise HTTPException(status_code=422, detail=f"role must be one of {sorted(_VALID_ROLES)}")
    target = _target_user(user_id, admin, db)
    if target.role == "admin" and role != "admin" and _active_admin_count(
            admin.org_id, db, exclude=target.id) == 0:
        raise HTTPException(status_code=400,
                            detail="this is the last admin — promote another member first")
    target.role = role
    account_audit.record_account_action(
        db, org_id=admin.org_id, actor_email=admin.email, action="member.role_change",
        target=target.email, detail={"role": role})
    db.commit()
    return {"status": "role_changed", "id": str(target.id), "role": target.role}


@router.post("/v1/auth/users/{user_id}/enable")
def enable_user(
    user_id: str,
    admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Re-enable a previously disabled user in this org."""
    target = _target_user(user_id, admin, db)
    target.disabled = False
    account_audit.record_account_action(
        db, org_id=admin.org_id, actor_email=admin.email, action="member.enable",
        target=target.email)
    db.commit()
    return {"status": "enabled", "id": str(target.id)}


@router.post("/v1/auth/users/{user_id}/resend-invite")
def resend_invite(
    user_id: str,
    admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Re-send the set-password / invite link to a user who hasn't finished setup."""
    target = _target_user(user_id, admin, db)
    if target.disabled:
        raise HTTPException(status_code=400, detail="enable the user before re-inviting")
    password_reset.issue_reset(db, target, target.email,
                               get_settings().dashboard_url, invite=True)
    return {"status": "invite_sent", "id": str(target.id)}


# ─────────────────── MFA self-enrollment (P1 · §D) ──────────────────────────

class MfaCodeRequest(BaseModel):
    code: str


class MfaDisableRequest(BaseModel):
    password: str


@router.post("/v1/auth/mfa/enroll")
def mfa_enroll(user: User = Depends(require_user), db: Session = Depends(get_db)):
    """Start self-enrollment: email the logged-in user a one-time code they confirm
    at /v1/auth/mfa/enable to switch two-factor on. Reuses the login OTP machinery."""
    mfa.issue_code(db, user, user.email)
    return {"status": "code_sent"}


@router.post("/v1/auth/mfa/enable")
def mfa_enable(payload: MfaCodeRequest, user: User = Depends(require_user),
               db: Session = Depends(get_db)):
    """Confirm the emailed code and turn two-factor ON for this account."""
    if not mfa.code_valid(user, payload.code.strip()):
        raise HTTPException(status_code=403, detail="that code is invalid or expired")
    mfa.clear_code(user)
    user.mfa_enabled = True
    account_audit.record_account_action(
        db, org_id=user.org_id, actor_email=user.email, action="mfa.enable")
    db.commit()
    return {"status": "mfa_enabled"}


@router.post("/v1/auth/mfa/disable", dependencies=[Depends(require_step_up_user)])
def mfa_disable(payload: MfaDisableRequest, user: User = Depends(require_user),
                db: Session = Depends(get_db)):
    """Turn two-factor OFF — re-checks the account password as a step-up guard."""
    if not bcrypt.checkpw(payload.password.encode("utf-8"),
                          user.password_hash.encode("utf-8")):
        raise HTTPException(status_code=403, detail="password is incorrect")
    mfa.clear_code(user)
    user.mfa_enabled = False
    account_audit.record_account_action(
        db, org_id=user.org_id, actor_email=user.email, action="mfa.disable")
    db.commit()
    return {"status": "mfa_disabled"}
