"""The evaluation exit — register #36, phase E1.

An evaluation offer sets `plan_tier="premium"`, `trial_ends_at=NULL`,
`monthly_log_quota=NULL` and four evaluation fields. Before this, NOTHING ever
unset them, so once `evaluation_ends_at` passed, `logs.py` refused every capture
with 402 `evaluation_expired` and there was no tier to fall back to and no
upgrade that would release the org: the anonymous checkout carried no org
identity, so paying provisioned a SECOND workspace and left the original — with
all its audit evidence — locked forever.

Two halves are tested here, and the second is the one that matters:

1. the condition is routed through `billing_state` rather than raised inline, so
   `/v1/billing/access` can finally say WHICH thing is wrong;
2. upgrading actually releases the org.
"""

from __future__ import annotations

import hashlib
import types
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app import billing_state
from app.config import get_settings
from app.db import SessionLocal
from app.models import EvaluationRedemption, Organization


def _rows(count: int) -> list[dict]:
    return [
        {
            "prompt_hash": hashlib.sha256(f"{uuid.uuid4()}".encode()).hexdigest(),
            "response_hash": hashlib.sha256(f"r-{uuid.uuid4()}".encode()).hexdigest(),
            "token_count": 9,
            "policy_tag": "expiry_test",
            "agent": "expiry-demo",
        }
        for _ in range(count)
    ]


def _patch_org(org_id, **fields) -> None:
    db = SessionLocal()
    try:
        org = db.get(Organization, uuid.UUID(str(org_id)))
        for key, value in fields.items():
            setattr(org, key, value)
        db.commit()
    finally:
        db.close()


def _org_row(org_id) -> Organization:
    db = SessionLocal()
    try:
        return db.get(Organization, uuid.UUID(str(org_id)))
    finally:
        db.close()


def _grant(org_id, *, credits: int = 10, used: int = 0, days: float = 7.0) -> None:
    """Apply exactly what signup and redeem apply for a live evaluation offer."""
    _patch_org(
        org_id,
        plan_tier="premium", trial_ends_at=None, monthly_log_quota=None,
        evaluation_offer_id="expiry-test", evaluation_credit_limit=credits,
        evaluation_credits_used=used,
        evaluation_ends_at=datetime.now(timezone.utc) + timedelta(days=days),
    )


def _expire(org_id) -> None:
    _patch_org(org_id, evaluation_ends_at=datetime.now(timezone.utc) - timedelta(seconds=1))


# ── 1 · each state maps to its own reason ────────────────────────────────────

def test_a_live_offer_reports_nothing_and_locks_nothing(make_org, login):
    org = make_org()
    _grant(org["org_id"])
    body = login(org["admin_email"], org["admin_password"]).get(
        "/v1/billing/access").json()
    assert body["locked"] is False
    assert body["reason"] == billing_state.NONE
    assert body["capture_blocked"] is False


def test_an_expired_offer_reports_its_own_reason_and_locks(make_org, login):
    """The gap E1 closes: this org used to be refused at /v1/logs with a code
    /v1/billing/access had never heard of, so the dashboard could not explain it."""
    org = make_org()
    _grant(org["org_id"])
    _expire(org["org_id"])
    body = login(org["admin_email"], org["admin_password"]).get(
        "/v1/billing/access").json()
    assert body["reason"] == "evaluation_expired"
    assert body["locked"] is True, "the owner's decision is a lock"
    assert body["capture_blocked"] is True
    assert "upgrade" in body["message"].lower(), "a lock must name its remedy"


def test_credits_exhausted_reports_but_does_NOT_lock(make_org, login):
    """That org is inside a LIVE offer — the window is open, the allowance is
    simply spent — so it keeps reading its own evidence. Only expiry locks."""
    org = make_org()
    _grant(org["org_id"], credits=4, used=4)
    client = login(org["admin_email"], org["admin_password"])
    body = client.get("/v1/billing/access").json()
    assert body["reason"] == "evaluation_credits_exhausted"
    assert body["locked"] is False
    assert body["capture_blocked"] is True
    # Not locked is not a claim about a boolean: prove a non-exempt route still
    # answers. /v1/billing/* is gate-exempt, so it could never show this.
    assert client.get("/v1/usage").status_code == 200


