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
    """Ingest one event, grade it as a breach, then drain the alert queue —
    the real production path (grading enqueues, the notifications thread sends).
    Returns the per-user emails that were sent."""
    from app.schemas import Verdict
    from app import user_notifications as un
    from app import worker as workermod

    give_judge_key(org["org_id"])
    rows = [{"prompt_hash": _h(f"p{tc}"), "response_hash": _h(f"r{tc}"),
             "token_count": tc, "policy_tag": tag}]
    assert client.post("/v1/logs/batch", json=rows, headers=org["auth"]).status_code == 202
    monkeypatch.setattr(
        workermod.gemini, "evaluate",
        lambda meta, policy_config=None, history=None, api_key=None:
        Verdict(policy_breach=True, reason="pii detected", risk_score=risk))
    # worker.email_mod and user_notifications.email_mod are the SAME module
    # object, so one patch captures both senders — clear between the phases to
    # isolate the per-user drain from the org-level notifier.
    sent: list = []
    monkeypatch.setattr(un.email_mod, "send_email",
                        lambda **kw: sent.append(kw) or True)
    db = SessionLocal()
    try:
        for row in workermod._claim_batch(db, 100, 300):
            workermod._grade_one(db, row)
        sent.clear()                    # drop the org-level email
        un.drain_breach_alerts(db)      # …so `sent` is per-user only
    finally:
        db.close()
    return sent


@pytest.fixture(autouse=True)
def _empty_breach_queue():
    """The alert queue is module-level — never leak items between tests."""
    from app import user_notifications as un
    import queue as _q
    while True:
        try:
            un._BREACH_QUEUE.get_nowait()
        except _q.Empty:
            break
    yield


def _set_pref(db, email: str, key: str, value):
    u = db.query(User).filter(User.email == email).one()
    prefs = dict(u.preferences or {})
    prefs[key] = value
    u.preferences = prefs
    db.commit()


def _org_policy_off(db, org_id):
    """Tenant turned breach notifications off entirely."""
    _org_policy(db, org_id, mode="none")


def _org_policy(db, org_id, *, mode="immediate", notify_email=None):
    p = db.get(OrgPolicy, uuid.UUID(str(org_id)))
    if p is None:
        p = OrgPolicy(org_id=uuid.UUID(str(org_id)))
        db.add(p)
    p.notify_on_breach = mode
    p.notify_email = notify_email
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
        _org_policy(db, org["org_id"], notify_email="ops@example.org")
    finally:
        db.close()
    sent = _seed_breach(client, org, monkeypatch)
    to = {kw["to"] for kw in sent}
    # default ON: both the admin and the member get one
    assert org["admin_email"] in to
    assert "member@example.com" in to
    assert "ops@example.org" not in to      # that's the org-level notifier's job
    assert all("breach" in kw["subject"].lower() for kw in sent)


def test_breach_alert_respects_opt_out(make_org, add_user, client, monkeypatch):
    org = make_org()
    add_user(org["org_id"], "quiet@example.com", "Passw0rd123", role="member")
    db = SessionLocal()
    try:
        _org_policy(db, org["org_id"], notify_email="ops@example.org")
        _set_pref(db, "quiet@example.com", "notify_breach_alerts", False)
    finally:
        db.close()
    sent = _seed_breach(client, org, monkeypatch)
    to = {kw["to"] for kw in sent}
    assert "quiet@example.com" not in to
    assert org["admin_email"] in to


def test_breach_alert_honors_org_level_off_switch(make_org, add_user, client, monkeypatch):
    """A tenant that set breach notifications to 'none' must not be overridden
    by the per-user default-ON pref."""
    org = make_org()
    add_user(org["org_id"], "member@example.com", "Passw0rd123", role="member")
    db = SessionLocal()
    try:
        _org_policy_off(db, org["org_id"])          # notify_on_breach = "none"
    finally:
        db.close()
    assert _seed_breach(client, org, monkeypatch) == []


def test_breach_alert_honors_org_level_digest_mode(make_org, client, monkeypatch):
    """'digest' means batched, not immediate — no per-seat immediate mail."""
    org = make_org()
    db = SessionLocal()
    try:
        p = db.get(OrgPolicy, uuid.UUID(str(org["org_id"])))
        if p is None:
            p = OrgPolicy(org_id=uuid.UUID(str(org["org_id"])))
            db.add(p)
        p.notify_on_breach = "digest"
        db.commit()
    finally:
        db.close()
    assert _seed_breach(client, org, monkeypatch) == []


