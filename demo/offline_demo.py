"""Run a no-network, no-LLM Foxy Audit judge demo.

This exercises the customer-owned commitment check and the independent verifier
against a small synthetic export. It deliberately edits one historical row so a
judge can see the first broken sequence without Postgres, an API key, or a model.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "verifier"))

import foxy_verify as verifier  # noqa: E402


ORG_ID = "11111111-2222-3333-4444-555555555555"
DEMO_KEY = "judge-only-demo-key"
EVENTS = (
    ("evt-001", "Summarize this synthetic visit", "Synthetic visit summary"),
    ("evt-002", "Check this synthetic claim", "Synthetic claim review"),
    ("evt-003", "Draft a synthetic follow-up", "Synthetic follow-up draft"),
)


def build_export() -> tuple[dict, dict]:
    rows = []
    known_events = {}
    previous = verifier.GENESIS_HASH

    for seq, (event_id, prompt, response) in enumerate(EVENTS, start=1):
        known_events[event_id] = {"prompt": prompt, "response": response}
        row = {
            "seq": seq,
            "prev_hash": previous,
            "event_id": event_id,
            "client_id": "judge-demo-client",
            "client_seq": seq,
            "event_type": "interaction",
            "commitment_alg": "hmac-sha256",
            "prompt_hash": verifier.commitment_hex(prompt, DEMO_KEY),
            "response_hash": verifier.commitment_hex(response, DEMO_KEY),
            "token_count": 12 + seq,
            "policy_tag": "demo",
            "agent": "offline-simulator",
            "event_metadata": {"provider": "offline-demo"},
            "pii_signals": [],
            "occurred_at": f"2026-07-19T12:00:0{seq}+00:00",
            "chain_version": 2,
        }
        row["chain_hash"] = verifier.compute_chain_hash(
            org_id=ORG_ID,
            prev_hash=previous,
            **{key: row[key] for key in (
                "prompt_hash", "response_hash", "token_count", "policy_tag",
                "seq", "agent", "event_id", "client_id", "client_seq",
                "event_type", "commitment_alg", "event_metadata", "pii_signals",
                "occurred_at", "chain_version")},
        )
        rows.append(row)
        previous = row["chain_hash"]

    export = {
        "org_id": ORG_ID,
        "count": len(rows),
        "logs": rows,
        "anchor": {
            "chain": "offline-demo",
            "status": "confirmed",
            "root_hash": previous,
            "last_seq": len(rows),
        },
    }
    return export, known_events


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="also write the synthetic export, sidecar, and tampered export here",
    )
    args = parser.parse_args()

    export, known_events = build_export()
    chain_result = verifier.verify_export(export)
    commitment_result = verifier.verify_known_events(export, known_events, DEMO_KEY)
    anchor_result = verifier.check_anchor_offline(export, chain_result)

    tampered = copy.deepcopy(export)
    tampered["logs"][1]["token_count"] += 1
    tamper_result = verifier.verify_export(tampered)

    checks = {
        "chain": chain_result.get("ok") is True,
        # Assert the FIELDS, not the whole dict. Exact-equality against a result
        # shape breaks whenever the verifier learns to report something new: H2
        # added `unprovable` (a salted row whose sidecar has no salt), the
        # commitments here verified perfectly, and this line still went red.
        # Naming the fields is also stricter than the old comparison was — it now
        # requires the unprovable list to be empty rather than merely absent.
        "commitments": (
            commitment_result.get("ok") is True
            and commitment_result.get("checked") == len(EVENTS)
            and not commitment_result.get("unprovable")
        ),
        "anchor": bool(anchor_result and anchor_result.get("matches")),
        "tamper_detection": (
            tamper_result.get("ok") is False
            and tamper_result.get("first_broken_seq") == 2
        ),
    }

    print("Foxy Audit offline judge demo")
    print("  no LLM, API, database, network, or third-party package required")
    print(f"[{'PASS' if checks['chain'] else 'FAIL'}] chain verified: {chain_result['count']} rows")
    print(f"[{'PASS' if checks['commitments'] else 'FAIL'}] customer commitments verified: {commitment_result.get('checked', 0)}")
    print(f"[{'PASS' if checks['anchor'] else 'FAIL'}] offline receipt matches chain head")
    print(f"[{'PASS' if checks['tamper_detection'] else 'FAIL'}] tamper detected at sequence 2")

    if args.output_dir:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        write_json(args.output_dir / "foxy-audit-export.json", export)
        write_json(args.output_dir / "known-events.json", known_events)
        write_json(args.output_dir / "tampered-export.json", tampered)
        print(f"  artifacts: {args.output_dir.resolve()}")

    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
