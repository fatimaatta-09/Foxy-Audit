"""GET /v1/analytics/threats — SQL-aggregated threat rollup for the dashboard.

Aggregates in the database (count / avg / group-by / limit) instead of loading the
org's whole ledger into memory, and is readable by the dashboard *session* OR the
SDK Bearer key (resolve_org) so the web dashboard — not just the desktop — can use
it. Timestamps are clean ISO-8601. (Phase 5 · 5A.3)
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import Integer, case, cast, func, select, text
from sqlalchemy.orm import Session

from ..auth import resolve_org
from ..db import get_db
from ..models import AuditLog, Organization

router = APIRouter()

_BREACH = (AuditLog.grading_status == "graded") & (
    AuditLog.gemini_verdict["policy_breach"].astext == "true")
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


@router.get("/v1/analytics/timeseries")
def get_threat_timeseries(
    days: int = 30,
    org: Organization = Depends(resolve_org),
    db: Session = Depends(get_db),
):
    """Breaches per day split by risk band (high ≥70 / medium 40-69 / low <40). NEW — the existing
    /threats groups only by policy_tag. Real DB aggregation; days is clamped to [1,365] (an int, so the
    interval literal is injection-safe). Empty list when there are no breaches in the window."""
    d = max(1, min(365, int(days)))
    # #123 · PINNED TO UTC. date_trunc/::date on a timestamptz resolves in the
    # SESSION timezone, and nothing in this project pins it — so this bucketed
    # by the database server's local day. R3 fixed the same shape in usage.py;
    # these labels are the days the dashboard's threat timeline draws, and F1
    # gave its last bar a focal mark — a zone-shifted label moves the emphasis
    # onto the wrong day as well as the count.
    day = func.to_char(func.timezone("UTC", AuditLog.created_at), "YYYY-MM-DD")
    band = case((_RISK >= 70, "high"), (_RISK >= 40, "medium"), else_="low")
    rows = db.execute(
        select(day, band, func.count())
        .where(AuditLog.org_id == org.id, _BREACH,
               AuditLog.created_at >= text(f"now() - interval '{d} days'"))
        .group_by(day, band)
        .order_by(day)
    ).all()
    by_day: dict = {}
    for dd, bnd, cnt in rows:
        e = by_day.setdefault(dd, {"day": dd, "high": 0, "medium": 0, "low": 0, "total": 0})
        e[bnd] = int(cnt)
        e["total"] += int(cnt)
    return {"days": [by_day[k] for k in sorted(by_day)]}


@router.get("/v1/analytics/by-agent")
def get_threats_by_agent(
    org: Organization = Depends(resolve_org),
    db: Session = Depends(get_db),
):
    """Breaches grouped by the model/agent that produced them (6B attribution) with avg risk. NEW.
    NULL agents collapse to 'unattributed'. Real aggregation; empty list when there are no breaches."""
    rows = db.execute(
        select(func.coalesce(AuditLog.agent, "unattributed"),
               func.count(), func.coalesce(func.avg(_RISK), 0))
        .where(AuditLog.org_id == org.id, _BREACH)
        .group_by(AuditLog.agent)
        .order_by(func.count().desc())
        .limit(12)
    ).all()
    return {"agents": [{"agent": a, "count": int(c), "avg_risk": int(r)} for a, c, r in rows]}
