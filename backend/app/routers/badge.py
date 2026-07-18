"""GET /v1/badge/{token}.svg — public, embeddable trust badge (Phase 6 · 6C).

Opt-in per org (organizations.public_badge_token, minted via POST /v1/account/badge).
UNAUTHENTICATED and cacheable so it can be dropped into any page via <img>. Shows
AGGREGATE status only — verified state, audited count, last-anchor freshness — and
never the org_id, name, or any log. Rate-limited like the rest of the customer API.
"""

from __future__ import annotations

import html as _html
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..anchor import latest_anchor
from ..db import get_db
from ..models import AuditLog, Organization
from .logs import limiter

router = APIRouter()


def _explorer_tx(chain: str | None, tx_hash: str | None) -> str | None:
    """Public block-explorer URL for an anchor tx, or None for the stub provider."""
    if not tx_hash or not chain:
        return None
    c = chain.lower()
    if "sepolia" in c:
        return f"https://sepolia.etherscan.io/tx/{tx_hash}"
    if "mainnet" in c or c in ("ethereum", "evm"):
        return f"https://etherscan.io/tx/{tx_hash}"
    return None

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


def _org_by_token(token: str, db: Session) -> Organization:
    org = db.execute(
        select(Organization).where(
            Organization.public_badge_token == token,
            Organization.deleted_at.is_(None))
    ).scalar_one_or_none()
    if org is None:
        raise HTTPException(status_code=404, detail="unknown verification link")
    return org


class PublicStatus(BaseModel):
    """AGGREGATE, content-blind status for a public verify link — deliberately
    excludes org_id, org name, and every per-record hash. What a customer's buyer
    sees to trust the audit trail, nothing more."""
    verified: bool
    audited: int
    anchored: bool
    freshness: str | None = None
    anchored_at: str | None = None
    chain: str | None = None
    explorer_url: str | None = None


def _aggregate_status(org: Organization, db: Session) -> PublicStatus:
    audited = db.execute(
        select(func.count()).select_from(AuditLog).where(AuditLog.org_id == org.id)
    ).scalar_one()
    anchor = latest_anchor(db, org.id)
    verified = anchor is not None and anchor.status == "confirmed"
    freshness = (_humanize_age(anchor.anchored_at)
                 if verified and anchor.anchored_at else None)
    return PublicStatus(
        verified=verified, audited=int(audited), anchored=anchor is not None,
        freshness=freshness,
        anchored_at=anchor.anchored_at.isoformat() if (verified and anchor and anchor.anchored_at) else None,
        chain=anchor.chain if verified and anchor else None,
        explorer_url=_explorer_tx(anchor.chain, anchor.tx_hash) if verified and anchor else None,
    )


@router.get("/v1/badge/{token}/status", response_model=PublicStatus)
@limiter.limit("120/minute")
def badge_status(token: str, request: Request, db: Session = Depends(get_db)):
    """JSON aggregate status behind a public verify link (no auth). Content-blind."""
    status = _aggregate_status(_org_by_token(token, db), db)
    return Response(content=status.model_dump_json(), media_type="application/json",
                    headers={"Cache-Control": "public, max-age=300"})


@router.get("/verify/{token}")
@limiter.limit("120/minute")
def public_verify_page(token: str, request: Request, db: Session = Depends(get_db)):
    """A shareable, no-login page a customer sends to their buyer/auditor. Shows
    aggregate verified status only (reuses the badge token) — never logs, hashes,
    or org identity. 404s for an unknown/revoked token."""
    status = _aggregate_status(_org_by_token(token, db), db)  # 404s before rendering
    st_txt = "Verified & anchored" if status.verified else (
        "Awaiting first anchor" if status.anchored else "Active — not yet anchored")
    st_color = "#1e9e5a" if status.verified else "#c98a1a"
    audited = f"{status.audited:,}"
    fresh = (f"Last anchored on the public chain {_html.escape(status.freshness)}."
             if status.freshness else "The next anchor is scheduled on this workspace's plan cadence.")
    explorer = (f'<a class="etx" href="{_html.escape(status.explorer_url)}" target="_blank" '
                f'rel="noopener">View the anchoring transaction on Etherscan &#8599;</a>'
                if status.explorer_url else "")
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex">
<title>Foxy Audit — verification status</title>
<style>
:root{{--bg:#f5efe6;--card:#fbf6ee;--ink:#2b2117;--muted:#8a7a63;--fox:#ff7a1a;--line:#ecdfca;}}
*{{box-sizing:border-box}}body{{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;padding:24px}}
.card{{width:100%;max-width:440px;background:var(--card);border:1px solid var(--line);border-radius:20px;
box-shadow:0 8px 30px rgba(176,138,90,.18);padding:30px 28px}}
.brand{{display:flex;align-items:center;gap:8px;font-weight:800;color:var(--fox);font-size:15px}}
.status{{margin:22px 0 6px;font-size:22px;font-weight:800;color:{st_color}}}
.sub{{color:var(--muted);font-size:13px;line-height:1.6}}
.rows{{margin:22px 0 6px;border-top:1px dashed var(--line);padding-top:16px}}
.row{{display:flex;justify-content:space-between;padding:7px 0;font-size:13.5px}}
.row b{{font-weight:700}}.etx{{color:var(--fox);font-size:13px;text-decoration:none;font-weight:600}}
.foot{{margin-top:18px;font-size:11px;color:var(--muted);line-height:1.6;border-top:1px dashed var(--line);padding-top:14px}}
.dot{{width:9px;height:9px;border-radius:50%;background:{st_color};display:inline-block;margin-right:7px}}
</style></head><body><div class="card">
<div class="brand">&#129418; Foxy Audit</div>
<div class="status"><span class="dot"></span>{st_txt}</div>
<div class="sub">This workspace maintains a tamper-evident, hash-chained audit trail of its AI
interactions. The status below is independently anchored to a public blockchain.</div>
<div class="rows">
  <div class="row"><span class="muted">Interactions audited</span><b>{audited}</b></div>
  <div class="row"><span class="muted">Public-chain anchor</span><b style="color:{st_color}">{"confirmed" if status.verified else ("pending" if status.anchored else "none yet")}</b></div>
</div>
<div class="sub" style="margin-top:6px">{fresh}</div>
<div style="margin-top:14px">{explorer}</div>
<div class="foot">Aggregate status only &mdash; no prompts, responses, hashes, or workspace identity
are exposed here. Learn how it works at <a class="etx" href="https://foxyaudit.tech" target="_blank" rel="noopener">foxyaudit.tech</a>.</div>
</div></body></html>"""
    return Response(content=page, media_type="text/html",
                    headers={"Cache-Control": "public, max-age=120"})
