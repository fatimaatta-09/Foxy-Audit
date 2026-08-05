"""Paddle checkout + webhook — M2.

The money path. Everything here drives the real routes; nothing inspects a
helper's body, because a helper nobody calls is exactly as broken as no helper
(A6's lesson) and the defect always lives at the call site.

Assertions say what they WANT (`== 200`, `== 202`), never what they fear
(`!= 402`): a guard phrased as `!= failure` is satisfied by a 405 from a route
that does not exist, which is how two "SDK ingest is never gated" guards ran
green for months without reaching the code they named.

Paddle is stubbed at ONE seam — `paddle._request`, the single function that does
network I/O — so every layer above it, including signature verification, the
durable log and the idempotency, is the real code.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app import billing_state, paddle
from app.config import get_settings
from app.db import SessionLocal
from app.models import ApiKey, Organization, PaymentEvent, User

SECRET = "pdl_ntfset_test_secret_m2"
PRICE_PRO = "pri_test_pro_m2"
PRICE_MAX = "pri_test_max_m2"


# ── helpers ──────────────────────────────────────────────────────────────────

def _sign(body: bytes, secret: str = SECRET, ts: int | None = None) -> str:
    """Build a real Paddle-Signature header: ts=<unix>;h1=<hmac of "ts:body">."""
    ts = int(time.time()) if ts is None else ts
    mac = hmac.new(secret.encode(), f"{ts}:".encode() + body, hashlib.sha256).hexdigest()
    return f"ts={ts};h1={mac}"


def _envelope(event_type: str, data: dict, event_id: str | None = None) -> bytes:
    return json.dumps({
        "event_id": event_id or f"evt_{uuid.uuid4().hex[:24]}",
        "event_type": event_type,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "notification_id": f"ntf_{uuid.uuid4().hex[:24]}",
        "data": data,
    }).encode()


def _txn_completed(org_id: str, price_id: str = PRICE_PRO, *,
                   customer="ctm_m2", subscription="sub_m2") -> dict:
    """A transaction.completed data object, shaped as Paddle documents it."""
    return {
        "id": "txn_m2_" + uuid.uuid4().hex[:12],
        "status": "completed",
        "customer_id": customer,
        "subscription_id": subscription,
        "custom_data": {"foxy_org_id": str(org_id), "foxy_plan": "pro"},
        "items": [{"price": {"id": price_id}, "quantity": 1}],
    }


def _sub(org_id: str | None, status: str, *, sub_id="sub_m2",
         customer="ctm_m2") -> dict:
    data = {"id": sub_id, "status": status, "customer_id": customer}
    if org_id:
        data["custom_data"] = {"foxy_org_id": str(org_id)}
    return data


def _configure(monkeypatch, *, api_key="sdbx_test_key", secret=SECRET) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "paddle_api_key", api_key)
    monkeypatch.setattr(s, "paddle_webhook_secret", secret)
    monkeypatch.setattr(s, "paddle_price_pro", PRICE_PRO)
    monkeypatch.setattr(s, "paddle_price_max", PRICE_MAX)


def _post(client, body: bytes, header: str | None = None):
    return client.post("/v1/webhooks/paddle", content=body,
                       headers={"paddle-signature": header if header is not None
                                else _sign(body),
                                "content-type": "application/json"})


def _org_row(org_id) -> Organization:
    db = SessionLocal()
    try:
        return db.get(Organization, uuid.UUID(str(org_id)))
    finally:
        db.close()


def _patch_org(org_id, **fields) -> None:
    db = SessionLocal()
    try:
        org = db.get(Organization, uuid.UUID(str(org_id)))
        for k, v in fields.items():
            setattr(org, k, v)
        db.commit()
    finally:
        db.close()


def _grant_expired_evaluation(org_id) -> None:
    """Exactly what signup/redeem apply, then expired — the org most likely to
    be the first paying customer, and the one register #36 was about."""
    _patch_org(org_id, plan_tier="premium", trial_ends_at=None, monthly_log_quota=None,
               evaluation_offer_id="m2-test", evaluation_credit_limit=10,
               evaluation_credits_used=10,
               evaluation_ends_at=datetime.now(timezone.utc) - timedelta(seconds=1))


def _rows(n: int = 1) -> list[dict]:
    return [{
        "prompt_hash": hashlib.sha256(f"{uuid.uuid4()}".encode()).hexdigest(),
        "response_hash": hashlib.sha256(f"r{uuid.uuid4()}".encode()).hexdigest(),
        "token_count": 5, "policy_tag": "m2", "agent": "paddle-demo",
    } for _ in range(n)]


