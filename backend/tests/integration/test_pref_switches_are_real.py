"""Every preference switch must do something (P3 §6).

"Notifications currently have no settings, most buttons don't work / do nothing."
A dead switch is worse than an absent one: it teaches the user the product lies,
and it costs them the one thing a settings page is for — believing that what it
says is what happens.

So this file is a structural guard rather than a behaviour test. Behaviour is
covered per-toggle in test_user_notifications.py (opt-out suppresses, opt-in
delivers, for all three notification preferences). What was NOT covered is the
failure the owner actually hit: a switch that persists perfectly and changes
nothing. That cannot be caught by testing the switch — only by asking whether
anything reads it.

The audit that produced KNOWN_DEAD, run over the whole tree:

    hide_sensitive_metadata        REAL — masks metadata in the dashboard
    notify_breach_alerts           REAL — user_notifications.send_breach_alert
    notify_weekly_digest           REAL — user_notifications.send_weekly_digests
    notify_key_rotation_reminders  REAL — user_notifications.send_key_rotation_reminders
    notify_product_updates         DEAD — nothing sends product updates, anywhere
    notify_security_alerts         DEAD — no consumer; and after P3 §3 there
                                          cannot be one, because new-device
                                          alerts are deliberately not opt-out-able
"""

from __future__ import annotations

import os
import re

import pytest

from app.routers.account import _ALLOWED_PREFS

_BACKEND = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_REPO = os.path.dirname(_BACKEND)
_DASH = os.path.join(_REPO, "foxy-dashboard", "foxy-audit-premium.html")

# Switches known to store a value nobody reads. Both are removed — from the UI
# markup and from _ALLOWED_PREFS together — in the dashboard-UI phase of P3;
# removing the key while the toggle still renders would leave the switch even
# more broken than it is now (silently rejected instead of silently ignored).
# This set must only ever shrink. Adding to it is the bug this file exists to stop.
KNOWN_DEAD = {"notify_product_updates", "notify_security_alerts"}


def _strip_comment(line: str) -> str:
    """Drop a trailing Python comment so a key merely NAMED in a comment does not
    count as somebody reading it."""
    return line.split("#", 1)[0] if "#" in line else line


def _backend_consumers(key: str) -> list[str]:
    """Lines outside the declaration itself that actually read the key."""
    hits = []
    for root, _dirs, files in os.walk(os.path.join(_BACKEND, "app")):
        if "__pycache__" in root:
            continue
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(root, fn)
            with open(path, encoding="utf-8") as fh:
                for i, raw in enumerate(fh, 1):
                    line = _strip_comment(raw)
                    if key not in line:
                        continue
                    # The allow-list declaration is not a consumer.
                    if os.path.basename(path) == "account.py":
                        continue
                    hits.append(f"{fn}:{i}")
    return hits


def _dashboard_consumers(key: str) -> list[str]:
    """Lines in the SPA that read the key for something other than rendering its
    own switch. `savePref('key',...)` writes it; `$('x').checked=!!prefs.key`
    restores the switch. Neither is a consumer — a switch that only drives itself
    is exactly the dead switch."""
    hits = []
    with open(_DASH, encoding="utf-8") as fh:
        for i, line in enumerate(fh, 1):
            if key not in line:
                continue
            if "savePref(" in line or ".checked=" in line:
                continue
            hits.append(f"premium.html:{i}")
    return hits


@pytest.mark.parametrize("key", sorted(_ALLOWED_PREFS - KNOWN_DEAD))
def test_every_accepted_preference_is_read_by_something(key):
    consumers = _backend_consumers(key) + _dashboard_consumers(key)
    assert consumers, (
        f"'{key}' is accepted by /v1/account/preferences and stored, but nothing "
        f"reads it. Make it real or delete the switch — do not ship a control that "
        f"does nothing.")


@pytest.mark.parametrize("key", sorted(KNOWN_DEAD))
def test_known_dead_switches_are_still_dead(key):
    """If someone wires one of these up, this fails and they remove it from
    KNOWN_DEAD — so the ledger cannot silently go stale."""
    assert not (_backend_consumers(key) + _dashboard_consumers(key)), (
        f"'{key}' now has a consumer — take it out of KNOWN_DEAD.")


def test_the_dead_list_only_shrinks():
    """A tripwire for the next person: this set is a debt, not a category."""
    assert KNOWN_DEAD <= {"notify_product_updates", "notify_security_alerts"}, (
        "a new dead switch was added — §6 says make it real or delete it")
    assert KNOWN_DEAD <= _ALLOWED_PREFS, (
        "a key in KNOWN_DEAD is already gone from _ALLOWED_PREFS — drop it here too")


def test_hide_sensitive_metadata_actually_masks(make_org, login):
    """The one non-notification switch. Its effect is client-side, so assert the
    dashboard reads it into its masking path — and that the value round-trips on
    the two endpoints the SPA reads at boot."""
    with open(_DASH, encoding="utf-8") as fh:
        html = fh.read()
    assert re.search(r"masked\s*=.*prefs\.hide_sensitive_metadata", html), \
        "nothing in the dashboard masks on this preference any more"

    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    assert c.put("/v1/account/preferences",
                 json={"preferences": {"hide_sensitive_metadata": True}}).status_code == 200
    assert c.get("/v1/account/preferences").json()["preferences"]["hide_sensitive_metadata"] is True
    assert c.get("/v1/auth/me").json()["preferences"]["hide_sensitive_metadata"] is True


def test_security_alerts_cannot_be_opted_out_of(make_org, add_user, monkeypatch):
    """§3's new-device alert must ignore any stored preference. This is why
    notify_security_alerts is being deleted rather than wired up: the honest
    answer to 'can I turn this off?' is no."""
    import queue

    from fastapi.testclient import TestClient

    from app import user_notifications as un
    from app.db import SessionLocal
    from app.main import app
    from app.models import User
    from sqlalchemy import select

    org = make_org()
    ua_a, ua_b = "Mozilla/5.0 (Windows NT 10.0) Chrome/126.0", "Mozilla/5.0 (iPhone) Version/17.0"

    # Opt out of everything the user is able to opt out of.
    with SessionLocal() as db:
        u = db.execute(select(User).where(User.email == org["admin_email"])).scalar_one()
        u.preferences = {k: False for k in _ALLOWED_PREFS}
        db.commit()

    while True:
        try:
            un._DEVICE_QUEUE.get_nowait()
        except queue.Empty:
            break

    cl = TestClient(app)
    cl.post("/v1/auth/login", json={"email": org["admin_email"], "password": org["admin_password"]},
            headers={"user-agent": ua_a})
    cl.post("/v1/auth/login", json={"email": org["admin_email"], "password": org["admin_password"]},
            headers={"user-agent": ua_b})

    queued = []
    while True:
        try:
            queued.append(un._DEVICE_QUEUE.get_nowait())
        except queue.Empty:
            break
    assert len(queued) == 1, "a new-device alert was suppressed by a user preference"
