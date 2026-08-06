"""A customer can finally see what they paid (M3f · register #94).

Two kinds of customer pay us and neither saw a record of it. A Paddle card
customer's transaction lived in `payment_events`, which is a staff webhook log
rather than a receipt. A Payoneer customer's payment existed only as a
`payment_reference` on an `admin_actions` row — readable by STAFF since M3c, and
invisible to the person who actually paid.

`invoices` was widened rather than duplicated; the reasoning is in migration
0063's docstring. These guards are about the outcome on real rows: that both
kinds of payment appear, that neither is invented, that no amount is faked, and
that one org can never read another's.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, text

from app.config import get_settings
from app.db import SessionLocal
from app.models import Invoice, Organization

SECRET = "pdl_ntfset_m3f"
PRICE_PRO = "pri_m3f_pro"


# ── helpers ──────────────────────────────────────────────────────────────────

def _configure(monkeypatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "paddle_api_key", "sdbx_m3f")
    monkeypatch.setattr(s, "paddle_webhook_secret", SECRET)
    monkeypatch.setattr(s, "paddle_price_pro", PRICE_PRO)


def _sign(body: bytes) -> str:
    ts = int(time.time())
    mac = hmac.new(SECRET.encode(), f"{ts}:".encode() + body, hashlib.sha256).hexdigest()
    return f"ts={ts};h1={mac}"


def _txn(org_id: str | None, *, origin="api", total="4900", currency="USD",
         txn_id=None, customer="ctm_m3f", subscription="sub_m3f") -> dict:
    data: dict = {
        "id": txn_id or ("txn_m3f_" + uuid.uuid4().hex[:12]),
        "status": "completed", "origin": origin,
        "customer_id": customer, "subscription_id": subscription,
        "currency_code": currency,
        "custom_data": ({"foxy_org_id": str(org_id)} if org_id else {"foxy_plan": "pro"}),
        "items": [{"price": {"id": PRICE_PRO}, "quantity": 1}],
        "billing_period": {"starts_at": "2026-08-01T00:00:00Z",
                           "ends_at": "2026-09-01T00:00:00Z"},
    }
    if total is not None:
        data["details"] = {"totals": {"grand_total": total}}
    return data


def _post(client, data: dict, event_type="transaction.completed"):
    body = json.dumps({
        "event_id": "evt_m3f_" + uuid.uuid4().hex[:20], "event_type": event_type,
        "occurred_at": datetime.now(timezone.utc).isoformat(), "data": data,
    }).encode()
    return client.post("/v1/webhooks/paddle", content=body,
                       headers={"paddle-signature": _sign(body),
                                "content-type": "application/json"})


def _payments(org_id=None) -> list[Invoice]:
    db = SessionLocal()
    try:
        q = select(Invoice)
        if org_id:
            q = q.where(Invoice.org_id == uuid.UUID(str(org_id)))
        return list(db.execute(q.order_by(Invoice.created_at)).scalars().all())
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


def _staff(make_staff, staff_login):
    who = make_staff(role="superadmin")
    return staff_login(who["email"], who["password"])


# ── 1 · a card payment becomes a receipt ────────────────────────────────────

def test_a_paddle_purchase_is_visible_to_the_customer(client, make_org, login,
                                                      monkeypatch):
    _configure(monkeypatch)
    org = make_org()
    assert login(org["admin_email"], org["admin_password"]).get(
        "/v1/invoices").json() == [], "an org that never paid must show nothing"

    assert _post(client, _txn(org["org_id"])).json()["status"] == "upgraded"

    body = login(org["admin_email"], org["admin_password"]).get("/v1/invoices").json()
    assert len(body) == 1, body
    row = body[0]
    assert row["provider"] == "paddle"
    assert row["amount_cents"] == 4900
    assert row["currency"] == "usd"
    assert row["status"] == "paid"
    assert row["reference"].startswith("txn_")
    assert row["stripe_invoice_id"] is None, "a Paddle id must not be written there"
    assert row["period_start"] and row["period_end"]


def test_a_renewal_is_a_receipt_too(client, make_org, monkeypatch):
    """Without this a customer sees month one and then silence."""
    _configure(monkeypatch)
    org = make_org()
    _patch_org(org["org_id"], plan_tier="pro")
    assert _post(client, _txn(org["org_id"])).json()["status"] == "upgraded"
    assert _post(client, _txn(org["org_id"], origin="subscription_recurring")
                 ).json()["status"] == "renewed"
    assert len(_payments(org["org_id"])) == 2


def test_the_same_transaction_never_becomes_two_receipts(client, make_org, monkeypatch):
    """`(provider, provider_ref)` is UNIQUE, so a replayed webhook and a staff
    replay land on the row that is already there."""
    _configure(monkeypatch)
    org = make_org()
    fixed = "txn_m3f_fixed_id"
    assert _post(client, _txn(org["org_id"], txn_id=fixed)).json()["status"] == "upgraded"
    # A DIFFERENT event id carrying the SAME transaction — the payment_events
    # dedupe cannot help here, so only the receipt's own key can.
    assert _post(client, _txn(org["org_id"], txn_id=fixed)).status_code == 200
    assert len(_payments(org["org_id"])) == 1


def test_the_amount_is_stored_in_minor_units_untouched(client, make_org, monkeypatch):
    """¥1000 IS ¥1000. Dividing on the way in understates yen 100x — the exact
    failure `desktop/billing_data.py`'s ZERO_DECIMAL table exists to prevent."""
    _configure(monkeypatch)
    org = make_org()
    assert _post(client, _txn(org["org_id"], total="1000", currency="JPY")
                 ).json()["status"] == "upgraded"
    row = _payments(org["org_id"])[0]
    assert row.amount_cents == 1000, "the amount was scaled on the way in"
    assert row.currency == "jpy"


