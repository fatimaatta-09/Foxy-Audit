"""P2 · §F — breach notifier (worker) + notification-destination config.

The worker fires an email (and optional webhook) when a breach is graded and
the org's policy asks for immediate notice. Content-blind: seq/risk/reason only.
"""
from __future__ import annotations

import hashlib
import uuid

from app import worker
from app.db import SessionLocal
from app.models import OrgPolicy


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _breach_event():
    return {
        "event_id": str(uuid.uuid4()),
        "prompt_hash": _h("p"), "response_hash": _h("r"),
        "token_count": 10, "policy_tag": "chat",
        "pii_signals": ["email"],   # deterministic breach via policy_engine
    }


def _set_policy(org_id, **fields):
    db = SessionLocal()
    try:
        oid = uuid.UUID(org_id)
        p = db.get(OrgPolicy, oid) or OrgPolicy(org_id=oid)
        for k, v in fields.items():
            setattr(p, k, v)
        db.add(p)
        db.commit()
    finally:
        db.close()


def _grade_first(monkeypatch):
    sent = []
    monkeypatch.setattr(worker.email_mod, "send_email",
                        lambda **kw: sent.append(kw) or True)
    db = SessionLocal()
    try:
        rows = worker._claim_batch(db, 10, 300)
        assert rows, "expected a pending row to grade"
        worker._grade_one(db, rows[0])
    finally:
        db.close()
    return sent


def test_email_sent_on_breach_when_immediate(make_org, client, monkeypatch):
    org = make_org()
    client.post("/v1/logs/batch", json=[_breach_event()], headers=org["auth"])
    _set_policy(org["org_id"], notify_on_breach="immediate", notify_email="alerts@corp.com")
    sent = _grade_first(monkeypatch)
    assert any(m["to"] == "alerts@corp.com" for m in sent), sent


def test_no_email_when_notify_none(make_org, client, monkeypatch):
    org = make_org()
    client.post("/v1/logs/batch", json=[_breach_event()], headers=org["auth"])
    _set_policy(org["org_id"], notify_on_breach="none", notify_email="alerts@corp.com")
    sent = _grade_first(monkeypatch)
    assert not any(m.get("to") == "alerts@corp.com" for m in sent)


def test_webhook_posted_on_breach(make_org, client, monkeypatch):
    org = make_org()
    client.post("/v1/logs/batch", json=[_breach_event()], headers=org["auth"])
    _set_policy(org["org_id"], notify_on_breach="immediate",
                notify_webhook_url="https://example.test/hook")
    posts = []
    monkeypatch.setattr(worker.email_mod, "send_email", lambda **kw: True)
    monkeypatch.setattr(worker.requests, "post",
                        lambda url, **kw: posts.append((url, kw)) or True)
    _grade_first(monkeypatch)
    assert posts and posts[0][0] == "https://example.test/hook"
    assert posts[0][1]["json"]["type"] == "policy_breach"


# ── config round-trip + validation (PUT /v1/policies) ──

def test_notify_config_roundtrips(make_org, login):
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    body = c.get("/v1/policies").json()
    body["notify_email"] = "sec@corp.com"
    body["notify_webhook_url"] = "https://corp.com/foxy-hook"
    assert c.put("/v1/policies", json=body).status_code == 200
    got = c.get("/v1/policies").json()
    assert got["notify_email"] == "sec@corp.com"
    assert got["notify_webhook_url"] == "https://corp.com/foxy-hook"


def test_notify_config_rejects_bad_values(make_org, login):
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    body = c.get("/v1/policies").json()
    bad_hook = dict(body, notify_webhook_url="ftp://nope")
    assert c.put("/v1/policies", json=bad_hook).status_code == 422
    bad_email = dict(body, notify_email="not-an-email")
    assert c.put("/v1/policies", json=bad_email).status_code == 422
