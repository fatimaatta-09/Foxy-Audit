"""IP allow-list guard for the admin sub-app ("site 3").

Runs OUTERMOST on the admin app, so a request from a non-allowed network is
rejected with 403 BEFORE any auth or routing. Defense-in-depth ON TOP of
require_staff — never a replacement.

Posture (secure by default):
- In **prod**, an *empty* allow-list DENIES the admin app entirely (403). An unset
  restriction must never silently expose the staff console to the whole internet.
  Set ADMIN_IP_ALLOWLIST to your office/VPN IPs or CIDRs to open it to those
  ranges (or "0.0.0.0/0,::/0" to intentionally allow all).
- In **dev** (non-prod), an empty list allows everything, so local work is
  frictionless.

Matching is CIDR-aware and shares the helpers in ip_allow, so the first
X-Forwarded-For hop (set by our own proxy) is trusted when present.
"""

from __future__ import annotations

import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from .. import ip_allow
from ..config import get_settings

log = logging.getLogger("foxy.admin_guard")


def _is_allowed(is_prod: bool, allow: list[str], client_ip: str) -> bool:
    """Admin-app access decision. Empty allow-list → allow in dev, DENY in prod
    (secure by default). Non-empty → CIDR-aware membership check."""
    if not allow:
        return not is_prod
    return ip_allow.ip_allowed(client_ip, allow)


class AdminIPAllowlistMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        # One-time startup visibility into the resulting posture.
        if get_settings().is_prod and not get_settings().get_admin_ip_allowlist():
            log.warning(
                "ADMIN_IP_ALLOWLIST is empty in prod — the admin app is DENYING ALL "
                "requests (secure default). Set ADMIN_IP_ALLOWLIST to your office/VPN "
                "IPs/CIDRs to open it (or 0.0.0.0/0,::/0 to allow all)."
            )

    async def dispatch(self, request: Request, call_next):
        s = get_settings()
        if not _is_allowed(s.is_prod, s.get_admin_ip_allowlist(), ip_allow.client_ip(request)):
            return PlainTextResponse("Forbidden", status_code=403)
        return await call_next(request)
