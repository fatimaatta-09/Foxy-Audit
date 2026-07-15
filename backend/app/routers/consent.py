"""POST /v1/consent — record a cookie-consent decision from the marketing banner.

Public + rate-limited. Writes one auditable consent_events row with the IP/UA
anonymized (HMAC pepper, like the traffic beacon). This endpoint sets NO cookies —
the banner stores the visitor's choice client-side; this is purely the server-side
audit trail (GDPR/CCPA "prove you obtained consent").
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..auth import hash_key
from ..db import get_db
from ..models import ConsentEvent
from .logs import limiter          # reuse the app's single Limiter instance

router = APIRouter()


class ConsentRequest(BaseModel):
    analytics: bool = False
    functional: bool = False
    region: str | None = Field(default=None, max_length=8)     # eu | us | other
    regime: str | None = Field(default=None, max_length=8)     # gdpr | ccpa
    policy_version: str | None = Field(default="1.0", max_length=16)


@router.post("/v1/consent")
@limiter.limit("30/minute")
def record_consent(payload: ConsentRequest, request: Request, db: Session = Depends(get_db)):
    ua = request.headers.get("user-agent")
    db.add(ConsentEvent(
        analytics=bool(payload.analytics),
        functional=bool(payload.functional),
        region=(payload.region or None),
        regime=(payload.regime or None),
        policy_version=(payload.policy_version or None),
        ip_hash=hash_key(request.client.host) if request.client else None,
        ua_hash=hash_key(ua) if ua else None,
    ))
    db.commit()
    return {"status": "recorded"}
