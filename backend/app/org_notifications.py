"""Org-level breach notices — queued by grading, sent from their own thread.

This is the notice a tenant configures in their policy
(``org_policies.notify_on_breach == "immediate"``): one email to the org's
notification address, plus an optional webhook POST. It is a different thing
from the per-user alerts in ``user_notifications`` — that fan-out is driven by
each member's own preference bag, this one by the org's policy — and the two are
deduped against each other so no address receives the same breach twice.

**Why it moved out of the grading batch.** It used to be sent inline in
``worker._grade_one``: a synchronous ``send_email`` plus a 5-second webhook POST,
inside the loop that also drives the worker's liveness heartbeat. A mail
provider that accepts the connection and then hangs would stall the batch, and a
stalled batch stops the heartbeat and eventually trips ``/health/ready`` — a mail
outage taking the readiness probe down with it. Grading now only queues plain
values, exactly as the per-user path already did, and this module's thread does
the network work with no DB transaction held open across it.

Content-blind, like everything else here: seq, risk score and a truncated
reason — never prompt or response text.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
import uuid

import requests
from sqlalchemy.orm import Session

from . import email as email_mod, email_templates as et
from .models import Organization, OrgPolicy

log = logging.getLogger("foxy.org_notify")

#: Bounded on purpose. If the sender is down and the queue fills, dropping the
#: oldest notices is better than growing without limit inside the worker — the
#: breach itself is already durably recorded in the ledger, which is the part
#: that must never be lost.
_QUEUE_MAX = 2000
_NOTICE_QUEUE: "queue.Queue[dict]" = queue.Queue(maxsize=_QUEUE_MAX)

WEBHOOK_TIMEOUT = 5


def enqueue_breach_notice(row, verdict) -> None:
    """Called from worker._grade_one. Copies plain values off the grading row —
    nothing ORM- or session-bound crosses the thread boundary — never blocks and
    never raises into grading."""
    try:
        _NOTICE_QUEUE.put_nowait({
            "org_id": str(row["org_id"]),
            "seq": row.get("seq"),
            "risk": verdict.risk_score,
            "reason": (verdict.reason or "")[:200],
        })
    except queue.Full:
        log.warning("org breach-notice queue full — dropping notice for org %s "
                    "seq %s", row.get("org_id"), row.get("seq"))
    except Exception as exc:                # noqa: BLE001 — never break grading
        log.warning("could not queue org breach notice: %s", exc)


def send_breach_notice(db: Session, item: dict) -> bool:
    """Send one queued notice. Returns True if an email went out.

    The org's policy is re-read here rather than captured at enqueue time: a
    tenant who turns breach notices off between the grade and the send should
    not receive one."""
    oid = uuid.UUID(str(item["org_id"]))
    policy = db.get(OrgPolicy, oid)
    if policy is None or policy.notify_on_breach != "immediate":
        return False

    seq, risk, reason = item.get("seq"), item.get("risk"), item.get("reason") or ""
    org = db.get(Organization, oid)
    sent = False
    to = policy.notify_email or (org.contact_email if org else None)
    if to:
        html, plain = et.layout(
            title="Policy breach flagged",
            preheader=f"A policy breach was flagged in your audit trail (record #{seq}, risk {risk}).",
            blocks=[
                et.paragraph(f"A policy breach was flagged in your audit trail "
                             f"(record #{seq}, risk {risk})."),
                et.callout(reason, tone="bad"),
                et.muted("Open your dashboard ledger to review. Only hashes are stored — "
                         "never the prompt or response."),
            ],
            surface="customer",
        )
        email_mod.send_email(
            to=to, subject="\U0001f534 Policy breach flagged — Foxy Audit",
            html=html, text=plain)
        sent = True

    if policy.notify_webhook_url:
        try:
            requests.post(policy.notify_webhook_url, json={
                "type": "policy_breach", "seq": seq, "risk_score": risk,
                "reason": reason, "org_id": str(oid)}, timeout=WEBHOOK_TIMEOUT)
        except Exception:                   # noqa: BLE001 — webhook is best-effort
            log.warning("breach webhook POST failed for org %s", oid)
    return sent


def drain_breach_notices(db: Session, *, limit: int = 200) -> int:
    """Send every queued notice (up to `limit`). Returns emails sent."""
    sent = 0
    for _ in range(limit):
        try:
            item = _NOTICE_QUEUE.get_nowait()
        except queue.Empty:
            break
        try:
            sent += 1 if send_breach_notice(db, item) else 0
        except Exception as exc:            # noqa: BLE001 — one notice must not stop the drain
            db.rollback()
            log.warning("org breach notice failed for org %s: %s",
                        item.get("org_id"), exc)
    return sent


def org_notifications_loop(stopping: dict, s) -> None:
    """Own thread + session, mirroring usage.usage_loop.

    Deliberately NOT gated on ``user_notifications_enabled``: that switch turns
    off the per-user preference fan-out, and using it to also silence a tenant's
    configured policy notice would disable a paid feature by accident.
    """
    from .db import SessionLocal
    log.info("Org breach notices ON (drain=%ss)", s.breach_alert_drain_interval)
    while not stopping["flag"]:
        db = SessionLocal()
        try:
            drain_breach_notices(db)
        except Exception as exc:            # noqa: BLE001 — a bad pass must not kill the thread
            log.warning("org notifications loop error: %s", exc)
        finally:
            db.close()
        waited = 0.0
        while waited < s.breach_alert_drain_interval and not stopping["flag"]:
            time.sleep(min(1.0, s.breach_alert_drain_interval - waited))
            waited += 1.0


def queue_depth() -> int:
    """For tests and the admin health surface."""
    return _NOTICE_QUEUE.qsize()
