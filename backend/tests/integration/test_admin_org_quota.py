"""C3 · quota + month-to-date usage on the organisations list row.

The console draws a meter from these three fields, so what matters is not that
numbers come back but that each one means exactly what the meter claims:

  * `monthly_log_quota` is the ENFORCED cap (the value logs.py's
    credits_exhausted gate tests), so NULL is "nothing is counting against this
    org" and not "look up the plan default",
  * `usage_this_month` is a UTC month-to-date sum and excludes last month,
  * the count comes from the LEDGER, so it survives the rollup's rolling window
    -- C3 read usage_daily and understated every day before yesterday (#120),
  * and the whole list costs ONE grouped query, not one per row.

Run with DATABASE_URL pointing at :5433/foxy_pytest -- conftest defaults to 5432
and will otherwise use the wrong database without saying so.
"""
from __future__ import annotations

import pytest

import uuid
from datetime import date, datetime, timedelta, timezone

import hashlib

from sqlalchemy import text

from app.db import SessionLocal
from app.models import AuditLog, Organization, UsageDaily


def _h(x: str) -> str:
    return hashlib.sha256(x.encode()).hexdigest()


def _seed_audit_n(oid, n, at=None, seq0=0):
    """n ledger rows in one statement, optionally at a fixed instant."""
    db = SessionLocal()
    try:
        db.execute(text(
            "INSERT INTO audit_logs (id, org_id, seq, prompt_hash, response_hash,"
            " token_count, policy_tag, prev_hash, chain_hash, agent, grading_status,"
            " created_at) SELECT gen_random_uuid(), :oid, :seq0 + g,"
            " md5(g::text)||md5(g::text), md5(g::text)||md5(g::text), 5, 'test',"
            " md5(g::text)||md5(g::text), md5(g::text)||md5(g::text), 'agent-x',"
            " 'graded', coalesce(:at, now()) FROM generate_series(1, :n) g"),
            {"oid": oid, "n": n, "at": at, "seq0": seq0})
        db.commit()
    finally:
        db.close()


def _seed_audit(oid, seq):
    """One ledger row, written inside the caller's session."""
    db = SessionLocal()
    try:
        h = _h(f"{oid}-{seq}")
        db.add(AuditLog(org_id=oid, seq=seq, prompt_hash=h, response_hash=h,
                        token_count=5, policy_tag="test", prev_hash=h, chain_hash=h,
                        agent="agent-x", grading_status="graded"))
        db.commit()
    finally:
        db.close()

URL = "/admin/v1/organizations"


def _month_start():
    return datetime.now(timezone.utc).date().replace(day=1)


def _seed_usage(org_id, day, logs):
    """A usage_daily row. Kept only to prove the endpoint does NOT read it."""
    db = SessionLocal()
    try:
        db.add(UsageDaily(org_id=uuid.UUID(str(org_id)), day=day, logs_count=logs))
        db.commit()
    finally:
        db.close()


def _set_quota(org_id, quota):
    db = SessionLocal()
    try:
        org = db.get(Organization, uuid.UUID(str(org_id)))
        org.monthly_log_quota = quota
        db.commit()
    finally:
        db.close()


def _row(client, org_id):
    rows = client.get(URL).json()
    match = [r for r in rows if r["id"] == str(org_id)]
    assert match, "org %s is not in the list" % org_id
    return match[0]


def test_the_list_row_carries_the_quota_and_the_month_to_date_usage(
        make_staff, staff_login, make_org):
    org = make_org()
    _set_quota(org["org_id"], 25_000)
    _seed_audit_n(uuid.UUID(str(org["org_id"])), 1240)
    s = make_staff()
    c = staff_login(s["email"], s["password"])
    r = _row(c, org["org_id"])
    assert r["monthly_log_quota"] == 25_000
    assert r["usage_this_month"] == 1240, "the month's days were not counted"


def test_last_months_usage_is_not_counted_in_this_month(
        make_staff, staff_login, make_org):
    """The meter's denominator is monthly, so a sum that reached back would
    show an org over a limit it has not touched. usage_daily keeps FULL history
    -- rollup_recent recomputes a 2-day window but UPSERTS, and nothing prunes
    the table -- so there really are older rows to exclude."""
    org = make_org()
    _set_quota(org["org_id"], 1_000)
    oid = uuid.UUID(str(org["org_id"]))
    last_month = datetime.combine(_month_start(), datetime.min.time(),
                                  tzinfo=timezone.utc) - timedelta(hours=1)
    _seed_audit_n(oid, 5_000, at=last_month)
    _seed_audit_n(oid, 7, seq0=10_000)
    s = make_staff()
    c = staff_login(s["email"], s["password"])
    r = _row(c, org["org_id"])
    assert r["usage_this_month"] == 7, (
        "last month's 5,000 leaked into this month: %s" % r["usage_this_month"])


