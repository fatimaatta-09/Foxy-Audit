"""New-device sign-in alerts for platform STAFF (admin console punch-list P0).

The staff mirror of test_new_device_alert.py. Same three rules — fire on a
genuinely new device, never on every login, never on the first-ever sign-in —
answered from ``staff_sessions`` rather than ``login_events``, because staff
logins write no LoginEvent and P0 adds no table.

The fourth rule is the one that matters most: the sign-in request must only ever
ENQUEUE. A staff console whose login waits on a mail provider is a console that
locks its own operators out when the provider has a bad day.
"""

from __future__ import annotations

import queue
import uuid

import pytest
from fastapi.testclient import TestClient

from app import user_notifications as un
from app.db import SessionLocal
from app.main import app
from app.models import StaffUser

CHROME = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
IPHONE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
          "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")


@pytest.fixture(autouse=True)
def _drain_queue():
    """Each test starts with an empty queue — it is module-global."""
    while True:
        try:
            un._STAFF_DEVICE_QUEUE.get_nowait()
        except queue.Empty:
            break
    yield


def _queued() -> list[dict]:
    out = []
    while True:
        try:
            out.append(un._STAFF_DEVICE_QUEUE.get_nowait())
        except queue.Empty:
            return out


def _login(email, pw, ua):
    return TestClient(app).post("/admin/v1/auth/login",
                                json={"email": email, "password": pw},
                                headers={"user-agent": ua})


def test_first_ever_staff_login_is_not_a_new_device(make_staff):
    """Nobody has signed in yet, so there is no "before" to be unfamiliar to."""
    s = make_staff(role="operator")
    assert _login(s["email"], s["password"], CHROME).status_code == 200
    assert _queued() == []


def test_same_device_twice_alerts_nobody(make_staff):
    s = make_staff(role="operator")
    _login(s["email"], s["password"], CHROME)
    _login(s["email"], s["password"], CHROME)
    _login(s["email"], s["password"], CHROME)
    assert _queued() == [], "alerting on every login trains people to ignore the alert"


def test_a_genuinely_new_device_queues_one_alert(make_staff):
    s = make_staff(role="operator")
    _login(s["email"], s["password"], CHROME)          # known device
    assert _queued() == []
    assert _login(s["email"], s["password"], IPHONE).status_code == 200
    items = _queued()
    assert len(items) == 1
    assert items[0]["email"] == s["email"]
    assert items[0]["staff_user_id"] == s["id"]
    assert items[0]["user_agent"] == IPHONE


def test_returning_to_a_known_device_is_silent(make_staff):
    s = make_staff(role="operator")
    _login(s["email"], s["password"], CHROME)
    _login(s["email"], s["password"], IPHONE)
    _queued()
    _login(s["email"], s["password"], CHROME)          # back to the old one
    assert _queued() == []


def test_failed_logins_do_not_establish_a_device(make_staff):
    """A wrong password must not teach the console to trust that browser."""
    s = make_staff(role="operator")
    _login(s["email"], s["password"], CHROME)
    for _ in range(3):
        assert _login(s["email"], "wrong-password", IPHONE).status_code == 401
    _queued()
    assert _login(s["email"], s["password"], IPHONE).status_code == 200
    assert len(_queued()) == 1, "the iPhone was never a successful device before now"


def test_a_broken_mailer_does_not_break_the_login(make_staff, monkeypatch):
    """THE POINT OF P0 §7. If send_email is reachable from a staff login, a hung
    or throwing provider takes the admin console's front door with it."""
    s = make_staff(role="operator")
    _login(s["email"], s["password"], CHROME)

    def _explode(**kw):
        raise RuntimeError("mail provider is down")

    monkeypatch.setattr(un.email_mod, "send_email", _explode)
    r = _login(s["email"], s["password"], IPHONE)
    assert r.status_code == 200 and r.json()["email"] == s["email"]
    assert len(_queued()) == 1


def test_the_drain_swallows_a_mailer_exception(make_staff, monkeypatch):
    """And it must not take the drain thread down either — the alert is lost, the
    loop keeps running."""
    s = make_staff(role="operator")
    _login(s["email"], s["password"], CHROME)
    _login(s["email"], s["password"], IPHONE)

    def _explode(**kw):
        raise RuntimeError("mail provider is down")

    monkeypatch.setattr(un.email_mod, "send_email", _explode)
    with SessionLocal() as db:
        assert un.drain_staff_device_alerts(db) == 0
    assert _queued() == [], "the failed item is consumed, not left to loop forever"


def test_the_drain_sends_it_and_the_body_is_content_blind(make_staff, monkeypatch):
    s = make_staff(role="operator")
    _login(s["email"], s["password"], CHROME)
    _login(s["email"], s["password"], IPHONE)

    sent = []
    monkeypatch.setattr(un.email_mod, "send_email",
                        lambda **kw: (sent.append(kw), True)[1])
    with SessionLocal() as db:
        assert un.drain_staff_device_alerts(db) == 1
    assert len(sent) == 1
    msg = sent[0]
    assert msg["to"] == s["email"]
    body = msg["html"] + (msg["text"] or "")
    assert "iPhone" in body                        # the device is described
    assert "not available" in body                 # location is honestly absent
    assert "Devices & sessions" in body or "Devices &amp; sessions" in body
    for secret in ("password_hash", "token_hash", "key_hash", "_key_enc"):
        assert secret not in body

    with SessionLocal() as db:
        assert un.drain_staff_device_alerts(db) == 0, "the queue must not re-send"


def test_the_ops_kill_switch_stops_the_enqueue(make_staff, monkeypatch):
    """Gate the ENQUEUE, not just the drain — otherwise the queue fills with
    alerts nobody reads and starts dropping them (the breach-queue lesson)."""
    s = make_staff(role="operator")
    _login(s["email"], s["password"], CHROME)
    monkeypatch.setattr(un.get_settings(), "user_notifications_enabled", False)
    _login(s["email"], s["password"], IPHONE)
    assert _queued() == []


def test_it_is_not_preference_gated(make_staff):
    """A staff member who muted every other notification still gets this one: it
    is the alert that says somebody else is using cross-tenant read access."""
    s = make_staff(role="operator")
    with SessionLocal() as db:
        row = db.get(StaffUser, uuid.UUID(s["id"]))
        row.preferences = {"notify_broadcasts": False, "notify_targeted": False,
                           "notify_system": False}
        db.commit()
    _login(s["email"], s["password"], CHROME)
    _login(s["email"], s["password"], IPHONE)
    assert len(_queued()) == 1
