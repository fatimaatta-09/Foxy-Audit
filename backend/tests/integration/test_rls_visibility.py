"""#122 · a staff read that cannot see across organisations must say so.

THE DEFECT. Seventeen tables carry FORCE RLS with a policy of
`org_id = current_setting('app.current_org', true)::uuid`. The `true` is
missing_ok, so an unset GUC is NULL and matches nothing. Staff paths never set
that GUC on purpose, so every cross-org aggregate on this platform works because
the connection role is a superuser -- and if it stops being one, none of those
queries raise. They return zero rows, and zero failed / zero stale / zero
unanchored is the shape of a perfectly healthy platform.

⚠ THE HARD PART IS PROVING THE CHECK FIRES WITHOUT CHANGING THE ROLE THE SUITE
RUNS AS. `SET LOCAL ROLE "foxy_app"` inside a transaction does it: `current_user`
and `row_security_active()` both track it and both revert at rollback, so these
run the REAL function against the REAL confined role and leak nothing. That role
is not invented here -- migration 0021 creates it and `_scope_org` already drops
every customer request to it.

    DATABASE_URL=postgresql+psycopg://foxy:foxy@localhost:5433/foxy_pytest \\
      python -m pytest backend/tests/integration/test_rls_visibility.py -q
"""

from __future__ import annotations

import hashlib
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import SessionLocal, engine
from app.main import app, customer_api
from app.db import get_db
from app.rls import cross_org_visibility
from app.routers.admin_health import _empty_health, build_health

CONFINED = 'SET LOCAL ROLE "foxy_app"'


def _seed_log(db, org_id: str, seq: int = 1) -> None:
    """One audit_logs row, written as the suite's superuser. Constructed rather
    than asserted-if-present: `traffic_events` taught this repo that an
    `if count == 0: skip` premise stops asserting instead of failing."""
    h = hashlib.sha256(f"{org_id}:{seq}".encode()).hexdigest()
    db.execute(text("""
        INSERT INTO audit_logs (id, org_id, seq, prompt_hash, response_hash,
                                token_count, policy_tag, prev_hash, chain_hash,
                                grading_status)
        VALUES (:id, :org, :seq, :h, :h, 1, 'test', :h, :h, 'pending')
    """), {"id": uuid.uuid4(), "org": org_id, "seq": seq, "h": h})


@pytest.fixture
def confined():
    """A session that has dropped to the confined role for one transaction."""
    db = SessionLocal()
    try:
        db.execute(text(CONFINED))
        yield db
    finally:
        db.rollback()
        db.close()


# ── the two answers ─────────────────────────────────────────────────────────
def test_the_role_this_suite_runs_as_can_see_across_organisations() -> None:
    """The control. If this ever failed, every other guard below would be
    measuring a broken baseline rather than the thing it names."""
    db = SessionLocal()
    try:
        v = cross_org_visibility(db)
    finally:
        db.close()
    assert v["status"] == "ok", v
    assert v["blinded_tables"] == [], v
    assert v["role"], "the effective role was not reported"


def test_it_fires_under_a_role_that_genuinely_cannot_see(confined) -> None:
    """THE ONE THIS PHASE EXISTS FOR, and the non-circularity proof in the same
    breath: the confined role gets the HONEST answer about itself. A check that
    read rows behind a policy would have come back "ok" here for exactly the
    reason every broken aggregate does -- nothing visible, nothing wrong.

    The four named tables are the ones the admin console's cross-org aggregates
    actually read; the list is not hardcoded in the check, so it grows with the
    schema."""
    v = cross_org_visibility(confined)
    assert v["status"] == "blind", v
    assert v["role"] == "foxy_app", v
    for t in ("audit_logs", "chain_anchors", "usage_daily", "invoices"):
        assert t in v["blinded_tables"], "%s is not reported blinded: %s" % (
            t, v["blinded_tables"])
    assert "zero rows" in v["detail"], v["detail"]


def _seed_one(make_org) -> None:
    org = make_org()
    db = SessionLocal()
    try:
        _seed_log(db, org["org_id"], 1)
        db.commit()
        assert db.execute(text("SELECT count(*) FROM audit_logs")).scalar_one() == 1, (
            "the premise was never established")
    finally:
        db.close()