def _events(provider_event_id: str | None = None) -> list[PaymentEvent]:
    db = SessionLocal()
    try:
        q = select(PaymentEvent)
        if provider_event_id:
            q = q.where(PaymentEvent.provider_event_id == provider_event_id)
        return list(db.execute(q).scalars().all())
    finally:
        db.close()


# ── 1 · signature verification ───────────────────────────────────────────────

def test_a_valid_signature_is_accepted(client, monkeypatch):
    _configure(monkeypatch)
    body = _envelope("ping", {})
    assert _post(client, body).status_code == 200


def test_the_wrong_secret_is_rejected(client, monkeypatch):
    _configure(monkeypatch)
    body = _envelope("ping", {})
    r = _post(client, body, _sign(body, secret="pdl_ntfset_not_it"))
    assert r.status_code == 400
    assert _events() == [], "an unverified body must never reach the database"


def test_a_tampered_body_is_rejected(client, monkeypatch):
    """The signature covers the body. Sign one payload, send another."""
    _configure(monkeypatch)
    signed = _envelope("ping", {})
    header = _sign(signed)
    tampered = signed.replace(b'"ping"', b'"transaction.completed"')
    assert tampered != signed
    assert _post(client, tampered, header).status_code == 400


def test_the_signature_is_computed_over_the_raw_bytes(client, monkeypatch):
    """Re-serialising parsed JSON reorders keys and respaces the body, and the
    digest never matches again. This proves we verify what arrived, not what a
    JSON round-trip produced — the failure would look exactly like an attack."""
    _configure(monkeypatch)
    body = b'{"event_id":"evt_raw_m2",  "event_type":"ping" , "data":{}}'
    reserialised = json.dumps(json.loads(body)).encode()
    assert reserialised != body, "the fixture must actually differ after a round trip"
    assert _post(client, body, _sign(body)).status_code == 200
    assert _post(client, reserialised, _sign(body)).status_code == 400


def test_a_malformed_signature_header_is_rejected(client, monkeypatch):
    _configure(monkeypatch)
    body = _envelope("ping", {})
    for header in ("", "garbage", "ts=;h1=", "h1=abc", "ts=123",
                   "ts=notanumber;h1=abc"):
        assert _post(client, body, header).status_code == 400, header


def test_a_stale_timestamp_is_rejected(client, monkeypatch):
    """Bounds replay of a captured-but-valid request. The window is 300s, so
    something well outside it must fail even though the digest is correct."""
    _configure(monkeypatch)
    body = _envelope("ping", {})
    stale = int(time.time()) - 3600
    assert _post(client, body, _sign(body, ts=stale)).status_code == 400


def test_a_far_future_timestamp_is_rejected(client, monkeypatch):
    """The window is absolute, not one-sided — a clock ahead is as wrong as one
    behind, and a one-sided check is trivially bypassed by sending a future ts."""
    _configure(monkeypatch)
    body = _envelope("ping", {})
    assert _post(client, body, _sign(body, ts=int(time.time()) + 3600)).status_code == 400


def test_a_timestamp_inside_the_window_is_accepted(client, monkeypatch):
    _configure(monkeypatch)
    body = _envelope("ping", {})
    assert _post(client, body, _sign(body, ts=int(time.time()) - 120)).status_code == 200


def test_the_signature_comparison_is_constant_time():
    """Not a style preference: there is a published advisory against a Paddle
    library for using `==` here (GHSA-mjgf-xj26-9qf9). A byte-at-a-time compare
    leaks the expected digest through timing and lets a signature be forged one
    nibble at a time. Asserted on the source because timing cannot be measured
    reliably in CI."""
    import inspect
    source = inspect.getsource(paddle.verify_signature)
    assert "compare_digest" in source
    body = source.split('"""')[-1]          # past the docstring, which mentions ==
    assert "==" not in body.replace("!=", ""), "digest comparison must not use =="


def test_an_unconfigured_deployment_reports_503_not_400(client, monkeypatch):
    """503 and 400 are different faults. "We cannot check" must not read as
    "you are an impostor" — an operator debugging a missing secret would chase
    the wrong thing entirely."""
    monkeypatch.setattr(get_settings(), "paddle_webhook_secret", "")
    body = _envelope("ping", {})
    assert _post(client, body, _sign(body)).status_code == 503


# ── 2 · the durable log and its idempotency ──────────────────────────────────

