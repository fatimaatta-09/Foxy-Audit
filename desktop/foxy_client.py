"""
Foxy Audit desktop — the ONE backend API client.

Every HTTP call the desktop app makes to the Foxy Audit backend goes through
this module.  It implements the wire contract of `backend/app` exactly
(app/auth.py, app/routers/auth_human.py, app/middleware/csrf.py):

- **Bearer-key mode** (org API key): header `Authorization: Bearer <key>`.
  Sent only when no session cookie exists — `resolve_org` prefers the header,
  so mixing both would silently authenticate as the key on org routes.
- **Session mode**: the signed `session` cookie (SameSite=Lax, Max-Age 30 d on
  the cookie; real expiry is the DB session row — 12 h, or 30 d remember-me).
  The step-up grant (`step_up_until`) and session rotation live INSIDE the
  signed cookie value, so the jar must always adopt the latest Set-Cookie —
  urllib's HTTPCookieProcessor does — and re-persist it.
- **CSRF (double-submit)**: on every unsafe method while a session cookie is
  present, echo cookie `foxy_csrf` as header `X-CSRF-Token`.  The middleware
  mints the cookie on any response that lacked it.  On
  403 `{"detail": "CSRF token missing or invalid"}` the request is retried
  exactly once with the freshly minted cookie.
- **Step-up**: 403 `{"detail": "step_up_required"}` raises StepUpRequired and
  `FoxyClient` emits `step_up_required` — the UI runs
  POST /v1/auth/step-up/request → /confirm {code} (both CSRF-checked), then
  retries the original request once with the re-issued session cookie.
- **401 `{"detail": "Session expired or revoked"}`** raises SessionExpired and
  emits `session_expired` — the UI drops to the login screen.
  403 "This workspace is suspended" / "has been deleted" raises
  WorkspaceUnavailable — a terminal error state, not a retry.

Secrets (session cookies, org API key, AI provider keys) live in the OS
keychain via `keyring` (Windows Credential Locker / macOS Keychain /
SecretService) — never in QSettings or JSON files.

`FoxyHttp` is the pure-stdlib core (no Qt) so the protocol logic is testable
against a stub HTTP server; `FoxyClient` adds the Qt signals; `ApiWorker` is
the one generic QThread that replaces the per-endpoint worker classes.
"""

from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request
from http.cookiejar import Cookie, CookieJar

from PyQt6.QtCore import QObject, QThread, pyqtSignal

# Exact wire strings — matched verbatim against backend responses.
CSRF_COOKIE = "foxy_csrf"
CSRF_HEADER = "X-CSRF-Token"
CSRF_DETAIL = "CSRF token missing or invalid"          # middleware/csrf.py
STEP_UP_DETAIL = "step_up_required"                    # app/auth.py require_step_up_user
SESSION_EXPIRED_DETAIL = "Session expired or revoked"  # app/auth.py require_user
WORKSPACE_403_DETAILS = (
    "This workspace is suspended",
    "This workspace has been deleted",
)

_MUTATING = {"POST", "PUT", "DELETE", "PATCH"}

KEYCHAIN_SERVICE = "FoxyAudit"
_COOKIES_SECRET = "session_cookies"


# ── Errors ──────────────────────────────────────────────────────────────────
class ApiError(Exception):
    """Any non-2xx backend response (or transport failure, status=0)."""

    def __init__(self, status: int, reason: str = "", detail: str = ""):
        self.status = status
        self.reason = reason
        self.detail = detail
        super().__init__(self._text())

    def _text(self) -> str:
        if self.status:
            return f"HTTP {self.status}: {self.reason or self.detail}"
        return self.detail or self.reason or "connection failed"

    def __str__(self) -> str:
        return self._text()


class SessionExpired(ApiError):
    """401 'Session expired or revoked' — the UI must drop to the login screen."""


class StepUpRequired(ApiError):
    """403 'step_up_required' — carries the original request so the UI can
    retry it once after the step-up code is confirmed."""

    def __init__(self, method: str, path: str, body=None):
        self.method = method
        self.path = path
        self.body = body
        super().__init__(403, "Forbidden", STEP_UP_DETAIL)


class WorkspaceUnavailable(ApiError):
    """403 suspended/deleted workspace — terminal, do not retry."""


# ── Secret storage (OS keychain) ────────────────────────────────────────────
class KeyringSecretStore:
    """Secrets in the OS keychain (Windows Credential Locker / macOS Keychain /
    SecretService) under one service name.  All failures degrade to None —
    callers treat a missing secret as 'not set'."""

    def __init__(self):
        import keyring  # deferred so importing this module never hard-requires it
        self._keyring = keyring

    def get(self, name: str) -> str | None:
        try:
            return self._keyring.get_password(KEYCHAIN_SERVICE, name)
        except Exception:
            return None

    def set(self, name: str, value: str) -> bool:
        try:
            self._keyring.set_password(KEYCHAIN_SERVICE, name, value)
            return True
        except Exception:
            return False

    def delete(self, name: str) -> None:
        try:
            self._keyring.delete_password(KEYCHAIN_SERVICE, name)
        except Exception:
            pass


