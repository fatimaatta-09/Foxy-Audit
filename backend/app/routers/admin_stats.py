"""Platform-wide KPIs + the in/out traffic feed for the admin site ("site 3").

Mounted under /admin → /admin/v1/stats and /admin/v1/traffic. Viewer role. Reads
are cross-org (no RLS GUC set; staff see the whole platform). traffic_events and
usage_daily aggregates keep these dashboards off the raw ledger.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ..auth import require_platform_role
from ..db import get_db
from ..models import (
    MarketingLead, Organization, StaffUser, TrafficEvent, UsageDaily, User,
)

router = APIRouter()


class PlatformStats(BaseModel):
    total_orgs: int
    active_orgs: int
    suspended_orgs: int
    total_users: int
    total_staff: int
    total_logs: int
    total_breaches: int
    leads_by_status: dict[str, int]


class TrafficRow(BaseModel):
    site: str
    path: str
    method: str
    status_code: int
    latency_ms: int | None = None
    org_id: str | None = None
    created_at: str


class TrafficFeed(BaseModel):
    by_site: dict[str, int]
    errors: int
    recent: list[TrafficRow]


@router.get("/v1/stats", response_model=PlatformStats)
def platform_stats(
    staff: StaffUser = Depends(require_platform_role("viewer")),
    db: Session = Depends(get_db),
):
    total_orgs = db.execute(
        select(func.count()).select_from(Organization)
        .where(Organization.deleted_at.is_(None))
    ).scalar_one()
    active_orgs = db.execute(
        select(func.count()).select_from(Organization)
        .where(Organization.subscription_status == "active",
               Organization.deleted_at.is_(None))
    ).scalar_one()
    suspended_orgs = db.execute(
        select(func.count()).select_from(Organization)
        .where(Organization.suspended.is_(True))
    ).scalar_one()
    total_users = db.execute(select(func.count()).select_from(User)).scalar_one()
    total_staff = db.execute(select(func.count()).select_from(StaffUser)).scalar_one()

    # Platform-wide log + breach totals from the rollup (cheap; no ledger scan).
    total_logs = db.execute(
        select(func.coalesce(func.sum(UsageDaily.logs_count), 0))
    ).scalar_one()
    total_breaches = db.execute(
        select(func.coalesce(func.sum(UsageDaily.breach_count), 0))
    ).scalar_one()

    leads_by_status = {s: 0 for s in ("new", "trial", "converted", "churned")}
    for status, cnt in db.execute(
        select(MarketingLead.status, func.count()).group_by(MarketingLead.status)
    ).all():
        leads_by_status[status] = cnt

    return PlatformStats(
        total_orgs=total_orgs, active_orgs=active_orgs, suspended_orgs=suspended_orgs,
        total_users=total_users, total_staff=total_staff, total_logs=int(total_logs),
        total_breaches=int(total_breaches), leads_by_status=leads_by_status,
    )


@router.get("/v1/traffic", response_model=TrafficFeed)
def traffic_feed(
    staff: StaffUser = Depends(require_platform_role("viewer")),
    db: Session = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=500),
):
    """Recent in/out traffic across all 3 sites + light aggregates. IP/UA are
    already HMAC-hashed at capture, so nothing here re-exposes raw client data."""
    by_site = {"marketing": 0, "app": 0, "admin": 0}
    for site, cnt in db.execute(
        select(TrafficEvent.site, func.count())
        .where(TrafficEvent.created_at >= text("now() - interval '7 days'"))
        .group_by(TrafficEvent.site)
    ).all():
        if site in by_site:
            by_site[site] = cnt

    errors = db.execute(
        select(func.count()).select_from(TrafficEvent)
        .where(TrafficEvent.status_code >= 400,
               TrafficEvent.created_at >= text("now() - interval '7 days'"))
    ).scalar_one()

    rows = db.execute(
        select(TrafficEvent).order_by(TrafficEvent.created_at.desc()).limit(limit)
    ).scalars().all()
    recent = [
        TrafficRow(
            site=r.site, path=r.path, method=r.method, status_code=r.status_code,
            latency_ms=r.latency_ms, org_id=str(r.org_id) if r.org_id else None,
            created_at=r.created_at.isoformat() if r.created_at else "",
        )
        for r in rows
    ]
    return TrafficFeed(by_site=by_site, errors=errors, recent=recent)
