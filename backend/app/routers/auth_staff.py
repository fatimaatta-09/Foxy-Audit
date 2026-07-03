"""Platform-staff session auth for the internal admin site ("site 3").

A DELIBERATELY separate channel from customer auth (auth_human.py): staff log in
here over the `foxy_staff_session` cookie (a distinct SessionMiddleware on the
admin sub-app, signed with STAFF_SESSION_SECRET). This router is mounted under
`/admin`, so its paths resolve to `/admin/v1/auth/*`. It only ever queries
`staff_users`, so a customer `users` credential can never mint a staff session.
"""

from __future__ import annotations

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import require_staff
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


@router.post("/v1/auth/login", response_model=StaffMeResponse)
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

    # Rotate the session on login (drop any prior anonymous session id) to close
    # session fixation, then stamp the staff identity under a NAMESPACED key that
    # a customer session never carries.
    request.session.clear()
    request.session["staff_user_id"] = str(staff.id)
    request.session["staff_role"] = staff.platform_role
    return StaffMeResponse(id=str(staff.id), email=staff.email,
                           platform_role=staff.platform_role)


@router.post("/v1/auth/logout")
def staff_logout(request: Request):
    request.session.clear()
    return {"status": "logged_out"}


@router.get("/v1/auth/me", response_model=StaffMeResponse)
def staff_me(staff: StaffUser = Depends(require_staff)):
    return StaffMeResponse(id=str(staff.id), email=staff.email,
                           platform_role=staff.platform_role)
