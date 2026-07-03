"""IP allow-list guard for the admin sub-app ("site 3").

Runs OUTERMOST on the admin app, so a request from a non-allowed network is
rejected with 403 BEFORE any auth or routing. An empty allow-list (the dev
default) allows everything; in prod, restrict admin.foxyaudit.com to office/VPN
ranges. This is defense-in-depth ON TOP of require_staff — never a replacement.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from ..config import get_settings


class AdminIPAllowlistMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        allow = get_settings().get_admin_ip_allowlist()
        if allow:
            # Trust the first X-Forwarded-For hop (set by our own proxy) if present.
            xff = request.headers.get("x-forwarded-for")
            client = (xff.split(",")[0].strip() if xff
                      else (request.client.host if request.client else ""))
            if client not in allow:
                return PlainTextResponse("Forbidden", status_code=403)
        return await call_next(request)
