"""Staff-account management for the admin site — superadmin only.

Mounted under /admin → /admin/v1/staff*. Only a superadmin can create or disable
platform staff, so operators/viewers can never escalate their own privileges.
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

from ..admin_audit import client_ip, record_admin_action
from ..auth import require_platform_role
from ..db import get_db
from ..models import StaffUser

router = APIRouter()

_VALID_PLATFORM_ROLES = {"viewer", "operator", "superadmin"}


class StaffListItem(BaseModel):
    id: str
    email: str
    platform_role: str
    disabled: bool


class CreateStaffRequest(BaseModel):
    email: str
    platform_role: str = "viewer"
    password: str | None = None      # omit to auto-generate a one-time temp password


class CreateStaffResponse(BaseModel):
    id: str
    email: str
    platform_role: str
    temp_password: str
    message: str = "Share this temporary password securely — it is shown once."


@router.get("/v1/staff", response_model=list[StaffListItem])
def list_staff(
    staff: StaffUser = Depends(require_platform_role("superadmin")),
    db: Session = Depends(get_db),
):
    rows = db.execute(select(StaffUser).order_by(StaffUser.email)).scalars().all()
    return [StaffListItem(id=str(s.id), email=s.email, platform_role=s.platform_role,
                          disabled=s.disabled) for s in rows]


@router.post("/v1/staff", response_model=CreateStaffResponse)
def create_staff(
    payload: CreateStaffRequest,
    request: Request,
    staff: StaffUser = Depends(require_platform_role("superadmin")),
    db: Session = Depends(get_db),
):
    role = payload.platform_role.strip().lower()
    if role not in _VALID_PLATFORM_ROLES:
        raise HTTPException(status_code=422,
                            detail=f"platform_role must be one of {sorted(_VALID_PLATFORM_ROLES)}")
    email = payload.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=422, detail="a valid email is required")
    temp = payload.password or ("foxy-staff-" + secrets.token_urlsafe(9))
    ph = bcrypt.hashpw(temp.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    new_staff = StaffUser(email=email, password_hash=ph, platform_role=role,
                          created_by=staff.id)
    db.add(new_staff)
    record_admin_action(db, staff, "staff.create", target_type="staff_user",
                        detail={"email": email, "platform_role": role}, ip=client_ip(request))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="a staff user with that email already exists")
    db.refresh(new_staff)
    return CreateStaffResponse(id=str(new_staff.id), email=new_staff.email,
                               platform_role=new_staff.platform_role, temp_password=temp)


@router.post("/v1/staff/{staff_id}/disable")
def disable_staff(
    staff_id: str,
    request: Request,
    staff: StaffUser = Depends(require_platform_role("superadmin")),
    db: Session = Depends(get_db),
):
    """Disable a staff account. Cannot disable yourself (lockout guard)."""
    try:
        sid = uuid.UUID(str(staff_id))
    except ValueError:
        raise HTTPException(status_code=422, detail="invalid staff id")
    if sid == staff.id:
        raise HTTPException(status_code=400, detail="you cannot disable your own account")
    target = db.get(StaffUser, sid)
    if target is None:
        raise HTTPException(status_code=404, detail="staff user not found")
    target.disabled = True
    record_admin_action(db, staff, "staff.disable", target_type="staff_user",
                        target_id=str(target.id), detail={"email": target.email},
                        ip=client_ip(request))
    db.commit()
    return {"status": "disabled", "id": str(target.id)}
