"""Per-user notification emails (Phase D-S — Settings → Notifications toggles).

Makes the three dashboard notification preferences real. Each sender is gated on
the recipient's own ``users.preferences`` bag, with defaults matching the
Settings UI's initial checkbox states:

* ``notify_breach_alerts`` (default ON) — one email per graded breach to every
  opted-in org member, deduped against the org-level notifier in
  ``worker._notify_breach`` so no address ever gets the same breach twice.
* ``notify_weekly_digest`` (default ON) — Mondays, a summary of the just-
  completed ISO week from the ``usage_daily`` rollup. Sent at most once per
  org per week via a ``notifications`` marker row (kind='digest',
  target_id='YYYY-Www') that doubles as the in-app notification.
* ``notify_key_rotation_reminders`` (default OFF — opt-in) — admins only, when
  an active API key is older than 90 days. At most once per org per calendar
  month via a marker row (kind='key_rotation', target_id='YYYY-MM').

All numbers come from real rows (usage_daily / api_keys) — never fabricated.
Emails are content-blind (seq + risk + reason + counts; no prompt/response
text) and rendered through email_templates, which escapes at every boundary.

Runs in the worker under a BYPASSRLS role (like app/usage.py) so the cross-org
queries see every tenant; suspended and soft-deleted orgs are skipped. The
weekly/monthly jobs write their marker row and COMMIT BEFORE sending, so a
crash mid-send can never double-email a week/month (at-most-once delivery).
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from . import email as email_mod, email_templates as et
from .models import ApiKey, Notification, Organization, OrgPolicy, User

log = logging.getLogger("foxy.user_notifications")

DASHBOARD_URL = "https://app.foxyaudit.tech/dashboard"
KEY_ROTATION_MAX_AGE_DAYS = 90


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


# ── (a) per-user breach alerts — called from worker._grade_one ──────────────
def send_breach_alerts(db: Session, row, verdict) -> int:
    """Email each opted-in org member about one graded breach.

    Called AFTER the verdict commit, right after the org-level notifier —
    never raises into grading. The org-level destination (policy.notify_email
    or the org contact) is skipped here when that path just fired, so nobody
    is emailed twice for the same breach. Returns emails sent."""
    sent = 0
    try:
        oid = row["org_id"] if isinstance(row["org_id"], uuid.UUID) else uuid.UUID(str(row["org_id"]))
        policy = db.get(OrgPolicy, oid)
        org = db.get(Organization, oid)
        if org is None or org.suspended or org.deleted_at is not None:
            return 0
        org_level_to = None
        if policy is not None and policy.notify_on_breach == "immediate":
            org_level_to = (policy.notify_email
                            or (org.contact_email if org else None) or "").lower() or None
        seq = row.get("seq")
        risk = verdict.risk_score
        reason = (verdict.reason or "")[:200]
        for user in _org_users(db, oid):
            if not _wants(user, "notify_breach_alerts", default=True):
                continue
            if org_level_to and user.email.lower() == org_level_to:
                continue   # the org-level notifier already emailed this address
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
                cta={"label": "Review in dashboard", "url": DASHBOARD_URL},
                surface="customer",
            )
            if email_mod.send_email(
                    to=user.email,
                    subject="\U0001f534 Policy breach flagged — Foxy Audit",
                    html=html, text=plain):
                sent += 1
    except Exception as exc:                # noqa: BLE001 — notify never breaks grading
        log.warning("per-user breach alerts failed: %s", exc)
    return sent


# ── (b) weekly digest — Mondays, once per ISO week ──────────────────────────
def _last_completed_week(today: date) -> tuple[str, date, date]:
    """('YYYY-Www', monday, sunday) of the ISO week that just ended."""
    monday_this = today - timedelta(days=today.isoweekday() - 1)
    start = monday_this - timedelta(days=7)
    end = monday_this - timedelta(days=1)
    iso = start.isocalendar()
    return f"{iso.year}-W{iso.week:02d}", start, end


def send_weekly_digests(db: Session, *, today: date | None = None) -> int:
    """Send each opted-in user a digest of the last completed ISO week.

    Fires on Mondays only; the marker row makes re-runs (and multi-process
    workers, modulo a tiny race — notifications has no unique constraint) a
    no-op. Numbers are the org's real usage_daily sums. Returns emails sent."""
    today = today or datetime.now(timezone.utc).date()
    if today.isoweekday() != 1:
        return 0
    week_id, start, end = _last_completed_week(today)
    sent = 0
    for org in _live_orgs(db):
        try:
            if _marker_exists(db, org.id, "digest", week_id):
                continue
            recipients = [u for u in _org_users(db, org.id)
                          if _wants(u, "notify_weekly_digest", default=True)]
            if not recipients:
                continue
            row = db.execute(text(
                "SELECT COALESCE(SUM(logs_count),0)   AS logs, "
                "       COALESCE(SUM(tokens_sum),0)   AS tokens, "
                "       COALESCE(SUM(breach_count),0) AS breaches, "
                "       COALESCE(SUM(graded_count),0) AS graded "
                "FROM usage_daily WHERE org_id = :oid AND day BETWEEN :s AND :e"),
                {"oid": org.id, "s": start, "e": end}).mappings().first()
            logs, tokens = int(row["logs"]), int(row["tokens"])
            breaches, graded = int(row["breaches"]), int(row["graded"])
            # Marker (also the in-app notification) committed BEFORE sending →
            # a crash mid-send can never double-email this week.
            db.add(Notification(
                org_id=org.id, kind="digest", level="info",
                title=f"Weekly digest — {logs:,} events, {breaches:,} breach(es)",
                body=(f"Week {week_id}: {logs:,} events logged · {tokens:,} tokens · "
                      f"{breaches:,} breach(es) flagged · {graded:,} graded."),
                target_type="digest", target_id=week_id))
            db.commit()
            if breaches:
                status = et.callout(f"{breaches:,} policy breach(es) were flagged last "
                                    f"week — review your ledger.", tone="warn")
            else:
                status = et.callout("No policy breaches last week.", tone="ok")
            html, plain = et.layout(
                title="Your week in review",
                preheader=f"Week {week_id}: {logs:,} events, {breaches:,} breach(es).",
                blocks=[
                    et.heading("Your week in review"),
                    et.paragraph(f"Here's what happened in {org.name}'s audit trail "
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
                             "Notifications. Counts come from your ledger's daily "
                             "rollup — only metadata, never content."),
                ],
                cta={"label": "Open dashboard", "url": DASHBOARD_URL},
                surface="customer",
            )
            for u in recipients:
                if email_mod.send_email(
                        to=u.email,
                        subject=f"Your Foxy Audit week — {logs:,} events, "
                                f"{breaches:,} breach(es)",
                        html=html, text=plain):
                    sent += 1
        except Exception as exc:            # noqa: BLE001 — one org must not stop the sweep
            db.rollback()
            log.warning("weekly digest failed for org %s: %s", org.id, exc)
    return sent


