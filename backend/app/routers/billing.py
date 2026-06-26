"""POST /v1/webhooks/stripe — handle Stripe subscription lifecycle events.

On ``checkout.session.completed`` the handler automatically provisions an
Organization, generates an API key, and persists the Stripe customer/
subscription references so the dashboard can display billing status.

On ``customer.subscription.updated`` / ``deleted`` it patches the org's
``subscription_status`` so the backend can enforce plan limits or revoke
access when a subscription lapses.
"""

from __future__ import annotations

import hashlib
import logging
import secrets

from fastapi import APIRouter, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.status import HTTP_200_OK

from ..config import get_settings
from ..db import SessionLocal
from ..models import Organization

log = logging.getLogger("foxy.billing")
router = APIRouter()


def _generate_api_key() -> tuple[str, str]:
    """Return (plaintext_key, sha256_hash).  The plaintext is shown once."""
    key = "foxy_sk_" + secrets.token_hex(24)
    key_hash = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return key, key_hash


@router.post("/v1/webhooks/stripe", status_code=HTTP_200_OK)
async def stripe_webhook(
    request: Request,
    stripe_signature: str = Header(default="", alias="stripe-signature"),
):
    settings = get_settings()
    if not settings.stripe_webhook_secret:
        raise HTTPException(status_code=503, detail="Stripe not configured")

    body = await request.body()

    # Verify signature to prevent spoofed events.
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

    event_type = event.get("type", "")
    data_obj = event.get("data", {}).get("object", {})

    if event_type == "checkout.session.completed":
        return _handle_checkout(data_obj)

    if event_type in (
        "customer.subscription.updated",
        "customer.subscription.deleted",
    ):
        return _handle_subscription_change(data_obj)

    # Acknowledge unhandled event types without error.
    return {"status": "ignored", "type": event_type}


def _handle_checkout(session: dict) -> dict:
    """Provision a new Organization when a Stripe checkout completes."""
    customer_id = session.get("customer", "")
    customer_email = session.get("customer_email", "unknown")
    subscription_id = session.get("subscription", "")

    plaintext_key, key_hash = _generate_api_key()

    db: Session = SessionLocal()
    try:
        # Prevent duplicate orgs if the webhook is retried.
        existing = db.execute(
            select(Organization).where(
                Organization.stripe_customer_id == customer_id
            )
        ).scalar_one_or_none()
        if existing:
            return {"status": "already_provisioned", "org_id": str(existing.id)}

        org = Organization(
            name=customer_email,
            api_key_hash=key_hash,
            stripe_customer_id=customer_id,
            stripe_subscription_id=subscription_id,
            plan_tier="pro",
            subscription_status="active",
        )
        db.add(org)
        db.commit()
        db.refresh(org)
    finally:
        db.close()

    log.info("Provisioned org %s for customer %s", org.id, customer_id)
    return {
        "status": "provisioned",
        "org_id": str(org.id),
        "api_key": plaintext_key,
    }


def _handle_subscription_change(subscription: dict) -> dict:
    """Update org subscription_status when Stripe notifies us."""
    customer_id = subscription.get("customer", "")
    new_status = subscription.get("status", "unknown")

    # Map Stripe status to our simplified enum.
    status_map = {
        "active": "active",
        "past_due": "past_due",
        "canceled": "cancelled",
        "unpaid": "past_due",
        "trialing": "active",
    }
    mapped = status_map.get(new_status, new_status)

    db: Session = SessionLocal()
    try:
        org = db.execute(
            select(Organization).where(
                Organization.stripe_customer_id == customer_id
            )
        ).scalar_one_or_none()
        if org is None:
            return {"status": "org_not_found", "customer": customer_id}

        org.subscription_status = mapped
        db.commit()
    finally:
        db.close()

    log.info("Updated org %s status → %s", org.id, mapped)
    return {"status": "updated", "subscription_status": mapped}