def test_the_lock_does_not_lift_when_the_dashboard_gate_is_reached(make_org, login):
    """A locked org is 402'd on every route outside `auth._GATE_EXEMPT`, with the
    evaluation reason rather than a generic one."""
    org = make_org()
    _grant(org["org_id"])
    client = login(org["admin_email"], org["admin_password"])
    assert client.get("/v1/usage").status_code == 200
    _expire(org["org_id"])
    blocked = client.get("/v1/usage")
    assert blocked.status_code == 402
    assert blocked.json()["detail"]["code"] == "evaluation_expired"
    # …and the remedy stays reachable, or the lock would be a brick.
    assert client.get("/v1/billing/access").status_code == 200


# ── 2 · capture, before and after the window closes ──────────────────────────

def test_capture_is_refused_after_expiry(client, make_org):
    org = make_org()
    _grant(org["org_id"], credits=10)
    assert client.post("/v1/logs/batch", json=_rows(1),
                       headers=org["auth"]).status_code == 202
    _expire(org["org_id"])
    refused = client.post("/v1/logs/batch", json=_rows(1), headers=org["auth"])
    assert refused.status_code == 402
    assert refused.json()["detail"]["code"] == "evaluation_expired"


def test_expiry_is_reported_ahead_of_exhausted_credits(client, make_org):
    """An org can be in both states at once. The window closing is the fact that
    matters — buying credits would not help it."""
    org = make_org()
    _grant(org["org_id"], credits=2, used=2)
    _expire(org["org_id"])
    refused = client.post("/v1/logs/batch", json=_rows(1), headers=org["auth"])
    assert refused.json()["detail"]["code"] == "evaluation_expired"


def test_the_gate_order_in_capture_block_is_unchanged(make_org):
    """trial_expired → subscription_inactive → evaluation_expired →
    evaluation_credits_exhausted. Peeled one layer at a time, so a reordering
    cannot pass by accident."""
    now = datetime.now(timezone.utc)
    org = _org_row(make_org()["org_id"])
    org.plan_tier = "free"
    org.trial_ends_at = now - timedelta(days=1)
    org.subscription_status = "cancelled"
    org.evaluation_offer_id = "order-test"
    org.evaluation_credit_limit = 1
    org.evaluation_credits_used = 1
    org.evaluation_ends_at = now - timedelta(days=1)

    assert billing_state.capture_block(org, now).reason == "trial_expired"
    org.trial_ends_at = None
    org.plan_tier = "premium"                     # what a granted offer leaves behind
    assert billing_state.capture_block(org, now).reason == "subscription_inactive"
    org.subscription_status = None
    assert billing_state.capture_block(org, now).reason == "evaluation_expired"
    org.evaluation_ends_at = now + timedelta(days=1)
    assert billing_state.capture_block(org, now).reason == "evaluation_credits_exhausted"
    org.evaluation_credit_limit = 5
    assert billing_state.capture_block(org, now) is None


def test_a_batch_of_pure_duplicates_is_still_a_receipt_after_expiry(client, make_org):
    """`pending=0`. Every row already in the chain spends no credit, and an
    idempotent retry has always been answered with the original receipts."""
    org = make_org()
    _grant(org["org_id"], credits=10)
    batch = _rows(1)
    batch[0]["event_id"] = str(uuid.uuid4())
    assert client.post("/v1/logs/batch", json=batch,
                       headers=org["auth"]).status_code == 202
    _expire(org["org_id"])
    replay = client.post("/v1/logs/batch", json=batch, headers=org["auth"])
    assert replay.status_code == 202, replay.text
    assert replay.json()["receipts"][0]["status"] == "duplicate"


# ── 3 · the upgrade, which is the half that did not exist ────────────────────

def _checkout(session: dict) -> tuple[dict, str | None]:
    from app.routers.billing import _handle_checkout

    db = SessionLocal()
    try:
        result = _handle_checkout(db, session)
        db.commit()
        return result
    finally:
        db.close()


def test_an_org_that_upgrades_can_capture_again(client, make_org):
    """THE regression test. Everything else is scaffolding for this one.

    Before E1 the money changed nothing: the evaluation fields survived, so
    `logs.py` kept entering the evaluation branch and kept refusing.
    """
    org = make_org()
    _grant(org["org_id"], credits=10)
    _expire(org["org_id"])
    assert client.post("/v1/logs/batch", json=_rows(1),
                       headers=org["auth"]).status_code == 402

    result, upgraded_id = _checkout({
        "customer": "cus_upgrade", "customer_email": "who@cares.test",
        "subscription": "sub_upgrade",
        "metadata": {"foxy_org_id": org["org_id"], "foxy_plan": "max"},
    })
    assert result["status"] == "upgraded"
    assert upgraded_id == org["org_id"], "the SAME workspace, not a new one"

    row = _org_row(org["org_id"])
    assert row.evaluation_offer_id is None, "the field nothing used to clear"
    assert row.evaluation_ends_at is None
    assert row.evaluation_credit_limit is None
    assert row.evaluation_credits_used == 0
    assert row.plan_tier == "max"
    assert row.monthly_log_quota == get_settings().quota_for("max")
    assert row.stripe_customer_id == "cus_upgrade"
    assert row.stripe_subscription_id == "sub_upgrade"
    assert row.subscription_status == "active"

    accepted = client.post("/v1/logs/batch", json=_rows(1), headers=org["auth"])
    assert accepted.status_code == 202, accepted.text