# ── (c) key-rotation reminders — daily check, once per month ────────────────
def send_key_rotation_reminders(db: Session, *, today: date | None = None) -> int:
    """Remind opted-in ADMINS when active API keys are older than 90 days.

    At most one reminder per org per calendar month (marker row). The marker is
    only written when someone is actually emailed, so an admin who opts in
    later still gets that month's reminder. Returns emails sent."""
    today = today or datetime.now(timezone.utc).date()
    month_id = f"{today.year}-{today.month:02d}"
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=KEY_ROTATION_MAX_AGE_DAYS)
    sent = 0
    for org in _live_orgs(db):
        try:
            if _marker_exists(db, org.id, "key_rotation", month_id):
                continue
            stale = db.execute(
                select(ApiKey).where(
                    ApiKey.org_id == org.id, ApiKey.status == "active",
                    ApiKey.created_at < cutoff)
            ).scalars().all()
            stale = [k for k in stale
                     if k.expires_at is None or k.expires_at > now]
            if not stale:
                continue
            admins = [u for u in _org_users(db, org.id)
                      if u.role == "admin"
                      and _wants(u, "notify_key_rotation_reminders", default=False)]
            if not admins:
                continue
            db.add(Notification(
                org_id=org.id, kind="key_rotation", level="warning",
                title=f"{len(stale)} API key(s) older than {KEY_ROTATION_MAX_AGE_DAYS} days",
                body=("Rotating long-lived keys limits the blast radius of a leak. "
                      "Rotate from Access → API keys."),
                target_type="key_rotation", target_id=month_id))
            db.commit()
            rows = [(k.name, f"{(now - k.created_at).days} days old") for k in stale]
            html, plain = et.layout(
                title="API keys due for rotation",
                preheader=f"{len(stale)} active API key(s) are older than "
                          f"{KEY_ROTATION_MAX_AGE_DAYS} days.",
                blocks=[
                    et.heading("API keys due for rotation"),
                    et.paragraph(f"{len(stale)} active API key(s) in {org.name} are older "
                                 f"than {KEY_ROTATION_MAX_AGE_DAYS} days:"),
                    et.info_rows(rows),
                    et.muted("Rotating long-lived keys limits the blast radius of a "
                             "leak. You get this monthly nudge because key-rotation "
                             "reminders are on in Settings → Notifications."),
                ],
                cta={"label": "Manage API keys", "url": DASHBOARD_URL},
                surface="customer",
            )
            for u in admins:
                if email_mod.send_email(
                        to=u.email,
                        subject=f"⚠️ {len(stale)} API key(s) due for rotation "
                                f"— Foxy Audit",
                        html=html, text=plain):
                    sent += 1
        except Exception as exc:            # noqa: BLE001 — one org must not stop the sweep
            db.rollback()
            log.warning("key-rotation reminder failed for org %s: %s", org.id, exc)
    return sent


# ── worker thread ───────────────────────────────────────────────────────────
def run_once(db: Session, *, today: date | None = None) -> None:
    """One digest + rotation pass. Both jobs dedupe via marker rows, so any
    call frequency is safe."""
    send_weekly_digests(db, today=today)
    send_key_rotation_reminders(db, today=today)


def user_notifications_loop(stopping: dict, s) -> None:
    """Periodic pass in its OWN thread + session (mirrors usage.usage_loop)."""
    import time
    from .db import SessionLocal
    log.info("User notification jobs ON (interval=%ss)", s.user_notifications_interval)
    while not stopping["flag"]:
        db = SessionLocal()
        try:
            run_once(db)
        except Exception as exc:            # noqa: BLE001 — a bad pass must not kill the thread
            log.warning("user notifications loop error: %s", exc)
        finally:
            db.close()
        waited = 0.0
        while waited < s.user_notifications_interval and not stopping["flag"]:
            time.sleep(min(1.0, s.user_notifications_interval - waited))
            waited += 1.0
