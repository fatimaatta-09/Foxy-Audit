"""BYOK provider keys are encrypted at rest with Fernet — never plaintext.

Security invariants exercised here:
  * a stored customer provider key is unreadable from the database alone;
  * if BYOK is used but the deployment has no PROVIDER_KEY_ENCRYPTION_KEY we FAIL
    CLOSED (raise) rather than persist/return plaintext;
  * the KEK can be ROTATED (comma-separated list) with zero downtime;
  * a stored blob is CONTEXT-BOUND to (org_id, provider) and cannot be replayed
    into another tenant's row or the other provider slot.
"""

from __future__ import annotations

import pytest

from app import crypto_secrets
from app.config import get_settings

ORG_A = "11111111-1111-1111-1111-111111111111"
ORG_B = "22222222-2222-2222-2222-222222222222"


def _set_kek(monkeypatch, value: str) -> None:
    monkeypatch.setattr(get_settings(), "provider_key_encryption_key", value)


def test_encrypt_decrypt_round_trip():
    secret = "AIzaSy-super-secret-customer-gemini-key"
    token = crypto_secrets.encrypt_secret(secret, ORG_A, "gemini")
    assert crypto_secrets.decrypt_secret(token, ORG_A, "gemini") == secret


def test_ciphertext_never_contains_the_plaintext():
    secret = "sk-proj-customer-openai-key-0123456789"
    token = crypto_secrets.encrypt_secret(secret, ORG_A, "openai")
    assert secret not in token
    assert token != secret


def test_encryption_is_non_deterministic():
    secret = "same-key-two-tenants"
    assert (crypto_secrets.encrypt_secret(secret, ORG_A, "gemini")
            != crypto_secrets.encrypt_secret(secret, ORG_A, "gemini"))


def test_encrypt_fails_closed_without_a_deployment_key(monkeypatch):
    _set_kek(monkeypatch, "")
    with pytest.raises(crypto_secrets.SecretsNotConfigured):
        crypto_secrets.encrypt_secret("byok-key", ORG_A, "gemini")


def test_decrypt_fails_closed_without_a_deployment_key(monkeypatch):
    token = crypto_secrets.encrypt_secret("byok-key", ORG_A, "gemini")
    _set_kek(monkeypatch, "")
    with pytest.raises(crypto_secrets.SecretsNotConfigured):
        crypto_secrets.decrypt_secret(token, ORG_A, "gemini")


def test_decrypt_rejects_a_token_from_a_completely_different_key(monkeypatch):
    """A foreign key (with the original NOT in the list) must not decrypt."""
    token = crypto_secrets.encrypt_secret("byok-key", ORG_A, "gemini")
    _set_kek(monkeypatch, crypto_secrets.generate_encryption_key())
    with pytest.raises(crypto_secrets.SecretDecryptionError):
        crypto_secrets.decrypt_secret(token, ORG_A, "gemini")


def test_encryption_configured_reports_deployment_readiness(monkeypatch):
    assert crypto_secrets.encryption_configured() is True
    _set_kek(monkeypatch, "")
    assert crypto_secrets.encryption_configured() is False


def test_generated_key_is_usable(monkeypatch):
    _set_kek(monkeypatch, crypto_secrets.generate_encryption_key())
    token = crypto_secrets.encrypt_secret("hello", ORG_A, "openai")
    assert crypto_secrets.decrypt_secret(token, ORG_A, "openai") == "hello"


# ── rotation (MultiFernet) ────────────────────────────────────────────────────
def test_rotation_new_key_first_still_decrypts_old_blobs(monkeypatch):
    """Rotate by prepending a new key; the OLD key stays in the list so existing
    blobs keep decrypting — zero downtime."""
    old = get_settings().provider_key_encryption_key
    old = old.get_secret_value() if hasattr(old, "get_secret_value") else old
    token = crypto_secrets.encrypt_secret("legacy-key", ORG_A, "gemini")

    new = crypto_secrets.generate_encryption_key()
    _set_kek(monkeypatch, f"{new},{old}")                     # NEW primary, OLD kept
    assert crypto_secrets.decrypt_secret(token, ORG_A, "gemini") == "legacy-key"

    # New writes use the new primary key and are readable under the rotated list.
    fresh = crypto_secrets.encrypt_secret("fresh-key", ORG_A, "gemini")
    assert crypto_secrets.decrypt_secret(fresh, ORG_A, "gemini") == "fresh-key"


def test_rotation_dropping_old_key_stops_decrypting_its_blobs(monkeypatch):
    token = crypto_secrets.encrypt_secret("legacy-key", ORG_A, "gemini")
    _set_kek(monkeypatch, crypto_secrets.generate_encryption_key())   # old key dropped
    with pytest.raises(crypto_secrets.SecretDecryptionError):
        crypto_secrets.decrypt_secret(token, ORG_A, "gemini")


# ── context binding (org_id + provider envelope) ──────────────────────────────
def test_blob_cannot_be_replayed_into_another_org(monkeypatch):
    token = crypto_secrets.encrypt_secret("byok-key", ORG_A, "gemini")
    with pytest.raises(crypto_secrets.SecretDecryptionError):
        crypto_secrets.decrypt_secret(token, ORG_B, "gemini")


def test_blob_cannot_be_replayed_into_the_other_provider_slot(monkeypatch):
    token = crypto_secrets.encrypt_secret("byok-key", ORG_A, "gemini")
    with pytest.raises(crypto_secrets.SecretDecryptionError):
        crypto_secrets.decrypt_secret(token, ORG_A, "openai")


# ── decrypt_secret None/empty guard (fix #7) ──────────────────────────────────
@pytest.mark.parametrize("bad", [None, "", "   "])
def test_decrypt_guards_empty_token(bad):
    with pytest.raises(crypto_secrets.SecretDecryptionError):
        crypto_secrets.decrypt_secret(bad, ORG_A, "gemini")


# ── malformed vs unset KEK (fix #5) ───────────────────────────────────────────
def test_malformed_kek_is_distinct_from_unset(monkeypatch):
    _set_kek(monkeypatch, "not-a-valid-fernet-key")
    assert crypto_secrets.encryption_status() == "malformed"
    with pytest.raises(crypto_secrets.SecretsMisconfigured):
        crypto_secrets.encrypt_secret("byok-key", ORG_A, "gemini")

    _set_kek(monkeypatch, "")
    assert crypto_secrets.encryption_status() == "unset"

    # A good key reports 'ok'.
    _set_kek(monkeypatch, crypto_secrets.generate_encryption_key())
    assert crypto_secrets.encryption_status() == "ok"