class MemorySecretStore:
    """In-memory fallback (tests, or hosts without a keychain backend).
    Secrets are NEVER written to QSettings/JSON — if the keychain is missing
    they simply don't survive a restart."""

    def __init__(self):
        self._data: dict[str, str] = {}

    def get(self, name: str) -> str | None:
        return self._data.get(name)

    def set(self, name: str, value: str) -> bool:
        self._data[name] = value
        return True

    def delete(self, name: str) -> None:
        self._data.pop(name, None)


_default_store = None


def default_secret_store():
    """Process-wide secret store: the OS keychain, or an in-memory fallback."""
    global _default_store
    if _default_store is None:
        try:
            _default_store = KeyringSecretStore()
        except Exception as exc:
            print(f"[foxy] keyring unavailable ({exc}) — secrets held in memory only",
                  file=sys.stderr)
            _default_store = MemorySecretStore()
    return _default_store


# ── Pure-stdlib HTTP core (no Qt — unit-tested against a stub server) ───────
class FoxyHttp:
    def __init__(self, base_url: str = "", bearer_key: str = "",
                 secret_store=None, timeout: float = 10.0):
        self.base_url = base_url
        self.bearer_key = bearer_key
        self.timeout = timeout
        self._store = secret_store
        self._jar = CookieJar()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self._jar))
        self._persist_lock = threading.Lock()
        self._last_saved = None
        self._load_cookies()

    # ── cookie jar ↔ keychain ──
    def _load_cookies(self):
        if self._store is None:
            return
        raw = self._store.get(_COOKIES_SECRET)
        if not raw:
            return
        try:
            for d in json.loads(raw):
                self._jar.set_cookie(Cookie(
                    version=d.get("version", 0), name=d["name"], value=d["value"],
                    port=None, port_specified=False,
                    domain=d.get("domain", ""), domain_specified=bool(d.get("domain")),
                    domain_initial_dot=d.get("domain", "").startswith("."),
                    path=d.get("path", "/"), path_specified=True,
                    secure=d.get("secure", False), expires=d.get("expires"),
                    discard=False, comment=None, comment_url=None, rest={},
                ))
            self._last_saved = raw
        except Exception:
            pass  # corrupt blob — start with an empty jar

    def _persist_cookies(self):
        """Re-save the jar whenever it changed.  The step-up grant and session
        rotation live inside the `session` cookie value, so every response's
        Set-Cookie must stick — a stale persisted cookie silently loses them."""
        if self._store is None:
            return
        cookies = [{
            "version": c.version, "name": c.name, "value": c.value,
            "domain": c.domain, "path": c.path, "secure": c.secure,
            "expires": c.expires,
        } for c in self._jar]
        raw = json.dumps(cookies, sort_keys=True)
        with self._persist_lock:
            if raw != self._last_saved:
                if self._store.set(_COOKIES_SECRET, raw):
                    self._last_saved = raw

    def has_session(self) -> bool:
        return any(c.name == "session" for c in self._jar)

    def clear_session(self):
        """Drop all cookies (sign-out / terminal auth failure)."""
        self._jar.clear()
        if self._store is not None:
            self._store.delete(_COOKIES_SECRET)
        self._last_saved = None

    def _csrf_token(self) -> str | None:
        for c in self._jar:
            if c.name == CSRF_COOKIE:
                return c.value
        return None

    # ── the request ──
    def request(self, method: str, path: str, body=None, timeout: float | None = None):
        """One backend call.  Returns parsed JSON (dict/list) for JSON
        responses, raw bytes otherwise.  Raises ApiError subclasses on non-2xx."""
        method = method.upper()
        url = self.base_url.rstrip("/") + path
        for attempt in (0, 1):
            headers = {}
            data = None
            if body is not None:
                data = json.dumps(body).encode("utf-8")
                headers["Content-Type"] = "application/json"
            if self.bearer_key and not self.has_session():
                headers["Authorization"] = f"Bearer {self.bearer_key}"
            if method in _MUTATING and self.has_session():
                token = self._csrf_token()
                if token:
                    headers[CSRF_HEADER] = token
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            try:
                with self._opener.open(req, timeout=timeout or self.timeout) as resp:
                    raw = resp.read()
                    ctype = resp.headers.get("Content-Type", "")
                self._persist_cookies()
                if not raw:
                    return {}
                if "json" in ctype:
                    return json.loads(raw.decode("utf-8"))
                return raw
            except urllib.error.HTTPError as e:
                # The cookie processor already extracted Set-Cookie from the
                # error response (e.g. the CSRF middleware mints foxy_csrf on
                # the 403 itself) — keep those cookies.
                self._persist_cookies()
                detail = self._error_detail(e)
                if e.code == 401 and detail == SESSION_EXPIRED_DETAIL:
                    raise SessionExpired(e.code, e.reason, detail) from None
                if e.code == 403 and detail == STEP_UP_DETAIL:
                    raise StepUpRequired(method, path, body) from None
                if e.code == 403 and detail in WORKSPACE_403_DETAILS:
                    raise WorkspaceUnavailable(e.code, e.reason, detail) from None
                if e.code == 403 and detail == CSRF_DETAIL and attempt == 0:
                    if self._csrf_token() is None:
                        self._mint_csrf(timeout or self.timeout)
                    continue  # single retry with the (re)minted cookie
                raise ApiError(e.code, e.reason, detail) from None
            except urllib.error.URLError as e:
                raise ApiError(0, "", str(e.reason)) from None

    @staticmethod
    def _error_detail(e: urllib.error.HTTPError) -> str:
        try:
            payload = json.loads(e.read().decode("utf-8"))
            return payload.get("detail", "") if isinstance(payload, dict) else ""
        except Exception:
            return ""

    def _mint_csrf(self, timeout: float):
        """Any request mints `foxy_csrf` when the cookie is absent — a cheap GET
        (status ignored) refreshes the jar before the retry."""
        req = urllib.request.Request(self.base_url.rstrip("/") + "/v1/auth/me")
        try:
            with self._opener.open(req, timeout=timeout):
                pass
        except Exception:
            pass  # only the Set-Cookie side effect matters


