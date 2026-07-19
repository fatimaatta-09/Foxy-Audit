"""POST /v1/passport — generate a cryptographically verifiable compliance report.

Queries the last 30 days (or a custom range) of audit logs for the calling org,
recomputes the chain to verify integrity, aggregates stats by policy tag, and
renders the ``compliance_passport.html`` Jinja2 template.

Returns **HTML** — the caller (dashboard or browser) can print-to-PDF or render
directly.  This avoids heavy native dependencies like weasyprint in production.
"""

from __future__ import annotations

import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse, Response
from jinja2 import Environment, FileSystemLoader
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..anchor import latest_anchor
from ..auth import resolve_org
from ..chain import GENESIS_HASH, compute_chain_hash
from ..db import get_db
from ..evidence_coverage import calculate_capture_coverage
from ..models import AuditLog, Organization
from ..policy_snapshot import policy_snapshot_hash

log = logging.getLogger("foxy.passport")
router = APIRouter()

# templates/ lives at  backend/app/templates/, NOT backend/app/routers/templates/
# os.path.dirname(__file__) == .../backend/app/routers — so we go one level up.
_TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "..", "templates")
_jinja_env = Environment(
    loader=FileSystemLoader(_TEMPLATE_DIR),
    autoescape=True,
)


@router.post("/v1/passport")
def generate_passport(
    org: Organization = Depends(resolve_org),
    db: Session = Depends(get_db),
    days: int = Query(default=30, ge=1, le=365),
    date_from: str | None = Query(default=None, description="ISO date YYYY-MM-DD; overrides `days`"),
    date_to: str | None = Query(default=None, description="ISO date YYYY-MM-DD; defaults to now"),
):
    now = datetime.now(timezone.utc)

    def _parse(d: str | None):
        if not d:
            return None
        try:
            return datetime.fromisoformat(d).replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    # An explicit from/to range (dashboard date pickers) overrides the rolling
    # `days` window. window_end is exclusive-of-next-day so the whole 'to' day counts.
    start = _parse(date_from)
    end_day = _parse(date_to)
    window_end = (end_day + timedelta(days=1)) if end_day else now
    if start is None:
        start = now - timedelta(days=days)

    rows = db.execute(
        select(AuditLog)
        .where(AuditLog.org_id == org.id,
               AuditLog.created_at >= start,
               AuditLog.created_at < window_end)
        .order_by(AuditLog.seq.asc())
    ).scalars().all()

    # ── Verify the chain ─────────────────────────────────────────────────
    chain_verified: bool | None = True if rows else None
    broken_seq = None
    chain_detail = "Report-period chain hashes verified."
    prev_hash = GENESIS_HASH

    # If the window doesn't start at seq 1, fetch the hash of the row before.
    if rows and rows[0].seq > 1:
        predecessor = db.execute(
            select(AuditLog.chain_hash)
            .where(AuditLog.org_id == org.id, AuditLog.seq == rows[0].seq - 1)
        ).scalar_one_or_none()
        if predecessor:
            prev_hash = predecessor

    for row in rows:
        expected = compute_chain_hash(
            org_id=org.id,
            prompt_hash=row.prompt_hash,
            response_hash=row.response_hash,
            token_count=row.token_count,
            policy_tag=row.policy_tag,
            seq=row.seq,
            prev_hash=prev_hash,
            agent=row.agent, chain_version=row.chain_version or 1,
            event_id=row.event_id, client_id=row.client_id, client_seq=row.client_seq,
            event_type=row.event_type, commitment_alg=row.commitment_alg,
            event_metadata=row.event_metadata, pii_signals=row.pii_signals,
            occurred_at=row.occurred_at,
        )
        if expected != row.chain_hash:
            chain_verified = False
            broken_seq = row.seq
            chain_detail = f"Report-period chain hash mismatch at seq {row.seq}."
            break
        prev_hash = row.chain_hash
    if not rows:
        chain_detail = "No audit events were captured in the selected report period."

    # ── Aggregate stats ──────────────────────────────────────────────────
    policy_stats: dict[str, dict] = defaultdict(
        lambda: {"count": 0, "breaches": 0, "total_tokens": 0}
    )
    policy_snapshots: dict[str, dict] = {}
    breach_events = 0
    for row in rows:
        tag = row.policy_tag
        policy_stats[tag]["count"] += 1
        policy_stats[tag]["total_tokens"] += row.token_count
        verdict = row.gemini_verdict or {}
        if verdict.get("policy_breach"):
            policy_stats[tag]["breaches"] += 1
            breach_events += 1
        metadata = row.event_metadata or {}
        snapshot = metadata.get("policy_snapshot")
        snapshot_hash = metadata.get("policy_snapshot_hash")
        if (isinstance(snapshot, dict) and isinstance(snapshot_hash, str)
                and policy_snapshot_hash(snapshot) == snapshot_hash):
            item = policy_snapshots.setdefault(snapshot_hash, {
                "hash": snapshot_hash, "events": 0, "snapshot": snapshot,
            })
            item["events"] += 1

    policies = []
    for tag, s in sorted(policy_stats.items()):
        policies.append({
            "tag": tag,
            "count": s["count"],
            "breaches": s["breaches"],
            "avg_tokens": round(s["total_tokens"] / s["count"]) if s["count"] else 0,
        })
    policy_snapshot_rows = sorted(
        policy_snapshots.values(), key=lambda item: item["hash"]
    )

    total_events = len(rows)
    compliant_events = total_events - breach_events
    compliance_rate = round(
        (compliant_events / total_events * 100) if total_events else 100, 1
    )

    coverage = calculate_capture_coverage(
        db,
        org.id,
        start=start,
        end=window_end,
        limit=25,
        chain_verified=chain_verified,
        chain_detail=chain_detail,
    )

    # ── Public-chain anchor (Phase 3 A1) — cite it if the org has one ────
    anchor = latest_anchor(db, org.id)
    anchor_ctx = None
    if anchor is not None:
        anchor_ctx = {
            "chain": anchor.chain,
            "status": anchor.status,
            "tx_hash": anchor.tx_hash,
            "block_number": anchor.block_number,
            "root_hash": anchor.root_hash,
            "last_seq": anchor.last_seq,
            "anchored_at": anchor.anchored_at.strftime("%Y-%m-%d %H:%M:%S UTC")
            if anchor.anchored_at else None,
        }

    # ── Render ───────────────────────────────────────────────────────────
    template = _jinja_env.get_template("compliance_passport.html")
    html_string = template.render(
        org_name=org.name,
        org_id=str(org.id),
        plan_tier=getattr(org, "plan_tier", None),
        date_from=start.strftime("%Y-%m-%d"),
        date_to=(end_day or now).strftime("%Y-%m-%d"),
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        total_events=total_events,
        compliant_events=compliant_events,
        breach_events=breach_events,
        compliance_rate=compliance_rate,
        policies=policies,
        policy_snapshots=policy_snapshot_rows,
        chain_verification=("verified" if chain_verified is True
                            else "failed" if chain_verified is False
                            else "not_checked"),
        chain_detail=chain_detail,
        broken_seq=broken_seq,
        first_seq=rows[0].seq if rows else "—",
        last_seq=rows[-1].seq if rows else "—",
        root_hash=rows[-1].chain_hash if rows else GENESIS_HASH,
        genesis_hash=GENESIS_HASH,
        anchor=anchor_ctx,
        coverage=coverage,
    )
    
    # Prefer PDF, but degrade to HTML if weasyprint's native libraries
    # aren't installed (the review flagged weasyprint as a deploy risk — a missing
    # lib must not take down the endpoint, and the import stays lazy so the app
    # still boots without it).
    try:
        from weasyprint import HTML
        pdf_bytes = HTML(string=html_string).write_pdf()
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": "attachment; filename=passport.pdf"},
        )
    except Exception as exc:
        log.warning("weasyprint unavailable — returning HTML passport: %s", exc)
        return HTMLResponse(content=html_string)
