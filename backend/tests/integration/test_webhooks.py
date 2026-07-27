"""P3 · §F — outbound webhook subscriptions: CRUD, admin-only, isolation,
signed delivery from the grading worker, and a test ping."""
from __future__ import annotations

import hashlib
import hmac
import json
import uuid

from app import webhook_delivery, worker
from app.db import SessionLocal


class _Resp:
    def __init__(self, code): self.status_code = code


def _capture(monkeypatch):
    calls = []
    monkeypatch.setattr(webhook_delivery.requests, "post",
                        lambda url, **kw: calls.append((url, kw)) or _Resp(200))
    return calls


def _breach_event():
    h = lambda s: hashlib.sha256(s.encode()).hexdigest()
    return {"event_id": str(uuid.uuid4()), "prompt_hash": h("p"), "response_hash": h("r"),
            "token_count": 10, "policy_tag": "chat", "pii_signals": ["email"]}


def _clean_event():
    h = lambda s: hashlib.sha256(s.encode()).hexdigest()
    return {"event_id": str(uuid.uuid4()), "prompt_hash": h("pc"), "response_hash": h("rc"),
            "token_count": 10, "policy_tag": "chat"}


def _grade_all():
    """Grade everything pending, then drain the delivery queue.

    Grading only QUEUES deliveries now — one synchronous POST per subscription
    inside the batch was stalling the worker heartbeat, and it fires on every
    graded row. The drain thread does the POSTing in production; here the test
    does it explicitly."""
    db = SessionLocal()
    try:
        for row in worker._claim_batch(db, 50, 300):
            worker._grade_one(db, row)
        webhook_delivery.drain_deliveries(db)
    finally:
        db.close()


# ── CRUD / guards ────────────────────────────────────────────────────────────

def test_create_list_delete(make_org, login):
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    r = c.post("/v1/webhooks", json={"url": "https://ex.test/hook", "events": ["breach"]})
    assert r.status_code == 200, r.text
    created = r.json()
    assert created["secret"].startswith("whsec_") and created["events"] == "breach"
    lst = c.get("/v1/webhooks").json()
    assert len(lst) == 1 and "secret" not in lst[0] and lst[0]["secret_prefix"].startswith("whsec_")
    assert c.delete(f"/v1/webhooks/{created['id']}").status_code == 200
    assert c.get("/v1/webhooks").json() == []


def test_validation(make_org, login):
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    assert c.post("/v1/webhooks", json={"url": "ftp://x", "events": ["breach"]}).status_code == 422
    assert c.post("/v1/webhooks", json={"url": "https://x.test", "events": ["nope"]}).status_code == 422


def test_admin_only(make_org, add_user, login):
    org = make_org()
    add_user(org["org_id"], "m@corp.com", "memberpass1", role="member")
    c = login("m@corp.com", "memberpass1")
    assert c.post("/v1/webhooks", json={"url": "https://x.test", "events": ["breach"]}).status_code == 403
    assert c.get("/v1/webhooks").status_code == 403


def test_isolation(make_org, login):
    a, b = make_org(), make_org()
    cb = login(b["admin_email"], b["admin_password"])
    wid = cb.post("/v1/webhooks", json={"url": "https://b.test", "events": ["breach"]}).json()["id"]
    ca = login(a["admin_email"], a["admin_password"])
    assert wid not in [w["id"] for w in ca.get("/v1/webhooks").json()]
    assert ca.delete(f"/v1/webhooks/{wid}").status_code == 404


# ── signed delivery from the worker ──────────────────────────────────────────

def test_breach_delivery_is_signed(make_org, login, client, monkeypatch):
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    created = c.post("/v1/webhooks", json={"url": "https://ex.test/hook", "events": ["breach"]}).json()
    calls = _capture(monkeypatch)
    client.post("/v1/logs/batch", json=[_breach_event()], headers=org["auth"])
    _grade_all()
    assert calls, "expected a webhook delivery"
    url, kw = calls[0]
    assert url == "https://ex.test/hook"
    body = kw["data"]
    expected = "sha256=" + hmac.new(created["secret"].encode(), body, hashlib.sha256).hexdigest()
    assert kw["headers"]["X-Foxy-Signature"] == expected
    assert kw["headers"]["X-Foxy-Event"] == "breach"
    assert json.loads(body)["type"] == "breach"


def test_graded_only_sub_skips_and_receives(make_org, login, client, monkeypatch):
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    c.post("/v1/webhooks", json={"url": "https://g.test", "events": ["graded"]})
    calls = _capture(monkeypatch)
    client.post("/v1/logs/batch", json=[_clean_event()], headers=org["auth"])
    _grade_all()
    assert calls and json.loads(calls[0][1]["data"])["type"] == "graded"


def test_test_ping(make_org, login, monkeypatch):
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    wid = c.post("/v1/webhooks", json={"url": "https://ex.test", "events": ["breach"]}).json()["id"]
    _capture(monkeypatch)
    r = c.post(f"/v1/webhooks/{wid}/test")
    assert r.status_code == 200 and r.json()["status"] == "200"
