"""POST /v1/logs/batch — ingest a batch of interactions into the hash chain.
GET  /v1/logs, /v1/logs/{seq}, /v1/stats — dashboard reads.

Durable ingest (hybrid): the batch is written to the chain SYNCHRONOUSLY — each
row lands with grading_status='pending', committed before the 202 — so a crash
can never lose it. The expensive Gemini grading is deferred to the durable poller
in app/worker.py, which claims 'pending' rows and back-fills the verdict using the
org's policy config. Chain hashing stays cheap and inline; only grading is async.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session
from starlette.status import HTTP_202_ACCEPTED

from ..anchor import latest_anchor
from ..auth import require_org, resolve_org
from ..chain import GENESIS_HASH, compute_chain_hash
from ..config import get_settings
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
_UNKNOWN = (
    (AuditLog.gemini_verdict["decision"].astext == "unknown")
    | (AuditLog.gemini_verdict["reason"].astext.like("evaluator_unavailable:%"))
    | (AuditLog.gemini_verdict["reason"].astext == "evaluator_unavailable")
)


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
    # Serialise allowance consumption with the chain writer. Evaluation credits
    # are a hard cap; existing logs, exports, and verification remain available.
    locked_org = db.execute(
        select(Organization).where(Organization.id == org.id).with_for_update()
    ).scalar_one()
    if locked_org.evaluation_offer_id:
        now = datetime.now(timezone.utc)
        if locked_org.evaluation_ends_at and now >= locked_org.evaluation_ends_at:
            raise HTTPException(status_code=402, detail={
                "code": "evaluation_expired",
                "message": "This evaluation offer has ended. Existing evidence remains available.",
            })
        credit_limit = locked_org.evaluation_credit_limit or 0
        requested = len(payload)
        if locked_org.evaluation_credits_used + requested > credit_limit:
            raise HTTPException(status_code=402, detail={
                "code": "evaluation_credits_exhausted",
                "message": "This evaluation offer has no remaining event credits.",
            })
        locked_org.evaluation_credits_used += requested
    org = locked_org

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
            agent=item.agent,
        )
        rows.append(AuditLog(
            org_id=org.id, seq=seq,
            prompt_hash=item.prompt_hash, response_hash=item.response_hash,
            token_count=item.token_count, policy_tag=item.policy_tag,
            agent=item.agent,
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


@router.get("/v1/logs/breaches")
def list_breaches(
    org: Organization = Depends(resolve_org),
    db: Session = Depends(get_db),
    since_seq: int = Query(default=0, ge=0,
                           description="only breaches with seq > this (poller cursor)"),
    limit: int = Query(default=200, ge=1, le=500),
):
    """Graded policy breaches for the org with seq > since_seq, ascending by seq.

    The feed the desktop fox polls so it can react to a *real* backend-detected
    breach (the backend grades asynchronously and, in prod, is remote — so it can
    never push a UDP event to the desktop's localhost). The poller advances
    since_seq so a breach the fox already reacted to never re-fires. Declared
    BEFORE /v1/logs/{seq} so the literal path isn't captured as a seq int.
    """
    rows = db.execute(
        select(AuditLog)
        .where(
            AuditLog.org_id == org.id,
            AuditLog.seq > since_seq,
            AuditLog.grading_status == "graded",
            _BREACH,
        )
        .order_by(AuditLog.seq.asc())
        .limit(limit)
    ).scalars().all()
    return [
        {
            "seq": r.seq,
            "policy_tag": r.policy_tag,
            "reason": (r.gemini_verdict or {}).get("reason", ""),
            "risk_score": (r.gemini_verdict or {}).get("risk_score", 0),
        }
        for r in rows
    ]


_EXPORT_COLS = ["seq", "created_at", "policy_tag", "agent", "token_count", "prompt_hash",
                "response_hash", "pii_signals", "prev_hash", "chain_hash",
                "gemini_verdict", "grading_status", "graded_at"]


def _export_row(r: AuditLog) -> dict:
    return {
        "seq": r.seq,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "policy_tag": r.policy_tag,
        "agent": r.agent,
        "token_count": r.token_count,
        "prompt_hash": r.prompt_hash,
        "response_hash": r.response_hash,
        "pii_signals": r.pii_signals,
        "prev_hash": r.prev_hash,
        "chain_hash": r.chain_hash,
        "gemini_verdict": r.gemini_verdict,
        "grading_status": r.grading_status,
        "graded_at": r.graded_at.isoformat() if r.graded_at else None,
    }


def _anchor_export(a) -> dict | None:
    """The org's latest anchor receipt, embedded in the JSON export so the
    open-source verifier can offline-compare it — and, with --anchor, confirm the
    root live on the public chain (Phase 6 · 6D). None when the org has no anchor."""
    if a is None:
        return None
    return {
        "chain": a.chain, "status": a.status, "root_hash": a.root_hash,
        "last_seq": a.last_seq, "tx_hash": a.tx_hash, "block_number": a.block_number,
        "anchored_at": a.anchored_at.isoformat() if a.anchored_at else None,
        "contract": get_settings().anchor_evm_contract or None,
    }


@router.get("/v1/logs/export")
@limiter.limit("6/minute")
def export_logs(
    request: Request,
    org: Organization = Depends(resolve_org),
    db: Session = Depends(get_db),
    format: str = Query(default="json", pattern="^(json|csv)$"),
):
    """Download the org's ENTIRE audit-log ledger (its own data) as JSON or CSV —
    data portability for the tenant. Scoped by org_id (+ RLS). Declared BEFORE
    /v1/logs/{seq} so the literal path isn't captured as a seq int."""
    rows = db.execute(
        select(AuditLog).where(AuditLog.org_id == org.id).order_by(AuditLog.seq.asc())
    ).scalars().all()

    if format == "csv":
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=_EXPORT_COLS)
        w.writeheader()
        for r in rows:
            d = _export_row(r)
            d["pii_signals"] = "" if d["pii_signals"] is None else json.dumps(d["pii_signals"])
            d["gemini_verdict"] = "" if d["gemini_verdict"] is None else json.dumps(d["gemini_verdict"])
            w.writerow(d)
        return Response(
            content=buf.getvalue(), media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="foxy-audit-logs.csv"'})

    body = json.dumps(
        {"org_id": str(org.id), "count": len(rows),
         "anchor": _anchor_export(latest_anchor(db, org.id)),
         "logs": [_export_row(r) for r in rows]},
        default=str)
    return Response(
        content=body, media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="foxy-audit-logs.json"'})


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
    # Real average time-to-verdict (ingest → graded), in seconds; None if nothing
    # has been graded yet. Replaces the dashboard's fabricated "42ms judge latency".
    avg_verdict = db.execute(
        select(func.avg(func.extract("epoch", AuditLog.graded_at - AuditLog.created_at)))
        .where(AuditLog.org_id == org.id, AuditLog.graded_at.isnot(None), ~_UNKNOWN)
    ).scalar_one()

    evaluator_unknown = db.execute(
        select(func.count()).select_from(AuditLog)
        .where(AuditLog.org_id == org.id, AuditLog.grading_status == "graded", _UNKNOWN)
    ).scalar_one()
    known_graded = db.execute(
        select(func.count()).select_from(AuditLog)
        .where(AuditLog.org_id == org.id, AuditLog.grading_status == "graded", ~_UNKNOWN)
    ).scalar_one()
    known_clean = db.execute(
        select(func.count()).select_from(AuditLog)
        .where(
            AuditLog.org_id == org.id,
            AuditLog.grading_status == "graded",
            ~_UNKNOWN,
            ~_BREACH,
        )
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

    clean_rate = round(100.0 * known_clean / known_graded, 1) if known_graded else 0.0
    return StatsResponse(
        total_logged=total, breaches=breaches, clean_rate=clean_rate,
        avg_token_count=round(float(avg_tokens), 1),
        judge_model=get_settings().gemini_model,
        avg_seconds_to_verdict=round(float(avg_verdict), 1) if avg_verdict is not None else None,
        grading=GradingCounts(**gc), activity_7d=activity,
        evaluator_unknown=evaluator_unknown,
    )
