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
from tests.integration.judge_helpers import give_judge_key

_h = lambda s: hashlib.sha256(s.encode()).hexdigest()  # noqa: E731


def _ingest(client, org, specs):
    """specs = list of (token_count, policy_tag)."""
    payload = [
        {"prompt_hash": _h(f"p{i}"), "response_hash": _h(f"r{i}"),
         "token_count": tc, "policy_tag": tag}
        for i, (tc, tag) in enumerate(specs)
    ]
    give_judge_key(org["org_id"])       # per-tenant judge: this org brings its own key
    assert client.post("/v1/logs/batch", headers=org["auth"], json=payload).status_code == 202


def _grade(monkeypatch, verdict_for):
    from app import worker as workermod

    def fake_eval(meta, policy_config=None, history=None, api_key=None):
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


def test_threats_recent_high_risk_include_agent(make_org, client, monkeypatch):
    """The alert feed carries agent/model attribution (6B) so the dashboard renders
    '<policy> · <agent>' rows instead of the old hardcoded device names."""
    from app.schemas import Verdict
    org = make_org()
    payload = [{"prompt_hash": _h("pa"), "response_hash": _h("ra"),
                "token_count": 77, "policy_tag": "hipaa", "agent": "gpt-4o"}]
    give_judge_key(org["org_id"])       # per-tenant judge: this org brings its own key
    assert client.post("/v1/logs/batch", headers=org["auth"], json=payload).status_code == 202
    _grade(monkeypatch, lambda meta: Verdict(policy_breach=True, reason="tripped", risk_score=88))
    data = client.get("/v1/analytics/threats", headers=org["auth"]).json()
    assert data["recent_high_risk"], "expected a high-risk event"
    assert data["recent_high_risk"][0]["agent"] == "gpt-4o"


# ─────────────────── P7 · /v1/analytics/timeseries + /by-agent ────────────────
def _ingest_agents(client, org, monkeypatch):
    """seq1 gpt-4 risk90 · seq2 gpt-4 risk80 · seq3 claude risk40 — all breaches, two agents."""
    from app.schemas import Verdict
    payload = [
        {"prompt_hash": _h("ap1"), "response_hash": _h("ar1"), "token_count": 91, "policy_tag": "hipaa", "agent": "gpt-4"},
        {"prompt_hash": _h("ap2"), "response_hash": _h("ar2"), "token_count": 81, "policy_tag": "hipaa", "agent": "gpt-4"},
        {"prompt_hash": _h("ap3"), "response_hash": _h("ar3"), "token_count": 41, "policy_tag": "soc2", "agent": "claude"},
    ]
    give_judge_key(org["org_id"])       # per-tenant judge: this org brings its own key
    assert client.post("/v1/logs/batch", headers=org["auth"], json=payload).status_code == 202
    table = {91: (True, 90), 81: (True, 80), 41: (True, 40)}

    def verdict_for(meta):
        b, r = table[meta["token_count"]]
        return Verdict(policy_breach=b, reason="x", risk_score=r)
    _grade(monkeypatch, verdict_for)


def test_timeseries_buckets_by_risk_band(make_org, login, client, monkeypatch):
    org = make_org()
    _seed(client, org, monkeypatch)              # breaches at risk 90, 40, 80 (all today)
    r = login(org["admin_email"], org["admin_password"]).get("/v1/analytics/timeseries?days=30")
    assert r.status_code == 200
    days = r.json()["days"]
    assert len(days) == 1                         # all created today → one bucket
    t = days[0]
    assert t["high"] == 2 and t["medium"] == 1 and t["low"] == 0 and t["total"] == 3


def test_timeseries_empty_when_no_breaches(make_org, login):
    org = make_org()
    r = login(org["admin_email"], org["admin_password"]).get("/v1/analytics/timeseries")
    assert r.status_code == 200 and r.json()["days"] == []


def test_timeseries_requires_auth(client):
    assert client.get("/v1/analytics/timeseries").status_code == 401


def test_by_agent_groups_with_avg_risk(make_org, client, monkeypatch):
    org = make_org()
    _ingest_agents(client, org, monkeypatch)
    r = client.get("/v1/analytics/by-agent", headers=org["auth"])
    assert r.status_code == 200
    agents = {a["agent"]: a for a in r.json()["agents"]}
    assert agents["gpt-4"]["count"] == 2 and agents["gpt-4"]["avg_risk"] == 85   # (90+80)/2
    assert agents["claude"]["count"] == 1 and agents["claude"]["avg_risk"] == 40


def test_by_agent_requires_auth(client):
    assert client.get("/v1/analytics/by-agent").status_code == 401


def test_new_analytics_org_isolation(make_org, login, client, monkeypatch):
    a = make_org()
    b = make_org()
    _seed(client, a, monkeypatch)                 # only org A has breaches
    cb = login(b["admin_email"], b["admin_password"])
    assert cb.get("/v1/analytics/timeseries").json()["days"] == []
    assert cb.get("/v1/analytics/by-agent").json()["agents"] == []
