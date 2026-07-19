"""Security / login monitor for the ops console (P4 · §K).

Read-only over login_events (successes AND failures). Surfaces a failed-login trend,
top offenders by IP, a lockout watchlist (emails failing repeatedly in the last day),
and a recent-attempts feed. Viewer-gated; cross-org.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Date, case, cast, func, select
from sqlalchemy.orm import Session

from ..auth import require_platform_role
from ..db import get_db
from ..models import LoginEvent, StaffUser

router = APIRouter()


@router.get("/v1/security/logins")
def security_logins(
    staff: StaffUser = Depends(require_platform_role("viewer")),
    db: Session = Depends(get_db),
    days: int = Query(default=7, ge=1, le=90),
):
    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=days - 1)).date()

    dexpr = cast(func.timezone("UTC", LoginEvent.created_at), Date)
    rows = db.execute(
        select(dexpr, func.count(),
               func.coalesce(func.sum(case((LoginEvent.success.is_(True), 1), else_=0)), 0))
        .where(dexpr >= start).group_by(dexpr)
    ).all()
    smap = {r[0]: (int(r[1]), int(r[2])) for r in rows}
    series, d = [], start
    while d <= now.date():
        total, ok = smap.get(d, (0, 0))
        series.append({"day": d.isoformat(), "failed": total - ok, "success": ok})
        d += timedelta(days=1)

    top_offenders = [
        {"ip": ip, "failed": int(c), "last_at": m.isoformat() if m else None}
        for ip, c, m in db.execute(
            select(LoginEvent.ip, func.count(), func.max(LoginEvent.created_at))
            .where(LoginEvent.success.is_(False), LoginEvent.ip.isnot(None))
            .group_by(LoginEvent.ip).order_by(func.count().desc()).limit(10)
        ).all()
    ]

    since24 = now - timedelta(hours=24)
    watchlist = [
        {"email": e, "failed_24h": int(c), "last_at": m.isoformat() if m else None}
        for e, c, m in db.execute(
            select(LoginEvent.email, func.count(), func.max(LoginEvent.created_at))
            .where(LoginEvent.success.is_(False), LoginEvent.created_at >= since24)
            .group_by(LoginEvent.email).having(func.count() >= 5)
            .order_by(func.count().desc()).limit(20)
        ).all()
    ]

    recent = [
        {"email": x.email, "ip": x.ip, "success": x.success,
         "org_id": str(x.org_id) if x.org_id else None,
         "user_agent": (x.user_agent or "")[:60],
         "at": x.created_at.isoformat() if x.created_at else None}
        for x in db.execute(
            select(LoginEvent).order_by(LoginEvent.created_at.desc()).limit(50)
        ).scalars().all()
    ]

    return {"days": days, "series": series, "top_offenders": top_offenders,
            "watchlist": watchlist, "recent": recent}
