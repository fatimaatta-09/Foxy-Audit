"""Signup payment gate: card captured, never charged (P3 §4).

The owner's decision was "card collected at signup, free tier never charged
without an explicit upgrade, dashboard locked until a card is on file". The risk
in that shape is not technical — it is that the lock bricks people. So most of
what is asserted here is about the gate NOT trapping anyone: it is off by
default, it never blocks the endpoints you need to escape it, and it fails open
rather than closed when Stripe cannot be reached.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid

import pytest

from app.config import get_settings
from app.db import SessionLocal
from app.models import Organization


@pytest.fixture
def gate_on(monkeypatch):
    """Turning the gate on is TWO decisions, not one (P3 §4 grandfather clause).

    The flag alone is deliberately a no-op: with no cutoff chosen, every org is
    grandfathered, so enabling the flag by itself can never lock anybody out. A
    cutoff in the past is what actually brings orgs into scope, and these tests
    create their orgs now, so they land after it."""
    monkeypatch.setattr(get_settings(), "require_card_on_file", True)
    monkeypatch.setattr(get_settings(), "card_gate_grandfather_before",
                        "2020-01-01T00:00:00+00:00")


def _set_card(org_id, on: bool, brand="visa", last4="4242"):
    from datetime import datetime, timezone
    with SessionLocal() as db:
        o = db.get(Organization, uuid.UUID(str(org_id)))
        o.card_on_file = on
        o.card_brand = brand if on else None
        o.card_last4 = last4 if on else None
        o.card_added_at = datetime.now(timezone.utc) if on else None
        db.commit()


def _signed(payload: str, secret: str) -> str:
    ts = int(time.time())
    sig = hmac.new(secret.encode(), f"{ts}.{payload}".encode(), hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


def _send_event(client, monkeypatch, evt: dict):
    secret = "test-webhook-secret"
    monkeypatch.setattr(get_settings(), "stripe_webhook_secret", secret)
    body = json.dumps({"object": "event", **evt})
    return client.post("/v1/webhooks/stripe", content=body,
                       headers={"stripe-signature": _signed(body, secret),
                                "content-type": "application/json"})


# ── §4.3 · the lock, and everything that must not be locked ─────────────────

def test_gate_is_off_by_default(make_org, login):
    """Shipping this ON would lock every existing customer out on deploy."""
    assert get_settings().require_card_on_file is False
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    assert c.get("/v1/auth/me").status_code == 200
    assert c.get("/v1/logs").status_code == 200


def test_no_card_locks_the_dashboard_with_402(make_org, login, gate_on):
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    r = c.get("/v1/logs")
    assert r.status_code == 402
    detail = r.json()["detail"]
    assert detail["code"] == "card_required"
    assert "not charged" in detail["message"]


def test_a_card_on_file_unlocks_it(make_org, login, gate_on):
    org = make_org()
    _set_card(org["org_id"], True)
    c = login(org["admin_email"], org["admin_password"])
    assert c.get("/v1/logs").status_code == 200


def test_the_lock_never_traps_you(make_org, login, gate_on):
    """You must always be able to sign out, see who you are, read the reason,
    and reach billing to add the card that unlocks you. Lock any of these and
    the product is bricked with no way back."""
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    assert c.get("/v1/auth/me").status_code == 200
    assert c.get("/v1/billing/access").status_code == 200
    assert c.get("/v1/billing/plan").status_code == 200
    assert c.get("/v1/auth/sessions").status_code == 200
    assert c.post("/v1/auth/logout").status_code == 200


def test_locked_state_explains_itself(make_org, login, gate_on):
    """§4.6 — a clear explanation of what is needed, not a broken dashboard."""
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    d = c.get("/v1/billing/access").json()
    assert d["locked"] is True and d["card_required"] is True
    assert d["card_on_file"] is False and d["card"] is None
    assert d["free_tier_is_free"] is True
    assert "verified, not charged" in d["message"]


def test_access_reports_the_card_once_present(make_org, login, gate_on):
    org = make_org()
    _set_card(org["org_id"], True, brand="visa", last4="4242")
    c = login(org["admin_email"], org["admin_password"])
    d = c.get("/v1/billing/access").json()
    assert d["locked"] is False
    assert d["card"] == {"brand": "visa", "last4": "4242", **{"added_at": d["card"]["added_at"]}}
    assert d["card"]["added_at"]


def test_sdk_ingest_is_never_card_gated(make_org, client, gate_on):
    """The gate is a DASHBOARD gate. Blocking ingest on a missing card would make
    a customer silently lose audit evidence — the one thing they bought.

    D1: this posted to `/v1/logs`, which only serves GET, so it collected a 405
    and passed its `!= 402` assertion without ever reaching the ingest path. It
    now posts a real batch and asserts 202."""
    h = hashlib.sha256(str(uuid.uuid4()).encode()).hexdigest()
    r = client.post("/v1/logs/batch", headers=make_org()["auth"], json=[{
        "prompt_hash": h, "response_hash": h, "token_count": 8, "policy_tag": "test",
    }])
    assert r.status_code == 202, r.text


# ── §4.1 · $0 authorisation, not a charge ───────────────────────────────────

def test_card_setup_is_admin_only(make_org, add_user, login):
    org = make_org()
    add_user(org["org_id"], "member@test.dev", "memberpass1", role="member")
    c = login("member@test.dev", "memberpass1")
    assert c.post("/v1/billing/card-setup-session").status_code == 403


def test_card_setup_graceful_without_stripe(make_org, login):
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    r = c.post("/v1/billing/card-setup-session")
    assert r.status_code == 503, r.text        # never a 500, never a bogus URL


def test_card_setup_uses_setup_mode_and_no_line_items(make_org, login, monkeypatch):
    """The whole "captured, not charged" promise rests on mode='setup'. A session
    with line_items or any other mode would be a purchase."""
    org = make_org()
    monkeypatch.setattr(get_settings(), "stripe_secret_key", "sk_test_x")
    captured = {}

    import stripe

    def _create(**kw):
        captured.update(kw)
        return type("S", (), {"url": "https://checkout.stripe.test/cs_setup"})()

    monkeypatch.setattr(stripe.checkout.Session, "create", staticmethod(_create))
    c = login(org["admin_email"], org["admin_password"])
    r = c.post("/v1/billing/card-setup-session")
    assert r.status_code == 200, r.text
    assert r.json() == {"checkout_url": "https://checkout.stripe.test/cs_setup",
                        "mode": "setup", "amount": 0}
    assert captured["mode"] == "setup"
    assert "line_items" not in captured, "line_items would make this a charge"
    assert captured["metadata"]["foxy_org_id"] == str(org["org_id"])


# ── §4.2 · the webhook records a card without provisioning a plan ───────────

def test_setup_checkout_marks_card_without_touching_the_plan(make_org, client, monkeypatch):
    org = make_org()
    before = client.get("/v1/billing/plan", headers=org["auth"]).json()
    r = _send_event(client, monkeypatch, {
        "id": "evt_setup_1", "type": "checkout.session.completed",
        "data": {"object": {"mode": "setup", "customer": "cus_gate_1",
                            "metadata": {"foxy_org_id": str(org["org_id"]),
                                         "foxy_purpose": "card_on_file"}}}})
    assert r.status_code == 200 and r.json()["status"] == "card_on_file"
    after = client.get("/v1/billing/plan", headers=org["auth"]).json()
    assert after["plan_tier"] == before["plan_tier"], "a $0 setup must not upgrade anybody"
    assert after["subscription_status"] == before["subscription_status"]
    assert client.get("/v1/billing/access", headers=org["auth"]).json()["card_on_file"] is True


def test_setup_checkout_binds_the_stripe_customer(make_org, client, monkeypatch):
    """Without this, cancel and the billing portal have no customer to act on."""
    org = make_org()
    _send_event(client, monkeypatch, {
        "id": "evt_setup_2", "type": "checkout.session.completed",
        "data": {"object": {"mode": "setup", "customer": "cus_gate_2",
                            "metadata": {"foxy_org_id": str(org["org_id"])}}}})
    with SessionLocal() as db:
        assert db.get(Organization, uuid.UUID(org["org_id"])).stripe_customer_id == "cus_gate_2"


def test_setup_for_an_unknown_org_is_ignored_not_fatal(client, monkeypatch):
    r = _send_event(client, monkeypatch, {
        "id": "evt_setup_3", "type": "checkout.session.completed",
        "data": {"object": {"mode": "setup", "customer": "cus_nobody",
                            "metadata": {}}}})
    assert r.status_code == 200 and r.json()["status"] == "ignored"


def test_detaching_a_card_while_another_remains_keeps_you_unlocked(
        make_org, client, monkeypatch):
    """Swapping a card fires detached AFTER attached. Clearing the flag blindly
    would lock a paying customer out for replacing an expiring card."""
    org = make_org()
    _set_card(org["org_id"], True)
    with SessionLocal() as db:
        db.get(Organization, uuid.UUID(org["org_id"])).stripe_customer_id = "cus_swap"
        db.commit()

    monkeypatch.setattr(get_settings(), "stripe_secret_key", "sk_test_x")
    import stripe
    monkeypatch.setattr(stripe.PaymentMethod, "list",
                        staticmethod(lambda **kw: {"data": [{"id": "pm_other"}]}))
    r = _send_event(client, monkeypatch, {
        "id": "evt_detach_1", "type": "payment_method.detached",
        "data": {"object": {"customer": "cus_swap", "card": {"brand": "visa"}}}})
    assert r.json()["status"] == "unchanged"
    with SessionLocal() as db:
        assert db.get(Organization, uuid.UUID(org["org_id"])).card_on_file is True


def test_detaching_the_last_card_clears_the_flag(make_org, client, monkeypatch):
    org = make_org()
    _set_card(org["org_id"], True)
    with SessionLocal() as db:
        db.get(Organization, uuid.UUID(org["org_id"])).stripe_customer_id = "cus_last"
        db.commit()
    monkeypatch.setattr(get_settings(), "stripe_secret_key", "sk_test_x")
    import stripe
    monkeypatch.setattr(stripe.PaymentMethod, "list", staticmethod(lambda **kw: {"data": []}))
    r = _send_event(client, monkeypatch, {
        "id": "evt_detach_2", "type": "payment_method.detached",
        "data": {"object": {"customer": "cus_last"}}})
    assert r.json()["status"] == "card_removed"
    with SessionLocal() as db:
        assert db.get(Organization, uuid.UUID(org["org_id"])).card_on_file is False


def test_detach_fails_open_when_stripe_is_unreachable(make_org, client, monkeypatch):
    """Failing closed here would lock a customer out on a transient Stripe error.
    Failing open only risks showing an unlocked dashboard for a moment."""
    org = make_org()
    _set_card(org["org_id"], True)
    with SessionLocal() as db:
        db.get(Organization, uuid.UUID(org["org_id"])).stripe_customer_id = "cus_flaky"
        db.commit()
    monkeypatch.setattr(get_settings(), "stripe_secret_key", "sk_test_x")
    import stripe

    def _boom(**kw):
        raise RuntimeError("stripe is down")

    monkeypatch.setattr(stripe.PaymentMethod, "list", staticmethod(_boom))
    r = _send_event(client, monkeypatch, {
        "id": "evt_detach_3", "type": "payment_method.detached",
        "data": {"object": {"customer": "cus_flaky"}}})
    assert r.json()["status"] == "unchanged"
    with SessionLocal() as db:
        assert db.get(Organization, uuid.UUID(org["org_id"])).card_on_file is True


def test_card_columns_never_hold_anything_chargeable(make_org, client, monkeypatch):
    """We store a flag and a label. Not a payment-method id, not a token, not a PAN."""
    org = make_org()
    _send_event(client, monkeypatch, {
        "id": "evt_setup_4", "type": "checkout.session.completed",
        "data": {"object": {"mode": "setup", "customer": "cus_gate_4",
                            "setup_intent": "seti_secret_value",
                            "metadata": {"foxy_org_id": str(org["org_id"])}}}})
    with SessionLocal() as db:
        o = db.get(Organization, uuid.UUID(org["org_id"]))
        blob = f"{o.card_brand}{o.card_last4}"
        assert "seti_" not in blob and "pm_" not in blob


# ── §4.5 · cancellation you can actually reach ─────────────────────────────

def test_cancel_requires_step_up(make_org, login):
    org = make_org()
    c = login(org["admin_email"], org["admin_password"], with_step_up=False)
    r = c.post("/v1/billing/cancel")
    assert r.status_code == 403 and r.json()["detail"] == "step_up_required"


def test_cancel_requires_admin(make_org, add_user, login):
    org = make_org()
    add_user(org["org_id"], "member@test.dev", "memberpass1", role="member")
    c = login("member@test.dev", "memberpass1")
    assert c.post("/v1/billing/cancel").status_code == 403


def test_cancel_without_a_subscription_is_a_clear_400(make_org, login):
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    r = c.post("/v1/billing/cancel")
    assert r.status_code in (400, 503), r.text


def test_cancel_sets_cancel_at_period_end_not_immediate(make_org, login, monkeypatch):
    """Cancelling must not delete access the customer already paid for."""
    org = make_org()
    with SessionLocal() as db:
        o = db.get(Organization, uuid.UUID(org["org_id"]))
        o.stripe_customer_id, o.stripe_subscription_id = "cus_c", "sub_c"
        db.commit()
    monkeypatch.setattr(get_settings(), "stripe_secret_key", "sk_test_x")
    captured = {}
    import stripe

    def _modify(sub_id, **kw):
        captured.update({"id": sub_id, **kw})
        return {"current_period_end": 2000000000}

    monkeypatch.setattr(stripe.Subscription, "modify", staticmethod(_modify))
    c = login(org["admin_email"], org["admin_password"])
    r = c.post("/v1/billing/cancel")
    assert r.status_code == 200, r.text
    assert captured == {"id": "sub_c", "cancel_at_period_end": True}
    assert r.json()["cancel_at_period_end"] is True
    assert r.json()["access_until"].startswith("2033-")


# ── §4.4 · warning before anything changes ─────────────────────────────────

def _org_with_trial_ending(org_id, days_out: int):
    from datetime import datetime, timedelta, timezone
    with SessionLocal() as db:
        o = db.get(Organization, uuid.UUID(str(org_id)))
        o.trial_ends_at = datetime.now(timezone.utc) + timedelta(days=days_out)
        db.commit()
        return o.trial_ends_at.date().isoformat()


def test_trial_notice_lands_the_configured_number_of_days_early(make_org, monkeypatch):
    from app import user_notifications as un
    org = make_org()
    _org_with_trial_ending(org["org_id"], get_settings().billing_change_notice_days)
    sent = []
    monkeypatch.setattr(un.email_mod, "send_email", lambda **kw: (sent.append(kw), True)[1])
    with SessionLocal() as db:
        assert un.send_trial_ending_notices(db) >= 1
    body = sent[0]["html"] + (sent[0]["text"] or "")
    assert "will not be charged" in body
    assert "trial ends soon" in sent[0]["subject"].lower()


def test_trial_notice_is_not_sent_early_or_late(make_org, monkeypatch):
    from app import user_notifications as un
    org = make_org()
    _org_with_trial_ending(org["org_id"], get_settings().billing_change_notice_days + 3)
    monkeypatch.setattr(un.email_mod, "send_email", lambda **kw: True)
    with SessionLocal() as db:
        assert un.send_trial_ending_notices(db) == 0


def test_trial_notice_sends_once_however_often_the_sweep_runs(make_org, monkeypatch):
    from app import user_notifications as un
    org = make_org()
    _org_with_trial_ending(org["org_id"], get_settings().billing_change_notice_days)
    monkeypatch.setattr(un.email_mod, "send_email", lambda **kw: True)
    with SessionLocal() as db:
        assert un.send_trial_ending_notices(db) >= 1
        assert un.send_trial_ending_notices(db) == 0
        assert un.send_trial_ending_notices(db) == 0


def test_trial_notice_ignores_notification_preferences(make_org, monkeypatch):
    """A billing notice is not marketing. Somebody who muted product updates has
    not agreed to be surprised by a change in what they are charged."""
    from app import user_notifications as un
    from app.models import User
    from app.routers.account import _ALLOWED_PREFS
    from sqlalchemy import select
    org = make_org()
    _org_with_trial_ending(org["org_id"], get_settings().billing_change_notice_days)
    with SessionLocal() as db:
        u = db.execute(select(User).where(User.email == org["admin_email"])).scalar_one()
        u.preferences = {k: False for k in _ALLOWED_PREFS}
        db.commit()
    monkeypatch.setattr(un.email_mod, "send_email", lambda **kw: True)
    with SessionLocal() as db:
        assert un.send_trial_ending_notices(db) >= 1


def test_trial_notice_retries_when_every_email_failed(make_org, monkeypatch):
    from app import user_notifications as un
    org = make_org()
    _org_with_trial_ending(org["org_id"], get_settings().billing_change_notice_days)
    monkeypatch.setattr(un.email_mod, "send_email", lambda **kw: False)
    with SessionLocal() as db:
        assert un.send_trial_ending_notices(db) == 0
    monkeypatch.setattr(un.email_mod, "send_email", lambda **kw: True)
    with SessionLocal() as db:
        assert un.send_trial_ending_notices(db) >= 1
