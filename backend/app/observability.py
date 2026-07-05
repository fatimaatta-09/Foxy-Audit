"""Request correlation IDs + structured logging (Phase 5 · 5E).

Every request gets an id (echoed from an inbound ``X-Request-ID`` — e.g. set by
the proxy — or freshly generated) that is returned on the response and attached
to every log line emitted while handling it, so a single request is traceable
across the customer/admin apps and the logs are aggregation-ready.

``RequestIdMiddleware`` is pure-ASGI (registered at import time, so it runs in
tests). ``configure_logging`` installs the handler and is called from the app
lifespan / worker entrypoint — NOT at import — so it never disturbs pytest's own
log capture.
"""

from __future__ import annotations

import contextvars
import json
import logging
import uuid

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


class RequestIdMiddleware:
    """Pure-ASGI: set the per-request id contextvar and echo it on the response."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        inbound = dict(scope.get("headers") or {}).get(b"x-request-id")
        rid = inbound.decode("latin-1")[:64] if inbound else uuid.uuid4().hex
        token = request_id_var.set(rid)

        async def _send(message):
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                headers.append((b"x-request-id", rid.encode("latin-1")))
            await send(message)

        try:
            await self.app(scope, receive, _send)
        finally:
            request_id_var.reset(token)


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", "-"),
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(json_logs: bool) -> None:
    """Install a single root handler carrying the request id. JSON in prod (for
    aggregation), a readable line in dev. Call once at startup, never at import."""
    handler = logging.StreamHandler()
    handler.addFilter(_RequestIdFilter())
    if json_logs:
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
