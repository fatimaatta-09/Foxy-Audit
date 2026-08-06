"""Two defects found on a live workspace after the first real Paddle purchase.

Both were observed on 2026-08-06 against an org that had genuinely bought Pro
through checkout → webhook → upgrade → dashboard.

  #101  a paying customer is still told their trial is ending
  #97   a renewal silently restores a tier staff downgraded by hand

Everything here asserts the OUTCOME on a row after driving a real route — never
the shape of a helper. A helper nobody calls is exactly as broken as no helper,
and both of these defects lived at a call site rather than inside a function.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app import paddle
from app.config import get_settings
from app.db import SessionLocal
from app.models import Organization, PaymentEvent

SECRET = "pdl_ntfset_m3b"
PRICE_PRO = "pri_m3b_pro"
PRICE_MAX = "pri_m3b_max"


# ── helpers ──────────────────────────────────────────────────────────────────

def _sign(body: bytes) -> str:
    ts = int(time.time())
    mac = hmac.new(SECRET.encode(), f"{ts}:".encode() + body, hashlib.sha256).hexdigest()
    return f"ts={ts};h1={mac}"


def _configure(monkeypatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "paddle_api_key", "sdbx_m3b")
    monkeypatch.setattr(s, "paddle_webhook_secret", SECRET)
    monkeypatch.setattr(s, "paddle_price_pro", PRICE_PRO)
    monkeypatch.setattr(s, "paddle_price_max", PRICE_MAX)


def _envelope(event_type: str, data: dict) -> bytes:
    return json.dumps({
        "event_id": f"evt_{uuid.uuid4().hex[:24]}",
        "event_type": event_type,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "notification_id": f"ntf_{uuid.uuid4().hex[:24]}",
        "data": data,
    }).encode()


def _txn(org_id: str | None, *, price_id=PRICE_PRO, origin: str | None = None,
         customer="ctm_m3b", subscription="sub_m3b") -> dict:
    """A transaction.completed data object. `origin` is the field this phase is
    about; omitted when the caller wants the no-origin fallback."""
    data: dict = {
        "id": "txn_m3b_" + uuid.uuid4().hex[:12],
        "status": "completed",
        "customer_id": customer,
        "subscription_id": subscription,
        "items": [{"price": {"id": price_id}, "quantity": 1}],
        "custom_data": {"foxy_plan": "pro"},
    }
    if org_id:
        data["custom_data"]["foxy_org_id"] = str(org_id)
    if origin is not None:
        data["origin"] = origin
    return data


def _post(client, data: dict, event_type: str = "transaction.completed"):
    body = _envelope(event_type, data)
    return client.post("/v1/webhooks/paddle", content=body,
                       headers={"paddle-signature": _sign(body),
                                "content-type": "application/json"})


def _patch_org(org_id, **fields) -> None:
    db = SessionLocal()
    try:
        org = db.get(Organization, uuid.UUID(str(org_id)))
        for k, v in fields.items():
            setattr(org, k, v)
        db.commit()
    finally:
        db.close()


def _org(org_id) -> Organization:
    db = SessionLocal()
    try:
        return db.get(Organization, uuid.UUID(str(org_id)))
    finally:
        db.close()


def _trialing(org_id, days: int = 7) -> datetime:
    """Stamp a live trial, exactly as signup does."""
    ends = datetime.now(timezone.utc) + timedelta(days=days)
    _patch_org(org_id, plan_tier="free", trial_ends_at=ends,
               monthly_log_quota=get_settings().quota_for("free"))
    return ends


def _stripe_checkout(session: dict):
    from app.routers.billing import _handle_checkout
    db = SessionLocal()
    try:
        result = _handle_checkout(db, session)
        db.commit()
        return result
    finally:
        db.close()


# ── 1 · #101 · a purchase ends the trial ────────────────────────────────────

def test_buying_pro_through_paddle_leaves_no_trial_behind(client, make_org, monkeypatch):
    """THE live observation: Pro / Active / 25,000 credits, still displaying
    "Your trial ends in 7 days"."""
    _configure(monkeypatch)
    org = make_org()
    _trialing(org["org_id"])
    assert _org(org["org_id"]).trial_ends_at is not None, "the fixture must set one"

    r = _post(client, _txn(org["org_id"], origin=paddle.ORIGIN_API))
    assert r.status_code == 200 and r.json()["status"] == "upgraded", r.text

    row = _org(org["org_id"])
    assert row.plan_tier == "pro"
    assert row.trial_ends_at is None, "a paying customer is not on a trial"


def test_buying_through_stripe_leaves_no_trial_behind(make_org):
    """The other processor's upgrade path had the identical hole. Stripe has
    never taken a payment here, but the path is live code and M4a's lock will
    read the same column."""
    org = make_org()
    _trialing(org["org_id"])

    result, _ = _stripe_checkout({
        "customer": "cus_m3b", "customer_email": "who@cares.test",
        "subscription": "sub_m3b_stripe",
        "metadata": {"foxy_org_id": org["org_id"], "foxy_plan": "max"},
    })
    assert result["status"] == "upgraded"
    row = _org(org["org_id"])
    assert row.plan_tier == "max"
    assert row.trial_ends_at is None


def test_an_anonymously_provisioned_org_has_no_trial(client, monkeypatch):
    """The sale-page door creates the workspace itself. It must not be born with
    a trial it has already paid past."""
    _configure(monkeypatch)
    data = _txn(None, origin=paddle.ORIGIN_API, customer="ctm_m3b_anon",
                subscription="sub_m3b_anon")
    data["customer_email"] = "walkup@m3b.test"
    r = _post(client, data)
    assert r.status_code == 200 and r.json()["status"] == "provisioned", r.text

    db = SessionLocal()
    try:
        row = db.execute(select(Organization).where(
            Organization.contact_email == "walkup@m3b.test")).scalar_one()
    finally:
        db.close()
    assert row.plan_tier == "pro"
    assert row.trial_ends_at is None


def test_a_free_org_keeps_its_trial(client, make_org, monkeypatch):
    """The population this must not touch. Nothing in this phase may shorten a
    free org's trial — that clock is the free tier working as designed."""
    _configure(monkeypatch)
    org = make_org()
    ends = _trialing(org["org_id"])

    # An unrelated Paddle event for a different customer entirely.
    other = _txn(None, origin=paddle.ORIGIN_API, customer="ctm_someone_else",
                 subscription="sub_someone_else")
    other["customer_email"] = "someone@else.test"
    assert _post(client, other).status_code == 200

    row = _org(org["org_id"])
    assert row.plan_tier == "free"
    assert row.trial_ends_at is not None, "the free org's trial was cleared"
    assert abs((row.trial_ends_at - ends).total_seconds()) < 2


