"""/v1/stats — truthful dashboard fields (Phase 7 · 7A-3).

The web dashboard showed a hardcoded "Gemini 1.5 Pro" model label and a fabricated
"42ms judge latency". /v1/stats now surfaces the real judge model and a real average
time-to-verdict so the dashboard can render them instead of static fibs.
"""

from __future__ import annotations

import hashlib

from sqlalchemy import text

from app.db import SessionLocal


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _ingest(client, org, n):
    rows = [{"prompt_hash": _h(f"p{i}"), "response_hash": _h(f"r{i}"),
             "token_count": 100, "policy_tag": "chat"} for i in range(n)]
    assert client.post("/v1/logs/batch", json=rows, headers=org["auth"]).status_code == 202


def test_stats_exposes_real_judge_model(make_org, client):
    org = make_org()
    _ingest(client, org, 1)
    s = client.get("/v1/stats", headers=org["auth"]).json()
    assert s["judge_model"] == "gemini-2.5-flash"       # the real configured model


def test_stats_time_to_verdict_is_measured(make_org, client):
    org = make_org()
    _ingest(client, org, 3)
    db = SessionLocal()
    try:
        db.execute(text("UPDATE audit_logs SET grading_status='graded', "
                        "graded_at = created_at + interval '2 seconds'"))
        db.commit()
    finally:
        db.close()
    s = client.get("/v1/stats", headers=org["auth"]).json()
    assert s["avg_seconds_to_verdict"] == 2.0


def test_stats_time_to_verdict_null_when_nothing_graded(make_org, client):
    org = make_org()
    _ingest(client, org, 2)                              # all still pending
    s = client.get("/v1/stats", headers=org["auth"]).json()
    assert s["avg_seconds_to_verdict"] is None
