"""Revenue / billing analytics + Stripe-event replay for the ops console (P4 · §J).

Reads are cross-org (staff bypass RLS). Revenue is derived from PAID invoices; the
Stripe-event list exposes only metadata (never the raw payload). Replay re-dispatches a
stored event through the SAME billing handlers the live webhook uses, so it genuinely
re-processes — and is audited.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..admin_audit import client_ip, record_admin_action
from ..auth import require_platform_role, require_step_up_dep
from ..db import get_db
from ..models import Invoice, Organization, PaymentEvent, StaffUser, StripeEvent
from .billing import (
    _INVOICE_EVENTS, _PADDLE_PURCHASE_EVENTS, _PADDLE_STATE_EVENTS,
    _SUBSCRIPTION_EVENTS, _deliver_credentials, _handle_checkout,
    _handle_invoice, _handle_subscription_change, _paddle_apply_purchase,
    _paddle_apply_subscription,
)

log = logging.getLogger("foxy.admin_billing")
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


# ────────────────────── Paddle events + replay (M3d · #98 · #103) ────────────
# Paddle is the processor that actually takes money here. Stripe has never taken
# a payment and has had a full events page with replay since P4; Paddle had
# neither, so a `failed` row was unrecoverable once Paddle stopped retrying — a
# customer who has paid and was never upgraded, with no button anywhere.

#: The statuses a replay is allowed to act on, and this is a DELIBERATE
#: divergence from the Stripe route above, which accepts any row.
#:
#: A replay re-runs the handler for a row that already exists, which is exactly
#: the thing `ON CONFLICT DO NOTHING` on `provider_event_id` normally prevents.
#: For a purchase that is harmless — the handler writes the same tier and quota
#: it wrote before and never calls Paddle, so nothing can be charged or refunded.
#: For a SUBSCRIPTION event it is not: those carry a state, and re-running an old
#: one applies that old state over anything newer. Replaying last month's
#: `subscription.past_due` on an org that has since paid marks it past_due again
#: and restarts the dunning clock.
#:
#: `failed` and `received` are the rows where the handler demonstrably did not
#: finish, which is the entire use case. `processed` and `ignored` already did
#: their job, so re-running them can only apply stale state.
_REPLAYABLE = ("failed", "received")


@router.get("/v1/billing/payment-events")
def payment_events(
    staff: StaffUser = Depends(require_platform_role("viewer")),
    db: Session = Depends(get_db),
    provider: str | None = None,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """Durable non-Stripe webhook log — metadata only.

    THE RAW PAYLOAD NEVER LEAVES THE SERVER, and that is why this route exists
    rather than a row in `admin_data.TABLE_REGISTRY`. That browser returns every
    column minus a denylist, and a Paddle payload carries the customer's name,
    email and billing address. Every field below is named explicitly, so a
    column added to the model later is invisible here until somebody decides it
    should not be.
    """
    where = []
    if provider:
        where.append(PaymentEvent.provider == provider)
    if status:
        where.append(PaymentEvent.status == status)
    total = db.execute(
        select(func.count()).select_from(PaymentEvent).where(*where)).scalar_one()
    rows = db.execute(
        select(PaymentEvent).where(*where)
        .order_by(PaymentEvent.received_at.desc()).limit(limit).offset(offset)
    ).scalars().all()
    return {"total": total, "limit": limit, "offset": offset, "items": [
        {"id": str(e.id), "provider": e.provider,
         "provider_event_id": e.provider_event_id, "type": e.type,
         "status": e.status, "error": e.error,
         "org_id": str(e.org_id) if e.org_id else None,
         "received_at": e.received_at.isoformat() if e.received_at else None,
         "processed_at": e.processed_at.isoformat() if e.processed_at else None}
        for e in rows
    ]}


@router.post("/v1/billing/payment-events/{event_id}/replay",
             dependencies=[Depends(require_step_up_dep)])
def replay_payment_event(
    event_id: str,
    request: Request,
    staff: StaffUser = Depends(require_platform_role("operator")),
    db: Session = Depends(get_db),
):
    """Re-dispatch a stored Paddle event through the live handlers + audit it.

    Mirrors `replay_stripe_event`'s safeguards — operator role, step-up, one
    admin_actions row whether it succeeds or fails, the row stamped afterwards —
    with two differences, both deliberate:

    * **Only `failed` and `received` rows.** See `_REPLAYABLE`.
    * **The exception TYPE is recorded, never `str(exc)`.** The Stripe route
      stores `str(exc)` and echoes it in its 500 body; a Paddle handler's
      exception can quote the payload it choked on, and that payload holds
      customer name, email and address.
    """
    try:
        eid = uuid.UUID(event_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="invalid event id")
    ev = db.get(PaymentEvent, eid)
    if ev is None:
        raise HTTPException(status_code=404, detail="payment event not found")
    if (ev.status or "") not in _REPLAYABLE:
        raise HTTPException(
            status_code=409,
            detail=f"only {' or '.join(_REPLAYABLE)} events can be replayed; "
                   f"this one is {ev.status}")

    payload = ev.payload or {}
    etype = ev.type or payload.get("event_type", "")
    data_obj = payload.get("data") or {}
    try:
        if etype in _PADDLE_PURCHASE_EVENTS:
            result, org_id = _paddle_apply_purchase(db, data_obj)
        elif etype in _PADDLE_STATE_EVENTS:
            result, org_id = _paddle_apply_subscription(db, data_obj)
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
        record_admin_action(db, staff, "payment.replay", target_org_id=ev.org_id,
                            target_type="payment_event", target_id=str(ev.id),
                            detail={"provider": ev.provider, "type": etype,
                                    "result": result.get("status")},
                            ip=client_ip(request))
        db.commit()
        # Post-commit, exactly as the webhook does it: a mail failure must not be
        # able to roll back the provisioning and let another attempt fork a
        # second workspace. Only a genuinely NEW workspace gets an invite — a
        # replay over an org that already exists returns `already_provisioned`,
        # so a customer is not emailed a second set-password link.
        if result.get("status") == "provisioned":
            _deliver_credentials(db, org_id)
        return {"status": "replayed", "event_status": final,
                "result": result.get("status")}
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — persist the failure like the webhook
        db.rollback()
        ev = db.get(PaymentEvent, eid)
        kind = type(exc).__name__
        if ev is not None:
            ev.status = "failed"
            ev.error = kind[:500]
            record_admin_action(db, staff, "payment.replay",
                                target_type="payment_event", target_id=str(ev.id),
                                detail={"provider": ev.provider, "type": etype,
                                        "error": kind},
                                ip=client_ip(request))
            db.commit()
        log.warning("paddle replay of %s (%s) failed: %s", event_id, etype, kind)
        raise HTTPException(status_code=500, detail="replay failed")
