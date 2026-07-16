"""Public marketing endpoints (site 1 → the backend).

POST /v1/leads   — capture a signup/lead from the marketing site (funnel entry).
POST /v1/track   — a lightweight marketing-page visit beacon → one traffic_events
                   row with site='marketing' (anonymous, org_id NULL).

Both are UNAUTHENTICATED and rate-limited (they face the public internet). They
store no secrets; the beacon hashes IP/UA with the server pepper like the traffic
middleware, and keeps only the path (no query string).
"""

from __future__ import annotations

import html as _html
import uuid

import requests
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import email as email_mod
from ..config import get_settings
from ..auth import hash_key
from ..db import SessionLocal, get_db
from ..models import MarketingLead, StaffUser, TrafficEvent
from .logs import limiter          # reuse the app's single Limiter instance

router = APIRouter()

# Sources whose messages are high-priority: they page every superadmin on arrival
# and sort to the top of the admin inbox (see admin_inbox._is_priority).
PRIORITY_SOURCES = {"enterprise", "demo"}


def _esc(s: str | None) -> str:
    return _html.escape(s or "")


def _verify_recaptcha(token: str | None) -> bool:
    """Server-side Google reCAPTCHA v2 check for the public demo form. Skips (True)
    when no secret is configured; fails closed on an explicit reject; fails OPEN on a
    network error so a Google outage never blocks a real prospect."""
    secret = get_settings().recaptcha_secret_key
    if not secret:
        return True
    if not token:
        return False
    try:
        r = requests.post("https://www.google.com/recaptcha/api/siteverify",
                          data={"secret": secret, "response": token}, timeout=8)
        data = r.json()
        # v3 returns a score (0.0–1.0); v2 has none. Require success, and for v3 a
        # score above the bot threshold.
        return bool(data.get("success")) and float(data.get("score", 1.0)) >= 0.5
    except Exception:                       # noqa: BLE001 — don't block real users on a Google outage
        return True


def _notify_priority_contact(name: str | None, email_addr: str, message: str | None) -> None:
    """Best-effort alert for a new priority (enterprise) contact — runs as a
    BackgroundTask so it never blocks /v1/leads. Emails every ENABLED superadmin at
    their registered staff address, plus a confirmation to the sender. send_email
    already swallows its own errors; we only guard the DB read."""
    db = SessionLocal()
    try:
        recipients = [s.email for s in db.execute(
            select(StaffUser).where(StaffUser.platform_role == "superadmin",
                                    StaffUser.disabled.is_(False))
        ).scalars().all()]
    except Exception:                       # noqa: BLE001 — notification is best-effort
        recipients = []
    finally:
        db.close()

    who = (name or email_addr or "Someone").strip()
    body = (message or "").strip()
    alert_html = (
        f"<p><b>{_esc(who)}</b> &lt;{_esc(email_addr)}&gt; sent a <b>priority</b> enterprise inquiry:</p>"
        f"<blockquote style='border-left:3px solid #ff7a2e;padding-left:12px;color:#333'>"
        f"{_esc(body).replace(chr(10), '<br>')}</blockquote>"
        f"<p>Open it in the admin inbox to claim &amp; reply.</p>")
    alert_text = (f"{who} <{email_addr}> sent a PRIORITY enterprise inquiry:\n\n{body}\n\n"
                  "Open the admin inbox to claim & reply.")
    for r in recipients:
        email_mod.send_email(to=r, subject=f"\U0001f534 Priority contact — {who}",
                             html=alert_html, text=alert_text)

    email_mod.send_email(
        to=email_addr, subject="We got your message — Foxy Audit",
        html=("<p>Thanks for reaching out to Foxy Audit about a custom plan.</p>"
              "<p>Your message is with our team and we'll get back to you shortly.</p>"
              "<p style='color:#888;font-size:12px'>— Foxy Audit</p>"),
        text=("Thanks for reaching out to Foxy Audit about a custom plan.\n"
              "Your message is with our team and we'll get back to you shortly.\n\n— Foxy Audit"))


class LeadRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    name: str | None = Field(default=None, max_length=255)
    company: str | None = Field(default=None, max_length=255)
    source: str | None = Field(default=None, max_length=64)
    subject: str | None = Field(default=None, max_length=128)
    message: str | None = Field(default=None, max_length=4000)
    utm_campaign: str | None = Field(default=None, max_length=128)
    utm_source: str | None = Field(default=None, max_length=128)
    utm_medium: str | None = Field(default=None, max_length=128)
    recaptcha_token: str | None = Field(default=None, max_length=4096)   # demo form (reCAPTCHA v2)


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
def create_lead(payload: LeadRequest, request: Request, background: BackgroundTasks,
                db: Session = Depends(get_db)):
    """Record a marketing lead. If an ACTIVE lead already exists for this email,
    update its details instead of duplicating (matches the partial-unique index)."""
    email = payload.email.strip().lower()
    is_priority = (payload.source or "").strip().lower() in PRIORITY_SOURCES
    has_msg = bool(payload.message and payload.message.strip())
    # The public demo form is bot-protected with reCAPTCHA (verified only when the
    # server secret is set; other lead sources don't carry a token).
    if (payload.source or "").strip().lower() == "demo" and not _verify_recaptcha(payload.recaptcha_token):
        raise HTTPException(status_code=400, detail="reCAPTCHA verification failed — please try again.")
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
        existing.subject = payload.subject or existing.subject
        if payload.message and payload.message.strip():
            new_msg = payload.message.strip()
            if existing.message and new_msg not in existing.message:
                # Preserve prior messages (e.g. multiple support notes) instead of
                # overwriting — append with a label, keep the most recent ~16k chars.
                label = payload.subject or existing.source or "message"
                existing.message = (existing.message.rstrip() + f"\n\n--- {label} ---\n" + new_msg)[-16000:]
            elif not existing.message:
                existing.message = new_msg
        existing.utm_campaign = payload.utm_campaign or existing.utm_campaign
        existing.utm_source = payload.utm_source or existing.utm_source
        existing.utm_medium = payload.utm_medium or existing.utm_medium
        db.commit()
        if is_priority and has_msg:
            background.add_task(_notify_priority_contact, payload.name, email, payload.message)
        return LeadResponse(status="updated", id=str(existing.id))

    lead = MarketingLead(
        email=email, name=payload.name, company=payload.company, source=payload.source,
        subject=payload.subject, message=payload.message,
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
    if is_priority and has_msg:
        background.add_task(_notify_priority_contact, payload.name, email, payload.message)
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
        status_code=200,
        ip_hash=hash_key(request.client.host) if request.client else None,
        ua_hash=hash_key(request.headers.get("user-agent")) if request.headers.get("user-agent") else None,
        referrer=referrer, visitor_id=_maybe_uuid(payload.visitor_id),
        session_id=_maybe_uuid(payload.session_id),
    )
    db.add(ev)
    db.commit()
    return {"status": "ok"}
