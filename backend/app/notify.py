"""Generate staff notifications from REAL events (Phase D).

Rows are STAGED in the same transaction as the source mutation — the caller commits them
together, mirroring admin_audit.record_admin_action. Recipient preferences (notify_broadcasts /
notify_targeted / notify_system, default on) are honoured, so a staff member never gets a kind of
notification they turned off. Never fabricate: only call these from a real event handler.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import StaffNotification, StaffUser


def _wants(staff: StaffUser, pref_key: str) -> bool:
    prefs = staff.preferences or {}
    return prefs.get(pref_key, True) is not False        # default on


def notify(db: Session, staff_user_id, kind: str, title: str, *, body: str | None = None,
           level: str = "info", target_type: str | None = None, target_id: str | None = None) -> None:
    """Stage a single notification row (no commit)."""
    db.add(StaffNotification(
        staff_user_id=staff_user_id, kind=kind, title=title, body=body, level=level,
        target_type=target_type, target_id=target_id))


def notify_staff(db: Session, staff: StaffUser, kind: str, title: str, *,
                 pref: str = "notify_targeted", **kw) -> None:
    """Notify one staff member (a StaffUser) if their preference allows the kind."""
    if _wants(staff, pref):
        notify(db, staff.id, kind, title, **kw)


def broadcast(db: Session, kind: str, title: str, *, pref: str = "notify_broadcasts",
              exclude_id=None, **kw) -> None:
    """Fan out to every ACTIVE staff member who wants this kind (one row each → per-staff read)."""
    staff = db.execute(
        select(StaffUser).where(StaffUser.disabled.is_(False))).scalars().all()
    for s in staff:
        if exclude_id is not None and s.id == exclude_id:
            continue
        if _wants(s, pref):
            notify(db, s.id, kind, title, **kw)
