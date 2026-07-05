"""admin_actions retention — the staff-action audit trail is purged past a
retention window so it doesn't grow unbounded (Phase 5 · 5D.5). It's not
partitioned like traffic_events, so this is a plain age-based DELETE run by the
worker's maintenance pass (system/superuser — staff can never DELETE from it).
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import select

from app.db import SessionLocal
from app.models import AdminAction


def _add(db, sid, action, age_days):
    db.add(AdminAction(staff_user_id=sid, action=action,
                       created_at=dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=age_days)))


def test_purge_admin_actions_removes_old_keeps_recent(make_staff):
    from app import usage
    sid = uuid.UUID(make_staff()["id"])
    db = SessionLocal()
    try:
        _add(db, sid, "act.old", 400)
        _add(db, sid, "act.recent", 10)
        db.commit()

        removed = usage.purge_admin_actions(db, 365)
        assert removed == 1
        left = db.execute(select(AdminAction.action)).scalars().all()
        assert "act.recent" in left and "act.old" not in left
    finally:
        db.close()


def test_run_once_wires_in_the_purge(make_staff):
    from app import usage
    sid = uuid.UUID(make_staff()["id"])
    db = SessionLocal()
    try:
        _add(db, sid, "act.ancient", 500)
        db.commit()
        usage.run_once(db)                      # the worker's maintenance pass
        left = db.execute(select(AdminAction.action)).scalars().all()
        assert "act.ancient" not in left
    finally:
        db.close()