def test_on_a_fresh_connection_the_failure_is_zero_rows_and_no_error(make_org) -> None:
    """THE PREMISE, CONSTRUCTED RATHER THAN HOPED FOR. If a confined role RAISED
    here the platform would already be loud and this phase would be unnecessary.

    ⚠ `engine.dispose()` IS LOAD-BEARING. `current_setting('app.current_org',
    true)` is NULL only while the GUC has never been set on that physical
    connection; the pool hands back connections that earlier tests have scoped,
    and this assertion silently flipped to the other failure mode in a full-suite
    run before the dispose was added. Dropping the pool guarantees a virgin
    connection, so the mode under test is the one named in the title."""
    _seed_one(make_org)
    engine.dispose()
    db = SessionLocal()
    try:
        db.execute(text(CONFINED))
        assert db.execute(text(
            "SELECT coalesce(current_setting('app.current_org', true), '<unset>')"
        )).scalar_one() == "<unset>", "the connection was not virgin after dispose()"
        blind = db.execute(text("SELECT count(*) FROM audit_logs")).scalar_one()
    finally:
        db.rollback()
        db.close()
    assert blind == 0, (
        "the confined role saw %s rows, so this database is not exhibiting the "
        "defect and the guards below prove nothing" % blind)


def test_on_a_recycled_connection_the_same_read_raises_instead(make_org) -> None:
    """THE SECOND FAILURE MODE, WHICH #122's MODEL DID NOT HAVE. `set_config(…,
    true)` is transaction-local, and at the end of that transaction the GUC
    reverts to its RESET value — which for a never-explicitly-set custom GUC is
    the EMPTY STRING, not unset. `''::uuid` raises.

    So the confined failure is silent zeros on a fresh connection and a 500 on
    any connection a customer request has already touched — and on a pooled app
    that is most of them. Both are wrong; only one is loud, and which one you get
    depends on pool luck. That is precisely why the check asks the planner about
    the role instead of inferring anything from a row count."""
    import sqlalchemy.exc

    _seed_one(make_org)
    db = SessionLocal()
    try:
        # exactly what _scope_org does for a customer request, then let it end
        db.execute(text("SELECT set_config('app.current_org', :o, true)"),
                   {"o": str(uuid.uuid4())})
        db.rollback()
        assert db.execute(text(
            "SELECT coalesce(current_setting('app.current_org', true), '<unset>')"
        )).scalar_one() == "", "the GUC did not revert to the empty string"
        db.execute(text(CONFINED))
        with pytest.raises(sqlalchemy.exc.DataError):
            db.execute(text("SELECT count(*) FROM audit_logs")).scalar_one()
    finally:
        db.rollback()
        db.close()


def test_the_check_survives_both_modes(make_org, confined) -> None:
    """Whichever mode the connection is in, the REPORT must still be produced —
    it reads the catalog, not the table, so the poisoned GUC cannot reach it."""
    _seed_one(make_org)
    confined.execute(text("SELECT set_config('app.current_org', '', true)"))
    v = cross_org_visibility(confined)
    assert v["status"] == "blind", (
        "the check was taken down by the same empty GUC that breaks the "
        "aggregates it exists to explain: %s" % v)


def test_an_empty_database_is_not_an_alarm() -> None:
    """⚠ THE FALSE-POSITIVE THAT WOULD KILL THIS CHECK. A fresh deployment with
    no customers is a legitimate state, and an alarm that fires there is one
    people learn to ignore -- and then the real one is ignored too. The check
    reads no application row at all, so it is silent here by construction rather
    than by a suppression rule that could drift. `_clean_db` has just truncated
    every tenant table, so the premise holds without arranging anything."""
    db = SessionLocal()
    try:
        assert db.execute(text("SELECT count(*) FROM organizations")).scalar_one() == 0
        v = cross_org_visibility(db)
    finally:
        db.close()
    assert v["status"] == "ok", (
        "an empty database was reported as a misconfiguration: %s" % v)


def test_a_scoped_confined_session_is_still_reported_blind(make_org) -> None:
    """THE CASE A ROW-COUNT CHECK WOULD HAVE MISSED, which is why this asks the
    planner instead. With the GUC set, a confined role sees ONE org's rows --
    non-zero, plausible, and wrong. "Did an unscoped read return fewer rows than
    expected" answers "no" here and passes; the console then reports one tenant's
    numbers as the whole platform's, which is worse than reporting zero."""
    a, b = make_org(), make_org()
    seeder = SessionLocal()
    try:
        _seed_log(seeder, a["org_id"], 1)
        _seed_log(seeder, b["org_id"], 1)
        seeder.commit()
    finally:
        seeder.close()

    db = SessionLocal()
    try:
        db.execute(text("SELECT set_config('app.current_org', :o, true)"),
                   {"o": a["org_id"]})
        db.execute(text(CONFINED))
        seen = db.execute(text("SELECT count(*) FROM audit_logs")).scalar_one()
        v = cross_org_visibility(db)
    finally:
        db.rollback()
        db.close()
    assert seen == 1, (
        "expected one org's rows out of two, got %s -- the premise is wrong" % seen)
    assert v["status"] == "blind", (
        "a scoped confined session read like a healthy one: %s" % v)


