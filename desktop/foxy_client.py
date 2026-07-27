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
import time
import urllib.error
import urllib.parse
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
        # The SERVER's detail first — it is the actionable half ("Access from
        # this IP is not allowed"); `reason` is only the generic HTTP phrase
        # ("Forbidden"). Workers hand str(exc) to the UI, so preferring reason
        # here silently threw away every message worth showing.
        if self.status:
            return f"HTTP {self.status}: {self.detail or self.reason}"
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

    persistent = True  # survives a restart — legacy QSettings copies may be scrubbed

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
    they simply don't survive a restart.  persistent=False tells callers that
    a legacy stored copy must NOT be destroyed after migrating into here."""

    persistent = False

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
        Set-Cookie must stick — a stale persisted cookie silently loses them.
        Serialization happens inside the lock so a slow thread can never
        overwrite a newer snapshot with a stale one."""
        if self._store is None:
            return
        with self._persist_lock:
            cookies = [{
                "version": c.version, "name": c.name, "value": c.value,
                "domain": c.domain, "path": c.path, "secure": c.secure,
                "expires": c.expires,
            } for c in self._jar]
            raw = json.dumps(cookies, sort_keys=True)
            if raw != self._last_saved:
                if self._store.set(_COOKIES_SECRET, raw):
                    self._last_saved = raw

    def _cookie_matches(self, c: Cookie, base_url: str | None = None) -> bool:
        """Does this jar cookie apply to the given (or current) base_url host,
        and is it unexpired?  The jar can hold cookies for another backend (the
        user can repoint base_url in Settings) — those must not count as a
        session here, and their CSRF value must never be echoed cross-host."""
        host = urllib.parse.urlsplit(
            self.base_url if base_url is None else base_url).hostname or ""
        dom = (c.domain or "").lstrip(".")
        if "." not in host and dom.endswith(".local"):
            dom = dom[: -len(".local")]  # cookiejar stores single-label hosts as host.local
        if not host or not dom or not (host == dom or host.endswith("." + dom)):
            return False
        if c.expires and c.expires < time.time():
            return False
        return True

    def has_session(self, base_url: str | None = None) -> bool:
        return any(c.name == "session" and self._cookie_matches(c, base_url)
                   for c in self._jar)

    def clear_session(self):
        """Drop all cookies (sign-out / terminal auth failure)."""
        self._jar.clear()
        if self._store is not None:
            self._store.delete(_COOKIES_SECRET)
        self._last_saved = None

    def session_value(self, base_url: str | None = None) -> str | None:
        """The session cookie currently in play for this host, if any."""
        for c in self._jar:
            if c.name == "session" and self._cookie_matches(c, base_url):
                return c.value
        return None

    def clear_session_if_current(self, token: str | None,
                                 base_url: str | None = None) -> bool:
        """Drop the jar only if `token` is STILL the live session cookie.

        A slow request can 401 long after the user has signed in again on
        another thread; clearing unconditionally would then throw away the
        fresh, working session. Returns True if the jar was cleared."""
        if token is None:
            return False
        # Check AND clear under ONE lock: releasing between them leaves a window
        # where a sign-in on another thread installs a fresh cookie that this
        # call then wipes — precisely the race the check exists to prevent.
        with self._persist_lock:
            current = None
            for c in self._jar:
                if c.name == "session" and self._cookie_matches(c, base_url):
                    current = c.value
                    break
            if current is not None and current != token:
                return False        # rotated under us — the newer session wins
            self._jar.clear()
            if self._store is not None:
                self._store.delete(_COOKIES_SECRET)
            self._last_saved = None
        return True

    def _csrf_token(self, base_url: str | None = None) -> str | None:
        for c in self._jar:
            if c.name == CSRF_COOKIE and self._cookie_matches(c, base_url):
                return c.value
        return None

    # ── the request ──
    def request(self, method: str, path: str, body=None,
                timeout: float | None = None, force_bearer: bool = False,
                base_url: str | None = None, bearer_key: str | None = None):
        """One backend call.  Returns parsed JSON (dict/list) for JSON
        responses, raw bytes otherwise.  Raises ApiError subclasses on non-2xx.

        force_bearer: some endpoints (GET /v1/health) are Bearer-only on the
        backend (require_org never consults the session) — pass True so the
        org key is sent even while a session cookie exists.
        base_url/bearer_key: a caller-supplied credential snapshot, so several
        worker threads sharing this object can never mix one call's URL with
        another's key (falls back to the instance attributes)."""
        method = method.upper()
        base_url = self.base_url if base_url is None else base_url
        bearer_key = self.bearer_key if bearer_key is None else bearer_key
        url = base_url.rstrip("/") + path
        for attempt in (0, 1):
            headers = {}
            data = None
            if body is not None:
                data = json.dumps(body).encode("utf-8")
                headers["Content-Type"] = "application/json"
            if bearer_key and (force_bearer or not self.has_session(base_url)):
                headers["Authorization"] = f"Bearer {bearer_key}"
            if method in _MUTATING and self.has_session(base_url):
                token = self._csrf_token(base_url)
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
                    if self._csrf_token(base_url) is None:
                        self._mint_csrf(timeout or self.timeout, base_url)
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

    def _mint_csrf(self, timeout: float, base_url: str | None = None):
        """Any request mints `foxy_csrf` when the cookie is absent — a cheap GET
        (status ignored) refreshes the jar before the retry."""
        base = self.base_url if base_url is None else base_url
        req = urllib.request.Request(base.rstrip("/") + "/v1/auth/me")
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
        self._cred_lock = threading.Lock()

    def _fresh_settings(self):
        """QSettings is reentrant across INSTANCES, not one instance across
        threads — request() runs on worker threads, so read the current values
        through a same-class instance made on the calling thread (FoxSettings
        is documented cheap to construct)."""
        try:
            return type(self.settings)()
        except Exception:
            return self.settings

    def _credentials(self) -> tuple[str, str]:
        """Read (backend_url, org_key) as ONE consistent pair.

        Concurrent workers share this client; without the lock two threads can
        interleave their reads/writes and a request can go out with one org's
        URL and another's key. The pair is returned (not stored on the shared
        FoxyHttp) so each in-flight request keeps its own snapshot."""
        if self.settings is None:
            return self.http.base_url, self.http.bearer_key
        with self._cred_lock:
            s = self._fresh_settings()
            return s.backend_url(), s.org_api_key()

    def request(self, method: str, path: str, body=None,
                timeout: float | None = None, force_bearer: bool = False):
        base_url, bearer_key = self._credentials()
        # Remember WHICH session this call rode on, so a late 401 can only
        # invalidate that one (see clear_session_if_current).
        sent_with = self.http.session_value(base_url)
        try:
            return self.http.request(method, path, body=body, timeout=timeout,
                                     force_bearer=force_bearer,
                                     base_url=base_url, bearer_key=bearer_key)
        except StepUpRequired as e:
            self.step_up_required.emit(e)
            raise
        except SessionExpired as e:
            # Drop the dead cookie: keeping it makes has_session() lie, which
            # both fakes a signed-in state and keeps suppressing the Bearer key
            # that would still work for the key-only endpoints. Guarded so a
            # slow 401 cannot wipe a session the user established meanwhile.
            self.http.clear_session_if_current(sent_with, base_url)
            self.session_expired.emit(str(e))
            raise
        except WorkspaceUnavailable as e:
            self.workspace_unavailable.emit(e.detail)
            raise

    def get(self, path: str, timeout: float | None = None):
        return self.request("GET", path, timeout=timeout)

    def post(self, path: str, body=None, timeout: float | None = None):
        return self.request("POST", path, body=body, timeout=timeout)

    # ── auth (D1) ──────────────────────────────────────────────────────────
    # Thin wrappers over the session endpoints. They return the backend's own
    # payloads and let ApiError propagate, so the UI can show the server's
    # wording (e.g. the 403 IP-allowlist detail) instead of inventing its own.
    def me(self):
        """Session probe. Raises SessionExpired/ApiError when not signed in."""
        return self.get("/v1/auth/me", timeout=8)

    def has_session(self) -> bool:
        """True when a session cookie for the CURRENT backend is in the jar.
        Offline and cheap — call me() to confirm the server still honours it."""
        base_url, _ = self._credentials()
        return self.http.has_session(base_url)

    def login(self, email: str, password: str, remember_me: bool = False):
        """POST /v1/auth/login. Returns {'mfa_required': True, 'email': …} when
        the account has email-OTP MFA on — finish with verify_mfa()."""
        return self.post("/v1/auth/login", {
            "email": email, "password": password, "remember_me": bool(remember_me)},
            timeout=15)

    def verify_mfa(self, email: str, code: str, remember_me: bool = False):
        return self.post("/v1/auth/mfa", {
            "email": email, "code": code, "remember_me": bool(remember_me)},
            timeout=15)

    def forgot_password(self, email: str):
        """Always 200 and silent — the backend never reveals whether the address
        exists, so the UI must show the same message either way."""
        return self.post("/v1/auth/forgot-password", {"email": email}, timeout=15)

    def logout(self):
        """End the server session AND drop the local jar, so a stale cookie can
        never outlive the sign-out."""
        try:
            return self.post("/v1/auth/logout", timeout=10)
        finally:
            self.http.clear_session()

    def sign_in_with_org_key(self, org_key: str | None = None):
        """Bridge an org API key to a real session without a password:
        POST /v1/auth/handoff (Bearer) mints a 120 s single-use token, then
        /v1/auth/handoff/redeem exchanges it for the session cookie. This is
        the only desktop route for Google/SSO-only accounts.

        Unlike the other helpers this talks to FoxyHttp directly and therefore
        raises ApiError WITHOUT emitting session_expired / step_up_required:
        the caller is signing in, so there is no session to expire and no
        gated action to re-auth — the login window shows the error inline."""
        base_url, stored = self._credentials()
        key = (org_key or stored or "").strip()
        if not key:
            raise ApiError(0, "", "no organization API key")
        minted = self.http.request("POST", "/v1/auth/handoff", timeout=10,
                                   force_bearer=True, base_url=base_url,
                                   bearer_key=key)
        token = minted.get("token") if isinstance(minted, dict) else None
        if not token:
            raise ApiError(0, "", "handoff did not return a token")
        return self.http.request("POST", "/v1/auth/handoff/redeem",
                                 body={"token": token}, timeout=10,
                                 base_url=base_url, bearer_key=key)

    def request_step_up(self):
        """Email a fresh 6-digit code for a step-up-gated action."""
        return self.post("/v1/auth/step-up/request", timeout=15)

    def confirm_step_up(self, code: str):
        """Confirm the code. The ~10-minute grant rides inside the re-issued
        session cookie, which the jar adopts and persists automatically."""
        return self.post("/v1/auth/step-up/confirm", {"code": (code or "").strip()},
                         timeout=15)

    def retry(self, pending: StepUpRequired):
        """Replay the request that triggered a step-up, after confirmation."""
        return self.request(pending.method, pending.path, body=pending.body)


