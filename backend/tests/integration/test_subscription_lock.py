"""The subscription lock, and what it deliberately does not touch (D1).

Before this there were two unrelated 402s and neither covered "payment pending":
the card gate in `auth.py` knew nothing about subscriptions, and the capture gate
in `logs.py` fired only for `{cancelled, unpaid}`. Neither could tell the
dashboard WHICH condition applied, so the UI had no way to render "add a card"
differently from "your last payment failed" — two problems with two different
fixes and, for the customer, two different things to go and do.

Most of what is asserted here is about the lock NOT costing anyone something
irrecoverable:

* capture keeps working while past_due, because evidence cannot be re-created
  afterwards and a declined card can be re-entered;
* a free-tier org is never touched by it, whatever its status column holds;
* the grace window is real, so one declined charge on a Tuesday is not a lockout;
* an org with no recorded start is never locked at all.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.config import get_settings
from app.db import SessionLocal
from app.models import Organization


def _set(org_id, **fields):
    with SessionLocal() as db:
        o = db.get(Organization, uuid.UUID(str(org_id)))
        for k, v in fields.items():
            setattr(o, k, v)
        db.commit()


def _days_ago(n: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=n)


def _ingest(client, org):
    """Capture one event the way the SDK does.

    `POST /v1/logs/batch`, not `/v1/logs` — the latter is a GET-only listing and
    posting to it returns 405, which quietly satisfies any assertion of the form
    `status_code != 402`. Two existing guards were written that way and could
    never have failed; both are corrected in this branch."""
    h = hashlib.sha256(f"{uuid.uuid4()}".encode()).hexdigest()
    return client.post("/v1/logs/batch", headers=org["auth"], json=[{
        "prompt_hash": h, "response_hash": h, "token_count": 8, "policy_tag": "test",
    }])


# ══ each state maps to the right reason ════════════════════════════════════

@pytest.mark.parametrize("status,expected", [
    ("active", "none"),
    ("incomplete", "subscription_incomplete"),
    ("incomplete_expired", "subscription_incomplete"),
    ("past_due", "subscription_past_due"),
    ("cancelled", "subscription_cancelled"),
])
def test_each_state_maps_to_its_own_reason(make_org, login, status, expected):
    """The whole point of D1. One 402 that means five things is a 402 the UI can
    only render as "something is wrong with billing", which is not an action."""
    org = make_org()
    _set(org["org_id"], plan_tier="pro", subscription_status=status,
         past_due_since=_days_ago(30) if status == "past_due" else None)
    c = login(org["admin_email"], org["admin_password"])
    assert c.get("/v1/billing/access").json()["reason"] == expected


def test_the_reason_and_the_402_speak_the_same_vocabulary(make_org, login):
    """One set of strings, so the dashboard needs one switch rather than two that
    drift apart. `/v1/billing/access` explains the 402; it cannot explain it in
    different words."""
    org = make_org()
    _set(org["org_id"], plan_tier="pro", subscription_status="incomplete")
    c = login(org["admin_email"], org["admin_password"])
    blocked = c.get("/v1/logs")
    assert blocked.status_code == 402
    assert blocked.json()["detail"]["code"] == c.get("/v1/billing/access").json()["reason"]


def test_incomplete_locks_immediately(make_org, login):
    """Nothing was ever paid, so there is nothing to extend grace on."""
    org = make_org()
    _set(org["org_id"], plan_tier="pro", subscription_status="incomplete")
    c = login(org["admin_email"], org["admin_password"])
    r = c.get("/v1/logs")
    assert r.status_code == 402
    assert r.json()["detail"]["code"] == "subscription_incomplete"
    d = c.get("/v1/billing/access").json()
    assert d["locked"] is True
    # P2 · was `d["grace_ends_at"] is None`. That field is gone (#48) because
    # nothing read it while the same date was already in `message`. The intent
    # is unchanged and now asserted at the surviving home: nothing was ever
    # paid, so there is no window and the sentence must not name a date.
    assert "Update it by" not in d["message"], (
        "an incomplete subscription was given a grace deadline")


def test_a_healthy_paid_org_is_untouched(make_org, login):
    org = make_org()
    _set(org["org_id"], plan_tier="pro", subscription_status="active")
    c = login(org["admin_email"], org["admin_password"])
    assert c.get("/v1/logs").status_code == 200
    assert c.get("/v1/billing/access").json()["locked"] is False


# ══ the grace window ═══════════════════════════════════════════════════════

def test_past_due_inside_the_grace_window_is_not_locked(make_org, login):
    """A bank declining once is not a customer who stopped paying. Stripe retries
    over days; locking on the first failure locks out people whose retry is about
    to succeed."""
    org = make_org()
    _set(org["org_id"], plan_tier="pro", subscription_status="past_due",
         past_due_since=_days_ago(2))
    c = login(org["admin_email"], org["admin_password"])
    assert c.get("/v1/logs").status_code == 200, "locked while Stripe was still retrying"
    d = c.get("/v1/billing/access").json()
    assert d["locked"] is False
    assert d["reason"] == "subscription_past_due", \
        "inside the window is still worth telling them about — that is what it is for"
    # P2 · this assertion's own message said what it was really protecting —
    # "the UI cannot say 'update it by' without a date" — so it now reads the
    # sentence the UI actually renders instead of a field the UI never read.
    assert re.search(r"Update it by \d{4}-\d{2}-\d{2} to keep your dashboard",
                     d["message"]), (
        f"the customer is not told when the window closes: {d['message']!r}")


def test_past_due_beyond_the_grace_window_locks(make_org, login):
    """The window is a delay, not an exemption."""
    org = make_org()
    _set(org["org_id"], plan_tier="pro", subscription_status="past_due",
         past_due_since=_days_ago(30))
    c = login(org["admin_email"], org["admin_password"])
    r = c.get("/v1/logs")
    assert r.status_code == 402
    assert r.json()["detail"]["code"] == "subscription_past_due"


def test_the_window_is_configuration_not_a_constant(make_org, login, monkeypatch):
    """Same org, same stamp, two windows, two answers."""
    org = make_org()
    _set(org["org_id"], plan_tier="pro", subscription_status="past_due",
         past_due_since=_days_ago(10))
    c = login(org["admin_email"], org["admin_password"])
    monkeypatch.setattr(get_settings(), "subscription_past_due_grace_days", 30)
    assert c.get("/v1/logs").status_code == 200
    monkeypatch.setattr(get_settings(), "subscription_past_due_grace_days", 3)
    assert c.get("/v1/logs").status_code == 402


def test_past_due_with_no_recorded_start_is_never_locked(make_org, login):
    """The fail-open direction, and the reason migration 0060 backfills nothing.

    Every row that predates `past_due_since` reads NULL, as does any status set
    by hand rather than by the webhook. Locking on missing data would mean
    deploying this column starts a countdown for customers whose failure date
    nobody actually knows — the same mistake the card gate's grandfather clause
    exists to prevent."""
    org = make_org()
    _set(org["org_id"], plan_tier="pro", subscription_status="past_due",
         past_due_since=None)
    c = login(org["admin_email"], org["admin_password"])
    assert c.get("/v1/logs").status_code == 200
    d = c.get("/v1/billing/access").json()
    assert d["locked"] is False
    # P2 · same swap. No recorded start means no window to name, and the
    # message falls back to the undated branch rather than inventing one.
    assert "Update it by" not in d["message"]
    assert "Update your payment method." in d["message"]
    assert d["reason"] == "subscription_past_due"


def test_the_shipped_grace_is_long_enough_to_outlast_a_retry(make_org):
    """Stripe's default retry schedule runs to about a week. A window shorter
    than that locks customers out mid-retry, which is the exact support ticket
    this window exists to avoid."""
    assert get_settings().subscription_past_due_grace_days >= 7


# ══ capture is not part of this ════════════════════════════════════════════

def test_capture_still_succeeds_while_past_due(make_org, client):
    """The one that must never regress. A customer cannot go back and re-create
    the model calls their agents made while a card was being fixed — but they can
    always re-enter the card. Evidence is the asymmetric loss, so capture keeps
    running even once the dashboard is locked."""
    org = make_org()
    _set(org["org_id"], plan_tier="pro", subscription_status="past_due",
         past_due_since=_days_ago(90))          # far beyond any window
    assert _ingest(client, org).status_code == 202


def test_capture_still_succeeds_while_incomplete(make_org, client):
    org = make_org()
    _set(org["org_id"], plan_tier="pro", subscription_status="incomplete")
    assert _ingest(client, org).status_code == 202


def test_cancelled_still_blocks_capture(make_org, client):
    """D1 must not accidentally widen the capture gate's escape hatch either."""
    org = make_org()
    _set(org["org_id"], plan_tier="pro", subscription_status="cancelled")
    r = _ingest(client, org)
    assert r.status_code == 402
    assert r.json()["detail"]["code"] == "subscription_inactive"