def test_staff_setting_free_still_grants_a_trial(make_org, make_staff, staff_login):
    """`set_organization_plan` now routes its non-free branch through the shared
    helper. Its FREE branch must still hand out a trial — the refactor was meant
    to be behaviour-identical, and this is the half that could silently vanish."""
    org = make_org()
    _patch_org(org["org_id"], plan_tier="pro", trial_ends_at=None)
    who = make_staff(role="superadmin")
    staff = staff_login(who["email"], who["password"])

    r = staff.post(f"/admin/v1/organizations/{org['org_id']}/plan", json={"plan": "free"})
    assert r.status_code == 200, r.text
    row = _org(org["org_id"])
    assert row.plan_tier == "free"
    assert row.trial_ends_at is not None, "a free org must get its trial clock"


def test_staff_setting_a_paid_plan_still_clears_the_trial(make_org, make_staff,
                                                          staff_login):
    org = make_org()
    _trialing(org["org_id"])
    who = make_staff(role="superadmin")
    staff = staff_login(who["email"], who["password"])

    assert staff.post(f"/admin/v1/organizations/{org['org_id']}/plan",
                      json={"plan": "pro"}).status_code == 200
    assert _org(org["org_id"]).trial_ends_at is None


# ── 2 · #97 · a renewal is not a decision ───────────────────────────────────

