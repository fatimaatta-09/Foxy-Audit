"""GET/PUT /v1/policies — judge selection, write-only keys, and the tier gate.

Security invariants proved here:
  * a stored provider key is NEVER returned by any API — only booleans,
  * the tier gate is enforced SERVER-SIDE (non-premium + platform → 403),
  * keys are stored encrypted, and an empty string clears them.
"""

from __future__ import annotations

import uuid

from app.crypto_secrets import decrypt_secret
from app.db import SessionLocal
from app.models import OrgPolicy, Organization

TENANT_GEMINI = "AIzaSy-tenant-gemini-key-value"
TENANT_OPENAI = "sk-proj-tenant-openai-key-value"

_BASE = {"pii_detection": True, "prompt_injection": True,
         "regulated_data_mode": False, "max_token_threshold": 50000}


def _put(admin, **fields):
    return admin.put("/v1/policies", json={**_BASE, **fields})


def _set_tier(org_id: str, tier: str | None) -> None:
    db = SessionLocal()
    try:
        db.get(Organization, uuid.UUID(org_id)).plan_tier = tier
        db.commit()
    finally:
        db.close()


def _stored(org_id: str) -> OrgPolicy | None:
    """The org's policy row as actually persisted (None if it was never created)."""
    db = SessionLocal()
    try:
        row = db.get(OrgPolicy, uuid.UUID(org_id))
        if row is not None:
            db.expunge(row)
        return row
    finally:
        db.close()


# ── read contract: booleans, never keys ──────────────────────────────────────

def test_get_returns_judge_defaults_and_no_key_fields(make_org, client):
    org = make_org()
    body = client.get("/v1/policies", headers=org["auth"]).json()
    assert body["judge_provider"] == "gemini"
    assert body["judge_key_mode"] == "own"
    assert body["gemini_key_set"] is False
    assert body["openai_key_set"] is False
    assert body["plan_tier"] is None
    assert body["platform_keys_allowed"] is False
    # write-only fields must never appear on a read
    assert "gemini_api_key" not in body and "openai_api_key" not in body
    assert "gemini_key_enc" not in body and "openai_key_enc" not in body


def test_get_reports_key_presence_as_booleans_only(make_org, client, login):
    org = make_org()
    admin = login(org["admin_email"], org["admin_password"])
    assert _put(admin, gemini_api_key=TENANT_GEMINI).status_code == 200

    body = client.get("/v1/policies", headers=org["auth"]).json()
    assert body["gemini_key_set"] is True
    assert body["openai_key_set"] is False
    assert TENANT_GEMINI not in client.get("/v1/policies", headers=org["auth"]).text


def test_no_endpoint_response_ever_contains_a_stored_key(make_org, client, login):
    """Sweep the tenant-facing surfaces an operator might expect to leak."""
    org = make_org()
    admin = login(org["admin_email"], org["admin_password"])
    assert _put(admin, judge_provider="both", gemini_api_key=TENANT_GEMINI,
                openai_api_key=TENANT_OPENAI).status_code == 200

    for get in (lambda: client.get("/v1/policies", headers=org["auth"]),
                lambda: admin.get("/v1/policies"),
                lambda: admin.get("/v1/account"),
                lambda: admin.get("/v1/keys")):
        response = get()
        if response.status_code != 200:
            continue
        assert TENANT_GEMINI not in response.text
        assert TENANT_OPENAI not in response.text


def test_put_response_never_echoes_the_submitted_key(make_org, login):
    org = make_org()
    admin = login(org["admin_email"], org["admin_password"])
    response = _put(admin, gemini_api_key=TENANT_GEMINI)
    assert response.status_code == 200
    assert TENANT_GEMINI not in response.text
    assert response.json()["gemini_key_set"] is True


# ── write contract: encrypted at rest, clearable ─────────────────────────────

def test_submitted_key_is_stored_encrypted(make_org, login):
    org = make_org()
    admin = login(org["admin_email"], org["admin_password"])
    assert _put(admin, judge_provider="openai", openai_api_key=TENANT_OPENAI).status_code == 200

    row = _stored(org["org_id"])
    assert row.openai_key_enc is not None
    assert TENANT_OPENAI not in row.openai_key_enc          # ciphertext, not plaintext
    assert decrypt_secret(row.openai_key_enc) == TENANT_OPENAI


