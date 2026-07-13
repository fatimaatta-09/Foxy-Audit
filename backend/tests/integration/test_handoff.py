"""POST /v1/auth/handoff (mint via org API key) + /v1/auth/handoff/redeem (auto-login).

The desktop pet mints a single-use token with its org key, then opens the dashboard with
?handoff=<token>; redeeming establishes the session with no password. Covers happy path,
single-use, bad token, expiry, and mint-requires-auth.
"""
from __future__ import annotations

import datetime as dt
import hashlib

from sqlalchemy import select

from app.db import SessionLocal
from app.models import AuthHandoffToken


def _mint(client, org) -> str:
    r = client.post("/v1/auth/handoff", headers=org["auth"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["expires_in"] > 0
    return body["token"]


def test_handoff_mint_and_redeem_logs_in(make_org, client):
    org = make_org()
    token = _mint(client, org)
    # Redeem with NO Bearer/session — the token is the credential. Establishes a cookie.
    r = client.post("/v1/auth/handoff/redeem", json={"token": token})
    assert r.status_code == 200, r.text
    assert r.json()["org_id"] == org["org_id"]
    assert r.json()["role"] == "admin"
    # The session now works for a cookie-only call:
    me = client.get("/v1/auth/me")
    assert me.status_code == 200 and me.json()["org_id"] == org["org_id"]


def test_handoff_is_single_use(make_org, client):
    org = make_org()
    token = _mint(client, org)
    assert client.post("/v1/auth/handoff/redeem", json={"token": token}).status_code == 200
    # Second redeem of the same token is rejected (used_at set).
    assert client.post("/v1/auth/handoff/redeem", json={"token": token}).status_code == 401


def test_handoff_bad_token_rejected(make_org, client):
    make_org()
    assert client.post("/v1/auth/handoff/redeem",
                       json={"token": "not-a-real-token"}).status_code == 401


def test_handoff_expired_rejected(make_org, client):
    org = make_org()
    token = _mint(client, org)
    db = SessionLocal()
    try:
        row = db.execute(select(AuthHandoffToken).where(
            AuthHandoffToken.token_hash == hashlib.sha256(token.encode()).hexdigest()
        )).scalar_one()
        row.expires_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)
        db.commit()
    finally:
        db.close()
    assert client.post("/v1/auth/handoff/redeem", json={"token": token}).status_code == 401


def test_handoff_mint_requires_org_key(client):
    # No Bearer + no session → mint is unauthenticated.
    assert client.post("/v1/auth/handoff").status_code == 401


def test_handoff_is_org_scoped(make_org, client):
    """A token minted for org A logs into org A, never org B."""
    a, b = make_org(), make_org()
    token = _mint(client, a)
    r = client.post("/v1/auth/handoff/redeem", json={"token": token})
    assert r.status_code == 200
    assert r.json()["org_id"] == a["org_id"] != b["org_id"]