def test_a_verified_event_is_logged_before_it_is_dispatched(client, monkeypatch):
    _configure(monkeypatch)
    body = _envelope("ping", {}, event_id="evt_logged_m2")
    assert _post(client, body).status_code == 200
    rows = _events("evt_logged_m2")
    assert len(rows) == 1
    assert rows[0].provider == "paddle"
    assert rows[0].type == "ping"
    assert rows[0].status == "ignored", "an event we do not act on is ignored, not failed"
    assert rows[0].payload["event_id"] == "evt_logged_m2"


def test_the_same_event_twice_is_a_no_op(client, make_org, monkeypatch):
    """THE replay guard, and the one the owner asked to see.

    Paddle guarantees at-least-once delivery and retries on exponential backoff
    whenever this endpoint does not answer 200 within five seconds, so a repeat
    is normal traffic, not an attack. The second delivery must change nothing.
    """
    _configure(monkeypatch)
    org = make_org()
    _patch_org(org["org_id"], plan_tier="free")
    body = _envelope("transaction.completed",
                     _txn_completed(org["org_id"]), event_id="evt_replay_m2")
    header = _sign(body)

    first = client.post("/v1/webhooks/paddle", content=body,
                        headers={"paddle-signature": header})
    assert first.status_code == 200
    assert first.json()["status"] == "upgraded"

    second = client.post("/v1/webhooks/paddle", content=body,
                         headers={"paddle-signature": header})
    assert second.status_code == 200
    assert second.json()["status"] == "duplicate", "the second delivery re-ran the work"

    assert len(_events("evt_replay_m2")) == 1, "a retry must not write a second row"
    assert len(_events()) == 1


def test_two_different_events_both_land(client, monkeypatch):
    """The dedupe key must be the event id, not the event TYPE — otherwise a
    second, genuinely different event of the same type would be dropped."""
    _configure(monkeypatch)
    assert _post(client, _envelope("ping", {}, event_id="evt_a_m2")).status_code == 200
    assert _post(client, _envelope("ping", {}, event_id="evt_b_m2")).status_code == 200
    assert len(_events()) == 2


def test_an_envelope_without_an_event_id_is_refused(client, monkeypatch):
    """No id means no idempotency key, and storing it would make every retry a
    fresh row. Refuse rather than invent one."""
    _configure(monkeypatch)
    body = json.dumps({"event_type": "ping", "data": {}}).encode()
    assert _post(client, body, _sign(body)).status_code == 400
    assert _events() == []


# ── 3 · a purchase upgrades the right workspace ──────────────────────────────

def test_a_purchase_upgrades_that_org_and_never_creates_a_second(
        client, make_org, monkeypatch):
    """Register #36. Stripe's anonymous checkout carried no org identity, so the
    webhook missed the customer lookup and provisioned a SECOND workspace — the
    customer paid and landed somewhere empty while the original stayed locked.
    `custom_data.foxy_org_id` is what stops that here."""
    _configure(monkeypatch)
    org = make_org()
    _patch_org(org["org_id"], plan_tier="free")
    db = SessionLocal()
    try:
        before = {str(i) for i in db.execute(select(Organization.id)).scalars().all()}
    finally:
        db.close()

    r = _post(client, _envelope("transaction.completed", _txn_completed(org["org_id"])))
    assert r.status_code == 200 and r.json()["status"] == "upgraded"

    db = SessionLocal()
    try:
        after = {str(i) for i in db.execute(select(Organization.id)).scalars().all()}
    finally:
        db.close()
    assert after == before, "a purchase must never fork a second workspace"

    row = _org_row(org["org_id"])
    assert row.plan_tier == "pro"
    assert row.monthly_log_quota == get_settings().quota_for("pro")
    assert row.subscription_status == "active"
    assert row.past_due_since is None
    assert row.paddle_customer_id == "ctm_m2"
    assert row.paddle_subscription_id == "sub_m2"


