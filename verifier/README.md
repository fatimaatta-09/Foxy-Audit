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

## The recipe (frozen)

Each row's hash is:

```
Hₙ = SHA256( "org_id|prompt_hash|response_hash|token_count|policy_tag|seq"  [+ "|agent=<agent>"]  +  Hₙ₋₁ )
H₀ = "0" × 64   (genesis)
```

The `|agent=<agent>` segment is appended **only when the row has an agent**, so rows
logged before agent attribution hash identically. Only the SHA-256 hashes of your
prompt/response are ever stored — never the raw text.

## Trust model

| Check | When | Proves |
|-------|------|--------|
| Chain integrity | always | no historical row was altered (pure recompute) |
| Anchor, offline | if a receipt is present | the export's chain matches the anchored root Foxy recorded |
| Anchor, on-chain (`--anchor`) | opt-in | that root really exists on a public chain, independent of Foxy |

`verifier/test_verify.py` cross-checks this script's recipe against the backend's real
`chain.py` on every run, so the two can never silently drift.
