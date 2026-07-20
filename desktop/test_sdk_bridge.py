"""Protocol tests for the desktop SDK bridge.

PyQt6 is optional in the repository test environment. When installed, these
tests prove that the real bridge emits the local capture signal consumed by the
pet and dashboard.
"""

from __future__ import annotations

import json

import pytest


pytest.importorskip("PyQt6")

from sdk_bridge import SDKBridgeListener  # noqa: E402


def test_hash_ok_is_forwarded_with_delivery_state():
    listener = SDKBridgeListener()
    received = []
    listener.hash_confirmed.connect(received.append)

    listener._process_packet(json.dumps({
        "event": "hash_ok",
        "policy": "judge_smoke",
        "tokens": 12,
        "delivery": "queued",
    }).encode("utf-8"))

    assert received == [{
        "event": "hash_ok",
        "policy": "judge_smoke",
        "tokens": 12,
        "delivery": "queued",
    }]


def test_malformed_packet_is_ignored():
    listener = SDKBridgeListener()
    received = []
    listener.hash_confirmed.connect(received.append)

    listener._process_packet(b"not-json")

    assert received == []
