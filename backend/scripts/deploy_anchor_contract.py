"""Compile + deploy AnchorRegistry.sol to the configured EVM chain (Phase 3 A1).

    python scripts/deploy_anchor_contract.py

Reads ANCHOR_EVM_RPC_URL / ANCHOR_EVM_PRIVATE_KEY / ANCHOR_EVM_CHAIN from config
(backend/.env), compiles the contract with solcx, deploys it, and prints the
address to set as ANCHOR_EVM_CONTRACT. A CLI alternative to Remix.

Requires: web3, py-solc-x.  The deployer account must hold a little (test) ETH.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import get_settings  # noqa: E402

_SOLC = "0.8.20"
_CONTRACT_SOL = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "contracts", "AnchorRegistry.sol"))


def main() -> None:
    s = get_settings()
    if not s.anchor_evm_rpc_url or not s.anchor_evm_private_key:
        print("ERROR: set ANCHOR_EVM_RPC_URL and ANCHOR_EVM_PRIVATE_KEY (backend/.env)",
              file=sys.stderr)
        sys.exit(1)

    import solcx
    from web3 import Web3

    try:
        solcx.set_solc_version(_SOLC)
    except Exception:
        print(f"Installing solc {_SOLC} ...")
        solcx.install_solc(_SOLC)
        solcx.set_solc_version(_SOLC)

    source = open(_CONTRACT_SOL, encoding="utf-8").read()
    compiled = solcx.compile_source(source, output_values=["abi", "bin"])
    key = next(k for k in compiled if k.endswith(":AnchorRegistry"))
    abi, bytecode = compiled[key]["abi"], compiled[key]["bin"]

    w3 = Web3(Web3.HTTPProvider(s.anchor_evm_rpc_url))
    if not w3.is_connected():
        print("ERROR: cannot reach ANCHOR_EVM_RPC_URL", file=sys.stderr)
        sys.exit(1)
    acct = w3.eth.account.from_key(s.anchor_evm_private_key)
    bal = w3.eth.get_balance(acct.address)
    print(f"Deployer : {acct.address}")
    print(f"Balance  : {w3.from_wei(bal, 'ether')} ETH   chainId: {w3.eth.chain_id}")
    if bal == 0:
        print("ERROR: deployer has 0 ETH — fund it at a Sepolia faucet first.",
              file=sys.stderr)
        sys.exit(1)

    # EIP-1559 fees: a legacy gasPrice at/below the base fee gets dropped on
    # Sepolia. maxFee = 2*base + priority gives headroom for base-fee drift.
    base = w3.eth.get_block("latest").get("baseFeePerGas")
    priority = w3.to_wei(2, "gwei")
    fees = ({"maxPriorityFeePerGas": priority, "maxFeePerGas": base * 2 + priority}
            if base is not None else {"gasPrice": w3.eth.gas_price})

    Contract = w3.eth.contract(abi=abi, bytecode=bytecode)
    tx = Contract.constructor().build_transaction({
        "from": acct.address,
        "nonce": w3.eth.get_transaction_count(acct.address),
        "chainId": w3.eth.chain_id,
        **fees,
    })
    signed = acct.sign_transaction(tx)
    raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction")
    txh = w3.eth.send_raw_transaction(raw)
    txh_hex = txh.hex() if hasattr(txh, "hex") else str(txh)
    if not txh_hex.startswith("0x"):
        txh_hex = "0x" + txh_hex
    print(f"Deploy tx: {txh_hex}  (waiting for receipt, up to ~5 min) ...")
    receipt = w3.eth.wait_for_transaction_receipt(txh, timeout=300)
    if receipt.get("status") != 1:
        print("ERROR: deployment reverted", file=sys.stderr)
        sys.exit(1)

    addr = receipt["contractAddress"]
    print()
    print("AnchorRegistry deployed OK")
    print(f"  ANCHOR_EVM_CONTRACT={addr}")
    if s.anchor_evm_chain == "sepolia":
        print(f"  Etherscan: https://sepolia.etherscan.io/address/{addr}")


if __name__ == "__main__":
    main()
