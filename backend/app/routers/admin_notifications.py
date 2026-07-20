"""Staff notifications center (Phase D). Read-only + mark-read; rows are generated elsewhere
from real events (admin_config broadcast, admin_staff targeted actions, admin_orgs system events).
No step-up (benign self-service); CSRF-protected like every admin write."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..auth import require_staff
from ..db import get_db
from ..models import StaffNotification, StaffUser

router = APIRouter()


def _mine(staff: StaffUser):
    # my rows + any broadcast-to-all rows (staff_user_id NULL)
    return or_(StaffNotification.staff_user_id == staff.id,
               StaffNotification.staff_user_id.is_(None))


def _unread_count(db: Session, staff: StaffUser) -> int:
    return int(db.execute(
        select(func.count()).select_from(StaffNotification)
        .where(_mine(staff), StaffNotification.read_at.is_(None))).scalar_one())


@router.get("/v1/notifications")
def list_notifications(staff: StaffUser = Depends(require_staff), db: Session = Depends(get_db),
                       limit: int = Query(default=30, ge=1, le=100), unread_only: bool = False):
    q = select(StaffNotification).where(_mine(staff))
    if unread_only:
        q = q.where(StaffNotification.read_at.is_(None))
    rows = db.execute(
        q.order_by(StaffNotification.created_at.desc()).limit(limit)).scalars().all()
    return {
        "unread": _unread_count(db, staff),
        "items": [{
            "id": str(n.id), "kind": n.kind, "title": n.title, "body": n.body, "level": n.level,
            "target_type": n.target_type, "target_id": n.target_id,
            "read": n.read_at is not None,
            "created_at": n.created_at.isoformat() if n.created_at else None,
        } for n in rows],
    }


@router.get("/v1/notifications/unread-count")
def unread_count(staff: StaffUser = Depends(require_staff), db: Session = Depends(get_db)):
    return {"unread": _unread_count(db, staff)}


@router.post("/v1/notifications/{note_id}/read")
def mark_read(note_id: str, staff: StaffUser = Depends(require_staff), db: Session = Depends(get_db)):
    try:
        nid = uuid.UUID(note_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="not found")
    n = db.get(StaffNotification, nid)
    if n is None or (n.staff_user_id is not None and n.staff_user_id != staff.id):
        raise HTTPException(status_code=404, detail="not found")
    if n.read_at is None:
        n.read_at = func.now()
        db.commit()
    return {"status": "ok"}


@router.post("/v1/notifications/read-all")
def mark_all_read(staff: StaffUser = Depends(require_staff), db: Session = Depends(get_db)):
    db.query(StaffNotification).filter(
        StaffNotification.staff_user_id == staff.id,
        StaffNotification.read_at.is_(None),
    ).update({StaffNotification.read_at: func.now()}, synchronize_session=False)
    db.commit()
    return {"status": "ok"}
