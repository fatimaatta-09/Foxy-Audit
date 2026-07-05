"""Dead-letter alerting on failed grading (Phase 5 · 5E.1).

When rows pile up in grading_status='failed' (the Gemini judge exhausted all
retries), the worker's maintenance pass logs a WARNING and — at most once per
cooldown — emails a staff alert. Silent today; this makes it loud.
"""

from __future__ import annotations

import hashlib
import types

from sqlalchemy import text

from app.db import SessionLocal


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _ingest_failed(client, org, n: int) -> None:
    rows = [{"prompt_hash": _h(f"{org['org_id']}p{i}"),
             "response_hash": _h(f"{org['org_id']}r{i}"),
             "token_count": 10, "policy_tag": "chat"} for i in range(n)]
    assert client.post("/v1/logs/batch", json=rows, headers=org["auth"]).status_code == 202
    db = SessionLocal()
    try:
        db.execute(text("UPDATE audit_logs SET grading_status = 'failed'"))
        db.commit()
    finally:
        db.close()


def _settings(**kw):
    base = dict(grading_failure_alert_threshold=1,
                grading_failure_alert_cooldown=3600,
                alert_email="ops@foxy.audit")
    base.update(kw)
    return types.SimpleNamespace(**base)


def _mock_email(monkeypatch) -> dict:
    from app import email as emailmod
    sent: dict = {}
    monkeypatch.setattr(emailmod, "send_email", lambda **kw: (sent.update(kw), True)[1])
    return sent


def test_alerts_when_failures_at_threshold(make_org, client, monkeypatch):
    from app import usage
    sent = _mock_email(monkeypatch)
    _ingest_failed(client, make_org(), 2)
    db = SessionLocal()
    try:
        ok = usage.alert_on_grading_failures(db, _settings(), {}, now=1000.0)
        assert ok is True
        assert sent["to"] == "ops@foxy.audit"
        assert "2" in (sent["subject"] + sent.get("text", ""))
    finally:
        db.close()


def test_no_alert_below_threshold(monkeypatch):
    from app import usage
    sent = _mock_email(monkeypatch)
    db = SessionLocal()
    try:
        ok = usage.alert_on_grading_failures(db, _settings(), {}, now=1000.0)  # 0 failed rows
        assert ok is False and sent == {}
    finally:
        db.close()


def test_cooldown_suppresses_repeat_then_re_alerts(make_org, client, monkeypatch):
    from app import usage
    sent = _mock_email(monkeypatch)
    _ingest_failed(client, make_org(), 2)
    db = SessionLocal()
    try:
        state: dict = {}
        assert usage.alert_on_grading_failures(db, _settings(), state, now=1000.0) is True
        sent.clear()
        assert usage.alert_on_grading_failures(db, _settings(), state, now=1060.0) is False  # within cooldown
        assert sent == {}
        assert usage.alert_on_grading_failures(db, _settings(), state, now=5000.0) is True    # cooldown elapsed
    finally:
        db.close()


def test_log_only_when_no_alert_email(make_org, client, monkeypatch):
    from app import usage
    sent = _mock_email(monkeypatch)
    _ingest_failed(client, make_org(), 1)
    db = SessionLocal()
    try:
        ok = usage.alert_on_grading_failures(db, _settings(alert_email=""), {}, now=1000.0)
        assert ok is False and sent == {}       # threshold met (logged) but no email configured
    finally:
        db.close()
