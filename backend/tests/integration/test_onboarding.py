"""Self-serve onboarding (Phase 6 · 6A).

Free signup provisions a usable free-tier org (peppered SDK key that immediately
works) + an INVITED admin (set-password link emailed, no shared password), and
returns the SDK key once. Paid checkout returns a Stripe Checkout URL.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime, timezone


def _signed_header(payload: str, secret: str) -> str:
    """Build a Stripe-style `t=…,v1=…` header the SDK's construct_event accepts."""
    ts = int(time.time())
    sig = hmac.new(secret.encode(), f"{ts}.{payload}".encode(), hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


def _mock_email(monkeypatch) -> dict:
    from app import password_reset as pr
    sent: dict = {}
    monkeypatch.setattr(pr.email, "send_email", lambda **kw: (sent.update(kw), True)[1])
    return sent


def test_free_signup_creates_usable_org(client, monkeypatch):
    sent = _mock_email(monkeypatch)
    r = client.post("/v1/signup", json={"email": "founder@newco.test", "name": "New Co"})
    assert r.status_code == 200
    body = r.json()

    key = body["api_key"]
    assert key.startswith("foxy_sk_")
    # the SDK key is immediately usable (the peppered require_org path)
    assert client.get("/v1/logs", headers={"Authorization": f"Bearer {key}"}).status_code == 200
    # a set-password invite email went to the founder (no shared password in the response)
    assert "founder@newco.test" in str(sent)
    assert not body.get("temp_password")
    from app.db import SessionLocal
    from app.models import Organization
    db = SessionLocal()
    try:
        org = db.get(Organization, uuid.UUID(body["org_id"]))
        assert org.plan_tier == "free"
        assert org.trial_ends_at is not None
        assert 6.9 <= (org.trial_ends_at - datetime.now(timezone.utc)).total_seconds() / 86400 <= 7.1
    finally:
        db.close()


def test_free_signup_rejects_bad_email(client):
    assert client.post("/v1/signup", json={"email": "not-an-email"}).status_code == 422


def test_checkout_session_returns_url(client, monkeypatch):
    # Stripe is mocked — the endpoint must build a Checkout Session (right price +
    # mode per plan) and return its URL.
    import types
    captured: dict = {}

    def _create(**kw):
        captured.clear(); captured.update(kw)
        return types.SimpleNamespace(url="https://checkout.stripe.test/pay/abc")

    fake_stripe = types.SimpleNamespace(
        checkout=types.SimpleNamespace(Session=types.SimpleNamespace(create=_create)))
    monkeypatch.setitem(__import__("sys").modules, "stripe", fake_stripe)

    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "stripe_secret_key", "sk_test_x")
    monkeypatch.setattr(get_settings(), "stripe_price_companion", "price_comp_x")
    monkeypatch.setattr(get_settings(), "stripe_price_max", "price_max_x")
    monkeypatch.setattr(get_settings(), "stripe_price_guardian", "price_guard_x")

    # subscription plan
    r = client.post("/v1/billing/checkout-session",
                    json={"email": "buyer@co.test", "plan": "companion"})
    assert r.status_code == 200
    assert r.json()["checkout_url"].startswith("https://checkout.stripe.test/")
    assert captured["mode"] == "subscription"
    assert captured["line_items"][0]["price"] == "price_comp_x"
    assert captured["metadata"]["foxy_plan"] == "pro"

    r = client.post("/v1/billing/checkout-session",
                    json={"email": "buyer@co.test", "plan": "max"})
    assert r.status_code == 200
    assert captured["mode"] == "subscription"
    assert captured["line_items"][0]["price"] == "price_max_x"
    assert captured["metadata"]["foxy_plan"] == "max"

    # one-time (lifetime) plan → mode=payment
    r = client.post("/v1/billing/checkout-session",
                    json={"email": "buyer@co.test", "plan": "guardian"})
    assert r.status_code == 200
    assert captured["mode"] == "payment"
    assert captured["line_items"][0]["price"] == "price_guard_x"


def test_checkout_webhook_invites_admin_without_emailing_a_bearer_key(client, monkeypatch):
    """A completed checkout sends a password-set invitation, never a bearer key."""
    from app.config import get_settings
    from app import email as emailmod
    secret = "test-webhook-secret"
    monkeypatch.setattr(get_settings(), "stripe_webhook_secret", secret)
    sends: list = []
    monkeypatch.setattr(emailmod, "send_email", lambda **kw: (sends.append(kw), True)[1])

    payload = json.dumps({
        "id": "evt_checkout_6a", "object": "event",
        "type": "checkout.session.completed",
        "data": {"object": {"customer": "cus_6A", "customer_email": "paid@co.test",
                            "subscription": "sub_6A",
                            "metadata": {"foxy_plan": "max"}}}})
    r = client.post("/v1/webhooks/stripe", content=payload,
                    headers={"stripe-signature": _signed_header(payload, secret),
                             "content-type": "application/json"})
    assert r.status_code == 200

    blob = json.dumps(sends)
    assert "reset_token=" in blob          # a set-password invite link was emailed
    assert "paid@co.test" in blob          # to the new admin
    assert "foxy_sk_" not in blob          # API keys are never transported by email
    from app.db import SessionLocal
    from app.models import ApiKey, Organization
    db = SessionLocal()
    try:
        org = db.query(Organization).filter(Organization.contact_email == "paid@co.test").one()
        assert org.plan_tier == "max"
        assert org.monthly_log_quota == get_settings().quota_for("max")
        assert not db.query(ApiKey).filter(
            ApiKey.org_id == org.id, ApiKey.status == "active").count()
    finally:
        db.close()
