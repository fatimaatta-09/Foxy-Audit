"""Outbound webhook delivery (P3 · §F).

The worker calls deliver_grading() after a verdict is durably committed. Each
active subscription that matches gets ONE signed POST — body is canonical JSON,
signed HMAC-SHA256 with the subscription secret in `X-Foxy-Signature`. Delivery
is best-effort (short timeout, errors swallowed) and never raises into grading;
last_status/last_delivery_at are recorded for dashboard observability.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import WebhookSubscription

log = logging.getLogger("foxy.webhooks")

VALID_EVENTS = {"breach", "graded"}


def sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _deliver_one(sub: WebhookSubscription, event_type: str, payload: dict) -> str:
    body = json.dumps({"type": event_type, **payload},
                      sort_keys=True, separators=(",", ":")).encode("utf-8")
    try:
        r = requests.post(sub.url, data=body, timeout=5, headers={
            "Content-Type": "application/json",
            "X-Foxy-Event": event_type,
            "X-Foxy-Signature": "sha256=" + sign(sub.secret, body),
        })
        return str(r.status_code)
    except Exception:                        # noqa: BLE001 — delivery is best-effort
        log.warning("webhook delivery failed for sub %s", sub.id)
        return "error"


def _subs(db: Session, org_id) -> list[WebhookSubscription]:
    return db.execute(
        select(WebhookSubscription).where(
            WebhookSubscription.org_id == org_id,
            WebhookSubscription.active.is_(True))
    ).scalars().all()


def deliver_grading(db: Session, org_id, *, is_breach: bool, payload: dict) -> None:
    """Deliver a graded interaction to each matching subscription (one POST each:
    the most specific event the sub asked for). Commits status updates."""
    applicable = {"graded"} | ({"breach"} if is_breach else set())
    touched = False
    for sub in _subs(db, org_id):
        sub_events = {e.strip() for e in (sub.events or "").split(",") if e.strip()}
        matched = applicable & sub_events
        if not matched:
            continue
        event_type = "breach" if "breach" in matched else "graded"
        sub.last_status = _deliver_one(sub, event_type, payload)[:32]
        sub.last_delivery_at = datetime.now(timezone.utc)
        touched = True
    if touched:
        db.commit()