def test_the_origin_values_are_paddles_own(monkeypatch):
    """Read off Paddle's transaction schema, not guessed. `subscription_recurring`
    is a renewal; `api` is what OUR first purchases carry, because the backend
    creates the transaction and Paddle.js only opens it."""
    _configure(monkeypatch)
    assert paddle.chooses_a_plan({"origin": paddle.ORIGIN_API}) is True
    assert paddle.chooses_a_plan({"origin": paddle.ORIGIN_WEB}) is True
    assert paddle.chooses_a_plan({"origin": paddle.ORIGIN_SUBSCRIPTION_UPDATE}) is True
    assert paddle.chooses_a_plan({"origin": paddle.ORIGIN_SUBSCRIPTION_RECURRING}) is False
    assert paddle.chooses_a_plan({"origin": paddle.ORIGIN_SUBSCRIPTION_CHARGE}) is False
    assert paddle.chooses_a_plan(
        {"origin": paddle.ORIGIN_SUBSCRIPTION_PAYMENT_METHOD_CHANGE}) is False
    # An absent origin keeps today's behaviour: failing to upgrade somebody who
    # has just paid is worse than re-applying a tier that already matches.
    assert paddle.chooses_a_plan({}) is True
    assert paddle.chooses_a_plan({"origin": ""}) is True


def test_a_renewal_does_not_undo_a_staff_downgrade(client, make_org, make_staff,
                                                   staff_login, monkeypatch):
    """THE regression test. Everything else in this section is scaffolding.

    Buy Pro, have staff move them to free by hand, then let the monthly renewal
    arrive carrying the original Pro price. The staff decision has to survive.
    """
    _configure(monkeypatch)
    org = make_org()
    assert _post(client, _txn(org["org_id"], origin=paddle.ORIGIN_API)
                 ).json()["status"] == "upgraded"
    assert _org(org["org_id"]).plan_tier == "pro"

    who = make_staff(role="superadmin")
    staff = staff_login(who["email"], who["password"])
    assert staff.post(f"/admin/v1/organizations/{org['org_id']}/plan",
                      json={"plan": "free"}).status_code == 200
    assert _org(org["org_id"]).plan_tier == "free"

    renewal = _post(client, _txn(org["org_id"],
                                 origin=paddle.ORIGIN_SUBSCRIPTION_RECURRING))
    assert renewal.status_code == 200
    assert renewal.json()["status"] == "renewed", renewal.json()

    row = _org(org["org_id"])
    assert row.plan_tier == "free", "the renewal put the purchased tier back"
    assert row.monthly_log_quota == get_settings().quota_for("free"), (
        "the renewal restored the purchased quota"
    )


def test_a_renewal_still_confirms_the_money(client, make_org, monkeypatch):
    """A renewal must not be inert. It is the evidence that a dunning clock
    should stop — it just may not rewrite the plan."""
    _configure(monkeypatch)
    org = make_org()
    _patch_org(org["org_id"], plan_tier="pro", subscription_status="past_due",
               past_due_since=datetime.now(timezone.utc) - timedelta(days=3),
               paddle_subscription_id="sub_m3b", paddle_customer_id="ctm_m3b")

    r = _post(client, _txn(org["org_id"], origin=paddle.ORIGIN_SUBSCRIPTION_RECURRING))
    assert r.status_code == 200 and r.json()["status"] == "renewed"

    row = _org(org["org_id"])
    assert row.subscription_status == "active"
    assert row.past_due_since is None, "a paid renewal must stop the dunning clock"
    assert row.plan_tier == "pro", "and must not have changed the tier"


def test_a_renewal_ends_a_trial_and_an_evaluation(client, make_org, monkeypatch):
    """Both regimes end because the org is paying, which a completed renewal
    proves just as well as a first purchase does."""
    _configure(monkeypatch)
    org = make_org()
    _patch_org(org["org_id"], plan_tier="pro",
               trial_ends_at=datetime.now(timezone.utc) + timedelta(days=7),
               evaluation_offer_id="m3b-offer", evaluation_credit_limit=10,
               evaluation_credits_used=3,
               evaluation_ends_at=datetime.now(timezone.utc) + timedelta(days=2))

    assert _post(client, _txn(org["org_id"],
                              origin=paddle.ORIGIN_SUBSCRIPTION_RECURRING)
                 ).json()["status"] == "renewed"
    row = _org(org["org_id"])
    assert row.trial_ends_at is None
    assert row.evaluation_offer_id is None
    assert row.plan_tier == "pro"


