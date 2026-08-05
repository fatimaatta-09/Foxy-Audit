"""Staff activation actually unlocks a customer who paid — M0.

THE DEFECT
----------
`POST /admin/v1/organizations/{id}/plan` sets the tier, the quota, the trial and
`subscription_status="active"`. It did not touch the four evaluation fields, and
`billing_state.dashboard_lock()` tries `evaluation_lock()` FIRST. So for an org
whose evaluation window had expired, staff could set them to `pro` and the
dashboard stayed locked and capture stayed refused: the customer paid and
nothing changed. E1 fixed this for the Stripe webhook only.

That org — an evaluator whose trial ran out — is the single most likely first
paying customer, and the invoice-and-activate path is the one that takes money
without a payment processor at all.

WHAT THESE GUARDS ASSERT, AND WHY IT IS THE OUTCOME
---------------------------------------------------
A6 shipped a guard that read a helper's body and stayed green while the caller
stopped calling it. So nothing here inspects `end_evaluation`. Every test below
drives the real route and then asks the two questions a paying customer asks:
can I open my dashboard, and is my evidence being recorded. Per the playbook,
they assert `== 200` / `== 202` — what we want — never `!= 402`, which a 405 on
a mistyped route satisfies just as happily.

Note the ingest route is `POST /v1/logs/batch`. `POST /v1/logs` does not exist
(`/v1/logs` is GET-only) and asserting against it is exactly the trap D1 left
behind in `test_sdk_ingest_is_never_card_gated`.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db import SessionLocal
from app.models import AdminAction, EvaluationRedemption, Organization


def _rows(count: int = 1) -> list[dict]:
    return [
        {
            "prompt_hash": hashlib.sha256(f"{uuid.uuid4()}".encode()).hexdigest(),
            "response_hash": hashlib.sha256(f"r-{uuid.uuid4()}".encode()).hexdigest(),
            "token_count": 7,
            "policy_tag": "staff_activation_test",
            "agent": "activation-demo",
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
    """Exactly what signup and redeem apply for a live evaluation offer."""
    _patch_org(
        org_id,
        plan_tier="premium", trial_ends_at=None, monthly_log_quota=None,
        evaluation_offer_id="m0-activation-test", evaluation_credit_limit=credits,
        evaluation_credits_used=used,
        evaluation_ends_at=datetime.now(timezone.utc) + timedelta(days=days),
    )


def _expire(org_id) -> None:
    _patch_org(org_id, evaluation_ends_at=datetime.now(timezone.utc) - timedelta(seconds=1))


def _staff(make_staff, staff_login):
    who = make_staff(role="superadmin")
    return staff_login(who["email"], who["password"])


def _actions(org_id, action: str = "org.plan.set") -> list[AdminAction]:
    db = SessionLocal()
    try:
        return list(db.execute(
            select(AdminAction)
            .where(AdminAction.target_org_id == uuid.UUID(str(org_id)))
            .where(AdminAction.action == action)
        ).scalars().all())
    finally:
        db.close()


# ── 1 · the one that matters ─────────────────────────────────────────────────

def test_staff_activation_unlocks_an_expired_evaluator(
        client, make_org, login, make_staff, staff_login):
    """THE regression test. Everything else here is scaffolding for this one.

    The customer pays by invoice, staff sets the plan, and the workspace has to
    actually open. Both halves are asserted: the dashboard AND capture, because
    they are separate gates (`dashboard_lock` vs `capture_block`) and clearing
    the fields is what releases both.
    """
    org = make_org()
    _grant(org["org_id"])
    _expire(org["org_id"])

    customer = login(org["admin_email"], org["admin_password"])
    # Prove the org really is in the broken state first, or the test could pass
    # against an org that was never locked.
    assert customer.get("/v1/usage").status_code == 402
    refused = client.post("/v1/logs/batch", json=_rows(), headers=org["auth"])
    assert refused.status_code == 402
    assert refused.json()["detail"]["code"] == "evaluation_expired"

    r = _staff(make_staff, staff_login).post(
        f"/admin/v1/organizations/{org['org_id']}/plan", json={"plan": "pro"})
    assert r.status_code == 200, r.text
    assert r.json()["plan_tier"] == "pro"

    assert customer.get("/v1/usage").status_code == 200, "the dashboard must open"
    accepted = client.post("/v1/logs/batch", json=_rows(), headers=org["auth"])
    assert accepted.status_code == 202, accepted.text


def test_billing_access_stops_reporting_the_evaluation_after_activation(
        make_org, login, make_staff, staff_login):
    """The banner the dashboard renders comes from `/v1/billing/access`, not from
    whether a route happens to answer. It has to agree."""
    from app import billing_state

    org = make_org()
    _grant(org["org_id"])
    _expire(org["org_id"])
    customer = login(org["admin_email"], org["admin_password"])
    assert customer.get("/v1/billing/access").json()["reason"] == "evaluation_expired"

    _staff(make_staff, staff_login).post(
        f"/admin/v1/organizations/{org['org_id']}/plan", json={"plan": "pro"})

    body = customer.get("/v1/billing/access").json()
    assert body["locked"] is False
    assert body["reason"] == billing_state.NONE
    assert body["capture_blocked"] is False


def test_the_four_evaluation_fields_are_cleared_on_the_row(
        make_org, make_staff, staff_login):
    """Named individually. A helper that cleared three of four would still let
    `evaluation_lock` fire, and the outcome tests above would catch it — but this
    one says which field, which is what a bisect needs."""
    org = make_org()
    _grant(org["org_id"], credits=10, used=4)
    _expire(org["org_id"])

    _staff(make_staff, staff_login).post(
        f"/admin/v1/organizations/{org['org_id']}/plan", json={"plan": "max"})

    row = _org_row(org["org_id"])
    assert row.evaluation_offer_id is None
    assert row.evaluation_credit_limit is None
    assert row.evaluation_credits_used == 0
    assert row.evaluation_ends_at is None
    assert row.plan_tier == "max", "the tier and the fields only ever move together"


# ── 2 · what must NOT change ─────────────────────────────────────────────────

def test_a_never_evaluated_org_is_unaffected(
        client, make_org, login, make_staff, staff_login):
    """The population this change must not touch: an ordinary tenant with no
    offer in its history. Every evaluation field was already NULL, so clearing
    them is a no-op, and the route's existing behaviour is unchanged."""
    org = make_org()
    _patch_org(org["org_id"], plan_tier="pro", subscription_status="active",
               monthly_log_quota=None)
    customer = login(org["admin_email"], org["admin_password"])
    assert customer.get("/v1/usage").status_code == 200

    r = _staff(make_staff, staff_login).post(
        f"/admin/v1/organizations/{org['org_id']}/plan",
        json={"plan": "max", "monthly_log_quota": 4242})
    assert r.status_code == 200, r.text
    assert r.json()["monthly_log_quota"] == 4242

    row = _org_row(org["org_id"])
    assert row.plan_tier == "max"
    assert row.monthly_log_quota == 4242
    assert row.subscription_status == "active"
    assert row.trial_ends_at is None
    assert row.evaluation_offer_id is None
    assert customer.get("/v1/usage").status_code == 200
    assert client.post("/v1/logs/batch", json=_rows(),
                       headers=org["auth"]).status_code == 202


