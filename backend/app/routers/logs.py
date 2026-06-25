"""POST /v1/logs — ingest one interaction's metadata into the hash chain.

Sequence (all in one transaction):
  1. Lock this org's tail row (FOR UPDATE) to serialize concurrent inserts and
     prevent the chain from forking.
  2. Compute the new chain hash from the previous row's hash.
  3. Grade the metadata with Gemini (inline, so the verdict is returned to the SDK
     — which then drives the desktop fox's red alert on a breach).
  4. Insert the row and commit once.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import gemini
from ..auth import require_org
from ..chain import GENESIS_HASH, compute_chain_hash
from ..db import get_db
from ..models import AuditLog, Organization
from ..schemas import LogIngest, LogResponse, Verdict

router = APIRouter()


@router.post("/v1/logs", response_model=LogResponse)
def ingest(
    payload: LogIngest,
    org: Organization = Depends(require_org),
    db: Session = Depends(get_db),
):
    prev = db.execute(
        select(AuditLog.seq, AuditLog.chain_hash)
        .where(AuditLog.org_id == org.id)
        .order_by(AuditLog.seq.desc())
        .limit(1)
        .with_for_update()
    ).first()

    prev_seq = prev.seq if prev else 0
    prev_hash = prev.chain_hash if prev else GENESIS_HASH
    seq = prev_seq + 1

    chain_hash = compute_chain_hash(
        org_id=org.id,
        prompt_hash=payload.prompt_hash,
        response_hash=payload.response_hash,
        token_count=payload.token_count,
        policy_tag=payload.policy_tag,
        seq=seq,
        prev_hash=prev_hash,
    )

    verdict: Verdict = gemini.evaluate(
        {
            "prompt_hash": payload.prompt_hash,
            "response_hash": payload.response_hash,
            "token_count": payload.token_count,
            "policy_tag": payload.policy_tag,
        }
    )

    row = AuditLog(
        org_id=org.id,
        seq=seq,
        prompt_hash=payload.prompt_hash,
        response_hash=payload.response_hash,
        token_count=payload.token_count,
        policy_tag=payload.policy_tag,
        prev_hash=prev_hash,
        chain_hash=chain_hash,
        gemini_verdict=verdict.model_dump(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    return LogResponse(log_id=row.id, seq=seq, chain_hash=chain_hash, verdict=verdict)
