"""Envelope for customer-supplied provider API keys (BYOK), encrypted at rest.

A tenant that brings its own Gemini/OpenAI key hands us a live billing credential.
That secret is stored ONLY as a Fernet token (AES-128-CBC + HMAC-SHA256, random IV)
under the deployment key(s) ``PROVIDER_KEY_ENCRYPTION_KEY`` — each a urlsafe-base64
32-byte value. Plaintext exists only inside the grading call that needs it.

Two hardening properties beyond raw Fernet:

* **Rotation** — ``PROVIDER_KEY_ENCRYPTION_KEY`` may be a COMMA-SEPARATED list.
  A ``MultiFernet`` encrypts with the FIRST key and decrypts by trying ALL of
  them, so a key can be rotated with zero downtime (put the new key first, keep
  the old one until every blob is re-encrypted).

* **Context binding** — Fernet has no associated data, so a raw token decrypts in
  any row and any provider slot. We instead encrypt a small JSON envelope carrying
  the owning ``org_id`` and ``provider`` and validate BOTH on decrypt, so a stored
  blob cannot be replayed into another tenant's row or the other provider slot.

Fail closed, always. If BYOK is in play and the deployment has no (or an unusable)
encryption key, every path here raises rather than writing a plaintext secret to a
column or handing one back to a caller. Callers translate that into an honest error
(503 on the API, ``evaluator_unavailable`` in the worker) — never a silent
downgrade to plaintext.
"""

from __future__ import annotations

import base64
import json
import os

from .config import get_settings

ENCRYPTION_KEY_ENV = "PROVIDER_KEY_ENCRYPTION_KEY"
_ENVELOPE_VERSION = 1


class SecretsNotConfigured(RuntimeError):
    """The deployment cannot handle BYOK keys (no usable PROVIDER_KEY_ENCRYPTION_KEY)."""


class SecretsMisconfigured(SecretsNotConfigured):
    """A PROVIDER_KEY_ENCRYPTION_KEY is PRESENT but not a valid Fernet key (a typo).

    A subclass of SecretsNotConfigured so existing ``except SecretsNotConfigured``
    handlers (and ``encryption_configured()``) still treat it as "can't encrypt",
    while startup validation can tell a typo apart from an intentional blank.
    """


class SecretDecryptionError(RuntimeError):
    """A stored token could not be decrypted, is malformed, or failed org/provider
    validation (a swapped or replayed ciphertext)."""


def _load_keys() -> list[str]:
    """The configured Fernet key(s), newest-first. Empty list = BYOK disabled."""
    raw = get_settings().provider_key_encryption_key
    # SecretStr in prod config; tolerate a plain str too (tests / older callers).
    value = raw.get_secret_value() if hasattr(raw, "get_secret_value") else (raw or "")
    return [k.strip() for k in value.strip().split(",") if k.strip()]


def _multifernet():
    """Build a MultiFernet over the configured key(s), or fail closed.

    Raises SecretsNotConfigured when unset and SecretsMisconfigured (a subclass)
    when present-but-invalid. Never logs the key material.
    """
    keys = _load_keys()
    if not keys:
        raise SecretsNotConfigured(
            f"{ENCRYPTION_KEY_ENV} is not set; refusing to handle BYOK provider keys"
        )
    try:
        from cryptography.fernet import Fernet, MultiFernet
    except ImportError as exc:                      # pragma: no cover - packaging guard
        raise SecretsNotConfigured("cryptography is not installed") from exc
    try:
        return MultiFernet([Fernet(k) for k in keys])
    except (ValueError, TypeError) as exc:
        raise SecretsMisconfigured(
            f"{ENCRYPTION_KEY_ENV} must be a comma-separated list of "
            "urlsafe-base64-encoded 32-byte Fernet keys"
        ) from exc


def encryption_configured() -> bool:
    """True when BYOK keys can be stored/read on this deployment."""
    try:
        _multifernet()
    except SecretsNotConfigured:
        return False
    return True


def encryption_status() -> str:
    """'ok' | 'unset' | 'malformed' — for startup validation. Never the key value."""
    try:
        _multifernet()
        return "ok"
    except SecretsMisconfigured:
        return "malformed"
    except SecretsNotConfigured:
        return "unset"


def encrypt_secret(plaintext: str, org_id, provider: str) -> str:
    """Encrypt a provider key bound to ``(org_id, provider)`` for storage.

    Raises SecretsNotConfigured if the deployment cannot encrypt — a key is never
    written in plaintext.
    """
    envelope = json.dumps(
        {"v": _ENVELOPE_VERSION, "org_id": str(org_id),
         "provider": str(provider), "key": plaintext},
        separators=(",", ":"), sort_keys=True,
    )
    return _multifernet().encrypt(envelope.encode("utf-8")).decode("ascii")


def decrypt_secret(token: str | None, org_id, provider: str) -> str:
    """Decrypt a stored provider key and validate it belongs to ``(org_id, provider)``.

    In-memory use only — never persist or log the return value. Raises
    SecretDecryptionError on an empty/None token, a bad/legacy blob, or an
    org/provider mismatch; SecretsNotConfigured if the deployment cannot decrypt.
    """
    if not token or not str(token).strip():
        raise SecretDecryptionError("no stored provider key to decrypt")
    from cryptography.fernet import InvalidToken
    fernet = _multifernet()
    try:
        raw = fernet.decrypt(str(token).encode("utf-8"))
    except InvalidToken as exc:
        raise SecretDecryptionError("stored provider key could not be decrypted") from exc
    try:
        env = json.loads(raw.decode("utf-8"))
        stored_org = env["org_id"]
        stored_provider = env["provider"]
        key = env["key"]
    except (ValueError, KeyError, TypeError) as exc:
        raise SecretDecryptionError("stored provider key envelope is malformed") from exc
    if stored_org != str(org_id) or stored_provider != str(provider):
        # Decrypted cleanly but does not belong here — refuse rather than use a key
        # written for a different org/provider (cross-tenant key swap).
        raise SecretDecryptionError("stored provider key context mismatch")
    return key


def generate_encryption_key() -> str:
    """A fresh deployment key — for `PROVIDER_KEY_ENCRYPTION_KEY` and for tests."""
    return base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")
