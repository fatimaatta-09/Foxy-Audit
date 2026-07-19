"""POST /v1/webhooks/stripe — durable, idempotent Stripe webhook handling.

Every verified event is FIRST written to stripe_events (UNIQUE stripe_event_id →
ON CONFLICT DO NOTHING), so a Stripe retry/replay is a no-op and the billing feed
is fully auditable from the admin site. The event is then dispatched and its
stripe_events row is stamped processed|ignored|failed IN THE SAME transaction as
the org/invoice mutation — a change without a logged event (or vice-versa) can't
happen. Handled events:

  checkout.session.completed                  → provision org + tag the lead converted
  customer.subscription.updated / deleted     → patch subscription_status
  invoice.paid / payment_failed / finalized   → upsert invoices history
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session
from starlette.status import HTTP_200_OK

from .. import password_reset
from ..auth import hash_key, require_role, resolve_org
from ..config import get_settings
from ..db import SessionLocal, get_db
from ..models import ApiKey, Invoice, MarketingLead, Organization, StripeEvent, User
from .logs import limiter

log = logging.getLogger("foxy.billing")
router = APIRouter()

_SUBSCRIPTION_EVENTS = ("customer.subscription.updated", "customer.subscription.deleted")
_INVOICE_EVENTS = ("invoice.paid", "invoice.payment_failed", "invoice.finalized")


def _generate_api_key() -> tuple[str, str]:
    """Return (plaintext_key, sha256_hash).  The plaintext is shown once."""
    key = "foxy_sk_" + secrets.token_hex(24)
    key_hash = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return key, key_hash


class SignupRequest(BaseModel):
    email: str
    name: str | None = None
    plan: str | None = None          # self-serve signup is always the free tier


@router.post("/v1/signup")
@limiter.limit("5/minute")
def signup(payload: SignupRequest, request: Request, db: Session = Depends(get_db)):
    """Self-serve FREE signup (Phase 6 · 6A): provision a usable free-tier org + a
    peppered SDK key + an INVITED admin (a set-password link is emailed — no shared
    password). Returns the SDK key ONCE."""
    email_addr = payload.email.strip().lower()
    if "@" not in email_addr or "." not in email_addr.split("@")[-1]:
        raise HTTPException(status_code=422, detail="a valid email is required")

    existing = db.execute(select(Organization).where(
        func.lower(Organization.contact_email) == email_addr,
        Organization.deleted_at.is_(None),
    )).scalars().first()
    if existing is not None:
        raise HTTPException(status_code=409, detail="an account already exists for this email")

    plaintext_key, key_hash = _generate_api_key()
    org = Organization(name=(payload.name or email_addr).strip()[:255],
                       api_key_hash=key_hash, plan_tier="free", contact_email=email_addr,
                       trial_ends_at=datetime.now(timezone.utc) + timedelta(
                           days=get_settings().trial_days),
                       monthly_log_quota=get_settings().quota_for("free"))
    db.add(org)
    db.flush()
    db.add(ApiKey(org_id=org.id, name="primary",
                  key_prefix=plaintext_key[:11] + "…" + plaintext_key[-4:],
                  key_hash=hash_key(plaintext_key), status="active"))
    placeholder = bcrypt.hashpw(secrets.token_urlsafe(32).encode(), bcrypt.gensalt()).decode()
    admin = User(org_id=org.id, email=email_addr, password_hash=placeholder, role="admin")
    db.add(admin)
    lead = db.execute(select(MarketingLead).where(
        func.lower(MarketingLead.email) == email_addr,
        MarketingLead.status != "churned")).scalars().first()
    if lead is not None:
        lead.status, lead.converted_org_id = "converted", org.id
    db.commit()
    db.refresh(admin)
    # Email the invited admin a set-password link (reuse the 5D invite flow).
    password_reset.issue_reset(db, admin, admin.email, get_settings().dashboard_url, invite=True)
    return {"status": "created", "org_id": str(org.id), "api_key": plaintext_key,
            "message": "Check your email to set your password."}


class CheckoutRequest(BaseModel):
    email: str
    plan: str = "pro"


# plan → (price-id config attr, Stripe Checkout mode). Subscriptions recur;
# the lifetime "guardian" tier is a one-time charge (mode=payment).
_PLANS = {
    "pro":       ("stripe_price_pro", "subscription"),
    "max":       ("stripe_price_max", "subscription"),
    "companion": ("stripe_price_companion", "subscription"),
    "guardian":  ("stripe_price_guardian", "payment"),
}


@router.post("/v1/billing/checkout-session")
@limiter.limit("5/minute")
def checkout_session(payload: CheckoutRequest, request: Request):
    """Create a Stripe Checkout Session for a paid plan and return its URL; the
    webhook provisions the org once payment completes. (Phase 6 · 6A)"""
    s = get_settings()
    if not s.stripe_secret_key:
        raise HTTPException(status_code=503, detail="billing not configured")
    email_addr = payload.email.strip().lower()
    if "@" not in email_addr or "." not in email_addr.split("@")[-1]:
        raise HTTPException(status_code=422, detail="a valid email is required")
    requested_plan = payload.plan.strip().lower()
    plan = _PLANS.get(requested_plan)
    price_id = getattr(s, plan[0], "") if plan else ""
    if not plan or not price_id:
        raise HTTPException(status_code=422, detail="unknown or unconfigured plan")
    try:
        import stripe
        stripe.api_key = s.stripe_secret_key
        canonical_plan = get_settings().canonical_plan(requested_plan)
        session = stripe.checkout.Session.create(
            mode=plan[1],
            line_items=[{"price": price_id, "quantity": 1}],
            customer_email=email_addr,
            metadata={"foxy_plan": canonical_plan},
            success_url=f"{s.dashboard_url}?checkout=success",
            cancel_url=f"{s.dashboard_url}?checkout=cancel",
        )
        return {"checkout_url": session.url}
    except HTTPException:
        raise
    except Exception as exc:                # noqa: BLE001
        log.warning("checkout session failed: %s", exc)
        raise HTTPException(status_code=502, detail="could not start checkout")


def _ts(value) -> datetime | None:
    """Stripe unix timestamp → tz-aware datetime (or None)."""
    if not value:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


@router.post("/v1/webhooks/stripe", status_code=HTTP_200_OK)
async def stripe_webhook(
    request: Request,
    stripe_signature: str = Header(default="", alias="stripe-signature"),
):
    settings = get_settings()
    if not settings.stripe_webhook_secret:
        raise HTTPException(status_code=503, detail="Stripe not configured")

    body = await request.body()
    try:
        import stripe

        event = stripe.Webhook.construct_event(
            body, stripe_signature, settings.stripe_webhook_secret
        )
    except ImportError:
        raise HTTPException(status_code=503, detail="stripe package not installed")
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid webhook signature")
    except Exception as exc:
        log.warning("stripe webhook parse error: %s", exc)
        raise HTTPException(status_code=400, detail="Webhook parse error")

    # Signature is verified above; read fields from the plain JSON. (The stripe
    # Event object's .get() is NOT dict-compatible in stripe v15+ — it raises
    # AttributeError — so never call .get() on it.)
    payload_json = json.loads(body)
    event_id = payload_json.get("id", "")
    event_type = payload_json.get("type", "")
    data_obj = payload_json.get("data", {}).get("object", {})

    db: Session = SessionLocal()
    try:
        # (1) Idempotent durable log. No returned id → we've seen this event: no-op.
        row_id = db.execute(
            pg_insert(StripeEvent)
            .values(stripe_event_id=event_id, type=event_type,
                    payload=payload_json, status="received")
            .on_conflict_do_nothing(index_elements=["stripe_event_id"])
            .returning(StripeEvent.id)
        ).scalar_one_or_none()
        if row_id is None:
            db.rollback()
            return {"status": "duplicate", "type": event_type}

        # (2) Dispatch + stamp the event row in the SAME transaction as the mutation.
        try:
            if event_type == "checkout.session.completed":
                result, org_id = _handle_checkout(db, data_obj)
            elif event_type in _SUBSCRIPTION_EVENTS:
                result, org_id = _handle_subscription_change(db, data_obj)
            elif event_type in _INVOICE_EVENTS:
                result, org_id = _handle_invoice(db, data_obj)
            else:
                result, org_id = {"status": "ignored", "type": event_type}, None

            final = "ignored" if result.get("status") == "ignored" else "processed"
            db.execute(
                update(StripeEvent).where(StripeEvent.id == row_id)
                .values(status=final, org_id=org_id, processed_at=func.now())
            )
            db.commit()
            # Post-commit side-effect (never before the row is durable): a fresh
            # Checkout provisioning emails the new admin a set-password link. API
            # keys are deliberately created only from the authenticated dashboard.
            if (event_type == "checkout.session.completed"
                    and result.get("status") == "provisioned"):
                _deliver_credentials(db, org_id)
            return result
        except Exception as exc:  # noqa: BLE001 — persist the failure, never drop the record
            db.rollback()
            db.execute(
                update(StripeEvent).where(StripeEvent.id == row_id)
                .values(status="failed", error=str(exc)[:500])
            )
            db.commit()
            log.warning("stripe event %s (%s) failed: %s", event_id, event_type, exc)
            raise HTTPException(status_code=500, detail="event processing failed")
    finally:
        db.close()


def _handle_checkout(db: Session, session: dict) -> tuple[dict, str | None]:
    """Provision a new Organization when a Stripe checkout completes, and mark the
    matching marketing lead converted. No commit here — the caller commits once."""
    customer_id = session.get("customer", "")
    customer_email = (session.get("customer_email") or "unknown").strip().lower()
    subscription_id = session.get("subscription", "")
    metadata = session.get("metadata") or {}
    plan_tier = get_settings().canonical_plan(metadata.get("foxy_plan") or "pro")
    if plan_tier not in {"pro", "max", "premium"}:
        plan_tier = "pro"

    existing = db.execute(
        select(Organization).where(Organization.stripe_customer_id == customer_id)
    ).scalar_one_or_none()
    if existing:
        return {"status": "already_provisioned", "org_id": str(existing.id)}, str(existing.id)

    # ``organizations.api_key_hash`` is a required legacy column. Keep it bound to
    # an unrecoverable random value rather than minting a bearer key that would need
    # to be transported by email. The buyer creates their first named key after
    # setting a password and signing in to the dashboard.
    inactive_legacy_hash = hashlib.sha256(secrets.token_bytes(32)).hexdigest()
    org = Organization(
        name=customer_email, api_key_hash=inactive_legacy_hash, stripe_customer_id=customer_id,
        stripe_subscription_id=subscription_id, plan_tier=plan_tier,
        subscription_status="active", contact_email=customer_email,
        monthly_log_quota=get_settings().quota_for(plan_tier),
    )
    db.add(org)
    db.flush()   # assign org.id without committing

    # Make the org usable without sending a bearer secret over email. The invited
    # admin sets a password, signs in, and creates a named SDK key in /v1/keys,
    # where plaintext is returned exactly once over the authenticated session.
    db.add(User(
        org_id=org.id, email=customer_email,
        password_hash=bcrypt.hashpw(secrets.token_urlsafe(32).encode(), bcrypt.gensalt()).decode(),
        role="admin",
    ))

    # Funnel close: tag an active lead for this email as converted.
    lead = db.execute(
        select(MarketingLead).where(
            func.lower(MarketingLead.email) == customer_email,
            MarketingLead.status != "churned",
        )
    ).scalars().first()
    if lead is not None:
        lead.status = "converted"
        lead.converted_org_id = org.id

    log.info("Provisioned org %s for customer %s", org.id, customer_id)
    return {"status": "provisioned", "org_id": str(org.id)}, str(org.id)


def _deliver_credentials(db: Session, org_id: str | None) -> None:
    """After checkout commits, email only a password-set invitation.

    Bearer API keys must never be sent by email. The recipient creates a named key
    after signing in, where the dashboard can display it once over an authenticated
    session. Delivery is best-effort so a mail failure cannot trigger a duplicate
    Stripe provisioning attempt.
    """
    if not org_id:
        return
    try:
        admin = db.execute(
            select(User).where(User.org_id == uuid.UUID(str(org_id)), User.role == "admin")
        ).scalars().first()
        if admin is None:
            return
        password_reset.issue_reset(db, admin, admin.email,
                                   get_settings().dashboard_url, invite=True)
    except Exception as exc:                # noqa: BLE001
        log.warning("credential delivery failed for org %s: %s", org_id, exc)


def _handle_subscription_change(db: Session, subscription: dict) -> tuple[dict, str | None]:
    """Update org subscription_status when Stripe notifies us."""
    customer_id = subscription.get("customer", "")
    status_map = {
        "active": "active", "past_due": "past_due", "canceled": "cancelled",
        "unpaid": "past_due", "trialing": "active",
    }
    mapped = status_map.get(subscription.get("status", "unknown"),
                            subscription.get("status", "unknown"))

    org = db.execute(
        select(Organization).where(Organization.stripe_customer_id == customer_id)
    ).scalar_one_or_none()
    if org is None:
        return {"status": "org_not_found", "customer": customer_id}, None
    org.subscription_status = mapped
    log.info("Updated org %s status → %s", org.id, mapped)
    return {"status": "updated", "subscription_status": mapped}, str(org.id)


def _handle_invoice(db: Session, inv: dict) -> tuple[dict, str | None]:
    """Upsert one invoice into the per-org billing history."""
    customer_id = inv.get("customer", "")
    org = db.execute(
        select(Organization).where(Organization.stripe_customer_id == customer_id)
    ).scalar_one_or_none()
    if org is None:
        return {"status": "org_not_found", "customer": customer_id}, None

    # Scope RLS to this org before writing the RLS-protected invoices row (no-op
    # under the superuser role, required under a hardened one — as worker.py does).
    db.execute(text("SELECT set_config('app.current_org', :oid, true)"),
               {"oid": str(org.id)})

    amount = inv.get("amount_paid") or inv.get("amount_due") or inv.get("total") or 0
    values = {
        "org_id": org.id,
        "stripe_invoice_id": inv.get("id", ""),
        "amount_cents": int(amount),
        "currency": (inv.get("currency") or "usd")[:3],
        "status": (inv.get("status") or "open")[:16],
        "period_start": _ts(inv.get("period_start")),
        "period_end": _ts(inv.get("period_end")),
    }
    db.execute(
        pg_insert(Invoice).values(**values)
        .on_conflict_do_update(
            index_elements=["stripe_invoice_id"],
            set_={"status": values["status"], "amount_cents": values["amount_cents"],
                  "period_start": values["period_start"], "period_end": values["period_end"]},
        )
    )
    return {"status": "invoice_recorded", "invoice": values["stripe_invoice_id"]}, str(org.id)


# ─────────────── plan view + billing portal (P1 · §E) ───────────────────────

class PlanResponse(BaseModel):
    plan_tier: str | None = None
    subscription_status: str | None = None
    trial_ends_at: str | None = None
    monthly_log_quota: int | None = None     # NULL = unlimited
    has_billing_account: bool = False        # a Stripe customer exists → portal available


@router.get("/v1/billing/plan", response_model=PlanResponse)
def billing_plan(org: Organization = Depends(resolve_org)):
    """Current plan / subscription snapshot for the caller's org (session or key)."""
    return PlanResponse(
        plan_tier=org.plan_tier,
        subscription_status=org.subscription_status,
        trial_ends_at=org.trial_ends_at.isoformat() if org.trial_ends_at else None,
        monthly_log_quota=org.monthly_log_quota,
        has_billing_account=bool(org.stripe_customer_id),
    )


@router.post("/v1/billing/portal")
@limiter.limit("10/minute")
def billing_portal(request: Request, admin: User = Depends(require_role("admin")),
                   db: Session = Depends(get_db)):
    """Create a Stripe billing-portal session so an admin can manage the
    subscription (update card, cancel, view invoices). Admin only. Returns 503
    when billing isn't configured and 400 before the org has a Stripe customer."""
    s = get_settings()
    if not s.stripe_secret_key:
        raise HTTPException(status_code=503, detail="billing not configured")
    org = db.get(Organization, admin.org_id)
    if org is None or not org.stripe_customer_id:
        raise HTTPException(status_code=400,
                            detail="no billing account yet — start a paid plan first")
    try:
        import stripe
        stripe.api_key = s.stripe_secret_key
        session = stripe.billing_portal.Session.create(
            customer=org.stripe_customer_id,
            return_url=f"{s.dashboard_url}?billing=portal",
        )
        return {"portal_url": session.url}
    except HTTPException:
        raise
    except Exception as exc:                 # noqa: BLE001
        log.warning("billing portal failed: %s", exc)
        raise HTTPException(status_code=502, detail="could not open billing portal")
