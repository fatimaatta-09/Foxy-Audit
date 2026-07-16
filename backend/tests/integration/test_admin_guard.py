"""The admin IP allow-list fails OPEN on an empty list — deliberately.

An operator's admin WiFi has a dynamic IP that changes daily, so a fail-closed
empty list would lock every admin out; staff login + MFA is the real gate. These
tests pin that behaviour (empty -> allow, with a one-time prod startup warning)
and confirm a POPULATED list still denies other IPs. There is intentionally no
"fail-closed" mode — see docs/Foxy_Audit_Status_Latest.md.
"""
from __future__ import annotations

import logging

from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

import app.middleware.admin_guard as admin_guard


class _FakeSettings:
    """Minimal stand-in for Settings — only what the middleware reads."""

    def __init__(self, allow, is_prod=False):
        self._allow = list(allow)
        self.is_prod = is_prod

    def get_admin_ip_allowlist(self):
        return list(self._allow)


def _client(monkeypatch, allow, is_prod=False) -> TestClient:
    monkeypatch.setattr(admin_guard, "get_settings", lambda: _FakeSettings(allow, is_prod))

    async def ok(request):
        return PlainTextResponse("ok")

    app_ = Starlette(routes=[Route("/admin/ping", ok)])
    app_.add_middleware(admin_guard.AdminIPAllowlistMiddleware)
    return TestClient(app_)


def test_empty_allowlist_allows_all_and_warns_in_prod(monkeypatch, caplog):
    """Empty list in prod: request is allowed through, but a one-time WARNING fires."""
    with caplog.at_level(logging.WARNING, logger="foxy.admin_guard"):
        client = _client(monkeypatch, [], is_prod=True)
        resp = client.get("/admin/ping")
    assert resp.status_code == 200 and resp.text == "ok"
    assert any("empty in prod" in rec.message for rec in caplog.records)


def test_populated_allowlist_denies_other_ips(monkeypatch):
    """A populated list 403s a client whose forwarded IP isn't on it."""
    client = _client(monkeypatch, ["203.0.113.5"])
    resp = client.get("/admin/ping", headers={"x-forwarded-for": "198.51.100.9"})
    assert resp.status_code == 403


def test_populated_allowlist_admits_listed_ip(monkeypatch):
    """A populated list admits a client whose forwarded IP is on it."""
    client = _client(monkeypatch, ["203.0.113.5"])
    resp = client.get("/admin/ping", headers={"x-forwarded-for": "203.0.113.5"})
    assert resp.status_code == 200 and resp.text == "ok"
