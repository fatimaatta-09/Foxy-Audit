"""Per-org dashboard IP allow-list (Phase 5 · 5K).

An org can restrict dashboard (session) access to specific IPs/CIDRs. Enforced on
the session auth paths, never on the SDK key. Setting a list that would exclude
your own IP is refused (self-lockout guard).
"""

from __future__ import annotations

from sqlalchemy import text

from app.db import SessionLocal
from app.ip_allow import ip_allowed


def _set_allowlist(org_id: str, raw: str) -> None:
    db = SessionLocal()
    try:
        db.execute(text("UPDATE organizations SET ip_allowlist = :v WHERE id = cast(:o as uuid)"),
                   {"v": raw, "o": org_id})
        db.commit()
    finally:
        db.close()


def test_ip_allowed_exact_and_cidr():
    assert ip_allowed("1.2.3.4", []) is True                    # empty → allow all
    assert ip_allowed("1.2.3.4", ["1.2.3.4"]) is True
    assert ip_allowed("1.2.3.5", ["1.2.3.4"]) is False
    assert ip_allowed("10.0.0.7", ["10.0.0.0/24"]) is True
    assert ip_allowed("10.0.1.7", ["10.0.0.0/24"]) is False
    assert ip_allowed("not-an-ip", ["1.2.3.4"]) is False


def test_dashboard_blocked_from_outside_ip(make_org, login):
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    _set_allowlist(org["org_id"], "9.9.9.9")
    assert c.get("/v1/auth/me", headers={"X-Forwarded-For": "1.2.3.4"}).status_code == 403
    assert c.get("/v1/auth/me", headers={"X-Forwarded-For": "9.9.9.9"}).status_code == 200


def test_empty_allowlist_allows_all(make_org, login):
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    assert c.get("/v1/auth/me", headers={"X-Forwarded-For": "203.0.113.9"}).status_code == 200


def test_set_allowlist_self_lockout_guard(make_org, login):
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    # a list that excludes the caller's own IP is refused
    assert c.post("/v1/account/ip-allowlist", json={"allowlist": "9.9.9.9"},
                  headers={"X-Forwarded-For": "1.2.3.4"}).status_code == 400
    # a list that includes the caller is accepted...
    assert c.post("/v1/account/ip-allowlist", json={"allowlist": "1.2.3.0/24"},
                  headers={"X-Forwarded-For": "1.2.3.4"}).status_code == 200
    # ...and now an outside IP is blocked
    assert c.get("/v1/auth/me", headers={"X-Forwarded-For": "9.9.9.9"}).status_code == 403