def test_breach_alert_not_duplicated_with_org_level_email(make_org, client, monkeypatch):
    """The org-level notifier and the per-user sender must not both email the
    same address for one breach — counted across BOTH phases."""
    from app.schemas import Verdict
    from app import user_notifications as un
    from app import worker as workermod

    org = make_org()
    db = SessionLocal()
    try:
        _org_policy(db, org["org_id"], notify_email=org["admin_email"])
    finally:
        db.close()

    give_judge_key(org["org_id"])
    assert client.post("/v1/logs/batch", json=[{
        "prompt_hash": _h("dup-p"), "response_hash": _h("dup-r"),
        "token_count": 90, "policy_tag": "hipaa"}],
        headers=org["auth"]).status_code == 202
    monkeypatch.setattr(
        workermod.gemini, "evaluate",
        lambda meta, policy_config=None, history=None, api_key=None:
        Verdict(policy_breach=True, reason="pii detected", risk_score=90))
    sent: list = []
    monkeypatch.setattr(un.email_mod, "send_email",
                        lambda **kw: sent.append(kw) or True)
    db = SessionLocal()
    try:
        for row in workermod._claim_batch(db, 100, 300):
            workermod._grade_one(db, row)
        un.drain_breach_alerts(db)      # keep BOTH phases in `sent`
    finally:
        db.close()
    admin_mails = [kw for kw in sent if kw["to"] == org["admin_email"]]
    assert len(admin_mails) == 1


# ── (b) weekly digest ───────────────────────────────────────────────────────
def _seed_events(org_id, day: date, *, count=1, tokens=100, breach=False, seq_base=None):
    """Insert real audit_logs rows on a given day — the digest reads the ledger
    itself, never the rolling usage_daily rollup (which understates old days)."""
    from sqlalchemy import text
    import json as _json
    db = SessionLocal()
    try:
        start = db.execute(text(
            "SELECT COALESCE(MAX(seq), 0) FROM audit_logs WHERE org_id = :oid"),
            {"oid": uuid.UUID(str(org_id))}).scalar() or 0
        base = seq_base if seq_base is not None else start
        verdict = _json.dumps({"policy_breach": bool(breach), "reason": "x",
                               "risk_score": 90 if breach else 0, "decision": "flag"})
        for i in range(count):
            db.execute(text(
                "INSERT INTO audit_logs (id, org_id, seq, prompt_hash, response_hash, "
                "token_count, policy_tag, chain_hash, prev_hash, created_at, "
                "grading_status, gemini_verdict) "
                "VALUES (:id, :oid, :seq, :ph, :rh, :tc, 'hipaa', :ch, '', "
                ":ts, 'graded', CAST(:v AS jsonb))"),
                {"id": uuid.uuid4(), "oid": uuid.UUID(str(org_id)), "seq": base + i + 1,
                 "ph": _h(f"p{day}{base+i}"), "rh": _h(f"r{day}{base+i}"),
                 "tc": tokens, "ch": _h(f"c{day}{base+i}"),
                 "ts": datetime(day.year, day.month, day.day, 12, 0,
                                tzinfo=timezone.utc),
                 "v": verdict})
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
    _seed_events(org["org_id"], monday - timedelta(days=3), count=7, tokens=100)
    _seed_events(org["org_id"], monday - timedelta(days=5), count=5, tokens=100, breach=True)
    sent: list = []
    monkeypatch.setattr(un.email_mod, "send_email", lambda **kw: sent.append(kw) or True)
    db = SessionLocal()
    try:
        assert un.send_weekly_digests(db, today=monday) == 1
        body = sent[0]["text"]
        assert "12" in body            # 7 + 5 real ledger rows, not fabricated
        assert "5" in body             # the 5 breach rows
        # a second pass in the same week must not re-send
        sent.clear()
        assert un.send_weekly_digests(db, today=monday) == 0
        assert sent == []
        markers = db.query(Notification).filter(Notification.kind == "digest").all()
        assert len(markers) == 1
        assert markers[0].target_id == f"{(monday - timedelta(days=7)).isocalendar()[0]}-W{(monday - timedelta(days=7)).isocalendar()[1]:02d}"
    finally:
        db.close()


