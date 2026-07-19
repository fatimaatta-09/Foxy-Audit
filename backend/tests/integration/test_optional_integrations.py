"""Opt-in tests for real external services.

These tests are skipped unless credentials are present, so normal CI stays
hermetic while the live paths remain exercisable on demand:

    GEMINI_API_KEY set                 -> real Gemini judge
    OPENAI_API_KEY set                 -> real OpenAI Responses API judge
    ANCHOR_PROVIDER=evm plus EVM vars  -> real Sepolia anchoring

Example:
    OPENAI_API_KEY=... pytest backend/tests/integration/test_optional_integrations.py
"""

from __future__ import annotations

import hashlib
import os

import pytest
from sqlalchemy import text

from app.db import SessionLocal


def _ingest_one(client, org):
    h = hashlib.sha256
    client.post("/v1/logs/batch", json=[{
        "prompt_hash": h(b"p").hexdigest(), "response_hash": h(b"r").hexdigest(),
        "token_count": 10, "policy_tag": "chat"}], headers=org["auth"])


@pytest.mark.skipif(not os.environ.get("GEMINI_API_KEY"),
                    reason="set GEMINI_API_KEY to exercise the real Gemini judge")
def test_real_gemini_grades_a_row(make_org, client):
    from app import worker
    org = make_org()
    _ingest_one(client, org)
    db = SessionLocal()
    try:
        rows = worker._claim_batch(db, 10, 300)
        worker._grade_one(db, rows[0])
        assert db.execute(text("SELECT grading_status FROM audit_logs")).scalar() == "graded"
    finally:
        db.close()


@pytest.mark.skipif(not os.environ.get("OPENAI_API_KEY"),
                    reason="set OPENAI_API_KEY to exercise the real OpenAI judge")
def test_real_openai_grades_a_row(make_org, client):
    from app import worker
    org = make_org()
    _ingest_one(client, org)
    db = SessionLocal()
    try:
        rows = worker._claim_batch(db, 10, 300)
        worker._grade_one(db, rows[0])
        assert db.execute(text("SELECT grading_status FROM audit_logs")).scalar() == "graded"
    finally:
        db.close()


@pytest.mark.skipif(os.environ.get("ANCHOR_PROVIDER") != "evm",
                    reason="set ANCHOR_PROVIDER=evm + ANCHOR_EVM_* for live-chain anchoring")
def test_real_evm_anchor_runs(make_org, client):
    from app import anchor
    from app.config import get_settings
    org = make_org()
    _ingest_one(client, org)
    db = SessionLocal()
    try:
        n = anchor.anchor_all_due(db, get_settings())
        assert n >= 0
    finally:
        db.close()
