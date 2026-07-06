# Anchoring Runbook — flipping prod from `stub` to real Sepolia (EVM)

Anchoring publishes each org's hash-chain head to a public chain so tampering is
externally provable, not just internally recomputable. Prod ships with
`ANCHOR_PROVIDER=stub` (deterministic, no external chain). This runbook flips it to
the real `evm` provider on Sepolia. **The flip spends live testnet gas from a funded
key** — do it deliberately.

The safety rails from 7C (`_ensure_wallet_funded`, `alert_on_anchor_problems`) make
the flip observable: a low wallet refuses to submit, and failed/stale anchors page
`ALERT_EMAIL`. Turn them on as part of the flip.

## Preconditions

- The `AnchorRegistry` contract is deployed on Sepolia at
  `0x4D4F3359cA43874f9e7e9E158f6E7209dD984F4E` (see `contracts/`).
- A funded Sepolia key (the account that will pay gas). Fund it from a faucet; keep
  a buffer above `ANCHOR_WALLET_MIN_BALANCE_WEI`.
- A Sepolia JSON-RPC URL (e.g. Alchemy/Infura). **It usually embeds an API key** —
  it is redacted from `chain_anchors.detail`, but still treat it as a secret.
- `web3` installed in the backend image (already a dependency for `evm`).

## The flip (VM `deploy/.env`)

Set these keys, then restart the worker:

```bash
ANCHOR_ENABLED=true
ANCHOR_PROVIDER=evm
ANCHOR_EVM_RPC_URL=https://eth-sepolia.g.alchemy.com/v2/<KEY>   # gitleaks:allow (placeholder, not a secret)
ANCHOR_EVM_CHAIN=sepolia
ANCHOR_EVM_PRIVATE_KEY=<funded-sepolia-key>   # gitleaks:allow (placeholder, not a secret)
ANCHOR_EVM_CONTRACT=0x4D4F3359cA43874f9e7e9E158f6E7209dD984F4E

# Safety rails — turn them on with the flip
ANCHOR_WALLET_MIN_BALANCE_WEI=10000000000000000   # 0.01 ETH floor; refuse below this
ANCHOR_STALE_ALERT_SECONDS=172800                 # alert if newest confirmed anchor > 48h old
ANCHOR_ALERT_COOLDOWN=3600
ALERT_EMAIL=ops@foxyaudit.tech                    # must be set for alerts to send
```

Note: a sub-hour per-tier cadence also needs a smaller `ANCHOR_INTERVAL_SECONDS`
(the sweep frequency bounds how often the cadence can fire).

Recreate the worker so it loads the new env. `docker compose restart` does NOT
re-read changed env values — you must `up` so the container is recreated. The
service is `foxy-worker` (runs `app.worker_main`). Run from the repo root:

```bash
docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env \
  up -d --force-recreate foxy-worker
docker compose -f deploy/docker-compose.prod.yml logs -f foxy-worker   # watch it come up
```

(Pushing to `main` also triggers CD, which runs `up --build -d` for the whole
stack and recreates the worker the same way.)

On startup you should see:
`Public-chain anchoring ON (provider=evm interval=3600s)`.

## Confirming it works

1. Trigger an anchor for an org with new logs — either wait for the sweep, or as an
   org admin `POST /v1/anchors` ("anchor now", not cadence-gated).
2. `GET /v1/anchors` should return a receipt with `chain: "sepolia"`,
   `status: "confirmed"`, a real `tx_hash`, and a `block_number`.
3. `GET /v1/verify` → `last_anchor.matches_current_chain: true`.
4. Optionally look up the `tx_hash` on https://sepolia.etherscan.io.

## When something is wrong

- **`status: "failed"`, detail mentions the balance floor** → the funded wallet is
  below `ANCHOR_WALLET_MIN_BALANCE_WEI`. Top it up from a faucet.
- **`status: "failed"`, other detail** → read `docker compose -f deploy/docker-compose.prod.yml logs foxy-worker` (RPC URL
  and key are redacted from the persisted detail, not from your own logs). Common
  causes: bad RPC URL, wrong contract address, RPC rate limit.
- **No new anchors appearing** → check `ANCHOR_ENABLED=true`, the worker restarted,
  and orgs actually have new logs since their last anchor. Within a tier's cadence
  window the automatic sweep intentionally waits.
- **Alert email fired** → an org's latest anchor is `failed` or the newest confirmed
  anchor is older than `ANCHOR_STALE_ALERT_SECONDS`. Investigate per above.

## Rolling back

Set `ANCHOR_PROVIDER=stub` (or `ANCHOR_ENABLED=false`) and recreate the worker
(`up -d --force-recreate foxy-worker`).
Existing real receipts remain valid and verifiable; new anchors go back to stub.
