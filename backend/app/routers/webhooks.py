"""Customer outbound webhook subscriptions (P3 · §F).

  GET    /v1/webhooks            list the org's subscriptions (secret masked)
  POST   /v1/webhooks            create one — returns the signing secret ONCE
  DELETE /v1/webhooks/{id}       remove one
  POST   /v1/webhooks/{id}/test  send a signed test ping, return the status

Admin-only, org-scoped. The worker delivers real events via webhook_delivery.
"""

from __future__ import annotations

import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import account_audit, webhook_delivery
from ..auth import require_role
from ..db import get_db
from ..models import User, WebhookSubscription

router = APIRouter()


class WebhookItem(BaseModel):
    id: str
    url: str
    events: str
    active: bool
    secret_prefix: str          # display only — full secret is shown once at create
    last_status: str | None = None
    last_delivery_at: str | None = None
    created_at: str | None = None


class CreateWebhookRequest(BaseModel):
    url: str = Field(max_length=1024)
    events: list[str] = Field(default_factory=lambda: ["breach"])


def _serialize(w: WebhookSubscription) -> WebhookItem:
    return WebhookItem(
        id=str(w.id), url=w.url, events=w.events, active=w.active,
        secret_prefix=(w.secret[:11] + "…") if w.secret else "",
        last_status=w.last_status,
        last_delivery_at=w.last_delivery_at.isoformat() if w.last_delivery_at else None,
        created_at=w.created_at.isoformat() if w.created_at else None,
    )


@router.get("/v1/webhooks", response_model=list[WebhookItem])
def list_webhooks(admin: User = Depends(require_role("admin")),
                  db: Session = Depends(get_db)):
    rows = db.execute(
        select(WebhookSubscription).where(WebhookSubscription.org_id == admin.org_id)
        .order_by(WebhookSubscription.created_at.desc())
    ).scalars().all()
    return [_serialize(w) for w in rows]


class CreateWebhookResponse(WebhookItem):
    secret: str                 # full HMAC secret — shown ONCE


@router.post("/v1/webhooks", response_model=CreateWebhookResponse)
def create_webhook(body: CreateWebhookRequest,
                   admin: User = Depends(require_role("admin")),
                   db: Session = Depends(get_db)):
    url = body.url.strip()
    if not url.startswith(("https://", "http://")):
        raise HTTPException(status_code=422, detail="url must be an http(s) URL")
    events = sorted({e.strip().lower() for e in body.events if e.strip()})
    if not events or any(e not in webhook_delivery.VALID_EVENTS for e in events):
        raise HTTPException(status_code=422,
                            detail=f"events must be a subset of {sorted(webhook_delivery.VALID_EVENTS)}")
    secret = "whsec_" + secrets.token_hex(24)
    row = WebhookSubscription(org_id=admin.org_id, url=url, secret=secret,
                              events=",".join(events), active=True)
    db.add(row)
    db.flush()
    account_audit.record_account_action(
        db, org_id=admin.org_id, actor_email=admin.email, action="webhook.create",
        target=url, detail={"events": events})
    db.commit()
    db.refresh(row)
    item = _serialize(row)
    return CreateWebhookResponse(secret=secret, **item.model_dump())


@router.delete("/v1/webhooks/{webhook_id}")
def delete_webhook(webhook_id: str,
                   admin: User = Depends(require_role("admin")),
                   db: Session = Depends(get_db)):
    try:
        wid = uuid.UUID(str(webhook_id))
    except ValueError:
        raise HTTPException(status_code=422, detail="invalid webhook id")
    row = db.execute(
        select(WebhookSubscription).where(
            WebhookSubscription.id == wid, WebhookSubscription.org_id == admin.org_id)
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="webhook not found")
    db.delete(row)
    account_audit.record_account_action(
        db, org_id=admin.org_id, actor_email=admin.email, action="webhook.delete",
        target=row.url)
    db.commit()
    return {"status": "deleted", "id": str(wid)}


@router.post("/v1/webhooks/{webhook_id}/test")
def test_webhook(webhook_id: str,
                 admin: User = Depends(require_role("admin")),
                 db: Session = Depends(get_db)):
    """Send a signed test ping to the subscription and report the HTTP status."""
    try:
        wid = uuid.UUID(str(webhook_id))
    except ValueError:
        raise HTTPException(status_code=422, detail="invalid webhook id")
    row = db.execute(
        select(WebhookSubscription).where(
            WebhookSubscription.id == wid, WebhookSubscription.org_id == admin.org_id)
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="webhook not found")
    status = webhook_delivery._deliver_one(row, "test", {"message": "Foxy Audit test event"})
    row.last_status = status[:32]
    from datetime import datetime, timezone
    row.last_delivery_at = datetime.now(timezone.utc)
    db.commit()
    return {"status": status}
