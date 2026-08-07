"""What a dashboard lock may and may not withhold, and two audit holes (P1).

Three register entries, one principle between them:

    A lock may withhold the SERVICE. It must never withhold the customer's
    existing PROPERTY, or their means of removing the lock.

#49 — `/v1/logs/export` was behind every lock, so a billing state stood between a
customer and their own ledger on a product whose argument is *do not trust our
dashboard, verify the evidence yourself*.

#99 — a duplicate Paddle customer answered 500, and Paddle retries anything that
is not a 200. A deterministic failure bought an unbounded retry schedule.

#57 — redeeming an evaluation was audited; ending one by paying was not, though
it is the larger change and the moment a prospect becomes a customer.

The dangerous half of #49 is the widening, so most of what follows is about what
did NOT become exempt.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text

from app import auth as auth_mod
from app.config import get_settings
from app.db import SessionLocal
from app.models import AccountAction, Organization, PaymentEvent

SECRET = "pdl_ntfset_p1"


# ── helpers ──────────────────────────────────────────────────────────────────

def _set(org_id, **fields) -> None:
    with SessionLocal() as db:
        o = db.get(Organization, uuid.UUID(str(org_id)))
        for k, v in fields.items():
            setattr(o, k, v)
        db.commit()


def _lock_expired_evaluation(org_id) -> None:
    """The condition #49 names, and the one an auditor would meet mid-engagement."""
    _set(org_id, plan_tier="premium", evaluation_offer_id="offer-p1",
         evaluation_credit_limit=10,
         evaluation_ends_at=datetime.now(timezone.utc) - timedelta(days=1))


def _actions(org_id, action=None):
    with SessionLocal() as db:
        q = select(AccountAction).where(
            AccountAction.org_id == uuid.UUID(str(org_id)))
        if action:
            q = q.where(AccountAction.action == action)
        return list(db.execute(q.order_by(AccountAction.created_at)).scalars().all())


def _ingest(client, org):
    """Capture one event the way the SDK does — `POST /v1/logs/batch`, never the
    GET-only `/v1/logs`, which would collect 405 and satisfy any `!= 402`."""
    h = hashlib.sha256(str(uuid.uuid4()).encode()).hexdigest()
    return client.post("/v1/logs/batch", headers=org["auth"], json=[{
        "prompt_hash": h, "response_hash": h, "token_count": 8, "policy_tag": "t",
    }])


def _configure_paddle(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "paddle_api_key", "sdbx_p1")
    monkeypatch.setattr(s, "paddle_webhook_secret", SECRET)
    monkeypatch.setattr(s, "paddle_price_pro", "pri_p1_pro")


def _post_paddle(client, data, event_type="transaction.completed"):
    body = json.dumps({
        "event_id": "evt_p1_" + uuid.uuid4().hex[:20], "event_type": event_type,
        "occurred_at": datetime.now(timezone.utc).isoformat(), "data": data,
    }).encode()
    ts = int(time.time())
    mac = hmac.new(SECRET.encode(), f"{ts}:".encode() + body, hashlib.sha256).hexdigest()
    return client.post("/v1/webhooks/paddle", content=body,
                       headers={"paddle-signature": f"ts={ts};h1={mac}",
                                "content-type": "application/json"})


# ── 1 · #49 · the property a lock may not withhold ──────────────────────────

def test_a_locked_org_can_export_its_own_evidence(make_org, login):
    """THE guard this phase exists to earn. The org is as locked as it gets —
    an evaluation whose window shut — and the export still answers."""
    org = make_org()
    _lock_expired_evaluation(org["org_id"])
    c = login(org["admin_email"], org["admin_password"])

    assert c.get("/v1/logs").status_code == 402, (
        "the fixture is not actually locked, so this proves nothing")
    r = c.get("/v1/logs/export")
    assert r.status_code == 200, r.text


