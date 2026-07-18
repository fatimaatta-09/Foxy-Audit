#!/usr/bin/env python3
"""Foxy Audit — independent, open-source ledger verifier (Phase 6 · 6D).

Re-verifies a Foxy Audit log export WITHOUT trusting Foxy. It re-implements the
hash-chain recipe here from scratch (stdlib only, zero Foxy imports), recomputes
every row from genesis, and reports the first tampered sequence — the whole point
being that you can run this yourself and don't have to take our word for it.

    python foxy_verify.py foxy-audit-logs.json
    python foxy_verify.py foxy-audit-logs.json --anchor --rpc https://rpc.sepolia.org

Input: the JSON you download from the dashboard (GET /v1/logs/export?format=json):

    {
      "org_id": "…", "count": N,
      "anchor": { "root_hash": "…", "last_seq": K, "tx_hash": "0x…",
                  "chain": "sepolia", "contract": "0x…" },   # optional
      "logs": [ { "seq": 1, "prompt_hash": "…", "response_hash": "…",
                  "token_count": 10, "policy_tag": "chat", "agent": "gpt-4o"|null,
                  "prev_hash": "…", "chain_hash": "…" }, … ]
    }

Exit code 0 = intact, 1 = tampering / anchor mismatch. `--json` for machine output.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sys

# ─── the chain recipe — an INDEPENDENT copy of backend/app/chain.py ───────────
# Keep this byte-for-byte identical to the backend, INCLUDING the rule that
# `agent` is appended only when present (so pre-agent rows hash unchanged). If the
# backend recipe ever changes, this must change with it — verifier/test_verify.py
# cross-checks the two on every run.

GENESIS_HASH = "0" * 64


def compute_chain_hash(*, org_id, prompt_hash, response_hash, token_count,
                       policy_tag, seq, prev_hash, agent=None, chain_version=1,
                       event_id=None, client_id=None, client_seq=None,
                       event_type=None, commitment_alg=None, event_metadata=None,
                       pii_signals=None, occurred_at=None):
    if chain_version >= 2:
        event = {
            "org_id": str(org_id), "event_id": str(event_id) if event_id else None,
            "client_id": client_id, "client_seq": client_seq,
            "event_type": event_type or "interaction",
            "commitment_alg": commitment_alg or "sha256-legacy",
            "prompt_hash": prompt_hash, "response_hash": response_hash,
            "token_count": token_count, "policy_tag": policy_tag, "agent": agent,
            "pii_signals": pii_signals, "event_metadata": event_metadata,
            "occurred_at": occurred_at, "seq": seq,
        }
        blob = json.dumps(event, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256((blob + prev_hash).encode("utf-8")).hexdigest()
    data_blob = f"{org_id}|{prompt_hash}|{response_hash}|{token_count}|{policy_tag}|{seq}"
    if agent:
        data_blob += f"|agent={agent}"
    return hashlib.sha256((data_blob + prev_hash).encode("utf-8")).hexdigest()


# ─── verification ─────────────────────────────────────────────────────────────

def _row_hash(org_id, row, prev_hash):
    return compute_chain_hash(
        org_id=org_id, prompt_hash=row["prompt_hash"], response_hash=row["response_hash"],
        token_count=row["token_count"], policy_tag=row["policy_tag"], seq=row["seq"],
        prev_hash=prev_hash, agent=row.get("agent"), chain_version=row.get("chain_version", 1),
        event_id=row.get("event_id"), client_id=row.get("client_id"),
        client_seq=row.get("client_seq"), event_type=row.get("event_type"),
        commitment_alg=row.get("commitment_alg"), event_metadata=row.get("event_metadata"),
        pii_signals=row.get("pii_signals"), occurred_at=row.get("occurred_at"))


def commitment_hex(value, key):
    """Match the SDK's HMAC commitment for customer-held known content."""
    canonical = json.dumps(value, ensure_ascii=True, sort_keys=True,
                           separators=(",", ":"), default=str)
    return hmac.new(str(key).encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_known_events(data, known_events, key):
    """Check commitments using a customer-owned sidecar without sending content to Foxy."""
    checked = 0
    for row in data.get("logs", []):
        event_id = row.get("event_id")
        known = known_events.get(str(event_id)) if event_id else None
        if not known:
            continue
        if commitment_hex(known.get("prompt", ""), key) != row.get("prompt_hash"):
            return {"ok": False, "event_id": str(event_id), "field": "prompt_hash"}
        if commitment_hex(known.get("response", ""), key) != row.get("response_hash"):
            return {"ok": False, "event_id": str(event_id), "field": "response_hash"}
        checked += 1
    return {"ok": True, "checked": checked}


def verify_export(data):
    """Recompute the whole chain from genesis and compare each row's stored hash.
    Returns {ok, count, first_broken_seq, detail, head, head_seq}."""
    org_id = data.get("org_id")
    rows = sorted(data.get("logs", []), key=lambda r: r["seq"])
    prev = GENESIS_HASH
    expected_seq = 1
    for row in rows:
        if row["seq"] != expected_seq:
            return {"ok": False, "count": len(rows), "first_broken_seq": row["seq"],
                    "detail": f"sequence gap before seq {row['seq']}",
                    "head": None, "head_seq": None}
        if row.get("prev_hash", GENESIS_HASH) != prev:
            return {"ok": False, "count": len(rows), "first_broken_seq": row["seq"],
                    "detail": f"previous hash mismatch at seq {row['seq']}",
                    "head": None, "head_seq": None}
        expected = _row_hash(org_id, row, prev)
        if expected != row.get("chain_hash"):
            return {"ok": False, "count": len(rows), "first_broken_seq": row["seq"],
                    "detail": f"chain hash mismatch at seq {row['seq']}",
                    "head": None, "head_seq": None}
        prev = row["chain_hash"]
        expected_seq += 1
    anchor = data.get("anchor") or {}
    if rows and anchor.get("last_seq", 0) > rows[-1]["seq"]:
        return {"ok": False, "count": len(rows), "first_broken_seq": None,
                "detail": "export stops before the anchored checkpoint",
                "head": None, "head_seq": None}
    return {"ok": True, "count": len(rows), "first_broken_seq": None,
            "detail": "chain intact",
            "head": prev if rows else GENESIS_HASH,
            "head_seq": rows[-1]["seq"] if rows else 0}


def recompute_head_upto(data, upto_seq):
    """Independently recompute the chain head at ``upto_seq`` from genesis."""
    org_id = data.get("org_id")
    prev, last = GENESIS_HASH, None
    for row in sorted(data.get("logs", []), key=lambda r: r["seq"]):
        if row["seq"] > upto_seq:
            break
        prev = _row_hash(org_id, row, prev)
        last = prev
    return last


def check_anchor_offline(data, _verify_result=None):
    """Compare the export's embedded anchor receipt to an INDEPENDENT recompute of
    the chain head at the anchored seq. Returns None if there's no receipt.

    NOTE: the receipt is Foxy-provided, so this proves the export's chain matches
    what Foxy says it anchored — for proof against the PUBLIC chain, use --anchor."""
    a = data.get("anchor")
    if not a:
        return None
    recomputed = recompute_head_upto(data, a["last_seq"])
    return {
        "matches": recomputed == a.get("root_hash"),
        "root_hash": a.get("root_hash"), "recomputed": recomputed,
        "last_seq": a.get("last_seq"), "chain": a.get("chain"),
        "tx_hash": a.get("tx_hash"), "block_number": a.get("block_number"),
        "contract": a.get("contract"),
    }


# ─── optional live on-chain check (lazy web3) ─────────────────────────────────

def _norm_hex(s):
    return str(s).lower().removeprefix("0x")


def check_anchor_onchain(rpc, tx_hash, expected_root, contract=None):
    """Confirm the anchoring tx really emitted Anchored(root) on the public chain.
    Best-effort and network-dependent; `ok` is None when it couldn't run."""
    if not rpc or not tx_hash or not expected_root:
        return {"ok": None, "detail": "need --rpc and an anchor tx_hash + root in the export"}
    try:
        from web3 import Web3
    except ImportError:
        return {"ok": None, "detail": "web3 not installed — `pip install web3` to use --anchor"}
    try:
        w3 = Web3(Web3.HTTPProvider(rpc))
        receipt = w3.eth.get_transaction_receipt(tx_hash)
        sig = _norm_hex(w3.keccak(text="Anchored(address,bytes32,uint256)").hex())
    except Exception as exc:  # noqa: BLE001 — any RPC/tx error → couldn't verify
        return {"ok": None, "detail": f"could not query the chain: {exc}"}

    want = _norm_hex(expected_root)
    for lg in receipt["logs"]:
        topics = [_norm_hex(t.hex() if hasattr(t, "hex") else t) for t in lg["topics"]]
        if not topics or topics[0] != sig:
            continue
        if contract and _norm_hex(lg["address"]) != _norm_hex(contract):
            continue
        if len(topics) >= 3 and topics[2] == want:   # topic[2] = indexed bytes32 root
            return {"ok": True, "address": lg["address"],
                    "block_number": receipt.get("blockNumber"),
                    "detail": "Anchored(root) event confirmed on-chain"}
    return {"ok": False, "detail": "no matching Anchored(root) event in that transaction"}


# ─── CLI ──────────────────────────────────────────────────────────────────────

def _print_human(result, anchor_off, anchor_live):
    # ASCII-only markers so the tool never crashes on a non-UTF-8 console (Windows
    # cp1252, cp437, …) — it must run for anyone, anywhere.
    if result["ok"]:
        print(f"[OK]   chain intact - {result['count']} rows verified from genesis")
        if result["count"]:
            print(f"       head @ seq {result['head_seq']} = {result['head']}")
    else:
        print(f"[FAIL] CHAIN BROKEN at seq {result['first_broken_seq']} - {result['detail']}")

    if anchor_off is None:
        print("[--]   no anchor receipt in this export")
    elif anchor_off["matches"]:
        print(f"[OK]   anchor receipt matches the chain @ seq {anchor_off['last_seq']}")
        print(f"       root {anchor_off['root_hash']} (chain={anchor_off['chain']})")
    else:
        print(f"[FAIL] anchor receipt DOES NOT MATCH the chain @ seq {anchor_off['last_seq']}")
        print(f"       receipt {anchor_off['root_hash']} vs recomputed {anchor_off['recomputed']}")

    if anchor_live is not None:
        if anchor_live.get("ok") is True:
            print(f"[OK]   ON-CHAIN: {anchor_live['detail']} "
                  f"(block {anchor_live.get('block_number')})")
        elif anchor_live.get("ok") is False:
            print(f"[FAIL] ON-CHAIN: {anchor_live['detail']}")
        else:
            print(f"[--]   on-chain check skipped: {anchor_live['detail']}")


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(
        description="Independently verify a Foxy Audit log export (no Foxy trust required).")
    ap.add_argument("export", help="path to the downloaded foxy-audit-logs.json")
    ap.add_argument("--anchor", action="store_true",
                    help="also confirm the anchor root live on the public chain (needs web3 + --rpc)")
    ap.add_argument("--rpc", help="EVM RPC URL for --anchor, e.g. https://rpc.sepolia.org")
    ap.add_argument("--contract", help="AnchorRegistry address to match (defaults to the export's)")
    ap.add_argument("--commitment-key", help="customer-owned HMAC key for a known-event sidecar")
    ap.add_argument("--events", help="JSON sidecar mapping event_id to prompt/response values")
    ap.add_argument("--json", action="store_true", help="machine-readable JSON output")
    args = ap.parse_args(argv)

    try:
        with open(args.export, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"could not read export: {exc}", file=sys.stderr)
        return 2

    result = verify_export(data)
    commitment_result = None
    if args.commitment_key and args.events:
        try:
            with open(args.events, encoding="utf-8") as f:
                commitment_result = verify_known_events(
                    data, json.load(f), args.commitment_key)
        except (OSError, json.JSONDecodeError) as exc:
            commitment_result = {"ok": False, "detail": f"could not read known-event sidecar: {exc}"}
    anchor_off = check_anchor_offline(data, result)
    anchor_live = None
    if args.anchor:
        a = data.get("anchor") or {}
        anchor_live = check_anchor_onchain(
            args.rpc, a.get("tx_hash"), a.get("root_hash"),
            args.contract or a.get("contract"))

    if args.json:
        print(json.dumps({"chain": result, "anchor_offline": anchor_off,
                          "anchor_onchain": anchor_live,
                          "commitments": commitment_result}, indent=2))
    else:
        _print_human(result, anchor_off, anchor_live)

    bad = ((not result["ok"])
           or (commitment_result is not None and not commitment_result["ok"])
           or (anchor_off is not None and not anchor_off["matches"])
           or (anchor_live is not None and anchor_live.get("ok") is False))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
