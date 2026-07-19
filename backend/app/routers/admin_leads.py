"""Leads pipeline (P4 · §M) — kanban over marketing_leads + audited status moves.

Distinct from the support Inbox (which shows message-bearing leads): this is the full
pre-sales pipeline bucketed new → trial → converted → churned, with UTM attribution and
the converted-org linkage. Reads viewer-gated; the status transition is operator+ audited.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..admin_audit import client_ip, record_admin_action
from ..auth import require_platform_role
from ..db import get_db
from ..models import MarketingLead, Organization, StaffUser

router = APIRouter()

_STATUSES = ("new", "trial", "converted", "churned")


def _serialize(lead: MarketingLead, names: dict) -> dict:
    return {
        "id": str(lead.id), "email": lead.email, "name": lead.name, "company": lead.company,
        "source": lead.source, "subject": lead.subject, "utm_source": lead.utm_source,
        "utm_medium": lead.utm_medium, "utm_campaign": lead.utm_campaign, "status": lead.status,
        "converted_org_id": str(lead.converted_org_id) if lead.converted_org_id else None,
        "converted_org_name": names.get(lead.converted_org_id),
        "created_at": lead.created_at.isoformat() if lead.created_at else None,
    }


@router.get("/v1/leads")
def list_leads(
    staff: StaffUser = Depends(require_platform_role("viewer")),
    db: Session = Depends(get_db),
    q: str | None = None,
):
    """All leads bucketed into a kanban (new/trial/converted/churned) + counts."""
    rows = db.execute(
        select(MarketingLead).order_by(MarketingLead.created_at.desc()).limit(1000)
    ).scalars().all()
    if q:
        ql = q.strip().lower()
        rows = [
            r for r in rows
            if ql in " ".join([r.email or "", r.name or "", r.company or "", r.source or ""]).lower()
        ]
    oids = [r.converted_org_id for r in rows if r.converted_org_id]
    names: dict = {}
    if oids:
        for oid, nm in db.execute(
            select(Organization.id, Organization.name).where(Organization.id.in_(oids))
        ).all():
            names[oid] = nm
    buckets: dict[str, list] = {s: [] for s in _STATUSES}
    for lead in rows:
        buckets[lead.status if lead.status in buckets else "new"].append(_serialize(lead, names))
    return {"buckets": buckets, "counts": {s: len(buckets[s]) for s in _STATUSES}}


class LeadStatusBody(BaseModel):
    status: str


@router.post("/v1/leads/{lead_id}/status")
def set_lead_status(
    lead_id: str,
    body: LeadStatusBody,
    request: Request,
    staff: StaffUser = Depends(require_platform_role("operator")),
    db: Session = Depends(get_db),
):
    """Move a lead to a new pipeline stage (audited)."""
    status = body.status.strip().lower()
    if status not in _STATUSES:
        raise HTTPException(status_code=422, detail=f"status must be one of {list(_STATUSES)}")
    try:
        lid = uuid.UUID(lead_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="invalid lead id")
    lead = db.get(MarketingLead, lid)
    if lead is None:
        raise HTTPException(status_code=404, detail="lead not found")
    old = lead.status
    lead.status = status
    record_admin_action(db, staff, "lead.status", target_org_id=lead.converted_org_id,
                        target_type="marketing_lead", target_id=str(lead.id),
                        detail={"from": old, "to": status, "email": lead.email},
                        ip=client_ip(request))
    db.commit()
    return {"status": "updated", "id": str(lead.id), "lead_status": status}
