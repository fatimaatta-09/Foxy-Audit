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
import hmac
import json
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session
from starlette.status import HTTP_200_OK

from .. import email, password_reset
from ..auth import hash_key
from ..config import get_settings
from ..db import SessionLocal, get_db
from ..models import (
    ApiKey, EvaluationCampaign, EvaluationRedemption, Invoice, MarketingLead, Organization,
    StripeEvent, User,
)
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
    offer_code: str | None = Field(default=None, max_length=128)


def _normalise_offer_code(value: str) -> str:
    return value.strip().upper()


def _offer_email_hash(email_addr: str) -> str:
    """Hash email with a domain-separated pepper for redemption uniqueness."""
    pepper = get_settings().api_key_pepper.encode("utf-8")
    return hmac.new(pepper, f"judge-offer-email:{email_addr}".encode("utf-8"),
                    hashlib.sha256).hexdigest()


def _offer_code_hash(code: str) -> str:
    """Return the non-reversible, domain-separated campaign-code fingerprint."""
    pepper = get_settings().api_key_pepper.encode("utf-8")
    return hmac.new(pepper, f"judge-offer-code:{code}".encode("utf-8"),
                    hashlib.sha256).hexdigest()


def _claim_database_campaign(
    db: Session, email_addr: str, supplied: str,
) -> dict | None:
    """Claim an active staff-managed campaign, if the code matches one.

    A transaction advisory lock serializes capacity checks across API workers.
    The row is re-read under ``FOR UPDATE`` after the lock so revocation and
    redemption cannot race with one another.
    """
    now = datetime.now(timezone.utc)
    code_hash = _offer_code_hash(supplied)
    candidate = db.execute(
        select(EvaluationCampaign.id).where(
            EvaluationCampaign.code_hash == code_hash,
            EvaluationCampaign.status == "active",
            or_(EvaluationCampaign.starts_at.is_(None), EvaluationCampaign.starts_at <= now),
            or_(EvaluationCampaign.ends_at.is_(None), EvaluationCampaign.ends_at > now),
        )
    ).scalar_one_or_none()
    if candidate is None:
        return None

    if db.bind is not None and db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:offer_id))"),
                   {"offer_id": str(candidate)})
    campaign = db.execute(
        select(EvaluationCampaign).where(EvaluationCampaign.id == candidate)
        .with_for_update()
    ).scalar_one_or_none()
    if campaign is None or campaign.status != "active":
        raise HTTPException(status_code=422, detail="This evaluation offer is unavailable")
    if ((campaign.starts_at is not None and campaign.starts_at > now) or
            (campaign.ends_at is not None and campaign.ends_at <= now)):
        raise HTTPException(status_code=422, detail="This evaluation offer is unavailable")

    email_hash = _offer_email_hash(email_addr)
    already_redeemed = db.execute(
        select(EvaluationRedemption.id).where(
            EvaluationRedemption.offer_id == campaign.offer_id,
            EvaluationRedemption.email_hash == email_hash,
        )
    ).scalar_one_or_none()
    redeemed_count = db.execute(
        select(func.count()).select_from(EvaluationRedemption).where(
            EvaluationRedemption.offer_id == campaign.offer_id,
        )
    ).scalar_one()
    if already_redeemed is not None or int(redeemed_count) >= campaign.max_redemptions:
        raise HTTPException(status_code=422, detail="This evaluation offer is unavailable")

    expires_at = now + timedelta(days=campaign.duration_days)
    return {
        "offer_id": campaign.offer_id,
        "email_hash": email_hash,
        "credits": campaign.credits,
        "expires_at": expires_at,
    }


def _claim_judge_offer(db: Session, email_addr: str, offer_code: str | None) -> dict | None:
    """Validate a database campaign or configured judge code in this txn.

    Database campaigns are the normal staff-managed path. The environment-backed
    offer remains as a deployment-safe fallback so existing installations can
    upgrade to migration 0036 without losing their launch code.
    """
    if not offer_code:
        return None

    settings = get_settings()
    supplied = _normalise_offer_code(offer_code)
    if not supplied:
        raise HTTPException(status_code=422, detail="This evaluation offer is unavailable")
    try:
        database_offer = _claim_database_campaign(db, email_addr, supplied)
    except ProgrammingError:
        # Keep the deployment-configured launch offer usable while an older
        # instance is being migrated. The failed table lookup aborts the
        # transaction, so clear it before evaluating the fallback.
        db.rollback()
        log.warning("evaluation_campaigns table is unavailable; using env offer fallback")
        database_offer = None
    if database_offer is not None:
        return database_offer

    configured = _normalise_offer_code(settings.judge_offer_code)
    offer_id = settings.judge_offer_id.strip()[:64]
    unavailable = (
        not configured
        or not supplied
        or not offer_id
        or not hmac.compare_digest(supplied, configured)
        or settings.judge_offer_credits <= 0
        or settings.judge_offer_days <= 0
        or settings.judge_offer_max_redemptions <= 0
    )
    if unavailable:
        raise HTTPException(status_code=422, detail="This evaluation offer is unavailable")

    # Production and integration tests use PostgreSQL. Keeping this guarded makes
    # lightweight local database experiments degrade safely instead of failing on
    # PostgreSQL-specific lock syntax.
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:offer_id))"),
                   {"offer_id": offer_id})

    email_hash = _offer_email_hash(email_addr)
    already_redeemed = db.execute(
        select(EvaluationRedemption.id).where(
            EvaluationRedemption.offer_id == offer_id,
            EvaluationRedemption.email_hash == email_hash,
        )
    ).scalar_one_or_none()
    redeemed_count = db.execute(
        select(func.count()).select_from(EvaluationRedemption).where(
            EvaluationRedemption.offer_id == offer_id,
        )
    ).scalar_one()
    if already_redeemed is not None or int(redeemed_count) >= settings.judge_offer_max_redemptions:
        # Do not distinguish an exhausted campaign from a previously redeemed
        # email; the public signup route must not disclose campaign activity.
        raise HTTPException(status_code=422, detail="This evaluation offer is unavailable")

    expires_at = datetime.now(timezone.utc) + timedelta(days=settings.judge_offer_days)
    return {
        "offer_id": offer_id,
        "email_hash": email_hash,
        "credits": settings.judge_offer_credits,
        "expires_at": expires_at,
    }


