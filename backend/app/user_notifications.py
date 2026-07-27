"""Per-user notification emails (Phase D-S — Settings → Notifications toggles).

Makes the three dashboard notification preferences real. Each sender is gated on
the recipient's own ``users.preferences`` bag, with defaults matching the
Settings UI's initial checkbox states:

* ``notify_breach_alerts`` (default ON) — one email per graded breach to every
  opted-in org member. Only when the ORG's policy asks for immediate breach
  notice (``org_policies.notify_on_breach``): a tenant that turned breach
  notifications off must not be overridden by a per-user default. Deduped
  against the org-level address in ``worker._notify_breach`` so no address gets
  the same breach twice.
* ``notify_weekly_digest`` (default ON) — Mondays, a summary of the just
  completed ISO week. Sent at most once per org per week via a ``notifications``
  marker row (kind='digest', target_id='YYYY-Www') that doubles as the in-app
  notification. Orgs with no activity that week are skipped — an all-zeros
  email is noise, not news.
* ``notify_key_rotation_reminders`` (default OFF — opt-in) — admins only, when
  an active API key is older than 90 days, at most once per 30 days per org.

Numbers come from ``audit_logs`` — the append-only source of truth — NOT from
the ``usage_daily`` rollup: that rollup recomputes on a rolling 48-hour window,
so rows older than ~2 days hold only a partial-day count. Reading it for a past
week would email understated figures, and a wrong number in a compliance email
is worse than no email.

Emails are content-blind (counts, seq, risk, reason — never prompt/response
text) and rendered through email_templates, which escapes at every boundary.

Threading: the breach fan-out is QUEUED by grading and sent from this module's
own thread, so a slow mail provider can never stall the grading batch (which
would starve the worker heartbeat) — and no DB transaction is held open across
a network send.

Runs under a BYPASSRLS role (like app/usage.py) so the cross-org queries see
every tenant; suspended and soft-deleted orgs are skipped.
"""

from __future__ import annotations

import logging
import queue
import time
import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from . import email as email_mod, email_templates as et
from .config import get_settings
from .models import ApiKey, Notification, Organization, OrgPolicy, User

log = logging.getLogger("foxy.user_notifications")

KEY_ROTATION_MAX_AGE_DAYS = 90
KEY_ROTATION_REMINDER_EVERY_DAYS = 30
# Bounded so a breach storm can never grow memory without limit; overflow is
# logged and dropped (the in-app notification + ledger still record everything).
_BREACH_QUEUE: queue.Queue = queue.Queue(maxsize=2000)


def _dashboard_url() -> str:
    return get_settings().dashboard_url


def _wants(user: User, key: str, *, default: bool) -> bool:
    """Per-user pref gate (mirrors app/notify.py._wants for staff)."""
    prefs = user.preferences or {}
    if default:
        return prefs.get(key, True) is not False   # default ON: absent = deliver
    return prefs.get(key) is True                  # default OFF: explicit opt-in


def _org_users(db: Session, org_id) -> list[User]:
    return db.execute(
        select(User).where(User.org_id == org_id, User.disabled.is_(False))
    ).scalars().all()


def _live_orgs(db: Session) -> list[Organization]:
    return db.execute(
        select(Organization).where(
            Organization.suspended.is_(False), Organization.deleted_at.is_(None))
    ).scalars().all()


def _marker_exists(db: Session, org_id, kind: str, target_id: str) -> bool:
    return db.execute(
        select(Notification.id).where(
            Notification.org_id == org_id, Notification.kind == kind,
            Notification.target_id == target_id).limit(1)
    ).first() is not None


def _marker_since(db: Session, org_id, kind: str, since: datetime) -> bool:
    """Any marker of this kind written since `since` (rolling-window dedupe)."""
    return db.execute(
        select(Notification.id).where(
            Notification.org_id == org_id, Notification.kind == kind,
            Notification.created_at >= since).limit(1)
    ).first() is not None


def _drop_marker(db: Session, marker_id) -> None:
    """Undo a marker whose emails all failed, so a later pass can retry."""
    try:
        row = db.get(Notification, marker_id)
        if row is not None:
            db.delete(row)
            db.commit()
    except Exception as exc:                # noqa: BLE001
        db.rollback()
        log.warning("could not roll back notification marker %s: %s", marker_id, exc)