def test_a_locked_org_can_verify_and_measure_its_own_chain(make_org, login):
    """Verification is the product's central claim; coverage is what says
    whether the export is complete. Neither is worth much without the other."""
    org = make_org()
    _lock_expired_evaluation(org["org_id"])
    c = login(org["admin_email"], org["admin_password"])
    assert c.get("/v1/verify").status_code == 200
    assert c.get("/v1/coverage").status_code == 200


def test_every_lock_condition_lets_the_evidence_out(make_org, login):
    """#49 names four conditions, not one. The exemption is on the PATH, so it
    cannot depend on which condition fired — asserted rather than assumed,
    because "it is path-based" is exactly the kind of reasoning that is true
    right up until somebody adds a condition-specific branch."""
    now = datetime.now(timezone.utc)
    cases = {
        "evaluation_expired": dict(
            plan_tier="premium", evaluation_offer_id="o", evaluation_credit_limit=5,
            evaluation_ends_at=now - timedelta(days=1)),
        "subscription_past_due": dict(
            plan_tier="pro", subscription_status="past_due",
            past_due_since=now - timedelta(days=90)),
        "subscription_incomplete": dict(
            plan_tier="pro", subscription_status="incomplete"),
    }
    for reason, fields in cases.items():
        org = make_org()
        _set(org["org_id"], **fields)
        c = login(org["admin_email"], org["admin_password"])
        blocked = c.get("/v1/logs")
        assert blocked.status_code == 402, f"{reason} did not lock the dashboard"
        assert blocked.json()["detail"]["code"] == reason
        assert c.get("/v1/logs/export").status_code == 200, (
            f"{reason} still withholds the customer's evidence")


# ── 2 · #49 · what did NOT become exempt ────────────────────────────────────

def test_browsing_the_ledger_is_still_behind_the_lock(make_org, login):
    """The data is property; the convenience UI over it is service. Export and
    verify are the paths that let a customer leave with their evidence and check
    it without us — which is the product's own argument — and those are what the
    lock may not touch. The dashboard's browser is not."""
    org = make_org()
    _lock_expired_evaluation(org["org_id"])
    c = login(org["admin_email"], org["admin_password"])
    for path in ("/v1/logs", "/v1/logs/breaches", "/v1/stats", "/v1/usage",
                 "/v1/analytics/threats", "/v1/policies", "/v1/notifications"):
        assert c.get(path).status_code == 402, f"{path} is no longer gated"


def test_nothing_that_mutates_became_exempt(make_org, login):
    """Reading what you already have is property. Changing your account while
    locked is not."""
    org = make_org()
    _lock_expired_evaluation(org["org_id"])
    c = login(org["admin_email"], org["admin_password"])
    assert c.post("/v1/keys", json={"name": "x"}).status_code == 402
    assert c.post("/v1/exports", json={"type": "logs"}).status_code == 402
    assert c.put("/v1/policies", json={"pii_detection": True}).status_code == 402


def test_the_export_prefix_does_not_take_ingest_with_it():
    """`_GATE_EXEMPT` is prefix-matched with `startswith`, so this is the whole
    risk of the change: a prefix of `/v1/logs` would have swallowed
    `/v1/logs/batch`. Asserted against the tuple itself rather than through a
    request, because the request would pass for the wrong reason — ingest
    authenticates with `require_org`, which never consults this tuple at all."""
    exempt = auth_mod._GATE_EXEMPT
    assert "/v1/logs" not in exempt, (
        "a bare /v1/logs prefix is exempt — it covers every logs route including "
        "ingest, and reads that should stay behind the lock")
    for path in ("/v1/logs/batch", "/v1/logs", "/v1/logs/breaches", "/v1/keys",
                 "/v1/exports", "/v1/passport"):
        assert not path.startswith(exempt), f"{path} is exempt by prefix accident"
    for path in ("/v1/logs/export", "/v1/verify", "/v1/verify/hash/abc",
                 "/v1/coverage"):
        assert path.startswith(exempt), f"{path} is not exempt"