def test_cancelled_does_not_lock_the_dashboard(make_org, login):
    """Today's shipped behaviour, kept deliberately: someone who left can still
    read and export the evidence they already paid for. Leaving is not owing."""
    org = make_org()
    _set(org["org_id"], plan_tier="pro", subscription_status="cancelled")
    c = login(org["admin_email"], org["admin_password"])
    assert c.get("/v1/logs").status_code == 200
    d = c.get("/v1/billing/access").json()
    assert d["locked"] is False and d["capture_blocked"] is True


def test_access_separates_locked_from_capture_blocked(make_org, login):
    """They are different questions and they genuinely disagree in both
    directions — past_due locks the dashboard and keeps capturing, cancelled
    stops capturing and keeps the dashboard. One boolean cannot say that."""
    org = make_org()
    _set(org["org_id"], plan_tier="pro", subscription_status="past_due",
         past_due_since=_days_ago(30))
    c = login(org["admin_email"], org["admin_password"])
    d = c.get("/v1/billing/access").json()
    assert d["locked"] is True and d["capture_blocked"] is False


# ══ who this can never touch ═══════════════════════════════════════════════

@pytest.mark.parametrize("tier", [None, "", "free"])
@pytest.mark.parametrize("status", ["past_due", "incomplete", "cancelled"])
def test_a_free_tier_org_is_never_locked_by_the_subscription_gate(
        make_org, login, tier, status):
    """A free org has no subscription to fail. Whatever is in that column is
    left over from something else, and it must not cost them their dashboard."""
    org = make_org()
    _set(org["org_id"], plan_tier=tier, subscription_status=status,
         past_due_since=_days_ago(90))
    c = login(org["admin_email"], org["admin_password"])
    assert c.get("/v1/logs").status_code == 200
    assert c.get("/v1/billing/access").json()["locked"] is False