# ── (a) per-user breach alerts — queued by grading, sent by this thread ─────
def enqueue_breach_alert(row, verdict) -> None:
    """Called from worker._grade_one. Copies plain values off the grading row
    (nothing ORM- or session-bound crosses the thread boundary), never blocks
    and never raises into grading."""
    # The kill switch has to stop the ENQUEUE, not just the drain. It gates the
    # thread that consumes this queue (worker.py), so with the switch off,
    # grading kept filling a queue nobody was reading: it reached its cap and
    # then logged "queue full" for every breach from then on. Nothing was lost —
    # the breach is in the ledger regardless — but the switch did not do what it
    # says, and the warnings made the worker log useless.
    if not get_settings().user_notifications_enabled:
        return
    try:
        _BREACH_QUEUE.put_nowait({
            "org_id": str(row["org_id"]),
            "seq": row.get("seq"),
            "risk": verdict.risk_score,
            "reason": (verdict.reason or "")[:200],
        })
    except queue.Full:
        log.warning("breach-alert queue full — dropping alert for org %s seq %s",
                    row.get("org_id"), row.get("seq"))
    except Exception as exc:                # noqa: BLE001 — never break grading
        log.warning("could not queue breach alert: %s", exc)


def drain_breach_alerts(db: Session, *, limit: int = 200) -> int:
    """Send every queued breach alert (up to `limit`). Returns emails sent."""
    sent = 0
    for _ in range(limit):
        try:
            item = _BREACH_QUEUE.get_nowait()
        except queue.Empty:
            break
        try:
            sent += send_breach_alert(db, item)
        except Exception as exc:            # noqa: BLE001 — one alert must not stop the drain
            db.rollback()
            log.warning("breach alert failed for org %s: %s", item.get("org_id"), exc)
    return sent


def send_breach_alert(db: Session, item: dict) -> int:
    """Email each opted-in org member about one graded breach.

    Honors the ORG policy first: if the tenant set breach notifications to
    anything but 'immediate', nobody is emailed — a per-user default must not
    re-enable what the org turned off. The org-level destination is skipped so
    that address is not emailed twice for one breach."""
    oid = uuid.UUID(str(item["org_id"]))
    org = db.get(Organization, oid)
    if org is None or org.suspended or org.deleted_at is not None:
        return 0
    policy = db.get(OrgPolicy, oid)
    # No policy row yet = the model's server_default ('immediate').
    mode = policy.notify_on_breach if policy is not None else "immediate"
    if mode != "immediate":
        return 0
    org_level_to = ((policy.notify_email if policy is not None else None)
                    or org.contact_email or "").strip().lower() or None

    recipients = [u.email for u in _org_users(db, oid)
                  if _wants(u, "notify_breach_alerts", default=True)
                  and u.email.strip().lower() != org_level_to]
    if not recipients:
        return 0
    seq, risk, reason = item["seq"], item["risk"], item["reason"]
    # Release the read transaction BEFORE any network send: holding it open
    # across a 10 s email timeout pins the autovacuum horizon on hot tables.
    db.commit()

    html, plain = et.layout(
        title="Policy breach flagged",
        preheader=f"A policy breach was flagged in your audit trail "
                  f"(record #{seq}, risk {risk}).",
        blocks=[
            et.paragraph(f"A policy breach was flagged in your organization's "
                         f"audit trail (record #{seq}, risk {risk})."),
            et.callout(reason, tone="bad"),
            et.muted("You get this email because breach alerts are on in "
                     "Settings → Notifications. Only hashes are stored — "
                     "never the prompt or response."),
        ],
        cta={"label": "Review in dashboard", "url": _dashboard_url()},
        surface="customer",
    )
    sent = 0
    for to in recipients:
        if email_mod.send_email(
                to=to, subject="\U0001f534 Policy breach flagged — Foxy Audit",
                html=html, text=plain):
            sent += 1
    return sent


# ── (b) weekly digest — Mondays, once per ISO week ──────────────────────────
def _last_completed_week(today: date) -> tuple[str, date, date]:
    """('YYYY-Www', monday, sunday) of the ISO week that just ended."""
    monday_this = today - timedelta(days=today.isoweekday() - 1)
    start = monday_this - timedelta(days=7)
    end = monday_this - timedelta(days=1)
    iso = start.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}", start, end