@router.post("/v1/signup")
@limiter.limit("5/minute")
def signup(payload: SignupRequest, request: Request, db: Session = Depends(get_db)):
    """Self-serve FREE signup (Phase 6 · 6A): provision a usable free-tier org + a
    peppered SDK key + an INVITED admin (a set-password link is emailed — no shared
    password). Returns the SDK key ONCE."""
    email_addr = payload.email.strip().lower()
    if "@" not in email_addr or "." not in email_addr.split("@")[-1]:
        raise HTTPException(status_code=422, detail="a valid email is required")

    offer = _claim_judge_offer(db, email_addr, payload.offer_code)
    plaintext_key, key_hash = _generate_api_key()
    org = Organization(
        name=(payload.name or email_addr).strip()[:255], api_key_hash=key_hash,
        plan_tier="premium" if offer else "free", contact_email=email_addr,
        monthly_log_quota=None if offer else get_settings().quota_for("free"),
        evaluation_offer_id=offer["offer_id"] if offer else None,
        evaluation_credit_limit=offer["credits"] if offer else None,
        evaluation_credits_used=0,
        evaluation_ends_at=offer["expires_at"] if offer else None,
    )
    db.add(org)
    db.flush()
    if offer:
        db.add(EvaluationRedemption(
            offer_id=offer["offer_id"], org_id=org.id, email_hash=offer["email_hash"],
            credits_granted=offer["credits"], expires_at=offer["expires_at"],
        ))
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
    response = {
        "status": "created", "org_id": str(org.id), "api_key": plaintext_key,
        "message": "Check your email to set your password.",
    }
    if offer:
        response["evaluation_offer"] = {
            "label": "Premium judge access",
            "credits_total": offer["credits"],
            "credits_remaining": offer["credits"],
            "expires_at": offer["expires_at"].isoformat(),
            "no_auto_charge": True,
        }
    return response


class CheckoutRequest(BaseModel):
    email: str
    plan: str = "pro"


# plan → (price-id config attr, Stripe Checkout mode). Subscriptions recur;
# the lifetime "guardian" tier is a one-time charge (mode=payment).
_PLANS = {
    "pro":       ("stripe_price_pro", "subscription"),
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
    plan = _PLANS.get(payload.plan.strip().lower())
    price_id = getattr(s, plan[0], "") if plan else ""
    if not plan or not price_id:
        raise HTTPException(status_code=422, detail="unknown or unconfigured plan")
    try:
        import stripe
        stripe.api_key = s.stripe_secret_key
        session = stripe.checkout.Session.create(
            mode=plan[1],
            line_items=[{"price": price_id, "quantity": 1}],
            customer_email=payload.email.strip().lower(),
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
            # checkout provision emails the new admin a set-password link + SDK key.
            if (event_type == "checkout.session.completed"
                    and result.get("status") == "provisioned"):
                _deliver_credentials(db, org_id, result.get("api_key", ""))
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

    existing = db.execute(
        select(Organization).where(Organization.stripe_customer_id == customer_id)
    ).scalar_one_or_none()
    if existing:
        return {"status": "already_provisioned", "org_id": str(existing.id)}, str(existing.id)

    plaintext_key, key_hash = _generate_api_key()
    org = Organization(
        name=customer_email, api_key_hash=key_hash, stripe_customer_id=customer_id,
        stripe_subscription_id=subscription_id, plan_tier="pro",
        subscription_status="active", contact_email=customer_email,
    )
    db.add(org)
    db.flush()   # assign org.id without committing

    # Make the org actually USABLE, not just an orphan row (Phase 5 · 5A.2):
    # a peppered API key (the primary require_org path) + an INVITED admin dashboard
    # user. The webhook emails a set-password link + the SDK key post-commit, so no
    # shared plaintext password is ever created (6A credential delivery).
    db.add(ApiKey(
        org_id=org.id, name="primary",
        key_prefix=plaintext_key[:11] + "…" + plaintext_key[-4:],
        key_hash=hash_key(plaintext_key), status="active",
    ))
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
    return {"status": "provisioned", "org_id": str(org.id),
            "api_key": plaintext_key}, str(org.id)


def _deliver_credentials(db: Session, org_id: str | None, api_key: str) -> None:
    """After a checkout provision COMMITS, email the new admin a set-password link
    (5D invite) + their SDK key (shown once). Best-effort: a mail failure must never
    fail the webhook — Stripe would otherwise retry an already-completed provision."""
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
        if api_key:
            email.send_email(
                to=admin.email, subject="Your Foxy Audit API key",
                html=("<p>Welcome to Foxy Audit! Your SDK key — store it securely, it "
                      f"is shown only once:</p><pre>{api_key}</pre>"),
                text=("Welcome to Foxy Audit! Your SDK key — store it securely, it is "
                      f"shown only once:\n{api_key}"))
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