def test_a_transaction_with_no_total_records_no_amount(client, make_org, monkeypatch):
    """A dash, not a zero. Zero would tell the customer they paid nothing."""
    _configure(monkeypatch)
    org = make_org()
    assert _post(client, _txn(org["org_id"], total=None)).json()["status"] == "upgraded"
    assert _payments(org["org_id"])[0].amount_cents is None


# ── 2 · a payment taken outside a processor becomes a receipt ───────────────

def test_a_staff_recorded_reference_reaches_the_customer(make_org, login, make_staff,
                                                         staff_login):
    """The loop M0 opened and M3c half-closed: the reference a staff member types
    was readable by STAFF and invisible to the person who paid."""
    org = make_org()
    r = _staff(make_staff, staff_login).post(
        f"/admin/v1/organizations/{org['org_id']}/plan",
        json={"plan": "pro", "payment_reference": "payoneer-INV-2026-0041"})
    assert r.status_code == 200, r.text

    body = login(org["admin_email"], org["admin_password"]).get("/v1/invoices").json()
    assert len(body) == 1
    row = body[0]
    assert row["provider"] == "manual"
    assert row["reference"] == "payoneer-INV-2026-0041"
    assert row["status"] == "paid"
    assert row["amount_cents"] is None, (
        "an amount nobody recorded must be null, never zero"
    )
    assert row["stripe_invoice_id"] is None


def test_activating_a_plan_without_a_reference_invents_nothing(make_org, login,
                                                               make_staff, staff_login):
    """No reference, no payment to record. An org with no payments has an empty
    list and that is the honest answer for it."""
    org = make_org()
    assert _staff(make_staff, staff_login).post(
        f"/admin/v1/organizations/{org['org_id']}/plan",
        json={"plan": "pro"}).status_code == 200
    assert login(org["admin_email"], org["admin_password"]).get("/v1/invoices").json() == []


