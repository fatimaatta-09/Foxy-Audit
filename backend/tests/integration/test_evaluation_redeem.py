"""POST /v1/billing/redeem — an evaluation code claimed by an EXISTING org.

Signup already applies an offer to a brand-new org. The difference here is that
the org has a life already, and an evaluation offer is not purely additive: it
puts ingestion behind `evaluation_credits_exhausted` / `evaluation_expired` in
logs.py. So most of this file is about the endpoint refusing.
"""

from __future__ import annotations

import hashlib
import threading
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db import SessionLocal
from app.models import EvaluationCampaign, EvaluationRedemption, Organization

CODE = "ROUND-TWO-JUDGE-ACCESS-01"


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _rows(count: int) -> list[dict]:
    return [
        {
            "prompt_hash": _hash(f"redeem-prompt-{index}-{uuid.uuid4()}"),
            "response_hash": _hash(f"redeem-response-{index}"),
            "token_count": 12,
            "policy_tag": "redeem_test",
            "agent": "redeem-demo",
        }
        for index in range(count)
    ]


def _campaign(offer_id="round-two", code=CODE, credits=250, days=31, max_redemptions=5):
    """Create a campaign directly — the admin route is covered separately."""
    from app.routers.billing import _offer_code_hash

    db = SessionLocal()
    try:
        db.add(EvaluationCampaign(
            offer_id=offer_id, label="Round two", code_hash=_offer_code_hash(code),
            credits=credits, duration_days=days, max_redemptions=max_redemptions,
            status="active",
        ))
        db.commit()
    finally:
        db.close()


def _patch_org(org_id, **fields):
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


# ── the guards, each with its own reason ─────────────────────────────────────

def test_guard_refuses_a_paid_plan(make_org, login):
    """Guard 1. A gift must never silently replace a plan someone is paying for."""
    _campaign()
    org = make_org()
    _patch_org(org["org_id"], plan_tier="pro", subscription_status="active",
               stripe_subscription_id="sub_test123")
    r = login(org["admin_email"], org["admin_password"]).post(
        "/v1/billing/redeem", json={"code": CODE})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "paid_plan"
    assert _org_row(org["org_id"]).evaluation_offer_id is None


def test_guard_refuses_a_running_evaluation(make_org, login):
    """Guard 2. Offers do not stack — the second would silently reset the first."""
    _campaign()
    org = make_org()
    _patch_org(org["org_id"], evaluation_offer_id="something-else",
               evaluation_credit_limit=10,
               evaluation_ends_at=datetime.now(timezone.utc) + timedelta(days=3))
    r = login(org["admin_email"], org["admin_password"]).post(
        "/v1/billing/redeem", json={"code": CODE})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "evaluation_active"
    assert _org_row(org["org_id"]).evaluation_offer_id == "something-else"


def test_guard_refuses_a_second_redemption_for_the_org(make_org, login):
    """Guard 3, as the SCHEMA states it rather than as the plan stated it.

    evaluation_redemptions carries uq_evaluation_redemption_org — a UNIQUE on
    org_id alone — so an org may redeem exactly once, ever, not once per offer.
    A per-offer check would let a second campaign past the guard and then blow up
    on an IntegrityError instead of answering 409. This uses a DIFFERENT campaign
    from the one already redeemed, which is precisely the case the narrower guard
    would have missed.
    """
    _campaign()
    _campaign(offer_id="round-two-b", code="A-SECOND-CAMPAIGN-CODE-01")
    org = make_org()
    client = login(org["admin_email"], org["admin_password"])
    assert client.post("/v1/billing/redeem", json={"code": CODE}).status_code == 200

    # While the first offer is still running, the accurate reason is the no-stack
    # one — guard 2 is deliberately ahead of guard 3.
    running = client.post("/v1/billing/redeem", json={"code": "A-SECOND-CAMPAIGN-CODE-01"})
    assert running.status_code == 409
    assert running.json()["detail"]["code"] == "evaluation_active"

    # Once it lapses, the schema's one-per-org rule is what refuses. This is the
    # case a per-offer guard would have let through into an IntegrityError.
    _patch_org(org["org_id"], evaluation_ends_at=datetime.now(timezone.utc) - timedelta(days=1))
    r = client.post("/v1/billing/redeem", json={"code": "A-SECOND-CAMPAIGN-CODE-01"})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "already_redeemed"

    db = SessionLocal()
    try:
        count = db.execute(select(EvaluationRedemption)).scalars().all()
        assert len(count) == 1, "a second redemption row was written"
    finally:
        db.close()


