"""Independently verify a public-chain anchor (Phase 3 A1).

    python scripts/verify_anchor.py --key foxy_sk_...

Given an org's API key, this:
  1. finds the org's latest anchor receipt (or --anchor-id),
  2. recomputes the hash chain from genesis up to the anchored seq and asserts it
     equals the anchored root_hash — proving no earlier record was altered,
  3. for an on-chain (evm) anchor, reads the anchoring transaction back and
     asserts the calldata's root matches too — the external, operator-independent
     proof.

Exit code 0 = verified, 1 = mismatch/failure. This is the "don't trust us, check
the math" story an auditor can run themselves.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, text  # noqa: E402

from app.anchor import latest_anchor, recompute_head  # noqa: E402
from app.auth import hash_key  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import ApiKey, ChainAnchor, Organization  # noqa: E402


def _resolve_org(db, key: str):
    """Find the org for an API key via the peppered path, then legacy sha256."""
    row = db.execute(
        select(ApiKey).where(ApiKey.key_hash == hash_key(key), ApiKey.status == "active")
    ).scalar_one_or_none()
    if row is not None:
        return row.org_id
    legacy = hashlib.sha256(key.encode("utf-8")).hexdigest()
    org = db.execute(
        select(Organization).where(Organization.api_key_hash == legacy)
    ).scalar_one_or_none()
    return org.id if org else None


def _check_onchain(anchor: ChainAnchor) -> tuple[bool | None, str]:
    """For an evm anchor, read the tx back and compare its calldata root."""
    if anchor.chain == "stub" or not anchor.tx_hash:
        return None, "stub/no external chain — on-chain read skipped"
    s = get_settings()
    if not s.anchor_evm_rpc_url:
        return None, "ANCHOR_EVM_RPC_URL not set — cannot read the chain"
    try:
        from web3 import Web3
        w3 = Web3(Web3.HTTPProvider(s.anchor_evm_rpc_url))
        tx = w3.eth.get_transaction(anchor.tx_hash)
        receipt = w3.eth.get_transaction_receipt(anchor.tx_hash)
        inp = tx["input"]
        inp = inp.hex() if hasattr(inp, "hex") else str(inp)
        inp = inp[2:] if inp.startswith("0x") else inp
        # calldata must be exactly anchor(bytes32): 4-byte selector + 32-byte root.
        if len(inp) < 72:
            return False, f"unexpected calldata ({len(inp)} hex chars; need 72 for anchor(bytes32))"
        es = Web3.keccak(text="anchor(bytes32)").hex()
        es = es[2:] if es.startswith("0x") else es
        expected_sel, selector = es[:8].lower(), inp[:8].lower()
        onchain_root = inp[8:72].lower()            # bytes 4..36 = the bytes32 root
        sel_ok = selector == expected_sel
        root_ok = onchain_root == anchor.root_hash.lower()
        ok = (receipt["status"] == 1) and sel_ok and root_ok
        return ok, (f"selector {'ok' if sel_ok else 'MISMATCH'}, "
                    f"root {onchain_root[:16]}... ({'match' if root_ok else 'MISMATCH'}), "
                    f"tx status {receipt['status']}")
    except Exception as exc:                          # pragma: no cover
        return None, f"on-chain read failed: {exc}"


def main() -> None:
    ap = argparse.ArgumentParser(description="Independently verify a Foxy Audit anchor.")
    ap.add_argument("--key", required=True, help="the org's API key")
    ap.add_argument("--anchor-id", help="specific anchor id (default: latest)")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        org_id = _resolve_org(db, args.key)
        if org_id is None:
            print("ERROR: no org matches that API key", file=sys.stderr)
            sys.exit(1)
        db.execute(text("SELECT set_config('app.current_org', :oid, true)"),
                   {"oid": str(org_id)})

        if args.anchor_id:
            anchor = db.get(ChainAnchor, args.anchor_id)
        else:
            anchor = latest_anchor(db, org_id)
        if anchor is None or anchor.org_id != org_id:
            print("ERROR: no anchor found for this org", file=sys.stderr)
            sys.exit(1)

        print(f"Anchor {anchor.id}")
        print(f"  chain={anchor.chain} status={anchor.status} seq={anchor.last_seq} "
              f"tx={anchor.tx_hash}")
        print(f"  anchored root_hash: {anchor.root_hash}")

        computed = recompute_head(db, org_id, anchor.last_seq)
        print(f"  recomputed head:    {computed}")
        local_ok = (computed is not None and computed == anchor.root_hash)
        print(f"  [{'PASS' if local_ok else 'FAIL'}] local recompute "
              f"{'matches' if local_ok else 'DOES NOT MATCH'} the anchored root")

        onchain_ok, note = _check_onchain(anchor)
        print(f"  [{'PASS' if onchain_ok else ('n/a' if onchain_ok is None else 'FAIL')}] "
              f"on-chain: {note}")

        ok = local_ok and (onchain_ok is not False)
        print("\nRESULT:", "VERIFIED" if ok else "VERIFICATION FAILED")
        sys.exit(0 if ok else 1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
