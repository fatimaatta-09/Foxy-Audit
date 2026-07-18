"""Public verify link (§A) — aggregate, content-blind status behind the badge token.

Guards: unknown token 404s; the JSON + HTML never leak org_id, org name, or any
per-record hash; a fresh org reads as un-anchored; the count is exposed but nothing else.
"""
from __future__ import annotations

from app.db import SessionLocal
from app.models import Organization


def _mint_token(client, org) -> str:
    """Set the public badge token directly (the mint endpoint needs an admin
    session; the public routes only need the token to exist)."""
    db = SessionLocal()
    try:
        o = db.get(Organization, org["org_id"])
        o.public_badge_token = "tok-" + str(org["org_id"]).replace("-", "")[:16]
        token = o.public_badge_token
        db.commit()
    finally:
        db.close()
    return token


def _clear_token(org) -> None:
    db = SessionLocal()
    try:
        db.get(Organization, org["org_id"]).public_badge_token = None
        db.commit()
    finally:
        db.close()


def test_unknown_token_404s(client):
    assert client.get("/v1/badge/nope-not-real/status").status_code == 404
    assert client.get("/verify/nope-not-real").status_code == 404


def test_status_json_is_aggregate_and_content_blind(make_org, client):
    org = make_org()
    token = _mint_token(client, org)
    r = client.get(f"/v1/badge/{token}/status")
    assert r.status_code == 200, r.text
    d = r.json()
    assert set(d) == {"verified", "audited", "anchored", "freshness",
                      "anchored_at", "chain", "explorer_url"}
    assert d["verified"] is False and d["anchored"] is False and d["audited"] == 0
    # never leak identity
    body = r.text
    assert str(org["org_id"]) not in body
    # DB org name must not appear
    db = SessionLocal()
    try:
        name = db.get(Organization, org["org_id"]).name
    finally:
        db.close()
    assert name is None or name not in body


def test_verify_page_renders_and_hides_identity(make_org, client):
    org = make_org()
    token = _mint_token(client, org)
    r = client.get(f"/verify/{token}")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    body = r.text
    assert "Foxy Audit" in body
    assert str(org["org_id"]) not in body
    # no per-record hash column names leak into the public page
    assert "prompt_hash" not in body and "chain_hash" not in body


def test_revoked_token_stops_working(make_org, client):
    org = make_org()
    token = _mint_token(client, org)
    assert client.get(f"/verify/{token}").status_code == 200
    _clear_token(org)   # revoke
    assert client.get(f"/verify/{token}").status_code == 404
    assert client.get(f"/v1/badge/{token}/status").status_code == 404