def test_sdk_ingest_is_still_gated_by_the_thing_that_gates_it(client, make_org):
    """The guarantee that must survive this phase, proved against the mechanism
    that actually provides it.

    Ingest never passed through `_enforce_dashboard_gates` — it authenticates
    with `require_org`, and its commercial gate is `capture_block`. So the risk
    was never that this change un-gates ingest; it is that a future reader
    assumes the exempt tuple is what governs it. Both halves are asserted: a
    locked org is still refused capture, and it is refused with the CAPTURE
    gate's own code rather than a dashboard reason.
    """
    org = make_org()
    _lock_expired_evaluation(org["org_id"])
    r = _ingest(client, org)
    assert r.status_code == 402, "an expired evaluation no longer blocks capture"
    assert r.json()["detail"]["code"] == "evaluation_expired"

    # And the Bearer path is not gated by the dashboard lock at all — which is
    # why exempting these reads grants a locked org nothing it lacked.
    healthy = make_org()
    assert _ingest(client, healthy).status_code == 202


def test_a_locked_org_reaches_export_over_its_api_key_too(client, make_org):
    """The fact that makes this widening small: `resolve_org` takes the SDK key
    as well as the cookie, and the Bearer path has never been gated. What P1
    changed is which credential works in a browser."""
    org = make_org()
    _lock_expired_evaluation(org["org_id"])
    assert client.get("/v1/logs/export", headers=org["auth"]).status_code == 200


# ── 3 · #99 · a deterministic failure must not buy a retry schedule ─────────

def test_a_duplicate_billing_customer_answers_200_so_paddle_stops(
        client, make_org, monkeypatch):
    """Paddle retries anything that is not a 200 within five seconds
    (`paddle.py`), so the old 500 bought a retry schedule that could only
    reproduce the same uniqueness violation forever, on the path money arrives
    on. The constraint stays — two orgs sharing a billing customer is real
    corruption — but the answer changes."""
    _configure_paddle(monkeypatch)
    first, second = make_org(), make_org()
    _set(first["org_id"], paddle_customer_id="ctm_shared_p1")

    data = {
        "id": "txn_p1_" + uuid.uuid4().hex[:10], "status": "completed",
        "origin": "web", "customer_id": "ctm_shared_p1",
        "subscription_id": "sub_p1", "currency_code": "USD",
        "custom_data": {"foxy_org_id": str(second["org_id"])},
        "items": [{"price": {"id": "pri_p1_pro"}, "quantity": 1}],
        "details": {"totals": {"grand_total": "4900"}},
    }
    r = _post_paddle(client, data)
    assert r.status_code == 200, (
        f"a duplicate billing customer still answers {r.status_code}, so Paddle "
        "keeps retrying a failure that cannot succeed")
    assert r.json()["status"] == "failed"
    assert r.json()["reason"].startswith("integrity:")


def test_the_duplicate_is_recorded_where_a_human_will_see_it(
        client, make_org, monkeypatch):
    """Answering 200 is only safe because the event is kept and surfaced. It
    lands on the payment-events page M3d built."""
    _configure_paddle(monkeypatch)
    first, second = make_org(), make_org()
    _set(first["org_id"], paddle_customer_id="ctm_shared_p2")
    data = {
        "id": "txn_p2_" + uuid.uuid4().hex[:10], "status": "completed",
        "origin": "web", "customer_id": "ctm_shared_p2",
        "subscription_id": "sub_p2", "currency_code": "USD",
        "custom_data": {"foxy_org_id": str(second["org_id"])},
        "items": [{"price": {"id": "pri_p1_pro"}, "quantity": 1}],
        "details": {"totals": {"grand_total": "4900"}},
    }
    _post_paddle(client, data)

    with SessionLocal() as db:
        rows = list(db.execute(select(PaymentEvent)).scalars().all())
    failed = [r for r in rows if r.status == "failed"]
    assert failed, "the duplicate was not recorded at all"
    err = failed[-1].error or ""
    # ASSERT THE SHAPE, not the absence of three keywords. The first version of
    # this checked that "SELECT" and "INSERT" were not in the string, and a
    # mutation replacing the constraint name with str(exc) sailed through it —
    # the failing statement here is an UPDATE. `integrity:` plus a bare SQL
    # identifier is what is wanted, so that is what is asserted.
    assert re.fullmatch(r"integrity:[a-z0-9_]+", err), (
        f"the stored reason is not a bare constraint name: {err!r} — an "
        "IntegrityError's str() carries the statement and its bound parameters, "
        "and staff read this column")
    assert "paddle_customer" in err, "the reason does not name the constraint that fired"


