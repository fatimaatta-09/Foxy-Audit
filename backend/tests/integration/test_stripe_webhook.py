"""Stripe webhook signature verification (Phase 5 · 5H).

The /v1/webhooks/stripe endpoint verifies the Stripe signature before trusting
the payload — the billing tests elsewhere call _handle_checkout directly and
bypass this. Missing config → 503, bad signature → 400, a validly-signed payload
is accepted and processed.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time


def _signed_header(payload: str, secret: str) -> str:
    """Build a Stripe-style `t=…,v1=…` header the SDK's construct_event accepts."""
    ts = int(time.time())
    sig = hmac.new(secret.encode(), f"{ts}.{payload}".encode(), hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


def test_webhook_503_when_unconfigured(client):
    # STRIPE_WEBHOOK_SECRET defaults to "" in tests → endpoint reports not-configured
    r = client.post("/v1/webhooks/stripe", content="{}",
                    headers={"stripe-signature": "t=1,v1=x"})
    assert r.status_code == 503


def test_webhook_rejects_bad_signature(client, monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "stripe_webhook_secret", "test-webhook-secret")
    r = client.post("/v1/webhooks/stripe", content='{"id":"evt_bad"}',
                    headers={"stripe-signature": "t=1,v1=deadbeef"})
    assert r.status_code == 400


def test_webhook_accepts_valid_signature(client, monkeypatch):
    from app.config import get_settings
    secret = "test-webhook-secret"
    monkeypatch.setattr(get_settings(), "stripe_webhook_secret", secret)
    # Real Stripe events carry a top-level "object":"event" — construct_event reads it.
    payload = json.dumps({"id": "evt_ok_5h", "object": "event", "type": "ping",
                          "data": {"object": {}}})
    r = client.post("/v1/webhooks/stripe", content=payload,
                    headers={"stripe-signature": _signed_header(payload, secret),
                             "content-type": "application/json"})
    assert r.status_code == 200
