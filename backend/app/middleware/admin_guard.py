"""IP allow-list guard for the admin sub-app ("site 3").

Runs OUTERMOST on the admin app, so a request from a non-allowed network is
rejected with 403 BEFORE any auth or routing. An empty allow-list (the dev
default) allows everything; in prod, restrict admin.foxyaudit.com to office/VPN
ranges. This is defense-in-depth ON TOP of require_staff — never a replacement.

Fail-open on an empty list is deliberate (staff login + MFA is always required,
and a fail-closed default could lock every operator out), but it must not be
SILENT: when the list is empty in prod we log a one-time WARNING at startup so an
unset allow-list is a visible choice, not an accident.
"""

from __future__ import annotations

import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from ..config import get_settings

log = logging.getLogger("foxy.admin_guard")


class AdminIPAllowlistMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        # One-time visibility: prod with no allow-list = admin IP-restriction OFF.
        if get_settings().is_prod and not get_settings().get_admin_ip_allowlist():
            log.warning("ADMIN_IP_ALLOWLIST is empty in prod — the admin app accepts "
                        "requests from ANY IP (staff login + MFA still required). Set "
                        "ADMIN_IP_ALLOWLIST to office/VPN ranges to restrict it.")

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