_WEEK_TOTALS_SQL = text(
    # Straight from audit_logs (append-only source of truth) — usage_daily's
    # rolling-48h rollup understates any day older than ~2 days.
    """
    SELECT count(*)                                                      AS logs,
           coalesce(sum(token_count), 0)                                 AS tokens,
           count(*) FILTER (WHERE gemini_verdict->>'policy_breach' = 'true') AS breaches,
           count(*) FILTER (WHERE grading_status = 'graded')              AS graded
      FROM audit_logs
     WHERE org_id = :oid
       AND created_at >= :start
       AND created_at <  :end
    """
)


def send_weekly_digests(db: Session, *, today: date | None = None) -> int:
    """Send each opted-in user a digest of the last completed ISO week.

    Mondays only. Orgs with no events that week are skipped. The marker row
    makes re-runs a no-op; if every email fails the marker is rolled back so a
    later pass retries instead of silently losing the week."""
    today = today or datetime.now(timezone.utc).date()
    if today.isoweekday() != 1:
        return 0
    week_id, start, end = _last_completed_week(today)
    start_ts = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    end_ts = start_ts + timedelta(days=7)          # half-open [Mon, next Mon)
    sent = 0
    for org in _live_orgs(db):
        try:
            if _marker_exists(db, org.id, "digest", week_id):
                continue
            recipients = [u.email for u in _org_users(db, org.id)
                          if _wants(u, "notify_weekly_digest", default=True)]
            if not recipients:
                continue
            row = db.execute(_WEEK_TOTALS_SQL,
                             {"oid": org.id, "start": start_ts, "end": end_ts}
                             ).mappings().first()
            logs, tokens = int(row["logs"]), int(row["tokens"])
            breaches, graded = int(row["breaches"]), int(row["graded"])
            if logs == 0:
                continue      # nothing happened — don't mail an all-zeros week
            org_name = org.name          # plain locals: no ORM access after commit
            # Marker (also the in-app notification) committed BEFORE sending, so
            # a crash mid-send cannot double-email this week.
            marker = Notification(
                org_id=org.id, kind="digest", level="info",
                title=f"Weekly digest — {logs:,} events, {breaches:,} breach(es)",
                body=(f"Week {week_id}: {logs:,} events logged · {tokens:,} tokens · "
                      f"{breaches:,} breach(es) flagged · {graded:,} graded."),
                target_type="digest", target_id=week_id)
            db.add(marker)
            db.commit()
            marker_id = marker.id
            status = (et.callout(f"{breaches:,} policy breach(es) were flagged last "
                                 f"week — review your ledger.", tone="warn")
                      if breaches else
                      et.callout("No policy breaches last week.", tone="ok"))
            html, plain = et.layout(
                title="Your week in review",
                preheader=f"Week {week_id}: {logs:,} events, {breaches:,} breach(es).",
                blocks=[
                    et.heading("Your week in review"),
                    et.paragraph(f"Here's what happened in {org_name}'s audit trail "
                                 f"during week {week_id} ({start.isoformat()} – "
                                 f"{end.isoformat()})."),
                    et.info_rows([
                        ("Events logged", f"{logs:,}"),
                        ("Tokens observed", f"{tokens:,}"),
                        ("Breaches flagged", f"{breaches:,}"),
                        ("Events graded", f"{graded:,}"),
                    ]),
                    status,
                    et.muted("You get this digest because it's on in Settings → "
                             "Notifications. Counts come straight from your "
                             "ledger — only metadata, never content."),
                ],
                cta={"label": "Open dashboard", "url": _dashboard_url()},
                surface="customer",
            )
            ok = 0
            for to in recipients:
                if email_mod.send_email(
                        to=to,
                        subject=f"Your Foxy Audit week — {logs:,} events, "
                                f"{breaches:,} breach(es)",
                        html=html, text=plain):
                    ok += 1
            if ok:
                sent += ok
            else:
                # Provider down / no API key: don't burn the week.
                _drop_marker(db, marker_id)
        except Exception as exc:            # noqa: BLE001 — one org must not stop the sweep
            db.rollback()
            log.warning("weekly digest failed for org %s: %s", org.id, exc)
    return sent