def test_recording_the_same_reference_twice_is_one_receipt(make_org, make_staff,
                                                           staff_login):
    """Staff pressing Apply twice must not show the customer two payments."""
    org = make_org()
    staff = _staff(make_staff, staff_login)
    for _ in range(2):
        assert staff.post(f"/admin/v1/organizations/{org['org_id']}/plan",
                          json={"plan": "pro",
                                "payment_reference": "payoneer-INV-9"}).status_code == 200
    assert len(_payments(org["org_id"])) == 1


def test_a_transaction_with_no_id_records_nothing(client, make_org, monkeypatch):
    """No id means no idempotency key, and a receipt that can duplicate itself on
    every delivery is worse than no receipt. Found by re-breaking: the early
    return had no guard, so removing it silently produced un-deduplicable rows."""
    _configure(monkeypatch)
    org = make_org()
    data = _txn(org["org_id"])
    data["id"] = ""
    assert _post(client, data).status_code == 200
    assert _payments(org["org_id"]) == [], (
        "a transaction with no id produced a receipt that cannot be deduplicated"
    )

# ── 3 · isolation ───────────────────────────────────────────────────────────

def test_a_customer_cannot_read_another_orgs_payments(make_org, login, make_staff,
                                                      staff_login):
    """THE isolation guard. `invoices` is FORCE ROW LEVEL SECURITY with an
    `org_isolation` policy, and the route also filters by org_id — two layers,
    because the app connects as a superuser that bypasses RLS and the explicit
    WHERE is therefore load-bearing."""
    mine, theirs = make_org(), make_org()
    staff = _staff(make_staff, staff_login)
    for org, ref in ((mine, "payoneer-MINE"), (theirs, "payoneer-THEIRS")):
        assert staff.post(f"/admin/v1/organizations/{org['org_id']}/plan",
                          json={"plan": "pro", "payment_reference": ref}
                          ).status_code == 200

    body = login(mine["admin_email"], mine["admin_password"]).get("/v1/invoices").json()
    assert [r["reference"] for r in body] == ["payoneer-MINE"], body
    assert "payoneer-THEIRS" not in json.dumps(body)

    other = login(theirs["admin_email"], theirs["admin_password"]).get(
        "/v1/invoices").json()
    assert [r["reference"] for r in other] == ["payoneer-THEIRS"], other


def test_the_row_is_reachable_only_by_its_own_org_under_the_confined_role(make_org,
                                                                          make_staff,
                                                                          staff_login):
    """The RLS layer on its own, with the app's superuser dropped for the confined
    `foxy_app` role — which is what the policy actually protects. If the policy
    were lost, the route's WHERE would still hide the row and this is the guard
    that would notice."""
    mine, theirs = make_org(), make_org()
    staff = _staff(make_staff, staff_login)
    for org, ref in ((mine, "rls-MINE"), (theirs, "rls-THEIRS")):
        staff.post(f"/admin/v1/organizations/{org['org_id']}/plan",
                   json={"plan": "pro", "payment_reference": ref})

    db = SessionLocal()
    try:
        db.execute(text("SELECT set_config('app.current_org', :oid, true)"),
                   {"oid": str(mine["org_id"])})
        db.execute(text('SET LOCAL ROLE "foxy_app"'))
        seen = [r[0] for r in db.execute(text(
            "SELECT provider_ref FROM invoices WHERE provider_ref IS NOT NULL")).all()]
    finally:
        db.rollback()
        db.close()
    assert seen == ["rls-MINE"], f"RLS let the confined role see {seen}"


