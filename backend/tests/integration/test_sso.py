"""P3 · §H — enterprise SSO (OIDC): admin config, domain discovery, and the
callback (with the IdP HTTP round-trip mocked). SSO is inert until configured."""
from __future__ import annotations

import time
from urllib.parse import parse_qs, urlparse

from app import oidc
from app.db import SessionLocal
from app.models import SsoConnection, User


_CONN = {"email_domain": "acme.com", "issuer": "https://idp.acme.com",
         "client_id": "cid-123", "client_secret": "shh-secret", "active": True}


def _configure(login, org, **over):
    c = login(org["admin_email"], org["admin_password"])
    r = c.put("/v1/auth/sso/connection", json={**_CONN, **over})
    assert r.status_code == 200, r.text
    return c


# ── admin config ──────────────────────────────────────────────────────────────

def test_config_crud_masks_secret(make_org, login):
    org = make_org()
    c = _configure(login, org)
    got = c.get("/v1/auth/sso/connection").json()
    assert got["configured"] and got["issuer"] == "https://idp.acme.com"
    assert got["has_secret"] is True and "client_secret" not in got
    assert c.delete("/v1/auth/sso/connection").status_code == 200
    assert c.get("/v1/auth/sso/connection").json()["configured"] is False


def test_config_validation(make_org, login):
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    assert c.put("/v1/auth/sso/connection", json={**_CONN, "email_domain": "not a domain"}).status_code == 422
    assert c.put("/v1/auth/sso/connection", json={**_CONN, "issuer": "http://insecure"}).status_code == 422


def test_config_admin_only(make_org, add_user, login):
    org = make_org()
    add_user(org["org_id"], "m@corp.com", "memberpass1", role="member")
    c = login("m@corp.com", "memberpass1")
    assert c.put("/v1/auth/sso/connection", json=_CONN).status_code == 403


def test_domain_cannot_be_hijacked(make_org, login):
    a, b = make_org(), make_org()
    _configure(login, a)                                  # org A claims acme.com
    cb = login(b["admin_email"], b["admin_password"])
    assert cb.put("/v1/auth/sso/connection", json=_CONN).status_code == 409


# ── discovery ─────────────────────────────────────────────────────────────────

def test_discover_no_sso_for_unknown_domain(client):
    assert client.post("/v1/auth/sso/discover", json={"email": "x@nowhere.com"}).json()["sso"] is False


def test_discover_returns_authorize_url(make_org, login, client, monkeypatch):
    org = make_org()
    _configure(login, org)
    monkeypatch.setattr(oidc, "discover", lambda issuer: {
        "authorization_endpoint": "https://idp.acme.com/authorize",
        "token_endpoint": "https://idp.acme.com/token"})
    d = client.post("/v1/auth/sso/discover", json={"email": "alice@acme.com"}).json()
    assert d["sso"] is True
    q = parse_qs(urlparse(d["authorize_url"]).query)
    assert q["client_id"] == ["cid-123"] and q["response_type"] == ["code"] and q["state"]


# ── callback ──────────────────────────────────────────────────────────────────

def _drive_discover(client, monkeypatch, email="alice@acme.com"):
    monkeypatch.setattr(oidc, "discover", lambda issuer: {
        "authorization_endpoint": "https://idp.acme.com/authorize",
        "token_endpoint": "https://idp.acme.com/token"})
    d = client.post("/v1/auth/sso/discover", json={"email": email}).json()
    return parse_qs(urlparse(d["authorize_url"]).query)["state"][0]


def test_callback_provisions_and_logs_in(make_org, login, client, monkeypatch):
    org = make_org()
    _configure(login, org)
    state = _drive_discover(client, monkeypatch)
    monkeypatch.setattr(oidc, "exchange_code", lambda **kw: {"id_token": "fake"})
    monkeypatch.setattr(oidc, "decode_id_token", lambda t: {"email": "alice@acme.com"})
    monkeypatch.setattr(oidc, "valid_claims", lambda *a, **k: True)
    r = client.get(f"/v1/auth/sso/callback?code=abc&state={state}", follow_redirects=False)
    assert r.status_code == 303
    # the SSO user now exists in the SSO org as a member, and the session works
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.email == "alice@acme.com", User.org_id == org["org_id"]).one()
        assert u.role == "member"
    finally:
        db.close()
    assert client.get("/v1/auth/me").json()["email"] == "alice@acme.com"


def test_callback_rejects_bad_state(make_org, login, client, monkeypatch):
    org = make_org()
    _configure(login, org)
    _drive_discover(client, monkeypatch)
    assert client.get("/v1/auth/sso/callback?code=abc&state=WRONG",
                      follow_redirects=False).status_code == 400


def test_callback_rejects_domain_mismatch(make_org, login, client, monkeypatch):
    org = make_org()
    _configure(login, org)
    state = _drive_discover(client, monkeypatch)
    monkeypatch.setattr(oidc, "exchange_code", lambda **kw: {"id_token": "fake"})
    monkeypatch.setattr(oidc, "decode_id_token", lambda t: {"email": "eve@evil.com"})
    monkeypatch.setattr(oidc, "valid_claims", lambda *a, **k: True)
    assert client.get(f"/v1/auth/sso/callback?code=abc&state={state}",
                      follow_redirects=False).status_code == 401


# ── claim validation unit ─────────────────────────────────────────────────────

def test_valid_claims_checks_iss_aud_exp_nonce():
    base = {"iss": "https://idp.acme.com", "aud": "cid-123",
            "exp": int(time.time()) + 300, "nonce": "n1"}
    ok = dict(base)
    assert oidc.valid_claims(ok, issuer="https://idp.acme.com", client_id="cid-123", nonce="n1")
    assert not oidc.valid_claims({**base, "aud": "other"}, issuer="https://idp.acme.com", client_id="cid-123", nonce="n1")
    assert not oidc.valid_claims({**base, "nonce": "bad"}, issuer="https://idp.acme.com", client_id="cid-123", nonce="n1")
    assert not oidc.valid_claims({**base, "exp": int(time.time()) - 3600}, issuer="https://idp.acme.com", client_id="cid-123", nonce="n1")


def test_blank_secret_keeps_existing_or_422_when_new(make_org, login):
    org = make_org()
    c = _configure(login, org)
    r = c.put("/v1/auth/sso/connection", json={**_CONN, "client_secret": "", "active": False})
    assert r.status_code == 200 and r.json()["active"] is False and r.json()["has_secret"] is True
    org2 = make_org()
    c2 = login(org2["admin_email"], org2["admin_password"])
    assert c2.put("/v1/auth/sso/connection",
                  json={**_CONN, "email_domain": "brandnew.com", "client_secret": ""}).status_code == 422