def test_unknown_code_is_refused_without_disclosing_anything(make_org, login):
    _campaign()
    org = make_org()
    r = login(org["admin_email"], org["admin_password"]).post(
        "/v1/billing/redeem", json={"code": "NOT-A-REAL-CODE-AT-ALL"})
    assert r.status_code == 422
    assert r.json()["detail"] == "This evaluation offer is unavailable"


# ── the happy path ───────────────────────────────────────────────────────────

def test_a_clean_redeem_applies_the_offer_and_writes_one_row(make_org, login):
    _campaign(credits=250, days=31)
    org = make_org()
    r = login(org["admin_email"], org["admin_password"]).post(
        "/v1/billing/redeem", json={"code": CODE})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["credits_total"] == 250
    assert body["credits_remaining"] == 250
    assert body["no_auto_charge"] is True

    row = _org_row(org["org_id"])
    assert row.evaluation_offer_id == "round-two"
    assert row.evaluation_credit_limit == 250
    assert row.evaluation_credits_used == 0
    assert row.evaluation_ends_at is not None

    db = SessionLocal()
    try:
        redemptions = db.execute(select(EvaluationRedemption)).scalars().all()
        assert len(redemptions) == 1
        assert redemptions[0].credits_granted == 250
        assert str(redemptions[0].org_id) == str(org["org_id"])
    finally:
        db.close()


def test_the_offer_also_lifts_the_gates_that_would_mask_it(make_org, login):
    """The plan's guard 4 said to set ONLY the four evaluation fields. This is the
    test that guard would fail.

    logs.py gates capture in order: trial_expired → subscription_inactive →
    credits_exhausted (monthly_log_quota) → the evaluation pair. An org on the
    free tier past its trial never reaches the evaluation gate, so credits
    granted without clearing trial_ends_at can never be spent — the gift would be
    inert. Applying signup's exact field set is what makes it mean what it says.
    """
    _campaign(credits=5)
    org = make_org()
    _patch_org(org["org_id"], plan_tier="free", monthly_log_quota=1,
               trial_ends_at=datetime.now(timezone.utc) - timedelta(days=1))

    headers = org["auth"]
    blocked = client_post_logs(org, headers, 1)
    assert blocked.status_code == 402
    assert blocked.json()["detail"]["code"] == "trial_expired"

    assert login(org["admin_email"], org["admin_password"]).post(
        "/v1/billing/redeem", json={"code": CODE}).status_code == 200

    row = _org_row(org["org_id"])
    assert row.trial_ends_at is None, "the trial gate still masks the offer"
    assert row.monthly_log_quota is None, "the free quota still caps the offer"
    assert (row.plan_tier or "").lower() == "premium"

    accepted = client_post_logs(org, headers, 2)
    assert accepted.status_code == 202, accepted.text
    assert _org_row(org["org_id"]).evaluation_credits_used == 2


def test_capture_stops_when_the_granted_credits_run_out(make_org, login):
    """The trade the UI has to disclose: the evaluation cap becomes the only gate,
    and it is finite."""
    _campaign(credits=2)
    org = make_org()
    assert login(org["admin_email"], org["admin_password"]).post(
        "/v1/billing/redeem", json={"code": CODE}).status_code == 200
    assert client_post_logs(org, org["auth"], 2).status_code == 202
    blocked = client_post_logs(org, org["auth"], 1)
    assert blocked.status_code == 402
    assert blocked.json()["detail"]["code"] == "evaluation_credits_exhausted"


def client_post_logs(org, headers, count):
    from fastapi.testclient import TestClient

    from app.main import app
    with TestClient(app) as c:
        return c.post("/v1/logs/batch", json=_rows(count), headers=headers)


# ── campaign-side limits still hold on this path ─────────────────────────────

def test_max_redemptions_still_holds(make_org, login):
    _campaign(max_redemptions=1)
    first, second = make_org(), make_org()
    assert login(first["admin_email"], first["admin_password"]).post(
        "/v1/billing/redeem", json={"code": CODE}).status_code == 200
    r = login(second["admin_email"], second["admin_password"]).post(
        "/v1/billing/redeem", json={"code": CODE})
    assert r.status_code == 422
    assert _org_row(second["org_id"]).evaluation_offer_id is None


