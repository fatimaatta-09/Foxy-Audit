"""POST /v1/logs/batch — ingest a batch of interactions into the hash chain.
GET  /v1/logs, /v1/logs/{seq}, /v1/stats — dashboard reads.

Durable ingest (hybrid): the batch is written to the chain SYNCHRONOUSLY — each
row lands with grading_status='pending', committed before the 202 — so a crash
can never lose it. The expensive Gemini grading is deferred to the durable poller
in app/worker.py, which claims 'pending' rows and back-fills the verdict using the
org's policy config. Chain hashing stays cheap and inline; only grading is async.
"""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session
from starlette.status import HTTP_202_ACCEPTED

from ..auth import require_org, resolve_org
from ..chain import GENESIS_HASH, compute_chain_hash
from ..db import get_db
from ..models import AuditLog, Organization
from ..schemas import (
    ActivityDay, GradingCounts, LogIngest, LogListItem, LogListResponse,
    StatsResponse,
)

from slowapi import Limiter
from slowapi.util import get_remote_address


def _rate_limit_key(request: Request) -> str:
    auth = request.headers.get("Authorization")
    if auth:
        return auth
    return get_remote_address(request)


limiter = Limiter(key_func=_rate_limit_key)

router = APIRouter()

# Reads filter explicitly on org_id (the app DB role is a superuser that bypasses
# RLS, so the WHERE clause is what enforces tenant isolation — as in verify.py).
_BREACH = AuditLog.gemini_verdict["policy_breach"].astext == "true"


@router.post("/v1/logs/batch", status_code=HTTP_202_ACCEPTED)
@limiter.limit("60/minute")
def ingest_batch(
    request: Request,
    payload: List[LogIngest],
    org: Organization = Depends(require_org),
    db: Session = Depends(get_db),
):
    """Write the batch to the hash chain synchronously (durable). Each row lands
    grading_status='pending' (column default); the poller grades them async."""
    # Lock the org's tail row so concurrent batches can't fork the chain.
    prev = db.execute(
        select(AuditLog.seq, AuditLog.chain_hash)
        .where(AuditLog.org_id == org.id)
        .order_by(AuditLog.seq.desc())
        .limit(1)
        .with_for_update()
    ).first()
    prev_seq = prev.seq if prev else 0
    prev_hash = prev.chain_hash if prev else GENESIS_HASH

    rows = []
    for item in payload:
        seq = prev_seq + 1
        chain_hash = compute_chain_hash(
            org_id=org.id,
            prompt_hash=item.prompt_hash,
            response_hash=item.response_hash,
            token_count=item.token_count,
            policy_tag=item.policy_tag,
            seq=seq,
            prev_hash=prev_hash,
        )
        rows.append(AuditLog(
            org_id=org.id, seq=seq,
            prompt_hash=item.prompt_hash, response_hash=item.response_hash,
            token_count=item.token_count, policy_tag=item.policy_tag,
            pii_signals=item.pii_signals,
            prev_hash=prev_hash, chain_hash=chain_hash,
            gemini_verdict=None,          # grading_status defaults to 'pending'
        ))
        prev_seq = seq
        prev_hash = chain_hash

    db.add_all(rows)
    db.commit()
    return {"status": "pending", "count": len(rows)}


@router.get("/v1/logs", response_model=LogListResponse)
def list_logs(
    org: Organization = Depends(resolve_org),
    db: Session = Depends(get_db),
    page: int = Query(default=1, ge=1, description="1-indexed page number"),
    limit: int = Query(default=50, ge=1, le=200, description="rows per page"),
):
    """Return paginated audit log rows for the caller's org (newest first)."""
    offset = (page - 1) * limit
    total: int = db.execute(
        select(func.count()).select_from(AuditLog).where(AuditLog.org_id == org.id)
    ).scalar_one()
    rows = db.execute(
        select(AuditLog)
        .where(AuditLog.org_id == org.id)
        .order_by(AuditLog.seq.desc())
        .offset(offset)
        .limit(limit)
    ).scalars().all()
    return LogListResponse(
        items=[LogListItem.model_validate(r) for r in rows],
        total=total, page=page, limit=limit,
    )


@router.get("/v1/logs/{seq}", response_model=LogListItem)
def get_log_by_seq(
    seq: int,
    org: Organization = Depends(resolve_org),
    db: Session = Depends(get_db),
):
    """Fetch a single audit log row by sequence number (RLS scopes to the org)."""
    row = db.execute(
        select(AuditLog).where(AuditLog.org_id == org.id, AuditLog.seq == seq)
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"No log found for seq={seq}")
    return LogListItem.model_validate(row)


@router.get("/v1/stats", response_model=StatsResponse)
def stats(
    org: Organization = Depends(resolve_org),
    db: Session = Depends(get_db),
):
    """Aggregates for the dashboard hero/stat tiles and 7-day sparkline."""
    total = db.execute(
        select(func.count()).select_from(AuditLog).where(AuditLog.org_id == org.id)
    ).scalar_one()
    breaches = db.execute(
        select(func.count()).select_from(AuditLog)
        .where(AuditLog.org_id == org.id, _BREACH)
    ).scalar_one()
    avg_tokens = db.execute(
        select(func.coalesce(func.avg(AuditLog.token_count), 0))
        .where(AuditLog.org_id == org.id)
    ).scalar_one()

    gc = {"pending": 0, "in_progress": 0, "graded": 0, "failed": 0}
    for status, cnt in db.execute(
        select(AuditLog.grading_status, func.count())
        .where(AuditLog.org_id == org.id)
        .group_by(AuditLog.grading_status)
    ).all():
        if status in gc:
            gc[status] = cnt

    day = func.to_char(func.date_trunc("day", AuditLog.created_at), "YYYY-MM-DD")
    activity = [
        ActivityDay(date=d, count=c, breaches=b)
        for d, c, b in db.execute(
            select(day, func.count(), func.count().filter(_BREACH))
            .where(AuditLog.org_id == org.id,
                   AuditLog.created_at >= text("now() - interval '7 days'"))
            .group_by(day)
            .order_by(day)
        ).all()
    ]

    clean_rate = round(100.0 * (total - breaches) / total, 1) if total else 100.0
    return StatsResponse(
        total_logged=total, breaches=breaches, clean_rate=clean_rate,
        avg_token_count=round(float(avg_tokens), 1),
        grading=GradingCounts(**gc), activity_7d=activity,
    )
