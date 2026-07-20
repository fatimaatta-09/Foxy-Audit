"""Revenue / billing analytics + Stripe-event replay for the ops console (P4 · §J).

Reads are cross-org (staff bypass RLS). Revenue is derived from PAID invoices; the
Stripe-event list exposes only metadata (never the raw payload). Replay re-dispatches a
stored event through the SAME billing handlers the live webhook uses, so it genuinely
re-processes — and is audited.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..admin_audit import client_ip, record_admin_action
from ..auth import require_platform_role, require_step_up_dep
from ..db import get_db
from ..models import Invoice, Organization, StaffUser, StripeEvent
from .billing import (
    _INVOICE_EVENTS, _SUBSCRIPTION_EVENTS, _deliver_credentials, _handle_checkout,
    _handle_invoice, _handle_subscription_change,
)

router = APIRouter()


def _month(col):
    return func.to_char(func.timezone("UTC", col), "YYYY-MM")


@router.get("/v1/revenue")
def revenue(
    staff: StaffUser = Depends(require_platform_role("viewer")),
    db: Session = Depends(get_db),
    months: int = Query(default=12, ge=1, le=36),
):
    """Monthly revenue (paid invoices), revenue-by-plan, 30-day total, and the
    subscription-status breakdown."""
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=31 * months)
    since6 = now - timedelta(days=31 * 6)
    paid = Invoice.status == "paid"

    m = _month(Invoice.created_at)
    monthly = [
        {"month": mm, "cents": int(c)}
        for mm, c in db.execute(
            select(m, func.coalesce(func.sum(Invoice.amount_cents), 0))
            .where(paid, Invoice.created_at >= since).group_by(m).order_by(m)
        ).all()
    ]

    bp: dict[str, dict] = {}
    for mm, plan, cents in db.execute(
        select(m, Organization.plan_tier, func.coalesce(func.sum(Invoice.amount_cents), 0))
        .join(Organization, Organization.id == Invoice.org_id)
        .where(paid, Invoice.created_at >= since6)
        .group_by(m, Organization.plan_tier).order_by(m)
    ).all():
        bp.setdefault(mm, {})[plan or "none"] = int(cents)
    by_plan_monthly = [{"month": mm, "plans": bp[mm]} for mm in sorted(bp)]

    rev30 = db.execute(
        select(func.coalesce(func.sum(Invoice.amount_cents), 0))
        .where(paid, Invoice.created_at >= now - timedelta(days=30))
    ).scalar_one()

    subscriptions: dict[str, int] = {}
    for st, cnt in db.execute(
        select(Organization.subscription_status, func.count())
        .where(Organization.deleted_at.is_(None))
        .group_by(Organization.subscription_status)
    ).all():
        subscriptions[st or "none"] = int(cnt)
    trialing = db.execute(
        select(func.count()).select_from(Organization)
        .where(Organization.deleted_at.is_(None),
               Organization.trial_ends_at.isnot(None), Organization.trial_ends_at > now)
    ).scalar_one()

    return {"revenue_30d_cents": int(rev30), "monthly": monthly,
            "by_plan_monthly": by_plan_monthly, "subscriptions": subscriptions,
            "trialing": int(trialing)}


@router.get("/v1/billing/stripe-events")
def stripe_events(
    staff: StaffUser = Depends(require_platform_role("viewer")),
    db: Session = Depends(get_db),
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """Durable Stripe webhook log (metadata only — the raw payload is never exposed)."""
    where = [StripeEvent.status == status] if status else []
    total = db.execute(select(func.count()).select_from(StripeEvent).where(*where)).scalar_one()
    rows = db.execute(
        select(StripeEvent).where(*where)
        .order_by(StripeEvent.received_at.desc()).limit(limit).offset(offset)
    ).scalars().all()
    return {"total": total, "limit": limit, "offset": offset, "items": [
        {"id": str(e.id), "stripe_event_id": e.stripe_event_id, "type": e.type,
         "status": e.status, "error": e.error,
         "org_id": str(e.org_id) if e.org_id else None,
         "received_at": e.received_at.isoformat() if e.received_at else None,
         "processed_at": e.processed_at.isoformat() if e.processed_at else None}
        for e in rows
    ]}


@router.post("/v1/billing/stripe-events/{event_id}/replay", dependencies=[Depends(require_step_up_dep)])
def replay_stripe_event(
    event_id: str,
    request: Request,
    staff: StaffUser = Depends(require_platform_role("operator")),
    db: Session = Depends(get_db),
):
    """Re-dispatch a stored Stripe event through the live billing handlers + audit it."""
    try:
        eid = uuid.UUID(event_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="invalid event id")
    ev = db.get(StripeEvent, eid)
    if ev is None:
        raise HTTPException(status_code=404, detail="stripe event not found")
    payload = ev.payload or {}
    etype = ev.type or payload.get("type", "")
    data_obj = (payload.get("data") or {}).get("object") or {}
    try:
        if etype == "checkout.session.completed":
            result, org_id = _handle_checkout(db, data_obj)
        elif etype in _SUBSCRIPTION_EVENTS:
            result, org_id = _handle_subscription_change(db, data_obj)
        elif etype in _INVOICE_EVENTS:
            result, org_id = _handle_invoice(db, data_obj)
        else:
            result, org_id = {"status": "ignored", "type": etype}, None
        final = "ignored" if result.get("status") == "ignored" else "processed"
        ev.status = final
        ev.error = None
        ev.processed_at = datetime.now(timezone.utc)
        if org_id:
            try:
                ev.org_id = uuid.UUID(org_id)
            except (ValueError, TypeError):
                pass
        record_admin_action(db, staff, "stripe.replay", target_org_id=ev.org_id,
                            target_type="stripe_event", target_id=str(ev.id),
                            detail={"type": etype, "result": result.get("status")},
                            ip=client_ip(request))
        db.commit()
        if etype == "checkout.session.completed" and result.get("status") == "provisioned":
            _deliver_credentials(db, org_id)
        return {"status": "replayed", "event_status": final, "result": result.get("status")}
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — persist the failure like the live webhook
        db.rollback()
        ev = db.get(StripeEvent, eid)
        if ev is not None:
            ev.status = "failed"
            ev.error = str(exc)[:500]
            record_admin_action(db, staff, "stripe.replay", target_type="stripe_event",
                                target_id=str(ev.id),
                                detail={"type": etype, "error": str(exc)[:200]},
                                ip=client_ip(request))
            db.commit()
        raise HTTPException(status_code=500, detail=f"replay failed: {exc}")