# ── the two surfaces it reaches ─────────────────────────────────────────────
def test_health_ready_refuses_to_be_ready_when_the_role_is_blind(make_org) -> None:
    """GUARD THE USE (A6). The check is worthless sitting in a module nothing
    calls, and /health/ready is the smoke test the deploy gates on -- a role that
    blinds every staff figure should roll the deploy back rather than pass it.

    The corroborating fact is what the blind payload does NOT contain: the
    healthy call reports `pending: 1`, and the blind one omits the counts
    entirely rather than publishing the 0 it would have measured. Not publishing
    a number you know is wrong is the point -- a 0 there is indistinguishable
    from a healthy queue."""
    org = make_org()
    seeder = SessionLocal()
    try:
        _seed_log(seeder, org["org_id"], 1)
        seeder.execute(text("UPDATE worker_heartbeat SET beat_at = now() WHERE id = 1"))
        seeder.commit()
    finally:
        seeder.close()

    healthy = TestClient(app).get("/health/ready")
    assert healthy.status_code == 200, healthy.json()
    assert healthy.json()["checks"]["rls"] == "ok", healthy.json()
    assert healthy.json()["checks"]["pending"] == 1, healthy.json()

    def _confined_db():
        db = SessionLocal()
        try:
            db.execute(text(CONFINED))
            yield db
        finally:
            db.rollback()
            db.close()

    # ⚠ ON customer_api, NOT app. main.py is a three-ASGI split and /health/ready
    # is a route of the MOUNTED sub-application; an override on the bare parent
    # never reaches it and the request quietly runs on the real superuser
    # session, which is how this guard first passed while proving nothing.
    customer_api.dependency_overrides[get_db] = _confined_db
    try:
        blind = TestClient(app).get("/health/ready")
    finally:
        customer_api.dependency_overrides.pop(get_db, None)

    body = blind.json()
    assert blind.status_code == 503, body
    assert body["status"] == "not_ready", body
    assert "zero rows" in body["checks"]["rls"], body["checks"]["rls"]
    assert "foxy_app" in body["checks"]["rls"], (
        "this run did not exercise the confined role at all: %s" % body)
    assert "pending" not in body["checks"] and "failed" not in body["checks"], (
        "the probe published counts it knew were unmeasurable: %s" % body)


def test_the_admin_health_payload_names_it_and_will_not_read_ok(make_org) -> None:
    """Every figure on that page is an unscoped read over a FORCE-RLS table, so
    a blind role makes each one zero -- and zero failed, zero stale, zero
    unanchored is exactly what a healthy platform looks like. Without this the
    page reports "ok" most confidently when it is blind."""
    make_org()
    db = SessionLocal()
    try:
        db.execute(text("UPDATE worker_heartbeat SET beat_at = now() WHERE id = 1"))
        db.commit()
        fine = build_health(db)
    finally:
        db.close()
    assert fine["rls"]["status"] == "ok", fine["rls"]
    assert fine["status"] == "ok", fine["status"]

    db = SessionLocal()
    try:
        db.execute(text(CONFINED))
        blind = build_health(db)
    finally:
        db.rollback()
        db.close()
    assert blind["rls"]["status"] == "blind", blind["rls"]
    assert blind["status"] == "degraded", (
        "the page read healthy while blind: %s" % blind["status"])
    assert "audit_logs" in blind["rls"]["blinded_tables"], blind["rls"]


def test_the_unreachable_database_payload_carries_the_key_too() -> None:
    """A key that only appears on the happy path cannot be read as "this was
    checked and was fine". When the database is unreachable the honest answer is
    `unknown`, not a missing field and not an invented `ok`."""
    empty = _empty_health(__import__("datetime").datetime.now(
        __import__("datetime").timezone.utc))
    assert empty["rls"]["status"] == "unknown", empty["rls"]
    assert empty["rls"]["blinded_tables"] == [], empty["rls"]


def test_it_reports_unknown_rather_than_ok_when_it_cannot_measure() -> None:
    """A health check that invents "fine" when it could not measure is the same
    class of lie it is here to catch. Driven on a session whose transaction has
    already failed, so the catalog read genuinely raises."""
    db = SessionLocal()
    try:
        try:
            db.execute(text("SELECT 1 FROM does_not_exist_at_all"))
        except Exception:
            pass
        v = cross_org_visibility(db)
    finally:
        db.rollback()
        db.close()
    # NOT `in ("unknown", "ok")`. The first version of this line accepted both,
    # which made it green against a mutation that returned "ok" from the very
    # except branch it exists to guard -- a guard that cannot fail is not one.
    assert v["status"] == "unknown", v
    assert v["blinded_tables"] == [] and v["role"] is None, v
    assert "could not read" in v["detail"], v["detail"]