def test_a_null_quota_is_reported_as_null_not_as_a_plan_default(
        make_staff, staff_login, make_org):
    """THE ONE THAT DECIDES WHAT THE METER MEANS.

    logs.py enforces `if quota is not None` against org.monthly_log_quota
    itself, so NULL means nothing is counting. Every creation path stores
    settings.quota_for(plan), which returns None for premium/guardian, and
    billing_state nulls it deliberately while an evaluation offer is live and
    says so: "the quota gate is inert on exactly the orgs this pair applies to".

    Resolving platform_config.effective_quota() here instead would hand the
    console a denominator -- 500 for a free-plan evaluation org -- and the meter
    would show a workspace over a limit that nothing applies to it.
    """
    org = make_org()
    _set_quota(org["org_id"], None)
    _seed_audit_n(uuid.UUID(str(org["org_id"])), 4_000)
    s = make_staff()
    c = staff_login(s["email"], s["password"])
    r = _row(c, org["org_id"])
    assert r["monthly_log_quota"] is None, (
        "a NULL cap came back as %r; the meter would draw a bar for an org "
        "nothing is counting against" % r["monthly_log_quota"])
    assert r["usage_this_month"] == 4_000, "usage is still reported without a cap"




def test_the_whole_list_costs_one_grouped_usage_query(
        make_staff, staff_login, make_org):
    """N+1 IS THE FAILURE MODE HERE, not slowness in the abstract: the list is
    unpaginated server-side, so one sum per row is one query per tenant against
    the platform's busiest rollup on every page load.

    Counted by echo, not by timing -- a timing assertion is flaky and would not
    say what went wrong.
    """
    orgs = [make_org() for _ in range(4)]
    for o in orgs:
        _seed_audit_n(uuid.UUID(str(o["org_id"])), 5)
    s = make_staff()
    c = staff_login(s["email"], s["password"])

    seen = []
    from sqlalchemy import event
    from app.db import engine

    def _rec(conn, cursor, statement, params, context, executemany):
        if "from audit_logs" in statement.lower() and "count(" in statement.lower():
            seen.append(statement)

    event.listen(engine, "before_cursor_execute", _rec)
    try:
        rows = c.get(URL).json()
    finally:
        event.remove(engine, "before_cursor_execute", _rec)

    assert len([r for r in rows if r["id"] in {str(o["org_id"]) for o in orgs}]) == 4
    # ONE grouped count, regardless of tenant count
    assert len(seen) == 1, (
        "the list issued %d ledger counts for 4 orgs; it must not scale with "
        "the number of tenants: %s" % (len(seen), seen))


def test_org_detail_still_answers_after_the_field_moved_up(
        make_staff, staff_login, make_org):
    """monthly_log_quota moved from OrgDetail onto OrgListItem, so OrgDetail
    inherits it and the old explicit keyword became a duplicate. Left in place
    it is a TypeError on every org-detail read -- caught here rather than in
    the console."""
    org = make_org()
    _set_quota(org["org_id"], 777)
    s = make_staff()
    c = staff_login(s["email"], s["password"])
    r = c.get(f"{URL}/{org['org_id']}")
    assert r.status_code == 200, r.text
    assert r.json()["monthly_log_quota"] == 777


# ── C3.1 · the meter reads the LEDGER, not the rollup (register #120) ────────


