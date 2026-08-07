"""Cross-org operational health for the internal admin site."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Date, case, cast, func, select, text
from sqlalchemy.orm import Session

from ..auth import require_platform_role
from ..config import Settings, get_settings
from ..db import get_db
from ..platform_config import get_int
from ..models import (
    AdminAction, AuditLog, ChainAnchor, Organization, StaffUser, UsageDaily,
    WorkerHeartbeat,
)

router = APIRouter()


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _iso(value: datetime | None) -> str | None:
    value = _utc(value)
    return value.isoformat() if value else None


def _age_seconds(value: datetime | None, now: datetime) -> float | None:
    value = _utc(value)
    return round(max(0.0, (now - value).total_seconds()), 3) if value else None


def _empty_health(now: datetime, db_status: str = "unavailable") -> dict:
    return {
        "status": "unavailable" if db_status != "ok" else "degraded",
        "generated_at": now.isoformat(),
        "database": {"status": db_status},
        "worker": {"status": "unavailable", "beat_at": None,
                    "age_seconds": None, "stale_after_seconds": None},
        "grading": {"status": "unavailable", "counts": {}},
        "anchors": {"status": "unavailable", "latest_confirmed_at": None,
                     "latest_confirmed_age_seconds": None, "failed_latest": 0,
                     "stale_latest": 0, "stale_after_seconds": 0},
        "circuit_breaker": {
            "state": "unavailable",
            "detail": "worker circuit-breaker state is process-local and not persisted",
        },
    }


def build_health(db: Session, settings: Settings | None = None) -> dict:
    """Build the admin health payload from durable state only."""
    settings = settings or get_settings()
    now = datetime.now(timezone.utc)
    result = _empty_health(now)

    try:
        db.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 - health must report the failed dependency
        db.rollback()
        return result

    result["database"] = {"status": "ok"}

    stale_after = settings.grading_poll_interval * 5 + settings.gemini_timeout + 10
    heartbeat = db.execute(
        select(WorkerHeartbeat.beat_at).where(WorkerHeartbeat.id == 1)
    ).scalar_one_or_none()
    worker_age = _age_seconds(heartbeat, now)
    worker_status = (
        "missing" if heartbeat is None else
        "stale" if worker_age is not None and worker_age > stale_after else "ok"
    )
    result["worker"] = {
        "status": worker_status,
        "beat_at": _iso(heartbeat),
        "age_seconds": worker_age,
        "stale_after_seconds": round(stale_after, 3),
    }

    grading_counts = {
        str(status): int(count)
        for status, count in db.execute(
            select(AuditLog.grading_status, func.count())
            .group_by(AuditLog.grading_status)
        ).all()
    }
    result["grading"] = {
        "status": "ok",
        "counts": grading_counts,
        "failed": grading_counts.get("failed", 0),
        "pending": grading_counts.get("pending", 0),
        "in_progress": grading_counts.get("in_progress", 0),
    }

    # One newest receipt per organization is enough to detect operator conditions.
    latest_by_org = {}
    for row in db.execute(
        select(ChainAnchor.org_id, ChainAnchor.status,
               ChainAnchor.confirmed_at, ChainAnchor.anchored_at)
        .order_by(ChainAnchor.org_id, ChainAnchor.anchored_at.desc(),
                  ChainAnchor.id.desc())
    ).all():
        latest_by_org.setdefault(row.org_id, row)

    confirmed = [
        _utc(row.confirmed_at) for row in latest_by_org.values()
        if row.status == "confirmed" and row.confirmed_at is not None
    ]
    newest_confirmed = max(confirmed) if confirmed else None
    stale_after_anchor = get_int(db, "anchor_stale_alert_seconds", settings.anchor_stale_alert_seconds)
    stale_latest = sum(
        1 for value in confirmed
        if stale_after_anchor > 0
        and (now - value).total_seconds() > stale_after_anchor
    )
    failed_latest = sum(1 for row in latest_by_org.values() if row.status == "failed")

    # C5 · THE DENOMINATOR. Everything above counts organisations that have
    # anchored at least once, so an org that has NEVER anchored is invisible on
    # this page — which is the one an operator most needs to see. Anchoring is
    # not opt-in per org: anchor_all_due sweeps `select(Organization.id)` and
    # there is no per-org flag anywhere (OrgPolicy has none); the per-plan
    # `anchor_cadence_*` settings change the INTERVAL, never whether. So with
    # anchoring enabled, every org whose chain has advanced should have one.
    #
    # "Orgs whose chain has advanced" is the honest denominator, not "all orgs":
    # a workspace that has never recorded an event has nothing to anchor, and
    # counting it as a gap would make the number permanently wrong on any
    # deployment with signups.
    #
    # ⚠ SHAPE MEASURED, NOT ASSUMED. `SELECT DISTINCT org_id FROM audit_logs`
    # scans the whole index — 112ms over 1.5M events. Driving from
    # `organizations` and probing per org stops at the first matching row:
    # 1.46ms for the same answer, and it scales with the ORG count rather than
    # the event count, which is the right shape for a metric on a polled page.
    live_event_orgs = set(db.execute(
        select(Organization.id).where(
            Organization.deleted_at.is_(None),
            select(AuditLog.org_id).where(AuditLog.org_id == Organization.id)
            .exists(),
        )
    ).scalars().all())
    anchored_orgs = len(live_event_orgs & set(latest_by_org))

    result["anchors"] = {
        "status": "ok" if failed_latest == 0 and stale_latest == 0 else "degraded",
        "provider": settings.anchor_provider,
        "enabled": bool(settings.anchor_enabled),
        "latest_confirmed_at": _iso(newest_confirmed),
        "latest_confirmed_age_seconds": _age_seconds(newest_confirmed, now),
        "failed_latest": failed_latest,
        "stale_latest": stale_latest,
        "stale_after_seconds": stale_after_anchor,
        # C5 · coverage. `expected` is 0 on a deployment where nothing has been
        # captured yet — that is an absence, not 0%, and the console renders the
        # two differently.
        "expected_orgs": len(live_event_orgs),
        "anchored_orgs": anchored_orgs,
    }

    degraded = (
        worker_status != "ok"
        or grading_counts.get("failed", 0) > 0
        or failed_latest > 0
        or stale_latest > 0
    )
    result["status"] = "degraded" if degraded else "ok"
    return result


@router.get("/v1/health")
def system_health(
    staff: StaffUser = Depends(require_platform_role("viewer")),
    db: Session = Depends(get_db),
):
    return build_health(db)


# ============================ Ops trends (Phase 1) ============================
# HONEST subset only: worker_heartbeat is a single current-value row and wallet
# balance is a live RPC (neither has stored history), so those are NOT charted.
# Real, durable series: grading throughput (usage_daily) + anchor activity
# (chain_anchors timestamps) + persisted alert-ack history (admin_actions).


@router.get("/v1/health/trends")
def health_trends(
    days: int = Query(default=14, ge=1, le=90),
    staff: StaffUser = Depends(require_platform_role("viewer")),
    db: Session = Depends(get_db),
):
    """Grading throughput + anchor activity per day, cross-org, zero-filled."""
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=days - 1)

    gmap = {
        r[0]: (int(r[1]), int(r[2]), int(r[3]))
        for r in db.execute(
            select(UsageDaily.day,
                   func.coalesce(func.sum(UsageDaily.graded_count), 0),
                   func.coalesce(func.sum(UsageDaily.failed_count), 0),
                   func.coalesce(func.sum(UsageDaily.pending_count), 0))
            .where(UsageDaily.day >= start)
            .group_by(UsageDaily.day)
        ).all()
    }
    dexpr = cast(func.timezone("UTC", ChainAnchor.anchored_at), Date)
    amap = {
        r[0]: (int(r[1]), int(r[2]))
        for r in db.execute(
            select(dexpr, func.count(),
                   func.coalesce(func.sum(case((ChainAnchor.status == "confirmed", 1),
                                               else_=0)), 0))
            .where(dexpr >= start)
            .group_by(dexpr)
        ).all()
    }
    grading, anchors, d = [], [], start
    while d <= today:
        g = gmap.get(d, (0, 0, 0))
        a = amap.get(d, (0, 0))
        grading.append({"day": d.isoformat(), "graded": g[0], "failed": g[1], "pending": g[2]})
        anchors.append({"day": d.isoformat(), "anchored": a[0], "confirmed": a[1]})
        d += timedelta(days=1)
    return {"days": days, "grading": grading, "anchors": anchors}


@router.get("/v1/health/alert-history")
def alert_history(
    limit: int = Query(default=50, ge=1, le=200),
    staff: StaffUser = Depends(require_platform_role("viewer")),
    db: Session = Depends(get_db),
):
    """Persisted alert acknowledgements (admin_actions where action='alert.ack')."""
    from ..models import StaffUser as _Staff
    rows = db.execute(
        select(AdminAction, _Staff.email)
        .join(_Staff, _Staff.id == AdminAction.staff_user_id, isouter=True)
        .where(AdminAction.action == "alert.ack")
        .order_by(AdminAction.created_at.desc())
        .limit(limit)
    ).all()
    return {"items": [
        {"id": str(a.id), "actor": email, "target_id": a.target_id,
         "target_type": a.target_type, "detail": a.detail,
         "at": a.created_at.isoformat() if a.created_at else None}
        for a, email in rows
    ]}
