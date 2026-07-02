"""Human session auth for the web dashboard.

Email/password login over a signed cookie session (Starlette SessionMiddleware).
This is deliberately a SEPARATE channel from the SDK's `Authorization: Bearer`
machine key (see auth.require_org): browsers get a cookie, the SDK keeps its
header, and the two never collide.
"""

from __future__ import annotations

import secrets
import uuid

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth import require_role, require_user
from ..db import get_db
from ..models import User

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
    temp_password: str
    message: str = "Share this temporary password securely — it is shown once."


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


# A fixed bcrypt hash used to equalize login timing when no user matches the
# email, so an attacker can't distinguish "no such email" from "wrong password"
# by response time — both paths run exactly one bcrypt verify.
_DUMMY_HASH = b"$2b$12$Rz1JAD5efasLHu5D.kolz.QagN8aF7XSazm89wlVY8DJ/cjvNXsrm"


@router.post("/v1/auth/login", response_model=MeResponse)
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
            request.session["user_id"] = str(user.id)
            request.session["org_id"] = str(user.org_id)
            request.session["role"] = user.role
            return MeResponse(email=user.email, role=user.role, org_id=str(user.org_id))
    if not candidates:
        bcrypt.checkpw(pw, _DUMMY_HASH)       # equalize timing vs. the valid-email path
    if disabled_match:
        raise HTTPException(status_code=403, detail="Account disabled")
    raise HTTPException(status_code=401, detail="Invalid email or password")


@router.post("/v1/auth/logout")
def logout(request: Request):
    request.session.clear()
    return {"status": "logged_out"}


@router.get("/v1/auth/me", response_model=MeResponse)
def me(user: User = Depends(require_user)):
    return MeResponse(email=user.email, role=user.role, org_id=str(user.org_id))


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
    """Admin invites a dashboard user in their org with a temp password."""
    role = payload.role.strip().lower()
    if role not in _VALID_ROLES:
        raise HTTPException(status_code=422, detail=f"role must be one of {sorted(_VALID_ROLES)}")
    email = payload.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=422, detail="a valid email is required")
    temp = payload.password or ("foxy-" + secrets.token_urlsafe(9))

    user = User(org_id=admin.org_id, email=email, password_hash=_bcrypt(temp), role=role)
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="a user with that email already exists")
    db.refresh(user)
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
