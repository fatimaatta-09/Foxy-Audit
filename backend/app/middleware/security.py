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


# P6c · Paths whose body is legitimately larger than a JSON payload, with the cap
# that applies instead of the global one.
#
# max_request_bytes is 2 MB, which is generous for JSON and too small for a photo.
# Without this the avatar route's own 5 MB cap would be unreachable — every upload
# over 2 MB would be refused here first, with a generic "Request body too large"
# instead of the endpoint's "image must be 5 MB or smaller". The limit is not
# loosened globally: raising max_request_bytes would also raise it for
# /v1/logs/batch, which is the unbounded ingest path this middleware exists for.
#
# This is a ceiling, not the check. The route still enforces 5 MB itself with a
# BOUNDED READ, because Content-Length is a claim the client makes and this
# middleware can only ever act on the claim.
_BODY_LIMIT_OVERRIDES = {
    "/v1/account/avatar": 5 * 1024 * 1024,
}


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject requests whose declared Content-Length exceeds max_request_bytes with
    413, before the body is read — a cheap anti-DoS guard on the unbounded ingest
    path (`/v1/logs/batch` had no cap)."""

    async def dispatch(self, request: Request, call_next):
        cl = request.headers.get("content-length")
        if cl is not None:
            limit = _BODY_LIMIT_OVERRIDES.get(
                request.url.path, get_settings().max_request_bytes)
            try:
                if int(cl) > limit:
                    return PlainTextResponse("Request body too large", status_code=413)
            except ValueError:
                pass
        return await call_next(request)