def test_the_per_email_cap_still_holds(make_org, login):
    """The cap keys on the redeeming user's email, so the same person cannot
    claim one campaign twice from two different workspaces."""
    _campaign(max_redemptions=5)
    first, second = make_org(), make_org()
    shared = "same-human@test.dev"
    db = SessionLocal()
    try:
        from app.models import User
        for org in (first, second):
            user = db.execute(select(User).where(
                User.email == org["admin_email"])).scalar_one()
            user.email = shared if org is first else org["admin_email"]
        db.commit()
    finally:
        db.close()

    assert login(shared, first["admin_password"]).post(
        "/v1/billing/redeem", json={"code": CODE}).status_code == 200

    db = SessionLocal()
    try:
        from app.models import User
        user = db.execute(select(User).where(
            User.email == second["admin_email"])).scalar_one()
        user.email = "second-" + shared
        db.commit()
    finally:
        db.close()
    # A different email on the same campaign is fine — proves the cap is on the
    # email, not on the campaign being spent.
    assert login("second-" + shared, second["admin_password"]).post(
        "/v1/billing/redeem", json={"code": CODE}).status_code == 200


def test_two_concurrent_redeems_of_the_last_slot_leave_one_winner(make_org, login):
    """pg_advisory_xact_lock in _claim_database_campaign is what makes this safe.
    Proven rather than assumed: two real threads, two sessions, one slot."""
    _campaign(max_redemptions=1)
    orgs = [make_org(), make_org()]
    clients = [login(o["admin_email"], o["admin_password"]) for o in orgs]
    results: list[int] = []
    barrier = threading.Barrier(2)

    def attempt(client):
        barrier.wait()
        results.append(client.post("/v1/billing/redeem", json={"code": CODE}).status_code)

    threads = [threading.Thread(target=attempt, args=(c,)) for c in clients]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert sorted(results) == [200, 422], f"expected exactly one winner, got {results}"
    db = SessionLocal()
    try:
        assert len(db.execute(select(EvaluationRedemption)).scalars().all()) == 1
    finally:
        db.close()


# ── step-up is enforced on both ends ─────────────────────────────────────────

def test_redeem_requires_step_up(make_org, login):
    _campaign()
    org = make_org()
    client = login(org["admin_email"], org["admin_password"], with_step_up=False)
    r = client.post("/v1/billing/redeem", json={"code": CODE})
    assert r.status_code == 403
    assert r.json()["detail"] == "step_up_required"
    assert _org_row(org["org_id"]).evaluation_offer_id is None


def test_creating_a_campaign_requires_step_up(make_staff, staff_login):
    superadmin = make_staff(role="superadmin")
    client = staff_login(superadmin["email"], superadmin["password"], with_step_up=False)
    payload = {"offer_id": "gated", "label": "Gated", "code": "A-CODE-LONG-ENOUGH-01",
               "credits": 10, "duration_days": 7, "max_redemptions": 1}
    r = client.post("/admin/v1/evaluation-campaigns", json=payload)
    assert r.status_code == 403
    assert r.json()["detail"] == "step_up_required"

    db = SessionLocal()
    try:
        assert db.execute(select(EvaluationCampaign)).scalars().all() == []
    finally:
        db.close()


def test_create_returns_a_link_that_carries_the_code_and_is_never_listed(
    make_staff, staff_login,
):
    superadmin = make_staff(role="superadmin")
    client = staff_login(superadmin["email"], superadmin["password"])
    payload = {"offer_id": "linked", "label": "Linked", "code": "LINK-CODE-LONG-ENOUGH",
               "credits": 10, "duration_days": 7, "max_redemptions": 1}
    body = client.post("/admin/v1/evaluation-campaigns", json=payload).json()
    assert body["redeem_url"].endswith("/redeem?offer=LINK-CODE-LONG-ENOUGH")
    assert "/dashboard/redeem" not in body["redeem_url"], \
        "the link was appended to dashboard_url's PATH, not its origin"

    listed = client.get("/admin/v1/evaluation-campaigns").json()[0]
    assert "redeem_url" not in listed and "redemption_code" not in listed
