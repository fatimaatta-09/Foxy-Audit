"""GET /v1/health — the endpoint the desktop app probes on startup.

Returns 200 only for a valid Bearer key (invalid → 401, which the desktop treats
as 'unreachable'). Body is informational; the desktop only checks the status code.
"""

from __future__ import annotations

import time
from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from ..auth import require_org
from ..models import AuditLog, Organization
from ..db import get_db
from ..config import get_settings

router = APIRouter()
START_TIME = time.time()

@router.get("/v1/health")
def health(
    org: Organization = Depends(require_org),
    db: Session = Depends(get_db)
):
    chain_height = db.execute(
        select(func.count()).select_from(AuditLog).where(AuditLog.org_id == org.id)
    ).scalar_one()
    uptime_seconds = int(time.time() - START_TIME)
    
    return {
        "status": "ok",
        "org": org.name,
        "gemini_model": get_settings().gemini_model,
        "chain_height": chain_height,
        "uptime_seconds": uptime_seconds
    }