def test_sdk_ingest_is_never_subscription_locked(make_org, client):
    """The gate is a DASHBOARD gate. The SDK authenticates with an API key and
    never passes through it — same guarantee the card gate already makes."""
    org = make_org()
    _set(org["org_id"], plan_tier="pro", subscription_status="incomplete")
    assert _ingest(client, org).status_code == 202


def test_the_lock_never_traps_you(make_org, login):
    """You must be able to sign out, read the reason, and reach billing to fix
    the payment that locked you. Lock any of those and the product is bricked
    with no way back — and unlike the card gate, this one is on by default."""
    org = make_org()
    _set(org["org_id"], plan_tier="pro", subscription_status="incomplete")
    c = login(org["admin_email"], org["admin_password"])
    assert c.get("/v1/auth/me").status_code == 200
    assert c.get("/v1/billing/access").status_code == 200
    assert c.get("/v1/billing/plan").status_code == 200
    assert c.post("/v1/auth/logout").status_code == 200


def test_the_kill_switch_works(make_org, login, monkeypatch):
    """A feature that locks paying customers out needs an off switch that does
    not require a deploy."""
    org = make_org()
    _set(org["org_id"], plan_tier="pro", subscription_status="incomplete")
    c = login(org["admin_email"], org["admin_password"])
    assert c.get("/v1/logs").status_code == 402
    monkeypatch.setattr(get_settings(), "subscription_lock_enabled", False)
    assert c.get("/v1/logs").status_code == 200
    assert c.get("/v1/billing/access").json()["reason"] == "none"


# ══ the card gate is a separate decision ═══════════════════════════════════

def test_the_card_gate_is_still_off_by_default(make_org, login):
    """D1 adds a lock that IS on by default. That must not have leaked into the
    card gate, whose input is legacy data and which stays the owner's call."""
    assert get_settings().require_card_on_file is False
    org = make_org()
    _set(org["org_id"], plan_tier="pro", subscription_status="active")
    c = login(org["admin_email"], org["admin_password"])
    assert c.get("/v1/logs").status_code == 200


def test_the_grandfather_clause_still_exempts_old_orgs_from_the_card_gate(
        make_org, login, monkeypatch):
    """Moving `_grandfathered` into billing_state must not have changed what it
    decides. An org created before the cutoff keeps its dashboard with the card
    flag on — the guarantee that makes the flag flippable at all."""
    monkeypatch.setattr(get_settings(), "require_card_on_file", True)
    monkeypatch.setattr(get_settings(), "card_gate_grandfather_before",
                        "2026-07-29T00:00:00+00:00")
    org = make_org()
    _set(org["org_id"], created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
         card_on_file=False)
    c = login(org["admin_email"], org["admin_password"])
    assert c.get("/v1/logs").status_code == 200, \
        "an existing org was locked out by the card gate"


