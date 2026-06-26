"""POST /logs/batch — ingest a batch of interactions into the hash chain.

Sequence:
  1. Authenticate the org via API key.
  2. Enqueue the entire batch payload to Celery for background processing.
  3. Return HTTP 202 Accepted instantly.
"""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends
from starlette.status import HTTP_202_ACCEPTED

from ..auth import require_org
from ..models import Organization
from ..schemas import LogIngest
from ..worker import process_log_batch_task

router = APIRouter()


@router.post("/logs/batch", status_code=HTTP_202_ACCEPTED)
def ingest_batch(
    payload: List[LogIngest],
    org: Organization = Depends(require_org),
):
    # Convert payload to dicts and inject org_id
    batch_data = []
    for item in payload:
        data = item.model_dump()
        data["org_id"] = str(org.id)
        batch_data.append(data)

    # Enqueue for async Gemini grading and DB insertion — fire and forget.
    process_log_batch_task.delay(batch_data)

    return {"status": "pending", "count": len(batch_data)}
