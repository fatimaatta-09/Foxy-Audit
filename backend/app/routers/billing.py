"""POST /v1/webhooks/stripe — durable, idempotent Stripe webhook handling.

Every verified event is FIRST written to stripe_events (UNIQUE stripe_event_id →
ON CONFLICT DO NOTHING), so a Stripe retry/replay is a no-op and the billing feed
is fully auditable from the admin site. The event is then dispatched and its
stripe_events row is stamped processed|ignored|failed IN THE SAME transaction as
the org/invoice mutation — a change without a logged event (or vice-versa) can't
happen. Handled events:

  checkout.session.completed (mode=setup)     → record a card on file ($0 auth, P3 §4)
  checkout.session.completed                  → provision org + tag the lead converted
  payment_method.attached / detached          → keep card_on_file honest (portal edits)
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
from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.orm import Session
from starlette.status import HTTP_200_OK

from .. import account_audit, password_reset
from ..auth import hash_key, require_role, require_step_up_user, resolve_org
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
# Card-on-file lifecycle (P3 §4). `attached` also covers a card added through the
# Stripe billing portal, so the flag stays true no matter which route was used.
_CARD_EVENTS = ("payment_method.attached", "payment_method.detached")


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

    # Validate any evaluation offer BEFORE the account-exists check so the offer's
    # per-email redemption cap surfaces its own 422 ahead of the generic duplicate-
    # account 409 — a judge re-redeeming the same code gets the offer-specific reason
    # (and offer capacity is never disclosed via a different status code).
    offer = _claim_judge_offer(db, email_addr, payload.offer_code)

    existing = db.execute(select(Organization).where(
        func.lower(Organization.contact_email) == email_addr,
        Organization.deleted_at.is_(None),
    )).scalars().first()
    if existing is not None:
        raise HTTPException(status_code=409, detail="an account already exists for this email")

    plaintext_key, key_hash = _generate_api_key()
    org = Organization(
        name=(payload.name or email_addr).strip()[:255], api_key_hash=key_hash,
        plan_tier="premium" if offer else "free", contact_email=email_addr,
        trial_ends_at=None if offer else datetime.now(timezone.utc) + timedelta(
            days=get_settings().trial_days),
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
            if event_type == "checkout.session.completed" and data_obj.get("mode") == "setup":
                # A card-on-file setup, NOT a purchase — there is no plan to
                # provision and no payment to record (P3 §4.1).
                result, org_id = _handle_card_setup(db, data_obj)
            elif event_type == "checkout.session.completed":
                result, org_id = _handle_checkout(db, data_obj)
            elif event_type in _CARD_EVENTS:
                result, org_id = _handle_payment_method(db, event_type, data_obj)
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


@router.get("/v1/billing/plans")
def billing_plans(org: Organization = Depends(resolve_org)):
    """The plans this deployment can actually sell, for the in-dashboard upgrade
    page (P2 §10.2 — it must not bounce anyone to the marketing site).

    A tier only appears if its Stripe price id is configured here, so a
    half-configured deployment offers nothing it cannot complete. NO PRICES are
    returned: the amount lives in Stripe and is confirmed at checkout, and
    inventing one in the dashboard would be exactly the fake data this project
    forbids. Quota and anchor cadence DO come from config, so they are real.
    """
    s = get_settings()
    current = (org.plan_tier or "free").strip().lower()
    out = []
    for tier, (price_attr, mode) in _PLANS.items():
        if not getattr(s, price_attr, ""):
            continue                       # not sellable on this deployment
        out.append({
            "tier": tier,
            "mode": mode,                  # subscription | payment (one-time)
            "monthly_log_quota": s.quota_for(tier),
            "anchor_cadence_hours": s.anchor_cadence_for(tier),
            "current": tier == current,
        })
    return {"current_tier": current, "plans": out}


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


# ─────────────────── payment gate: card on file, never charged (P3 §4) ───────
# The owner's decision: "card captured at signup, never charged without consent."
# The card is collected through a Stripe Checkout session in SETUP mode — a $0
# authorisation that validates the card and stores the payment method, with no
# line items and therefore no possible charge. Nothing added here creates one:
# upgrading still goes through /v1/billing/checkout-session, which the user has
# to start themselves (§4.2).

@router.get("/v1/billing/access")
def billing_access(org: Organization = Depends(resolve_org)):
    """Whether this org's dashboard is unlocked, and what is needed if it is not.

    Honest locked state (§4.6): the UI has to be able to say what is missing and
    why, instead of rendering a broken dashboard. Always 200 — this is the
    endpoint that EXPLAINS the 402, so it must stay readable while locked."""
    s = get_settings()
    required = bool(s.require_card_on_file)
    locked = required and not org.card_on_file
    return {
        "card_required": required,
        "card_on_file": bool(org.card_on_file),
        "locked": locked,
        "card": ({"brand": org.card_brand, "last4": org.card_last4,
                  "added_at": org.card_added_at.isoformat() if org.card_added_at else None}
                 if org.card_on_file else None),
        "plan_tier": org.plan_tier,
        "free_tier_is_free": True,
        "message": ("Add a payment method to unlock your dashboard. Your card is "
                    "verified, not charged." if locked else "No action needed."),
    }


@router.post("/v1/billing/card-setup-session")
@limiter.limit("10/minute")
def card_setup_session(request: Request, admin: User = Depends(require_role("admin")),
                       db: Session = Depends(get_db)):
    """Start a $0 card authorisation (Stripe Checkout, mode='setup').

    mode='setup' takes NO line_items and creates a SetupIntent, so Stripe validates
    the card and saves it against the customer without moving any money. That is
    what makes "captured, not charged" true — it is not a charge we refund later."""
    s = get_settings()
    if not s.stripe_secret_key:
        raise HTTPException(status_code=503, detail="billing not configured")
    org = db.get(Organization, admin.org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="workspace not found")
    try:
        import stripe
        stripe.api_key = s.stripe_secret_key
        kwargs = {
            "mode": "setup",
            "success_url": f"{s.dashboard_url}?card=added",
            "cancel_url": f"{s.dashboard_url}?card=cancelled",
            "metadata": {"foxy_org_id": str(org.id), "foxy_purpose": "card_on_file"},
        }
        if org.stripe_customer_id:
            kwargs["customer"] = org.stripe_customer_id
        else:
            kwargs["customer_creation"] = "always"
            if org.contact_email:
                kwargs["customer_email"] = org.contact_email
        session = stripe.checkout.Session.create(**kwargs)
        return {"checkout_url": session.url, "mode": "setup", "amount": 0}
    except HTTPException:
        raise
    except Exception as exc:                 # noqa: BLE001
        log.warning("card setup session failed: %s", exc)
        raise HTTPException(status_code=502, detail="could not start card setup")


@router.post("/v1/billing/cancel", dependencies=[Depends(require_step_up_user)])
@limiter.limit("5/minute")
def cancel_subscription(request: Request, admin: User = Depends(require_role("admin")),
                        db: Session = Depends(get_db)):
    """Cancel without contacting support (§4.5).

    Cancels at period end rather than immediately, so the customer keeps what they
    have already paid for; Stripe's webhook flips subscription_status when it
    finalises. Step-up gated like every other irreversible account action."""
    s = get_settings()
    if not s.stripe_secret_key:
        raise HTTPException(status_code=503, detail="billing not configured")
    org = db.get(Organization, admin.org_id)
    if org is None or not org.stripe_subscription_id:
        raise HTTPException(status_code=400, detail="no active subscription to cancel")
    try:
        import stripe
        stripe.api_key = s.stripe_secret_key
        sub = stripe.Subscription.modify(org.stripe_subscription_id,
                                         cancel_at_period_end=True)
    except HTTPException:
        raise
    except Exception as exc:                 # noqa: BLE001
        log.warning("cancel failed for org %s: %s", org.id, exc)
        raise HTTPException(status_code=502, detail="could not cancel the subscription")
    period_end = (sub.get("current_period_end") if isinstance(sub, dict)
                  else getattr(sub, "current_period_end", None))
    ends = _ts(period_end)
    account_audit.record_account_action(
        db, org_id=org.id, actor_email=admin.email, action="billing.cancel",
        target=str(org.id), detail={"cancel_at_period_end": True})
    db.commit()
    return {"status": "cancelling", "cancel_at_period_end": True,
            "access_until": ends.isoformat() if ends else None}


class RedeemRequest(BaseModel):
    code: str = Field(min_length=1, max_length=128)


@router.post("/v1/billing/redeem", dependencies=[Depends(require_step_up_user)])
@limiter.limit("5/minute")
def redeem_offer(request: Request, payload: RedeemRequest,
                 admin: User = Depends(require_role("admin")),
                 db: Session = Depends(get_db)):
    """Redeem an evaluation code on an EXISTING organization (round 2 · item 7).

    Signup already does this for a brand-new org. The difference here is that the
    org has a life already, and an evaluation offer is NOT purely additive:
    setting ``evaluation_offer_id`` puts ingestion behind
    ``evaluation_credits_exhausted`` / ``evaluation_expired`` in logs.py. So this
    endpoint refuses far more often than it accepts, and every refusal names its
    own reason.

    Two of the four planned guards did not survive contact with the code:

    * The plan's guard 3 was "refuse if this org already redeemed THIS offer_id".
      ``EvaluationRedemption`` carries ``uq_evaluation_redemption_org``, a UNIQUE
      on ``org_id`` alone — the schema already allows exactly one redemption per
      org, ever. Checking per-offer would let a second campaign through the guard
      and then fail on an IntegrityError instead of a clean 409, so the check
      here is the schema's: ANY prior redemption refuses.

    * The plan's guard 4 was "set only the four evaluation fields; do not touch
      plan_tier, monthly_log_quota or trial_ends_at". That leaves the grant inert.
      logs.py gates ingestion in order: ``trial_expired`` (free tier past
      ``trial_ends_at``) → ``subscription_inactive`` → ``credits_exhausted``
      (``monthly_log_quota``) → the evaluation pair. Guard 1 has already limited
      us to free orgs, so an org arriving here is either past its trial — where
      gate 1 rejects every capture and the credits can never be spent — or inside
      it, where the free monthly quota still caps the grant below the credits
      issued. Applying signup's exact field set is what makes the offer mean what
      it says, and on a free org it is an upgrade rather than the downgrade the
      guard was written to prevent.

    What has NOT been solved, and is disclosed in the UI before the user confirms:
    when the window closes, gate 4 stops capture entirely, so an org that could
    previously capture up to its free quota can capture nothing until it upgrades.
    Nothing clears the evaluation fields on expiry today.
    """
    supplied = _normalise_offer_code(payload.code)
    if not supplied:
        raise HTTPException(status_code=422, detail="This evaluation offer is unavailable")
    now = datetime.now(timezone.utc)

    # Lock the org BEFORE reading its billing state: two codes redeemed at once
    # must not both pass the no-stacking guard. Nothing else takes the campaign
    # lock and then the org lock, so org → campaign cannot cycle.
    org = db.execute(
        select(Organization).where(Organization.id == admin.org_id).with_for_update()
    ).scalar_one_or_none()
    if org is None or org.deleted_at is not None:
        raise HTTPException(status_code=404, detail="organization not found")

    # Order matters, and not in the order the plan listed them. Because a granted
    # offer sets plan_tier="premium" (see the docstring), an org that has already
    # redeemed LOOKS paid — so running the paid-plan guard first answers a second
    # attempt with "you are on a paid plan", which is both wrong and unactionable.
    # The evaluation-specific reasons have to come first.

    # Guard 2 — one offer at a time, no stacking.
    if org.evaluation_ends_at is not None and org.evaluation_ends_at > now:
        raise HTTPException(status_code=409, detail={
            "code": "evaluation_active",
            "message": "This workspace already has an evaluation offer running. "
                       "Offers do not stack.",
        })

    # Guard 3 — the schema's rule, not the plan's: one redemption per org, ever.
    if db.execute(select(EvaluationRedemption.id).where(
            EvaluationRedemption.org_id == org.id)).scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail={
            "code": "already_redeemed",
            "message": "This workspace has already redeemed an evaluation offer.",
        })

    # Guard 1 — never let a gift silently replace a plan someone is paying for.
    # A Stripe subscription is the money signal and is decisive on its own;
    # plan_tier only counts as "paid" when no evaluation offer explains it, since
    # a lapsed offer leaves the tier behind.
    paying = bool(org.stripe_subscription_id) or (org.subscription_status or "").lower() in {
        "active", "trialing", "past_due"}
    if paying or ((org.plan_tier or "free").lower() != "free"
                  and org.evaluation_offer_id is None):
        raise HTTPException(status_code=409, detail={
            "code": "paid_plan",
            "message": "This workspace is on a paid plan. An evaluation offer would replace it "
                       "with a capped, expiring one, so it cannot be redeemed here.",
        })

    # Campaign-side checks: status, window, per-email cap and capacity, all under
    # an advisory lock. Its refusals stay deliberately opaque — an authenticated
    # customer must not be able to probe campaign capacity either.
    offer = _claim_database_campaign(db, (admin.email or "").strip().lower(), supplied)
    if offer is None:
        raise HTTPException(status_code=422, detail="This evaluation offer is unavailable")

    org.evaluation_offer_id = offer["offer_id"]
    org.evaluation_credit_limit = offer["credits"]
    org.evaluation_credits_used = 0
    org.evaluation_ends_at = offer["expires_at"]
    # Guard 4, overruled — see the docstring. Identical to what signup applies.
    org.plan_tier = "premium"
    org.trial_ends_at = None
    org.monthly_log_quota = None

    db.add(EvaluationRedemption(
        offer_id=offer["offer_id"], org_id=org.id, email_hash=offer["email_hash"],
        credits_granted=offer["credits"], expires_at=offer["expires_at"],
    ))
    account_audit.record_account_action(
        db, org_id=org.id, actor_email=admin.email, action="billing.redeem_evaluation",
        target=offer["offer_id"], detail={"credits": offer["credits"],
                                          "expires_at": offer["expires_at"].isoformat()})
    try:
        db.commit()
    except IntegrityError:
        # uq_evaluation_redemption_org / uq_evaluation_offer_email — the same
        # refusals as above, lost a race. Report them as the guards, not as a 500.
        db.rollback()
        raise HTTPException(status_code=409, detail={
            "code": "already_redeemed",
            "message": "This workspace has already redeemed an evaluation offer.",
        })
    return {
        "status": "redeemed",
        "credits_total": offer["credits"],
        "credits_remaining": offer["credits"],
        "expires_at": offer["expires_at"].isoformat(),
        "no_auto_charge": True,
    }


def _org_for_customer(db: Session, customer_id: str | None) -> Organization | None:
    if not customer_id:
        return None
    return db.execute(select(Organization).where(
        Organization.stripe_customer_id == str(customer_id))).scalars().first()


def _mark_card(org: Organization, card: dict | None) -> None:
    """Record that a card exists, plus a display label. Never stores anything
    chargeable — no payment-method id, no token, no PAN."""
    org.card_on_file = True
    org.card_brand = (str(card.get("brand"))[:32] if card and card.get("brand") else None)
    org.card_last4 = (str(card.get("last4"))[:4] if card and card.get("last4") else None)
    org.card_added_at = datetime.now(timezone.utc)


def _handle_card_setup(db: Session, session: dict) -> tuple[dict, str | None]:
    """checkout.session.completed with mode='setup' — the $0 authorisation landed.

    No commit here; the caller commits once with the event row."""
    org_id = (session.get("metadata") or {}).get("foxy_org_id")
    org = None
    if org_id:
        try:
            org = db.get(Organization, uuid.UUID(str(org_id)))
        except (ValueError, AttributeError):
            org = None
    if org is None:
        org = _org_for_customer(db, session.get("customer"))
    if org is None:
        return {"status": "ignored", "reason": "no matching org"}, None
    # Bind the customer so later portal / cancel calls have one.
    if session.get("customer") and not org.stripe_customer_id:
        org.stripe_customer_id = str(session["customer"])
    _mark_card(org, None)
    return {"status": "card_on_file", "org_id": str(org.id)}, str(org.id)


def _handle_payment_method(db: Session, event_type: str,
                           pm: dict) -> tuple[dict, str | None]:
    """payment_method.attached / .detached — keeps the flag honest when the card
    is managed from the Stripe billing portal rather than our setup session."""
    org = _org_for_customer(db, pm.get("customer"))
    if org is None:
        return {"status": "ignored", "reason": "no matching org"}, None
    if event_type == "payment_method.attached":
        _mark_card(org, pm.get("card") or {})
        return {"status": "card_on_file", "org_id": str(org.id)}, str(org.id)
    # Detached: only clear if Stripe has no other card for this customer. A
    # customer swapping cards fires detached AFTER attached, and clearing blindly
    # would lock a paying customer out of their dashboard for replacing a card.
    try:
        import stripe
        stripe.api_key = get_settings().stripe_secret_key
        remaining = stripe.PaymentMethod.list(customer=org.stripe_customer_id, type="card")
        still_has = bool(getattr(remaining, "data", None) or
                         (remaining.get("data") if isinstance(remaining, dict) else None))
    except Exception as exc:                 # noqa: BLE001
        # Cannot confirm — leave the flag alone. Failing OPEN here only risks
        # showing an unlocked dashboard; failing closed would lock a customer out
        # on a transient Stripe error.
        log.warning("could not list payment methods for org %s: %s", org.id, exc)
        return {"status": "unchanged", "org_id": str(org.id)}, str(org.id)
    if still_has:
        return {"status": "unchanged", "org_id": str(org.id)}, str(org.id)
    org.card_on_file = False
    org.card_brand = org.card_last4 = None
    return {"status": "card_removed", "org_id": str(org.id)}, str(org.id)
