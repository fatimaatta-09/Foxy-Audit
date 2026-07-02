"""GET /v1/verify — recompute the caller's entire chain and report tampering.

This is the core tamper-evidence demo: if any historical row was altered, its
recomputed chain hash no longer matches the stored one, and every row after it
breaks too (avalanche effect). Returns the first broken sequence number.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from ..auth import resolve_org
from ..chain import GENESIS_HASH, compute_chain_hash
from ..db import get_db
from ..models import AuditLog, Organization
from ..schemas import VerifyResponse

router = APIRouter()


@router.get("/v1/verify", response_model=VerifyResponse)
def verify(
    org: Organization = Depends(resolve_org),
    db: Session = Depends(get_db),
):
    total = db.execute(
        select(func.count()).select_from(AuditLog).where(AuditLog.org_id == org.id)
    ).scalar_one()

    if total > 50000:
        return VerifyResponse(
            ok=False,
            count=total,
            first_broken_seq=None,
            detail="chain too long for full verify, use partial window"
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
        )
        if expected != row.chain_hash:
            return VerifyResponse(
                ok=False,
                count=total,
                first_broken_seq=row.seq,
                detail=f"chain hash mismatch at seq {row.seq}",
            )
        prev_hash = row.chain_hash

    return VerifyResponse(ok=True, count=total, first_broken_seq=None, detail="chain intact")
