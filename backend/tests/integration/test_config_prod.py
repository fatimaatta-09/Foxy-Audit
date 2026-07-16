"""Prod fail-fast (config._require_secure_prod).

FOXY_ENV=prod must refuse to construct on insecure/absent session secrets, and —
new in Phase 1 — must also refuse live EVM anchoring without its funded signing
key, instead of failing lazily on the first on-chain submit (anchor.py).
"""
from __future__ import annotations

import pytest

from app.config import Settings

# Strong, distinct secrets so only the anchor check can trip in the EVM tests.
_STRONG = dict(
    foxy_env="prod",
    session_secret="s" * 40,
    staff_session_secret="t" * 40,
    api_key_pepper="pepper",
)


def test_prod_requires_strong_secrets():
    # Insecure placeholders passed explicitly (so ambient SESSION_SECRET/
    # API_KEY_PEPPER env vars can't mask the check) -> must raise in prod.
    with pytest.raises(ValueError):
        Settings(_env_file=None, foxy_env="prod",
                 session_secret="dev-insecure-session-secret-change-me",
                 staff_session_secret="dev-insecure-staff-session-secret-change-me",
                 api_key_pepper="")


def test_prod_evm_anchoring_requires_private_key():
    with pytest.raises(ValueError, match="ANCHOR_EVM_PRIVATE_KEY"):
        Settings(_env_file=None, anchor_enabled=True, anchor_provider="evm",
                 anchor_evm_private_key="", **_STRONG)


def test_prod_evm_anchoring_ok_with_key():
    s = Settings(_env_file=None, anchor_enabled=True, anchor_provider="evm",
                 anchor_evm_private_key="0x" + "a" * 64, **_STRONG)
    assert s.is_prod


def test_prod_stub_anchoring_needs_no_key():
    # anchor defaults (disabled / stub) -> the EVM key check is inert.
    s = Settings(_env_file=None, **_STRONG)
    assert s.is_prod and not s.anchor_enabled
