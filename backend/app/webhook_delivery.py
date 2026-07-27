"""Outbound webhook delivery (P3 · §F).

Grading QUEUES a delivery after a verdict is durably committed; this module's
own thread does the POSTing. Each active subscription that matches gets ONE
signed POST — body is canonical JSON, signed HMAC-SHA256 with the subscription
secret in `X-Foxy-Signature`. Delivery is best-effort (short timeout, errors
swallowed) and never raises into grading; last_status/last_delivery_at are
recorded for dashboard observability.

**Why it is queued.** `deliver_grading` used to be called inline from
`worker._grade_one`: one synchronous `requests.post(timeout=5)` per active
subscription, inside the loop that also drives the worker's liveness heartbeat.
An org with four subscriptions pointing at a host that accepts and then hangs
added up to twenty seconds to a single row — and this fires on EVERY graded
row, not just breaches, so it was the larger of the two heartbeat risks and the
one still live after the emails were decoupled.

Subscriptions are resolved at SEND time rather than captured at enqueue time,
so one deactivated between the grade and the send is not delivered to.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import queue
import time
from datetime import datetime, timezone

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import WebhookSubscription

log = logging.getLogger("foxy.webhooks")

VALID_EVENTS = {"breach", "graded"}

#: Bounded, like the notice queues. If subscribers are down and this fills,
#: dropping deliveries beats growing without limit inside the worker — the
#: graded row is durably in the ledger either way, which is the part that must
#: never be lost.
_QUEUE_MAX = 5000
_DELIVERY_QUEUE: "queue.Queue[dict]" = queue.Queue(maxsize=_QUEUE_MAX)


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


def enqueue_grading(org_id, *, is_breach: bool, payload: dict) -> None:
    """Called from worker._grade_one. Copies plain values only — nothing ORM-
    or session-bound crosses the thread boundary — never blocks and never
    raises into grading."""
    try:
        _DELIVERY_QUEUE.put_nowait({
            "org_id": str(org_id), "is_breach": bool(is_breach),
            "payload": dict(payload),
        })
    except queue.Full:
        log.warning("webhook delivery queue full — dropping delivery for org %s",
                    org_id)
    except Exception as exc:                # noqa: BLE001 — never break grading
        log.warning("could not queue webhook delivery: %s", exc)


def drain_deliveries(db: Session, *, limit: int = 500) -> int:
    """POST every queued delivery (up to `limit`). Returns subscriptions hit."""
    delivered = 0
    for _ in range(limit):
        try:
            item = _DELIVERY_QUEUE.get_nowait()
        except queue.Empty:
            break
        try:
            delivered += deliver_grading(
                db, item["org_id"], is_breach=item["is_breach"],
                payload=item["payload"])
        except Exception as exc:            # noqa: BLE001 — one must not stop the drain
            db.rollback()
            log.warning("webhook delivery failed for org %s: %s",
                        item.get("org_id"), exc)
    return delivered


def queue_depth() -> int:
    """For tests and the admin health surface."""
    return _DELIVERY_QUEUE.qsize()


def webhook_delivery_loop(stopping: dict, s) -> None:
    """Own thread + session, mirroring org_notifications.org_notifications_loop."""
    from .db import SessionLocal
    log.info("Webhook delivery ON (drain=%ss)", s.breach_alert_drain_interval)
    while not stopping["flag"]:
        db = SessionLocal()
        try:
            drain_deliveries(db)
        except Exception as exc:            # noqa: BLE001 — a bad pass must not kill the thread
            log.warning("webhook delivery loop error: %s", exc)
        finally:
            db.close()
        waited = 0.0
        while waited < s.breach_alert_drain_interval and not stopping["flag"]:
            time.sleep(min(1.0, s.breach_alert_drain_interval - waited))
            waited += 1.0


def deliver_grading(db: Session, org_id, *, is_breach: bool, payload: dict) -> int:
    """Deliver a graded interaction to each matching subscription (one POST each:
    the most specific event the sub asked for). Commits status updates.

    Returns the number of subscriptions POSTed to. Called from the drain thread;
    `enqueue_grading` is what grading calls."""
    applicable = {"graded"} | ({"breach"} if is_breach else set())
    delivered = 0
    for sub in _subs(db, org_id):
        sub_events = {e.strip() for e in (sub.events or "").split(",") if e.strip()}
        matched = applicable & sub_events
        if not matched:
            continue
        event_type = "breach" if "breach" in matched else "graded"
        sub.last_status = _deliver_one(sub, event_type, payload)[:32]
        sub.last_delivery_at = datetime.now(timezone.utc)
        delivered += 1
    if delivered:
        db.commit()
    return delivered