def test_a_paying_expired_evaluator_is_actually_released(client, make_org, login,
                                                         monkeypatch):
    """THE end-to-end question, on real rows: an expired evaluator pays and the
    workspace has to actually open. Both gates are asserted, because the
    dashboard lock and the capture block are different sets."""
    _configure(monkeypatch)
    org = make_org()
    _grant_expired_evaluation(org["org_id"])

    customer = login(org["admin_email"], org["admin_password"])
    assert customer.get("/v1/usage").status_code == 402
    refused = client.post("/v1/logs/batch", json=_rows(), headers=org["auth"])
    assert refused.status_code == 402
    assert refused.json()["detail"]["code"] == "evaluation_expired"

    r = _post(client, _envelope("transaction.completed", _txn_completed(org["org_id"])))
    assert r.status_code == 200 and r.json()["status"] == "upgraded"

    row = _org_row(org["org_id"])
    assert row.evaluation_offer_id is None
    assert row.evaluation_credit_limit is None
    assert row.evaluation_credits_used == 0
    assert row.evaluation_ends_at is None

    assert customer.get("/v1/usage").status_code == 200, "the dashboard must open"
    accepted = client.post("/v1/logs/batch", json=_rows(), headers=org["auth"])
    assert accepted.status_code == 202, accepted.text
    assert customer.get("/v1/billing/access").json()["reason"] == billing_state.NONE


def test_the_tier_comes_from_the_price_not_from_custom_data(
        client, make_org, monkeypatch):
    """custom_data is something we put on a checkout; the price id is what Paddle
    actually charged for. Resolving from the price means nobody can talk us into
    a tier they did not pay for by editing the cheaper half of the payload."""
    _configure(monkeypatch)
    org = make_org()
    data = _txn_completed(org["org_id"], price_id=PRICE_MAX)
    data["custom_data"]["foxy_plan"] = "pro"          # the cheaper claim
    r = _post(client, _envelope("transaction.completed", data))
    assert r.status_code == 200
    assert _org_row(org["org_id"]).plan_tier == "max", "the PRICE decides the tier"


def test_a_purchase_at_an_unknown_price_grants_nothing(client, make_org, monkeypatch):
    """A real payment we cannot price is a human problem, not a licence to guess
    a tier. It is recorded, and the org is left exactly as it was."""
    _configure(monkeypatch)
    org = make_org()
    _patch_org(org["org_id"], plan_tier="free")
    data = _txn_completed(org["org_id"], price_id="pri_something_else")
    r = _post(client, _envelope("transaction.completed", data, event_id="evt_unpriced_m2"))
    assert r.status_code == 200 and r.json()["status"] == "ignored"
    assert _org_row(org["org_id"]).plan_tier == "free"
    assert _events("evt_unpriced_m2")[0].status == "ignored"


def test_an_unknown_org_id_is_recorded_not_provisioned(client, monkeypatch):
    _configure(monkeypatch)
    db = SessionLocal()
    try:
        before = len(db.execute(select(Organization.id)).scalars().all())
    finally:
        db.close()
    r = _post(client, _envelope("transaction.completed",
                                _txn_completed(str(uuid.uuid4()))))
    assert r.status_code == 200 and r.json()["status"] == "org_not_found"
    db = SessionLocal()
    try:
        assert len(db.execute(select(Organization.id)).scalars().all()) == before
    finally:
        db.close()


def test_subscription_created_also_upgrades(client, make_org, monkeypatch):
    """Paddle copies custom_data from the transaction onto the subscription it
    creates, so this event carries foxy_org_id too and is a second, independent
    route to the same outcome. Both must be idempotent with each other."""
    _configure(monkeypatch)
    org = make_org()
    data = _sub(org["org_id"], "active", sub_id="sub_created_m2")
    data["items"] = [{"price": {"id": PRICE_PRO}, "quantity": 1}]
    r = _post(client, _envelope("subscription.created", data))
    assert r.status_code == 200 and r.json()["status"] == "upgraded"
    assert _org_row(org["org_id"]).paddle_subscription_id == "sub_created_m2"


# ── 4 · subscription state maps onto the vocabulary that already exists ──────

def test_each_paddle_status_maps_onto_a_stored_value_we_already_use():
    """The stored vocabulary is a wire contract: three surfaces match these
    strings and `desktop/foxy_client.py` holds several as constants. A new stored
    value would be a cross-surface break, not a rename."""
    allowed = {"active", "past_due", "cancelled"}
    for status in ("active", "trialing", "past_due", "paused", "canceled"):
        mapped = paddle.map_status(status)
        assert mapped in allowed, f"{status} → {mapped} is not in the stored vocabulary"
    assert paddle.map_status("some_future_paddle_status") is None
    assert paddle.map_status(None) is None


def test_an_unmapped_status_leaves_the_stored_value_alone(client, make_org, monkeypatch):
    """Guessing what a vendor's new status means about access is how a paying
    customer gets locked out by somebody else's release note."""
    _configure(monkeypatch)
    org = make_org()
    _patch_org(org["org_id"], plan_tier="pro", subscription_status="active")
    r = _post(client, _envelope("subscription.updated",
                                _sub(org["org_id"], "something_new")))
    assert r.status_code == 200 and r.json()["status"] == "ignored"
    assert _org_row(org["org_id"]).subscription_status == "active"


