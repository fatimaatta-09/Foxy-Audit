"""POST /v1/passport — generate a cryptographically verifiable compliance report.

Queries the last 30 days (or a custom range) of audit logs for the calling org,
recomputes the chain to verify integrity, aggregates stats by policy tag, and
renders the ``compliance_passport.html`` Jinja2 template.

Returns **HTML** — the caller (dashboard or browser) can print-to-PDF or render
directly.  This avoids heavy native dependencies like weasyprint in production.
"""

from __future__ import annotations

import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import require_org
from ..chain import GENESIS_HASH, compute_chain_hash
from ..db import get_db
from ..models import AuditLog, Organization

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
    org: Organization = Depends(require_org),
    db: Session = Depends(get_db),
    days: int = Query(default=30, ge=1, le=365),
):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    rows = db.execute(
        select(AuditLog)
        .where(AuditLog.org_id == org.id, AuditLog.created_at >= cutoff)
        .order_by(AuditLog.seq.asc())
    ).scalars().all()

    # ── Verify the chain ─────────────────────────────────────────────────
    chain_intact = True
    broken_seq = None
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
        )
        if expected != row.chain_hash:
            chain_intact = False
            broken_seq = row.seq
            break
        prev_hash = row.chain_hash

    # ── Aggregate stats ──────────────────────────────────────────────────
    policy_stats: dict[str, dict] = defaultdict(
        lambda: {"count": 0, "breaches": 0, "total_tokens": 0}
    )
    breach_events = 0
    for row in rows:
        tag = row.policy_tag
        policy_stats[tag]["count"] += 1
        policy_stats[tag]["total_tokens"] += row.token_count
        verdict = row.gemini_verdict or {}
        if verdict.get("policy_breach"):
            policy_stats[tag]["breaches"] += 1
            breach_events += 1

    policies = []
    for tag, s in sorted(policy_stats.items()):
        policies.append({
            "tag": tag,
            "count": s["count"],
            "breaches": s["breaches"],
            "avg_tokens": round(s["total_tokens"] / s["count"]) if s["count"] else 0,
        })

    total_events = len(rows)
    compliant_events = total_events - breach_events
    compliance_rate = round(
        (compliant_events / total_events * 100) if total_events else 100, 1
    )

    # ── Render ───────────────────────────────────────────────────────────
    template = _jinja_env.get_template("compliance_passport.html")
    html_string = template.render(
        org_name=org.name,
        org_id=str(org.id),
        plan_tier=getattr(org, "plan_tier", None),
        date_from=cutoff.strftime("%Y-%m-%d"),
        date_to=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        total_events=total_events,
        compliant_events=compliant_events,
        breach_events=breach_events,
        compliance_rate=compliance_rate,
        policies=policies,
        chain_intact=chain_intact,
        broken_seq=broken_seq,
        first_seq=rows[0].seq if rows else "—",
        last_seq=rows[-1].seq if rows else "—",
        root_hash=rows[-1].chain_hash if rows else GENESIS_HASH,
        genesis_hash=GENESIS_HASH,
    )
    
    pdf_bytes = HTML(string=html_string).write_pdf()
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=passport.pdf"}
    )