# ── The one generic worker ──────────────────────────────────────────────────
class ApiWorker(QThread):
    """Runs a single client request off the GUI thread.
    succeeded carries the parsed JSON (dict or list); failed carries a short
    human-readable error string ("HTTP 404: Not Found", "timed out", …)."""

    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, client: FoxyClient, method: str, path: str,
                 body=None, timeout: float | None = None,
                 force_bearer: bool = False, parent=None):
        super().__init__(parent)
        self._client = client
        self._method = method
        self._path = path
        self._body = body
        self._timeout = timeout
        self._force_bearer = force_bearer

    def run(self):
        try:
            self.succeeded.emit(
                self._client.request(self._method, self._path,
                                     body=self._body, timeout=self._timeout,
                                     force_bearer=self._force_bearer))
        except Exception as e:
            self.failed.emit(str(e))


def spawn_worker(client: FoxyClient, method: str, path: str, *, body=None,
                 timeout: float | None = None, force_bearer: bool = False,
                 parent=None, on_ok=None, on_err=None,
                 track: set | None = None) -> ApiWorker:
    """Create, wire, track and start an ApiWorker.

    `track` is the owning window's live-worker set: the worker is added now and
    auto-removed (+ deleteLater) when it finishes, so replaced poll workers can
    no longer accumulate.  Shut the set down with shutdown_workers() on close."""
    w = ApiWorker(client, method, path, body=body, timeout=timeout,
                  force_bearer=force_bearer, parent=parent)
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
