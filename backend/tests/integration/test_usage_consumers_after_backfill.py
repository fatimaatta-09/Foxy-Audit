"""R3 · the six readers of usage_daily become correct without being touched.

This is the proof that fixing the TABLE was the right move rather than migrating
a fourth reader off it. Not one consumer query changes in this phase; each is
called through its real endpoint after the backfill and checked against the
ledger.

Two of them read ALL of history with no date filter -- `platform_stats` sums
logs_count and breach_count for "Audited interactions" and "Policy breaches", the
console's two headline KPI cards -- which is why the backfill is not optional.

Run with DATABASE_URL pointing at :5433/foxy_pytest.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app import usage
from app.db import SessionLocal


def _utc_midnight(days_ago: int):
    n = datetime.now(timezone.utc)
    return datetime(n.year, n.month, n.day, tzinfo=timezone.utc) - timedelta(days=days_ago)


def _seed(oid, n, at, breach=False, seq0=0):
    db = SessionLocal()
    try:
        db.execute(text(
            "INSERT INTO audit_logs (id, org_id, seq, prompt_hash, response_hash,"
            " token_count, policy_tag, prev_hash, chain_hash, agent, grading_status,"
            " gemini_verdict, created_at) SELECT gen_random_uuid(), :oid, :seq0 + g,"
            " md5(g::text)||md5(g::text), md5(g::text)||md5(g::text), 3, 'test',"
            " md5(g::text)||md5(g::text), md5(g::text)||md5(g::text), 'agent-x',"
            " 'graded', CAST(:verdict AS jsonb), :at FROM generate_series(1, :n) g"),
            {"oid": uuid.UUID(str(oid)), "n": n, "at": at, "seq0": seq0,
             "verdict": '{"policy_breach": true}' if breach else '{"policy_breach": false}'})
        db.commit()
    finally:
        db.close()


def _damage_everything():
    """Halve every stored count -- the shape the rolling window left behind."""
    db = SessionLocal()
    try:
        db.execute(text("UPDATE usage_daily SET logs_count = logs_count / 2, "
                        "breach_count = breach_count / 2"))
        db.commit()
    finally:
        db.close()


def _backfill():
    db = SessionLocal()
    try:
        return usage.backfill(db)
    finally:
        db.close()


def _seed_history(oid):
    """Events across three months, most of it far outside any rollup window."""
    _seed(oid, 30, _utc_midnight(1) + timedelta(hours=6), seq0=0)
    _seed(oid, 20, _utc_midnight(20) + timedelta(hours=6), seq0=1000)
    _seed(oid, 10, _utc_midnight(50) + timedelta(hours=6), breach=True, seq0=2000)
    return 60, 10          # total events, total breaches


def test_the_two_headline_kpi_cards_are_correct_after_the_backfill(
        make_staff, staff_login, make_org):
    """admin_stats.platform_stats sums logs_count and breach_count with NO date
    filter, so it reads every damaged row ever written. These are the two
    largest numbers on the console overview.

    The consumer query is untouched by this phase -- that is the point.
    """
    org = make_org()
    total, breaches = _seed_history(org["org_id"])
    _backfill()
    _damage_everything()

    s = make_staff()
    c = staff_login(s["email"], s["password"])
    before = c.get("/admin/v1/stats").json()
    assert before["total_logs"] < total, (
        "precondition: the damage did not take, so this proves nothing")

    _backfill()
    after = c.get("/admin/v1/stats").json()
    assert after["total_logs"] == total, (
        "Audited interactions reads %s of %s" % (after["total_logs"], total))
    assert after["total_breaches"] == breaches, (
        "Policy breaches reads %s of %s" % (after["total_breaches"], breaches))


def test_the_overview_trend_and_its_sparks_are_correct_after_the_backfill(
        make_staff, staff_login, make_org):
    """admin_stats._TS_METRICS interactions/breaches -- C1's kLogs and kBreaches
    sparklines and _ovDelta's 30-day trend. A 90-day window reaches far outside
    anything the rollup ever recomputed."""
    org = make_org()
    total, breaches = _seed_history(org["org_id"])
    _backfill()
    _damage_everything()

    s = make_staff()
    c = staff_login(s["email"], s["password"])
    _backfill()

    d = c.get("/admin/v1/stats/timeseries?metric=interactions&days=90").json()
    assert d["total"] == total, "the interactions series totals %s of %s" % (
        d["total"], total)
    b = c.get("/admin/v1/stats/timeseries?metric=breaches&days=90").json()
    assert b["total"] == breaches


def test_org360s_per_org_chart_is_correct_without_being_touched(
        make_staff, staff_login, make_org):
    """Register #120's other half. admin_orgs reads usage_daily over a `days`
    window for the org drill-down; for any days > 2 it was reading rows the
    rolling window had already eroded.

    NOT MIGRATED OFF THE TABLE, deliberately -- three readers have already left
    rather than fix this, and a fourth would be the wrong lesson. It is correct
    now because the table is.
    """
    org = make_org()
    total, _ = _seed_history(org["org_id"])
    _backfill()
    _damage_everything()

    s = make_staff()
    c = staff_login(s["email"], s["password"])
    _backfill()

    d = c.get(f"/admin/v1/organizations/{org['org_id']}/overview?days=90").json()
    assert sum(p["logs"] for p in d["usage"]) == total, (
        "org360's chart sums %s of %s"
        % (sum(p["logs"] for p in d["usage"]), total))


def test_top_orgs_and_health_trends_agree_with_the_ledger(
        make_staff, staff_login, make_org):
    """The remaining two aggregate readers: admin_stats.top_orgs (usage over a
    window) and admin_health.health_trends (grading throughput, which feeds C1's
    hFailed spark). Both are checked against the ledger rather than a constant,
    so neither can pass by coincidence."""
    org = make_org()
    total, _ = _seed_history(org["org_id"])
    _backfill()
    _damage_everything()
    _backfill()

    s = make_staff()
    c = staff_login(s["email"], s["password"])

    db = SessionLocal()
    try:
        truth_90 = db.execute(text(
            "SELECT count(*) FROM audit_logs WHERE org_id = :oid AND created_at >= "
            "(date_trunc('day', now() AT TIME ZONE 'UTC') - interval '89 days') "
            "AT TIME ZONE 'UTC'"), {"oid": uuid.UUID(str(org["org_id"]))}).scalar_one()
        graded_90 = db.execute(text(
            "SELECT count(*) FROM audit_logs WHERE grading_status = 'graded' AND "
            "created_at >= (date_trunc('day', now() AT TIME ZONE 'UTC') "
            "- interval '89 days') AT TIME ZONE 'UTC'")).scalar_one()
    finally:
        db.close()

    top = c.get("/admin/v1/stats/top-orgs?days=90&limit=10").json()["items"]
    mine = [t for t in top if t["org_id"] == str(org["org_id"])]
    assert mine and mine[0]["logs"] == truth_90, (
        "top-orgs reports %s, the ledger says %s"
        % (mine[0]["logs"] if mine else None, truth_90))

    tr = c.get("/admin/v1/health/trends?days=90").json()
    assert sum(g["graded"] for g in tr["grading"]) == graded_90, (
        "health trends sums %s graded, the ledger says %s"
        % (sum(g["graded"] for g in tr["grading"]), graded_90))
    assert total == truth_90, "the test's own window arithmetic drifted"
