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
                  "prev_hash": "…", "chain_hash": "…",
                  # chain_version 4 and later:
                  "verdict_hash": "…", "local_verdict": {…} }, … ]
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
                       pii_signals=None, occurred_at=None, verdict_hash=None):
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
        if chain_version >= 3:
            event["chain_version"] = chain_version
        # V4 binds the digest of the row's LOCAL, deterministic verdict (the one
        # decided at ingest). The AI judge's grade arrives later and is NOT bound
        # — see verdict_hash_hex below for what that means for a reader.
        if chain_version >= 4:
            event["verdict_hash"] = verdict_hash
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
        pii_signals=row.get("pii_signals"), occurred_at=row.get("occurred_at"),
        verdict_hash=row.get("verdict_hash"))


def verdict_hash_hex(verdict):
    """Re-derive the digest a V4 row binds, from the verdict body it exported.

    The chain binds `verdict_hash`, not the verdict itself, so the chain recompute
    alone would still pass if someone rewrote `local_verdict` and left the digest
    in place. Comparing this against the stored `verdict_hash` closes that: the
    verdict body is tamper-evident too.

    `local_verdict` is the LOCAL, deterministic verdict — decided by policy rules
    at ingest. `gemini_verdict` is the AI judge's later, advisory grade; it is not
    hashed and this tool does not check it.
    """
    canonical = json.dumps(verdict, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def commitment_hex(value, key, salt=None):
    """Match the SDK's HMAC commitment for customer-held known content.

    `salt` mirrors the SDK's optional per-event salt, mixed in CANONICALLY —
    HMAC(key, {"s": salt, "v": <canonical value>}) — never by concatenation.
    Omitting it reproduces the pre-salt digest byte for byte, which is what keeps
    every row written before salting existed verifiable forever.

    The salt never reaches Foxy. It lives only in the customer's own sidecar, so
    this check is the ONLY thing that needs it: `verify_export` below recomputes
    the chain from stored field values and never re-derives a hash from plaintext.
    """
    canonical = json.dumps(value, ensure_ascii=True, sort_keys=True,
                           separators=(",", ":"), default=str)
    if salt:
        canonical = json.dumps({"s": str(salt), "v": canonical},
                               ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hmac.new(str(key).encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()


def _is_salted(row):
    return str(row.get("commitment_alg") or "").endswith("-salted")


def verify_known_events(data, known_events, key):
    """Check commitments using a customer-owned sidecar without sending content to Foxy.

    Sidecar entries are keyed by event_id and hold {"prompt", "response"} plus, for
    a salted row, the "salt" the SDK recorded locally when the event was created.

    A salted row whose sidecar entry has NO salt is reported as `unprovable`, not
    as a mismatch and not as a pass: without the salt this tool cannot recompute
    the commitment at all, and calling that "tampered" would be a false accusation
    while calling it "verified" would be a lie. Same convention as the on-chain
    check — could-not-run is its own answer.
    """
    checked = 0
    unprovable = []
    for row in data.get("logs", []):
        event_id = row.get("event_id")
        known = known_events.get(str(event_id)) if event_id else None
        if not known:
            continue
        # An unsalted row hashes unsalted even if the sidecar carries a salt.
        salt = known.get("salt") if _is_salted(row) else None
        if _is_salted(row) and not salt:
            unprovable.append(str(event_id))
            continue
        if commitment_hex(known.get("prompt", ""), key, salt) != row.get("prompt_hash"):
            return {"ok": False, "event_id": str(event_id), "field": "prompt_hash",
                    "checked": checked, "unprovable": unprovable}
        if commitment_hex(known.get("response", ""), key, salt) != row.get("response_hash"):
            return {"ok": False, "event_id": str(event_id), "field": "response_hash",
                    "checked": checked, "unprovable": unprovable}
        checked += 1
    return {"ok": True, "checked": checked, "unprovable": unprovable}


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
        # The chain binds the verdict's DIGEST; this binds the digest to the body.
        # Without it, `local_verdict` could be rewritten and the chain still pass.
        if row.get("local_verdict") is not None:
            if verdict_hash_hex(row["local_verdict"]) != row.get("verdict_hash"):
                return {"ok": False, "count": len(rows), "first_broken_seq": row["seq"],
                        "detail": f"local verdict does not match its bound hash at seq {row['seq']}",
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

def _load_sidecar(path):
    """Read a known-event sidecar in either shape.

    * one JSON object keyed by event_id — what a customer hand-writes; or
    * JSON Lines, `{"event_id": …, "salt": …}` per line, which is what the SDK
      appends. It can only append: it knows the salt when the event happens and
      never rewrites the file.

    Lines merge per event_id, later wins, so a customer can append their own
    `{"event_id": …, "prompt": …, "response": …}` line beside the SDK's salt line
    instead of editing one big object.
    """
    with open(path, encoding="utf-8") as f:
        text = f.read()
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError:
        loaded = None
    # A top-level "event_id" means this is a single JSONL line, not an id → entry map.
    if isinstance(loaded, dict) and "event_id" not in loaded:
        return loaded
    merged = {}
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        entry = json.loads(line)
        event_id = str(entry.get("event_id") or "")
        if not event_id:
            raise ValueError(f"sidecar line {n} has no event_id")
        merged.setdefault(event_id, {}).update(
            {k: v for k, v in entry.items() if k != "event_id"})
    return merged


def _print_human(result, anchor_off, anchor_live, commitments=None):
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

    # The known-content check used to set the exit code while printing NOTHING
    # here, so a mismatch looked like a clean run that mysteriously returned 1.
    if commitments is not None:
        if commitments.get("detail"):
            print(f"[FAIL] known-content check: {commitments['detail']}")
        elif commitments["ok"]:
            print(f"[OK]   {commitments['checked']} known event(s) match their commitments")
        else:
            print(f"[FAIL] {commitments['field']} does NOT match the known content "
                  f"for event {commitments['event_id']}")
        skipped = len(commitments.get("unprovable") or [])
        if skipped:
            print(f"[--]   {skipped} salted event(s) not checked - no salt in the sidecar, "
                  f"so the commitment cannot be recomputed (--json lists them)")


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
    ap.add_argument("--events",
                    help="known-event sidecar: a JSON object keyed by event_id, or the "
                         "JSONL file the SDK appends salts to. Entries hold prompt/response "
                         "and, for a salted row, the salt (which never leaves your machine)")
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
            commitment_result = verify_known_events(
                data, _load_sidecar(args.events), args.commitment_key)
        except (OSError, ValueError) as exc:   # ValueError covers JSONDecodeError
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
        _print_human(result, anchor_off, anchor_live, commitment_result)

    bad = ((not result["ok"])
           or (commitment_result is not None and not commitment_result["ok"])
           or (anchor_off is not None and not anchor_off["matches"])
           or (anchor_live is not None and anchor_live.get("ok") is False))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