def test_going_past_due_stamps_the_clock_once_and_only_once(
        client, make_org, monkeypatch):
    """Paddle emits subscription.updated repeatedly while Retain works a dunning
    schedule. Re-stamping on each one would restart the grace window forever and
    the lock would never fire — the same trap the Stripe path documents."""
    _configure(monkeypatch)
    org = make_org()
    _patch_org(org["org_id"], plan_tier="pro", subscription_status="active")

    assert _post(client, _envelope("subscription.updated",
                                   _sub(org["org_id"], "past_due"))).status_code == 200
    first = _org_row(org["org_id"]).past_due_since
    assert first is not None

    for _ in range(3):
        assert _post(client, _envelope("subscription.updated",
                                       _sub(org["org_id"], "past_due"))).status_code == 200
    assert _org_row(org["org_id"]).past_due_since == first, "the clock restarted"


def test_recovering_from_past_due_clears_the_clock(client, make_org, monkeypatch):
    _configure(monkeypatch)
    org = make_org()
    _patch_org(org["org_id"], plan_tier="pro", subscription_status="past_due",
               past_due_since=datetime.now(timezone.utc) - timedelta(days=3))
    assert _post(client, _envelope("subscription.updated",
                                   _sub(org["org_id"], "active"))).status_code == 200
    row = _org_row(org["org_id"])
    assert row.subscription_status == "active"
    assert row.past_due_since is None


def test_a_cancelled_subscription_stops_capture_but_not_the_dashboard(
        client, make_org, login, monkeypatch):
    """D1's deliberate asymmetry, reached through Paddle: someone who left can
    still read and export the evidence they paid for. Leaving is not owing."""
    _configure(monkeypatch)
    org = make_org()
    _patch_org(org["org_id"], plan_tier="pro", subscription_status="active")
    assert _post(client, _envelope("subscription.canceled",
                                   _sub(org["org_id"], "canceled"))).status_code == 200
    assert _org_row(org["org_id"]).subscription_status == "cancelled"

    customer = login(org["admin_email"], org["admin_password"])
    assert customer.get("/v1/usage").status_code == 200, "the dashboard stays open"
    refused = client.post("/v1/logs/batch", json=_rows(), headers=org["auth"])
    assert refused.status_code == 402


def test_a_paused_subscription_keeps_capture_running(client, make_org, monkeypatch):
    """`paused` has no Stripe equivalent and is the one real judgement in the
    mapping. It becomes `past_due`, which locks the dashboard after the grace
    window while capture keeps recording — deliberately the conservative
    direction, because a payment can be recovered and evidence cannot."""
    _configure(monkeypatch)
    org = make_org()
    _patch_org(org["org_id"], plan_tier="pro", subscription_status="active")
    assert _post(client, _envelope("subscription.updated",
                                   _sub(org["org_id"], "paused"))).status_code == 200
    assert _org_row(org["org_id"]).subscription_status == "past_due"
    assert client.post("/v1/logs/batch", json=_rows(),
                       headers=org["auth"]).status_code == 202


# ── 5 · checkout ─────────────────────────────────────────────────────────────

def _stub_paddle_api(monkeypatch, captured: dict, url="https://pay.paddle.test/?_ptxn=txn_1"):
    """Stub the ONE function that does network I/O, so everything above it is
    the real code path."""
    def _fake(method, path, body=None):
        captured.update({"method": method, "path": path, "body": body})
        return {"data": {"id": "txn_1", "checkout": {"url": url}}}
    monkeypatch.setattr(paddle, "_request", _fake)


def test_the_checkout_carries_the_org_id_and_the_configured_price(
        make_org, login, monkeypatch):
    """`custom_data.foxy_org_id` is the single field that turns "provision" into
    "upgrade". Without it the webhook has nothing to match on."""
    _configure(monkeypatch)
    captured: dict = {}
    _stub_paddle_api(monkeypatch, captured)
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    r = c.post("/v1/billing/upgrade-session", json={"plan": "pro"})
    assert r.status_code == 200, r.text
    assert r.json()["checkout_url"] == "https://pay.paddle.test/?_ptxn=txn_1"
    assert r.json()["plan"] == "pro"
    assert captured["method"] == "POST" and captured["path"] == "/transactions"
    assert captured["body"]["custom_data"]["foxy_org_id"] == str(org["org_id"])
    assert captured["body"]["items"] == [{"price_id": PRICE_PRO, "quantity": 1}]