def test_a_failure_of_any_kind_is_actually_persisted(client, make_org, monkeypatch):
    """The defect underneath #99, found by asking the table rather than reading
    the comment above it.

    The received row is inserted in the SAME transaction as the dispatch, so
    `db.rollback()` on a failure discarded it — and the UPDATE that followed
    matched zero rows. The handler was labelled "persist the failure, never drop
    the record" and dropped every one. Both the register entry and the phase
    brief describe this path as stamping the event `failed`; it did not.

    Driven through a NON-integrity failure on purpose, so it covers the generic
    handler and not only the new branch.
    """
    _configure_paddle(monkeypatch)
    from app.routers import billing as billing_mod

    def boom(*a, **k):
        raise RuntimeError("dispatch exploded")

    monkeypatch.setattr(billing_mod, "_paddle_apply_purchase", boom)
    org = make_org()
    data = {
        "id": "txn_p4_" + uuid.uuid4().hex[:10], "status": "completed",
        "origin": "web", "customer_id": "ctm_p4", "subscription_id": "sub_p4",
        "currency_code": "USD", "custom_data": {"foxy_org_id": str(org["org_id"])},
        "items": [{"price": {"id": "pri_p1_pro"}, "quantity": 1}],
        "details": {"totals": {"grand_total": "4900"}},
    }
    r = _post_paddle(client, data)
    assert r.status_code == 500, (
        "a failure that MIGHT be transient must keep its retry — only a "
        "deterministic constraint violation answers 200")

    with SessionLocal() as db:
        rows = list(db.execute(select(PaymentEvent)).scalars().all())
    assert rows, "the failed event was not persisted at all"
    assert rows[-1].status == "failed"
    assert rows[-1].error == "RuntimeError", (
        "the stored reason is not the exception TYPE — str(exc) on this path can "
        "carry a statement and its bound parameters, and staff read this column")
    assert rows[-1].payload, "the failed event kept no payload to replay from"

def test_the_constraint_itself_is_untouched(make_org):
    """Keeping it is the deliberate half of #99: discovering two orgs on one
    billing customer late is worse than a loud refusal."""
    a, b = make_org(), make_org()
    _set(a["org_id"], paddle_customer_id="ctm_unique_p1")
    with SessionLocal() as db:
        o = db.get(Organization, uuid.UUID(str(b["org_id"])))
        o.paddle_customer_id = "ctm_unique_p1"
        with pytest.raises(Exception):
            db.commit()
        db.rollback()


# ── 4 · #57 · the exit is audited, on the customer's own trail ──────────────

def test_ending_an_evaluation_by_paying_is_recorded(make_org, make_staff, staff_login):
    """The grant writes `billing.redeem_evaluation`; the exit wrote nothing,
    though it is the larger change and the moment a prospect becomes a
    customer."""
    org = make_org()
    _lock_expired_evaluation(org["org_id"])
    who = make_staff(role="superadmin")
    staff = staff_login(who["email"], who["password"])
    r = staff.post(f"/admin/v1/organizations/{org['org_id']}/plan",
                   json={"plan": "pro"})
    assert r.status_code == 200, r.text

    rows = _actions(org["org_id"], "billing.evaluation_ended")
    assert len(rows) == 1, "ending the evaluation left no record"
    assert rows[0].target == "offer-p1", "the record does not name the offer"
    assert rows[0].detail.get("via") == "staff_activation"


