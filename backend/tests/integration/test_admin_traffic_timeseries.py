"""C2 · /admin/v1/stats/traffic-timeseries — four series, one scan.

The traffic page's KPI row asks four related questions of one table, so this is
one endpoint returning four series rather than four more members of
_TS_METRICS. What is worth testing here is not "does it return numbers" but the
four things that are easy to get wrong and invisible once shipped:

  * `errors` cuts ACROSS sites and is not a fourth site value,
  * the window is UTC calendar days, so an event's day is decided by its UTC
    date and not by the server's local one (the register already carries a
    timestamptz date bug from P3),
  * `delta_pct` is None, never 0.0, when the prior window is empty, and
  * the day axis is zero-filled, so a quiet day is a 0 and not a gap.

Run with DATABASE_URL pointing at :5433/foxy_pytest — conftest defaults to 5432
and will otherwise use the wrong database without saying so.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from app.db import SessionLocal
from app.models import TrafficEvent

URL = "/admin/v1/stats/traffic-timeseries"


def _today():
    return datetime.now(timezone.utc).date()


def _seed_traffic(site, when, status=200, n=1):
    """n events on one site at an exact instant."""
    db = SessionLocal()
    try:
        for _ in range(n):
            db.add(TrafficEvent(site=site, path="/x", method="GET",
                                status_code=status, created_at=when))
        db.commit()
    finally:
        db.close()


def _at(day, hour=12):
    return datetime.combine(day, datetime.min.time(),
                            tzinfo=timezone.utc) + timedelta(hours=hour)


def test_traffic_timeseries_is_viewer_gated(client, make_staff, staff_login):
    assert client.get(URL).status_code == 401
    s = make_staff()
    c = staff_login(s["email"], s["password"])
    assert c.get(URL).status_code == 200


def test_the_four_series_are_returned_and_errors_is_not_a_site(make_staff, staff_login):
    """`errors` is a cut across every site, not a fourth value of `site`. If it
    were read off `site` it would always be 0, because nothing ever writes
    'errors' there -- a card permanently reading zero on the page whose job is
    to show that something is wrong."""
    today = _today()
    _seed_traffic("marketing", _at(today), 200, n=3)
    _seed_traffic("app", _at(today), 500, n=2)          # errors, on `app`
    _seed_traffic("admin", _at(today), 404, n=1)        # error, on `admin`
    s = make_staff()
    c = staff_login(s["email"], s["password"])
    d = c.get(URL + "?days=7").json()

    assert set(d["series"]) == {"errors", "marketing", "app", "admin"}
    assert d["days"] == 7 and d["unit"] == "count"
    # the site series count EVERY request on that site, errors included
    assert d["series"]["marketing"]["total"] >= 3
    assert d["series"]["app"]["total"] >= 2
    assert d["series"]["admin"]["total"] >= 1
    # and errors is the cross-site total of status >= 400: 2 on app + 1 on admin
    assert d["series"]["errors"]["total"] >= 3, (
        "errors is not summing across sites; it may be reading `site`='errors'")


def test_every_day_in_the_window_is_present_even_when_nothing_happened(
        make_staff, staff_login):
    """A missing day is a gap in the sparkline, which reads as "no data" rather
    than "nothing happened". Zero-filled, so seven days means seven points."""
    s = make_staff()
    c = staff_login(s["email"], s["password"])
    for days in (7, 30):
        d = c.get(f"{URL}?days={days}").json()
        for key, ser in d["series"].items():
            assert len(ser["points"]) == days, (
                "%s returned %d points for days=%d" % (key, len(ser["points"]), days))
            assert all(isinstance(p["value"], int) for p in ser["points"]), (
                "%s has a non-integer point; a gap was left instead of a zero" % key)


def test_a_day_is_decided_by_its_utc_date(make_staff, staff_login):
    """23:30 UTC belongs to that UTC day. A naive cast puts it in the local one,
    which is how P3's timestamptz bug got in -- and on a 7-day window a whole
    day landing in the wrong bucket moves both ends of the delta."""
    today = _today()
    late = _at(today, hour=23) + timedelta(minutes=30)
    _seed_traffic("marketing", late, 200, n=5)
    s = make_staff()
    c = staff_login(s["email"], s["password"])
    d = c.get(URL + "?days=7").json()
    pts = {p["day"]: p["value"] for p in d["series"]["marketing"]["points"]}
    assert pts.get(today.isoformat(), 0) >= 5, (
        "a 23:30 UTC event did not land on its own UTC day: %s" % pts)


def test_an_empty_prior_window_is_null_and_never_zero(make_staff, staff_login):
    """A percentage against nothing is unanswerable, not 0%. The front end
    renders None as 'no prior data' for exactly this reason, and a 0.0 here
    would state that traffic was flat when it actually appeared from nothing.

    ⚠ ON `marketing`, WHICH IS THE ONLY SITE THIS SUITE CANNOT MANUFACTURE.
    TrafficMiddleware is mounted on the customer API as site='app' and on the
    admin API as site='admin' (app/main.py), so every request any test makes
    writes an ambient row on one of those two -- and it writes them on a
    ThreadPoolExecutor, so a row queued by the previous test can land after
    _clean_db has already truncated. Written first against `app`, guarded by
    `if prev_total == 0`, this assertion would simply stop running the moment
    an ambient row appeared in the prior window: green, and testing nothing.
    Nothing is mounted as `marketing`, so prev_total is 0 by construction and
    the assertion always fires.
    """
    today = _today()
    # one event today, nothing before it -> prev_total is 0 for certain
    _seed_traffic("marketing", _at(today), 200, n=1)
    s = make_staff()
    c = staff_login(s["email"], s["password"])
    d = c.get(URL + "?days=1").json()          # window = today only
    mkt = d["series"]["marketing"]
    assert mkt["total"] >= 1, "the seeded event is not in the window"
    assert mkt["prev_total"] == 0, (
        "something wrote marketing traffic; this test's premise is gone")
    assert mkt["delta_pct"] is None, (
        "an empty prior window produced %r instead of null" % mkt["delta_pct"])


def test_a_real_prior_window_produces_a_real_percentage(make_staff, staff_login):
    """The other half: a function that returned null whenever it was unsure
    would satisfy the test above and never report a change at all."""
    today = _today()
    _seed_traffic("admin", _at(today), 200, n=4)              # current day
    _seed_traffic("admin", _at(today - timedelta(days=1)), 200, n=2)   # prior day
    s = make_staff()
    c = staff_login(s["email"], s["password"])
    d = c.get(URL + "?days=1").json()
    admin = d["series"]["admin"]
    assert admin["prev_total"] >= 2
    assert admin["delta_pct"] is not None, "a comparable window produced no delta"


def test_the_window_bounds_exclude_what_falls_outside_it(make_staff, staff_login):
    """The filter is on the raw created_at column so the planner can prune
    partitions; this asserts that rewriting it that way did not move the
    boundary. An event 40 days back must not appear in a 7-day window."""
    today = _today()
    _seed_traffic("marketing", _at(today - timedelta(days=40)), 200, n=99)
    s = make_staff()
    c = staff_login(s["email"], s["password"])
    d = c.get(URL + "?days=7").json()
    assert d["series"]["marketing"]["total"] < 99, (
        "an event 40 days old is inside the 7-day window")


def test_the_metric_endpoint_still_names_exactly_its_four_metrics(
        make_staff, staff_login):
    """C2 deliberately did NOT extend _TS_METRICS, so the 422 that lists its
    members in words stays true.

    ASSERTED BY ASKING, NOT BY READING THE MESSAGE. Written first as a check
    that the detail string does not contain "marketing" -- and it stayed GREEN
    when _TS_METRICS gained "marketing", because the message is a hardcoded
    sentence and widening the set does not touch it. That is the shape this
    guard exists to catch: the set and its own description drifting apart. So
    it asks the endpoint what it actually accepts.
    """
    s = make_staff()
    c = staff_login(s["email"], s["password"])
    r = c.get("/admin/v1/stats/timeseries?metric=nope")
    assert r.status_code == 422
    detail = r.json()["detail"]
    for named in ("interactions", "breaches", "signups", "revenue"):
        assert named in detail, "the 422 no longer names %s" % named
        assert c.get(f"/admin/v1/stats/timeseries?metric={named}").status_code == 200, (
            "%s is named in the 422 but not accepted" % named)
    for foreign in ("marketing", "admin", "errors", "app"):
        assert c.get(
            f"/admin/v1/stats/timeseries?metric={foreign}").status_code == 422, (
            "%s is accepted by /stats/timeseries but is not in its 422 message; "
            "it belongs to /stats/traffic-timeseries" % foreign)
