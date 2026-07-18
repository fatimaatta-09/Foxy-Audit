"""P2 · §G — optional API-key expiry. An expired key authenticates no one; the
key list surfaces expiry + last-used; create accepts expires_in_days.

Keys are created via POST /v1/keys (peppered, no legacy org-hash fallback) so
expiry is actually exercised — the make_org fixture key also seeds the legacy
organizations.api_key_hash, which would otherwise mask per-key expiry.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from app.db import SessionLocal
from app.models import ApiKey


def _create_key(c, name, expires_in_days=None):
    body = {"name": name}
    if expires_in_days is not None:
        body["expires_in_days"] = expires_in_days
    r = c.post("/v1/keys", json=body)
    assert r.status_code == 200, r.text
    return r.json()          # includes api_key (plaintext) + id


def _set_expiry(key_id, when):
    db = SessionLocal()
    try:
        db.query(ApiKey).filter(ApiKey.id == uuid.UUID(key_id)).one().expires_at = when
        db.commit()
    finally:
        db.close()


def _auth(key):
    return {"Authorization": f"Bearer {key}"}


def test_unexpired_created_key_authenticates(make_org, login, client):
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    k = _create_key(c, "live", expires_in_days=30)
    assert client.get("/v1/stats", headers=_auth(k["api_key"])).status_code == 200


def test_expired_key_is_rejected(make_org, login, client):
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    k = _create_key(c, "soon-dead", expires_in_days=1)
    _set_expiry(k["id"], datetime.now(timezone.utc) - timedelta(seconds=1))
    assert client.get("/v1/stats", headers=_auth(k["api_key"])).status_code == 401


def test_key_without_expiry_never_expires(make_org, login, client):
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    k = _create_key(c, "forever")
    assert client.get("/v1/stats", headers=_auth(k["api_key"])).status_code == 200


def test_create_with_expiry_and_list_shows_it(make_org, login):
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    _create_key(c, "ci-key", expires_in_days=30)
    created = next(k for k in c.get("/v1/keys").json() if k["name"] == "ci-key")
    assert created["expires_at"] is not None and created["expired"] is False


def test_list_flags_expired(make_org, login):
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    k = _create_key(c, "old", expires_in_days=1)
    _set_expiry(k["id"], datetime.now(timezone.utc) - timedelta(days=1))
    assert any(x["expired"] is True for x in c.get("/v1/keys").json())