def test_nothing_is_recorded_for_an_org_that_had_no_evaluation(
        make_org, make_staff, staff_login):
    """All four call sites run unconditionally and the helper is safe on an org
    that never had an offer, so recording every time would tell most customers
    their evaluation ended when they never had one — fabricated history on the
    one page that exists to be trustworthy."""
    org = make_org()
    who = make_staff(role="superadmin")
    staff_login(who["email"], who["password"]).post(
        f"/admin/v1/organizations/{org['org_id']}/plan", json={"plan": "pro"})
    assert _actions(org["org_id"], "billing.evaluation_ended") == []


def test_the_record_goes_on_the_customers_trail_not_the_staffs(
        make_org, login, make_staff, staff_login):
    """`record_admin_action` answers "which staff member did this";
    `record_account_action` answers "what happened to my workspace". When a
    customer buys, no staff member was involved — and it is the trail the GRANT
    is already on, so the exit belongs beside it. The customer can read it."""
    org = make_org()
    _lock_expired_evaluation(org["org_id"])
    who = make_staff(role="superadmin")
    staff_login(who["email"], who["password"]).post(
        f"/admin/v1/organizations/{org['org_id']}/plan", json={"plan": "pro"})

    c = login(org["admin_email"], org["admin_password"])
    body = c.get("/v1/account/audit").json()
    actions = [a["action"] for a in (body if isinstance(body, list) else body.get("items", []))]
    assert "billing.evaluation_ended" in actions, (
        "the customer cannot see the end of their own evaluation")


def test_a_paddle_purchase_records_the_end_too(client, make_org, monkeypatch):
    """The webhook path, where no human is present at all."""
    _configure_paddle(monkeypatch)
    org = make_org()
    _lock_expired_evaluation(org["org_id"])
    # A contact email ON PURPOSE. Without one the fixture leaves the column NULL,
    # and a mutation that names the billing contact as the actor is then
    # indistinguishable from None — the assertion below passed for the wrong
    # reason until this line existed.
    _set(org["org_id"], contact_email="billing@example.test")
    data = {
        "id": "txn_p3_" + uuid.uuid4().hex[:10], "status": "completed",
        "origin": "web", "customer_id": "ctm_p3", "subscription_id": "sub_p3",
        "currency_code": "USD", "custom_data": {"foxy_org_id": str(org["org_id"])},
        "items": [{"price": {"id": "pri_p1_pro"}, "quantity": 1}],
        "details": {"totals": {"grand_total": "4900"}},
    }
    assert _post_paddle(client, data).status_code == 200
    rows = _actions(org["org_id"], "billing.evaluation_ended")
    assert len(rows) == 1
    assert rows[0].detail.get("via") == "paddle_purchase"
    assert rows[0].actor_email is None, (
        "a webhook named an actor it cannot know — which admin clicked buy is "
        "not something the processor tells us")


def test_no_router_clears_an_evaluation_without_recording_it():
    """The A6 lesson: a perfect helper nobody calls is as broken as no helper.

    `end_evaluation` stays pure and public because tests and the staff path use
    it, so the thing that keeps the audit honest is that no ROUTER reaches for
    the bare version — the wrapper is what binds reading the offer id to
    clearing it, in that order.
    """
    import pathlib
    import re

    routers = pathlib.Path(auth_mod.__file__).parent / "routers"
    offenders = []
    for path in routers.glob("*.py"):
        src = re.sub(r'""".*?"""', "", path.read_text(encoding="utf-8"), flags=re.S)
        src = re.sub(r"(?m)^\s*#.*$", "", src)
        for m in re.finditer(r"end_evaluation(?!_audited)\s*\(", src):
            offenders.append(f"{path.name}:{src[:m.start()].count(chr(10)) + 1}")
    assert not offenders, (
        f"these call the unaudited helper directly: {offenders}")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
