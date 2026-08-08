"""#123 · a day bucket must name the same day in every session timezone.

THE BUG CLASS. `date_trunc('day', <timestamptz>)` and `<timestamptz>::date`
resolve in the **session** timezone, and nothing in this project pins it — not
`app/db.py`, not the engine, not the compose file. R3 fixed `usage.py` and
pinned it to UTC; the class stayed open on three customer endpoints.

WHY IT HID. The two places this code runs skew in OPPOSITE directions, and
CI — the only one that gates anything — used to skew not at all:

    Asia/Karachi  (UTC+5, the dev box)  22:30Z on the 7th -> the 8th   (early)
    America/LA    (UTC-7, now CI)       02:30Z on the 8th -> the 7th   (late)
    UTC           (CI, before this)     both              -> correct

So the class reproduced on the developer's machine and vanished in CI, exactly
backwards. Pinning CI west of UTC is half the fix; these guards are the other
half, because they do not depend on where they run: each one drives the shipped
endpoint under THREE session zones and asserts the answer never moves.

⚠ THE OVERRIDE GOES ON `customer_api`, NOT `app`. main.py is a three-ASGI split
and these routes belong to the mounted sub-application; an override on the bare
parent never reaches them and the request quietly runs on an unpinned session.
F3 shipped that mistake and its guard passed while proving nothing, so the
fixture RECORDS what it actually set and every test asserts the recording.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import SessionLocal, get_db
from app.main import app, customer_api

# One zone each side of UTC, plus UTC itself. Karachi is the developer's own
# database; Los_Angeles is what CI now runs. If a bucket is computed in the
# session zone, these three disagree — that IS the defect.
ZONES = ("UTC", "America/Los_Angeles", "Asia/Karachi")


def _straddling_instants() -> tuple[datetime, datetime]:
    """Two UTC instants on a known-quiet recent day that land on DIFFERENT
    calendar days east and west of UTC.

    ⚠ BOTH ARE ON THE SAME UTC DAY, and that is the whole design. 02:30Z and
    22:30Z sit either side of a UTC day's middle, so a correct implementation
    puts them in ONE bucket while a session-zone one splits them into two: west
    of UTC the early instant falls back a day, east of it the late instant jumps
    forward. "Two events, one day" is the crisp statement of correctness, and it
    fails in both skew directions rather than only one.

    Anchored two days back so both sit inside the 7-day window the narrowest
    endpoint uses."""
    base = (datetime.now(timezone.utc) - timedelta(days=2)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    return base + timedelta(hours=2, minutes=30), base + timedelta(hours=22, minutes=30)


def _seed(org_id: str, when: datetime, seq: int, *, breach: bool) -> None:
    """One graded audit_logs row at an exact UTC instant.

    Written with an explicit `timestamptz` literal rather than a Python default,
    so the row's instant is fixed by the test and not by whatever the server
    process thinks the local time is."""
    h = hashlib.sha256(f"{org_id}:{seq}".encode()).hexdigest()
    verdict = ('{"policy_breach": true, "risk_score": 80, "decision": "breach"}'
               if breach else
               '{"policy_breach": false, "risk_score": 5, "decision": "clean"}')
    db = SessionLocal()
    try:
        db.execute(text("""
            INSERT INTO audit_logs (id, org_id, seq, prompt_hash, response_hash,
                                    token_count, policy_tag, prev_hash, chain_hash,
                                    grading_status, gemini_verdict, created_at)
            VALUES (:id, :org, :seq, :h, :h, 7, 'test', :h, :h, 'graded',
                    CAST(:v AS jsonb), CAST(:ts AS timestamptz))
        """), {"id": uuid.uuid4(), "org": org_id, "seq": seq, "h": h,
               "v": verdict, "ts": when.isoformat()})
        db.commit()
    finally:
        db.close()


@pytest.fixture
def in_zone():
    """Drive an endpoint with the request's own DB session pinned to `zone`.

    Returns (client_factory, seen) — `seen` accumulates the zone each overridden
    session actually reported, so a test can prove the override fired instead of
    assuming it. An override that never runs leaves `seen` empty."""
    seen: list[str] = []

    def _client(zone: str) -> TestClient:
        def _db():
            db = SessionLocal()
            try:
                # SET takes no bind parameter; `zone` comes from ZONES, never
                # from a request, and is checked against it before interpolation.
                assert zone in ZONES, zone
                # SET LOCAL, not SET: transaction-scoped explicitly rather than
                # relying on SQLAlchemy having begun one, so a pooled connection
                # can never go back to the pool carrying a foreign zone. If it
                # ever ran outside a transaction it would silently do nothing —
                # which is exactly what the SHOW TimeZone recorded below catches.
                db.execute(text(f"SET LOCAL TimeZone = '{zone}'"))
                seen.append(db.execute(text("SHOW TimeZone")).scalar_one())
                yield db
            finally:
                db.rollback()
                db.close()
        customer_api.dependency_overrides[get_db] = _db
        return TestClient(app)

    yield _client, seen
    customer_api.dependency_overrides.pop(get_db, None)


def _assert_probe_fired(seen: list[str], zone: str) -> None:
    assert seen, ("the dependency override never ran — the request used an "
                  "unpinned session and this assertion proves nothing")
    assert seen[-1] == zone, (
        "asked for %s, the request's session reported %s" % (zone, seen[-1]))


# ── the premise ─────────────────────────────────────────────────────────────
def test_the_two_zones_really_do_disagree_about_the_day() -> None:
    """THE PREMISE, MEASURED IN THE DATABASE UNDER TEST. If these agreed, every
    guard below would pass for the wrong reason — there would be no skew to be
    independent of. Also documents the direction of each skew."""
    early, late = _straddling_instants()
    db = SessionLocal()
    try:
        got = {}
        for z in ZONES:
            db.execute(text(f"SET LOCAL TimeZone = '{z}'"))
            got[z] = tuple(
                str(db.execute(text(
                    "SELECT date_trunc('day', CAST(:t AS timestamptz))::date"
                ), {"t": t.isoformat()}).scalar_one())
                for t in (early, late))
    finally:
        db.rollback()
        db.close()

    utc_early, utc_late = got["UTC"]
    assert got["America/Los_Angeles"][0] != utc_early, (
        "west of UTC did not roll the early instant back: %s" % (got,))
    assert got["Asia/Karachi"][1] != utc_late, (
        "east of UTC did not roll the late instant forward: %s" % (got,))
    assert got["UTC"] == (utc_early, utc_late)   # UTC is the reference, unmoved


# ── the three endpoints the pin surfaced ────────────────────────────────────
def test_v1_usage_buckets_by_utc_day_in_every_zone(make_org, in_zone) -> None:
    """`/v1/usage` builds its window bound in UTC (`tzinfo=timezone.utc`) and
    grouped in the session zone — so the bound and the buckets disagreed. Its own
    docstring claims it reuses "the rollup's own expressions"; the rollup pins
    UTC (R3) and this did not, so the two were never the same expression."""
    org = make_org()
    early, late = _straddling_instants()
    _seed(org["org_id"], early, 1, breach=False)
    _seed(org["org_id"], late, 2, breach=False)
    client, seen = in_zone

    answers = {}
    for z in ZONES:
        r = client(z).get("/v1/usage?days=30", headers=org["auth"])
        assert r.status_code == 200, r.text
        _assert_probe_fired(seen, z)
        answers[z] = {d["day"]: d["logs_count"] for d in r.json()["days"]}

    assert len(set(map(str, answers.values()))) == 1, (
        "the daily series changes with the session timezone: %s" % answers)
    # both instants are the same UTC day, so a correct bucket holds two events
    # in one key. A session-zone bucket splits them, in either direction.
    assert early.date() == late.date(), "the fixture stopped straddling one UTC day"
    assert answers["UTC"] == {early.date().isoformat(): 2}, answers["UTC"]


def test_v1_analytics_timeseries_buckets_by_utc_day_in_every_zone(make_org,
                                                                 in_zone) -> None:
    """The threat timeline. Its labels are the days the dashboard draws, and
    F1 gave its last bar a focal mark — so a zone-shifted label puts the
    emphasis on the wrong day as well as the count."""
    org = make_org()
    early, late = _straddling_instants()
    _seed(org["org_id"], early, 1, breach=True)
    _seed(org["org_id"], late, 2, breach=True)
    client, seen = in_zone

    answers = {}
    for z in ZONES:
        r = client(z).get("/v1/analytics/timeseries?days=30", headers=org["auth"])
        assert r.status_code == 200, r.text
        _assert_probe_fired(seen, z)
        answers[z] = {d["day"]: d["total"] for d in r.json()["days"]}

    assert len(set(map(str, answers.values()))) == 1, (
        "the threat timeline's days change with the session timezone: %s" % answers)
    # both instants are the same UTC day, so a correct bucket holds two events
    # in one key. A session-zone bucket splits them, in either direction.
    assert early.date() == late.date(), "the fixture stopped straddling one UTC day"
    assert answers["UTC"] == {early.date().isoformat(): 2}, answers["UTC"]


def test_v1_stats_activity_buckets_by_utc_day_in_every_zone(make_org,
                                                            in_zone) -> None:
    """`activity_7d` — the dashboard's seven-day bar chart, and the one whose
    LAST bucket F1 labels "today". A zone that rolls the day back moves an
    early-morning event out of today and into yesterday's bar."""
    org = make_org()
    early, late = _straddling_instants()
    _seed(org["org_id"], early, 1, breach=False)
    _seed(org["org_id"], late, 2, breach=True)
    client, seen = in_zone

    answers = {}
    for z in ZONES:
        r = client(z).get("/v1/stats", headers=org["auth"])
        assert r.status_code == 200, r.text
        _assert_probe_fired(seen, z)
        answers[z] = {d["date"]: d["count"] for d in r.json()["activity_7d"]}

    assert len(set(map(str, answers.values()))) == 1, (
        "activity_7d changes with the session timezone: %s" % answers)
    # both instants are the same UTC day, so a correct bucket holds two events
    # in one key. A session-zone bucket splits them, in either direction.
    assert early.date() == late.date(), "the fixture stopped straddling one UTC day"
    assert answers["UTC"] == {early.date().isoformat(): 2}, answers["UTC"]


