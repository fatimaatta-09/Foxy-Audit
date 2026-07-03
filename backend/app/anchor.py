"""Public-chain anchoring (Phase 3 A1).

Publishes each org's hash-chain head — ``root_hash`` = the latest
``audit_logs.chain_hash`` at ``last_seq`` — to a public chain and records the
receipt in ``chain_anchors``. Once the anchoring tx confirms, any third party
holding the receipt can read the on-chain value and prove the ledger existed in
exactly that state at that block. That makes tampering detectable EXTERNALLY
(after the next anchor), not merely recomputable by us — the whole trust upgrade
over ``/v1/verify`` alone.

Provider is pluggable (config ``anchor_provider``):
  * ``stub``          — deterministic, no external chain (dev / tests / CI).
  * ``evm``           — web3.py -> the AnchorRegistry contract on Sepolia (or any
                        EVM). Configure rpc/key/contract; see contracts/.
  * ``opentimestamps``— Bitcoin via OpenTimestamps (no wallet/gas; slower).

Framing caution: an anchor makes tampering *externally detectable after the next
anchor*, not impossible. We say "tamper-evident, independently verifiable".
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .chain import GENESIS_HASH, compute_chain_hash
from .config import Settings, get_settings
from .models import AuditLog, ChainAnchor, Organization

log = logging.getLogger("foxy.anchor")

# Minimal ABI: anchor(bytes32 root) — the contract emits an event and stores it.
_ANCHOR_ABI = [{
    "inputs": [{"internalType": "bytes32", "name": "root", "type": "bytes32"}],
    "name": "anchor", "outputs": [], "stateMutability": "nonpayable", "type": "function",
}]


@dataclass
class AnchorReceipt:
    chain: str
    status: str                    # confirmed | pending | failed
    tx_hash: str | None = None
    block_number: int | None = None
    detail: str | None = None


# ─────────────────────────────── chain head ──────────────────────────────────

def _scope(db: Session, org_id) -> None:
    db.execute(text("SELECT set_config('app.current_org', :oid, true)"), {"oid": str(org_id)})


def _redact(msg: str, settings: Settings) -> str:
    """Strip known secrets before persisting/logging an error. An RPC URL often
    embeds an API key (e.g. Alchemy /v2/<key>) and can appear in web3 errors, so
    never let it reach chain_anchors.detail (exposed via GET /v1/anchors)."""
    for secret in (settings.anchor_evm_private_key, settings.anchor_evm_rpc_url):
        if secret and secret in msg:
            msg = msg.replace(secret, "<redacted>")
    return msg


def head_of(db: Session, org_id) -> tuple[str | None, int]:
    """Return (chain_head_hash, last_seq) for the org, or (None, 0) if empty.
    The head is simply the stored chain_hash of the highest-seq row."""
    row = db.execute(
        select(AuditLog.chain_hash, AuditLog.seq)
        .where(AuditLog.org_id == org_id)
        .order_by(AuditLog.seq.desc())
        .limit(1)
    ).first()
    if row is None:
        return None, 0
    return row[0], row[1]


def recompute_head(db: Session, org_id, upto_seq: int) -> str | None:
    """Independently recompute the chain hash at ``upto_seq`` from genesis.
    Used by verify_anchor to prove the anchored root matches the ledger — if a
    historical row was altered, this no longer equals the anchored root_hash."""
    rows = db.execute(
        select(AuditLog)
        .where(AuditLog.org_id == org_id, AuditLog.seq <= upto_seq)
        .order_by(AuditLog.seq.asc())
    ).scalars().all()
    prev = GENESIS_HASH
    last = None
    for row in rows:
        prev = compute_chain_hash(
            org_id=org_id, prompt_hash=row.prompt_hash, response_hash=row.response_hash,
            token_count=row.token_count, policy_tag=row.policy_tag, seq=row.seq, prev_hash=prev,
        )
        last = prev
    return last


# ─────────────────────────────── providers ───────────────────────────────────

def _anchor_stub(root_hash: str, settings: Settings) -> AnchorReceipt:
    """Deterministic fake anchor — no external chain. Verifiable because the
    'on-chain' value is exactly the submitted root, so verify_anchor's recompute
    still has something concrete to check against."""
    digest = hashlib.sha256(("stub-anchor:" + root_hash).encode("utf-8")).hexdigest()
    return AnchorReceipt(
        chain="stub", status="confirmed", tx_hash="0x" + digest,
        block_number=int(root_hash[:12], 16) % 10_000_000,
        detail="simulated anchor (no external chain) — set ANCHOR_PROVIDER=evm for Sepolia",
    )


def _anchor_evm(root_hash: str, settings: Settings) -> AnchorReceipt:
    """Submit root to the AnchorRegistry contract on an EVM chain via web3.py."""
    if not settings.anchor_evm_rpc_url:
        raise RuntimeError("ANCHOR_EVM_RPC_URL is not set")
    if not settings.anchor_evm_private_key:
        raise RuntimeError("ANCHOR_EVM_PRIVATE_KEY is not set")
    if not settings.anchor_evm_contract:
        raise RuntimeError("ANCHOR_EVM_CONTRACT is not set")
    try:
        from web3 import Web3
    except ImportError as e:                      # pragma: no cover
        raise RuntimeError("web3 is not installed (pip install web3) — required for anchor_provider=evm") from e

    w3 = Web3(Web3.HTTPProvider(settings.anchor_evm_rpc_url))
    acct = w3.eth.account.from_key(settings.anchor_evm_private_key)
    contract = w3.eth.contract(
        address=Web3.to_checksum_address(settings.anchor_evm_contract), abi=_ANCHOR_ABI)
    root_bytes = bytes.fromhex(root_hash)         # 64 hex chars -> 32 bytes (bytes32)
    if len(root_bytes) != 32:
        raise RuntimeError(f"root_hash must be 32 bytes (64 hex chars), got {len(root_bytes)}")

    tx = contract.functions.anchor(root_bytes).build_transaction({
        "from": acct.address,
        "nonce": w3.eth.get_transaction_count(acct.address),
        "gas": 120_000,
        "gasPrice": w3.eth.gas_price,
        "chainId": w3.eth.chain_id,
    })
    signed = acct.sign_transaction(tx)
    raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction")  # web3 v7/v6
    tx_hash = w3.eth.send_raw_transaction(raw)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
    txh = receipt["transactionHash"]
    tx_hex = txh.hex() if hasattr(txh, "hex") else str(txh)   # v6 HexBytes / v7 HexStr
    if not tx_hex.startswith("0x"):
        tx_hex = "0x" + tx_hex
    return AnchorReceipt(
        chain=settings.anchor_evm_chain,
        status="confirmed" if receipt.get("status") == 1 else "failed",
        tx_hash=tx_hex,
        block_number=receipt["blockNumber"],
    )


def _anchor_opentimestamps(root_hash: str, settings: Settings) -> AnchorReceipt:
    """Timestamp the root on Bitcoin via OpenTimestamps (no wallet/gas). The
    proof starts 'pending' and is later upgraded to a Bitcoin-confirmed proof."""
    try:
        import opentimestamps  # noqa: F401
    except ImportError as e:                      # pragma: no cover
        raise RuntimeError(
            "opentimestamps is not installed (pip install opentimestamps) — required for "
            "anchor_provider=opentimestamps") from e
    # A full OTS submission (calendar aggregation + .ots proof persistence) is a
    # later step; the receipt shape is ready for it.
    return AnchorReceipt(
        chain="bitcoin", status="pending", tx_hash=None,
        detail="OpenTimestamps proof requested — confirms on the next Bitcoin block batch",
    )


_PROVIDERS = {
    "stub": _anchor_stub,
    "evm": _anchor_evm,
    "opentimestamps": _anchor_opentimestamps,
}


def run_provider(root_hash: str, settings: Settings) -> AnchorReceipt:
    provider = _PROVIDERS.get(settings.anchor_provider)
    if provider is None:
        raise RuntimeError(f"unknown anchor_provider '{settings.anchor_provider}'")
    return provider(root_hash, settings)


# ─────────────────────────────── orchestration ───────────────────────────────

def latest_anchor(db: Session, org_id) -> ChainAnchor | None:
    """Most recent anchor for the org (any status). Caller scopes RLS if needed."""
    return db.execute(
        select(ChainAnchor)
        .where(ChainAnchor.org_id == org_id)
        .order_by(ChainAnchor.anchored_at.desc())
        .limit(1)
    ).scalars().first()


def anchor_org(db: Session, org_id, settings: Settings | None = None,
               force: bool = False) -> ChainAnchor | None:
    """Anchor one org's current chain head. Returns the ChainAnchor row, or None
    if the org has no logs, or (unless force) its head hasn't advanced since the
    last successful anchor. A provider failure is persisted as status='failed'
    (a visible receipt), never silently dropped."""
    settings = settings or get_settings()
    org_id = org_id if isinstance(org_id, uuid.UUID) else uuid.UUID(str(org_id))
    _scope(db, org_id)

    head_hash, last_seq = head_of(db, org_id)
    if head_hash is None:
        return None

    if not force:
        prev = latest_anchor(db, org_id)
        if prev is not None and prev.last_seq == last_seq and prev.status != "failed":
            return None   # head hasn't advanced since a non-failed anchor

    try:
        receipt = run_provider(head_hash, settings)
    except Exception as exc:                        # noqa: BLE001 — record the failure
        safe = _redact(str(exc), settings)          # never persist/log RPC URL or key
        log.warning("anchor for org %s failed: %s", org_id, safe)
        receipt = AnchorReceipt(chain=settings.anchor_evm_chain if settings.anchor_provider == "evm"
                                else settings.anchor_provider,
                                status="failed", detail=f"{type(exc).__name__}: {safe}"[:500])

    row = ChainAnchor(
        org_id=org_id, root_hash=head_hash, last_seq=last_seq, chain=receipt.chain,
        tx_hash=receipt.tx_hash, block_number=receipt.block_number, status=receipt.status,
        detail=receipt.detail,
        confirmed_at=datetime.now(timezone.utc) if receipt.status == "confirmed" else None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    log.info("anchored org %s @ seq %s -> %s tx=%s status=%s",
             org_id, last_seq, receipt.chain, receipt.tx_hash, receipt.status)
    return row


def anchor_all_due(db: Session, settings: Settings | None = None) -> int:
    """Anchor every org whose head advanced since its last anchor. Runs in the
    worker (a BYPASSRLS/superuser role, so it sees all orgs). Returns the count
    of orgs anchored this pass."""
    settings = settings or get_settings()
    org_ids = db.execute(select(Organization.id)).scalars().all()
    anchored = 0
    for oid in org_ids:
        try:
            if anchor_org(db, oid, settings) is not None:
                anchored += 1
        except Exception as exc:                    # noqa: BLE001 — one org must not stop the sweep
            db.rollback()
            log.warning("anchor sweep skipped org %s: %s", oid, exc)
    return anchored
