"""New-device sign-in alerts (P3 §3).

The alert must fire on a genuinely new device, never on every login, and must be
sent from the notifications thread rather than inside the sign-in request — a
sign-in that waits on a mail provider is a sign-in that fails when the provider
does.
"""

from __future__ import annotations

import queue

import pytest
from fastapi.testclient import TestClient

from app import user_notifications as un
from app.main import app

CHROME = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
IPHONE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
          "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")


@pytest.fixture(autouse=True)
def _drain_queue():
    """Each test starts with an empty queue — it is module-global."""
    while True:
        try:
            un._DEVICE_QUEUE.get_nowait()
        except queue.Empty:
            break
    yield


def _queued() -> list[dict]:
    out = []
    while True:
        try:
            out.append(un._DEVICE_QUEUE.get_nowait())
        except queue.Empty:
            return out


def _login(email, pw, ua):
    return TestClient(app).post("/v1/auth/login", json={"email": email, "password": pw},
                                headers={"user-agent": ua})


def test_first_ever_login_is_not_a_new_device(make_org):
    """Telling somebody who just made an account that a stranger signed in is a lie."""
    org = make_org()
    assert _login(org["admin_email"], org["admin_password"], CHROME).status_code == 200
    assert _queued() == []


def test_same_device_twice_alerts_nobody(make_org):
    org = make_org()
    _login(org["admin_email"], org["admin_password"], CHROME)
    _login(org["admin_email"], org["admin_password"], CHROME)
    _login(org["admin_email"], org["admin_password"], CHROME)
    assert _queued() == [], "alerting on every login trains people to ignore the alert"


def test_a_genuinely_new_device_queues_one_alert(make_org):
    org = make_org()
    _login(org["admin_email"], org["admin_password"], CHROME)   # known device
    assert _queued() == []
    assert _login(org["admin_email"], org["admin_password"], IPHONE).status_code == 200
    items = _queued()
    assert len(items) == 1
    assert items[0]["email"] == org["admin_email"]
    assert items[0]["user_agent"] == IPHONE
    assert items[0]["session_id"], "the alert must name the session it is about"


def test_returning_to_a_known_device_is_silent(make_org):
    org = make_org()
    _login(org["admin_email"], org["admin_password"], CHROME)
    _login(org["admin_email"], org["admin_password"], IPHONE)
    _queued()
    _login(org["admin_email"], org["admin_password"], CHROME)   # back to the old one
    assert _queued() == []


def test_failed_logins_do_not_establish_a_device(make_org):
    """A wrong password must not teach the system to trust that device."""
    org = make_org()
    _login(org["admin_email"], org["admin_password"], CHROME)
    for _ in range(3):
        assert _login(org["admin_email"], "wrong-password", IPHONE).status_code == 401
    _queued()
    assert _login(org["admin_email"], org["admin_password"], IPHONE).status_code == 200
    assert len(_queued()) == 1, "the iPhone was never a successful device before now"


def test_login_does_not_wait_on_the_mail_provider(make_org, monkeypatch):
    """THE POINT OF §3. The request path must only enqueue. If send_email is
    reachable from a login, a hung provider hangs sign-in for everybody."""
    org = make_org()
    _login(org["admin_email"], org["admin_password"], CHROME)

    def _explode(**kw):
        raise AssertionError("send_email was called inside the login request path")

    monkeypatch.setattr(un.email_mod, "send_email", _explode)
    assert _login(org["admin_email"], org["admin_password"], IPHONE).status_code == 200
    assert len(_queued()) == 1


def test_the_drain_sends_it_and_the_body_is_content_blind(make_org, monkeypatch):
    org = make_org()
    _login(org["admin_email"], org["admin_password"], CHROME)
    _login(org["admin_email"], org["admin_password"], IPHONE)

    sent = []
    monkeypatch.setattr(un.email_mod, "send_email",
                        lambda **kw: (sent.append(kw), True)[1])
    from app.db import SessionLocal
    with SessionLocal() as db:
        assert un.drain_new_device_alerts(db) == 1
    assert len(sent) == 1
    msg = sent[0]
    assert msg["to"] == org["admin_email"]
    body = msg["html"] + (msg["text"] or "")
    assert "iPhone" in body                       # the device is described
    assert "not available" in body                # location is honestly absent
    for secret in ("password_hash", "token_hash", "key_hash", "_key_enc"):
        assert secret not in body

    with SessionLocal() as db:
        assert un.drain_new_device_alerts(db) == 0, "the queue must not re-send"


def test_the_ops_kill_switch_stops_the_enqueue(make_org, monkeypatch):
    """Gate the ENQUEUE, not just the drain — otherwise the queue fills with
    alerts nobody reads and starts dropping them (the breach-queue lesson)."""
    org = make_org()
    _login(org["admin_email"], org["admin_password"], CHROME)
    s = un.get_settings()
    monkeypatch.setattr(s, "user_notifications_enabled", False)
    _login(org["admin_email"], org["admin_password"], IPHONE)
    assert _queued() == []


@pytest.mark.parametrize("ua,want", [
    (CHROME, "Chrome on Windows"),
    (IPHONE, "Safari on iPhone"),
    ("", "an unrecognised device"),
    ("curl/8.4.0", "curl/8.4.0"),
])
def test_device_descriptions_never_invent(ua, want):
    assert un.describe_device(ua) == want
