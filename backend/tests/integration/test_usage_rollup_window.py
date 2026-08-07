"""R3 · the rollup window writes whole days, and history is repairable.

Register #121. `_ROLLUP_SQL` used to select `created_at >= now() - interval
'2 days'` -- a ROLLING window. A rolling 48h spans THREE calendar days and its
oldest day is a partial slice, and ON CONFLICT DO UPDATE writes that partial
OVER a complete row. At a 300s cadence the FINAL value stored for a day was
whatever was true in its last few minutes.

⚠ WHAT ACTUALLY REPRODUCES IT — this phase's brief said a single run cannot,
and building the guard disproved that. The rolling bound lands at the CURRENT
TIME OF DAY two days ago, so it cuts THROUGH the window's oldest day on EVERY
pass. One run already understates that day. What the repeated passes add is
permanence: each one rewrites the same day from a slice that keeps shrinking
until the day drops out entirely. Both are guarded below, separately, because
they are two different failures and a fix could plausibly address one only.

Run with DATABASE_URL pointing at :5433/foxy_pytest -- conftest defaults to 5432
and will otherwise use the wrong database without saying so.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app import usage
from app.db import SessionLocal


def _h(x: str) -> str:
    return hashlib.sha256(x.encode()).hexdigest()


def _seed(oid, n, at, seq0=0):
    """n ledger rows at an exact instant, in one statement."""
    db = SessionLocal()
    try:
        db.execute(text(
            "INSERT INTO audit_logs (id, org_id, seq, prompt_hash, response_hash,"
            " token_count, policy_tag, prev_hash, chain_hash, agent, grading_status,"
            " created_at) SELECT gen_random_uuid(), :oid, :seq0 + g,"
            " md5(g::text)||md5(g::text), md5(g::text)||md5(g::text), 3, 'test',"
            " md5(g::text)||md5(g::text), md5(g::text)||md5(g::text), 'agent-x',"
            " 'graded', :at FROM generate_series(1, :n) g"),
            {"oid": uuid.UUID(str(oid)), "n": n, "at": at, "seq0": seq0})
        db.commit()
    finally:
        db.close()


def _stored(oid):
    """{day: logs_count} as usage_daily currently holds it."""
    db = SessionLocal()
    try:
        return {r[0].isoformat(): int(r[1]) for r in db.execute(text(
            "SELECT day, logs_count FROM usage_daily WHERE org_id = :oid"),
            {"oid": uuid.UUID(str(oid))}).all()}
    finally:
        db.close()


def _truth(oid):
    """{day: count} straight from the ledger, bucketed in UTC — the second
    source. This equality IS the test: it is what /v1/usage and the admin quota
    meter both compute the long way."""
    db = SessionLocal()
    try:
        return {r[0].isoformat(): int(r[1]) for r in db.execute(text(
            "SELECT date_trunc('day', created_at AT TIME ZONE 'UTC')::date, count(*) "
            "FROM audit_logs WHERE org_id = :oid GROUP BY 1"),
            {"oid": uuid.UUID(str(oid))}).all()}
    finally:
        db.close()


def _rollup():
    db = SessionLocal()
    try:
        usage.rollup_recent(db)
    finally:
        db.close()


def _utc_midnight(days_ago: int):
    n = datetime.now(timezone.utc)
    return datetime(n.year, n.month, n.day, tzinfo=timezone.utc) - timedelta(days=days_ago)


# ── the window ───────────────────────────────────────────────────────────────

def test_the_oldest_day_in_the_window_is_counted_whole(make_org):
    """THE GUARD THIS PHASE EXISTS FOR, and it FAILS on the old window.

    The reproduction is simpler than "run it many times", and understanding why
    is the point. A rolling bound of `now() - interval '2 days'` lands at the
    CURRENT TIME OF DAY two days ago, so it cuts THROUGH that day: everything
    before that clock time is outside the window and is not counted, and the
    upsert then writes the truncated count over whatever was there. So a single
    pass already understates the window's oldest day — the repeated passes are
    what make the damage permanent, not what create it.

    Two events on that day, one at 00:00 UTC and one at midday. Under the old
    bound the 00:00 event is excluded at every run time after 00:00:00.000 and
    the day stores 1; anchored to date_trunc('day', ...) the bound IS midnight,
    so it stores 2.
    """
    org = make_org()
    oid = org["org_id"]
    day = _utc_midnight(2)                      # the window's oldest whole day
    _seed(oid, 1, day, seq0=0)                  # 00:00:00 — the row the old bound drops
    _seed(oid, 1, day + timedelta(hours=12), seq0=50)

    _rollup()

    stored = _stored(oid)
    assert stored.get(day.date().isoformat()) == 2, (
        "the window's oldest day stored %s of 2 events — the bound is cutting "
        "through the day instead of sitting on its boundary"
        % stored.get(day.date().isoformat()))
    assert stored == _truth(oid), (
        "usage_daily disagrees with the ledger: stored=%s truth=%s"
        % (stored, _truth(oid)))


def test_repeated_passes_do_not_erode_a_day(make_org):
    """The other half: the damage was PERMANENT because every pass rewrote the
    same day from a smaller slice. Days across the whole window are rolled up
    six times, exactly as the worker does, and every stored value must still
    equal the ledger afterwards."""
    org = make_org()
    oid = org["org_id"]
    for d in range(3):                          # 0, 1, 2 days ago — all in window
        _seed(oid, 10 + d, _utc_midnight(d) + timedelta(hours=9), seq0=d * 100)

    for _ in range(6):
        _rollup()

    assert _stored(oid) == _truth(oid), (
        "after six passes usage_daily disagrees with the ledger: stored=%s "
        "truth=%s" % (_stored(oid), _truth(oid)))


def test_a_day_is_not_rewritten_once_it_is_complete(make_org):
    """The mechanism, isolated. A day inside the window is rewritten every pass
    (correct -- today is in progress). A day OUTSIDE it must be left exactly as
    it was, so late-arriving rows cannot silently shrink it either.

    Asserted by writing a sentinel over an old day and confirming the rollup
    does not touch it -- which also proves the window has a lower bound at all.
    """
    org = make_org()
    oid = org["org_id"]
    old = _utc_midnight(5) + timedelta(hours=4)
    _seed(oid, 7, old)
    _rollup()
    assert _stored(oid).get(old.date().isoformat()) is None, (
        "the window reaches 5 days back; this test cannot show what it means to "
        "leave it")

    db = SessionLocal()
    try:
        db.execute(text(
            "INSERT INTO usage_daily (id, org_id, day, logs_count) VALUES "
            "(gen_random_uuid(), :oid, :day, 999)"),
            {"oid": uuid.UUID(str(oid)), "day": old.date()})
        db.commit()
    finally:
        db.close()

    for _ in range(4):
        _rollup()
    assert _stored(oid)[old.date().isoformat()] == 999, (
        "the rollup rewrote a day that is outside its window")


def test_the_window_is_anchored_to_a_whole_day_not_to_now(make_org):
    """The distinction the fix turns on, asserted on the SQL the worker runs.

    `- interval '2 days'` is the same number the broken version used; the fix is
    `date_trunc('day', ...)`. A future edit that "simplifies" the boundary back
    to a bare now() restores the defect while leaving the interval untouched,
    and every value-based guard above would still pass on the day it was run.
    """
    sql = str(usage._ROLLUP_SQL)
    assert "date_trunc('day', now() AT TIME ZONE 'UTC')" in sql, (
        "the window is no longer anchored to a whole UTC day")
    assert "now() - interval" not in sql, "the rolling window is back"


def test_every_day_the_rollup_writes_is_a_utc_day(make_org):
    """`date_trunc('day', created_at)` evaluates in the SESSION timezone, so the
    stored day used to be whatever calendar day the DB server sat in -- this
    machine is Asia/Karachi, where 22:30 UTC on the 7th became the 8th. All six
    readers compute their bounds with datetime.now(timezone.utc).date().

    An event placed at 22:30 UTC must land on its own UTC day whatever the
    session is set to; the session is switched here to prove it is invariant
    rather than merely correct on this machine.
    """
    org = make_org()
    oid = org["org_id"]
    late = _utc_midnight(1) + timedelta(hours=22, minutes=30)
    _seed(oid, 4, late)

    for tz in ("Asia/Karachi", "UTC", "America/Los_Angeles"):
        db = SessionLocal()
        try:
            # SET takes no bind parameters; tz comes from the literal tuple
            # above, so the interpolation carries no input.
            db.execute(text("SET TimeZone = '%s'" % tz))
            usage.rollup_recent(db)
        finally:
            db.close()
        assert _stored(oid).get(late.date().isoformat()) == 4, (
            "under TimeZone=%s the event landed on %s instead of its UTC day %s"
            % (tz, list(_stored(oid)), late.date().isoformat()))


# ── the backfill ─────────────────────────────────────────────────────────────

def test_the_backfill_corrects_a_row_the_old_window_left_wrong(make_org):
    """A deliberately wrong row -- exactly the shape the rolling window left
    behind, a count far below the truth -- is corrected from the ledger."""
    org = make_org()
    oid = org["org_id"]
    old = _utc_midnight(9) + timedelta(hours=3)
    _seed(oid, 40, old)

    db = SessionLocal()
    try:
        db.execute(text(
            "INSERT INTO usage_daily (id, org_id, day, logs_count, tokens_sum) "
            "VALUES (gen_random_uuid(), :oid, :day, 2, 6)"),
            {"oid": uuid.UUID(str(oid)), "day": old.date()})
        db.commit()
        assert _stored(oid)[old.date().isoformat()] == 2, "precondition"
        usage.backfill(db)
    finally:
        db.close()

    assert _stored(oid)[old.date().isoformat()] == 40, (
        "the backfill left a damaged row at %s" % _stored(oid))
    assert _stored(oid) == _truth(oid), "the table still disagrees with the ledger"


def test_the_backfill_covers_days_no_rollup_window_can_reach(make_org):
    """The reason it is not optional: two consumers read ALL of history with no
    date filter, so a day months old still feeds the console's two headline KPI
    cards. Spread across three months to exercise the month chunking."""
    org = make_org()
    oid = org["org_id"]
    for i, days_ago in enumerate((5, 40, 75)):
        _seed(oid, 11 + i, _utc_midnight(days_ago) + timedelta(hours=6), seq0=i * 500)

    db = SessionLocal()
    try:
        months = usage.backfill(db)
    finally:
        db.close()
    assert months >= 3, "expected at least 3 month chunks, ran %s" % months
    assert _stored(oid) == _truth(oid), (
        "stored=%s truth=%s" % (_stored(oid), _truth(oid)))


def test_the_backfill_is_idempotent(make_org):
    """It runs on deploy and may be re-run by hand. Twice must equal once."""
    org = make_org()
    oid = org["org_id"]
    _seed(oid, 13, _utc_midnight(20) + timedelta(hours=8))
    db = SessionLocal()
    try:
        usage.backfill(db)
        once = _stored(oid)
        usage.backfill(db)
        twice = _stored(oid)
    finally:
        db.close()
    assert once == twice == _truth(oid), "%s vs %s" % (once, twice)


def test_the_backfill_does_nothing_on_an_empty_ledger():
    """A fresh deployment runs this migration with no audit_logs at all. It must
    not raise on min()/max() returning NULL."""
    db = SessionLocal()
    try:
        assert usage.backfill(db) == 0
    finally:
        db.close()

def test_the_backfill_removes_a_row_with_no_ledger_behind_it(make_org):
    """FOUND BY RUNNING THE REAL MIGRATION, not by reasoning: against damaged
    data it repaired 75 -> 3001 where the ledger held 3000.

    An upsert can only correct days it recomputes, so it cannot remove a row
    that should not exist at all. Two ways one appears: the pre-R3 bucketing
    resolved in the SESSION timezone, so re-bucketing to UTC moves an event to
    the previous day and STRANDS the old row; and anything written by hand.
    `platform_stats` sums this table with NO date filter, so a single stranded
    row inflates "Audited interactions" on the console forever.
    """
    org = make_org()
    oid = org["org_id"]
    _seed(oid, 6, _utc_midnight(30) + timedelta(hours=5))

    db = SessionLocal()
    try:
        db.execute(text(
            "INSERT INTO usage_daily (id, org_id, day, logs_count) VALUES "
            "(gen_random_uuid(), :oid, :day, 4)"),
            {"oid": uuid.UUID(str(oid)), "day": _utc_midnight(31).date()})
        db.commit()
        assert sum(_stored(oid).values()) != sum(_truth(oid).values()), "precondition"
        usage.backfill(db)
    finally:
        db.close()

    assert _stored(oid) == _truth(oid), (
        "a day with no events behind it survived: stored=%s truth=%s"
        % (_stored(oid), _truth(oid)))


def test_the_orphan_sweep_does_not_take_real_days_with_it(make_org):
    """The other side, and the one that would be catastrophic: the sweep runs
    over the WHOLE table, so a bug in its NOT EXISTS would silently empty
    usage_daily. Every day that DOES have events must survive it."""
    org = make_org()
    oid = org["org_id"]
    for d in (2, 15, 45):
        _seed(oid, 5, _utc_midnight(d) + timedelta(hours=7), seq0=d * 100)
    db = SessionLocal()
    try:
        usage.backfill(db)
    finally:
        db.close()
    assert len(_stored(oid)) == 3, (
        "the sweep removed real days: %s" % _stored(oid))
    assert _stored(oid) == _truth(oid)
