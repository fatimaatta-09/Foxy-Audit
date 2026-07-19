"""Content-blind SDK capture coverage for customer workspaces.

This report turns the SDK's durable ``client_id``/``client_seq`` signals into an
auditor-facing evidence check. It intentionally does not claim complete capture:
calls that bypass the SDK are outside the observable system boundary.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..auth import resolve_org
from ..db import get_db
from ..evidence_coverage import calculate_capture_coverage
from ..models import Organization
from ..schemas import CoverageResponse
from .verify import verify_chain

router = APIRouter()


@router.get("/v1/coverage", response_model=CoverageResponse)
def get_capture_coverage(
    limit: int = Query(default=100, ge=1, le=500),
    org: Organization = Depends(resolve_org),
    db: Session = Depends(get_db),
):
    """Report continuity of SDK-reported client sequences without raw content."""
    chain_ok, first_broken_seq, chain_detail = verify_chain(db, org.id)
    if chain_ok is False:
        chain_detail = f"{chain_detail} (first broken seq {first_broken_seq})"
    return calculate_capture_coverage(
        db, org.id, limit=limit, chain_verified=chain_ok, chain_detail=chain_detail,
    )
