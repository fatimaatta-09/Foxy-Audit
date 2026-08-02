"""The grandfather clause (P3 §4).

`require_card_on_file` has shipped OFF since the gate was built, for one reason:
`card_on_file` is false on every row written before that column existed, so
turning the flag on locked out every customer the product already had — all at
once, for a card none of them were ever asked for. That made the flag
undeployable, which made the feature theoretical.

This is what makes it safe to turn on. The test that matters is
`test_flipping_the_flag_does_not_lock_out_an_existing_org`: it creates a
pre-cutoff org, flips the flag ON inside the test, and asserts the org still
reaches the dashboard.

The flag itself stays False. That is the owner's decision and nothing here
changes it — these tests flip it locally to prove what WOULD happen.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.config import get_settings
from app.db import SessionLocal
from app.models import Organization

CUTOFF = "2026-07-29T00:00:00+00:00"


@pytest.fixture
def gate_on(monkeypatch):
    monkeypatch.setattr(get_settings(), "require_card_on_file", True)
    monkeypatch.setattr(get_settings(), "card_gate_grandfather_before", CUTOFF)


def _stamp(org_id, *, created=None, subscription=None, card=False):
    with SessionLocal() as db:
        row = db.get(Organization, uuid.UUID(str(org_id)))
        if created is not None:
            row.created_at = created
        if subscription is not None:
            row.stripe_subscription_id = subscription
        row.card_on_file = card
        db.commit()


def _before_cutoff():
    return datetime.fromisoformat(CUTOFF) - timedelta(days=30)


def _after_cutoff():
    return datetime.fromisoformat(CUTOFF) + timedelta(days=30)


# ══ THE ONE THAT MATTERS ═══════════════════════════════════════════════════

def test_flipping_the_flag_does_not_lock_out_an_existing_org(make_org, login, gate_on):
    """An org that existed before the gate was built keeps its dashboard when the
    flag is turned on. Without this the flag can never be enabled at all."""
    org = make_org()
    _stamp(org["org_id"], created=_before_cutoff(), card=False)
    c = login(org["admin_email"], org["admin_password"])
    assert c.get("/v1/auth/me").status_code == 200
    assert c.get("/v1/logs").status_code == 200, \
        "an existing org was locked out by the card gate"


def test_the_gate_still_bites_for_orgs_created_after_the_cutoff(make_org, login, gate_on):
    """The exemption must not quietly disable the feature it protects."""
    org = make_org()
    _stamp(org["org_id"], created=_after_cutoff(), card=False)
    c = login(org["admin_email"], org["admin_password"])
    r = c.get("/v1/logs")
    assert r.status_code == 402
    assert r.json()["detail"]["code"] == "card_required"


def test_a_post_cutoff_org_with_a_card_is_fine(make_org, login, gate_on):
    org = make_org()
    _stamp(org["org_id"], created=_after_cutoff(), card=True)
    c = login(org["admin_email"], org["admin_password"])
    assert c.get("/v1/logs").status_code == 200


# ══ the subscription exemption ═════════════════════════════════════════════

def test_a_paying_customer_is_never_gated(make_org, login, gate_on):
    """`card_on_file` only tracks the newer $0-authorisation flow, so it
    under-reports anyone who subscribed through Checkout. A customer with a live
    subscription demonstrably has a card at Stripe — locking them out over a flag
    that post-dates their payment would be absurd."""
    org = make_org()
    _stamp(org["org_id"], created=_after_cutoff(), subscription="sub_live_123", card=False)
    c = login(org["admin_email"], org["admin_password"])
    assert c.get("/v1/logs").status_code == 200


# ══ the cutoff is configuration, not a constant ════════════════════════════

def test_the_cutoff_is_configurable(make_org, login, monkeypatch):
    """Moving it forward brings more orgs into scope; that is the control the
    owner has. Same org, two cutoffs, two answers."""
    org = make_org()
    created = datetime(2026, 7, 20, tzinfo=timezone.utc)
    _stamp(org["org_id"], created=created, card=False)
    monkeypatch.setattr(get_settings(), "require_card_on_file", True)

    monkeypatch.setattr(get_settings(), "card_gate_grandfather_before",
                        "2026-07-25T00:00:00+00:00")          # after it was created
    c = login(org["admin_email"], org["admin_password"])
    assert c.get("/v1/logs").status_code == 200

    monkeypatch.setattr(get_settings(), "card_gate_grandfather_before",
                        "2026-07-10T00:00:00+00:00")          # before it was created
    assert c.get("/v1/logs").status_code == 402


def test_no_cutoff_exempts_everyone(make_org, login, monkeypatch):
    """The fail-safe direction. With no cutoff chosen, nobody has decided which
    orgs are new enough to gate — so the gate exempts everyone rather than guess.
    Turning the gate on is therefore TWO decisions, and doing only one of them is
    a no-op instead of an outage."""
    org = make_org()
    _stamp(org["org_id"], created=_after_cutoff(), card=False)
    monkeypatch.setattr(get_settings(), "require_card_on_file", True)
    monkeypatch.setattr(get_settings(), "card_gate_grandfather_before", "")
    c = login(org["admin_email"], org["admin_password"])
    assert c.get("/v1/logs").status_code == 200


def test_a_malformed_cutoff_fails_open_rather_than_locking_everyone_out(
        make_org, login, monkeypatch):
    """A typo in configuration must not be a mass lockout. Failing open here
    risks an ungated dashboard; failing closed risks every customer at once."""
    org = make_org()
    _stamp(org["org_id"], created=_after_cutoff(), card=False)
    monkeypatch.setattr(get_settings(), "require_card_on_file", True)
    monkeypatch.setattr(get_settings(), "card_gate_grandfather_before", "not-a-date")
    c = login(org["admin_email"], org["admin_password"])
    assert c.get("/v1/logs").status_code == 200


def test_the_shipped_default_locks_nobody_out(make_org, login, monkeypatch):
    """Flipping ONLY the flag, with config exactly as shipped, must not gate a
    single org. This is the guarantee the whole clause exists to provide.

    Deliberately no hardcoded ship date: a date baked into config is wrong the
    moment it passes, and an org created an hour after midnight on that date
    would be gated — the exact lockout this prevents. The first version of this
    defaulted to the ship date and this test caught it."""
    assert get_settings().card_gate_grandfather_before == "", \
        "a cutoff was baked into shipped config; it will expire and start gating people"
    org = make_org()                       # created_at = now, from the DB default
    monkeypatch.setattr(get_settings(), "require_card_on_file", True)
    c = login(org["admin_email"], org["admin_password"])
    assert c.get("/v1/auth/me").status_code == 200
    assert c.get("/v1/logs").status_code == 200


# ══ the parts that must not have moved ═════════════════════════════════════

def test_the_flag_is_still_off(make_org):
    """The owner's decision. If this fails, someone enabled the gate in config."""
    assert get_settings().require_card_on_file is False