def test_the_meter_counts_days_the_rollup_window_has_already_rewritten(
        make_staff, staff_login, make_org):
    """THE GUARD THIS FIX EXISTS FOR, and the one C3 did not have.

    Mirrors test_usage_reports_days_older_than_the_rollup_window in
    test_account.py, which guards /v1/usage against this exact defect -- the
    third reader of this rollup to need it.

    `_ROLLUP_SQL` selects `created_at >= now() - interval '2 days'`, a ROLLING
    window, and upserts. Anything older is never revisited, so a day's stored
    value is frozen at whatever partial count was true when the window last
    covered it. Five events are backdated a week and the rollup is run exactly
    as the worker runs it; it cannot see them, so usage_daily holds nothing.

    The precondition is ASSERTED, not assumed: a version of this test where the
    rollup happened to pick the rows up would pass against the broken code.
    C3's meter returned 0 here. That is the whole defect -- on a surface whose
    job is finding orgs near their limit, understating reads as reassuring.
    """
    from app import usage

    org = make_org()
    _set_quota(org["org_id"], 1_000)
    oid = uuid.UUID(str(org["org_id"]))

    # UTC, and asserted to be inside this month -- date_trunc('day', now()) is
    # evaluated in the SESSION timezone, which on this machine is +05, so "7
    # days ago" landed in the PREVIOUS month and the test failed for a reason
    # that had nothing to do with the defect.
    now = datetime.now(timezone.utc)
    # FOUR days, not the seven the /v1/usage precedent uses: the rollup's window
    # is `now() - interval '2 days'`, so four is comfortably outside it while
    # still landing inside the current month for 27 days out of ~30. The
    # precondition below proves the choice rather than trusting it.
    backdated = now - timedelta(days=4)
    if backdated < datetime(now.year, now.month, 1, tzinfo=timezone.utc):
        pytest.skip("the month is younger than the rollup window plus a margin, "
                    "so no instant is both in-month and out-of-window")

    db = SessionLocal()
    try:
        _seed_audit_n(oid, 5, at=backdated)
        usage.rollup_recent(db)
        stale = db.execute(text(
            "SELECT coalesce(sum(logs_count), 0) FROM usage_daily WHERE org_id = :oid"),
            {"oid": oid}).scalar_one()
    finally:
        db.close()

    assert stale == 0, (
        "precondition gone: the rollup saw the backdated day, so this test is "
        "no longer exercising the window it exists to defeat")

    s = make_staff()
    c = staff_login(s["email"], s["password"])
    r = _row(c, org["org_id"])
    assert r["usage_this_month"] == 5, (
        "the meter reported %s of 5 events that the rollup cannot see -- it is "
        "reading usage_daily again" % r["usage_this_month"])


def test_a_partial_rollup_row_cannot_move_the_meter(make_staff, staff_login, make_org):
    """The other direction, and the one a "does it read the ledger" grep cannot
    prove: usage_daily is not merely incomplete, its stored values are WRONG --
    ON CONFLICT DO UPDATE writes a partial slice over a complete row.

    So a rollup row that disagrees with the ledger must not shift the answer at
    all. Here the rollup says 99,999 and the ledger says 3; the meter says 3.
    """
    org = make_org()
    _set_quota(org["org_id"], 1_000)
    oid = uuid.UUID(str(org["org_id"]))
    db = SessionLocal()
    try:
        for i in range(3):
            _seed_audit(oid, seq=i + 1)
        db.commit()
    finally:
        db.close()
    _seed_usage(org["org_id"], _month_start(), 99_999)

    s = make_staff()
    c = staff_login(s["email"], s["password"])
    r = _row(c, org["org_id"])
    assert r["usage_this_month"] == 3, (
        "a usage_daily row of 99,999 moved the meter to %s; the rollup is still "
        "a source" % r["usage_this_month"])


def test_the_meter_and_the_credits_gate_count_the_same_rows(
        make_staff, staff_login, make_org, client):
    """The point of the change: /v1/logs/batch refuses at `used + n > quota`
    where `used` counts audit_logs for the month. The meter now counts the same
    rows, so "99%" on the console and the 402 the customer gets are one number.

    Driven through the real ingest path rather than seeded, because the two
    sides have to agree about rows the PRODUCT wrote, not rows a test invented.
    """
    org = make_org()
    _set_quota(org["org_id"], 4)

    rows = [{"prompt_hash": _h(f"c{i}"), "response_hash": _h(f"r{i}"),
             "token_count": 5, "policy_tag": "test"} for i in range(3)]
    assert client.post("/v1/logs/batch", json=rows,
                       headers=org["auth"]).status_code == 202

    s = make_staff()
    c = staff_login(s["email"], s["password"])
    assert _row(c, org["org_id"])["usage_this_month"] == 3

    # 3 used + 2 more > 4 -> the gate refuses, and the console already said 3/4
    over = [{"prompt_hash": _h(f"x{i}"), "response_hash": _h(f"y{i}"),
             "token_count": 5, "policy_tag": "test"} for i in range(2)]
    blocked = client.post("/v1/logs/batch", json=over, headers=org["auth"])
    assert blocked.status_code == 402, blocked.text
    detail = blocked.json()["detail"]
    assert detail["code"] == "credits_exhausted"
    assert detail["used"] == _row(c, org["org_id"])["usage_this_month"], (
        "the gate counted %s and the meter shows %s"
        % (detail["used"], _row(c, org["org_id"])["usage_this_month"]))
    assert detail["included"] == _row(c, org["org_id"])["monthly_log_quota"]
