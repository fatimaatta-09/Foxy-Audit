"""Security-response headers + a request body-size cap (Phase 5 · 5B.6).

Both are registered on the customer AND admin sub-apps (main.py). SecurityHeaders
is added last so it runs OUTERMOST and its headers land on every response —
including the 413 the size cap returns and the 403 the admin IP-allowlist returns.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from ..config import get_settings


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Defensive response headers. CSP is deliberately minimal — the served
    dashboard/admin HTML use inline styles/scripts, so a strict no-unsafe-inline
    policy would break them; frame-ancestors/object-src still block clickjacking
    and plugin/object injection without touching the inline UI. HSTS only in prod
    (it must not be sent over the plain-HTTP dev origin)."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        h = response.headers
        h.setdefault("X-Frame-Options", "DENY")
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        h.setdefault(
            "Content-Security-Policy",
            "frame-ancestors 'none'; object-src 'none'; base-uri 'self'",
        )
        if get_settings().is_prod:
            h.setdefault(
                "Strict-Transport-Security", "max-age=63072000; includeSubDomains"
            )
        return response


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject requests whose declared Content-Length exceeds max_request_bytes with
    413, before the body is read — a cheap anti-DoS guard on the unbounded ingest
    path (`/v1/logs/batch` had no cap)."""

    async def dispatch(self, request: Request, call_next):
        cl = request.headers.get("content-length")
        if cl is not None:
            try:
                if int(cl) > get_settings().max_request_bytes:
                    return PlainTextResponse("Request body too large", status_code=413)
            except ValueError:
                pass
        return await call_next(request)
