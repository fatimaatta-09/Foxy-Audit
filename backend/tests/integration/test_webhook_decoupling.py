"""Webhook delivery is queued, and the judge's history comes from audit_logs.

Two carry-overs from the notification decoupling.

C5 — `webhook_delivery.deliver_grading()` was called inline from
`worker._grade_one`: one synchronous `requests.post(timeout=5)` per active
subscription, in the loop that also drives the worker's liveness heartbeat. It
fires on EVERY graded row rather than only on breaches, so it was the larger of
the two heartbeat risks and the one still live after the emails moved.

C6 — `worker._org_history()` read the `usage_daily` rollup, which recomputes
only a rolling 48-hour window, and hands the result to the AI judge as its
7-day temporal signal.
"""
from __future__ import annotations

import hashlib
import uuid

from sqlalchemy import text

from app import org_notifications, user_notifications, webhook_delivery, worker
from app.db import SessionLocal
from app.models import OrgPolicy, WebhookSubscription


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _clean_event():
    """No PII signals — grades clean, so this is NOT a breach."""
    return {"event_id": str(uuid.uuid4()), "prompt_hash": _h("p"),
            "response_hash": _h("r"), "token_count": 10, "policy_tag": "chat"}


def _breach_event():
    return {"event_id": str(uuid.uuid4()), "prompt_hash": _h("bp"),
            "response_hash": _h("br"), "token_count": 10, "policy_tag": "chat",
            "pii_signals": ["email"]}


def _subscribe(org_id, events="graded,breach", url="https://example.test/hook"):
    db = SessionLocal()
    try:
        db.add(WebhookSubscription(
            org_id=uuid.UUID(org_id), url=url, secret="s3cret",
            events=events, active=True))
        db.commit()
    finally:
        db.close()


def _drain_all():
    """Leave every module queue as we found it."""
    for mod, q in ((webhook_delivery, webhook_delivery._DELIVERY_QUEUE),
                   (org_notifications, org_notifications._NOTICE_QUEUE),
                   (user_notifications, user_notifications._BREACH_QUEUE)):
        while q.qsize():
            try:
                q.get_nowait()
            except Exception:
                break


def _grade_all():
    db = SessionLocal()
    try:
        rows = worker._claim_batch(db, 10, 300)
        assert rows, "expected a pending row to grade"
        for row in rows:
            worker._grade_one(db, row)
    finally:
        db.close()


# ── C5: no POST inside the grading batch ────────────────────────────────────

def test_grading_queues_the_webhook_instead_of_posting(make_org, client, monkeypatch):
    org = make_org()
    _subscribe(org["org_id"])
    client.post("/v1/logs/batch", json=[_clean_event()], headers=org["auth"])

    posts = []
    monkeypatch.setattr(webhook_delivery.requests, "post",
                        lambda url, **kw: posts.append(url) or _Resp())

    before = webhook_delivery.queue_depth()
    try:
        _grade_all()
        assert posts == [], "grading must not POST inline"
        assert webhook_delivery.queue_depth() == before + 1

        db = SessionLocal()
        try:
            assert webhook_delivery.drain_deliveries(db) == 1
        finally:
            db.close()
        assert posts == ["https://example.test/hook"]
    finally:
        _drain_all()


class _Resp:
    status_code = 200


def test_no_outbound_call_of_any_kind_happens_during_grading(
        make_org, client, monkeypatch):
    """The whole claim, asserted at once: after the emails AND the webhooks
    moved, a graded breach touches no third party from inside the batch."""
    org = make_org()
    _subscribe(org["org_id"])
    db = SessionLocal()
    try:
        oid = uuid.UUID(org["org_id"])
        p = db.get(OrgPolicy, oid) or OrgPolicy(org_id=oid)
        p.notify_on_breach = "immediate"
        p.notify_email = "alerts@corp.com"
        p.notify_webhook_url = "https://example.test/policy-hook"
        db.add(p)
        db.commit()
    finally:
        db.close()
    client.post("/v1/logs/batch", json=[_breach_event()], headers=org["auth"])

    def _boom(*_a, **_kw):
        raise AssertionError("no provider may be called inside the grading batch")

    monkeypatch.setattr(webhook_delivery.requests, "post", _boom)
    monkeypatch.setattr(org_notifications.requests, "post", _boom)
    monkeypatch.setattr(org_notifications.email_mod, "send_email", _boom)
    monkeypatch.setattr(user_notifications.email_mod, "send_email", _boom)
    try:
        _grade_all()          # raises if anything reaches out
    finally:
        _drain_all()


