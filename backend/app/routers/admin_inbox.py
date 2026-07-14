"""Support inbox for the admin site — the contact/support messages from the sales site.

Mounted under /admin -> /admin/v1/inbox*. Every marketing_lead that carries a written
`message` is an inbox item. Staff read freely; claiming or replying LOCKS a message to one
staff member (others become read-only). A reply emails the sender and is recorded.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import email as email_mod
from ..auth import require_staff
from ..db import get_db
from ..models import MarketingLead, StaffUser

router = APIRouter()


def _is_message():
    """Filter: leads that actually carry a written note (not bare signup leads)."""
    return [MarketingLead.message.isnot(None), func.length(func.trim(MarketingLead.message)) > 0]


def _iso(dt):
    return dt.isoformat() if dt else None


class InboxItem(BaseModel):
    id: str
    email: str
    name: str | None = None
    company: str | None = None
    source: str | None = None
    subject: str | None = None
    message: str | None = None
    created_at: str | None = None
    read: bool = False
    claimed_by: str | None = None          # email of the staff member who holds it
    claimed_by_id: str | None = None
    claimed_at: str | None = None
    reply: str | None = None
    replied_at: str | None = None


class InboxList(BaseModel):
    unread: int
    items: list[InboxItem]


def _serialize(lead: MarketingLead, staff_by_id: dict) -> InboxItem:
    return InboxItem(
        id=str(lead.id), email=lead.email, name=lead.name, company=lead.company,
        source=lead.source, subject=lead.subject, message=lead.message,
        created_at=_iso(lead.created_at), read=lead.read_at is not None,
        claimed_by=staff_by_id.get(lead.claimed_by),
        claimed_by_id=str(lead.claimed_by) if lead.claimed_by else None,
        claimed_at=_iso(lead.claimed_at), reply=lead.reply, replied_at=_iso(lead.replied_at))


@router.get("/v1/inbox", response_model=InboxList)
def list_inbox(staff: StaffUser = Depends(require_staff), db: Session = Depends(get_db)):
    rows = db.execute(select(MarketingLead).where(*_is_message())
                      .order_by(MarketingLead.created_at.desc()).limit(500)).scalars().all()
    ids = {r.claimed_by for r in rows if r.claimed_by}
    staff_by_id: dict = {}
    if ids:
        for s in db.execute(select(StaffUser).where(StaffUser.id.in_(ids))).scalars().all():
            staff_by_id[s.id] = s.email
    unread = sum(1 for r in rows if r.read_at is None)
    return InboxList(unread=unread, items=[_serialize(r, staff_by_id) for r in rows])


@router.get("/v1/inbox/unread-count")
def unread_count(staff: StaffUser = Depends(require_staff), db: Session = Depends(get_db)):
    n = db.execute(select(func.count()).select_from(MarketingLead)
                   .where(*_is_message(), MarketingLead.read_at.is_(None))).scalar() or 0
    return {"unread": int(n)}


def _get_lead(db: Session, lead_id: str) -> MarketingLead:
    try:
        lid = uuid.UUID(str(lead_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="not found")
    lead = db.get(MarketingLead, lid)
    if lead is None or not (lead.message and lead.message.strip()):
        raise HTTPException(status_code=404, detail="not found")
    return lead


@router.post("/v1/inbox/{lead_id}/read")
def mark_read(lead_id: str, staff: StaffUser = Depends(require_staff), db: Session = Depends(get_db)):
    lead = _get_lead(db, lead_id)
    if lead.read_at is None:
        lead.read_at = datetime.now(timezone.utc)
        db.commit()
    return {"status": "read"}


@router.post("/v1/inbox/{lead_id}/claim")
def claim(lead_id: str, staff: StaffUser = Depends(require_staff), db: Session = Depends(get_db)):
    lead = _get_lead(db, lead_id)
    if lead.claimed_by and lead.claimed_by != staff.id:
        holder = db.get(StaffUser, lead.claimed_by)
        raise HTTPException(status_code=409,
                            detail=f"already claimed by {holder.email if holder else 'another admin'}")
    now = datetime.now(timezone.utc)
    lead.claimed_by = staff.id
    lead.claimed_at = lead.claimed_at or now
    if lead.read_at is None:
        lead.read_at = now
    db.commit()
    return {"status": "claimed", "claimed_by": staff.email}


@router.post("/v1/inbox/{lead_id}/release")
def release(lead_id: str, staff: StaffUser = Depends(require_staff), db: Session = Depends(get_db)):
    lead = _get_lead(db, lead_id)
    if lead.claimed_by and lead.claimed_by != staff.id and staff.platform_role != "superadmin":
        raise HTTPException(status_code=403,
                            detail="only the admin who claimed it (or a superadmin) can release it")
    lead.claimed_by = None
    lead.claimed_at = None
    db.commit()
    return {"status": "released"}


class ReplyRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)


@router.post("/v1/inbox/{lead_id}/reply")
def reply(lead_id: str, payload: ReplyRequest,
          staff: StaffUser = Depends(require_staff), db: Session = Depends(get_db)):
    lead = _get_lead(db, lead_id)
    if lead.claimed_by and lead.claimed_by != staff.id:
        holder = db.get(StaffUser, lead.claimed_by)
        raise HTTPException(status_code=403,
                            detail=f"locked by {holder.email if holder else 'another admin'}")
    now = datetime.now(timezone.utc)
    lead.claimed_by = staff.id           # replying claims/locks it
    lead.claimed_at = lead.claimed_at or now
    lead.reply = payload.message.strip()
    lead.replied_at = now
    if lead.read_at is None:
        lead.read_at = now
    db.commit()
    body = payload.message.strip()
    sent = email_mod.send_email(
        to=lead.email, subject="Re: your message to Foxy Audit",
        html=f"<div style='font-family:sans-serif;font-size:14px'>{body}</div>"
             "<p style='color:#888;font-size:12px'>— Foxy Audit support</p>",
        text=body + "\n\n-- Foxy Audit support")
    return {"status": "replied", "emailed": bool(sent)}
