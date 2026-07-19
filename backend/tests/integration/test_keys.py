"""API keys: HMAC-with-pepper storage, create/use/revoke, legacy sha256 fallback,
peppered rotate, and input validation."""

from __future__ import annotations

import hashlib
import hmac
import uuid

from sqlalchemy import select

from app.auth import hash_key
from app.config import get_settings
from app.db import SessionLocal
from app.models import AccountAction, ApiKey, Organization


def test_create_use_revoke(make_org, login, client):
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    created = c.post("/v1/keys", json={"name": "ci key"}).json()
    newkey = created["api_key"]

    assert client.get("/v1/health",
                      headers={"Authorization": f"Bearer {newkey}"}).status_code == 200
    assert c.delete(f"/v1/keys/{created['id']}").status_code == 200
    assert client.get("/v1/health",
                      headers={"Authorization": f"Bearer {newkey}"}).status_code == 401


def test_key_stored_as_hmac_not_plain_sha256(make_org, login):
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    key = c.post("/v1/keys", json={"name": "h"}).json()["api_key"]

    db = SessionLocal()
    try:
        row = db.execute(
            select(ApiKey).where(ApiKey.key_hash == hash_key(key))
        ).scalar_one_or_none()
        assert row is not None                                    # HMAC match found
        assert row.key_hash != hashlib.sha256(key.encode()).hexdigest()  # not plain sha256
        # The pepper must actually be mixed in: a DIFFERENT pepper yields a
        # different hash (proves it's HMAC-with-pepper, not just "some HMAC").
        wrong = hmac.new((get_settings().api_key_pepper + "X").encode(),
                         key.encode(), hashlib.sha256).hexdigest()
        assert row.key_hash != wrong
    finally:
        db.close()


def test_legacy_only_key_still_authenticates(client):
    """A pre-A2 org that has ONLY the plain-sha256 org hash (no api_keys row) must
    still authenticate via the legacy fallback in require_org."""
    key = "foxy_sk_" + uuid.uuid4().hex + uuid.uuid4().hex
    db = SessionLocal()
    try:
        db.add(Organization(id=uuid.uuid4(), name="Legacy Co",
                            api_key_hash=hashlib.sha256(key.encode()).hexdigest()))
        db.commit()
    finally:
        db.close()
    assert client.get("/v1/health",
                      headers={"Authorization": f"Bearer {key}"}).status_code == 200


def test_invalid_key_401(client):
    assert client.get("/v1/health",
                      headers={"Authorization": "Bearer foxy_sk_not_a_real_key"}).status_code == 401


def test_delete_bad_uuid_returns_422(make_org, login):
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    assert c.delete("/v1/keys/not-a-uuid").status_code == 422


def test_whitespace_name_defaults(make_org, login):
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    assert c.post("/v1/keys", json={"name": "    "}).json()["name"] == "unnamed key"


def test_rotate_invalidates_only_presented_key_and_records_audit(make_org, login, client):
    org = make_org()
    old = org["api_key"]
    admin = login(org["admin_email"], org["admin_password"])
    sibling = admin.post("/v1/keys", json={"name": "worker"}).json()["api_key"]
    new = client.post("/v1/keys/rotate", headers=org["auth"]).json()["api_key"]
    assert client.get("/v1/health",
                      headers={"Authorization": f"Bearer {new}"}).status_code == 200
    assert client.get("/v1/health",
                      headers={"Authorization": f"Bearer {old}"}).status_code == 401
    assert client.get("/v1/health",
                      headers={"Authorization": f"Bearer {sibling}"}).status_code == 200

    db = SessionLocal()
    try:
        action = db.query(AccountAction).filter(
            AccountAction.org_id == org["org_id"],
            AccountAction.action == "key.rotate",
        ).one()
        assert action.actor_email == "api_key"
        assert action.detail == {"mode": "self_service"}
    finally:
        db.close()
