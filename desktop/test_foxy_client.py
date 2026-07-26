"""Pure-logic tests for foxy_client.FoxyHttp against a stub HTTP server.

The stub reproduces the backend's exact wire behaviour (cookie names, header
names, error detail strings — see backend/app/middleware/csrf.py and
app/auth.py) so the client's CSRF echo, single CSRF retry, step-up routing,
session-expiry routing and bearer mode are exercised without Qt or a real
backend.  No QApplication is created; only the stdlib core class is tested.
"""

from __future__ import annotations

import json
import threading
from http.cookiejar import Cookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from foxy_client import (
    ApiError,
    CSRF_DETAIL,
    FoxyHttp,
    MemorySecretStore,
    SESSION_EXPIRED_DETAIL,
    SessionExpired,
    StepUpRequired,
    WorkspaceUnavailable,
)

CSRF_VALUE = "csrf-token-test-1"
CSRF_EXEMPT = {"/v1/auth/login"}


class _StubHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep pytest output clean
        pass

    # ── helpers ──
    def _cookies(self) -> dict:
        out = {}
        for part in self.headers.get("Cookie", "").split(";"):
            if "=" in part:
                k, _, v = part.strip().partition("=")
                out[k] = v
        return out

    def _reply(self, status: int, payload=None, set_cookies=(),
               content_type="application/json", raw: bytes | None = None):
        cookies = list(set_cookies)
        # CSRF-middleware emulation: mint foxy_csrf on ANY response where the
        # request lacked the cookie (csrf.py:58-62).
        if "foxy_csrf" not in self._cookies():
            cookies.append(f"foxy_csrf={CSRF_VALUE}; Path=/")
        body = raw if raw is not None else (
            json.dumps(payload).encode() if payload is not None else b"")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        for c in cookies:
            self.send_header("Set-Cookie", c)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _csrf_ok(self) -> bool:
        cookies = self._cookies()
        if "session" not in cookies or self.path in CSRF_EXEMPT:
            return True  # enforced only when a session cookie rides along
        header = self.headers.get("X-CSRF-Token", "")
        return bool(header) and header == cookies.get("foxy_csrf", "")

    # ── routes ──
    def do_GET(self):
        self.server.calls.append(("GET", self.path,
                                  self.headers.get("Authorization", ""),
                                  self.headers.get("X-CSRF-Token", "")))
        if self.path == "/v1/bearer":
            self._reply(200, {"auth": self.headers.get("Authorization", "")})
        elif self.path == "/v1/expired":
            self._reply(401, {"detail": SESSION_EXPIRED_DETAIL})
        elif self.path == "/v1/unauth":
            self._reply(401, {"detail": "Not authenticated"})
        elif self.path == "/v1/suspended":
            self._reply(403, {"detail": "This workspace is suspended"})
        elif self.path == "/v1/binary":
            self._reply(200, content_type="application/octet-stream",
                        raw=b"\x00\x01binary-payload")
        elif self.path == "/v1/auth/me":
            self._reply(200, {"email": "t@example.com"})
        else:
            self._reply(404, {"detail": "Not Found"})

    def do_POST(self):
        # Drain the request body — replying without reading it makes Windows
        # RST the connection, which races the client's response read.
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        self.server.calls.append(("POST", self.path,
                                  self.headers.get("Authorization", ""),
                                  self.headers.get("X-CSRF-Token", "")))
        if self.path == "/v1/always-csrf-403":
            self._reply(403, {"detail": CSRF_DETAIL})
            return
        if not self._csrf_ok():
            self._reply(403, {"detail": CSRF_DETAIL})
            return
        if self.path == "/v1/auth/login":
            self._reply(200, {"email": "t@example.com", "role": "admin"},
                        set_cookies=["session=sess-plain; Path=/; Max-Age=2592000"])
        elif self.path == "/v1/echo":
            self._reply(200, {"ok": True})
        elif self.path == "/v1/gated":
            if "stepup" in self._cookies().get("session", ""):
                self._reply(200, {"ok": True})
            else:
                self._reply(403, {"detail": "step_up_required"})
        elif self.path == "/v1/auth/step-up/confirm":
            # The grant lives inside the re-issued session cookie (auth.py:271-273)
            self._reply(200, {"status": "step_up_granted"},
                        set_cookies=["session=sess-stepup; Path=/; Max-Age=2592000"])
        else:
            self._reply(404, {"detail": "Not Found"})


@pytest.fixture()
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _StubHandler)
    srv.calls = []
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def _client(server, **kw) -> FoxyHttp:
    return FoxyHttp(base_url=f"http://127.0.0.1:{server.server_address[1]}", **kw)


def _inject_session(http: FoxyHttp, value: str = "sess-plain",
                    name: str = "session", domain: str = "127.0.0.1",
                    expires=None):
    """Put ONLY a session cookie in the jar (no foxy_csrf) — the state a client
    restored from the keychain can be in."""
    http._jar.set_cookie(Cookie(
        version=0, name=name, value=value, port=None, port_specified=False,
        domain=domain, domain_specified=False, domain_initial_dot=False,
        path="/", path_specified=True, secure=False, expires=expires,
        discard=False, comment=None, comment_url=None, rest={}))


# ── bearer mode ─────────────────────────────────────────────────────────────
def test_bearer_header_sent_when_no_session(server):
    http = _client(server, bearer_key="foxy_key_abc")
    assert http.request("GET", "/v1/bearer")["auth"] == "Bearer foxy_key_abc"


def test_bearer_suppressed_once_session_exists(server):
    http = _client(server, bearer_key="foxy_key_abc")
    _inject_session(http)
    # resolve_org prefers the Authorization header — the client must not mix
    # credentials, so no bearer header goes out alongside the session cookie.
    assert http.request("GET", "/v1/bearer")["auth"] == ""


