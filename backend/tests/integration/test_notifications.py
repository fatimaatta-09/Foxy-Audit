"""P16 — customer notifications center (generated from REAL breach events).

Covers: auth-gating; honest empty state; a real policy breach becomes a notification and the sync is
idempotent (no duplicates); mark-read; read-all; org isolation (RLS).
"""

from __future__ import annotations

import hashlib

_h = lambda s: hashlib.sha256(s.encode()).hexdigest()  # noqa: E731


def _seed_breach(client, org, monkeypatch, tc=90, tag="hipaa", risk=90):
    from app.schemas import Verdict
    rows = [{"prompt_hash": _h(f"np{tc}"), "response_hash": _h(f"nr{tc}"),
             "token_count": tc, "policy_tag": tag}]
    assert client.post("/v1/logs/batch", json=rows, headers=org["auth"]).status_code == 202
    from app import worker as workermod
    monkeypatch.setattr(workermod.gemini, "evaluate",
                        lambda meta, policy_config=None, history=None:
                        Verdict(policy_breach=True, reason="x", risk_score=risk))
    from app.db import SessionLocal
    db = SessionLocal()
    try:
        for row in workermod._claim_batch(db, 100, 300):
            workermod._grade_one(db, row)
    finally:
        db.close()


def test_notifications_require_auth(client):
    assert client.get("/v1/notifications").status_code == 401
    assert client.post("/v1/notifications/read-all").status_code in (401, 403)


def test_empty_when_no_breaches(make_org, login):
    o = make_org()
    d = login(o["admin_email"], o["admin_password"]).get("/v1/notifications").json()
    assert d["unread"] == 0 and d["items"] == []


def test_breach_becomes_notification_idempotent(make_org, login, client, monkeypatch):
    o = make_org()
    _seed_breach(client, o, monkeypatch)
    c = login(o["admin_email"], o["admin_password"])
    d = c.get("/v1/notifications").json()
    assert d["unread"] >= 1
    breach = [n for n in d["items"] if n["kind"] == "breach"]
    assert breach and breach[0]["read"] is False and breach[0]["level"] == "critical"
    n1 = len(d["items"])
    assert len(c.get("/v1/notifications").json()["items"]) == n1     # idempotent — no duplicate


def test_mark_read_and_read_all(make_org, login, client, monkeypatch):
    o = make_org()
    _seed_breach(client, o, monkeypatch)
    c = login(o["admin_email"], o["admin_password"])
    nid = c.get("/v1/notifications").json()["items"][0]["id"]
    assert c.post(f"/v1/notifications/{nid}/read").status_code == 200
    assert next(n for n in c.get("/v1/notifications").json()["items"] if n["id"] == nid)["read"] is True
    c.post("/v1/notifications/read-all")
    assert c.get("/v1/notifications").json()["unread"] == 0


def test_org_isolation(make_org, login, client, monkeypatch):
    a = make_org()
    b = make_org()
    _seed_breach(client, a, monkeypatch)
    assert login(b["admin_email"], b["admin_password"]).get("/v1/notifications").json()["items"] == []