def test_the_upgrade_releases_the_dashboard_too(make_org, login):
    org = make_org()
    _grant(org["org_id"])
    _expire(org["org_id"])
    client = login(org["admin_email"], org["admin_password"])
    assert client.get("/v1/billing/access").json()["locked"] is True

    _checkout({"customer": "cus_release", "subscription": "sub_release",
               "metadata": {"foxy_org_id": org["org_id"], "foxy_plan": "pro"}})

    body = client.get("/v1/billing/access").json()
    assert body["locked"] is False
    assert body["reason"] == billing_state.NONE
    assert body["capture_blocked"] is False
    assert client.get("/v1/usage").status_code == 200


def test_the_upgrade_never_creates_a_second_workspace(make_org):
    """The original defect. The customer paid and landed in an empty workspace
    because the customer-id lookup missed on an org that had never paid."""
    org = make_org()
    _grant(org["org_id"])
    _expire(org["org_id"])
    db = SessionLocal()
    try:
        before = db.execute(select(Organization.id)).scalars().all()
    finally:
        db.close()

    _checkout({"customer": "cus_no_fork", "customer_email": "someone@test.dev",
               "subscription": "sub_no_fork",
               "metadata": {"foxy_org_id": org["org_id"], "foxy_plan": "pro"}})

    db = SessionLocal()
    try:
        after = db.execute(select(Organization.id)).scalars().all()
    finally:
        db.close()
    assert sorted(str(i) for i in after) == sorted(str(i) for i in before)


