"""Platform configuration, feature flags, announcements + broadcast (P3 · §F/§G).

Mounted under /admin -> /admin/v1/config, /announcements, /broadcast. Config reads/
writes are superadmin-only and audited; the active-announcement list is readable by
any staff so the console can render the broadcast banner.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import email as email_mod
from .. import platform_config as cfg
from ..admin_audit import client_ip, record_admin_action
from ..auth import require_platform_role, require_staff, require_step_up_dep
from ..db import get_db
from ..models import PlatformAnnouncement, StaffUser

router = APIRouter()


# ─────────────────────────── platform config / flags ────────────────────────

@router.get("/v1/config")
def get_config(
    staff: StaffUser = Depends(require_platform_role("superadmin")),
    db: Session = Depends(get_db),
):
    """Effective platform config: each editable key with value/default/override + meta."""
    return {"config": cfg.effective(db)}


class ConfigUpdate(BaseModel):
    updates: dict


@router.put("/v1/config", dependencies=[Depends(require_step_up_dep)])
def put_config(
    body: ConfigUpdate,
    request: Request,
    staff: StaffUser = Depends(require_platform_role("superadmin")),
    db: Session = Depends(get_db),
):
    """Set one or more config overrides. Unknown keys / bad types are rejected 422."""
    if not body.updates:
        raise HTTPException(status_code=422, detail="no updates provided")
    try:
        changed = cfg.set_values(db, body.updates, staff.id)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc))
    record_admin_action(db, staff, "config.update", target_type="platform_config",
                        detail={"keys": changed, "updates": body.updates},
                        ip=client_ip(request))
    db.commit()
    return {"status": "updated", "changed": changed, "config": cfg.effective(db)}


# ─────────────────────────── announcements / broadcast ──────────────────────

def _serialize(a: PlatformAnnouncement) -> dict:
    return {
        "id": str(a.id), "title": a.title, "body": a.body, "level": a.level,
        "active": a.active,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }


@router.get("/v1/announcements")
def list_announcements(
    staff: StaffUser = Depends(require_staff),
    db: Session = Depends(get_db),
    include_inactive: bool = False,
):
    """Announcements for the console banner. Active-only unless include_inactive."""
    stmt = select(PlatformAnnouncement).order_by(PlatformAnnouncement.created_at.desc())
    if not include_inactive:
        stmt = stmt.where(PlatformAnnouncement.active.is_(True))
    rows = db.execute(stmt).scalars().all()
    return {"items": [_serialize(a) for a in rows]}


class BroadcastRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=8000)
    level: str = "info"
    email_staff: bool = False


@router.post("/v1/broadcast", dependencies=[Depends(require_step_up_dep)])
def broadcast(
    body: BroadcastRequest,
    request: Request,
    staff: StaffUser = Depends(require_platform_role("superadmin")),
    db: Session = Depends(get_db),
):
    """Post a console banner announcement + optionally email all active staff."""
    level = body.level.strip().lower()
    if level not in ("info", "warning", "critical"):
        raise HTTPException(status_code=422, detail="level must be info, warning, or critical")
    ann = PlatformAnnouncement(id=uuid.uuid4(), title=body.title.strip(),
                               body=body.body.strip(), level=level, created_by=staff.id)
    db.add(ann)
    emailed = 0
    if body.email_staff:
        recipients = db.execute(
            select(StaffUser.email).where(StaffUser.disabled.is_(False))
        ).scalars().all()
        for to in recipients:
            if email_mod.send_email(
                to=to, subject=f"[Foxy Audit] {body.title.strip()}",
                html=f"<div style='font-family:sans-serif;font-size:14px'>{body.body.strip()}</div>",
                text=body.body.strip(),
            ):
                emailed += 1
    record_admin_action(db, staff, "broadcast.create", target_type="announcement",
                        target_id=str(ann.id),
                        detail={"title": ann.title, "level": level,
                                "email_staff": body.email_staff, "emailed": emailed},
                        ip=client_ip(request))
    db.commit()
    return {"status": "created", "id": str(ann.id), "emailed": emailed}


@router.post("/v1/announcements/{ann_id}/deactivate", dependencies=[Depends(require_step_up_dep)])
def deactivate_announcement(
    ann_id: str,
    request: Request,
    staff: StaffUser = Depends(require_platform_role("superadmin")),
    db: Session = Depends(get_db),
):
    try:
        aid = uuid.UUID(ann_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="invalid announcement id")
    ann = db.get(PlatformAnnouncement, aid)
    if ann is None:
        raise HTTPException(status_code=404, detail="announcement not found")
    ann.active = False
    record_admin_action(db, staff, "announcement.deactivate", target_type="announcement",
                        target_id=str(ann.id), ip=client_ip(request))
    db.commit()
    return {"status": "deactivated", "id": str(ann.id)}