def test_the_evaluation_redemption_row_survives_staff_activation(
        make_org, make_staff, staff_login):
    """`models.py` puts a UNIQUE on `org_id` ALONE — one redemption per org, ever.
    That row is the record the offer happened. Deleting it silently re-arms a
    second redemption, which is why `end_evaluation` clears the org's live fields
    and nothing else."""
    org = make_org()
    _grant(org["org_id"])
    _expire(org["org_id"])
    db = SessionLocal()
    try:
        db.add(EvaluationRedemption(
            offer_id="m0-activation-test", org_id=uuid.UUID(org["org_id"]),
            email_hash=hashlib.sha256(b"paid@customer.test").hexdigest(),
            credits_granted=10,
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
        db.commit()
    finally:
        db.close()

    _staff(make_staff, staff_login).post(
        f"/admin/v1/organizations/{org['org_id']}/plan", json={"plan": "pro"})

    db = SessionLocal()
    try:
        rows = db.execute(select(EvaluationRedemption).where(
            EvaluationRedemption.org_id == uuid.UUID(org["org_id"]))).scalars().all()
    finally:
        db.close()
    assert len(rows) == 1 and rows[0].offer_id == "m0-activation-test"


def test_clearing_a_LIVE_offer_cannot_make_an_org_worse_off(
        client, make_org, login, make_staff, staff_login):
    """The plan's assumption 1, tested rather than argued.

    Staff can set a plan on an org whose offer has NOT expired, and M0 clears
    the fields there too. That is safe in one direction only, and it is the
    direction that matters: these four fields can add `evaluation_expired` or
    `evaluation_credits_exhausted` to `capture_block`, and can never lift a
    block. So an org that could capture before can still capture after.

    Set up the strictest live case — a live window with every credit already
    spent, which blocks capture today — and prove activation releases it.
    """
    org = make_org()
    _grant(org["org_id"], credits=3, used=3, days=30.0)
    customer = login(org["admin_email"], org["admin_password"])
    assert customer.get("/v1/usage").status_code == 200      # live offer: not locked
    exhausted = client.post("/v1/logs/batch", json=_rows(), headers=org["auth"])
    assert exhausted.status_code == 402
    assert exhausted.json()["detail"]["code"] == "evaluation_credits_exhausted"

    _staff(make_staff, staff_login).post(
        f"/admin/v1/organizations/{org['org_id']}/plan", json={"plan": "pro"})

    assert customer.get("/v1/usage").status_code == 200
    assert client.post("/v1/logs/batch", json=_rows(),
                       headers=org["auth"]).status_code == 202


def test_the_stripe_upgrade_path_still_clears_the_same_fields(make_org):
    """`_upgrade_existing_org` now calls the shared helper instead of writing the
    four fields inline. Its behaviour must be bit-for-bit what E1 shipped —
    `test_evaluation_expiry_exit.py` owns the full proof; this is the seam."""
    from app.routers.billing import _handle_checkout

    org = make_org()
    _grant(org["org_id"])
    _expire(org["org_id"])
    db = SessionLocal()
    try:
        result, _ = _handle_checkout(db, {
            "customer": "cus_m0_seam", "subscription": "sub_m0_seam",
            "metadata": {"foxy_org_id": org["org_id"], "foxy_plan": "pro"}})
        db.commit()
    finally:
        db.close()
    assert result["status"] == "upgraded"
    row = _org_row(org["org_id"])
    assert row.evaluation_offer_id is None
    assert row.evaluation_credit_limit is None
    assert row.evaluation_credits_used == 0
    assert row.evaluation_ends_at is None


# ── 3 · recording what was paid ──────────────────────────────────────────────

def test_a_payment_reference_reaches_the_audit_trail(make_org, make_staff, staff_login):
    """The admin action IS the record of a manual payment — no `Invoice` row is
    written, because `invoices.stripe_invoice_id` is UNIQUE NOT NULL and means a
    Stripe invoice. `admin_audit_view` already renders and exports `detail`."""
    org = make_org()
    r = _staff(make_staff, staff_login).post(
        f"/admin/v1/organizations/{org['org_id']}/plan",
        json={"plan": "pro", "payment_reference": "payoneer-PR-90210"})
    assert r.status_code == 200, r.text

    rows = _actions(org["org_id"])
    assert len(rows) == 1
    assert rows[0].detail["payment_reference"] == "payoneer-PR-90210"
    assert rows[0].detail["plan"] == "pro"


def test_omitting_the_reference_stores_no_key_at_all(make_org, make_staff, staff_login):
    """Absent, not present-and-empty. A key holding "" reads as "we looked for a
    reference and there was none", which is a different claim from never having
    had one — and inventing a placeholder would be fabricated data."""
    org = make_org()
    staff = _staff(make_staff, staff_login)
    assert staff.post(f"/admin/v1/organizations/{org['org_id']}/plan",
                      json={"plan": "pro"}).status_code == 200
    assert "payment_reference" not in _actions(org["org_id"])[0].detail


def test_a_blank_reference_is_treated_as_omitted(make_org, make_staff, staff_login):
    """The console does not send the field when the input is empty, but a
    hand-rolled request can. Whitespace is not a reference."""
    org = make_org()
    staff = _staff(make_staff, staff_login)
    assert staff.post(f"/admin/v1/organizations/{org['org_id']}/plan",
                      json={"plan": "pro", "payment_reference": "   "}).status_code == 200
    assert "payment_reference" not in _actions(org["org_id"])[0].detail


def test_an_overlong_reference_is_refused_not_truncated(make_org, make_staff, staff_login):
    """422, not a silently shortened record. A truncated invoice number is a
    wrong invoice number, and this row is the only evidence of the payment."""
    org = make_org()
    staff = _staff(make_staff, staff_login)
    r = staff.post(f"/admin/v1/organizations/{org['org_id']}/plan",
                   json={"plan": "pro", "payment_reference": "x" * 129})
    assert r.status_code == 422
    assert _actions(org["org_id"]) == [], "a refused request records nothing"


def test_the_plan_route_is_still_step_up_gated(make_org, make_staff, staff_login):
    """M0 adds a field to a route that moves money-shaped state. The gate it
    already had must survive the change."""
    org = make_org()
    who = make_staff(role="superadmin")
    no_step_up = staff_login(who["email"], who["password"], with_step_up=False)
    r = no_step_up.post(f"/admin/v1/organizations/{org['org_id']}/plan",
                        json={"plan": "pro", "payment_reference": "PR-1"})
    assert r.status_code == 403
    assert r.json()["detail"] == "step_up_required"
    assert _actions(org["org_id"]) == []
