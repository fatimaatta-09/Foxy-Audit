from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import require_org
from ..db import get_db
from ..models import AuditLog, Organization

router = APIRouter()

@router.get("/v1/analytics/threats")
def get_threat_analytics(
    org: Organization = Depends(require_org),
    db: Session = Depends(get_db)
):
    rows = db.execute(
        select(AuditLog)
        .where(AuditLog.org_id == org.id)
    ).scalars().all()

    top_policies = {}
    total_risk = 0
    breach_count = 0
    high_risk_events = []

    for row in rows:
        verdict = row.gemini_verdict or {}
        if verdict.get("policy_breach"):
            breach_count += 1
            tag = row.policy_tag
            top_policies[tag] = top_policies.get(tag, 0) + 1
            
            risk = verdict.get("risk_score", 0)
            total_risk += risk
            
            if risk >= 50:
                high_risk_events.append({
                    "seq": row.seq,
                    "policy_tag": tag,
                    "risk_score": risk,
                    "reason": verdict.get("reason", ""),
                    "timestamp": row.created_at.isoformat() + "Z" if row.created_at else ""
                })

    sorted_policies = sorted([{"tag": k, "count": v} for k, v in top_policies.items()], key=lambda x: x["count"], reverse=True)
    
    high_risk_events.sort(key=lambda x: x["seq"], reverse=True)
    high_risk_events = high_risk_events[:10]
    
    avg_risk = int(total_risk / breach_count) if breach_count > 0 else 0

    return {
        "top_policies": sorted_policies,
        "avg_risk_score": avg_risk,
        "total_threats": breach_count,
        "recent_high_risk": high_risk_events
    }