def test_a_renewal_never_provisions_a_second_workspace(client, make_org, monkeypatch):
    """The anonymous door resolves by customer id, and a renewal for an
    anonymously-bought org carries no `foxy_org_id` at all — custom_data only
    ever held `foxy_plan` for that door. It must find the org, not make one."""
    _configure(monkeypatch)
    org = make_org()
    _patch_org(org["org_id"], plan_tier="pro", paddle_customer_id="ctm_anon_m3b",
               paddle_subscription_id="sub_anon_m3b")
    db = SessionLocal()
    try:
        before = len(db.execute(select(Organization.id)).scalars().all())
    finally:
        db.close()

    r = _post(client, _txn(None, origin=paddle.ORIGIN_SUBSCRIPTION_RECURRING,
                           customer="ctm_anon_m3b", subscription="sub_anon_m3b"))
    assert r.status_code == 200 and r.json()["status"] == "renewed", r.json()
    db = SessionLocal()
    try:
        assert len(db.execute(select(Organization.id)).scalars().all()) == before
    finally:
        db.close()


def test_a_first_purchase_still_upgrades_exactly_as_before(client, make_org,
                                                           monkeypatch):
    """The half that must NOT change. `transaction.completed` is still the right
    signal for a first purchase and this phase may not weaken it."""
    _configure(monkeypatch)
    org = make_org()
    _trialing(org["org_id"])

    r = _post(client, _txn(org["org_id"], price_id=PRICE_MAX,
                           origin=paddle.ORIGIN_API))
    assert r.status_code == 200 and r.json()["status"] == "upgraded"
    row = _org(org["org_id"])
    assert row.plan_tier == "max"
    assert row.monthly_log_quota == get_settings().quota_for("max")
    assert row.subscription_status == "active"
    assert row.paddle_customer_id == "ctm_m3b"
    assert row.paddle_subscription_id == "sub_m3b"
    assert row.trial_ends_at is None


def test_a_deliberate_plan_change_through_paddle_still_applies(client, make_org,
                                                               monkeypatch):
    """`subscription_update` is a real change billed now — somebody chose it, so
    it must be allowed to move the tier. Suppressing every non-first transaction
    would have broken this, which is why the rule is an origin allowlist rather
    than "only the first one counts"."""
    _configure(monkeypatch)
    org = make_org()
    assert _post(client, _txn(org["org_id"], origin=paddle.ORIGIN_API)
                 ).json()["status"] == "upgraded"
    assert _org(org["org_id"]).plan_tier == "pro"

    r = _post(client, _txn(org["org_id"], price_id=PRICE_MAX,
                           origin=paddle.ORIGIN_SUBSCRIPTION_UPDATE))
    assert r.status_code == 200 and r.json()["status"] == "upgraded"
    assert _org(org["org_id"]).plan_tier == "max"


def test_a_renewal_is_a_new_event_and_is_still_logged(client, make_org, monkeypatch):
    """A renewal is genuinely NEW, not a replay: `payment_events` dedupes on
    provider_event_id, and two renewals are two event ids. Both must be recorded
    — the durable log is how a human reconstructs a billing history."""
    _configure(monkeypatch)
    org = make_org()
    _patch_org(org["org_id"], plan_tier="pro")
    for _ in range(2):
        assert _post(client, _txn(org["org_id"],
                                  origin=paddle.ORIGIN_SUBSCRIPTION_RECURRING)
                     ).json()["status"] == "renewed"

    db = SessionLocal()
    try:
        rows = db.execute(select(PaymentEvent)).scalars().all()
    finally:
        db.close()
    assert len(rows) == 2, "two renewals must be two rows, not one deduped one"
    assert all(r.status == "processed" for r in rows)


def test_an_unknown_org_on_a_renewal_is_recorded_not_provisioned(client, monkeypatch):
    _configure(monkeypatch)
    db = SessionLocal()
    try:
        before = len(db.execute(select(Organization.id)).scalars().all())
    finally:
        db.close()
    r = _post(client, _txn(str(uuid.uuid4()),
                           origin=paddle.ORIGIN_SUBSCRIPTION_RECURRING,
                           customer="ctm_nobody_m3b", subscription="sub_nobody_m3b"))
    assert r.status_code == 200 and r.json()["status"] == "org_not_found"
    db = SessionLocal()
    try:
        assert len(db.execute(select(Organization.id)).scalars().all()) == before
    finally:
        db.close()
