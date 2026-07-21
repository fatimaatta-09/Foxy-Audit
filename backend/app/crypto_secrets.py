"""Envelope for customer-supplied provider API keys (BYOK), encrypted at rest.

A tenant that brings its own Gemini/OpenAI key hands us a live billing credential.
That secret is stored ONLY as a Fernet token (AES-128-CBC + HMAC-SHA256, random IV)
under the deployment key ``PROVIDER_KEY_ENCRYPTION_KEY`` — a urlsafe-base64 32-byte
value. Plaintext exists only inside the grading call that needs it.

Fail closed, always. If BYOK is in play and the deployment has no encryption key,
every path here raises rather than writing a plaintext secret to a column or
handing one back to a caller. Callers translate that into an honest error
(503 on the API, ``evaluator_unavailable`` in the worker) — never a silent
downgrade to plaintext.
"""

from __future__ import annotations

import base64
import os

from .config import get_settings

ENCRYPTION_KEY_ENV = "PROVIDER_KEY_ENCRYPTION_KEY"


class SecretsNotConfigured(RuntimeError):
    """The deployment has no (or an unusable) PROVIDER_KEY_ENCRYPTION_KEY."""


class SecretDecryptionError(RuntimeError):
    """A stored token could not be decrypted — wrong deployment key, or tampered."""


def _fernet():
    """Build the Fernet cipher, or fail closed. Never logs the key material."""
    key = (get_settings().provider_key_encryption_key or "").strip()
    if not key:
        raise SecretsNotConfigured(
            f"{ENCRYPTION_KEY_ENV} is not set; refusing to handle BYOK provider keys"
        )
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:                      # pragma: no cover - packaging guard
        raise SecretsNotConfigured("cryptography is not installed") from exc
    try:
        return Fernet(key)
    except (ValueError, TypeError) as exc:
        raise SecretsNotConfigured(
            f"{ENCRYPTION_KEY_ENV} must be urlsafe-base64-encoded 32 bytes"
        ) from exc


def encryption_configured() -> bool:
    """True when BYOK keys can be stored/read on this deployment."""
    try:
        _fernet()
    except SecretsNotConfigured:
        return False
    return True


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a provider key for storage. Raises SecretsNotConfigured if we can't."""
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(token: str) -> str:
    """Decrypt a stored provider key. In-memory use only — never persist or log it."""
    fernet = _fernet()
    try:
        from cryptography.fernet import InvalidToken
        return fernet.decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise SecretDecryptionError("stored provider key could not be decrypted") from exc


def generate_encryption_key() -> str:
    """A fresh deployment key — for `PROVIDER_KEY_ENCRYPTION_KEY` and for tests."""
    return base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")
