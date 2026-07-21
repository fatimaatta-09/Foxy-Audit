"""BYOK provider keys are encrypted at rest with Fernet — never plaintext.

Security invariant #1: a stored customer provider key must be unreadable from
the database alone. Invariant: if BYOK is used but the deployment has no
PROVIDER_KEY_ENCRYPTION_KEY, we FAIL CLOSED (raise) rather than silently
persisting or returning a plaintext secret.
"""

from __future__ import annotations

import pytest

from app import crypto_secrets
from app.config import get_settings


def test_encrypt_decrypt_round_trip():
    secret = "AIzaSy-super-secret-customer-gemini-key"
    token = crypto_secrets.encrypt_secret(secret)
    assert crypto_secrets.decrypt_secret(token) == secret


def test_ciphertext_never_contains_the_plaintext():
    secret = "sk-proj-customer-openai-key-0123456789"
    token = crypto_secrets.encrypt_secret(secret)
    assert secret not in token
    assert token != secret


def test_encryption_is_non_deterministic():
    """Fernet embeds a random IV: the same key must not produce a stable
    ciphertext an observer could fingerprint or compare across tenants."""
    secret = "same-key-two-tenants"
    assert crypto_secrets.encrypt_secret(secret) != crypto_secrets.encrypt_secret(secret)


def test_encrypt_fails_closed_without_a_deployment_key(monkeypatch):
    monkeypatch.setattr(get_settings(), "provider_key_encryption_key", "")
    with pytest.raises(crypto_secrets.SecretsNotConfigured):
        crypto_secrets.encrypt_secret("byok-key")


def test_decrypt_fails_closed_without_a_deployment_key(monkeypatch):
    token = crypto_secrets.encrypt_secret("byok-key")
    monkeypatch.setattr(get_settings(), "provider_key_encryption_key", "")
    with pytest.raises(crypto_secrets.SecretsNotConfigured):
        crypto_secrets.decrypt_secret(token)


def test_decrypt_rejects_a_token_from_a_different_deployment_key(monkeypatch):
    """A rotated/foreign key must not silently decrypt to garbage."""
    token = crypto_secrets.encrypt_secret("byok-key")
    monkeypatch.setattr(get_settings(), "provider_key_encryption_key",
                        crypto_secrets.generate_encryption_key())
    with pytest.raises(crypto_secrets.SecretDecryptionError):
        crypto_secrets.decrypt_secret(token)


def test_encryption_configured_reports_deployment_readiness(monkeypatch):
    assert crypto_secrets.encryption_configured() is True
    monkeypatch.setattr(get_settings(), "provider_key_encryption_key", "")
    assert crypto_secrets.encryption_configured() is False


def test_generated_key_is_usable(monkeypatch):
    monkeypatch.setattr(get_settings(), "provider_key_encryption_key",
                        crypto_secrets.generate_encryption_key())
    assert crypto_secrets.decrypt_secret(crypto_secrets.encrypt_secret("hello")) == "hello"
