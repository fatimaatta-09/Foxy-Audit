"""GET /v1/logs/breaches?since_seq=N — the feed the desktop fox polls so it can
react to a *real* backend-detected policy breach (Phase 5 · 5A.1).

Grading here uses a controlled Verdict (test isolation, same pattern as
test_policies) — the endpoint, the chain, and the DB are all real; only the LLM's
answer is made deterministic so the test is repeatable.
"""

from __future__ import annotations

import hashlib
from tests.integration.judge_helpers import give_judge_key

_h = lambda s: hashlib.sha256(s.encode()).hexdigest()  # noqa: E731


def _ingest(client, org, token_counts, tag="chat"):
    payload = [
        {"prompt_hash": _h(f"p{i}"), "response_hash": _h(f"r{i}"),
         "token_count": tc, "policy_tag": tag}
        for i, tc in enumerate(token_counts)
    ]
    give_judge_key(org["org_id"])       # per-tenant judge: this org brings its own key
    r = client.post("/v1/logs/batch", headers=org["auth"], json=payload)
    assert r.status_code == 202, r.text


def _grade_all(monkeypatch, breach_when):
    """Grade every pending row; flag a breach when breach_when(meta) is True."""
    from app import worker as workermod
    from app.schemas import Verdict

    def fake_eval(meta, policy_config=None, history=None, api_key=None):
        breach = breach_when(meta)
        return Verdict(
            policy_breach=breach,
            reason="policy tripped" if breach else "ok",
            risk_score=87 if breach else 0,
        )

    monkeypatch.setattr(workermod.gemini, "evaluate", fake_eval)
    from app.db import SessionLocal
    db = SessionLocal()
    try:
        rows = workermod._claim_batch(db, 100, 300)
        for row in rows:
            workermod._grade_one(db, row)
    finally:
        db.close()


def test_returns_only_graded_breaches(make_org, client, monkeypatch):
    """The feed returns graded breach rows only — not clean rows, not pending ones."""
    org = make_org()
    # 3 rows; only the middle (token_count 500) grades as a breach.
    _ingest(client, org, [10, 500, 20])
    _grade_all(monkeypatch, breach_when=lambda m: m["token_count"] > 100)

    r = client.get("/v1/logs/breaches", headers=org["auth"])
    assert r.status_code == 200, r.text
    items = r.json()
    assert len(items) == 1, items
    b = items[0]
    assert b["seq"] == 2
    assert b["policy_tag"] == "chat"
    assert b["risk_score"] == 87
    assert b["reason"] == "policy tripped"


def test_since_seq_filters(make_org, client, monkeypatch):
    """?since_seq=N returns only breaches with seq > N (so the poller never
    re-fires a breach the fox already reacted to)."""
    org = make_org()
    _ingest(client, org, [500, 10, 600])  # seq 1 breach, 2 clean, 3 breach
    _grade_all(monkeypatch, breach_when=lambda m: m["token_count"] > 100)

    items = client.get("/v1/logs/breaches?since_seq=1", headers=org["auth"]).json()
    assert [b["seq"] for b in items] == [3]


def test_pending_breach_excluded(make_org, client, monkeypatch):
    """A breach that hasn't been graded yet is not in the feed."""
    org = make_org()
    _ingest(client, org, [500])           # seq 1
    _grade_all(monkeypatch, breach_when=lambda m: True)   # grade seq 1
    _ingest(client, org, [600])           # seq 2 — left pending (ungraded)

    items = client.get("/v1/logs/breaches", headers=org["auth"]).json()
    assert [b["seq"] for b in items] == [1]


def test_breaches_isolated_between_orgs(make_org, client, monkeypatch):
    """Org B never sees org A's breaches (tenant isolation)."""
    a, b = make_org(), make_org()
    _ingest(client, a, [500])
    _grade_all(monkeypatch, breach_when=lambda m: True)

    assert client.get("/v1/logs/breaches", headers=b["auth"]).json() == []
    assert len(client.get("/v1/logs/breaches", headers=a["auth"]).json()) == 1
