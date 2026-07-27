"""Judge temporal reasoning (Phase 5 · 5J): the judge receives a compact
recent-history summary so it can flag escalating patterns across an org's recent
activity, not just single interactions.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.db import SessionLocal


def test_temporal_rule_present_only_with_history():
    from app.gemini import _build_system_prompt
    assert "TEMPORAL REASONING" in _build_system_prompt(None, {"recent_breaches": 5})
    assert "TEMPORAL REASONING" not in _build_system_prompt(None, None)


def test_org_history_summarizes_last_7_days(make_org):
    """Seeds audit_logs, not usage_daily.

    `_org_history` reads the append-only ledger now: the rollup only recomputes
    a rolling 48-hour window, so for five of these seven days it was handing
    the AI judge whatever partial counts the worker last wrote.
    """
    from app import worker
    from app.models import AuditLog

    org = make_org()
    db = SessionLocal()
    try:
        oid = uuid.UUID(org["org_id"])
        # 10 graded rows spread across the window, 3 of them breaches.
        for i in range(10):
            db.add(AuditLog(
                org_id=oid, seq=1000 + i,
                prompt_hash=f"{i:064d}", response_hash=f"{i:064d}",
                token_count=10, policy_tag="chat",
                prev_hash=f"{i:064d}", chain_hash=f"{i + 1:064d}",
                grading_status="graded",
                gemini_verdict={"policy_breach": i < 3, "risk_score": 90},
                created_at=datetime.now(timezone.utc) - timedelta(days=i % 6),
            ))
        db.commit()

        h = worker._org_history(db, oid)
        assert h["recent_breaches"] == 3
        assert h["recent_graded"] == 10
        assert h["breach_rate_pct"] == 30.0
    finally:
        db.close()
