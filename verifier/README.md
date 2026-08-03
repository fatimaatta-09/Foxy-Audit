# Foxy Audit — open-source verifier

Independently re-verify a Foxy Audit log export **without trusting Foxy**. A single,
dependency-free Python script that re-implements the hash-chain recipe from scratch and
recomputes your entire ledger from genesis, reporting the first tampered row (if any).

If Foxy — or anyone with database access — altered a historical interaction, the
recomputed chain hash for that row no longer matches the stored one, and every row after
it breaks too (avalanche effect). This tool proves that using only the public recipe
below and the export you downloaded — nothing else, no network, no Foxy code.

## Requirements

Python 3.9+ (standard library only). `web3` is optional — needed **only** for the live
`--anchor` on-chain check.

## Usage

1. In the dashboard, **Ledger → Export → JSON** (or `GET /v1/logs/export?format=json`) to
   download `foxy-audit-logs.json`.
2. Run:

```bash
python foxy_verify.py foxy-audit-logs.json
```

```
✓ chain intact — 512 rows verified from genesis
  head @ seq 512 = a3f9c17e…
✓ anchor receipt matches the chain @ seq 512
  root a3f9c17e… (chain=sepolia)
```

Exit code is `0` when intact, `1` when tampering or an anchor mismatch is found — so it
drops straight into CI. Add `--json` for machine-readable output.

### Live on-chain check (optional)

The offline check confirms the export's chain matches the anchor receipt **Foxy
included**. To confirm against the **public chain** instead of Foxy's word, verify the
anchoring transaction really emitted the root:

```bash
pip install web3
python foxy_verify.py foxy-audit-logs.json --anchor --rpc https://rpc.sepolia.org
```

This fetches the anchor transaction named in the export and confirms it emitted
`Anchored(root)` on the `AnchorRegistry` contract. It only applies to EVM-anchored orgs
(a stub-anchored export has no on-chain transaction to check).

## The recipe (versioned, and frozen at every version)

Each row declares its own `chain_version`, and each version is frozen forever — a new
one may only **add** a field, never reorder or remove one, so an export downloaded years
ago still verifies with today's script.

`chain_version` 1 — the original pipe-delimited blob:

```
Hₙ = SHA256( "org_id|prompt_hash|response_hash|token_count|policy_tag|seq"  [+ "|agent=<agent>"]  +  Hₙ₋₁ )
H₀ = "0" × 64   (genesis)
```

The `|agent=<agent>` segment is appended **only when the row has an agent**, so rows
logged before agent attribution hash identically.

`chain_version` 2 and up — canonical JSON of the event, then the same construction:

```
Hₙ = SHA256( json(event, sort_keys, separators=(",",":"), ensure_ascii)  +  Hₙ₋₁ )
```

| Version | Adds to the event |
|---------|-------------------|
| 2 | `event_id`, `client_id`, `client_seq`, `event_type`, `commitment_alg`, `event_metadata`, `pii_signals`, `occurred_at` |
| 3 | `chain_version` itself (so the declared format is bound too) |
| 4 | `verdict_hash` — `SHA256(json(local_verdict))` |

Only the SHA-256 hashes of your prompt/response are ever stored — never the raw text.

### Which verdict version 4 binds

`local_verdict` is the **deterministic** verdict, decided by policy rules on the row's
metadata at the moment it was recorded. That is what `verdict_hash` covers, and editing
it afterwards breaks the chain.

`gemini_verdict` is the **AI judge's** later grade. It is not bound, and cannot be: the
chain hash is fixed when the row is written, and the judge grades asynchronously
afterwards. Binding it would mean re-hashing rows after the fact — which would invalidate
every row after them. So the chain covers what the system *decided*; the model's opinion
sits beside it, clearly labelled, and this script does not check it.

## Trust model

| Check | When | Proves |
|-------|------|--------|
| Chain integrity | always | no historical row was altered (pure recompute) |
| Verdict binding | `chain_version` ≥ 4 | the row's local verdict is the one that was recorded — the digest is in the chain, and the verdict body still matches it |
| Anchor, offline | if a receipt is present | the export's chain matches the anchored root Foxy recorded |
| Anchor, on-chain (`--anchor`) | opt-in | that root really exists on a public chain, independent of Foxy |

`verifier/test_verify.py` cross-checks this script's recipe against the backend's real
`chain.py` on every run, so the two can never silently drift.
