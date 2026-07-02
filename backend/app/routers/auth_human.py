"""Human session auth for the web dashboard.

Email/password login over a signed cookie session (Starlette SessionMiddleware).
This is deliberately a SEPARATE channel from the SDK's `Authorization: Bearer`
machine key (see auth.require_org): browsers get a cookie, the SDK keeps its
header, and the two never collide.
"""

from __future__ import annotations

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import require_role, require_user
from ..db import get_db
from ..models import User

router = APIRouter()


class LoginRequest(BaseModel):
    email: str
    password: str


class MeResponse(BaseModel):
    email: str
    role: str
    org_id: str


class UserListItem(BaseModel):
    email: str
    role: str


@router.post("/v1/auth/login", response_model=MeResponse)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    user = db.execute(
        select(User).where(User.email == payload.email.strip().lower())
    ).scalars().first()
    # Constant-ish check: only verify if a user exists; generic 401 either way.
    if user is None or not bcrypt.checkpw(
        payload.password.encode("utf-8"), user.password_hash.encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    request.session["user_id"] = str(user.id)
    request.session["org_id"] = str(user.org_id)
    request.session["role"] = user.role
    return MeResponse(email=user.email, role=user.role, org_id=str(user.org_id))


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
    return [UserListItem(email=u.email, role=u.role) for u in users]