def test_the_checkout_response_shape_is_unchanged(make_org, login, monkeypatch):
    """Three shipped clients read exactly `checkout_url` and `plan` off this
    route. M2 adds a processor, not a response."""
    _configure(monkeypatch)
    _stub_paddle_api(monkeypatch, {})
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    body = c.post("/v1/billing/upgrade-session", json={"plan": "pro"}).json()
    assert set(body) == {"checkout_url", "plan"}


def test_an_unpriced_plan_is_422(make_org, login, monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(get_settings(), "paddle_price_max", "")
    _stub_paddle_api(monkeypatch, {})
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    assert c.post("/v1/billing/upgrade-session", json={"plan": "max"}).status_code == 422
    assert c.post("/v1/billing/upgrade-session",
                  json={"plan": "not-a-plan"}).status_code == 422


def test_a_provider_failure_is_502_not_500(make_org, login, monkeypatch):
    _configure(monkeypatch)

    def _boom(*a, **kw):
        raise RuntimeError("paddle is down")
    monkeypatch.setattr(paddle, "_request", _boom)
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    assert c.post("/v1/billing/upgrade-session", json={"plan": "pro"}).status_code == 502


def test_no_paddle_key_means_the_stripe_branch_still_runs(make_org, login, monkeypatch):
    """The branch is one `if` on `paddle.configured()`. With no Paddle key the
    route must behave exactly as it did before M2 — which, with no Stripe key
    either, is 503."""
    monkeypatch.setattr(get_settings(), "paddle_api_key", "")
    monkeypatch.setattr(get_settings(), "stripe_secret_key", "")
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    assert c.post("/v1/billing/upgrade-session", json={"plan": "pro"}).status_code == 503


def test_the_upgrade_session_is_still_admin_only(make_org, add_user, login, monkeypatch):
    _configure(monkeypatch)
    _stub_paddle_api(monkeypatch, {})
    org = make_org()
    add_user(org["org_id"], "member-m2@test.dev", "memberpass1", role="member")
    c = login("member-m2@test.dev", "memberpass1")
    assert c.post("/v1/billing/upgrade-session", json={"plan": "pro"}).status_code == 403


def test_the_upgrade_session_is_still_reachable_while_locked(make_org, login, monkeypatch):
    """A lock whose only remedy sits behind the lock is a brick. `/v1/billing/`
    is in `auth._GATE_EXEMPT` and this route depends on staying there."""
    _configure(monkeypatch)
    _stub_paddle_api(monkeypatch, {})
    org = make_org()
    _grant_expired_evaluation(org["org_id"])
    c = login(org["admin_email"], org["admin_password"])
    assert c.get("/v1/usage").status_code == 402          # genuinely locked
    assert c.post("/v1/billing/upgrade-session", json={"plan": "pro"}).status_code == 200


def test_sandbox_is_the_default_environment(monkeypatch):
    """A misconfigured PADDLE_ENV must fail against test money, not real money."""
    monkeypatch.setattr(get_settings(), "paddle_env", "sandbox")
    assert paddle.api_base() == "https://sandbox-api.paddle.com"
    monkeypatch.setattr(get_settings(), "paddle_env", "live")
    assert paddle.api_base() == "https://api.paddle.com"
    monkeypatch.setattr(get_settings(), "paddle_env", "typo")
    assert paddle.api_base() == "https://sandbox-api.paddle.com"


# ── 5b · the ANONYMOUS sale-page flow (M2 addendum) ─────────────────────────

def _anon_txn(email="buyer@anon.test", price_id=PRICE_PRO, *,
              customer="ctm_anon", subscription="sub_anon") -> dict:
    """A completed anonymous purchase: custom_data carries NO org id, because no
    workspace existed when the checkout was created."""
    return {
        "id": "txn_anon_" + uuid.uuid4().hex[:12],
        "status": "completed",
        "customer_id": customer,
        "customer_email": email,
        "subscription_id": subscription,
        "custom_data": {"foxy_plan": "pro"},          # note: no foxy_org_id
        "items": [{"price": {"id": price_id}, "quantity": 1}],
    }


def test_the_anonymous_checkout_sends_no_org_id(monkeypatch, client):
    """The sale page has no workspace to name, and the ABSENCE of the key is the
    signal the webhook branches on. An empty string would look like a present but
    unresolvable org and be recorded as a failure."""
    _configure(monkeypatch)
    captured: dict = {}
    _stub_paddle_api(monkeypatch, captured)
    r = client.post("/v1/billing/checkout-session",
                    json={"email": "buyer@anon.test", "plan": "pro"})
    assert r.status_code == 200, r.text
    assert "foxy_org_id" not in captured["body"]["custom_data"]
    assert captured["body"]["customer"] == {"email": "buyer@anon.test"}, (
        "the buyer's email must prefill their checkout"
    )


def test_the_anonymous_checkout_response_shape_is_unchanged(monkeypatch, client):
    """The sale page reads `checkout_url` and nothing else. The Stripe branch
    returns that key ALONE — no `plan` — so the Paddle branch must too."""
    _configure(monkeypatch)
    _stub_paddle_api(monkeypatch, {})
    body = client.post("/v1/billing/checkout-session",
                       json={"email": "buyer@anon.test", "plan": "pro"}).json()
    assert set(body) == {"checkout_url"}


def test_an_anonymous_purchase_provisions_exactly_one_workspace(client, monkeypatch):
    """The other door. No org id → create one, on the tier the PRICE says."""
    _configure(monkeypatch)
    db = SessionLocal()
    try:
        before = len(db.execute(select(Organization.id)).scalars().all())
    finally:
        db.close()

    r = _post(client, _envelope("transaction.completed", _anon_txn()))
    assert r.status_code == 200 and r.json()["status"] == "provisioned", r.text

    db = SessionLocal()
    try:
        orgs = db.execute(select(Organization)).scalars().all()
        org = db.execute(select(Organization).where(
            Organization.contact_email == "buyer@anon.test")).scalar_one()
        users = db.execute(select(User).where(User.org_id == org.id)).scalars().all()
        legacy_hash = org.api_key_hash
        keys = db.execute(select(ApiKey).where(ApiKey.org_id == org.id)).scalars().all()
    finally:
        db.close()
    assert len(orgs) == before + 1
    assert org.plan_tier == "pro"
    assert org.monthly_log_quota == get_settings().quota_for("pro")
    assert org.subscription_status == "active"
    assert org.paddle_customer_id == "ctm_anon"
    assert org.paddle_subscription_id == "sub_anon"
    assert [u.role for u in users] == ["admin"]
    assert legacy_hash, "api_key_hash is NOT NULL and must be bound to something"
    assert keys == [], (
        "provisioning must NOT mint a usable API key — a bearer secret would then "
        "have to travel by email"
    )


def test_a_replayed_anonymous_purchase_does_not_fork_a_second_workspace(
        client, monkeypatch):
    """Two independent defences, and this asserts both fire: the event id is
    already in payment_events, AND the customer id already maps to an org."""
    _configure(monkeypatch)
    data = _anon_txn()
    first = _post(client, _envelope("transaction.completed", data, event_id="evt_anon_1"))
    assert first.json()["status"] == "provisioned"

    # A DIFFERENT event id for the same customer — so the ON CONFLICT guard is
    # bypassed and only the customer-id lookup can save us.
    second = _post(client, _envelope("transaction.completed", data, event_id="evt_anon_2"))
    assert second.status_code == 200
    assert second.json()["status"] == "already_provisioned"

    db = SessionLocal()
    try:
        n = len(db.execute(select(Organization).where(
            Organization.contact_email == "buyer@anon.test")).scalars().all())
    finally:
        db.close()
    assert n == 1, "a second delivery forked a workspace"


def test_an_anonymous_purchase_at_an_unknown_price_provisions_nothing(
        client, monkeypatch):
    """Provisioning a workspace on a tier we cannot identify would be inventing a
    plan. Record it and create nothing."""
    _configure(monkeypatch)
    db = SessionLocal()
    try:
        before = len(db.execute(select(Organization.id)).scalars().all())
    finally:
        db.close()
    r = _post(client, _envelope("transaction.completed",
                                _anon_txn(price_id="pri_unknown"),
                                event_id="evt_anon_unpriced"))
    assert r.status_code == 200 and r.json()["status"] == "ignored"
    db = SessionLocal()
    try:
        assert len(db.execute(select(Organization.id)).scalars().all()) == before
    finally:
        db.close()
    assert _events("evt_anon_unpriced")[0].status == "ignored"


def test_provisioning_sends_a_set_password_invite_and_never_a_key(
        client, monkeypatch):
    """The invite is a set-password link ONLY. A bearer API key must never be
    emailed; the buyer mints one from the dashboard after signing in.

    Delivery is asserted to happen AFTER the commit — the org must already exist
    when the mail is attempted, or a mail failure could roll the provisioning
    back and let a retry create a second workspace."""
    _configure(monkeypatch)
    sent: list[dict] = []

    import app.password_reset as pr
    original = pr.issue_reset

    def _spy(db, user, email, url, invite=False):
        db_org = SessionLocal()
        try:
            exists = db_org.get(Organization, user.org_id) is not None
        finally:
            db_org.close()
        sent.append({"email": email, "invite": invite, "org_committed": exists})
        return original(db, user, email, url, invite=invite)

    monkeypatch.setattr(pr, "issue_reset", _spy)

    r = _post(client, _envelope("transaction.completed", _anon_txn()))
    assert r.json()["status"] == "provisioned"
    assert len(sent) == 1, "exactly one invite"
    assert sent[0]["email"] == "buyer@anon.test"
    assert sent[0]["invite"] is True, "it must be an INVITE, not a bare reset"
    assert sent[0]["org_committed"] is True, (
        "the invite was attempted before the transaction committed"
    )


def test_an_authenticated_upgrade_never_takes_the_provisioning_branch(
        client, make_org, monkeypatch):
    """The two doors must not cross. An event carrying an org id upgrades that
    workspace and creates nothing, even though the same handler serves both."""
    _configure(monkeypatch)
    org = make_org()
    db = SessionLocal()
    try:
        before = len(db.execute(select(Organization.id)).scalars().all())
    finally:
        db.close()
    r = _post(client, _envelope("transaction.completed", _txn_completed(org["org_id"])))
    assert r.json()["status"] == "upgraded"
    db = SessionLocal()
    try:
        assert len(db.execute(select(Organization.id)).scalars().all()) == before
    finally:
        db.close()


# ── 6 · the card gate exemption (register #95) ───────────────────────────────

def test_a_paddle_subscriber_is_grandfathered_from_the_card_gate(make_org, monkeypatch):
    """This read only `stripe_subscription_id`, so the newest paying customers
    would have been the only ones NOT exempt — precisely inverted. Forced on
    here because both real safeties are off by default, which would make the
    assertion pass for the wrong reason."""
    monkeypatch.setattr(get_settings(), "require_card_on_file", True)
    monkeypatch.setattr(get_settings(), "card_gate_grandfather_before",
                        "2000-01-01T00:00:00+00:00")
    org = _org_row(make_org()["org_id"])
    org.stripe_subscription_id = None
    org.paddle_subscription_id = None
    org.card_on_file = False
    assert billing_state.grandfathered(org) is False, "the gate must really be armed"
    assert billing_state.card_lock(org) is not None

    org.paddle_subscription_id = "sub_paying_m2"
    assert billing_state.grandfathered(org) is True
    assert billing_state.card_lock(org) is None


# ── 7 · secrets stay out of everything ───────────────────────────────────────

def test_no_secret_reaches_a_response_or_the_event_log(client, make_org, monkeypatch):
    """The API key travels in a header and the webhook secret signs the body.
    Neither may appear in a response body or in the durable log we show staff."""
    _configure(monkeypatch, api_key="sdbx_super_secret_key")
    org = make_org()
    body = _envelope("transaction.completed", _txn_completed(org["org_id"]),
                     event_id="evt_secret_m2")
    r = _post(client, body)
    assert r.status_code == 200
    assert "sdbx_super_secret_key" not in r.text
    assert SECRET not in r.text
    stored = json.dumps(_events("evt_secret_m2")[0].payload)
    assert "sdbx_super_secret_key" not in stored and SECRET not in stored


def _executable_source(module) -> str:
    """A module's source with every docstring and comment removed.

    A guard that scans a file scans the prose explaining the guard too — and this
    one caught exactly that on its first run, matching `paddle.py`'s own docstring
    describing the rule it enforces. Stripping the prose is the fix that survives
    somebody documenting the rule again; assembling the needle from fragments only
    hides the collision until the next person writes it out.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(module))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                body.pop(0)                      # the docstring
    return ast.unparse(tree)                     # comments do not survive unparse


def test_the_paddle_module_never_logs_str_of_an_exception():
    """A urllib error renders the request URL, and the API key travels in a
    header on that request. Hard rule 6: log the TYPE, not the rendered text.
    Checked on the source because the failure is invisible until the day it
    matters — and checked on the CODE, not the comments."""
    code = _executable_source(paddle)
    assert "str(" + "exc)" not in code, "log the exception type, never its rendered text"
    assert "type(exc).__name__" in code, "the type-only logging is gone"
