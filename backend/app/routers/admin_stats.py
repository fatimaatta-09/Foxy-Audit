"""Platform-wide KPIs + the in/out traffic feed for the admin site ("site 3").

Mounted under /admin → /admin/v1/stats and /admin/v1/traffic. Viewer role. Reads
are cross-org (no RLS GUC set; staff see the whole platform). traffic_events and
usage_daily aggregates keep these dashboards off the raw ledger.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import Date, cast, func, select, text
from sqlalchemy.orm import Session

from ..auth import require_platform_role
from ..db import get_db
from ..models import (
    Invoice, MarketingLead, Organization, StaffUser, TrafficEvent, UsageDaily, User,
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


# ============================ Trends (Phase 1) ============================
# Cross-org time-series + mixes for the Overview charts. Viewer-gated; staff read
# platform-wide (no org scope). Missing days are zero-filled so charts have a
# continuous axis; delta is vs the immediately-prior window of the same length.

_TS_METRICS = {"interactions", "breaches", "signups", "revenue"}


@router.get("/v1/stats/timeseries")
def stats_timeseries(
    metric: str = Query(...),
    days: int = Query(default=30, ge=1, le=365),
    staff: StaffUser = Depends(require_platform_role("viewer")),
    db: Session = Depends(get_db),
):
    """Per-day series for one metric + total and delta% vs the prior window.
    interactions/breaches ← usage_daily · signups ← organizations.created_at ·
    revenue (cents) ← paid invoices.amount_cents."""
    metric = (metric or "").lower()
    if metric not in _TS_METRICS:
        raise HTTPException(status_code=422,
                            detail="metric must be interactions, breaches, signups, or revenue")
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=days - 1)
    prev_start = start - timedelta(days=days)
    prev_end = start - timedelta(days=1)

    def agg(d0, d1) -> dict:
        if metric in ("interactions", "breaches"):
            col = UsageDaily.logs_count if metric == "interactions" else UsageDaily.breach_count
            rows = db.execute(
                select(UsageDaily.day, func.coalesce(func.sum(col), 0))
                .where(UsageDaily.day >= d0, UsageDaily.day <= d1)
                .group_by(UsageDaily.day)
            ).all()
            return {r[0]: int(r[1]) for r in rows}
        if metric == "signups":
            dexpr = cast(func.timezone("UTC", Organization.created_at), Date)
            rows = db.execute(
                select(dexpr, func.count())
                .where(dexpr >= d0, dexpr <= d1, Organization.deleted_at.is_(None))
                .group_by(dexpr)
            ).all()
            return {r[0]: int(r[1]) for r in rows}
        dexpr = cast(func.timezone("UTC", Invoice.created_at), Date)          # revenue
        rows = db.execute(
            select(dexpr, func.coalesce(func.sum(Invoice.amount_cents), 0))
            .where(dexpr >= d0, dexpr <= d1, Invoice.status == "paid")
            .group_by(dexpr)
        ).all()
        return {r[0]: int(r[1]) for r in rows}

    cur = agg(start, today)
    points, d = [], start
    while d <= today:
        points.append({"day": d.isoformat(), "value": cur.get(d, 0)})
        d += timedelta(days=1)
    total = sum(p["value"] for p in points)
    prev_total = sum(agg(prev_start, prev_end).values())
    delta_pct = round((total - prev_total) / prev_total * 100, 1) if prev_total > 0 else None
    return {"metric": metric, "days": days,
            "unit": "cents" if metric == "revenue" else "count",
            "points": points, "total": total, "prev_total": prev_total,
            "delta_pct": delta_pct}


@router.get("/v1/stats/plan-mix")
def stats_plan_mix(
    staff: StaffUser = Depends(require_platform_role("viewer")),
    db: Session = Depends(get_db),
):
    """Active-tenant count by plan tier (for the Overview plan-mix donut)."""
    mix = {}
    for tier, cnt in db.execute(
        select(Organization.plan_tier, func.count())
        .where(Organization.deleted_at.is_(None))
        .group_by(Organization.plan_tier)
    ).all():
        mix[tier or "none"] = int(cnt)
    order = ["free", "pro", "max", "premium", "none"]
    items = [{"plan": k, "count": mix[k]} for k in order if mix.get(k, 0) > 0]
    items += [{"plan": k, "count": v} for k, v in mix.items() if k not in order and v > 0]
    return {"total": sum(mix.values()), "items": items}


@router.get("/v1/stats/top-orgs")
def stats_top_orgs(
    days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=8, ge=1, le=50),
    staff: StaffUser = Depends(require_platform_role("viewer")),
    db: Session = Depends(get_db),
):
    """Top tenants by audited-interaction volume over the window (usage_daily)."""
    since = datetime.now(timezone.utc).date() - timedelta(days=days - 1)
    rows = db.execute(
        select(UsageDaily.org_id,
               func.coalesce(func.sum(UsageDaily.logs_count), 0).label("logs"),
               func.coalesce(func.sum(UsageDaily.breach_count), 0).label("breaches"))
        .where(UsageDaily.day >= since)
        .group_by(UsageDaily.org_id)
        .order_by(func.sum(UsageDaily.logs_count).desc())
        .limit(limit)
    ).all()
    ids = [r.org_id for r in rows]
    names = {}
    if ids:
        for oid, nm in db.execute(
            select(Organization.id, Organization.name).where(Organization.id.in_(ids))
        ).all():
            names[oid] = nm
    items = [{"org_id": str(r.org_id), "name": names.get(r.org_id, "—"),
              "logs": int(r.logs), "breaches": int(r.breaches)} for r in rows]
    return {"days": days, "items": items}