def test_the_webhook_fires_on_clean_rows_too_not_just_breaches(
        make_org, client, monkeypatch):
    """Why this one mattered more than the emails: it runs on every graded
    row, so the stall was proportional to total volume, not breach volume."""
    org = make_org()
    _subscribe(org["org_id"], events="graded")
    client.post("/v1/logs/batch", json=[_clean_event()], headers=org["auth"])
    posts = []
    monkeypatch.setattr(webhook_delivery.requests, "post",
                        lambda url, **kw: posts.append(url) or _Resp())
    try:
        _grade_all()
        db = SessionLocal()
        try:
            webhook_delivery.drain_deliveries(db)
        finally:
            db.close()
        assert posts, "a clean graded row must still deliver a 'graded' event"
    finally:
        _drain_all()


def test_a_subscription_deactivated_before_the_drain_is_not_delivered_to(
        make_org, client, monkeypatch):
    """Subscriptions are resolved at send time, not captured at enqueue."""
    org = make_org()
    _subscribe(org["org_id"])
    client.post("/v1/logs/batch", json=[_clean_event()], headers=org["auth"])
    posts = []
    monkeypatch.setattr(webhook_delivery.requests, "post",
                        lambda url, **kw: posts.append(url) or _Resp())
    try:
        _grade_all()
        db = SessionLocal()
        try:
            db.execute(text("UPDATE webhook_subscriptions SET active = false "
                            "WHERE org_id = :oid"),
                       {"oid": uuid.UUID(org["org_id"])})
            db.commit()
            assert webhook_delivery.drain_deliveries(db) == 0
        finally:
            db.close()
        assert posts == []
    finally:
        _drain_all()


def test_one_dead_subscriber_does_not_stop_the_drain(make_org, client, monkeypatch):
    org = make_org()
    _subscribe(org["org_id"])
    client.post("/v1/logs/batch", json=[_clean_event()], headers=org["auth"])

    def _explode(url, **_kw):
        raise RuntimeError("subscriber down")

    monkeypatch.setattr(webhook_delivery.requests, "post", _explode)
    try:
        _grade_all()
        db = SessionLocal()
        try:
            webhook_delivery.drain_deliveries(db)
            assert webhook_delivery.queue_depth() == 0
        finally:
            db.close()
    finally:
        _drain_all()


# ── C6: the judge's 7-day history is not stale ──────────────────────────────

def test_org_history_counts_days_the_rollup_never_recomputed(
        make_org, client):
    """The rollup only ever recomputes today + yesterday, so a breach four days
    old was invisible to the judge's temporal signal — it saw 0 breaches over a
    week that had one, and a 0% breach rate."""
    from app import usage

    org = make_org()
    client.post("/v1/logs/batch", json=[_breach_event()], headers=org["auth"])
    try:
        _grade_all()          # marks it graded + breach
    finally:
        _drain_all()

    db = SessionLocal()
    try:
        oid = uuid.UUID(org["org_id"])
        db.execute(text("UPDATE audit_logs SET created_at = now() - interval "
                        "'4 days' WHERE org_id = :oid"), {"oid": oid})
        db.commit()
        usage.rollup_recent(db)          # exactly what the worker runs
        stale = db.execute(text(
            "SELECT coalesce(sum(breach_count), 0) FROM usage_daily "
            "WHERE org_id = :oid"), {"oid": oid}).scalar_one()
        assert stale == 0, "precondition: the rollup must not see the old day"

        history = worker._org_history(db, oid)
    finally:
        db.close()

    assert history["window_days"] == 7
    assert history["recent_graded"] == 1
    assert history["recent_breaches"] == 1
    assert history["breach_rate_pct"] == 100.0


def test_org_history_is_scoped_to_one_org(make_org, client):
    a, b = make_org(), make_org()
    client.post("/v1/logs/batch", json=[_breach_event()], headers=b["auth"])
    try:
        _grade_all()
    finally:
        _drain_all()

    db = SessionLocal()
    try:
        history = worker._org_history(db, uuid.UUID(a["org_id"]))
    finally:
        db.close()
    assert history["recent_graded"] == 0 and history["recent_breaches"] == 0
    assert history["breach_rate_pct"] == 0.0


def test_org_history_ignores_events_outside_the_window(make_org, client):
    org = make_org()
    client.post("/v1/logs/batch", json=[_breach_event()], headers=org["auth"])
    try:
        _grade_all()
    finally:
        _drain_all()

    db = SessionLocal()
    try:
        oid = uuid.UUID(org["org_id"])
        db.execute(text("UPDATE audit_logs SET created_at = now() - interval "
                        "'30 days' WHERE org_id = :oid"), {"oid": oid})
        db.commit()
        history = worker._org_history(db, oid)
    finally:
        db.close()
    assert history["recent_graded"] == 0
