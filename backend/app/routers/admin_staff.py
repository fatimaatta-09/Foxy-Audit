"""Staff-account management for the admin site — superadmin only.

Mounted under /admin → /admin/v1/staff*. Only a superadmin can create or disable
platform staff, so operators/viewers can never escalate their own privileges.
"""

from __future__ import annotations

import secrets
import uuid

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import password_reset
from ..admin_audit import client_ip, record_admin_action
from .. import notify
from ..auth import require_platform_role, require_step_up_dep
from ..config import get_settings
from ..db import get_db
from ..models import AdminAction, StaffUser

router = APIRouter()

_VALID_PLATFORM_ROLES = {"viewer", "operator", "superadmin"}


class StaffListItem(BaseModel):
    id: str
    email: str
    platform_role: str
    disabled: bool
    mfa_enabled: bool = False
    created_at: str | None = None
    last_login: str | None = None


class CreateStaffRequest(BaseModel):
    email: str
    platform_role: str = "viewer"
    password: str | None = None      # omit to auto-generate a one-time temp password


class CreateStaffResponse(BaseModel):
    id: str
    email: str
    platform_role: str
    temp_password: str | None = None
    invited: bool = False
    message: str = "Share this temporary password securely — it is shown once."


@router.get("/v1/staff", response_model=list[StaffListItem])
def list_staff(
    staff: StaffUser = Depends(require_platform_role("superadmin")),
    db: Session = Depends(get_db),
):
    rows = db.execute(select(StaffUser).order_by(StaffUser.email)).scalars().all()
    last_login = {
        sid: ts for sid, ts in db.execute(
            select(AdminAction.staff_user_id, func.max(AdminAction.created_at))
            .where(AdminAction.action == "staff.login")
            .group_by(AdminAction.staff_user_id)
        ).all()
    }
    return [
        StaffListItem(
            id=str(s.id), email=s.email, platform_role=s.platform_role, disabled=s.disabled,
            mfa_enabled=s.mfa_enabled, created_at=s.created_at.isoformat() if s.created_at else None,
            last_login=last_login[s.id].isoformat() if last_login.get(s.id) else None)
        for s in rows
    ]


@router.post("/v1/staff", response_model=CreateStaffResponse, dependencies=[Depends(require_step_up_dep)])
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
    invited = not payload.password
    # Invited staff get an unusable random password until they set their own via
    # the emailed link; a supplied password is used directly. (5D.2)
    temp = payload.password or secrets.token_urlsafe(32)
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
    if invited:
        password_reset.issue_reset(db, new_staff, new_staff.email,
                                   get_settings().admin_url, invite=True, surface="staff")
        return CreateStaffResponse(id=str(new_staff.id), email=new_staff.email,
                                   platform_role=new_staff.platform_role, invited=True,
                                   message="An invite email with a set-password link was sent.")
    return CreateStaffResponse(id=str(new_staff.id), email=new_staff.email,
                               platform_role=new_staff.platform_role, temp_password=temp)


@router.post("/v1/staff/{staff_id}/disable", dependencies=[Depends(require_step_up_dep)])
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
    notify.notify_staff(db, target, "staff_disable", "Your account was disabled",
                        body="A superadmin disabled your staff account.", level="warning",
                        target_type="staff_user", target_id=str(target.id))
    db.commit()
    return {"status": "disabled", "id": str(target.id)}


# ======================= Staff management actions (Phase 2, audited) =======================


def _get_target(db: Session, staff_id: str) -> StaffUser:
    try:
        sid = uuid.UUID(str(staff_id))
    except ValueError:
        raise HTTPException(status_code=422, detail="invalid staff id")
    target = db.get(StaffUser, sid)
    if target is None:
        raise HTTPException(status_code=404, detail="staff user not found")
    return target


class RoleBody(BaseModel):
    platform_role: str