# ── the census, held in place ───────────────────────────────────────────────
def test_no_unpinned_day_bucket_returns_to_the_customer_routers() -> None:
    """A STATIC BACKSTOP, and it is honest about being one. The three guards
    above execute the endpoints and are the real proof; this one exists because
    the next date bucket somebody adds will be in a router these tests do not
    call, and a census that only lives in a phase report rots immediately.

    The idiom this file requires is the one admin_stats.py already documents:
    `timezone('UTC', ts)::date` IS the UTC calendar day."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[2] / "app"
    offenders = []
    for path in sorted(root.rglob("*.py")):
        src = path.read_text(encoding="utf-8")
        # strip comments and docstrings' prose mentions of date_trunc
        for m in re.finditer(r"date_trunc\(\s*[\"']([a-z]+)[\"']\s*,\s*([^)]*)\)", src):
            unit, arg = m.group(1), m.group(2)
            line = src[:m.start()].count("\n") + 1
            if re.search(r"^\s*#", src.splitlines()[line - 1]):
                continue
            if "AT TIME ZONE" in arg or "timezone(" in arg:
                continue
            offenders.append(f"{path.name}:{line}  date_trunc('{unit}', {arg.strip()})")

    # traffic partition bounds are filed, not fixed — see the phase report and
    # the note in usage.py. Any OTHER unpinned bucket is a regression.
    allowed = {"usage.py"}
    unexpected = [o for o in offenders if o.split(":")[0] not in allowed]
    assert not unexpected, (
        "unpinned day/month buckets on a timestamptz:\n  " + "\n  ".join(unexpected))


# ── the pin itself, guarded in the repo rather than in a phase report ───────
def _ci_yaml() -> dict:
    import pathlib

    import yaml
    root = pathlib.Path(__file__).resolve().parents[3]
    return yaml.safe_load((root / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"))


def test_ci_runs_postgres_at_a_non_utc_timezone() -> None:
    """GUARD THE USE (A6). Everything above proves the ENDPOINTS are timezone
    independent; this proves CI still exercises a skewed database, which is what
    catches the next date expression nobody thought to write a test for.

    Parsed as YAML rather than grepped, so a `TZ:` inside a comment cannot
    satisfy it -- my own throwaway version of this check was fooled by exactly
    that during the phase."""
    job = _ci_yaml()["jobs"]["backend-integration"]
    env = job["services"]["postgres"]["env"]
    tz = env.get("TZ")
    assert tz, "the postgres service lost its TZ, so CI is UTC-blind again"
    assert tz not in ("UTC", "Etc/UTC", "GMT", "Etc/GMT", "Z"), (
        "CI is back at %s, which is the blindness register #123 is about" % tz)


def test_ci_does_not_set_the_client_timezone_as_well() -> None:
    """⚠ THE CHECK MUST NOT PROVE ITSELF. PGTZ on the job would make libpq
    request a zone per session, so `SHOW TimeZone` would come back non-UTC even
    if the SERVER sat at UTC -- the verify step would pass while the suite ran
    UTC-blind. The server default is the only thing that makes the other 1139
    tests exercise a skewed database, so only the server is set."""
    y = _ci_yaml()
    job = y["jobs"]["backend-integration"]
    assert "PGTZ" not in (job.get("env") or {}), (
        "PGTZ on the job makes the verify step self-fulfilling")
    assert "PGTZ" not in (job["services"]["postgres"].get("env") or {}), (
        "PGTZ on the service env is not the server default either")


def test_ci_fails_loudly_if_postgres_ignored_the_setting() -> None:
    """⚠ THE ONLY THING BETWEEN THE `TZ:` AND A NO-OP. TZ on the container is
    not Postgres's TimeZone GUC -- the image picks it up at initdb, so an image
    change or a cached volume could move the container clock and leave the
    server at UTC. Without this step the pin would silently do nothing, which is
    the failure mode this whole phase exists to prevent one level down."""
    steps = _ci_yaml()["jobs"]["backend-integration"]["steps"]
    # matched on the RUN body, not the display name: the name is prose somebody
    # will reword, and the first version of this guard failed on "TimeZone" vs
    # "timezone" in a name it had written itself.
    verify = [i for i, st in enumerate(steps) if "SHOW TimeZone" in (st.get("run") or "")]
    # ⚠ "pytest" alone matches the verify step too — the DATABASE is called
    # foxy_pytest, and that substring made the ordering assertion compare the
    # verify step against itself. Anchored to the invocation.
    suite = [i for i, st in enumerate(steps)
             if "pytest tests/" in (st.get("run") or "")]
    assert len(verify) == 1, (
        "no step asks Postgres what timezone it took: %s"
        % [st.get("name") for st in steps])
    assert suite, "the integration suite step is gone"
    assert verify[0] < min(suite), (
        "the timezone check runs after the suite it is meant to precede, so a "
        "silently-ignored TZ would be reported as 1139 confusing passes")


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not on PATH")
@pytest.mark.parametrize("reported,offset,should_pass", [
    ("America/Los_Angeles", "-07:00:00", True),
    ("UTC", "00:00:00", False),
    ("Etc/UTC", "00:00:00", False),
    # the nastiest case: a plausible-looking zone name whose offset is actually
    # zero, which is what a TZ that silently fell back would look like
    ("Africa/Abidjan", "00:00:00", False),
])
def test_the_verify_step_actually_gates(reported, offset, should_pass, tmp_path) -> None:
    """DRIVE THE SHIPPED SCRIPT, DO NOT GREP IT (C0). The first version of this
    asserted `"exit 1" in body` and stayed green when one of the script's TWO
    exits was neutered -- a substring cannot tell you which branch runs.

    So the step's own `run:` block is executed against a stubbed `psql` that
    reports each answer in turn, and the exit status IS the assertion.
    """
    import subprocess

    steps = _ci_yaml()["jobs"]["backend-integration"]["steps"]
    body = next(st["run"] for st in steps if "SHOW TimeZone" in (st.get("run") or ""))

    stub = tmp_path / "psql"
    stub.write_text(
        '#!/bin/sh\n'
        'case "$*" in\n'
        '  *"SHOW TimeZone"*) echo "%s" ;;\n'
        '  *) echo "%s" ;;\n'
        'esac\n' % (reported, offset),
        encoding="utf-8")
    stub.chmod(0o755)
    script = tmp_path / "step.sh"
    script.write_text("set -e\n" + body, encoding="utf-8")

    env = dict(os.environ, PATH="%s%s%s" % (tmp_path, os.pathsep, os.environ["PATH"]))
    # encoding="utf-8": text=True alone decodes cp1252 here (C1).
    proc = subprocess.run([shutil.which("bash"), str(script)], env=env,
                          capture_output=True, text=True, encoding="utf-8")
    if should_pass:
        assert proc.returncode == 0, (
            "the step rejected a legitimately skewed database:\n%s%s"
            % (proc.stdout, proc.stderr))
    else:
        assert proc.returncode != 0, (
            "the step accepted %s (offset %s) -- CI would run UTC-blind and say "
            "nothing:\n%s%s" % (reported, offset, proc.stdout, proc.stderr))
