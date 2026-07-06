"""Anchoring safety rails (Phase 7 · 7C).

Two rails so a live EVM anchor can't silently fail:
  * a wallet-balance floor that refuses a doomed transaction, and
  * failure/stale alerting (mirrors the grading dead-letter alert) so a run of
    failed anchors — or a chain that stopped advancing — pages someone.
"""

from __future__ import annotations

import hashlib
import types
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.db import SessionLocal
from app.models import ChainAnchor


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _settings(**kw):
    base = dict(alert_email="ops@foxy.audit",
                anchor_alert_cooldown=3600,
                anchor_stale_alert_seconds=0,
                anchor_wallet_min_balance_wei=0)
    base.update(kw)
    return types.SimpleNamespace(**base)


def _mock_email(monkeypatch) -> dict:
    from app import email as emailmod
    sent: dict = {}
    monkeypatch.setattr(emailmod, "send_email", lambda **kw: (sent.update(kw), True)[1])
    return sent


def _add_anchor(org_id, status, *, anchored_at, confirmed_at=None) -> None:
    db = SessionLocal()
    try:
        db.add(ChainAnchor(
            org_id=uuid.UUID(org_id), root_hash=_h(status + str(anchored_at)),
            last_seq=1, chain="evm", status=status,
            anchored_at=anchored_at, confirmed_at=confirmed_at))
        db.commit()
    finally:
        db.close()


# ─────────────────────────── wallet-balance floor ────────────────────────────

def test_wallet_funded_check_raises_below_floor():
    from app import anchor
    s = _settings(anchor_wallet_min_balance_wei=1000)
    anchor._ensure_wallet_funded(1000, s)          # exactly the floor → OK
    anchor._ensure_wallet_funded(5000, s)          # above → OK
    with pytest.raises(RuntimeError):
        anchor._ensure_wallet_funded(999, s)       # below → refuse a doomed tx


def test_wallet_funded_check_disabled_at_zero():
    from app import anchor
    anchor._ensure_wallet_funded(0, _settings(anchor_wallet_min_balance_wei=0))  # off → OK


# ─────────────────────────── failure / stale alerts ──────────────────────────

def test_alerts_when_latest_anchor_failed(make_org, monkeypatch):
    from app import anchor
    sent = _mock_email(monkeypatch)
    org = make_org()
    now = 10_000.0
    _add_anchor(org["org_id"], "failed",
                anchored_at=datetime.fromtimestamp(now, tz=timezone.utc))
    db = SessionLocal()
    try:
        assert anchor.alert_on_anchor_problems(db, _settings(), {}, now=now) is True
        assert sent["to"] == "ops@foxy.audit"
    finally:
        db.close()


def test_no_alert_when_latest_anchor_confirmed(make_org, monkeypatch):
    from app import anchor
    sent = _mock_email(monkeypatch)
    org = make_org()
    now = 10_000.0
    _add_anchor(org["org_id"], "confirmed",
                anchored_at=datetime.fromtimestamp(now, tz=timezone.utc),
                confirmed_at=datetime.fromtimestamp(now, tz=timezone.utc))
    db = SessionLocal()
    try:
        assert anchor.alert_on_anchor_problems(db, _settings(), {}, now=now) is False
        assert sent == {}
    finally:
        db.close()


def test_alerts_when_confirmed_anchor_is_stale(make_org, monkeypatch):
    from app import anchor
    sent = _mock_email(monkeypatch)
    org = make_org()
    now = 100_000.0
    old = datetime.fromtimestamp(now, tz=timezone.utc) - timedelta(days=3)
    _add_anchor(org["org_id"], "confirmed", anchored_at=old, confirmed_at=old)
    db = SessionLocal()
    try:
        # stale window = 1 day; the only confirmed anchor is 3 days old → alert
        s = _settings(anchor_stale_alert_seconds=86_400)
        assert anchor.alert_on_anchor_problems(db, s, {}, now=now) is True
        assert sent["to"] == "ops@foxy.audit"
    finally:
        db.close()


def test_anchor_alert_cooldown_then_realerts(make_org, monkeypatch):
    from app import anchor
    sent = _mock_email(monkeypatch)
    org = make_org()
    now = 10_000.0
    _add_anchor(org["org_id"], "failed",
                anchored_at=datetime.fromtimestamp(now, tz=timezone.utc))
    db = SessionLocal()
    try:
        state: dict = {}
        assert anchor.alert_on_anchor_problems(db, _settings(), state, now=now) is True
        sent.clear()
        assert anchor.alert_on_anchor_problems(db, _settings(), state, now=now + 60) is False
        assert sent == {}
        assert anchor.alert_on_anchor_problems(db, _settings(), state, now=now + 4000) is True
    finally:
        db.close()


def test_anchor_alert_log_only_without_email(make_org, monkeypatch):
    from app import anchor
    sent = _mock_email(monkeypatch)
    org = make_org()
    now = 10_000.0
    _add_anchor(org["org_id"], "failed",
                anchored_at=datetime.fromtimestamp(now, tz=timezone.utc))
    db = SessionLocal()
    try:
        ok = anchor.alert_on_anchor_problems(db, _settings(alert_email=""), {}, now=now)
        assert ok is False and sent == {}
    finally:
        db.close()
