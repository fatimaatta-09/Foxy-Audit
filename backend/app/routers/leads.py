"""Public marketing endpoints (site 1 → the backend).

POST /v1/leads   — capture a signup/lead from the marketing site (funnel entry).
POST /v1/track   — a lightweight marketing-page visit beacon → one traffic_events
                   row with site='marketing' (anonymous, org_id NULL).

Both are UNAUTHENTICATED and rate-limited (they face the public internet). They
store no secrets; the beacon hashes IP/UA with the server pepper like the traffic
middleware, and keeps only the path (no query string).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth import hash_key
from ..db import get_db
from ..models import MarketingLead, TrafficEvent
from .logs import limiter          # reuse the app's single Limiter instance

router = APIRouter()


class LeadRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    name: str | None = Field(default=None, max_length=255)
    company: str | None = Field(default=None, max_length=255)
    source: str | None = Field(default=None, max_length=64)
    utm_campaign: str | None = Field(default=None, max_length=128)
    utm_source: str | None = Field(default=None, max_length=128)
    utm_medium: str | None = Field(default=None, max_length=128)


class LeadResponse(BaseModel):
    status: str
    id: str


class TrackRequest(BaseModel):
    path: str = Field(default="/", max_length=512)
    referrer: str | None = Field(default=None, max_length=512)
    visitor_id: str | None = None
    session_id: str | None = None


def _maybe_uuid(v: str | None):
    if not v:
        return None
    try:
        return uuid.UUID(str(v))
    except ValueError:
        return None


@router.post("/v1/leads", response_model=LeadResponse)
@limiter.limit("20/minute")
def create_lead(payload: LeadRequest, request: Request, db: Session = Depends(get_db)):
    """Record a marketing lead. If an ACTIVE lead already exists for this email,
    update its details instead of duplicating (matches the partial-unique index)."""
    email = payload.email.strip().lower()
    existing = db.execute(
        select(MarketingLead).where(
            func.lower(MarketingLead.email) == email,
            MarketingLead.status != "churned",
        )
    ).scalars().first()
    if existing is not None:
        existing.name = payload.name or existing.name
        existing.company = payload.company or existing.company
        existing.source = payload.source or existing.source
        existing.utm_campaign = payload.utm_campaign or existing.utm_campaign
        existing.utm_source = payload.utm_source or existing.utm_source
        existing.utm_medium = payload.utm_medium or existing.utm_medium
        db.commit()
        return LeadResponse(status="updated", id=str(existing.id))

    lead = MarketingLead(
        email=email, name=payload.name, company=payload.company, source=payload.source,
        utm_campaign=payload.utm_campaign, utm_source=payload.utm_source,
        utm_medium=payload.utm_medium,
    )
    db.add(lead)
    try:
        db.commit()
    except IntegrityError:            # race on the partial-unique index
        db.rollback()
        existing = db.execute(
            select(MarketingLead).where(
                func.lower(MarketingLead.email) == email,
                MarketingLead.status != "churned",
            )
        ).scalars().first()
        return LeadResponse(status="updated", id=str(existing.id) if existing else "")
    db.refresh(lead)
    return LeadResponse(status="created", id=str(lead.id))


@router.post("/v1/track")
@limiter.limit("120/minute")
def track(payload: TrackRequest, request: Request, db: Session = Depends(get_db)):
    """Marketing-site visit beacon → one anonymous 'marketing' traffic_events row."""
    referrer = payload.referrer or request.headers.get("referer")
    if referrer:
        referrer = referrer.split("?", 1)[0][:512]
    ev = TrafficEvent(
        site="marketing", path=payload.path.split("?", 1)[0][:512], method="GET",
        status_code=200, direction="in",
        ip_hash=hash_key(request.client.host) if request.client else None,
        ua_hash=hash_key(request.headers.get("user-agent")) if request.headers.get("user-agent") else None,
        referrer=referrer, visitor_id=_maybe_uuid(payload.visitor_id),
        session_id=_maybe_uuid(payload.session_id),
    )
    db.add(ev)
    db.commit()
    return {"status": "ok"}
