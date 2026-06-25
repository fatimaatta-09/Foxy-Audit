"""GET /v1/health — the endpoint the desktop app probes on startup.

Returns 200 only for a valid Bearer key (invalid → 401, which the desktop treats
as 'unreachable'). Body is informational; the desktop only checks the status code.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..auth import require_org
from ..models import Organization

router = APIRouter()


@router.get("/v1/health")
def health(org: Organization = Depends(require_org)):
    return {"status": "ok", "org": org.name}
