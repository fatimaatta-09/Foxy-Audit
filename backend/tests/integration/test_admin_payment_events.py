"""Paddle's event log, its replay, and the payload that must never leave (M3d).

Registers #98, #102 and #103. Paddle is the processor that actually takes money
here; Stripe has never taken a payment and has had a full events page with replay
since P4. Paddle had neither, so a `failed` row was unrecoverable once Paddle
stopped retrying — a customer who has paid and was never upgraded, with no button
anywhere.

The payload guard is the one that matters most. `payment_events.payload` is the
raw Paddle body and carries the customer's name, email and billing address. It is
asserted by planting a marker inside a stored payload and proving the marker
cannot be found in any response — so deleting the route's explicit field list, or
swapping it for a generic serializer, turns this red rather than green.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models import AdminAction, Organization, PaymentEvent

#: Planted inside a stored payload. If this string ever appears in a response,
#: a customer's details have leaked out of the server.
PII_MARKER = "marie.dubois@private-clinic.example"
PRICE_PRO = "pri_m3d_pro"


# ── helpers ──────────────────────────────────────────────────────────────────

def _configure(monkeypatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "paddle_api_key", "sdbx_m3d")
    monkeypatch.setattr(s, "paddle_price_pro", PRICE_PRO)


def _txn(org_id: str | None, *, origin="api", customer="ctm_m3d",
         subscription="sub_m3d") -> dict:
    return {
        "id": "txn_m3d_" + uuid.uuid4().hex[:12], "status": "completed",
        "origin": origin, "customer_id": customer, "subscription_id": subscription,
        "customer_email": PII_MARKER,
        "custom_data": ({"foxy_org_id": str(org_id), "foxy_plan": "pro"}
                        if org_id else {"foxy_plan": "pro"}),
        "items": [{"price": {"id": PRICE_PRO}, "quantity": 1}],
        "billing_details": {"address": {"first_line": "12 Rue de la Paix",
                                        "city": "Paris", "postal_code": "75002"}},
    }


def _store_event(data: dict, *, status="failed", etype="transaction.completed",
                 provider="paddle") -> str:
    """A payment_events row exactly as the webhook writes one."""
    eid = uuid.uuid4()
    envelope = {"event_id": "evt_m3d_" + uuid.uuid4().hex[:16], "event_type": etype,
                "occurred_at": datetime.now(timezone.utc).isoformat(), "data": data}
    db = SessionLocal()
    try:
        db.add(PaymentEvent(id=eid, provider=provider,
                            provider_event_id=envelope["event_id"], type=etype,
                            payload=envelope, status=status))
        db.commit()
    finally:
        db.close()
    return str(eid)


def _event(event_id: str) -> PaymentEvent:
    db = SessionLocal()
    try:
        return db.get(PaymentEvent, uuid.UUID(event_id))
    finally:
        db.close()


def _org(org_id) -> Organization:
    db = SessionLocal()
    try:
        return db.get(Organization, uuid.UUID(str(org_id)))
    finally:
        db.close()


def _patch_org(org_id, **fields) -> None:
    db = SessionLocal()
    try:
        o = db.get(Organization, uuid.UUID(str(org_id)))
        for k, v in fields.items():
            setattr(o, k, v)
        db.commit()
    finally:
        db.close()


def _staff(make_staff, staff_login, role="operator", **kw):
    who = make_staff(role=role)
    return staff_login(who["email"], who["password"], **kw)


def _actions(action="payment.replay") -> list[AdminAction]:
    db = SessionLocal()
    try:
        return list(db.execute(
            select(AdminAction).where(AdminAction.action == action)).scalars().all())
    finally:
        db.close()


# ── 1 · the payload never leaves the server (#102) ──────────────────────────

def test_the_event_list_never_returns_the_raw_payload(make_staff, staff_login):
    """THE guard. A Paddle payload carries the customer's name, email and billing
    address, which is why this route builds every item from an explicit field
    list instead of being a row in admin_data's registry.

    Proved with a marker planted in a stored payload rather than by reading the
    code: deleting the field list, or swapping it for a generic serializer, makes
    the marker appear and turns this red.
    """
    _store_event(_txn(None), status="processed")
    r = _staff(make_staff, staff_login, role="viewer").get(
        "/admin/v1/billing/payment-events")
    assert r.status_code == 200, r.text
    assert PII_MARKER not in r.text, "a customer's email leaked out of the event log"
    assert "Rue de la Paix" not in r.text, "a customer's address leaked"
    assert "payload" not in r.text


def test_the_event_list_returns_exactly_the_intended_fields(make_staff, staff_login):
    """Named explicitly, so a column added to the model later is invisible here
    until somebody decides it should not be."""
    _store_event(_txn(None), status="processed")
    body = _staff(make_staff, staff_login, role="viewer").get(
        "/admin/v1/billing/payment-events").json()
    assert set(body["items"][0]) == {
        "id", "provider", "provider_event_id", "type", "status", "error",
        "org_id", "received_at", "processed_at"}


def test_payment_events_is_not_in_the_generic_browser() -> None:
    """The cheap option, deliberately not taken: that browser returns every
    column minus a denylist."""
    from app.routers.admin_data import TABLE_REGISTRY
    assert "payment_events" not in TABLE_REGISTRY


def test_the_generic_browser_no_longer_returns_any_payload(make_staff, staff_login):
    """`stripe_events.payload` had the same exposure — dormant, because no Stripe
    payload has ever been written, but real. `_NEVER_EXPOSE` is global and
    `stripe_events` is the only registered table with a column of that name."""
    from app.routers.admin_data import _NEVER_EXPOSE
    assert "payload" in _NEVER_EXPOSE
    r = _staff(make_staff, staff_login, role="viewer").get(
        "/admin/v1/data/stripe_events?limit=5")
    assert r.status_code == 200, r.text
    assert "payload" not in r.text


# ── 2 · access ──────────────────────────────────────────────────────────────

def test_the_list_is_viewer_gated(client):
    assert client.get("/admin/v1/billing/payment-events").status_code == 401


def test_replay_needs_operator(make_staff, staff_login):
    ev = _store_event(_txn(None))
    r = _staff(make_staff, staff_login, role="viewer").post(
        f"/admin/v1/billing/payment-events/{ev}/replay")
    assert r.status_code == 403


def test_replay_needs_step_up(make_staff, staff_login):
    """Replay re-runs a money-shaped handler. It carries the same step-up gate
    every other irreversible staff action does."""
    ev = _store_event(_txn(None))
    who = make_staff(role="operator")
    no_step_up = staff_login(who["email"], who["password"], with_step_up=False)
    r = no_step_up.post(f"/admin/v1/billing/payment-events/{ev}/replay")
    assert r.status_code == 403
    assert r.json()["detail"] == "step_up_required"


# ── 3 · replay repairs the case it exists for (#103) ────────────────────────

def test_replaying_a_failed_purchase_upgrades_the_customer(
        make_org, make_staff, staff_login, monkeypatch):
    """The whole point. Paddle delivered, the handler failed, Paddle gave up, and
    a customer who paid was never upgraded."""
    _configure(monkeypatch)
    org = make_org()
    _patch_org(org["org_id"], plan_tier="free")
    ev = _store_event(_txn(org["org_id"]), status="failed")

    r = _staff(make_staff, staff_login).post(
        f"/admin/v1/billing/payment-events/{ev}/replay")
    assert r.status_code == 200, r.text
    assert r.json()["result"] == "upgraded"

    assert _org(org["org_id"]).plan_tier == "pro"
    row = _event(ev)
    assert row.status == "processed"
    assert row.error is None
    assert row.processed_at is not None
    assert str(row.org_id) == org["org_id"]


def test_a_replay_is_audited(make_org, make_staff, staff_login, monkeypatch):
    _configure(monkeypatch)
    org = make_org()
    ev = _store_event(_txn(org["org_id"]), status="failed")
    _staff(make_staff, staff_login).post(
        f"/admin/v1/billing/payment-events/{ev}/replay")
    rows = _actions()
    assert len(rows) == 1
    assert rows[0].detail["provider"] == "paddle"
    assert rows[0].detail["type"] == "transaction.completed"
    assert rows[0].target_type == "payment_event"


# ── 4 · what a SECOND run actually does ─────────────────────────────────────

def test_replaying_an_already_processed_event_is_refused(
        make_org, make_staff, staff_login, monkeypatch):
    """The deliberate divergence from the Stripe route, which accepts any row.

    A replay re-runs the handler for a row that already exists, bypassing the
    `ON CONFLICT DO NOTHING` that makes double delivery safe. For a purchase that
    is harmless; for a SUBSCRIPTION event it applies an old state over anything
    newer. `processed` and `ignored` already did their job.
    """
    _configure(monkeypatch)
    org = make_org()
    ev = _store_event(_txn(org["org_id"]), status="processed")
    r = _staff(make_staff, staff_login).post(
        f"/admin/v1/billing/payment-events/{ev}/replay")
    assert r.status_code == 409
    assert "processed" in r.json()["detail"]


def test_a_replay_cannot_be_run_twice(make_org, make_staff, staff_login, monkeypatch):
    """The first replay stamps the row `processed`, which puts it out of reach of
    the second. That is the guard against a staff member clicking twice."""
    _configure(monkeypatch)
    org = make_org()
    ev = _store_event(_txn(org["org_id"]), status="failed")
    staff = _staff(make_staff, staff_login)
    assert staff.post(f"/admin/v1/billing/payment-events/{ev}/replay").status_code == 200
    assert staff.post(f"/admin/v1/billing/payment-events/{ev}/replay").status_code == 409


def test_replaying_a_purchase_over_an_upgraded_org_changes_nothing(
        make_org, make_staff, staff_login, monkeypatch):
    """The idempotency question, answered on real rows rather than asserted.

    The handler never calls Paddle — it only writes to our database — so a second
    run can neither charge nor refund. It writes the same tier, quota and status
    it wrote the first time, and provisions nothing because the customer id
    already resolves to an org.
    """
    _configure(monkeypatch)
    org = make_org()
    _patch_org(org["org_id"], plan_tier="pro",
               monthly_log_quota=get_settings().quota_for("pro"),
               subscription_status="active", paddle_customer_id="ctm_m3d",
               paddle_subscription_id="sub_m3d")
    before = _org(org["org_id"])
    snapshot = (before.plan_tier, before.monthly_log_quota,
                before.subscription_status, before.paddle_subscription_id)

    db = SessionLocal()
    try:
        n_before = len(db.execute(select(Organization.id)).scalars().all())
    finally:
        db.close()

    ev = _store_event(_txn(org["org_id"]), status="failed")
    r = _staff(make_staff, staff_login).post(
        f"/admin/v1/billing/payment-events/{ev}/replay")
    assert r.status_code == 200, r.text

    after = _org(org["org_id"])
    assert (after.plan_tier, after.monthly_log_quota, after.subscription_status,
            after.paddle_subscription_id) == snapshot
    db = SessionLocal()
    try:
        assert len(db.execute(select(Organization.id)).scalars().all()) == n_before, (
            "the replay forked a second workspace"
        )
    finally:
        db.close()


def test_replaying_an_anonymous_purchase_does_not_email_twice(
        client, make_staff, staff_login, monkeypatch):
    """The one thing a replay COULD do to a customer: send a second set-password
    invite. It does not — an org already bound to that customer id comes back
    `already_provisioned`, and only a genuinely new workspace is emailed."""
    _configure(monkeypatch)
    sent: list[str] = []
    import app.password_reset as pr
    original = pr.issue_reset
    monkeypatch.setattr(pr, "issue_reset",
                        lambda db, u, email, url, invite=False: (
                            sent.append(email), original(db, u, email, url, invite=invite))[1])

    data = _txn(None, customer="ctm_m3d_anon", subscription="sub_m3d_anon")
    data["customer_email"] = "walkup-m3d@test.dev"
    ev1 = _store_event(data, status="failed")
    staff = _staff(make_staff, staff_login)
    assert staff.post(f"/admin/v1/billing/payment-events/{ev1}/replay").json()["result"] \
        == "provisioned"
    assert sent == ["walkup-m3d@test.dev"], sent

    ev2 = _store_event(data, status="failed")
    r = staff.post(f"/admin/v1/billing/payment-events/{ev2}/replay")
    assert r.status_code == 200
    assert r.json()["result"] == "already_provisioned"
    assert sent == ["walkup-m3d@test.dev"], "the customer was emailed a second invite"


# ── 5 · failures say nothing about the customer ─────────────────────────────

def test_a_failing_replay_records_the_type_not_the_message(
        make_org, make_staff, staff_login, monkeypatch):
    """A handler's exception can quote the payload it choked on, and that payload
    holds the customer's name, email and address. The Stripe route stores
    `str(exc)` and echoes it in its 500 body; this one must not."""
    _configure(monkeypatch)
    org = make_org()
    ev = _store_event(_txn(org["org_id"]), status="failed")

    import app.routers.admin_billing as ab

    def _boom(db, data):
        raise RuntimeError(f"cannot parse {PII_MARKER} at 12 Rue de la Paix")
    monkeypatch.setattr(ab, "_paddle_apply_purchase", _boom)

    r = _staff(make_staff, staff_login).post(
        f"/admin/v1/billing/payment-events/{ev}/replay")
    assert r.status_code == 500
    assert PII_MARKER not in r.text and "Rue de la Paix" not in r.text
    row = _event(ev)
    assert row.status == "failed"
    assert row.error == "RuntimeError", row.error
    assert PII_MARKER not in json.dumps(_actions()[0].detail)


def test_an_unknown_event_id_is_404_and_a_bad_one_422(make_staff, staff_login):
    staff = _staff(make_staff, staff_login)
    assert staff.post(
        f"/admin/v1/billing/payment-events/{uuid.uuid4()}/replay").status_code == 404
    assert staff.post(
        "/admin/v1/billing/payment-events/not-a-uuid/replay").status_code == 422


# ── 6 · the list itself ─────────────────────────────────────────────────────

def test_the_list_filters_and_pages(make_staff, staff_login):
    for _ in range(3):
        _store_event(_txn(None), status="failed")
    _store_event(_txn(None), status="processed")
    staff = _staff(make_staff, staff_login, role="viewer")
    body = staff.get("/admin/v1/billing/payment-events?status=failed").json()
    assert body["total"] == 3
    assert all(i["status"] == "failed" for i in body["items"])
    page = staff.get("/admin/v1/billing/payment-events?limit=2&offset=0").json()
    assert len(page["items"]) == 2 and page["total"] == 4
    assert staff.get(
        "/admin/v1/billing/payment-events?provider=stripe").json()["total"] == 0
