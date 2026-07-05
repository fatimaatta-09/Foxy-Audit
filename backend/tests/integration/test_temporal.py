"""Judge temporal reasoning (Phase 5 · 5J): the judge receives a compact
recent-history summary so it can flag escalating patterns across an org's recent
activity, not just single interactions.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text

from app.db import SessionLocal


def test_temporal_rule_present_only_with_history():
    from app.gemini import _build_system_prompt
    assert "TEMPORAL REASONING" in _build_system_prompt(None, {"recent_breaches": 5})
    assert "TEMPORAL REASONING" not in _build_system_prompt(None, None)


def test_org_history_summarizes_last_7_days(make_org):
    from app import worker
    org = make_org()
    db = SessionLocal()
    try:
        db.execute(text(
            "INSERT INTO usage_daily (org_id, day, breach_count, graded_count) "
            "VALUES (cast(:o as uuid), CURRENT_DATE, 3, 10)"), {"o": org["org_id"]})
        db.commit()
        h = worker._org_history(db, uuid.UUID(org["org_id"]))
        assert h["recent_breaches"] == 3
        assert h["recent_graded"] == 10
        assert h["breach_rate_pct"] == 30.0
    finally:
        db.close()
