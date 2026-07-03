"""EVM anchoring encode/decode consistency (Phase 3 A1, no live chain needed).

Guards the seam between anchor.py (submit), verify_anchor.py (read back), and
AnchorRegistry.sol: the function selector, ABI, bytes32 encoding, and calldata
layout must all agree, so a Sepolia anchor's on-chain root round-trips exactly
through the independent verifier. The actual HTTP broadcast is standard web3 and
is exercised only against a real/testnet RPC.
"""

from __future__ import annotations

import os
import re

from web3 import Web3

from app.anchor import _ANCHOR_ABI

_ROOT_HEX = "115ac4bcd47fc5a2738842d3c2a76170e794fa185174f2e890285e50f0d01943"


def _selector(sig: str) -> str:
    s = Web3.keccak(text=sig)[:4].hex()
    return s[2:] if s.startswith("0x") else s


def test_abi_signature_is_anchor_bytes32():
    fn = _ANCHOR_ABI[0]
    sig = fn["name"] + "(" + ",".join(i["type"] for i in fn["inputs"]) + ")"
    assert sig == "anchor(bytes32)"


def test_contract_source_declares_matching_function():
    sol_path = os.path.join(os.path.dirname(__file__), "..", "..", "..",
                            "contracts", "AnchorRegistry.sol")
    src = open(sol_path, encoding="utf-8").read()
    assert re.search(r"function\s+anchor\s*\(\s*bytes32", src)


def test_root_hash_encodes_to_32_bytes():
    assert len(bytes.fromhex(_ROOT_HEX)) == 32


def test_calldata_roundtrips_through_verifier_decode():
    """Encode anchor(root) as web3 would, then apply verify_anchor's extraction
    (selector = inp[:8], root = inp[8:72]) and recover the exact root."""
    contract = Web3().eth.contract(abi=_ANCHOR_ABI)
    calldata = contract.encode_abi("anchor", args=[bytes.fromhex(_ROOT_HEX)])
    calldata = calldata[2:] if calldata.startswith("0x") else calldata
    assert len(calldata) == 72                                   # 4-byte selector + 32-byte root
    assert calldata[:8].lower() == _selector("anchor(bytes32)")  # verify_anchor selector check
    assert calldata[8:72].lower() == _ROOT_HEX                   # verify_anchor root extraction


def test_receipt_tx_hash_hex_is_v6_v7_safe():
    from hexbytes import HexBytes
    for txh in (HexBytes("0x" + "ab" * 32), "0x" + "ab" * 32):   # v6 HexBytes / v7 HexStr
        tx_hex = txh.hex() if hasattr(txh, "hex") else str(txh)
        if not tx_hex.startswith("0x"):
            tx_hex = "0x" + tx_hex
        assert tx_hex.startswith("0x") and not tx_hex.startswith("0x0x")
        assert len(tx_hex) == 66
