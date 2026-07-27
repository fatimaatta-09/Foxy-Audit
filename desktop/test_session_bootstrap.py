"""Session-bootstrap wiring (D1.1).

The launch path and the sign-in path must behave identically once a session
exists — a RESTORED session is just as valid a credential for the breach
poller as a freshly minted one. This drove a real regression: returning
password-only users (no org API key) got a silent fox every launch because
only the sign-in path started the poller.

OmniAwareFox is far too heavy to instantiate here (sprites, tray, sensors,
pynput listeners), so the handlers are exercised against a stand-in that
carries the same attributes the real methods touch. The methods themselves are
the real ones, pulled off the class.
"""

from __future__ import annotations

import types

import pytest

import omni_fox


class _Dashboard:
    def __init__(self):
        self.signed_in_with = None
        self.signed_out = False

    def on_signed_in(self, me):
        self.signed_in_with = me

    def on_signed_out(self):
        self.signed_out = True


class _Fox:
    """The slice of OmniAwareFox the session handlers actually use."""

    def __init__(self, dashboard=None):
        self._session_ready = False
        self._me = {}
        self._login_win = None
        self._workers = set()
        self.dashboard = dashboard
        self.poller_starts = 0
        self.rechecked = 0
        self.states = []
        self.logins = []

    # real methods under test
    _on_session_ok = omni_fox.OmniAwareFox._on_session_ok
    _on_signed_in = omni_fox.OmniAwareFox._on_signed_in
    _on_session_probe_failed = omni_fox.OmniAwareFox._on_session_probe_failed

    # collaborators, stubbed
    def _ensure_breach_poller(self):
        self.poller_starts += 1

    def _recheck_backend(self):
        self.rechecked += 1

    def _set_state(self, name, row, dur):
        self.states.append(name)

    def show_login(self, *, reason="", ok=False):
        self.logins.append(reason)


ME = {"email": "auditor@example.com", "role": "admin", "org_id": "org-1"}


def test_restored_session_starts_the_breach_poller():
    """The launch path: /v1/auth/me confirmed a stored cookie."""
    fox = _Fox()
    fox._on_session_ok(ME)
    assert fox.poller_starts == 1, "a returning user must get breach polling"
    assert fox._session_ready is True
    assert fox._me == ME


def test_fresh_sign_in_starts_the_breach_poller():
    """The sign-in path — the sibling that already worked; kept so the two
    cannot drift apart again."""
    fox = _Fox()
    fox._on_signed_in(ME)
    assert fox.poller_starts == 1
    assert fox.rechecked == 1
    assert fox._session_ready is True


def test_both_session_paths_agree():
    restored, fresh = _Fox(), _Fox()
    restored._on_session_ok(ME)
    fresh._on_signed_in(ME)
    assert restored.poller_starts == fresh.poller_starts
    assert restored._session_ready == fresh._session_ready
    assert restored._me == fresh._me


def test_restored_session_updates_an_open_console():
    dash = _Dashboard()
    fox = _Fox(dashboard=dash)
    fox._on_session_ok(ME)
    assert dash.signed_in_with == ME


def test_unreachable_backend_is_not_a_sign_out():
    """An outage must not drag the user to a login screen they cannot use."""
    fox = _Fox()
    fox._on_session_probe_failed("timed out")
    assert fox.logins == []
    assert fox.poller_starts == 0


def test_rejected_session_routes_to_login():
    fox = _Fox()
    fox._on_session_probe_failed("HTTP 401: Session expired or revoked")
    assert len(fox.logins) == 1
    assert "expired" in fox.logins[0].lower()


def test_403_shows_the_servers_own_wording():
    """Suspended workspace / IP allow-list — not "session expired"."""
    fox = _Fox()
    fox._on_session_probe_failed("HTTP 403: Access from this IP is not allowed")
    assert fox.logins == ["Access from this IP is not allowed"]