def test_a_reference_reused_across_orgs_never_lands_on_the_wrong_one(make_org, login,
                                                                     make_staff,
                                                                     staff_login, caplog):
    """`(provider, provider_ref)` is UNIQUE globally, which is right for a
    processor id and blunt for a hand-typed one. If a staff member reuses a
    reference, the second org must not inherit the first org's receipt — and the
    fact that it got none must reach the log rather than vanish."""
    a, b = make_org(), make_org()
    staff = _staff(make_staff, staff_login)
    for org in (a, b):
        staff.post(f"/admin/v1/organizations/{org['org_id']}/plan",
                   json={"plan": "pro", "payment_reference": "payoneer-DUP-1"})

    assert len(_payments(a["org_id"])) == 1
    assert _payments(b["org_id"]) == [], "the second org inherited a receipt"
    assert any("another org" in r.getMessage() for r in caplog.records), (
        "the dropped receipt was silent"
    )

def test_the_writer_scopes_itself_and_does_not_lean_on_the_superuser(make_org):
    """`record_payment` sets `app.current_org` before it inserts, and this proves
    that call is load-bearing rather than decorative.

    Both writers happen to run as the app superuser, which BYPASSES RLS — so
    removing the `set_config` changed nothing and every other guard stayed green
    (found by re-breaking it). The GUC matters the moment a session is already
    confined: `_scope_org` drops customer requests to `foxy_app`, and under that
    role an INSERT with no `app.current_org` fails the policy's CHECK. Running
    the writer on a confined session is the only way to make that visible.
    """
    from app import billing_state

    org = make_org()
    db = SessionLocal()
    try:
        db.execute(text('SET LOCAL ROLE "foxy_app"'))   # no GUC set on purpose
        billing_state.record_payment(db, db.get(Organization,
                                                uuid.UUID(str(org["org_id"]))),
                                     provider="manual", reference="scope-probe",
                                     status="paid")
        db.flush()
    finally:
        db.rollback()
        db.close()

def test_the_route_still_filters_by_org_itself(make_org):
    """Isolation here is TWO layers and the explicit filter is the first one.

    Removing the `WHERE org_id` survives the behavioural guard above, because a
    customer request runs under the confined `foxy_app` role and RLS catches it —
    which is the design working, not a reason to drop the clause. The Backend
    note states the rule plainly: never remove an explicit org filter because
    "RLS covers it", since staff and worker paths do not run under that role.
    So this asserts the clause itself, at the source.
    """
    import inspect

    from app.routers import account

    src = inspect.getsource(account.list_invoices)
    assert "Invoice.org_id == org.id" in src, (
        "the invoice route no longer filters by org; it is relying on RLS alone"
    )

# ── 4 · what the record must not carry ──────────────────────────────────────

def test_no_processor_payload_reaches_the_customer(client, make_org, login,
                                                   monkeypatch):
    """#102 was opened this week because `payload` was reachable through the
    STAFF data browser. A customer-visible table is a worse place for it."""
    _configure(monkeypatch)
    org = make_org()
    data = _txn(org["org_id"])
    data["customer_email"] = "marie.dubois@private-clinic.example"
    data["billing_details"] = {"address": {"first_line": "12 Rue de la Paix"}}
    assert _post(client, data).json()["status"] == "upgraded"

    text_body = login(org["admin_email"], org["admin_password"]).get("/v1/invoices").text
    assert "marie.dubois" not in text_body
    assert "Rue de la Paix" not in text_body
    assert "payload" not in text_body
    row = _payments(org["org_id"])[0]
    assert not hasattr(row, "payload")


def test_the_response_keeps_the_shape_shipped_clients_read(client, make_org, login,
                                                           monkeypatch):
    """`desktop/billing_data.invoice_rows` and the dashboard's table both read
    date/amount/currency/status/period. This phase adds fields; it must not
    remove or rename one."""
    _configure(monkeypatch)
    org = make_org()
    _post(client, _txn(org["org_id"]))
    row = login(org["admin_email"], org["admin_password"]).get("/v1/invoices").json()[0]
    for field in ("id", "amount_cents", "currency", "status",
                  "period_start", "period_end", "created_at", "stripe_invoice_id"):
        assert field in row, f"{field} disappeared from a response two clients read"
    for added in ("provider", "reference"):
        assert added in row