def test_the_evaluation_redemption_row_survives_the_upgrade(make_org):
    """models.py puts a UNIQUE on org_id alone — one redemption per org, EVER.
    That row is the history; deleting it would silently re-arm a second offer."""
    org = make_org()
    _grant(org["org_id"])
    _expire(org["org_id"])
    db = SessionLocal()
    try:
        db.add(EvaluationRedemption(
            offer_id="expiry-test", org_id=uuid.UUID(org["org_id"]),
            email_hash=hashlib.sha256(b"judge@test.dev").hexdigest(),
            credits_granted=10,
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
        db.commit()
    finally:
        db.close()

    _checkout({"customer": "cus_history", "subscription": "sub_history",
               "metadata": {"foxy_org_id": org["org_id"], "foxy_plan": "pro"}})

    db = SessionLocal()
    try:
        rows = db.execute(select(EvaluationRedemption).where(
            EvaluationRedemption.org_id == uuid.UUID(org["org_id"]))).scalars().all()
    finally:
        db.close()
    assert len(rows) == 1 and rows[0].offer_id == "expiry-test"


def test_an_unknown_org_id_is_reported_not_provisioned(client):
    """A stale or hand-edited metadata value must never silently fall through to
    creating a workspace nobody asked for."""
    result, org_id = _checkout({
        "customer": "cus_ghost", "customer_email": "ghost@test.dev",
        "subscription": "sub_ghost",
        "metadata": {"foxy_org_id": str(uuid.uuid4()), "foxy_plan": "pro"},
    })
    assert result["status"] == "org_not_found" and org_id is None
    db = SessionLocal()
    try:
        assert db.execute(select(Organization).where(
            Organization.stripe_customer_id == "cus_ghost")).scalar_one_or_none() is None
    finally:
        db.close()


# ── 4 · the anonymous acquisition flow is untouched ──────────────────────────

def test_the_anonymous_flow_still_provisions_a_new_org(client):
    """The sale page uses this and it works. E1 is a second door, not a rewrite."""
    result, org_id = _checkout({
        "customer": "cus_anon_e1", "customer_email": "anon@startup.test",
        "subscription": "sub_anon_e1", "metadata": {"foxy_plan": "max"},
    })
    assert result["status"] == "provisioned" and org_id
    row = _org_row(org_id)
    assert row.contact_email == "anon@startup.test"
    assert row.plan_tier == "max"
    assert row.monthly_log_quota == get_settings().quota_for("max")
    assert row.evaluation_offer_id is None


def test_the_anonymous_checkout_session_still_sends_only_the_plan(client, monkeypatch):
    captured = {}

    def _create(**kw):
        captured.update(kw)
        return types.SimpleNamespace(url="https://checkout.stripe.test/cs_anon")

    monkeypatch.setitem(__import__("sys").modules, "stripe", types.SimpleNamespace(
        checkout=types.SimpleNamespace(Session=types.SimpleNamespace(create=_create))))
    monkeypatch.setattr(get_settings(), "stripe_secret_key", "sk_test_x")
    monkeypatch.setattr(get_settings(), "stripe_price_pro", "price_pro_x")

    r = client.post("/v1/billing/checkout-session",
                    json={"email": "buyer@co.test", "plan": "pro"})
    assert r.status_code == 200, r.text
    assert captured["metadata"] == {"foxy_plan": "pro"}
    assert "foxy_org_id" not in captured["metadata"]


# ── 5 · the authenticated door ───────────────────────────────────────────────

def _fake_stripe(monkeypatch, captured: dict):
    def _create(**kw):
        captured.update(kw)
        return types.SimpleNamespace(url="https://checkout.stripe.test/cs_upgrade")

    monkeypatch.setitem(__import__("sys").modules, "stripe", types.SimpleNamespace(
        checkout=types.SimpleNamespace(Session=types.SimpleNamespace(create=_create))))
    monkeypatch.setattr(get_settings(), "stripe_secret_key", "sk_test_x")
    monkeypatch.setattr(get_settings(), "stripe_price_pro", "price_pro_x")


def test_the_upgrade_session_carries_the_org(make_org, login, monkeypatch):
    """Without `foxy_org_id` the webhook has nothing to match on. This is the
    single field that turns 'provision' into 'upgrade'."""
    org = make_org()
    _grant(org["org_id"])
    _expire(org["org_id"])
    captured: dict = {}
    _fake_stripe(monkeypatch, captured)

    client = login(org["admin_email"], org["admin_password"])
    r = client.post("/v1/billing/upgrade-session", json={"plan": "pro"})
    assert r.status_code == 200, r.text
    assert r.json()["checkout_url"] == "https://checkout.stripe.test/cs_upgrade"
    assert captured["metadata"]["foxy_org_id"] == str(org["org_id"])
    assert captured["metadata"]["foxy_plan"] == "pro"
    assert captured["mode"] == "subscription"
    assert captured["line_items"][0]["price"] == "price_pro_x"


def test_the_upgrade_session_is_reachable_while_locked(make_org, login, monkeypatch):
    """A lock whose only remedy sits behind the lock is a brick. /v1/billing/ is
    in auth._GATE_EXEMPT and this route depends on staying there."""
    org = make_org()
    _grant(org["org_id"])
    _expire(org["org_id"])
    _fake_stripe(monkeypatch, {})
    client = login(org["admin_email"], org["admin_password"])
    assert client.get("/v1/usage").status_code == 402      # genuinely locked
    assert client.post("/v1/billing/upgrade-session",
                       json={"plan": "pro"}).status_code == 200


def test_the_upgrade_session_is_admin_only(make_org, add_user, login):
    org = make_org()
    add_user(org["org_id"], "member-e1@test.dev", "memberpass1", role="member")
    c = login("member-e1@test.dev", "memberpass1")
    assert c.post("/v1/billing/upgrade-session", json={"plan": "pro"}).status_code == 403


def test_the_upgrade_session_refuses_an_unconfigured_plan(make_org, login, monkeypatch):
    org = make_org()
    _fake_stripe(monkeypatch, {})
    c = login(org["admin_email"], org["admin_password"])
    assert c.post("/v1/billing/upgrade-session",
                  json={"plan": "not-a-plan"}).status_code == 422


def test_the_upgrade_session_is_graceful_without_stripe(make_org, login):
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    r = c.post("/v1/billing/upgrade-session", json={"plan": "pro"})
    assert r.status_code == 503, r.text        # never a 500, never a bogus URL


def test_the_upgrade_session_requires_authentication(client, monkeypatch):
    _fake_stripe(monkeypatch, {})
    assert client.post("/v1/billing/upgrade-session",
                       json={"plan": "pro"}).status_code == 401
