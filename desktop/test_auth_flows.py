"""Pure-logic tests for the D1 auth flows (no Qt widgets, no real backend).

The stub reproduces the live wire contract of backend/app/routers/auth_human.py:
login (+ the mfa_required branch), /v1/auth/mfa, forgot-password's deliberately
silent 200, logout, the handoff mint → redeem bridge, and the step-up
request/confirm pair whose grant rides inside a re-issued session cookie.

These cover FoxyClient's auth surface — the part that must be exactly right.
The widgets themselves are exercised by the offscreen render smoke.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from foxy_client import (
    ApiError, FoxyClient, FoxyHttp, MemorySecretStore, SessionExpired,
    StepUpRequired,
)

GOOD_EMAIL, GOOD_PW = "auditor@example.com", "Passw0rd123"
MFA_EMAIL = "mfa@example.com"
OTP = "424242"
ORG_KEY = "test_key_not_a_real_secret"
HANDOFF = "handoff-token-xyz"


class _AuthStub(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _cookies(self) -> dict:
        out = {}
        for part in self.headers.get("Cookie", "").split(";"):
            if "=" in part:
                k, _, v = part.strip().partition("=")
                out[k] = v
        return out

    def _reply(self, status: int, payload=None, set_cookies=()):
        body = json.dumps(payload if payload is not None else {}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        for c in list(set_cookies):
            self.send_header("Set-Cookie", c)
        if "foxy_csrf" not in self._cookies():
            self.send_header("Set-Cookie", "foxy_csrf=csrf-1; Path=/")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json_body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode())
        except Exception:
            return {}

    def do_GET(self):
        self.server.calls.append(("GET", self.path))
        if self.path == "/v1/expired-session":
            self._reply(401, {"detail": "Session expired or revoked"})
        elif self.path == "/v1/auth/me":
            if "session" in self._cookies():
                self._reply(200, {"email": GOOD_EMAIL, "role": "admin",
                                  "org_id": "org-1", "mfa_enabled": False,
                                  "full_name": None, "preferences": {}})
            else:
                self._reply(401, {"detail": "Session expired or revoked"})
        else:
            self._reply(404, {"detail": "Not Found"})

    def do_POST(self):
        body = self._json_body()
        self.server.calls.append(("POST", self.path))
        p = self.path

        if p == "/v1/auth/login":
            if body.get("email") == MFA_EMAIL and body.get("password") == GOOD_PW:
                self._reply(200, {"mfa_required": True, "email": MFA_EMAIL})
            elif body.get("email") == GOOD_EMAIL and body.get("password") == GOOD_PW:
                self.server.remember = bool(body.get("remember_me"))
                self._reply(200, {"email": GOOD_EMAIL, "role": "admin",
                                  "org_id": "org-1"},
                            set_cookies=["session=sess-1; Path=/"])
            elif body.get("email") == "blocked@example.com":
                self._reply(403, {"detail": "Access from this IP is not allowed"})
            else:
                self._reply(401, {"detail": "Invalid email or password"})

        elif p == "/v1/auth/mfa":
            if body.get("email") == MFA_EMAIL and body.get("code") == OTP:
                self.server.remember = bool(body.get("remember_me"))
                self._reply(200, {"email": MFA_EMAIL, "role": "member",
                                  "org_id": "org-1"},
                            set_cookies=["session=sess-mfa; Path=/"])
            else:
                self._reply(401, {"detail": "Invalid or expired code"})

        elif p == "/v1/auth/forgot-password":
            self._reply(200, {"status": "ok"})          # always, by design

        elif p == "/v1/auth/logout":
            self._reply(200, {"status": "logged_out"},
                        set_cookies=["session=; Path=/; Max-Age=0"])

        elif p == "/v1/auth/handoff":
            if self.headers.get("Authorization") == f"Bearer {ORG_KEY}":
                self._reply(200, {"token": HANDOFF, "expires_in": 120})
            else:
                self._reply(401, {"detail": "Invalid API key"})

        elif p == "/v1/auth/handoff/redeem":
            if body.get("token") == HANDOFF:
                self._reply(200, {"email": GOOD_EMAIL, "role": "admin",
                                  "org_id": "org-1"},
                            set_cookies=["session=sess-handoff; Path=/"])
            else:
                self._reply(401, {"detail": "Invalid or expired handoff"})

        elif p == "/v1/auth/step-up/request":
            if "session" not in self._cookies():
                self._reply(401, {"detail": "Session expired or revoked"})
            else:
                self._reply(200, {"status": "code_sent", "email": GOOD_EMAIL})

        elif p == "/v1/auth/step-up/confirm":
            if body.get("code") == OTP:
                # The grant lives INSIDE the re-issued session cookie.
                self._reply(200, {"status": "step_up_granted"},
                            set_cookies=["session=sess-1-stepup; Path=/"])
            else:
                self._reply(400, {"detail": "invalid or expired code"})

        elif p == "/v1/keys/gated":                     # a step-up-gated action
            if "stepup" in self._cookies().get("session", ""):
                self._reply(200, {"ok": True})
            else:
                self._reply(403, {"detail": "step_up_required"})
        else:
            self._reply(404, {"detail": "Not Found"})


@pytest.fixture()
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _AuthStub)
    srv.calls = []
    srv.remember = None
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


class _Settings:
    """Minimal stand-in for FoxSettings (same two getters the client uses)."""

    def __init__(self, base_url: str, key: str = ""):
        self._url, self._key = base_url, key

    def backend_url(self):
        return self._url

    def org_api_key(self):
        return self._key


def _client(server, key: str = "") -> FoxyClient:
    base = f"http://127.0.0.1:{server.server_address[1]}"
    return FoxyClient(settings=_Settings(base, key),
                      http=FoxyHttp(base_url=base, secret_store=MemorySecretStore()))


# ── password sign-in ────────────────────────────────────────────────────────
def test_login_success_establishes_session(server):
    c = _client(server)
    assert not c.has_session()
    out = c.login(GOOD_EMAIL, GOOD_PW, remember_me=True)
    assert out["email"] == GOOD_EMAIL and out["role"] == "admin"
    assert c.has_session()
    assert server.remember is True          # remember-me reaches the backend


def test_login_remember_me_is_passed_through_false(server):
    c = _client(server)
    c.login(GOOD_EMAIL, GOOD_PW, remember_me=False)
    assert server.remember is False


def test_login_bad_password_is_a_401_without_a_session(server):
    c = _client(server)
    with pytest.raises(ApiError) as e:
        c.login(GOOD_EMAIL, "wrong", remember_me=False)
    assert e.value.status == 401
    assert e.value.detail == "Invalid email or password"
    assert not c.has_session()


def test_403_detail_is_preserved_for_the_ui(server):
    """The IP-allowlist / disabled / suspended wording must survive all the way
    to the screen. Workers hand str(exc) to the UI, so the SERVER's detail —
    not the generic HTTP reason phrase — has to be what str() renders."""
    c = _client(server)
    with pytest.raises(ApiError) as e:
        c.login("blocked@example.com", GOOD_PW)
    assert e.value.status == 403
    assert e.value.detail == "Access from this IP is not allowed"
    assert str(e.value) == "HTTP 403: Access from this IP is not allowed"
    assert "Forbidden" not in str(e.value)


def test_rejected_session_is_dropped_from_the_jar(server):
    """A 401'd cookie must not linger: has_session() would keep reporting a
    session that no longer exists, and D0's bearer suppression would keep
    withholding the org key that still works."""
    c = _client(server, key="some-key")
    c.login(GOOD_EMAIL, GOOD_PW)
    assert c.has_session()
    c.http._jar.clear()                     # server-side revocation, locally unknown
    c.login(GOOD_EMAIL, GOOD_PW)            # sign in again to re-seed the cookie
    assert c.has_session()
    # Now make the server reject it the way a revoked session does.
    with pytest.raises(SessionExpired):
        c.request("GET", "/v1/expired-session")
    assert not c.has_session()


# ── MFA branch ──────────────────────────────────────────────────────────────
def test_login_returns_mfa_required_without_a_session(server):
    c = _client(server)
    out = c.login(MFA_EMAIL, GOOD_PW)
    assert out == {"mfa_required": True, "email": MFA_EMAIL}
    assert not c.has_session()              # no session until the code is verified


def test_mfa_verify_completes_the_sign_in(server):
    c = _client(server)
    c.login(MFA_EMAIL, GOOD_PW)
    out = c.verify_mfa(MFA_EMAIL, OTP, remember_me=True)
    assert out["email"] == MFA_EMAIL
    assert c.has_session()
    assert server.remember is True          # the choice carries through step 2


def test_mfa_wrong_code_keeps_the_user_out(server):
    c = _client(server)
    c.login(MFA_EMAIL, GOOD_PW)
    with pytest.raises(ApiError) as e:
        c.verify_mfa(MFA_EMAIL, "000000")
    assert e.value.detail == "Invalid or expired code"
    assert not c.has_session()


# ── forgot password ─────────────────────────────────────────────────────────
def test_forgot_password_is_silent_for_any_address(server):
    c = _client(server)
    assert c.forgot_password("nobody@example.com") == {"status": "ok"}
    assert c.forgot_password(GOOD_EMAIL) == {"status": "ok"}


# ── org API key handoff ─────────────────────────────────────────────────────
def test_org_key_handoff_bridges_to_a_session(server):
    c = _client(server)
    out = c.sign_in_with_org_key(ORG_KEY)
    assert out["email"] == GOOD_EMAIL
    assert c.has_session()
    paths = [p for m, p in server.calls if m == "POST"]
    assert paths[:2] == ["/v1/auth/handoff", "/v1/auth/handoff/redeem"]


def test_org_key_handoff_uses_the_stored_key_when_none_given(server):
    c = _client(server, key=ORG_KEY)
    assert c.sign_in_with_org_key()["email"] == GOOD_EMAIL


def test_org_key_handoff_rejects_a_bad_key(server):
    c = _client(server)
    with pytest.raises(ApiError) as e:
        c.sign_in_with_org_key("nope")
    assert e.value.status == 401
    assert not c.has_session()


def test_org_key_handoff_without_a_key_fails_before_any_call(server):
    c = _client(server)
    with pytest.raises(ApiError):
        c.sign_in_with_org_key("")
    assert server.calls == []


# ── step-up ─────────────────────────────────────────────────────────────────
def test_step_up_grant_lets_the_retry_through(server):
    c = _client(server)
    c.login(GOOD_EMAIL, GOOD_PW)
    with pytest.raises(StepUpRequired) as e:
        c.request("POST", "/v1/keys/gated", body={"x": 1})
    pending = e.value
    c.request_step_up()
    c.confirm_step_up(OTP)                  # rotates the session cookie
    assert c.retry(pending) == {"ok": True}


def test_step_up_wrong_code_is_a_400(server):
    c = _client(server)
    c.login(GOOD_EMAIL, GOOD_PW)
    with pytest.raises(ApiError) as e:
        c.confirm_step_up("111111")
    assert e.value.status == 400
    assert e.value.detail == "invalid or expired code"


def test_step_up_signal_carries_the_original_request(server):
    c = _client(server)
    c.login(GOOD_EMAIL, GOOD_PW)
    seen = []
    c.step_up_required.connect(seen.append)
    with pytest.raises(StepUpRequired):
        c.request("POST", "/v1/keys/gated", body={"key": "id-9"})
    assert len(seen) == 1
    assert (seen[0].method, seen[0].path, seen[0].body) == (
        "POST", "/v1/keys/gated", {"key": "id-9"})


# ── session probe + sign-out ────────────────────────────────────────────────
def test_me_probe_reports_identity_when_signed_in(server):
    c = _client(server)
    c.login(GOOD_EMAIL, GOOD_PW)
    me = c.me()
    assert me["email"] == GOOD_EMAIL and me["role"] == "admin"


def test_me_probe_raises_session_expired_when_signed_out(server):
    c = _client(server)
    seen = []
    c.session_expired.connect(seen.append)
    with pytest.raises(SessionExpired):
        c.me()
    assert len(seen) == 1                   # the UI gets routed to the login screen


def test_late_401_does_not_wipe_a_newer_session(server):
    """A slow request can 401 long after the user signed in again on another
    thread — clearing unconditionally would throw away the fresh session."""
    c = _client(server)
    c.login(GOOD_EMAIL, GOOD_PW)
    stale = c.http.session_value(c.http.base_url)
    # the user signs in again meanwhile: the jar now holds a different cookie
    c.http._jar.clear()
    c.login(MFA_EMAIL, GOOD_PW)
    c.verify_mfa(MFA_EMAIL, OTP)
    fresh = c.http.session_value(c.http.base_url)
    assert fresh and fresh != stale
    # the OLD request's 401 lands now
    assert c.http.clear_session_if_current(stale, c.http.base_url) is False
    assert c.has_session(), "the newer session must survive a stale 401"
    # a 401 for the CURRENT session does clear it
    assert c.http.clear_session_if_current(fresh, c.http.base_url) is True
    assert not c.has_session()


def test_logout_clears_the_local_jar(server):
    c = _client(server)
    c.login(GOOD_EMAIL, GOOD_PW)
    assert c.has_session()
    c.logout()
    assert not c.has_session()


def test_logout_clears_the_jar_even_when_the_server_is_unreachable(server):
    """A failed logout must not leave the desktop believing it is signed in."""
    c = _client(server)
    c.login(GOOD_EMAIL, GOOD_PW)
    c.http.base_url = "http://127.0.0.1:1"      # nothing listening
    c.settings._url = c.http.base_url
    with pytest.raises(ApiError):
        c.logout()
    c.http.base_url = f"http://127.0.0.1:{server.server_address[1]}"
    c.settings._url = c.http.base_url
    assert not c.has_session()
