"""Customer data-export — a tenant pulls its OWN audit logs (Phase 5 · 5D.4).

GET /v1/logs/export returns every audit_logs row for the caller's org (and only
that org), as a downloadable JSON (default) or CSV. Works over the SDK Bearer key
or the dashboard session; unauthenticated is 401.
"""

from __future__ import annotations

import hashlib


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _ingest(client, org, n: int) -> None:
    rows = [{"prompt_hash": _h(f"{org['org_id']}p{i}"),
             "response_hash": _h(f"{org['org_id']}r{i}"),
             "token_count": 100, "policy_tag": "chat"} for i in range(n)]
    assert client.post("/v1/logs/batch", json=rows,
                       headers=org["auth"]).status_code == 202


def test_export_json_is_tenant_scoped(make_org, client):
    a, b = make_org(), make_org()
    _ingest(client, a, 3)
    _ingest(client, b, 2)

    r = client.get("/v1/logs/export", headers=a["auth"])
    assert r.status_code == 200
    assert "attachment" in r.headers.get("content-disposition", "")
    data = r.json()
    assert data["count"] == 3                       # only org A's rows
    assert len(data["logs"]) == 3
    assert all("chain_hash" in row for row in data["logs"])


def test_export_csv(make_org, client):
    a = make_org()
    _ingest(client, a, 2)
    r = client.get("/v1/logs/export?format=csv", headers=a["auth"])
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    lines = r.text.strip().splitlines()
    assert lines[0].startswith("seq,")              # header row
    assert len(lines) == 3                          # header + 2 data rows


def test_export_verifies_with_standalone_verifier(make_org, client):
    """The real backend export must verify under the INDEPENDENT verifier/ tool
    (byte-for-byte recipe parity, incl. the 6B agent field) and its embedded anchor
    receipt must match — then tampering must be caught at the right seq. This is the
    whole trust claim of the open-source verifier (Phase 6 · 6D)."""
    import importlib.util
    import os
    import uuid

    from app.anchor import anchor_org
    from app.db import SessionLocal

    org = make_org()
    rows = [{"prompt_hash": _h(f"p{i}"), "response_hash": _h(f"r{i}"),
             "token_count": 10 * i + 5, "policy_tag": "chat"} for i in range(3)]
    rows[1]["agent"] = "gpt-4o"                              # an agent-bearing row
    assert client.post("/v1/logs/batch", json=rows, headers=org["auth"]).status_code == 202
    db = SessionLocal()
    try:
        anchor_org(db, uuid.UUID(org["org_id"]), force=True)   # embed a real anchor receipt
    finally:
        db.close()

    export = client.get("/v1/logs/export", headers=org["auth"]).json()
    assert export["anchor"] is not None and export["anchor"]["last_seq"] == 3

    vpath = os.path.join(os.path.dirname(__file__), "..", "..", "..", "verifier", "foxy_verify.py")
    spec = importlib.util.spec_from_file_location("foxy_verify", vpath)
    fv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fv)

    res = fv.verify_export(export)
    assert res["ok"] is True and res["count"] == 3          # backend export verifies clean
    assert fv.check_anchor_offline(export, res)["matches"] is True   # anchor root matches

    export["logs"][1]["token_count"] = 999                  # tamper the agent row
    assert fv.verify_export(export)["first_broken_seq"] == 2


def test_export_requires_auth(client):
    assert client.get("/v1/logs/export").status_code == 401


def test_export_over_session(make_org, login, client):
    org = make_org()
    _ingest(client, org, 1)
    c = login(org["admin_email"], org["admin_password"])
    r = c.get("/v1/logs/export")
    assert r.status_code == 200 and r.json()["count"] == 1


# ── the date range (P2 §8.5) ────────────────────────────────────────────────
# The Export page has always shown two date pickers. It sent them to
# /v1/passport and handed the log download a URL with no range on it at all, so
# the user picked a window and silently got the whole ledger. The endpoint now
# takes date_from/date_to — and "accepted and silently ignored" is exactly the
# failure that comes back in a refactor while still looking correct, so both
# bounds are pinned here rather than left to review.
#
# BOTH bounds, deliberately. A date_from-only test passes even when the end
# bound is off by a day, because rows created today are after any past start.
# `date_to=<today>` is the assertion that catches it: the range is inclusive of
# the whole 'to' day (`< date_to + 1 day`, matching routers/passport.py), so
# today's rows must survive a date_to of today.

def _day(offset: int) -> str:
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) + timedelta(days=offset)).strftime("%Y-%m-%d")


def test_export_without_a_range_is_still_the_whole_ledger(make_org, client):
    org = make_org()
    _ingest(client, org, 3)
    assert client.get("/v1/logs/export", headers=org["auth"]).json()["count"] == 3


def test_export_end_bound_includes_the_whole_to_day(make_org, client):
    """The off-by-one guard. Rows ingested now must survive date_to=today."""
    org = make_org()
    _ingest(client, org, 3)
    r = client.get(f"/v1/logs/export?date_to={_day(0)}", headers=org["auth"])
    assert r.status_code == 200
    assert r.json()["count"] == 3, (
        "date_to=today dropped today's rows — the end bound is exclusive of "
        "the 'to' day, but the passport treats it as inclusive")


def test_export_end_bound_actually_excludes_later_rows(make_org, client):
    """And the other side: an end bound that filters nothing is not a filter."""
    org = make_org()
    _ingest(client, org, 3)
    r = client.get(f"/v1/logs/export?date_to={_day(-1)}", headers=org["auth"])
    assert r.status_code == 200
    assert r.json()["count"] == 0, "date_to=yesterday returned today's rows"


def test_export_start_bound_is_inclusive_and_filters(make_org, client):
    org = make_org()
    _ingest(client, org, 3)
    auth = org["auth"]
    assert client.get(f"/v1/logs/export?date_from={_day(0)}",
                      headers=auth).json()["count"] == 3
    assert client.get(f"/v1/logs/export?date_from={_day(1)}",
                      headers=auth).json()["count"] == 0, \
        "date_from=tomorrow returned rows created today"


def test_export_range_applies_to_csv_too(make_org, client):
    """The filter runs before the format branch; CSV must not be a way around
    it. Header row only means zero data rows."""
    org = make_org()
    _ingest(client, org, 2)
    r = client.get(f"/v1/logs/export?format=csv&date_to={_day(-1)}",
                   headers=org["auth"])
    assert r.status_code == 200
    assert len(r.text.strip().splitlines()) == 1


def test_export_rejects_a_date_it_cannot_parse(make_org, client):
    org = make_org()
    r = client.get("/v1/logs/export?date_from=not-a-date", headers=org["auth"])
    assert r.status_code == 422
