"""Google sign-in / sign-up (SSO) — POST /v1/auth/google.

Unified login-or-provision: a verified Google identity logs into an existing
account (linked by google_sub or email) or provisions a fresh free-tier org. No
email OTP (Google is already a strong identity). Token verification is stubbed
here (like the email mocks) so no real Google network call is needed.
"""

from __future__ import annotations

import types

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Organization, User


def _configure(monkeypatch, claims: dict):
    """Turn the feature on + stub Google token verification to return `claims`."""
    from app.routers import auth_google
    monkeypatch.setattr(auth_google, "get_settings",
                        lambda: types.SimpleNamespace(google_oauth_client_id="test-client-id"))
    monkeypatch.setattr(auth_google, "_verify_google_token",
                        lambda credential, client_id: claims)


def test_unconfigured_returns_503(client, monkeypatch):
    from app.routers import auth_google
    monkeypatch.setattr(auth_google, "get_settings",
                        lambda: types.SimpleNamespace(google_oauth_client_id=""))
    r = client.post("/v1/auth/google", json={"credential": "x"})
    assert r.status_code == 503


def test_config_endpoint_exposes_client_id(client, monkeypatch):
    from app.routers import auth_google
    monkeypatch.setattr(auth_google, "get_settings",
                        lambda: types.SimpleNamespace(google_oauth_client_id="abc.apps.googleusercontent.com"))
    r = client.get("/v1/auth/google/config")
    assert r.status_code == 200 and r.json()["client_id"] == "abc.apps.googleusercontent.com"


def test_new_user_provisions_org_and_logs_in(client, monkeypatch):
    _configure(monkeypatch, {"sub": "g-new-1", "email": "Founder@Startup.io",
                             "email_verified": True, "name": "Founder"})
    r = client.post("/v1/auth/google", json={"credential": "tok"})
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == "founder@startup.io" and body["role"] == "admin"
    assert client.cookies.get("session")                 # session established, no OTP
    db = SessionLocal()
    try:
        u = db.execute(select(User).where(User.email == "founder@startup.io")).scalar_one()
        assert u.google_sub == "g-new-1"
        org = db.get(Organization, u.org_id)
        assert org is not None and org.plan_tier == "free"
    finally:
        db.close()


def test_existing_user_links_and_logs_in(make_org, client, monkeypatch):
    org = make_org()
    _configure(monkeypatch, {"sub": "g-existing-1", "email": org["admin_email"],
                             "email_verified": True, "name": "Admin"})
    r = client.post("/v1/auth/google", json={"credential": "tok"})
    assert r.status_code == 200
    assert r.json()["org_id"] == org["org_id"]           # existing org, not a new one
    db = SessionLocal()
    try:
        rows = db.execute(select(User).where(User.email == org["admin_email"])).scalars().all()
        assert len(rows) == 1                            # no duplicate provisioned
        assert rows[0].google_sub == "g-existing-1"      # linked
    finally:
        db.close()


def test_unverified_email_rejected(client, monkeypatch):
    _configure(monkeypatch, {"sub": "g-x", "email": "spoof@x.com", "email_verified": False})
    r = client.post("/v1/auth/google", json={"credential": "tok"})
    assert r.status_code == 401


def test_invalid_token_rejected(client, monkeypatch):
    from app.routers import auth_google
    monkeypatch.setattr(auth_google, "get_settings",
                        lambda: types.SimpleNamespace(google_oauth_client_id="test-client-id"))

    def boom(credential, client_id):
        raise ValueError("bad signature")
    monkeypatch.setattr(auth_google, "_verify_google_token", boom)
    r = client.post("/v1/auth/google", json={"credential": "tok"})
    assert r.status_code == 401
