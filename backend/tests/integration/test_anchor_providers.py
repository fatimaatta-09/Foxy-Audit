"""Anchor provider registry (Phase 5 · 5C).

OpenTimestamps was a stub that returned status='pending' forever — it never
actually timestamped to Bitcoin. Per "no fakes" it's removed, leaving only the
two real providers: 'stub' (deterministic, dev/CI) and 'evm' (Sepolia/any EVM).
Selecting the removed provider must now fail loudly, not fake a receipt.
"""

from __future__ import annotations

import types

import pytest

from app import anchor


def test_only_stub_and_evm_providers_remain():
    assert set(anchor._PROVIDERS) == {"stub", "evm"}


def test_opentimestamps_provider_is_removed():
    settings = types.SimpleNamespace(anchor_provider="opentimestamps")
    with pytest.raises(RuntimeError, match="unknown anchor_provider"):
        anchor.run_provider("deadbeef" * 8, settings)
