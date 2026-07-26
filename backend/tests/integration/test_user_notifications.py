"""Phase D-S — the three Settings → Notifications toggles, end to end.

Covers: the new prefs round-trip through /v1/account/preferences; per-user
breach alerts gated on notify_breach_alerts and deduped against the org-level
notifier; the Monday-only weekly digest with its ISO-week marker dedupe; and
the opt-in monthly key-rotation reminder.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from app.db import SessionLocal
from app.models import ApiKey, Notification, OrgPolicy, User
from judge_helpers import give_judge_key


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _seed_breach(client, org, monkeypatch, *, tc=90, tag="hipaa", risk=90):
    """Ingest one event and grade it as a breach with a stubbed judge."""
    from app.schemas import Verdict
    from app import worker as workermod

    give_judge_key(org["org_id"])
    rows = [{"prompt_hash": _h(f"p{tc}"), "response_hash": _h(f"r{tc}"),
             "token_count": tc, "policy_tag": tag}]
    assert client.post("/v1/logs/batch", json=rows, headers=org["auth"]).status_code == 202
    monkeypatch.setattr(
        workermod.gemini, "evaluate",
        lambda meta, policy_config=None, history=None, api_key=None:
        Verdict(policy_breach=True, reason="pii detected", risk_score=risk))
    sent: list = []
    monkeypatch.setattr(workermod.email_mod, "send_email",
                        lambda **kw: sent.append(kw) or True)
    db = SessionLocal()
    try:
        for row in workermod._claim_batch(db, 100, 300):
            workermod._grade_one(db, row)
    finally:
        db.close()
    return sent


def _set_pref(db, email: str, key: str, value):
    u = db.query(User).filter(User.email == email).one()
    prefs = dict(u.preferences or {})
    prefs[key] = value
    u.preferences = prefs
    db.commit()


def _org_policy_off(db, org_id):
    """Silence the org-level notifier so per-user sends are unambiguous."""
    p = db.get(OrgPolicy, uuid.UUID(str(org_id)))
    if p is None:
        p = OrgPolicy(org_id=uuid.UUID(str(org_id)))
        db.add(p)
    p.notify_on_breach = "none"
    db.commit()


# ── preferences round-trip ──────────────────────────────────────────────────
def test_new_notification_prefs_persist(make_org, login):
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    r = c.put("/v1/account/preferences", json={"preferences": {
        "notify_breach_alerts": False, "notify_weekly_digest": False,
        "notify_key_rotation_reminders": True}})
    assert r.status_code == 200
    prefs = r.json()["preferences"]
    assert prefs["notify_breach_alerts"] is False
    assert prefs["notify_weekly_digest"] is False
    assert prefs["notify_key_rotation_reminders"] is True
    # survives a reload and rides along on /v1/auth/me
    assert c.get("/v1/account/preferences").json()["preferences"]["notify_breach_alerts"] is False
    assert c.get("/v1/auth/me").json()["preferences"]["notify_weekly_digest"] is False


# ── (a) per-user breach alerts ──────────────────────────────────────────────
def test_breach_alert_emails_opted_in_member(make_org, add_user, client, monkeypatch):
    org = make_org()
    add_user(org["org_id"], "member@example.com", "Passw0rd123", role="member")
    db = SessionLocal()
    try:
        _org_policy_off(db, org["org_id"])
    finally:
        db.close()
    sent = _seed_breach(client, org, monkeypatch)
    to = {kw["to"] for kw in sent}
    # default ON: both the admin and the member get one
    assert org["admin_email"] in to
    assert "member@example.com" in to
    assert all("breach" in kw["subject"].lower() for kw in sent)


def test_breach_alert_respects_opt_out(make_org, add_user, client, monkeypatch):
    org = make_org()
    add_user(org["org_id"], "quiet@example.com", "Passw0rd123", role="member")
    db = SessionLocal()
    try:
        _org_policy_off(db, org["org_id"])
        _set_pref(db, "quiet@example.com", "notify_breach_alerts", False)
    finally:
        db.close()
    sent = _seed_breach(client, org, monkeypatch)
    to = {kw["to"] for kw in sent}
    assert "quiet@example.com" not in to
    assert org["admin_email"] in to


def test_breach_alert_not_duplicated_with_org_level_email(make_org, client, monkeypatch):
    """The org-level notifier and the per-user sender must not both email the
    same address for one breach."""
    org = make_org()
    db = SessionLocal()
    try:
        p = db.get(OrgPolicy, uuid.UUID(str(org["org_id"])))
        if p is None:
            p = OrgPolicy(org_id=uuid.UUID(str(org["org_id"])))
            db.add(p)
        p.notify_on_breach = "immediate"
        p.notify_email = org["admin_email"]      # same address as the admin user
        db.commit()
    finally:
        db.close()
    sent = _seed_breach(client, org, monkeypatch)
    admin_mails = [kw for kw in sent if kw["to"] == org["admin_email"]]
    assert len(admin_mails) == 1


# ── (b) weekly digest ───────────────────────────────────────────────────────
def _seed_usage(org_id, day: date, *, logs=10, tokens=1000, breaches=2, graded=10):
    from sqlalchemy import text
    db = SessionLocal()
    try:
        db.execute(text(
            "INSERT INTO usage_daily (id, org_id, day, logs_count, tokens_sum, "
            "breach_count, graded_count, failed_count, pending_count) "
            "VALUES (:id, :oid, :day, :l, :t, :b, :g, 0, 0)"),
            {"id": uuid.uuid4(), "oid": uuid.UUID(str(org_id)), "day": day,
             "l": logs, "t": tokens, "b": breaches, "g": graded})
        db.commit()
    finally:
        db.close()


def _monday() -> date:
    d = date(2026, 7, 27)                      # a Monday
    assert d.isoweekday() == 1
    return d


def test_weekly_digest_sends_real_numbers_and_dedupes(make_org, monkeypatch):
    from app import user_notifications as un
    org = make_org()
    monday = _monday()
    _seed_usage(org["org_id"], monday - timedelta(days=3), logs=7, tokens=700, breaches=1)
    _seed_usage(org["org_id"], monday - timedelta(days=5), logs=5, tokens=300, breaches=2)
    sent: list = []
    monkeypatch.setattr(un.email_mod, "send_email", lambda **kw: sent.append(kw) or True)
    db = SessionLocal()
    try:
        assert un.send_weekly_digests(db, today=monday) == 1
        body = sent[0]["text"]
        assert "12" in body            # 7 + 5 real events, not fabricated
        assert "3" in body             # 1 + 2 breaches
        # a second pass in the same week must not re-send
        sent.clear()
        assert un.send_weekly_digests(db, today=monday) == 0
        assert sent == []
        # exactly one marker row for the week
        markers = db.query(Notification).filter(Notification.kind == "digest").all()
        assert len(markers) == 1
        assert markers[0].target_id.endswith(f"W{(monday - timedelta(days=7)).isocalendar().week:02d}")
    finally:
        db.close()


def test_weekly_digest_only_on_monday(make_org, monkeypatch):
    from app import user_notifications as un
    make_org()
    sent: list = []
    monkeypatch.setattr(un.email_mod, "send_email", lambda **kw: sent.append(kw) or True)
    db = SessionLocal()
    try:
        assert un.send_weekly_digests(db, today=_monday() + timedelta(days=1)) == 0
        assert sent == []
    finally:
        db.close()


def test_weekly_digest_respects_opt_out(make_org, monkeypatch):
    from app import user_notifications as un
    org = make_org()
    monday = _monday()
    _seed_usage(org["org_id"], monday - timedelta(days=2))
    db = SessionLocal()
    try:
        _set_pref(db, org["admin_email"], "notify_weekly_digest", False)
    finally:
        db.close()
    sent: list = []
    monkeypatch.setattr(un.email_mod, "send_email", lambda **kw: sent.append(kw) or True)
    db = SessionLocal()
    try:
        assert un.send_weekly_digests(db, today=monday) == 0
        assert sent == []
        # no recipients → no marker row burned, so opting back in still works later
        assert db.query(Notification).filter(Notification.kind == "digest").count() == 0
    finally:
        db.close()


# ── (c) key-rotation reminders ──────────────────────────────────────────────
def _age_keys(org_id, days: int):
    db = SessionLocal()
    try:
        old = datetime.now(timezone.utc) - timedelta(days=days)
        for k in db.query(ApiKey).filter(ApiKey.org_id == uuid.UUID(str(org_id))).all():
            k.created_at = old
        db.commit()
    finally:
        db.close()


def test_rotation_reminder_is_opt_in(make_org, monkeypatch):
    """Default OFF: an admin who never opted in gets nothing."""
    from app import user_notifications as un
    org = make_org()
    _age_keys(org["org_id"], 120)
    sent: list = []
    monkeypatch.setattr(un.email_mod, "send_email", lambda **kw: sent.append(kw) or True)
    db = SessionLocal()
    try:
        assert un.send_key_rotation_reminders(db, today=date(2026, 7, 27)) == 0
        assert sent == []
    finally:
        db.close()


def test_rotation_reminder_sends_once_per_month(make_org, monkeypatch):
    from app import user_notifications as un
    org = make_org()
    _age_keys(org["org_id"], 120)
    db = SessionLocal()
    try:
        _set_pref(db, org["admin_email"], "notify_key_rotation_reminders", True)
    finally:
        db.close()
    sent: list = []
    monkeypatch.setattr(un.email_mod, "send_email", lambda **kw: sent.append(kw) or True)
    db = SessionLocal()
    try:
        assert un.send_key_rotation_reminders(db, today=date(2026, 7, 27)) == 1
        assert sent[0]["to"] == org["admin_email"]
        assert "rotation" in sent[0]["subject"].lower()
        sent.clear()
        # same month → deduped
        assert un.send_key_rotation_reminders(db, today=date(2026, 7, 31)) == 0
        assert sent == []
        # next month → reminds again
        assert un.send_key_rotation_reminders(db, today=date(2026, 8, 3)) == 1
    finally:
        db.close()


def test_rotation_reminder_skips_fresh_keys(make_org, monkeypatch):
    from app import user_notifications as un
    org = make_org()
    _age_keys(org["org_id"], 10)
    db = SessionLocal()
    try:
        _set_pref(db, org["admin_email"], "notify_key_rotation_reminders", True)
    finally:
        db.close()
    sent: list = []
    monkeypatch.setattr(un.email_mod, "send_email", lambda **kw: sent.append(kw) or True)
    db = SessionLocal()
    try:
        assert un.send_key_rotation_reminders(db, today=date(2026, 7, 27)) == 0
        assert sent == []
    finally:
        db.close()


def test_rotation_reminder_skips_non_admin(make_org, add_user, monkeypatch):
    from app import user_notifications as un
    org = make_org()
    add_user(org["org_id"], "member@example.com", "Passw0rd123", role="member")
    _age_keys(org["org_id"], 120)
    db = SessionLocal()
    try:
        _set_pref(db, "member@example.com", "notify_key_rotation_reminders", True)
    finally:
        db.close()
    sent: list = []
    monkeypatch.setattr(un.email_mod, "send_email", lambda **kw: sent.append(kw) or True)
    db = SessionLocal()
    try:
        assert un.send_key_rotation_reminders(db, today=date(2026, 7, 27)) == 0
        assert sent == []
    finally:
        db.close()