def test_a_failed_payment_is_reported_ahead_of_a_missing_card(
        make_org, login, monkeypatch):
    """An org in both states is told the thing it must actually go and do. The
    card it would be asked for is very likely the one that just declined."""
    monkeypatch.setattr(get_settings(), "require_card_on_file", True)
    monkeypatch.setattr(get_settings(), "card_gate_grandfather_before",
                        "2020-01-01T00:00:00+00:00")
    org = make_org()
    _set(org["org_id"], plan_tier="pro", subscription_status="incomplete",
         card_on_file=False)
    c = login(org["admin_email"], org["admin_password"])
    r = c.get("/v1/logs")
    assert r.status_code == 402
    assert r.json()["detail"]["code"] == "subscription_incomplete"


# ══ the stamp the window is measured from ══════════════════════════════════

def _signed(payload: str, secret: str) -> str:
    ts = int(time.time())
    sig = hmac.new(secret.encode(), f"{ts}.{payload}".encode(), hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


def _subscription_event(client, monkeypatch, customer: str, status: str):
    secret = "test-webhook-secret"
    monkeypatch.setattr(get_settings(), "stripe_webhook_secret", secret)
    body = json.dumps({
        "object": "event", "id": f"evt_{uuid.uuid4().hex}",
        "type": "customer.subscription.updated",
        "data": {"object": {"customer": customer, "status": status}},
    })
    return client.post("/v1/webhooks/stripe", content=body,
                       headers={"stripe-signature": _signed(body, secret),
                                "content-type": "application/json"})


def _past_due_since(org_id):
    with SessionLocal() as db:
        return db.get(Organization, uuid.UUID(str(org_id))).past_due_since


def test_the_webhook_stamps_when_the_failure_started(make_org, client, monkeypatch):
    org = make_org()
    _set(org["org_id"], plan_tier="pro", stripe_customer_id="cus_d1_stamp")
    assert _subscription_event(client, monkeypatch, "cus_d1_stamp", "past_due").status_code == 200
    assert _past_due_since(org["org_id"]) is not None


def test_a_retry_does_not_restart_the_clock(make_org, client, monkeypatch):
    """Stripe emits `customer.subscription.updated` repeatedly while it works
    through its retry schedule. Re-stamping on each one would restart the window
    every time, so it would never expire and the lock would never fire."""
    org = make_org()
    _set(org["org_id"], plan_tier="pro", stripe_customer_id="cus_d1_retry",
         subscription_status="past_due", past_due_since=_days_ago(5))
    first = _past_due_since(org["org_id"])
    assert _subscription_event(client, monkeypatch, "cus_d1_retry", "past_due").status_code == 200
    assert _past_due_since(org["org_id"]) == first


def test_recovering_clears_the_clock(make_org, client, monkeypatch, login):
    """The charge went through. They are a customer in good standing again, and
    a stale stamp would lock them the moment the next payment slipped."""
    org = make_org()
    _set(org["org_id"], plan_tier="pro", stripe_customer_id="cus_d1_recover",
         subscription_status="past_due", past_due_since=_days_ago(90))
    c = login(org["admin_email"], org["admin_password"])
    assert c.get("/v1/logs").status_code == 402
    assert _subscription_event(client, monkeypatch, "cus_d1_recover", "active").status_code == 200
    assert _past_due_since(org["org_id"]) is None
    assert c.get("/v1/logs").status_code == 200


def test_stripe_unpaid_is_stored_past_due_and_still_stamped(make_org, client, monkeypatch):
    """Stripe's `unpaid` means the retries are exhausted — strictly worse than
    past_due — but it has always been stored AS past_due, so it inherits the
    window rather than locking instantly. That is acceptable only because an org
    Stripe has given up on has been failing for weeks and is far beyond it; this
    asserts the stamp exists so the window can expire at all."""
    org = make_org()
    _set(org["org_id"], plan_tier="pro", stripe_customer_id="cus_d1_unpaid")
    assert _subscription_event(client, monkeypatch, "cus_d1_unpaid", "unpaid").status_code == 200
    with SessionLocal() as db:
        row = db.get(Organization, uuid.UUID(str(org["org_id"])))
        assert row.subscription_status == "past_due"
        assert row.past_due_since is not None
