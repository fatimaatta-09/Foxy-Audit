# AnchorRegistry — public-chain anchoring (Phase 3 A1)

`AnchorRegistry.sol` is the on-chain half of Foxy Audit's tamper-evidence story.
The backend (`app/anchor.py`, `anchor_provider=evm`) calls `anchor(bytes32 root)`
with each org's hash-chain head; the contract emits an `Anchored` event so the
publication is a permanent, publicly-verifiable record. It **stores nothing** —
the calldata + event log are the proof, which keeps gas tiny.

## Trust framing (read this)

An anchor makes tampering **externally detectable after the next anchor** — not
impossible. Anyone holding an anchor receipt can prove the ledger existed in that
exact state at that block, so a later silent rewrite of history is caught. Market
it as **"tamper-evident, independently verifiable"**, never "immutable".

## The app runs fine without any of this

Default `ANCHOR_PROVIDER=stub` needs no chain, wallet, or RPC — the whole anchor
flow (record → `/v1/anchors` → `verify_anchor.py` → passport) is exercised
locally and in CI. Going live on Sepolia is a **config change, no code change**.

## Deploy to Sepolia (~20 min, one-time, free)

1. **RPC URL** — a free endpoint from Alchemy/Infura, or a public Sepolia RPC.
2. **Funded key** — a fresh testnet account; get free ETH from a Sepolia faucet.
   Use a throwaway key; it only ever pays testnet gas.
3. **Deploy the contract** — easiest via [Remix](https://remix.ethereum.org):
   paste `AnchorRegistry.sol`, compile (0.8.20+), deploy with
   "Injected Provider" on the Sepolia network, and copy the deployed address.
   (Or Foundry: `forge create contracts/AnchorRegistry.sol:AnchorRegistry
   --rpc-url $RPC --private-key $KEY`.)
4. **Point the backend at it** and flip the provider:

   ```env
   ANCHOR_ENABLED=true
   ANCHOR_PROVIDER=evm
   ANCHOR_EVM_RPC_URL=https://eth-sepolia.g.alchemy.com/v2/<key>
   ANCHOR_EVM_CHAIN=sepolia
   ANCHOR_EVM_PRIVATE_KEY=0x<funded-testnet-key>
   ANCHOR_EVM_CONTRACT=0x<deployed-AnchorRegistry-address>
   ```

5. Restart the worker. It anchors each org's head every
   `ANCHOR_INTERVAL_SECONDS`. Trigger one immediately with
   `POST /v1/anchors` (admin), see it in `GET /v1/anchors`, and confirm it on
   Etherscan via the returned `tx_hash`.

## Independent verification

```bash
python scripts/verify_anchor.py --key foxy_sk_...
```

Recomputes the chain up to the anchored seq and asserts it equals the anchored
root; for an `evm` anchor it also reads the anchoring tx back and compares the
on-chain root. Exit 0 = verified.