# ── (c) key-rotation reminders — daily check, at most every 30 days ─────────
def send_key_rotation_reminders(db: Session, *, today: date | None = None) -> int:
    """Remind opted-in ADMINS when active API keys are older than 90 days.

    Rolling 30-day dedupe (not a calendar month, which would let a reminder on
    the 31st repeat on the 1st). The marker is written only when someone is
    actually emailed, so an admin who opts in later still gets reminded."""
    today = today or datetime.now(timezone.utc).date()
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=KEY_ROTATION_MAX_AGE_DAYS)
    since = now - timedelta(days=KEY_ROTATION_REMINDER_EVERY_DAYS)
    sent = 0
    for org in _live_orgs(db):
        try:
            if _marker_since(db, org.id, "key_rotation", since):
                continue
            stale = db.execute(
                select(ApiKey).where(
                    ApiKey.org_id == org.id, ApiKey.status == "active",
                    ApiKey.created_at < cutoff)
            ).scalars().all()
            rows = [(k.name, f"{(now - k.created_at).days} days old") for k in stale
                    if k.expires_at is None or k.expires_at > now]
            if not rows:
                continue
            admins = [u.email for u in _org_users(db, org.id)
                      if u.role == "admin"
                      and _wants(u, "notify_key_rotation_reminders", default=False)]
            if not admins:
                continue
            org_name = org.name          # plain local: no ORM access after commit
            marker = Notification(
                org_id=org.id, kind="key_rotation", level="warning",
                title=f"{len(rows)} API key(s) older than {KEY_ROTATION_MAX_AGE_DAYS} days",
                body=("Rotating long-lived keys limits the blast radius of a leak. "
                      "Rotate from Access → API keys."),
                target_type="key_rotation", target_id=today.isoformat())
            db.add(marker)
            db.commit()
            marker_id = marker.id
            html, plain = et.layout(
                title="API keys due for rotation",
                preheader=f"{len(rows)} active API key(s) are older than "
                          f"{KEY_ROTATION_MAX_AGE_DAYS} days.",
                blocks=[
                    et.heading("API keys due for rotation"),
                    et.paragraph(f"{len(rows)} active API key(s) in {org_name} are older "
                                 f"than {KEY_ROTATION_MAX_AGE_DAYS} days:"),
                    et.info_rows(rows),
                    et.muted("Rotating long-lived keys limits the blast radius of a "
                             "leak. You get this nudge because key-rotation "
                             "reminders are on in Settings → Notifications."),
                ],
                cta={"label": "Manage API keys", "url": _dashboard_url()},
                surface="customer",
            )
            ok = 0
            for to in admins:
                if email_mod.send_email(
                        to=to,
                        subject=f"⚠️ {len(rows)} API key(s) due for rotation "
                                f"— Foxy Audit",
                        html=html, text=plain):
                    ok += 1
            if ok:
                sent += ok
            else:
                _drop_marker(db, marker_id)
        except Exception as exc:            # noqa: BLE001 — one org must not stop the sweep
            db.rollback()
            log.warning("key-rotation reminder failed for org %s: %s", org.id, exc)
    return sent


# ── worker thread ───────────────────────────────────────────────────────────
def run_once(db: Session, *, today: date | None = None) -> None:
    """One digest + rotation pass. Both jobs dedupe, so any call frequency is
    safe (the breach queue is drained separately, on a much shorter tick)."""
    send_weekly_digests(db, today=today)
    send_key_rotation_reminders(db, today=today)


def user_notifications_loop(stopping: dict, s) -> None:
    """Drain queued breach alerts on a short tick; run the digest + rotation
    sweep on the long one. Own thread + session (mirrors usage.usage_loop) so a
    slow mail provider never stalls grading."""
    from .db import SessionLocal
    log.info("User notification jobs ON (sweep=%ss drain=%ss)",
             s.user_notifications_interval, s.breach_alert_drain_interval)
    last_sweep = 0.0
    while not stopping["flag"]:
        db = SessionLocal()
        try:
            drain_breach_alerts(db)
            now = time.monotonic()
            if now - last_sweep >= s.user_notifications_interval:
                last_sweep = now
                run_once(db)
        except Exception as exc:            # noqa: BLE001 — a bad pass must not kill the thread
            log.warning("user notifications loop error: %s", exc)
        finally:
            db.close()
        waited = 0.0
        while waited < s.breach_alert_drain_interval and not stopping["flag"]:
            time.sleep(min(1.0, s.breach_alert_drain_interval - waited))
            waited += 1.0