def test_omitting_the_key_field_keeps_the_stored_key(make_org, login):
    org = make_org()
    admin = login(org["admin_email"], org["admin_password"])
    assert _put(admin, gemini_api_key=TENANT_GEMINI).status_code == 200
    assert _put(admin, judge_provider="both").status_code == 200          # no key field
    assert decrypt_secret(_stored(org["org_id"]).gemini_key_enc) == TENANT_GEMINI


def test_empty_string_clears_the_stored_key(make_org, client, login):
    org = make_org()
    admin = login(org["admin_email"], org["admin_password"])
    assert _put(admin, gemini_api_key=TENANT_GEMINI).status_code == 200
    assert _put(admin, gemini_api_key="").status_code == 200
    assert _stored(org["org_id"]).gemini_key_enc is None
    assert client.get("/v1/policies", headers=org["auth"]).json()["gemini_key_set"] is False


def test_provider_selection_persists(make_org, client, login):
    org = make_org()
    admin = login(org["admin_email"], org["admin_password"])
    assert _put(admin, judge_provider="openai").status_code == 200
    assert client.get("/v1/policies", headers=org["auth"]).json()["judge_provider"] == "openai"


def test_invalid_provider_is_rejected(make_org, login):
    org = make_org()
    admin = login(org["admin_email"], org["admin_password"])
    assert _put(admin, judge_provider="claude-but-typoed").status_code == 422


# ── the tier gate is server-side ─────────────────────────────────────────────

def test_non_premium_org_cannot_select_platform_keys(make_org, login):
    org = make_org()
    admin = login(org["admin_email"], org["admin_password"])
    for tier in (None, "free", "pro", "max", "trial", "other"):
        _set_tier(org["org_id"], tier)
        response = _put(admin, judge_key_mode="platform")
        assert response.status_code == 403, f"tier {tier!r} was allowed platform keys"
        assert _stored(org["org_id"]).judge_key_mode == "own"     # nothing persisted


def test_premium_org_may_select_platform_keys(make_org, client, login):
    org = make_org()
    _set_tier(org["org_id"], "premium")
    admin = login(org["admin_email"], org["admin_password"])
    assert _put(admin, judge_key_mode="platform").status_code == 200

    body = client.get("/v1/policies", headers=org["auth"]).json()
    assert body["judge_key_mode"] == "platform"
    assert body["platform_keys_allowed"] is True
    assert body["plan_tier"] == "premium"


def test_premium_org_may_still_choose_its_own_key(make_org, client, login):
    org = make_org()
    _set_tier(org["org_id"], "premium")
    admin = login(org["admin_email"], org["admin_password"])
    assert _put(admin, judge_key_mode="own", gemini_api_key=TENANT_GEMINI).status_code == 200
    body = client.get("/v1/policies", headers=org["auth"]).json()
    assert body["judge_key_mode"] == "own"
    assert body["platform_keys_allowed"] is True                  # offered, not forced


def test_policy_write_still_requires_an_admin_session(make_org, client):
    """The SDK bearer key must not be able to set a judge key or key mode."""
    org = make_org()
    response = client.put("/v1/policies", headers=org["auth"],
                          json={**_BASE, "gemini_api_key": TENANT_GEMINI})
    assert response.status_code in (401, 403)
    assert _stored(org["org_id"]) is None or _stored(org["org_id"]).gemini_key_enc is None


# ── keys never reach the logs ────────────────────────────────────────────────

def test_storing_a_key_is_not_logged(make_org, login, caplog):
    org = make_org()
    admin = login(org["admin_email"], org["admin_password"])
    with caplog.at_level("DEBUG"):
        assert _put(admin, judge_provider="both", gemini_api_key=TENANT_GEMINI,
                    openai_api_key=TENANT_OPENAI).status_code == 200
    assert TENANT_GEMINI not in caplog.text
    assert TENANT_OPENAI not in caplog.text


def test_key_change_is_audited_without_the_key(make_org, login):
    """The account audit trail records THAT a key was set, never its value."""
    from sqlalchemy import text
    from app.db import engine
    org = make_org()
    admin = login(org["admin_email"], org["admin_password"])
    assert _put(admin, gemini_api_key=TENANT_GEMINI).status_code == 200
    with engine.begin() as conn:
        blob = " ".join(str(r) for r in conn.execute(
            text("SELECT action, detail::text FROM account_actions")).all())
    assert TENANT_GEMINI not in blob
