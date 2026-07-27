"""P2 · §F — breach notifier (worker) + notification-destination config.

The worker fires an email (and optional webhook) when a breach is graded and
the org's policy asks for immediate notice. Content-blind: seq/risk/reason only.
"""
from __future__ import annotations

import hashlib
import uuid

from app import org_notifications, worker
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


def _grade_first(monkeypatch, *, drain=True):
    """Grade one pending row, then drain the org-notice queue.

    The send is no longer inline in grading — _grade_one only queues — so a
    test that wants the email has to drain, exactly as the notifications thread
    does in production."""
    sent = []
    monkeypatch.setattr(org_notifications.email_mod, "send_email",
                        lambda **kw: sent.append(kw) or True)
    db = SessionLocal()
    try:
        rows = worker._claim_batch(db, 10, 300)
        assert rows, "expected a pending row to grade"
        worker._grade_one(db, rows[0])
        if drain:
            org_notifications.drain_breach_notices(db)
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
    monkeypatch.setattr(org_notifications.email_mod, "send_email", lambda **kw: True)
    monkeypatch.setattr(org_notifications.requests, "post",
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


# ── the send is decoupled from grading ──────────────────────────────────────

def test_grading_only_queues_and_never_calls_the_provider(
        make_org, client, monkeypatch):
    """The defect this fixes.

    The org notice used to be sent inline in _grade_one — a synchronous
    send_email plus a 5 s webhook POST inside the loop that also drives the
    worker's liveness heartbeat, so a mail provider that accepts and then hangs
    could stall grading and take /health/ready down with it. Grading must now
    touch no provider at all.
    """
    org = make_org()
    client.post("/v1/logs/batch", json=[_breach_event()], headers=org["auth"])
    _set_policy(org["org_id"], notify_on_breach="immediate",
                notify_email="alerts@corp.com")

    calls = []
    monkeypatch.setattr(org_notifications.email_mod, "send_email",
                        lambda **kw: calls.append(kw) or True)
    monkeypatch.setattr(org_notifications.requests, "post",
                        lambda url, **kw: calls.append(url) or True)

    before = org_notifications.queue_depth()
    db = SessionLocal()
    try:
        rows = worker._claim_batch(db, 10, 300)
        assert rows
        worker._grade_one(db, rows[0])
        assert calls == [], "grading must not call the mail provider inline"
        assert org_notifications.queue_depth() == before + 1

        # ...and the notice really does go out once the drain runs.
        assert org_notifications.drain_breach_notices(db) == 1
        assert any(c.get("to") == "alerts@corp.com"
                   for c in calls if isinstance(c, dict))
    finally:
        db.close()


def test_a_wedged_provider_cannot_stall_grading(make_org, client, monkeypatch):
    """A send that never returns is the failure mode that trips readiness. With
    the send queued, grading completes even when the provider is wedged."""
    org = make_org()
    client.post("/v1/logs/batch", json=[_breach_event()], headers=org["auth"])
    _set_policy(org["org_id"], notify_on_breach="immediate",
                notify_email="alerts@corp.com")

    def _hang(**_kw):
        raise AssertionError("the provider must not be reached during grading")

    monkeypatch.setattr(org_notifications.email_mod, "send_email", _hang)
    db = SessionLocal()
    try:
        rows = worker._claim_batch(db, 10, 300)
        worker._grade_one(db, rows[0])       # would raise if it sent inline
    finally:
        _drain_queue()
        db.close()


def _drain_queue():
    """Leave the module queue as we found it, so one test cannot feed another."""
    while org_notifications.queue_depth():
        try:
            org_notifications._NOTICE_QUEUE.get_nowait()
        except Exception:
            break


def test_a_failing_notice_does_not_stop_the_drain(make_org, client, monkeypatch):
    """One bad recipient must not strand every notice behind it."""
    org = make_org()
    client.post("/v1/logs/batch", json=[_breach_event()], headers=org["auth"])
    _set_policy(org["org_id"], notify_on_breach="immediate",
                notify_email="alerts@corp.com")

    boom = {"n": 0}

    def _explode(**_kw):
        boom["n"] += 1
        raise RuntimeError("provider down")

    monkeypatch.setattr(org_notifications.email_mod, "send_email", _explode)
    db = SessionLocal()
    try:
        rows = worker._claim_batch(db, 10, 300)
        worker._grade_one(db, rows[0])
        assert org_notifications.drain_breach_notices(db) == 0   # nothing sent
        assert boom["n"] == 1                                    # but it tried
        assert org_notifications.queue_depth() == 0              # and drained
    finally:
        db.close()


def test_policy_turned_off_between_grade_and_send_suppresses_it(
        make_org, client, monkeypatch):
    """The policy is re-read at send time, not captured at enqueue time."""
    org = make_org()
    client.post("/v1/logs/batch", json=[_breach_event()], headers=org["auth"])
    _set_policy(org["org_id"], notify_on_breach="immediate",
                notify_email="alerts@corp.com")

    sent = []
    monkeypatch.setattr(org_notifications.email_mod, "send_email",
                        lambda **kw: sent.append(kw) or True)
    db = SessionLocal()
    try:
        rows = worker._claim_batch(db, 10, 300)
        worker._grade_one(db, rows[0])
    finally:
        db.close()

    _set_policy(org["org_id"], notify_on_breach="none")
    db = SessionLocal()
    try:
        assert org_notifications.drain_breach_notices(db) == 0
    finally:
        db.close()
    assert sent == []


# ── the kill switch stops the enqueue, not just the drain ───────────────────

def test_user_alert_kill_switch_stops_the_enqueue(monkeypatch):
    """`user_notifications_enabled=false` gates the thread that DRAINS the
    per-user queue, so grading used to keep filling a queue nobody read — it
    hit its cap and then logged "queue full" for every breach from then on."""
    from app import user_notifications
    from app.config import get_settings

    settings = get_settings()
    before = user_notifications._BREACH_QUEUE.qsize()

    class _V:
        risk_score = 90
        reason = "pii"

    monkeypatch.setattr(settings, "user_notifications_enabled", False)
    user_notifications.enqueue_breach_alert({"org_id": uuid.uuid4(), "seq": 1}, _V())
    assert user_notifications._BREACH_QUEUE.qsize() == before

    monkeypatch.setattr(settings, "user_notifications_enabled", True)
    user_notifications.enqueue_breach_alert({"org_id": uuid.uuid4(), "seq": 2}, _V())
    assert user_notifications._BREACH_QUEUE.qsize() == before + 1
    user_notifications._BREACH_QUEUE.get_nowait()          # leave it as found


def test_org_notices_survive_the_per_user_kill_switch(
        make_org, client, monkeypatch):
    """The switch governs the per-user preference fan-out. A tenant's own
    configured policy notice is a different feature and must keep working."""
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "user_notifications_enabled", False)
    org = make_org()
    client.post("/v1/logs/batch", json=[_breach_event()], headers=org["auth"])
    _set_policy(org["org_id"], notify_on_breach="immediate",
                notify_email="alerts@corp.com")
    sent = _grade_first(monkeypatch)
    assert any(m["to"] == "alerts@corp.com" for m in sent), sent
