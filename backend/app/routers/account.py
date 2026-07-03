"""Customer-facing account reads: billing history + usage rollups (Phase 4 #2).

The customer dashboard's window onto the same `invoices` / `usage_daily` tables
the admin site reads cross-org. Auth is resolve_org (dashboard session cookie OR
SDK Bearer key) — both paths set the RLS GUC, and every query still carries an
explicit WHERE org_id because the app-level filter is the load-bearing tenant
isolation (the docker superuser role bypasses RLS).
"""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import resolve_org
from ..db import get_db
from ..models import Invoice, Organization, UsageDaily

router = APIRouter()


class InvoiceItem(BaseModel):
    id: str
    stripe_invoice_id: str
    amount_cents: int
    currency: str
    status: str
    period_start: str | None = None
    period_end: str | None = None
    created_at: str | None = None


class UsageDay(BaseModel):
    day: str
    logs_count: int
    tokens_sum: int
    breach_count: int
    graded_count: int
    failed_count: int
    pending_count: int


class UsageQuota(BaseModel):
    monthly_log_quota: int | None = None   # NULL = unlimited
    used_this_month: int = 0
    remaining: int | None = None           # NULL when unlimited


class UsageResponse(BaseModel):
    quota: UsageQuota
    days: list[UsageDay]


@router.get("/v1/invoices", response_model=list[InvoiceItem])
def list_invoices(
    org: Organization = Depends(resolve_org),
    db: Session = Depends(get_db),
    limit: int = Query(default=24, ge=1, le=100),
):
    """This org's billing history (populated by the Stripe webhook), newest first."""
    rows = db.execute(
        select(Invoice)
        .where(Invoice.org_id == org.id)
        .order_by(Invoice.created_at.desc())
        .limit(limit)
    ).scalars().all()
    return [
        InvoiceItem(
            id=str(i.id), stripe_invoice_id=i.stripe_invoice_id,
            amount_cents=i.amount_cents, currency=i.currency, status=i.status,
            period_start=i.period_start.isoformat() if i.period_start else None,
            period_end=i.period_end.isoformat() if i.period_end else None,
            created_at=i.created_at.isoformat() if i.created_at else None,
        )
        for i in rows
    ]


@router.get("/v1/usage", response_model=UsageResponse)
def get_usage(
    org: Organization = Depends(resolve_org),
    db: Session = Depends(get_db),
    days: int = Query(default=30, ge=1, le=90),
):
    """Per-day usage from the worker-maintained usage_daily rollup (never scans
    the raw ledger), plus quota headroom against organizations.monthly_log_quota."""
    today = date.today()
    since = today - timedelta(days=days - 1)

    rows = db.execute(
        select(UsageDaily)
        .where(UsageDaily.org_id == org.id, UsageDaily.day >= since)
        .order_by(UsageDaily.day.asc())
    ).scalars().all()

    month_start = today.replace(day=1)
    used_this_month = db.execute(
        select(func.coalesce(func.sum(UsageDaily.logs_count), 0))
        .where(UsageDaily.org_id == org.id, UsageDaily.day >= month_start)
    ).scalar_one()

    quota = org.monthly_log_quota
    return UsageResponse(
        quota=UsageQuota(
            monthly_log_quota=quota,
            used_this_month=int(used_this_month),
            remaining=None if quota is None else max(0, quota - int(used_this_month)),
        ),
        days=[
            UsageDay(
                day=r.day.isoformat(), logs_count=r.logs_count, tokens_sum=r.tokens_sum,
                breach_count=r.breach_count, graded_count=r.graded_count,
                failed_count=r.failed_count, pending_count=r.pending_count,
            )
            for r in rows
        ],
    )
