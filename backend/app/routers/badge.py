"""GET /v1/badge/{token}.svg — public, embeddable trust badge (Phase 6 · 6C).

Opt-in per org (organizations.public_badge_token, minted via POST /v1/account/badge).
UNAUTHENTICATED and cacheable so it can be dropped into any page via <img>. Shows
AGGREGATE status only — verified state, audited count, last-anchor freshness — and
never the org_id, name, or any log. Rate-limited like the rest of the customer API.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..anchor import latest_anchor
from ..db import get_db
from ..models import AuditLog, Organization
from .logs import limiter

router = APIRouter()

_FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"


def _humanize_age(dt: datetime) -> str:
    secs = int((datetime.now(timezone.utc) - dt).total_seconds())
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


def _render_badge(*, verified: bool, audited: int, freshness: str | None) -> str:
    """Branded clay card (Foxy orange), two lines: status + audited count/freshness.
    All inputs are server-computed integers/fixed strings — no tenant text, no user
    input — so the SVG can't be an injection vector."""
    status = "✓ Verified" if verified else "Pending"
    status_color = "#1e9e5a" if verified else "#c98a1a"
    line2 = f"{audited:,} audited"
    line2 += f" · anchored {freshness}" if freshness else " · not yet anchored"
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="300" height="58" role="img" '
        'aria-label="Foxy Audit trust badge">'
        '<defs><filter id="fx-shadow" x="-10%" y="-10%" width="120%" height="150%">'
        '<feDropShadow dx="0" dy="2" stdDeviation="2.5" flood-color="#b08a5a" '
        'flood-opacity="0.22"/></filter></defs>'
        '<rect x="3" y="2" width="294" height="52" rx="14" fill="#f8f2e9" '
        'stroke="#ecdfca" stroke-width="1" filter="url(#fx-shadow)"/>'
        '<rect x="3" y="2" width="5" height="52" rx="2.5" fill="#ff7a1a"/>'
        f'<text x="20" y="24" font-family="{_FONT}" font-size="14" font-weight="700" '
        f'fill="#ff7a1a">🦊 Foxy Audit</text>'
        f'<text x="284" y="24" text-anchor="end" font-family="{_FONT}" font-size="12.5" '
        f'font-weight="700" fill="{status_color}">{status}</text>'
        f'<text x="20" y="43" font-family="{_FONT}" font-size="11" fill="#8a7a63">{line2}</text>'
        '</svg>'
    )


@router.get("/v1/badge/{token}.svg")
@limiter.limit("120/minute")
def badge_svg(token: str, request: Request, db: Session = Depends(get_db)):
    org = db.execute(
        select(Organization).where(
            Organization.public_badge_token == token,
            Organization.deleted_at.is_(None))
    ).scalar_one_or_none()
    if org is None:
        raise HTTPException(status_code=404, detail="unknown badge")

    audited = db.execute(
        select(func.count()).select_from(AuditLog).where(AuditLog.org_id == org.id)
    ).scalar_one()
    anchor = latest_anchor(db, org.id)
    verified = anchor is not None and anchor.status == "confirmed"
    freshness = (_humanize_age(anchor.anchored_at)
                 if verified and anchor.anchored_at else None)

    svg = _render_badge(verified=verified, audited=int(audited), freshness=freshness)
    # Cache 5 min: a badge is embedded on many pages; live-ish is fine, and this
    # shields the DB from every page view. Aggregate only, so caching leaks nothing.
    return Response(content=svg, media_type="image/svg+xml",
                    headers={"Cache-Control": "public, max-age=300"})
