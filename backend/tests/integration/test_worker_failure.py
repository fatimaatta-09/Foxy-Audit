"""Worker retry / dead-letter transitions (Phase 5 · 5H).

_handle_failure returns a row to 'pending' (retry) until grading_attempts reaches
grading_max_attempts, after which it parks the row in 'failed' — the dead-letter
that 5E.1 alerts on. The happy path (_grade_one → 'graded') is covered elsewhere.
"""

from __future__ import annotations

import hashlib

from sqlalchemy import text

from app.db import SessionLocal


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def test_handle_failure_retries_then_dead_letters(make_org, client):
    from app import worker
    org = make_org()
    assert client.post("/v1/logs/batch", json=[{
        "prompt_hash": _h("p"), "response_hash": _h("r"),
        "token_count": 10, "policy_tag": "chat"}], headers=org["auth"]).status_code == 202

    db = SessionLocal()
    try:
        rows = worker._claim_batch(db, 10, 300)      # marks the row in_progress
        assert len(rows) == 1
        row = rows[0]

        # attempts below max → retry (back to pending for another claim)
        worker._handle_failure(db, dict(row, grading_attempts=1), max_attempts=5,
                               exc=Exception("gemini down"))
        assert db.execute(text("SELECT grading_status FROM audit_logs")).scalar() == "pending"

        # attempts at/over max → dead-letter
        worker._handle_failure(db, dict(row, grading_attempts=5), max_attempts=5,
                               exc=Exception("gemini down"))
        assert db.execute(text("SELECT grading_status FROM audit_logs")).scalar() == "failed"
    finally:
        db.close()
