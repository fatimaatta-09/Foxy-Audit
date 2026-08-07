"""C3 · quota + month-to-date usage on the organisations list row.

The console draws a meter from these three fields, so what matters is not that
numbers come back but that each one means exactly what the meter claims:

  * `monthly_log_quota` is the ENFORCED cap (the value logs.py's
    credits_exhausted gate tests), so NULL is "nothing is counting against this
    org" and not "look up the plan default",
  * `usage_this_month` is a UTC month-to-date sum and excludes last month,
  * `usage_rolled_up_at` distinguishes a measured zero from a rollup that has
    never run -- absence is not zero,
  * and the whole list costs ONE grouped query, not one per row.

Run with DATABASE_URL pointing at :5433/foxy_pytest -- conftest defaults to 5432
and will otherwise use the wrong database without saying so.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

from app.db import SessionLocal
from app.models import Organization, UsageDaily

URL = "/admin/v1/organizations"


def _month_start():
    return datetime.now(timezone.utc).date().replace(day=1)


def _seed_usage(org_id, day, logs):
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
    _seed_usage(org["org_id"], _month_start(), 900)
    _seed_usage(org["org_id"], _month_start() + timedelta(days=1), 340)
    s = make_staff()
    c = staff_login(s["email"], s["password"])
    r = _row(c, org["org_id"])
    assert r["monthly_log_quota"] == 25_000
    assert r["usage_this_month"] == 1240, "the month's days were not summed"
    assert r["usage_rolled_up_at"], "the rollup ran but the row says it never did"


def test_last_months_usage_is_not_counted_in_this_month(
        make_staff, staff_login, make_org):
    """The meter's denominator is monthly, so a sum that reached back would
    show an org over a limit it has not touched. usage_daily keeps FULL history
    -- rollup_recent recomputes a 2-day window but UPSERTS, and nothing prunes
    the table -- so there really are older rows to exclude."""
    org = make_org()
    _set_quota(org["org_id"], 1_000)
    last_month = _month_start() - timedelta(days=1)
    _seed_usage(org["org_id"], last_month, 5_000)
    _seed_usage(org["org_id"], _month_start(), 7)
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
    _seed_usage(org["org_id"], _month_start(), 4_000)
    s = make_staff()
    c = staff_login(s["email"], s["password"])
    r = _row(c, org["org_id"])
    assert r["monthly_log_quota"] is None, (
        "a NULL cap came back as %r; the meter would draw a bar for an org "
        "nothing is counting against" % r["monthly_log_quota"])
    assert r["usage_this_month"] == 4_000, "usage is still reported without a cap"


def test_an_org_with_no_rollup_rows_reads_zero_against_a_ran_rollup(
        make_staff, staff_login, make_org):
    """A zero is only a measurement once the rollup has produced something.
    Both facts are on the row so the console can tell them apart, and this
    asserts the pair rather than either alone.

    The premise is made true by construction -- one org gets a row, the other
    gets none -- so the assertion cannot go vacuous the way an `if x == 0:`
    guard would when ambient data appears.
    """
    quiet = make_org()
    busy = make_org()
    _seed_usage(busy["org_id"], _month_start(), 12)
    s = make_staff()
    c = staff_login(s["email"], s["password"])
    q, b = _row(c, quiet["org_id"]), _row(c, busy["org_id"])
    assert b["usage_this_month"] == 12
    assert q["usage_this_month"] == 0, "an org with no rows is not zero"
    # the rollup HAS run (busy proves it), so quiet's zero is a real zero
    assert q["usage_rolled_up_at"] is not None
    assert q["usage_rolled_up_at"] == b["usage_rolled_up_at"], (
        "the rollup stamp is per-org; it is a platform fact")


def test_a_rollup_that_never_ran_is_reported_as_never(
        make_staff, staff_login, make_org):
    """The other half, and the one the console renders differently: with no
    usage_daily rows at all, every org's zero is an ABSENCE. The meter must say
    so rather than draw an empty bar labelled 0%, which reads as a customer
    sending nothing.

    _clean_db truncates usage_daily before each test, so "never ran" holds by
    construction here -- and it is asserted, not assumed.
    """
    org = make_org()
    _set_quota(org["org_id"], 500)
    s = make_staff()
    c = staff_login(s["email"], s["password"])
    r = _row(c, org["org_id"])
    assert r["usage_rolled_up_at"] is None, (
        "the rollup has written nothing but the row claims a timestamp")
    assert r["usage_this_month"] == 0


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
        _seed_usage(o["org_id"], _month_start(), 5)
    s = make_staff()
    c = staff_login(s["email"], s["password"])

    seen = []
    from sqlalchemy import event
    from app.db import engine

    def _rec(conn, cursor, statement, params, context, executemany):
        if "usage_daily" in statement.lower():
            seen.append(statement)

    event.listen(engine, "before_cursor_execute", _rec)
    try:
        rows = c.get(URL).json()
    finally:
        event.remove(engine, "before_cursor_execute", _rec)

    assert len([r for r in rows if r["id"] in {str(o["org_id"]) for o in orgs}]) == 4
    # one grouped SUM + one MAX(computed_at) = two, regardless of tenant count
    assert len(seen) <= 2, (
        "the list issued %d usage_daily queries for 4 orgs; it must not scale "
        "with the number of tenants: %s" % (len(seen), seen))


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