def test_force_bearer_wins_over_session(server):
    # GET /v1/health is Bearer-only on the backend — force_bearer must send
    # the key even while a session cookie exists.
    http = _client(server, bearer_key="foxy_key_abc")
    _inject_session(http)
    assert http.request("GET", "/v1/bearer",
                        force_bearer=True)["auth"] == "Bearer foxy_key_abc"


def test_foreign_domain_session_does_not_suppress_bearer(server):
    # A session cookie persisted for another backend (user repointed base_url)
    # is not a session HERE: the bearer key must still be sent, and the foreign
    # CSRF value must never be echoed to this host.
    http = _client(server, bearer_key="foxy_key_abc")
    _inject_session(http, domain="app.foxyaudit.tech")
    _inject_session(http, name="foxy_csrf", value="foreign-csrf",
                    domain="app.foxyaudit.tech")
    assert not http.has_session()
    assert http._csrf_token() is None
    assert http.request("GET", "/v1/bearer")["auth"] == "Bearer foxy_key_abc"


def test_expired_session_cookie_is_not_a_session(server):
    http = _client(server, bearer_key="foxy_key_abc")
    _inject_session(http, expires=1)  # epoch-second 1: long expired
    assert not http.has_session()
    assert http.request("GET", "/v1/bearer")["auth"] == "Bearer foxy_key_abc"


# ── CSRF double-submit ──────────────────────────────────────────────────────
def test_csrf_echoed_on_post_with_session(server):
    http = _client(server)
    http.request("POST", "/v1/auth/login",
                 body={"email": "t@example.com", "password": "x"})
    assert http.has_session()
    assert http.request("POST", "/v1/echo") == {"ok": True}
    method, path, _auth, csrf = server.calls[-1]
    assert (method, path) == ("POST", "/v1/echo")
    assert csrf == CSRF_VALUE  # cookie value echoed in X-CSRF-Token


def test_csrf_403_retried_once_with_minted_cookie(server):
    http = _client(server)
    _inject_session(http)  # session but NO csrf cookie → first POST must 403
    assert http.request("POST", "/v1/echo") == {"ok": True}
    posts = [c for c in server.calls if c[:2] == ("POST", "/v1/echo")]
    assert len(posts) == 2          # 403 then the single retry
    assert posts[0][3] == ""        # first attempt had no token to echo
    assert posts[1][3] == CSRF_VALUE  # retry echoes the cookie the 403 minted


def test_csrf_403_not_retried_more_than_once(server):
    http = _client(server)
    _inject_session(http)
    with pytest.raises(ApiError) as exc:
        http.request("POST", "/v1/always-csrf-403")
    assert exc.value.status == 403
    assert len([c for c in server.calls
                if c[:2] == ("POST", "/v1/always-csrf-403")]) == 2


def test_no_csrf_header_in_bearer_only_mode(server):
    http = _client(server, bearer_key="k")
    http.request("POST", "/v1/echo")
    assert server.calls[-1][3] == ""  # no session cookie → middleware exempt


# ── step-up ─────────────────────────────────────────────────────────────────
def test_step_up_raises_with_original_request(server):
    http = _client(server)
    http.request("POST", "/v1/auth/login", body={"email": "e", "password": "p"})
    with pytest.raises(StepUpRequired) as exc:
        http.request("POST", "/v1/gated", body={"x": 1})
    assert exc.value.method == "POST"
    assert exc.value.path == "/v1/gated"
    assert exc.value.body == {"x": 1}


def test_step_up_confirm_rotates_cookie_and_retry_succeeds(server):
    http = _client(server)
    http.request("POST", "/v1/auth/login", body={"email": "e", "password": "p"})
    with pytest.raises(StepUpRequired):
        http.request("POST", "/v1/gated")
    # confirm re-issues the session cookie with the grant inside; the jar must
    # adopt it so the single retry of the original request passes the gate
    http.request("POST", "/v1/auth/step-up/confirm", body={"code": "123456"})
    assert http.request("POST", "/v1/gated") == {"ok": True}


# ── auth routing ────────────────────────────────────────────────────────────
def test_session_expired_detail_raises_session_expired(server):
    with pytest.raises(SessionExpired):
        _client(server).request("GET", "/v1/expired")


def test_other_401_is_plain_api_error(server):
    with pytest.raises(ApiError) as exc:
        _client(server).request("GET", "/v1/unauth")
    assert not isinstance(exc.value, SessionExpired)
    assert exc.value.status == 401


def test_suspended_workspace_is_terminal(server):
    with pytest.raises(WorkspaceUnavailable):
        _client(server).request("GET", "/v1/suspended")


# ── responses & persistence ─────────────────────────────────────────────────
def test_non_json_response_returns_raw_bytes(server):
    assert _client(server).request("GET", "/v1/binary") == b"\x00\x01binary-payload"


def test_cookies_persist_across_clients_via_secret_store(server):
    store = MemorySecretStore()
    first = _client(server, secret_store=store)
    first.request("POST", "/v1/auth/login", body={"email": "e", "password": "p"})
    assert first.has_session()

    second = _client(server, secret_store=store)  # fresh client, same keychain
    assert second.has_session()
    assert second.request("POST", "/v1/echo") == {"ok": True}


def test_clear_session_wipes_jar_and_store(server):
    store = MemorySecretStore()
    http = _client(server, secret_store=store)
    http.request("POST", "/v1/auth/login", body={"email": "e", "password": "p"})
    http.clear_session()
    assert not http.has_session()
    assert store.get("session_cookies") is None
    assert not _client(server, secret_store=store).has_session()
