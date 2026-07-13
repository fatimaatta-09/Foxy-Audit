"""GET /v1/analytics/threats — SQL-aggregated threat rollup for the dashboard.

Aggregates in the database (count / avg / group-by / limit) instead of loading the
org's whole ledger into memory, and is readable by the dashboard *session* OR the
SDK Bearer key (resolve_org) so the web dashboard — not just the desktop — can use
it. Timestamps are clean ISO-8601. (Phase 5 · 5A.3)
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import Integer, cast, func, select
from sqlalchemy.orm import Session

from ..auth import resolve_org
from ..db import get_db
from ..models import AuditLog, Organization

router = APIRouter()

_BREACH = AuditLog.gemini_verdict["policy_breach"].astext == "true"
_RISK = cast(AuditLog.gemini_verdict["risk_score"].astext, Integer)


@router.get("/v1/analytics/threats")
def get_threat_analytics(
    org: Organization = Depends(resolve_org),
    db: Session = Depends(get_db),
):
    total_threats = db.execute(
        select(func.count()).select_from(AuditLog)
        .where(AuditLog.org_id == org.id, _BREACH)
    ).scalar_one()

    avg_risk = db.execute(
        select(func.coalesce(func.avg(_RISK), 0))
        .where(AuditLog.org_id == org.id, _BREACH)
    ).scalar_one()

    top_policies = [
        {"tag": tag, "count": cnt}
        for tag, cnt in db.execute(
            select(AuditLog.policy_tag, func.count())
            .where(AuditLog.org_id == org.id, _BREACH)
            .group_by(AuditLog.policy_tag)
            .order_by(func.count().desc())
        ).all()
    ]

    recent_high_risk = [
        {
            "seq": r.seq,
            "policy_tag": r.policy_tag,
            "agent": r.agent,   # model/agent attribution (6B) — the dashboard renders "<policy> · <agent>"
            "risk_score": (r.gemini_verdict or {}).get("risk_score", 0),
            "reason": (r.gemini_verdict or {}).get("reason", ""),
            "timestamp": r.created_at.isoformat() if r.created_at else "",
        }
        for r in db.execute(
            select(AuditLog)
            .where(AuditLog.org_id == org.id, _BREACH, _RISK >= 50)
            .order_by(AuditLog.seq.desc())
            .limit(10)
        ).scalars().all()
    ]

    return {
        "top_policies": top_policies,
        "avg_risk_score": int(avg_risk),
        "total_threats": total_threats,
        "recent_high_risk": recent_high_risk,
    }