# ── Qt client (signals for auth routing) ────────────────────────────────────
class FoxyClient(QObject):
    """The app-wide client.  Reads backend URL + org key from FoxSettings at
    request time (so a settings change applies to the very next call, without
    restarting pollers), and surfaces auth events as Qt signals:

    - step_up_required(StepUpRequired) → open the step-up dialog, then retry
    - session_expired(str)             → drop to the login screen
    - workspace_unavailable(str)       → terminal error state
    """

    step_up_required = pyqtSignal(object)
    session_expired = pyqtSignal(str)
    workspace_unavailable = pyqtSignal(str)

    def __init__(self, settings=None, http: FoxyHttp | None = None, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.http = http or FoxyHttp(secret_store=default_secret_store())

    def request(self, method: str, path: str, body=None, timeout: float | None = None):
        if self.settings is not None:
            self.http.base_url = self.settings.backend_url()
            self.http.bearer_key = self.settings.org_api_key()
        try:
            return self.http.request(method, path, body=body, timeout=timeout)
        except StepUpRequired as e:
            self.step_up_required.emit(e)
            raise
        except SessionExpired as e:
            self.session_expired.emit(str(e))
            raise
        except WorkspaceUnavailable as e:
            self.workspace_unavailable.emit(e.detail)
            raise

    def get(self, path: str, timeout: float | None = None):
        return self.request("GET", path, timeout=timeout)

    def post(self, path: str, body=None, timeout: float | None = None):
        return self.request("POST", path, body=body, timeout=timeout)


# ── The one generic worker ──────────────────────────────────────────────────
class ApiWorker(QThread):
    """Runs a single client request off the GUI thread.
    succeeded carries the parsed JSON (dict or list); failed carries a short
    human-readable error string ("HTTP 404: Not Found", "timed out", …)."""

    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, client: FoxyClient, method: str, path: str,
                 body=None, timeout: float | None = None, parent=None):
        super().__init__(parent)
        self._client = client
        self._method = method
        self._path = path
        self._body = body
        self._timeout = timeout

    def run(self):
        try:
            self.succeeded.emit(
                self._client.request(self._method, self._path,
                                     body=self._body, timeout=self._timeout))
        except Exception as e:
            self.failed.emit(str(e))


def spawn_worker(client: FoxyClient, method: str, path: str, *, body=None,
                 timeout: float | None = None, parent=None,
                 on_ok=None, on_err=None, track: set | None = None) -> ApiWorker:
    """Create, wire, track and start an ApiWorker.

    `track` is the owning window's live-worker set: the worker is added now and
    auto-removed (+ deleteLater) when it finishes, so replaced poll workers can
    no longer accumulate.  Shut the set down with shutdown_workers() on close."""
    w = ApiWorker(client, method, path, body=body, timeout=timeout, parent=parent)
    if on_ok is not None:
        w.succeeded.connect(on_ok)
    if on_err is not None:
        w.failed.connect(on_err)
    if track is not None:
        track.add(w)
        w.finished.connect(lambda: (track.discard(w), w.deleteLater()))
    else:
        w.finished.connect(w.deleteLater)
    w.start()
    return w


def shutdown_workers(workers, wait_ms: int = 1500):
    """Best-effort wait for in-flight one-shot workers at window close."""
    for w in list(workers):
        try:
            if w.isRunning():
                w.wait(wait_ms)
        except RuntimeError:
            pass  # C++ object already deleted