def test_weekly_digest_excludes_events_outside_the_week(make_org, monkeypatch):
    """Only the completed ISO week counts — not this week, not the week before."""
    from app import user_notifications as un
    org = make_org()
    monday = _monday()
    _seed_events(org["org_id"], monday - timedelta(days=3), count=4)    # in week
    _seed_events(org["org_id"], monday, count=9)                        # this week
    _seed_events(org["org_id"], monday - timedelta(days=10), count=6)   # older week
    sent: list = []
    monkeypatch.setattr(un.email_mod, "send_email", lambda **kw: sent.append(kw) or True)
    db = SessionLocal()
    try:
        assert un.send_weekly_digests(db, today=monday) == 1
        assert "4" in sent[0]["text"]
        assert "Events logged: 4" in sent[0]["text"]
    finally:
        db.close()


def test_weekly_digest_only_on_monday(make_org, monkeypatch):
    from app import user_notifications as un
    org = make_org()
    _seed_events(org["org_id"], _monday() - timedelta(days=2), count=3)
    sent: list = []
    monkeypatch.setattr(un.email_mod, "send_email", lambda **kw: sent.append(kw) or True)
    db = SessionLocal()
    try:
        assert un.send_weekly_digests(db, today=_monday() + timedelta(days=1)) == 0
        assert sent == []
    finally:
        db.close()


def test_weekly_digest_skips_orgs_with_no_activity(make_org, monkeypatch):
    """A silent week is not news — never mail an all-zeros digest."""
    from app import user_notifications as un
    make_org()
    sent: list = []
    monkeypatch.setattr(un.email_mod, "send_email", lambda **kw: sent.append(kw) or True)
    db = SessionLocal()
    try:
        assert un.send_weekly_digests(db, today=_monday()) == 0
        assert sent == []
        assert db.query(Notification).filter(Notification.kind == "digest").count() == 0
    finally:
        db.close()


def test_weekly_digest_retries_when_every_email_failed(make_org, monkeypatch):
    """A provider outage must not silently burn the week — the marker is rolled
    back so a later pass sends it."""
    from app import user_notifications as un
    org = make_org()
    monday = _monday()
    _seed_events(org["org_id"], monday - timedelta(days=2), count=3)
    monkeypatch.setattr(un.email_mod, "send_email", lambda **kw: False)   # provider down
    db = SessionLocal()
    try:
        assert un.send_weekly_digests(db, today=monday) == 0
        assert db.query(Notification).filter(Notification.kind == "digest").count() == 0
    finally:
        db.close()
    sent: list = []
    monkeypatch.setattr(un.email_mod, "send_email", lambda **kw: sent.append(kw) or True)
    db = SessionLocal()
    try:
        assert un.send_weekly_digests(db, today=monday) == 1     # retried, not lost
        assert len(sent) == 1
    finally:
        db.close()


def test_weekly_digest_respects_opt_out(make_org, monkeypatch):
    from app import user_notifications as un
    org = make_org()
    monday = _monday()
    _seed_events(org["org_id"], monday - timedelta(days=2), count=3)
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


def test_rotation_reminder_dedupes_on_a_rolling_30_days(make_org, monkeypatch):
    """A calendar-month bucket would let a reminder on the 31st repeat on the
    1st — the window is rolling, so a fresh marker suppresses the next run."""
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
        assert un.send_key_rotation_reminders(db, today=date(2026, 7, 31)) == 1
        assert sent[0]["to"] == org["admin_email"]
        assert "rotation" in sent[0]["subject"].lower()
        sent.clear()
        # next calendar month, but only a day later → still suppressed
        assert un.send_key_rotation_reminders(db, today=date(2026, 8, 1)) == 0
        assert sent == []
        # …and once the marker ages past the window it reminds again
        marker = db.query(Notification).filter(
            Notification.kind == "key_rotation").one()
        marker.created_at = datetime.now(timezone.utc) - timedelta(days=31)
        db.commit()
        assert un.send_key_rotation_reminders(db, today=date(2026, 8, 31)) == 1
    finally:
        db.close()


def test_rotation_reminder_retries_when_every_email_failed(make_org, monkeypatch):
    from app import user_notifications as un
    org = make_org()
    _age_keys(org["org_id"], 120)
    db = SessionLocal()
    try:
        _set_pref(db, org["admin_email"], "notify_key_rotation_reminders", True)
    finally:
        db.close()
    monkeypatch.setattr(un.email_mod, "send_email", lambda **kw: False)
    db = SessionLocal()
    try:
        assert un.send_key_rotation_reminders(db, today=date(2026, 7, 27)) == 0
        assert db.query(Notification).filter(
            Notification.kind == "key_rotation").count() == 0
    finally:
        db.close()
    monkeypatch.setattr(un.email_mod, "send_email", lambda **kw: True)
    db = SessionLocal()
    try:
        assert un.send_key_rotation_reminders(db, today=date(2026, 7, 27)) == 1
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