@router.post("/v1/staff/{staff_id}/role", dependencies=[Depends(require_step_up_dep)])
def set_staff_role(
    staff_id: str,
    body: RoleBody,
    request: Request,
    staff: StaffUser = Depends(require_platform_role("superadmin")),
    db: Session = Depends(get_db),
):
    """Change a staff member's platform role. Guards self-demotion and the removal
    of the last active superadmin so the console can never be locked out."""
    role = body.platform_role.strip().lower()
    if role not in _VALID_PLATFORM_ROLES:
        raise HTTPException(status_code=422,
                            detail=f"platform_role must be one of {sorted(_VALID_PLATFORM_ROLES)}")
    target = _get_target(db, staff_id)
    if target.id == staff.id:
        raise HTTPException(status_code=400, detail="you cannot change your own role")
    if target.platform_role == "superadmin" and role != "superadmin":
        others = db.execute(
            select(func.count()).select_from(StaffUser)
            .where(StaffUser.platform_role == "superadmin",
                   StaffUser.disabled.is_(False), StaffUser.id != target.id)
        ).scalar_one()
        if others == 0:
            raise HTTPException(status_code=400, detail="cannot demote the last active superadmin")
    old = target.platform_role
    target.platform_role = role
    record_admin_action(db, staff, "staff.role", target_type="staff_user",
                        target_id=str(target.id),
                        detail={"email": target.email, "from": old, "to": role},
                        ip=client_ip(request))
    notify.notify_staff(db, target, "staff_role", "Your platform role changed",
                        body=f"{old} → {role}", target_type="staff_user",
                        target_id=str(target.id))
    db.commit()
    return {"status": "updated", "id": str(target.id), "platform_role": role}


@router.post("/v1/staff/{staff_id}/enable", dependencies=[Depends(require_step_up_dep)])
def enable_staff(
    staff_id: str,
    request: Request,
    staff: StaffUser = Depends(require_platform_role("superadmin")),
    db: Session = Depends(get_db),
):
    """Re-enable a disabled staff account."""
    target = _get_target(db, staff_id)
    target.disabled = False
    record_admin_action(db, staff, "staff.enable", target_type="staff_user",
                        target_id=str(target.id), detail={"email": target.email},
                        ip=client_ip(request))
    notify.notify_staff(db, target, "staff_enable", "Your account was re-enabled",
                        body="Your staff account was re-enabled.",
                        target_type="staff_user", target_id=str(target.id))
    db.commit()
    return {"status": "enabled", "id": str(target.id)}


@router.post("/v1/staff/{staff_id}/mfa/reset", dependencies=[Depends(require_step_up_dep)])
def reset_staff_mfa(
    staff_id: str,
    request: Request,
    staff: StaffUser = Depends(require_platform_role("superadmin")),
    db: Session = Depends(get_db),
):
    """Clear a staff member's MFA enrolment (recovery for a lost authenticator)."""
    target = _get_target(db, staff_id)
    target.mfa_enabled = False
    target.mfa_code_hash = None
    target.mfa_code_expires_at = None
    record_admin_action(db, staff, "staff.mfa_reset", target_type="staff_user",
                        target_id=str(target.id), detail={"email": target.email},
                        ip=client_ip(request))
    notify.notify_staff(db, target, "staff_mfa_reset", "Your MFA was reset",
                        body="A superadmin reset your MFA enrolment. Re-enrol from Settings.",
                        level="warning", target_type="staff_user", target_id=str(target.id))
    db.commit()
    return {"status": "mfa_reset", "id": str(target.id)}


@router.get("/v1/staff/{staff_id}/activity")
def staff_activity(
    staff_id: str,
    staff: StaffUser = Depends(require_platform_role("superadmin")),
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
):
    """Recent audit trail for a staff member — both actions they performed and
    actions performed ON their account (role change, disable, MFA reset)."""
    target = _get_target(db, staff_id)
    sid = target.id
    rows = db.execute(
        select(AdminAction).where(
            or_(AdminAction.staff_user_id == sid,
                and_(AdminAction.target_type == "staff_user",
                     AdminAction.target_id == str(sid)))
        ).order_by(AdminAction.created_at.desc()).limit(limit)
    ).scalars().all()
    return {"items": [
        {"action": a.action, "target_type": a.target_type, "target_id": a.target_id,
         "target_org_id": str(a.target_org_id) if a.target_org_id else None,
         "detail": a.detail, "ip": a.ip,
         "at": a.created_at.isoformat() if a.created_at else None,
         "by_self": str(a.staff_user_id) == str(sid)}
        for a in rows
    ]}
