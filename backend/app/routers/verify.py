"""GET /v1/verify — recompute the caller's entire chain and report tampering.

This is the core tamper-evidence demo: if any historical row was altered, its
recomputed chain hash no longer matches the stored one, and every row after it
breaks too (avalanche effect). Returns the first broken sequence number.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from ..anchor import latest_anchor, recompute_head
from ..auth import resolve_org
from ..chain import GENESIS_HASH, compute_chain_hash
from ..db import get_db
from ..models import AuditLog, Organization
from ..schemas import LastAnchor, VerifyResponse

router = APIRouter()


def _last_anchor(db, org_id, cross_check: bool) -> LastAnchor | None:
    """Build the LastAnchor block. When cross_check is set, recompute the chain
    up to the anchored seq and compare to the anchored root; skipped for very
    long chains (same guard as full verify) so /v1/verify never does an
    unbounded recompute on every call — matches_current_chain stays None then."""
    a = latest_anchor(db, org_id)
    if a is None:
        return None
    matches = None
    if cross_check:
        try:
            matches = (recompute_head(db, org_id, a.last_seq) == a.root_hash)
        except Exception:  # pragma: no cover — never let the cross-check break verify
            matches = None
    return LastAnchor(
        chain=a.chain, status=a.status, root_hash=a.root_hash, last_seq=a.last_seq,
        tx_hash=a.tx_hash, block_number=a.block_number,
        anchored_at=a.anchored_at.isoformat() if a.anchored_at else None,
        matches_current_chain=matches,
    )


@router.get("/v1/verify", response_model=VerifyResponse)
def verify(
    org: Organization = Depends(resolve_org),
    db: Session = Depends(get_db),
):
    total = db.execute(
        select(func.count()).select_from(AuditLog).where(AuditLog.org_id == org.id)
    ).scalar_one()

    anchor = _last_anchor(db, org.id, cross_check=(total <= 50000))

    if total > 50000:
        return VerifyResponse(
            ok=False,
            count=total,
            first_broken_seq=None,
            detail="chain too long for full verify, use partial window",
            last_anchor=anchor,
        )

    rows = db.execute(
        select(AuditLog).where(AuditLog.org_id == org.id).order_by(AuditLog.seq.asc())
    ).scalars().yield_per(1000)

    prev_hash = GENESIS_HASH
    for row in rows:
        expected = compute_chain_hash(
            org_id=org.id,
            prompt_hash=row.prompt_hash,
            response_hash=row.response_hash,
            token_count=row.token_count,
            policy_tag=row.policy_tag,
            seq=row.seq,
            prev_hash=prev_hash,
            agent=row.agent,
        )
        if expected != row.chain_hash:
            return VerifyResponse(
                ok=False,
                count=total,
                first_broken_seq=row.seq,
                detail=f"chain hash mismatch at seq {row.seq}",
                last_anchor=anchor,
            )
        prev_hash = row.chain_hash

    return VerifyResponse(ok=True, count=total, first_broken_seq=None,
                          detail="chain intact", last_anchor=anchor)


@router.get("/v1/verify/hash/{chain_hash}")
def verify_hash(
    chain_hash: str,
    org: Organization = Depends(resolve_org),
    db: Session = Depends(get_db),
):
    """Look up a SINGLE ledger entry by its chain hash and report whether it belongs
    to the caller's org, its verdict, and whether that row still recomputes intact.
    Backs the dashboard "paste any hash to check status" card with real data (there
    is no per-hash lookup elsewhere; /v1/verify recomputes the whole chain)."""
    h = (chain_hash or "").strip().lower()
    row = db.execute(
        select(AuditLog).where(AuditLog.org_id == org.id, AuditLog.chain_hash == h)
    ).scalar_one_or_none()
    if row is None:
        return {"found": False, "chain_hash": h}

    # Recompute THIS row's hash (needs the predecessor's stored hash) so we can report
    # verified vs tampered without recomputing the entire chain.
    prev_hash = GENESIS_HASH
    if row.seq > 1:
        prev_hash = db.execute(
            select(AuditLog.chain_hash)
            .where(AuditLog.org_id == org.id, AuditLog.seq == row.seq - 1)
        ).scalar_one_or_none() or GENESIS_HASH
    expected = compute_chain_hash(
        org_id=org.id, prompt_hash=row.prompt_hash, response_hash=row.response_hash,
        token_count=row.token_count, policy_tag=row.policy_tag, seq=row.seq,
        prev_hash=prev_hash, agent=row.agent,
    )
    verified = (expected == row.chain_hash)

    verdict = row.gemini_verdict or {}
    if not verified:
        status = "tampered"
    elif row.grading_status == "graded" and verdict.get("policy_breach"):
        status = "breach"
    elif row.grading_status == "graded":
        status = "safe"
    elif row.grading_status == "failed":
        status = "flag"
    else:
        status = "pending"

    return {
        "found": True,
        "seq": row.seq,
        "chain_hash": row.chain_hash,
        "policy_tag": row.policy_tag,
        "agent": row.agent,
        "verified": verified,
        "status": status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
