"""Public-chain anchor endpoints (Phase 3 A1).

  GET  /v1/anchors   list this org's anchor receipts (session OR Bearer)
  POST /v1/anchors   "anchor now" — publish the current chain head (admin only)

The receipts are the externally-verifiable proof: each cites the chain + tx that
recorded the org's ledger head. See app/anchor.py for the provider machinery and
scripts/verify_anchor.py for independent verification.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import anchor as anchor_engine
from ..auth import require_role, resolve_org
from ..config import get_settings
from ..db import get_db
from ..models import ChainAnchor, Organization, User

router = APIRouter()


def _human_interval(secs: int) -> str:
    if secs >= 86400 and secs % 86400 == 0:
        d = secs // 86400
        return f"{d} day" + ("s" if d != 1 else "")
    if secs >= 3600 and secs % 3600 == 0:
        h = secs // 3600
        return f"{h} hour" + ("s" if h != 1 else "")
    m = max(1, secs // 60)
    return f"{m} minute" + ("s" if m != 1 else "")


class AnchorSLA(BaseModel):
    plan_tier: str | None = None
    cadence_seconds: int
    cadence_human: str


@router.get("/v1/anchors/sla", response_model=AnchorSLA)
def anchor_sla(org: Organization = Depends(resolve_org)):
    """This org's automatic-anchor SLA — the cadence for its plan tier (6E). Manual
    'anchor now' is always available; this is how often the worker anchors for you."""
    secs = get_settings().anchor_cadence_for(org.plan_tier)
    return AnchorSLA(plan_tier=org.plan_tier, cadence_seconds=secs,
                     cadence_human=_human_interval(secs))


class AnchorItem(BaseModel):
    id: str
    root_hash: str
    last_seq: int
    chain: str
    tx_hash: str | None = None
    block_number: int | None = None
    status: str
    detail: str | None = None
    anchored_at: str
    confirmed_at: str | None = None


def _serialize(a: ChainAnchor) -> AnchorItem:
    return AnchorItem(
        id=str(a.id), root_hash=a.root_hash, last_seq=a.last_seq, chain=a.chain,
        tx_hash=a.tx_hash, block_number=a.block_number, status=a.status, detail=a.detail,
        anchored_at=a.anchored_at.isoformat() if a.anchored_at else "",
        confirmed_at=a.confirmed_at.isoformat() if a.confirmed_at else None,
    )


@router.get("/v1/anchors", response_model=list[AnchorItem])
def list_anchors(
    org: Organization = Depends(resolve_org),
    db: Session = Depends(get_db),
):
    """List this org's anchor receipts, newest first (dashboard session or SDK key)."""
    rows = db.execute(
        select(ChainAnchor).where(ChainAnchor.org_id == org.id)
        .order_by(ChainAnchor.anchored_at.desc())
    ).scalars().all()
    return [_serialize(a) for a in rows]


@router.post("/v1/anchors", response_model=AnchorItem)
def anchor_now(
    admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Publish the org's current chain head now — admin only. Idempotent and
    gas-safe: 409 if the current head is already anchored (or nothing is logged
    yet). A previously *failed* anchor for the same head is retried."""
    row = anchor_engine.anchor_org(db, admin.org_id, force=False)
    if row is None:
        raise HTTPException(
            status_code=409,
            detail="nothing new to anchor — the current chain head is already anchored (or no logs yet)",
        )
    return _serialize(row)
