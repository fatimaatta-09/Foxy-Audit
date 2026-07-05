"""In/out traffic capture — one traffic_events row per request, OFF the hot path.

Registered on each site sub-app (site='app' on the customer API, site='admin' on
the admin API), INSIDE its SessionMiddleware so the row can be attributed to the
logged-in org/user/staff. The actual INSERT is handed to a small threadpool and
never awaited, so the request pays only the in-process hashing cost; a write error
is swallowed and counted, never raised into the response.

Privacy: IP and User-Agent are HMAC-hashed with the server pepper (never stored
raw); path/referrer carry no query string; no bodies/headers/secrets are stored.
Mirrors the "durable work happens off the request" philosophy of app/worker.py.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor

from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from ..auth import hash_key
from ..config import get_settings
from ..db import SessionLocal

log = logging.getLogger("foxy.traffic")

# Small pool: a request never waits on the DB write. Bounded worker count keeps
# the write concurrency modest; the queue absorbs bursts.
_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="foxy-traffic")
_INFLIGHT: set[Future] = set()
_LOCK = threading.Lock()
_dropped = 0

# Never log these (recorded elsewhere or pure noise): the marketing beacon writes
# its own 'marketing' row; readiness probes and preflights are not user traffic.
_SKIP_PATHS = {"/health/ready", "/", "/v1/track"}

_INSERT_SQL = text(
    """
    INSERT INTO traffic_events
        (site, path, method, status_code, latency_ms,
         org_id, user_id, staff_id, ip_hash, ua_hash, referrer, created_at)
    VALUES
        (:site, :path, :method, :status_code, :latency_ms,
         :org_id, :user_id, :staff_id, :ip_hash, :ua_hash, :referrer, now())
    """
)


def _hash(value: str | None) -> str | None:
    return hash_key(value) if value else None


def _write_event(params: dict) -> None:
    """Insert one row in its own short-lived session. Swallows all errors —
    telemetry must never surface as a request failure or crash a pool thread."""
    db = SessionLocal()
    try:
        db.execute(_INSERT_SQL, params)
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        log.debug("traffic write dropped: %s", exc)
    finally:
        db.close()


def _submit(params: dict) -> None:
    global _dropped
    try:
        fut = _POOL.submit(_write_event, params)
    except RuntimeError:            # pool shutting down
        _dropped += 1
        return
    with _LOCK:
        _INFLIGHT.add(fut)
    fut.add_done_callback(lambda f: _INFLIGHT.discard(f))


def flush(timeout: float = 2.0) -> None:
    """Block until in-flight writes complete. For tests/graceful shutdown only."""
    with _LOCK:
        futs = list(_INFLIGHT)
    for f in futs:
        try:
            f.result(timeout=timeout)
        except Exception:           # noqa: BLE001
            pass


class TrafficMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, site: str):
        super().__init__(app)
        self.site = site

    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS":
            return await call_next(request)   # skip CORS preflights
        t0 = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            self._record(request, 500, t0)    # capture the failure, then re-raise
            raise
        self._record(request, response.status_code, t0)
        return response

    def _record(self, request: Request, status_code: int, t0: float) -> None:
        """Build + enqueue the event. Wrapped so nothing here can break the
        response — a telemetry bug must never take down a real request."""
        try:
            if not get_settings().traffic_tracking_enabled:
                return
            full_path = (request.scope.get("root_path", "") or "") + request.url.path
            if full_path in _SKIP_PATHS:
                return
            sess = request.scope.get("session") or {}
            referrer = request.headers.get("referer")
            if referrer:
                referrer = referrer.split("?", 1)[0][:512]
            params = {
                "site": self.site,
                "path": full_path[:512],
                "method": request.method[:8],
                "status_code": int(status_code),
                "latency_ms": int((time.perf_counter() - t0) * 1000),
                "org_id": sess.get("org_id"),
                "user_id": sess.get("user_id"),
                "staff_id": sess.get("staff_user_id"),
                "ip_hash": _hash(request.client.host if request.client else None),
                "ua_hash": _hash(request.headers.get("user-agent")),
                "referrer": referrer,
            }
            _submit(params)
        except Exception as exc:  # noqa: BLE001
            log.debug("traffic capture skipped: %s", exc)
