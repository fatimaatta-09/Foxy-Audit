"""GET /v1/analytics/threats — SQL-aggregated threat rollup (Phase 5 · 5A.3).

Must be readable by the dashboard *session* (not just the SDK Bearer key),
return correct aggregates without scanning the whole ledger into memory, and
emit valid ISO-8601 timestamps (the old code appended a stray "Z" to an already
tz-aware value, producing "…+00:00Z").

Grading uses a controlled Verdict (test isolation) — the endpoint + DB are real.
"""

from __future__ import annotations

import datetime as _dt
import hashlib

_h = lambda s: hashlib.sha256(s.encode()).hexdigest()  # noqa: E731


def _ingest(client, org, specs):
    """specs = list of (token_count, policy_tag)."""
    payload = [
        {"prompt_hash": _h(f"p{i}"), "response_hash": _h(f"r{i}"),
         "token_count": tc, "policy_tag": tag}
        for i, (tc, tag) in enumerate(specs)
    ]
    assert client.post("/v1/logs/batch", headers=org["auth"], json=payload).status_code == 202


def _grade(monkeypatch, verdict_for):
    from app import worker as workermod

    def fake_eval(meta, policy_config=None, history=None):
        return verdict_for(meta)

    monkeypatch.setattr(workermod.gemini, "evaluate", fake_eval)
    from app.db import SessionLocal
    db = SessionLocal()
    try:
        for row in workermod._claim_batch(db, 100, 300):
            workermod._grade_one(db, row)
    finally:
        db.close()


def _seed(client, org, monkeypatch):
    """seq1 hipaa risk90 · seq2 hipaa risk40 · seq3 soc2 risk80 · seq4 chat clean."""
    from app.schemas import Verdict
    _ingest(client, org, [(11, "hipaa"), (22, "hipaa"), (33, "soc2"), (44, "chat")])
    table = {11: (True, 90), 22: (True, 40), 33: (True, 80), 44: (False, 0)}

    def verdict_for(meta):
        breach, risk = table[meta["token_count"]]
        return Verdict(policy_breach=breach, reason="tripped" if breach else "ok",
                       risk_score=risk)

    _grade(monkeypatch, verdict_for)


def test_threats_readable_via_session(make_org, login, client, monkeypatch):
    """The cookie dashboard session can read threats (was Bearer-only → 401)."""
    org = make_org()
    _seed(client, org, monkeypatch)   # ingest+grade via the SDK-key header
    ca = login(org["admin_email"], org["admin_password"])
    r = ca.get("/v1/analytics/threats")
    assert r.status_code == 200, r.text
    assert r.json()["total_threats"] == 3


def test_threats_readable_via_bearer(make_org, client, monkeypatch):
    org = make_org()
    _seed(client, org, monkeypatch)
    r = client.get("/v1/analytics/threats", headers=org["auth"])
    assert r.status_code == 200, r.text
    assert r.json()["total_threats"] == 3


def test_threats_aggregates(make_org, client, monkeypatch):
    org = make_org()
    _seed(client, org, monkeypatch)
    data = client.get("/v1/analytics/threats", headers=org["auth"]).json()
    assert data["total_threats"] == 3
    assert data["avg_risk_score"] == 70                         # (90+40+80)/3
    assert data["top_policies"] == [{"tag": "hipaa", "count": 2},
                                    {"tag": "soc2", "count": 1}]
    # only risk >= 50, newest first
    assert [e["seq"] for e in data["recent_high_risk"]] == [3, 1]


def test_threats_timestamps_are_valid_iso(make_org, client, monkeypatch):
    org = make_org()
    _seed(client, org, monkeypatch)
    data = client.get("/v1/analytics/threats", headers=org["auth"]).json()
    assert data["recent_high_risk"], "expected some high-risk events"
    for e in data["recent_high_risk"]:
        # the old "…+00:00Z" raises here; a clean ISO value parses
        _dt.datetime.fromisoformat(e["timestamp"])
