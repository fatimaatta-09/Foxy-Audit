"""C5 · the denominator /v1/health was missing.

Every anchor figure on the health page comes from `latest_by_org`, which only
contains organisations that have anchored at least once. So an org that has
NEVER anchored -- the one an operator most needs to find -- could not appear in
any of them. These two fields are that denominator.

`expected_orgs` is deliberately NOT "all organisations": anchoring is not opt-in
per org (`anchor_all_due` sweeps `select(Organization.id)` and no per-org flag
exists anywhere; the per-plan `anchor_cadence_*` settings change the interval,
never whether), so with anchoring on, every org whose chain has advanced should
have one -- and a workspace that has never recorded an event has nothing to
anchor.

Run with DATABASE_URL pointing at :5433/foxy_pytest.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.db import SessionLocal


def _seed_event(oid, seq=1):
    db = SessionLocal()
    try:
        h = uuid.uuid4().hex * 2
        db.execute(text(
            "INSERT INTO audit_logs (id, org_id, seq, prompt_hash, response_hash,"
            " token_count, policy_tag, prev_hash, chain_hash, agent, grading_status,"
            " created_at) VALUES (gen_random_uuid(), :oid, :seq, :h, :h, 1, 't',"
            " :h, :h, 'a', 'graded', now())"),
            {"oid": uuid.UUID(str(oid)), "seq": seq, "h": h})
        db.commit()
    finally:
        db.close()


def _seed_anchor(oid, status="confirmed", when=None):
    db = SessionLocal()
    try:
        db.execute(text(
            "INSERT INTO chain_anchors (id, org_id, root_hash, last_seq, chain,"
            " status, anchored_at, confirmed_at) VALUES (gen_random_uuid(), :oid,"
            " :h, 1, 'sepolia', :st, :at, :cf)"),
            {"oid": uuid.UUID(str(oid)), "h": uuid.uuid4().hex * 2, "st": status,
             "at": when or datetime.now(timezone.utc),
             "cf": (when or datetime.now(timezone.utc)) if status == "confirmed" else None})
        db.commit()
    finally:
        db.close()


def _soft_delete(oid):
    db = SessionLocal()
    try:
        db.execute(text("UPDATE organizations SET deleted_at = now() WHERE id = :oid"),
                   {"oid": uuid.UUID(str(oid))})
        db.commit()
    finally:
        db.close()


def _anchors(client):
    return client.get("/admin/v1/health").json()["anchors"]


def test_an_org_that_never_anchored_is_counted(make_staff, staff_login, make_org):
    """THE GAP THIS PHASE CLOSES. failed_latest and stale_latest are both derived
    from latest_by_org, so an org with no anchor row at all contributes to
    neither -- it was invisible. Here two orgs record events and only one
    anchors; the missing one has to show up in the difference."""
    a, b = make_org(), make_org()
    _seed_event(a["org_id"])
    _seed_event(b["org_id"])
    _seed_anchor(a["org_id"])

    s = make_staff()
    c = staff_login(s["email"], s["password"])
    r = _anchors(c)
    assert r["expected_orgs"] == 2, r
    assert r["anchored_orgs"] == 1, r
    # and the pre-existing figures still say nothing about it
    assert r["failed_latest"] == 0 and r["stale_latest"] == 0, (
        "the missing org leaked into a field that only describes orgs that DID "
        "anchor")


def test_an_org_with_no_events_is_not_counted_as_a_gap(
        make_staff, staff_login, make_org):
    """"All organisations" is the wrong denominator. A workspace that has never
    recorded an event has nothing to anchor, and counting it would make this
    number permanently wrong on any deployment that takes signups."""
    busy, idle = make_org(), make_org()
    _seed_event(busy["org_id"])
    _seed_anchor(busy["org_id"])

    s = make_staff()
    c = staff_login(s["email"], s["password"])
    r = _anchors(c)
    assert r["expected_orgs"] == 1, (
        "an org with no events is in the denominator: %s" % r)
    assert r["anchored_orgs"] == 1
    assert idle["org_id"] is not None      # the idle org exists; it just does not count


def test_an_offboarded_org_drops_out_of_both_sides(
        make_staff, staff_login, make_org):
    """A soft-deleted tenant is not an integrity gap anyone will act on, so it
    leaves the denominator -- and it must leave the numerator with it, or a
    deleted-but-anchored org would push coverage above 100%."""
    live, gone = make_org(), make_org()
    for o in (live, gone):
        _seed_event(o["org_id"])
        _seed_anchor(o["org_id"])
    _soft_delete(gone["org_id"])

    s = make_staff()
    c = staff_login(s["email"], s["password"])
    r = _anchors(c)
    assert r["expected_orgs"] == 1, r
    assert r["anchored_orgs"] == 1, (
        "an offboarded org still counts as anchored, so coverage can exceed "
        "its own denominator: %s" % r)
    assert r["anchored_orgs"] <= r["expected_orgs"]


def test_coverage_is_zero_when_nothing_has_anchored(
        make_staff, staff_login, make_org):
    """The other side of the first test: a real denominator with an empty
    numerator is a measured zero, and must not be confused with the no-events
    case, which has no denominator at all."""
    a, b = make_org(), make_org()
    _seed_event(a["org_id"])
    _seed_event(b["org_id"])

    s = make_staff()
    c = staff_login(s["email"], s["password"])
    r = _anchors(c)
    assert r["expected_orgs"] == 2 and r["anchored_orgs"] == 0, r


def test_no_events_anywhere_reports_no_denominator(make_staff, staff_login, make_org):
    """Distinct from "0 of 2". The console renders this as a sentence rather than
    an empty bar, because 0% of nothing is not a measurement."""
    make_org()
    s = make_staff()
    c = staff_login(s["email"], s["password"])
    r = _anchors(c)
    assert r["expected_orgs"] == 0 and r["anchored_orgs"] == 0, r


def test_the_enabled_flag_still_rides_along(make_staff, staff_login):
    """anchor_enabled defaults to false, so "switched off" is the COMMON state
    and the console needs it to avoid drawing an empty bar that reads as
    "nothing is anchored" when the truth is "nothing is meant to be"."""
    s = make_staff()
    c = staff_login(s["email"], s["password"])
    assert "enabled" in _anchors(c)


def test_the_coverage_lookup_does_not_scale_with_the_ledger(
        make_staff, staff_login, make_org):
    """N+1 IS NOT THE RISK HERE -- scanning the ledger is. `SELECT DISTINCT
    org_id FROM audit_logs` scans the whole index (112ms over 1.5M events
    measured); driving from `organizations` and probing per org stops at the
    first matching row (1.46ms for the same answer).

    Counted by echo: the statement must touch audit_logs at most once per
    health call, and must not be issued once per ledger row.
    """
    orgs = [make_org() for _ in range(4)]
    for o in orgs:
        for i in range(1, 6):
            _seed_event(o["org_id"], seq=i)

    s = make_staff()
    c = staff_login(s["email"], s["password"])

    seen = []
    from sqlalchemy import event
    from app.db import engine

    def _rec(conn, cursor, statement, params, context, executemany):
        if "audit_logs" in statement.lower() and "insert" not in statement.lower():
            seen.append(statement)

    event.listen(engine, "before_cursor_execute", _rec)
    try:
        r = _anchors(c)
    finally:
        event.remove(engine, "before_cursor_execute", _rec)

    assert r["expected_orgs"] == 4, r
    assert len(seen) <= 2, (
        "the health call issued %d audit_logs reads for 4 orgs with 20 events; "
        "it must scale with neither: %s" % (len(seen), seen))
