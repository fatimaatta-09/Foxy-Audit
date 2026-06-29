"""POST /v1/logs/batch — ingest a batch of interactions into the hash chain.
GET  /v1/logs         — fetch paginated audit log rows for the authenticated org.

Sequence (POST):
  1. Authenticate the org via API key.
  2. Enqueue the entire batch payload to Celery for background processing.
  3. Return HTTP 202 Accepted instantly.

Sequence (GET):
  1. Authenticate the org via API key (RLS GUC is set by require_org).
  2. Query audit_logs filtered by org_id with pagination.
  3. Return JSON list — dashboard Refresh button calls this.
"""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.status import HTTP_202_ACCEPTED

from ..auth import require_org
from ..db import get_db
from ..models import AuditLog, Organization
from ..schemas import LogIngest, LogListItem, LogListResponse
from ..worker import submit_batch

router = APIRouter()


@router.post("/v1/logs/batch", status_code=HTTP_202_ACCEPTED)
def ingest_batch(
    payload: List[LogIngest],
    org: Organization = Depends(require_org),
):
    batch_data = []
    for item in payload:
        data = item.model_dump()
        data["org_id"] = str(org.id)
        batch_data.append(data)
    # Fire-and-forget: threading.ThreadPoolExecutor, no Redis/Celery required.
    submit_batch(batch_data)
    return {"status": "pending", "count": len(batch_data)}


@router.get("/v1/logs", response_model=LogListResponse)
def list_logs(
    org: Organization = Depends(require_org),
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
    org: Organization = Depends(require_org),
    db: Session = Depends(get_db),
):
    """Fetch a single audit log row by sequence number.

    Used by the Verification Sandbox to retrieve stored hashes for comparison.
    RLS already scopes to the authenticated org.
    """
    row = db.execute(
        select(AuditLog)
        .where(AuditLog.org_id == org.id, AuditLog.seq == seq)
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"No log found for seq={seq}")
    return LogListItem.model_validate(row)

