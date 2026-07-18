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
from ..models import AuditLog, Organization, OrganizationSequence
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
    # The sequence row exists even for an empty ledger. Locking it avoids the
    # classic first-ingest race where two writers both choose seq=1.
    db.execute(text(
        "INSERT INTO org_sequences(org_id, next_seq) "
        "SELECT :org_id, COALESCE(MAX(seq) + 1, 1) FROM audit_logs WHERE org_id = :org_id "
        "ON CONFLICT (org_id) DO NOTHING"), {"org_id": org.id})
    sequence = db.execute(
        select(OrganizationSequence).where(OrganizationSequence.org_id == org.id)
        .with_for_update()
    ).scalar_one()

    # A retry with the same event_id returns the original receipt. A conflicting
    # payload is rejected rather than silently treating two events as one.
    existing_receipts = []
    new_items = []
    for item in payload:
        existing = None
        if item.event_id:
            existing = db.execute(
                select(AuditLog).where(AuditLog.org_id == org.id,
                                       AuditLog.event_id == item.event_id)
            ).scalar_one_or_none()
            stored_metadata = dict(existing.event_metadata or {}) if existing else {}
            stored_metadata.pop("client_seq_gap", None)
            requested_metadata = dict(item.event_metadata or {})
            if existing and any((
                existing.prompt_hash != item.prompt_hash,
                existing.response_hash != item.response_hash,
                existing.token_count != item.token_count,
                existing.policy_tag != item.policy_tag,
                existing.agent != item.agent,
                existing.client_id != item.client_id,
                existing.client_seq != item.client_seq,
                existing.event_type != item.event_type,
                existing.commitment_alg != item.commitment_alg,
                existing.pii_signals != item.pii_signals,
                stored_metadata != requested_metadata,
                existing.occurred_at != item.occurred_at,
            )):
                raise HTTPException(status_code=409,
                                    detail=f"event_id {item.event_id} was already used with different content")
        if existing:
            existing_receipts.append({
                "event_id": str(item.event_id), "seq": existing.seq,
                "chain_hash": existing.chain_hash, "status": "duplicate",
            })
        else:
            new_items.append(item)

    # Lock the org's tail row after taking the allocator lock. Legacy rows can
    # still exist, so the allocator is initialized from the observed tail above.
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
    receipts = list(existing_receipts)
    warnings = []
    client_last: dict[str, int] = {}
    for item in new_items:
        seq = int(sequence.next_seq)
        sequence.next_seq = seq + 1
        metadata = dict(item.event_metadata or {})
        if item.client_id and item.client_seq is not None:
            if item.client_id not in client_last:
                client_last[item.client_id] = db.execute(
                    select(func.max(AuditLog.client_seq)).where(
                        AuditLog.org_id == org.id,
                        AuditLog.client_id == item.client_id,
                    )
                ).scalar() or 0
            expected = client_last[item.client_id] + 1
            if item.client_seq != expected:
                metadata["client_seq_gap"] = {
                    "expected": expected, "received": item.client_seq,
                }
                warnings.append({"client_id": item.client_id,
                                 "expected": expected, "received": item.client_seq})
            client_last[item.client_id] = item.client_seq
        chain_version = 2 if item.event_id else 1
        chain_hash = compute_chain_hash(
            org_id=org.id,
            prompt_hash=item.prompt_hash,
            response_hash=item.response_hash,
            token_count=item.token_count,
            policy_tag=item.policy_tag,
            seq=seq,
            prev_hash=prev_hash,
            agent=item.agent,
            chain_version=chain_version,
            event_id=item.event_id,
            client_id=item.client_id,
            client_seq=item.client_seq,
            event_type=item.event_type,
            commitment_alg=item.commitment_alg,
            event_metadata=metadata or None,
            pii_signals=item.pii_signals,
            occurred_at=item.occurred_at,
        )
        row = AuditLog(
            org_id=org.id, seq=seq,
            event_id=item.event_id, client_id=item.client_id,
            client_seq=item.client_seq, event_type=item.event_type,
            commitment_alg=item.commitment_alg,
            event_metadata=metadata or None, occurred_at=item.occurred_at,
            chain_version=chain_version,
            prompt_hash=item.prompt_hash, response_hash=item.response_hash,
            token_count=item.token_count, policy_tag=item.policy_tag,
            agent=item.agent,
            pii_signals=item.pii_signals,
            prev_hash=prev_hash, chain_hash=chain_hash,
            gemini_verdict=None,          # grading_status defaults to 'pending'
        )
        rows.append(row)
        receipts.append({
            "event_id": str(item.event_id) if item.event_id else None,
            "client_id": item.client_id, "client_seq": item.client_seq,
            "seq": seq, "chain_hash": chain_hash, "status": "accepted",
        })
        prev_seq = seq
        prev_hash = chain_hash

    db.add_all(rows)
    db.commit()
    return {"status": "pending", "count": len(payload), "receipts": receipts,
            "warnings": warnings}


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


_EXPORT_COLS = ["seq", "event_id", "client_id", "client_seq", "event_type",
                "commitment_alg", "event_metadata", "chain_version", "occurred_at", "created_at",
                "policy_tag", "agent", "token_count", "prompt_hash",
                "response_hash", "pii_signals", "prev_hash", "chain_hash",
                "gemini_verdict", "grading_status", "graded_at"]


def _export_row(r: AuditLog) -> dict:
    return {
        "seq": r.seq,
        "event_id": str(r.event_id) if r.event_id else None,
        "client_id": r.client_id,
        "client_seq": r.client_seq,
        "event_type": r.event_type,
        "commitment_alg": r.commitment_alg,
        "event_metadata": r.event_metadata,
        "chain_version": r.chain_version or 1,
        "occurred_at": r.occurred_at.isoformat() if r.occurred_at else None,
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
            d["event_metadata"] = "" if d["event_metadata"] is None else json.dumps(d["event_metadata"])
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
        .where(AuditLog.org_id == org.id, AuditLog.grading_status == "graded", _BREACH)
    ).scalar_one()
    avg_tokens = db.execute(
        select(func.coalesce(func.avg(AuditLog.token_count), 0))
        .where(AuditLog.org_id == org.id)
    ).scalar_one()
    # Real average time-to-verdict (ingest → graded), in seconds; None if nothing
    # has been graded yet. Replaces the dashboard's fabricated "42ms judge latency".
    avg_verdict = db.execute(
        select(func.avg(func.extract("epoch", AuditLog.graded_at - AuditLog.created_at)))
        .where(AuditLog.org_id == org.id, AuditLog.graded_at.isnot(None))
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
                   AuditLog.created_at >= text("now() - interval '7 days'"),
                   AuditLog.grading_status == "graded")
            .group_by(day)
            .order_by(day)
        ).all()
    ]

    clean = db.execute(
        select(func.count()).select_from(AuditLog)
        .where(AuditLog.org_id == org.id, AuditLog.grading_status == "graded",
               AuditLog.gemini_verdict["decision"].astext == "clean")
    ).scalar_one()
    determinate = clean + breaches
    clean_rate = round(100.0 * clean / determinate, 1) if determinate else None
    return StatsResponse(
        total_logged=total, breaches=breaches, clean_rate=clean_rate,
        avg_token_count=round(float(avg_tokens), 1),
        judge_model=get_settings().gemini_model,
        avg_seconds_to_verdict=round(float(avg_verdict), 1) if avg_verdict is not None else None,
        grading=GradingCounts(**gc), activity_7d=activity,
    )
