"""The tier model: paid Premium, evaluation Premium, and the demo (M4a).

Three things that were one thing before this phase.

`plan_tier="premium"` used to have a single meaning. The 2026-08-06 commercial
model sells Premium, and an evaluation offer sets exactly the same string —
deliberately, and permanently, for reasons `billing_state.evaluation_lock`'s
docstring sets out at length. So the tier stopped being able to answer "is this
org a customer?", while five entitlements were still asking it that way. The one
with money attached is `judge_routing.PLATFORM_KEY_TIERS`, which spends Foxy's
own LLM key.

The demo is new: signup can now provision a PENDING workspace that a human
approves, with the 7-day clock starting at approval rather than at signup.

The trial dashboard lock is also new and is the dangerous one, because the single
outcome the owner ruled out is it firing on the free organisations that already
exist. Nobody has production access to count them, so it is scoped by
CONSTRUCTION instead: `test_an_org_created_before_this_phase_can_never_be_trial_locked`
is the guard that proves the scope holds without anybody counting anything.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app import billing_state, judge_routing
from app.config import get_settings
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Organization, OrgPolicy


# ── helpers ──────────────────────────────────────────────────────────────────

def _set(org_id, **fields) -> None:
    with SessionLocal() as db:
        o = db.get(Organization, uuid.UUID(str(org_id)))
        for k, v in fields.items():
            setattr(o, k, v)
        db.commit()


def _org(org_id) -> Organization:
    with SessionLocal() as db:
        o = db.get(Organization, uuid.UUID(str(org_id)))
        db.expunge(o)
        return o


def _days(n: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=n)


def _ingest(client, org):
    """Capture one event the way the SDK does.

    `POST /v1/logs/batch`, never `/v1/logs` — the latter is a GET-only listing,
    so posting to it collects 405 and would satisfy any `!= 402` assertion
    without ever reaching the gate being tested.
    """
    h = hashlib.sha256(str(uuid.uuid4()).encode()).hexdigest()
    return client.post("/v1/logs/batch", headers=org["auth"], json=[{
        "prompt_hash": h, "response_hash": h, "token_count": 8, "policy_tag": "t",
    }])


def _platform_mode(org_id) -> None:
    """Ask for Foxy's keys, straight in the DB, bypassing the API's own tier gate
    so the GRADING-TIME re-check is what gets measured."""
    with SessionLocal() as db:
        row = db.get(OrgPolicy, uuid.UUID(str(org_id))) or OrgPolicy(
            org_id=uuid.UUID(str(org_id)))
        row.judge_provider, row.judge_key_mode = "gemini", "platform"
        db.merge(row)
        db.commit()


def _org_by_email(email):
    with SessionLocal() as db:
        o = db.execute(select(Organization).where(
            Organization.contact_email == email)).scalars().one()
        db.expunge(o)
        return o

def _staff(make_staff, staff_login):
    who = make_staff(role="superadmin")
    return staff_login(who["email"], who["password"])


# ── 1 · paid Premium is not evaluation Premium ──────────────────────────────

def test_a_paid_premium_customer_grades_on_foxys_key(make_org):
    """The owner chose this knowing the cost is unbounded. It is the direction
    that must NOT change, so it is asserted before the one that does."""
    org = make_org()
    _set(org["org_id"], plan_tier="premium")
    assert judge_routing.platform_keys_allowed(_org(org["org_id"])) is True


def test_a_live_evaluation_still_grades_on_foxys_key(make_org):
    """Unchanged by M4a, and deliberately so: not needing your own provider key
    is most of what a judge offer is FOR. Narrowing this to paid-only would have
    gutted the offer while claiming to fix a leak."""
    org = make_org()
    _set(org["org_id"], plan_tier="premium", evaluation_offer_id="offer-live",
         evaluation_credit_limit=100, evaluation_ends_at=_days(30))
    assert judge_routing.platform_keys_allowed(_org(org["org_id"])) is True


def test_an_expired_evaluation_stops_grading_on_foxys_key(make_org):
    """The one that changes. An expired evaluator keeps reading `premium`
    forever — that is decided, not accidental — so before M4a it kept the
    platform-key privilege forever too."""
    org = make_org()
    _set(org["org_id"], plan_tier="premium", evaluation_offer_id="offer-dead",
         evaluation_credit_limit=100, evaluation_ends_at=_days(-1))
    assert judge_routing.platform_keys_allowed(_org(org["org_id"])) is False


def test_an_evaluator_who_buys_reads_as_paid(make_org, make_staff, staff_login):
    """`end_evaluation` clears the marker on every purchase path and, since M0,
    on staff activation. Asserted through the real staff route rather than by
    setting the fields, because the route is what has to remember to call it."""
    org = make_org()
    _set(org["org_id"], plan_tier="premium", evaluation_offer_id="offer-x",
         evaluation_credit_limit=100, evaluation_ends_at=_days(-1))
    r = _staff(make_staff, staff_login).post(
        f"/admin/v1/organizations/{org['org_id']}/plan", json={"plan": "premium"})
    assert r.status_code == 200, r.text
    assert judge_routing.platform_keys_allowed(_org(org["org_id"])) is True


def test_the_privilege_is_withdrawn_where_the_key_is_actually_spent(make_org):
    """At the CALL SITE, not only in the predicate.

    `resolve_judge_routing` is the function the worker calls, and a perfect
    predicate nobody consults is exactly as leaky as no predicate — the failure
    A6 shipped once already. An expired evaluator asking for platform keys must
    come back in `own` mode with no key, which the worker turns into an honest
    `evaluator_unavailable` rather than a bill.
    """
    org = make_org()
    _set(org["org_id"], plan_tier="premium", evaluation_offer_id="offer-dead",
         evaluation_credit_limit=100, evaluation_ends_at=_days(-1))
    _platform_mode(org["org_id"])
    with SessionLocal() as db:
        routing = judge_routing.resolve_judge_routing(db, org["org_id"])
    assert routing.key_mode == "own", "an expired evaluator kept Foxy's keys"
    assert routing.gemini_key is None
    assert routing.can_call("gemini") is False


def test_a_paid_premium_customer_keeps_the_key_at_grading_time(make_org):
    """The same call site, the direction that must not regress."""
    org = make_org()
    _set(org["org_id"], plan_tier="premium")
    _platform_mode(org["org_id"])
    with SessionLocal() as db:
        routing = judge_routing.resolve_judge_routing(db, org["org_id"])
    assert routing.key_mode == "platform"


def test_an_expired_evaluator_cannot_select_platform_keys_in_settings(make_org, login):
    """An expired evaluator cannot switch itself onto Foxy's keys.

    NOT via the tier gate, which is what this guard first asserted and got wrong.
    `/v1/policies` is not in `_GATE_EXEMPT`, so `evaluation_lock` answers 402
    before `platform_keys_allowed` is ever consulted — the settings-side check
    changed by M4a is therefore UNREACHABLE for this population today, and saying
    otherwise would have been a guard passing for the wrong reason.

    What is asserted is the outcome: the request does not succeed, and the stored
    key mode is unchanged. The grading-time re-check above is where the change is
    actually load-bearing.
    """
    org = make_org()
    _set(org["org_id"], plan_tier="premium", evaluation_offer_id="offer-dead",
         evaluation_credit_limit=100, evaluation_ends_at=_days(-1))
    c = login(org["admin_email"], org["admin_password"])
    r = c.put("/v1/policies", json={"judge_provider": "gemini",
                                    "judge_key_mode": "platform"})
    assert r.status_code == 402, r.text
    assert r.json()["detail"]["code"] == billing_state.EVALUATION_EXPIRED
    with SessionLocal() as db:
        row = db.get(OrgPolicy, uuid.UUID(str(org["org_id"])))
    assert row is None or row.judge_key_mode != "platform"


def test_a_paid_premium_customer_can_select_platform_keys_in_settings(make_org, login):
    org = make_org()
    _set(org["org_id"], plan_tier="premium")
    c = login(org["admin_email"], org["admin_password"])
    r = c.put("/v1/policies", json={"judge_provider": "gemini",
                                    "judge_key_mode": "platform"})
    assert r.status_code == 200, r.text
    assert c.get("/v1/policies").json()["platform_keys_allowed"] is True


def test_a_free_org_is_untouched_by_any_of_this(make_org):
    """The population that must not move. `make_org` writes no tier at all, which
    is what every org created outside billing looks like."""
    org = make_org()
    assert judge_routing.platform_keys_allowed(_org(org["org_id"])) is False
    _set(org["org_id"], plan_tier="pro")
    assert judge_routing.platform_keys_allowed(_org(org["org_id"])) is False


# ── 2 · the demo state and its approval ─────────────────────────────────────

def test_a_pending_workspace_cannot_read_its_dashboard(make_org, login):
    org = make_org()
    _set(org["org_id"], plan_tier="free", approval_status="pending")
    c = login(org["admin_email"], org["admin_password"])
    r = c.get("/v1/logs")
    assert r.status_code == 402, r.text
    assert r.json()["detail"]["code"] == billing_state.ACCOUNT_PENDING
    assert "reviewing" in r.json()["detail"]["message"].lower(), (
        "a pending workspace is told nothing useful about why it is empty"
    )


def test_a_pending_workspace_cannot_capture(client, make_org):
    """Not the usual "evidence survives every payment problem" case: there is no
    relationship yet to preserve evidence for, and capturing before approval is
    the free-access farming the approval exists to stop."""
    org = make_org()
    _set(org["org_id"], plan_tier="free", approval_status="pending")
    r = _ingest(client, org)
    assert r.status_code == 402, r.text
    assert r.json()["detail"]["code"] == billing_state.ACCOUNT_PENDING


def test_an_evaluation_org_can_still_capture_after_the_pending_check(client, make_org):
    """The pending check runs FIRST in `capture_block`, ahead of the evaluation
    branch, so it must not disturb what follows it.

    It did. The local holding the lock was called `pending`, which is also this
    function's BATCH-SIZE parameter, so `pending > 0` further down became
    `None > 0` — a TypeError on every ingest by an org holding an evaluation
    offer. Nineteen tests went red and not one of them was in this file: every
    guard here reached `capture_block` through an org with no offer, so the line
    that broke was never executed.
    """
    org = make_org()
    _set(org["org_id"], plan_tier="premium", evaluation_offer_id="offer-live",
         evaluation_credit_limit=50, evaluation_credits_used=0,
         evaluation_ends_at=_days(30))
    assert _ingest(client, org).status_code == 202, (
        "a live evaluation could not capture — the pending check broke the "
        "branch below it"
    )

def test_billing_and_auth_stay_reachable_while_pending(make_org, login):
    """A lock whose only remedy sits behind the lock is a brick. Buying a plan is
    the one thing that resolves this without anybody's approval, so `/v1/billing/`
    has to answer — and they must be able to sign out."""
    org = make_org()
    _set(org["org_id"], plan_tier="free", approval_status="pending")
    c = login(org["admin_email"], org["admin_password"])
    assert c.get("/v1/billing/access").status_code == 200
    assert c.get("/v1/auth/me").status_code == 200
    assert c.post("/v1/auth/logout").status_code == 200


def test_a_pending_workspace_reads_as_not_started_never_as_expired(make_org):
    """`trial_ends_at` stays NULL until approval, and both gates already guard
    `is not None`. Confirmed here rather than trusted: a pending org told its
    trial had EXPIRED would be a lie about a clock that never started."""
    org = make_org()
    _set(org["org_id"], plan_tier="free", approval_status="pending",
         trial_ends_at=None)
    row = _org(org["org_id"])
    assert row.trial_ends_at is None
    now = datetime.now(timezone.utc)
    assert billing_state.capture_block(row, now).reason == billing_state.ACCOUNT_PENDING
    assert billing_state.trial_lock(row, now) is None


def test_approval_starts_the_clock_from_that_moment(make_org, make_staff, staff_login):
    """Not from signup. Approving takes a day or two, and a clock stamped at
    signup silently hands somebody a five-day demo and calls it seven."""
    org = make_org()
    # Signed up EIGHT DAYS AGO, which is the whole point: approving takes a day
    # or two, and a fresh fixture cannot tell "now" from "created_at" — the first
    # version of this guard could not, and a mutation that backdated the clock to
    # signup survived it.
    _set(org["org_id"], plan_tier="free", approval_status="pending",
         created_at=datetime.now(timezone.utc) - timedelta(days=8))
    before = datetime.now(timezone.utc)
    r = _staff(make_staff, staff_login).post(
        f"/admin/v1/organizations/{org['org_id']}/approve")
    assert r.status_code == 200, r.text

    row = _org(org["org_id"])
    assert row.approval_status == "approved"
    started = row.trial_ends_at - timedelta(days=get_settings().trial_days)
    assert started >= before - timedelta(seconds=5), (
        "the clock was backdated to signup — an eight-day-old application would "
        "be approved straight into an expired demo"
    )
    assert row.trial_ends_at > datetime.now(timezone.utc), (
        "approval handed out a demo that had already ended"
    )


def test_an_approved_workspace_works_again(make_org, login, make_staff, staff_login):
    org = make_org()
    _set(org["org_id"], plan_tier="free", approval_status="pending")
    _staff(make_staff, staff_login).post(
        f"/admin/v1/organizations/{org['org_id']}/approve")
    c = login(org["admin_email"], org["admin_password"])
    assert c.get("/v1/logs").status_code == 200


def test_approving_something_that_is_not_pending_is_refused(make_org, make_staff,
                                                            staff_login):
    """Two silent damages in one route, so both are a 409. Approving an already
    approved org restarts its 7 days; approving an org that never came through
    the demo route enrols a grandfathered customer into a lock they are exempt
    from — and that org is every organisation older than migration 0064."""
    staff = _staff(make_staff, staff_login)
    grandfathered = make_org()                      # approval_status is NULL
    r = staff.post(f"/admin/v1/organizations/{grandfathered['org_id']}/approve")
    assert r.status_code == 409, r.text
    assert _org(grandfathered["org_id"]).approval_status is None
    assert _org(grandfathered["org_id"]).trial_ends_at is None, (
        "a refused approval still stamped a trial"
    )

    demo = make_org()
    _set(demo["org_id"], plan_tier="free", approval_status="pending")
    assert staff.post(f"/admin/v1/organizations/{demo['org_id']}/approve"
                      ).status_code == 200
    assert staff.post(f"/admin/v1/organizations/{demo['org_id']}/approve"
                      ).status_code == 409


def test_someone_who_pays_instead_of_waiting_is_not_still_waiting(make_org, login):
    """The M0 defect's shape: a purchase that changes nothing because a field was
    left behind. Somebody unwilling to wait for a human buys a plan, and must not
    then be told we are reviewing their request."""
    org = make_org()
    _set(org["org_id"], plan_tier="free", approval_status="pending")
    _set(org["org_id"], plan_tier="pro")            # what a purchase writes
    row = _org(org["org_id"])
    assert billing_state.awaiting_approval(row) is False
    assert billing_state.pending_lock(row) is None
    c = login(org["admin_email"], org["admin_password"])
    assert c.get("/v1/logs").status_code == 200


def test_a_purchase_also_clears_the_marker_so_the_queue_is_truthful(make_org):
    """Data hygiene rather than the safety property — the guard above proves the
    behaviour holds without this. Here so a queue filtering `approval_status =
    'pending'` in SQL does not keep showing a paying customer."""
    org = make_org()
    _set(org["org_id"], plan_tier="free", approval_status="pending")
    row = _org(org["org_id"])
    billing_state.end_trial(row)
    assert row.approval_status is None


def test_signup_provisions_pending_only_when_the_deployment_asks(client, monkeypatch):
    """OFF by default. Merging this phase must not put every new signup into a
    queue that has no console behind it until M4c."""
    s = get_settings()
    r = client.post("/v1/signup", json={"email": "m4a-off@test.dev", "name": "Off"})
    assert r.status_code == 200, r.text
    assert "approval_status" not in r.json()
    assert _org(r.json()["org_id"]).approval_status is None

    monkeypatch.setattr(s, "demo_approval_required", True)
    r = client.post("/v1/signup", json={"email": "m4a-on@test.dev", "name": "On"})
    assert r.status_code == 200, r.text
    assert r.json()["approval_status"] == "pending"
    assert "reviewing" in r.json()["message"].lower(), (
        "the applicant is told only to set a password for a workspace that will "
        "402 on everything"
    )
    row = _org(r.json()["org_id"])
    assert row.approval_status == "pending"
    assert row.trial_ends_at is None, "the clock started at signup"


def test_an_evaluation_signup_never_waits_in_the_queue(client, monkeypatch):
    """Whoever issued the code already approved that relationship; routing a judge
    through a queue they have been invited past would be absurd."""
    s = get_settings()
    monkeypatch.setattr(s, "demo_approval_required", True)
    monkeypatch.setattr(s, "judge_offer_code", "M4AOFFER")
    r = client.post("/v1/signup", json={"email": "m4a-judge@test.dev",
                                        "offer_code": "M4AOFFER"})
    assert r.status_code == 200, r.text
    row = _org(r.json()["org_id"])
    assert row.approval_status is None
    assert row.plan_tier == "premium"


def test_the_google_door_answers_the_queue_the_same_way(client, monkeypatch):
    """The SECOND free-signup door, and it has to queue too.

    A demo route that only holds the email/password door back is not a demo
    route: anyone refused there, or unwilling to wait, signs in with Google and
    is provisioned instantly. There was no guard on this at all until a mutation
    that reverted the Google path survived every other test in this file.
    """
    import types

    from app.routers import auth_google

    def _configure(pending: bool):
        monkeypatch.setattr(auth_google, "get_settings", lambda: types.SimpleNamespace(
            google_oauth_client_id="test-client-id", trial_days=7,
            demo_approval_required=pending, quota_for=lambda tier: 500))
        monkeypatch.setattr(auth_google, "_verify_google_token",
                            lambda credential, client_id: claims)

    claims = {"email": "m4a-google-on@test.dev", "sub": "g-m4a-1",
             "email_verified": True, "name": "Queued"}
    _configure(True)
    assert client.post("/v1/auth/google", json={"credential": "x"}).status_code == 200
    row = _org_by_email("m4a-google-on@test.dev")
    assert row.approval_status == "pending", (
        "Google sign-in walked straight past the approvals queue"
    )
    assert row.trial_ends_at is None, "the clock started before anyone approved"

    claims = {"email": "m4a-google-off@test.dev", "sub": "g-m4a-2",
             "email_verified": True, "name": "Instant"}
    _configure(False)
    assert client.post("/v1/auth/google", json={"credential": "x"}).status_code == 200
    off = _org_by_email("m4a-google-off@test.dev")
    assert off.approval_status is None
    assert off.trial_ends_at is not None, "the default path stopped granting a trial"

# ── 3 · the trial dashboard lock, and who it must never touch ───────────────

def test_an_org_created_before_this_phase_can_never_be_trial_locked(make_org, login):
    """THE guard this phase exists to earn.

    Existing free organisations are grandfathered by an explicit owner decision,
    and nobody has production access to count them — so the scope cannot rest on
    a number, a date, or a flag. It rests on a column that nothing except the
    demo route ever writes: `make_org` produces exactly what a pre-0064 row looks
    like, `approval_status` NULL, and no amount of trial expiry can lock it.

    The org here is as expired as it is possible to be: free tier, a stamp a year
    in the past. Before M4a it read its dashboard. After M4a it still does.
    """
    org = make_org()
    _set(org["org_id"], plan_tier="free", trial_ends_at=_days(-365))
    row = _org(org["org_id"])
    assert row.approval_status is None, "the fixture no longer models a legacy row"

    assert billing_state.trial_lock(row) is None
    assert billing_state.dashboard_lock(row) is None, (
        "a grandfathered free org was locked out of its own dashboard"
    )
    c = login(org["admin_email"], org["admin_password"])
    assert c.get("/v1/logs").status_code == 200


def test_an_approved_demo_with_no_stamp_reads_as_not_started(make_org):
    """NULL is "the clock has not started", never "it ran out".

    The pending guard above cannot prove this: a pending org is turned away by
    `trial_lock`'s FIRST condition and never reaches the date comparison, so a
    mutation inverting the NULL test survived it. This one is approved, so it
    gets all the way to the comparison with nothing to compare against.

    Reachable in one sequence today — an approved demo buys (`end_trial` clears
    the stamp and leaves `approval_status` alone) and is later put back on free
    by staff. The tier check would also catch that particular row, which is
    exactly why this is asserted separately: two independent reasons, and a
    guard for each, or the second is load-bearing without anyone knowing.
    """
    org = make_org()
    _set(org["org_id"], plan_tier="free", approval_status="approved",
         trial_ends_at=None)
    assert billing_state.trial_lock(_org(org["org_id"])) is None, (
        "an approved demo with no clock was told its trial had expired"
    )

def test_an_approved_demo_is_locked_on_day_seven(make_org, login):
    """The behaviour that is actually new. Same row as the guard above in every
    respect except the one that scopes it."""
    org = make_org()
    _set(org["org_id"], plan_tier="free", trial_ends_at=_days(-1),
         approval_status="approved")
    c = login(org["admin_email"], org["admin_password"])
    r = c.get("/v1/logs")
    assert r.status_code == 402, r.text
    assert r.json()["detail"]["code"] == billing_state.TRIAL_EXPIRED


def test_an_approved_demo_inside_its_week_is_not_locked(make_org, login):
    org = make_org()
    _set(org["org_id"], plan_tier="free", trial_ends_at=_days(3),
         approval_status="approved")
    c = login(org["admin_email"], org["admin_password"])
    assert c.get("/v1/logs").status_code == 200


def test_a_demo_that_buys_is_not_locked_by_the_trial(make_org, login):
    """Two independent reasons it cannot fire — the tier is no longer free, and
    since M3b every purchase path clears the stamp. Both are asserted because the
    tier check alone would leave a paying customer one stale column away from a
    lockout."""
    org = make_org()
    _set(org["org_id"], plan_tier="free", trial_ends_at=_days(-1),
         approval_status="approved")
    _set(org["org_id"], plan_tier="pro")
    row = _org(org["org_id"])
    assert billing_state.trial_lock(row) is None
    c = login(org["admin_email"], org["admin_password"])
    assert c.get("/v1/logs").status_code == 200


def test_the_trial_lock_says_exactly_what_the_capture_block_says(make_org):
    """One fact ends both, so one sentence says it. Two wordings of "your trial
    ended" is how the dashboard and the 402 body drift apart, and this lock emits
    a reason the dashboard ALREADY has copy for — which is why M4a adds no new
    wording for it anywhere."""
    org = make_org()
    _set(org["org_id"], plan_tier="free", trial_ends_at=_days(-1),
         approval_status="approved")
    row, now = _org(org["org_id"]), datetime.now(timezone.utc)
    locked, blocked = billing_state.trial_lock(row, now), billing_state.capture_block(row, now)
    assert locked.reason == blocked.reason == billing_state.TRIAL_EXPIRED
    assert locked.message == blocked.message


def test_the_trial_lock_never_speaks_for_an_evaluation(make_org):
    """An evaluation org is premium, so it can never satisfy this lock — and it
    must not, because `evaluation_lock` has its own message about an offer the
    customer actually received."""
    org = make_org()
    _set(org["org_id"], plan_tier="premium", trial_ends_at=_days(-1),
         approval_status="approved", evaluation_offer_id="offer-dead",
         evaluation_credit_limit=10, evaluation_ends_at=_days(-1))
    row = _org(org["org_id"])
    assert billing_state.trial_lock(row) is None
    assert billing_state.dashboard_lock(row).reason == billing_state.EVALUATION_EXPIRED


def test_sdk_ingest_is_never_stopped_by_the_new_dashboard_lock(client, make_org):
    """`_GATE_EXEMPT` and the `require_org` / `require_user` split exist so a
    dashboard condition can never cost a customer their evidence. The trial lock
    is new, so the guarantee is re-proved against it rather than assumed.

    An APPROVED demo past day 7 is locked out of the dashboard by `trial_lock`
    and separately refused by `capture_block`, which has always answered
    `trial_expired` for exactly this org — so the assertion here is that the
    refusal still comes from the capture gate with its own code, not that ingest
    succeeds. The org that proves ingest is untouched is the paid one below.
    """
    demo = make_org()
    _set(demo["org_id"], plan_tier="free", trial_ends_at=_days(-1),
         approval_status="approved")
    r = _ingest(client, demo)
    assert r.status_code == 402
    assert r.json()["detail"]["code"] == billing_state.TRIAL_EXPIRED

    paid = make_org()
    _set(paid["org_id"], plan_tier="pro", trial_ends_at=_days(-1),
         approval_status="approved", card_on_file=False)
    assert _ingest(client, paid).status_code == 202, (
        "a paying customer's evidence was refused by a dashboard-side condition"
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
