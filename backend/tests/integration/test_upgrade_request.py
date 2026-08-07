"""P4 · #56 — a member can ask the admins for a bigger plan, and only ask.

Buying a plan is `require_role("admin")` on purpose: anyone on a team being able
to spend company money is a worse problem than the one it solves. The cost was
that a member who wanted a bigger plan had nowhere to go. This gives them a
door, and the two things worth guarding pull against each other:

* the door must **not** be a purchase path — a member still cannot buy;
* pressing it repeatedly must **not** produce a pile of notifications, and the
  second press must still be answered honestly rather than silently dropped.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.db import SessionLocal
from app.models import Notification, Organization
from app.user_notifications import UPGRADE_REQUEST_KIND


def _requests(org_id) -> list[Notification]:
    db = SessionLocal()
    try:
        return db.query(Notification).filter(
            Notification.org_id == org_id,
            Notification.kind == UPGRADE_REQUEST_KIND).all()
    finally:
        db.close()


def _member(make_org, add_user, login, email="member@test.dev"):
    org = make_org()
    add_user(org["org_id"], email, "memberpass1", role="member")
    return org, login(email, "memberpass1")


# ══ the door is not a purchase path ═════════════════════════════════════════
def test_a_member_cannot_start_a_checkout_for_this_workspace_by_any_route():
    """Every route that could spend this workspace's money, enumerated from the
    router itself rather than from memory, must refuse a member.

    `/v1/billing/checkout-session` is deliberately absent: it is the ANONYMOUS
    acquisition flow the sale page uses, it takes no session at all, and it
    provisions a NEW organisation rather than upgrading this one (#36). It is
    not a way for a member to buy THIS workspace a plan, which is what this
    guard is about.
    """
    from app.routers import billing

    money = {"/v1/billing/upgrade-session", "/v1/billing/portal",
             "/v1/billing/card-setup-session", "/v1/billing/cancel"}
    found = {r.path for r in billing.router.routes
             if getattr(r, "path", "") in money}
    assert found == money, f"a money route was renamed or removed: {money - found}"


def test_every_money_route_refuses_a_member(make_org, add_user, login):
    org, c = _member(make_org, add_user, login)
    for path in ("/v1/billing/upgrade-session", "/v1/billing/portal",
                 "/v1/billing/card-setup-session", "/v1/billing/cancel"):
        r = c.post(path, json={"plan": "pro"})
        assert r.status_code == 403, f"{path} answered {r.status_code}"
    # And the new route, which the same member CAN reach, hands back nothing a
    # browser could follow to a payment page.
    body = c.post("/v1/billing/upgrade-request").json()
    assert set(body) == {"can_purchase", "requested_at", "cooldown_hours", "created"}
    assert body["can_purchase"] is False


def test_the_request_writes_a_notification_and_never_a_payment(
        make_org, add_user, login):
    org, c = _member(make_org, add_user, login)
    db = SessionLocal()
    try:
        o = db.get(Organization, org["org_id"])
        before = {"tier": o.plan_tier, "status": o.subscription_status}
    finally:
        db.close()
    assert c.post("/v1/billing/upgrade-request").status_code == 200
    rows = _requests(org["org_id"])
    assert len(rows) == 1
    row = rows[0]
    assert row.user_id is None, "org-wide, so one admin reading it is the team reading it"
    assert row.level == "info"
    assert "member@test.dev" in (row.body or ""), "an admin must know who to talk to"
    db = SessionLocal()
    try:
        o = db.get(Organization, org["org_id"])
        assert o.plan_tier == before["tier"], "asking changed the plan"
        assert o.stripe_subscription_id is None
        assert o.subscription_status == before["status"]
    finally:
        db.close()


# ══ the second press ════════════════════════════════════════════════════════
def test_a_second_press_writes_no_second_notification(make_org, add_user, login):
    org, c = _member(make_org, add_user, login)
    first = c.post("/v1/billing/upgrade-request").json()
    second = c.post("/v1/billing/upgrade-request").json()
    assert len(_requests(org["org_id"])) == 1
    assert first["created"] is True and second["created"] is False
    # Honest, not a silent no-op: the second answer carries the same moment the
    # admins were actually told, so the page can say when.
    assert second["requested_at"] == first["requested_at"]


def test_a_second_member_is_told_the_admins_already_know(
        make_org, add_user, login):
    """Three colleagues hitting the same wall on the same workspace is one fact.
    Deduped per ORG, not per member — otherwise a five-person team turns one
    problem into five identical alerts."""
    org, first = _member(make_org, add_user, login, "one@test.dev")
    add_user(org["org_id"], "two@test.dev", "memberpass1", role="member")
    second = login("two@test.dev", "memberpass1")

    a = first.post("/v1/billing/upgrade-request").json()
    b = second.post("/v1/billing/upgrade-request").json()
    assert len(_requests(org["org_id"])) == 1
    assert b["created"] is False
    assert b["requested_at"] == a["requested_at"]


def test_the_cooldown_expires(make_org, add_user, login):
    """A workspace that asked a month ago and still has not been upgraded must
    be able to ask again — a dedupe with no end is a mute button."""
    org, c = _member(make_org, add_user, login)
    c.post("/v1/billing/upgrade-request")
    db = SessionLocal()
    try:
        row = db.query(Notification).filter(
            Notification.org_id == org["org_id"],
            Notification.kind == UPGRADE_REQUEST_KIND).one()
        row.created_at = datetime.now(timezone.utc) - timedelta(hours=25)
        db.commit()
    finally:
        db.close()
    assert c.post("/v1/billing/upgrade-request").json()["created"] is True
    assert len(_requests(org["org_id"])) == 2


# ══ the state the page reads back ═══════════════════════════════════════════
def test_the_state_survives_a_new_session(make_org, add_user, login):
    """The control has to still say "sent" after a refresh, so the state lives
    in the row, not in a page variable. A brand-new client proves it."""
    org, c = _member(make_org, add_user, login)
    sent = c.post("/v1/billing/upgrade-request").json()["requested_at"]
    fresh = login("member@test.dev", "memberpass1")
    state = fresh.get("/v1/billing/upgrade-request").json()
    assert state["requested_at"] == sent
    assert state["can_purchase"] is False
    assert state["cooldown_hours"] == 24


def test_an_untouched_workspace_reports_nothing_sent(make_org, add_user, login):
    org, c = _member(make_org, add_user, login)
    state = c.get("/v1/billing/upgrade-request").json()
    assert state["requested_at"] is None
    assert "created" not in state, "a read must not claim to have written"


def test_an_admin_is_told_they_can_buy(make_org, login):
    """`can_purchase` is the SERVER's answer, so the page cannot offer a member
    a button the route will refuse, or hide one from an admin who has it."""
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    assert c.get("/v1/billing/upgrade-request").json()["can_purchase"] is True


def test_both_verbs_need_a_session(client):
    assert client.get("/v1/billing/upgrade-request").status_code == 401
    assert client.post("/v1/billing/upgrade-request").status_code == 401


# ══ reachable from inside the lock ══════════════════════════════════════════
def test_a_locked_workspace_can_still_ask(make_org, add_user, login):
    """The member who most needs this is the one looking at a lock overlay: the
    plan buttons on it are admin-only, and the rest of the dashboard is behind
    it. `/v1/billing/` is already in `auth._GATE_EXEMPT`, so this needed no
    widening of that boundary — the guard is here to keep it that way.
    """
    org, c = _member(make_org, add_user, login)
    db = SessionLocal()
    try:
        # `approval_status='pending'` — the one lock with no feature flag behind
        # it, so this guard tests the gate rather than a switch that is off.
        db.get(Organization, org["org_id"]).approval_status = "pending"
        db.commit()
    finally:
        db.close()

    # The lock is really on: a gated route refuses this member with 402.
    assert c.get("/v1/stats").status_code == 402
    assert c.get("/v1/billing/upgrade-request").status_code == 200
    assert c.post("/v1/billing/upgrade-request").status_code == 200
    assert len(_requests(org["org_id"])) == 1


def test_the_kind_is_the_one_the_surfaces_were_taught(make_org, add_user, login):
    """The dashboard carries a `KIND_HELP` entry keyed on this exact string, and
    it is what a customer reads to find out what the alert means. A rename here
    with no rename there degrades to no explainer at all — silently."""
    org, c = _member(make_org, add_user, login)
    c.post("/v1/billing/upgrade-request")
    assert _requests(org["org_id"])[0].kind == "upgrade_request"