def test_sdk_ingest_is_still_never_gated(make_org, client, gate_on):
    """Blocking ingest makes a paying customer lose the evidence they bought.

    D1: this posted to `/v1/logs` — a GET-only listing — and asserted only
    `!= 402`. Every run got 405 and passed, so the guarantee it names was never
    once tested. It now posts a real batch and asserts the success status, which
    405 cannot satisfy."""
    org = make_org()
    _stamp(org["org_id"], created=_after_cutoff(), card=False)
    h = hashlib.sha256(str(uuid.uuid4()).encode()).hexdigest()
    r = client.post("/v1/logs/batch", headers=org["auth"], json=[{
        "prompt_hash": h, "response_hash": h, "token_count": 8, "policy_tag": "test",
    }])
    assert r.status_code == 202, r.text


def test_a_gated_org_can_still_reach_billing_and_auth(make_org, login, gate_on):
    """Lock somebody out of billing and they cannot add the card that unlocks
    them; lock them out of auth and they cannot even sign out."""
    org = make_org()
    _stamp(org["org_id"], created=_after_cutoff(), card=False)
    c = login(org["admin_email"], org["admin_password"])
    assert c.get("/v1/auth/me").status_code == 200
    assert c.get("/v1/billing/access").status_code == 200
    assert c.post("/v1/auth/logout").status_code == 200
