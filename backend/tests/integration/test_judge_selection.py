"""Per-tenant AI Judge selection — storage, snapshot isolation, and the tier gate.

Each org picks which judge grades THEIR events (gemini | openai | both) and whose
key pays for it (own = BYOK, platform = Foxy's keys, premium only). The columns
are additive and deliberately EXCLUDED from the policy snapshot: routing is an
operational/billing choice, and a key must never reach the chain.
"""

from __future__ import annotations

import uuid

from app.crypto_secrets import decrypt_secret
from app.db import SessionLocal
from app.models import OrgPolicy, Organization
from app.policy_snapshot import capture_policy_snapshot, policy_snapshot_hash


def _policy(org_id: str) -> OrgPolicy:
    db = SessionLocal()
    try:
        row = db.get(OrgPolicy, uuid.UUID(org_id))
        if row is None:
            row = OrgPolicy(org_id=uuid.UUID(org_id))
            db.add(row)
            db.commit()
            db.refresh(row)
        db.expunge(row)
        return row
    finally:
        db.close()


def _set_tier(org_id: str, tier: str | None) -> None:
    db = SessionLocal()
    try:
        org = db.get(Organization, uuid.UUID(org_id))
        org.plan_tier = tier
        db.commit()
    finally:
        db.close()


# ── storage: columns exist, default safely, and hold ciphertext only ──────────

def test_judge_columns_default_to_gemini_and_byok(make_org):
    org = make_org()
    row = _policy(org["org_id"])
    assert row.judge_provider == "gemini"
    assert row.judge_key_mode == "own"
    assert row.gemini_key_enc is None
    assert row.openai_key_enc is None


def test_stored_key_column_holds_ciphertext_not_the_key(make_org):
    org = make_org()
    secret = "AIzaSy-tenant-owned-gemini-key"
    db = SessionLocal()
    try:
        row = db.get(OrgPolicy, uuid.UUID(org["org_id"])) or OrgPolicy(
            org_id=uuid.UUID(org["org_id"]))
        from app.crypto_secrets import encrypt_secret
        row.gemini_key_enc = encrypt_secret(secret, org["org_id"], "gemini")
        db.add(row)
        db.commit()
    finally:
        db.close()
    stored = _policy(org["org_id"]).gemini_key_enc
    assert secret not in stored
    assert decrypt_secret(stored, org["org_id"], "gemini") == secret


# ── the chain must never see routing choices or keys ─────────────────────────

def test_policy_snapshot_excludes_judge_routing_and_keys(make_org):
    org = make_org()
    row = _policy(org["org_id"])
    snapshot = capture_policy_snapshot(row)
    for leaky in ("judge_provider", "judge_key_mode", "gemini_key_enc",
                  "openai_key_enc", "gemini_api_key", "openai_api_key"):
        assert leaky not in snapshot


def test_changing_judge_routing_does_not_change_the_snapshot_hash(make_org):
    """Existing rows' chain snapshot hash must be unchanged by this feature."""
    org = make_org()
    row = _policy(org["org_id"])
    before = policy_snapshot_hash(capture_policy_snapshot(row))

    db = SessionLocal()
    try:
        live = db.get(OrgPolicy, uuid.UUID(org["org_id"]))
        live.judge_provider = "both"
        live.judge_key_mode = "platform"
        from app.crypto_secrets import encrypt_secret
        live.gemini_key_enc = encrypt_secret("k1", org["org_id"], "gemini")
        live.openai_key_enc = encrypt_secret("k2", org["org_id"], "openai")
        db.commit()
    finally:
        db.close()

    after = policy_snapshot_hash(capture_policy_snapshot(_policy(org["org_id"])))
    assert after == before
