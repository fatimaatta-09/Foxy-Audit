"""Per-org IP allow-list matching (Phase 5 · 5K).

Restricts a workspace's DASHBOARD access (session auth) to configured IPs/CIDRs.
NOT applied to the SDK Bearer key — the SDK runs from arbitrary app IPs. An empty
allow-list means no restriction. Supports exact IPs and CIDR ranges (v4/v6).
"""

from __future__ import annotations

import ipaddress

from starlette.requests import Request


def client_ip(request: Request) -> str:
    """The caller's IP — the first X-Forwarded-For hop (set by our own proxy) if
    present, else the socket peer."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else ""


def parse_allowlist(raw: str | None) -> list[str]:
    return [e.strip() for e in (raw or "").split(",") if e.strip()]


def ip_allowed(ip_str: str, allowlist: list[str]) -> bool:
    """True if ip_str matches any exact IP or CIDR in allowlist (empty → allow)."""
    if not allowlist:
        return True
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    for entry in allowlist:
        try:
            if "/" in entry:
                if ip in ipaddress.ip_network(entry, strict=False):
                    return True
            elif ip == ipaddress.ip_address(entry):
                return True
        except ValueError:
            continue
    return False
