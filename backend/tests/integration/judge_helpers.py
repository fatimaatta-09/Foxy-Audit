"""Test helper for per-tenant judge routing (migration 0053).

Since the AI Judge became per-tenant, an org with no provider key and no premium
plan gets NO judge at all — that is the tier matrix, not a bug. Any test that
wants grading to actually reach a (stubbed) judge must first give its org a key.

Exposed both as this plain function — importable from module-level test helpers —
and as the `configure_judge` fixture in conftest.py.
"""

from __future__ import annotations

import uuid

from app.crypto_secrets import encrypt_secret
from app.db import SessionLocal
from app.models import OrgPolicy, Organization


def give_judge_key(org_id, *, provider: str = "gemini", key_mode: str = "own",
                   gemini_key: str | None = "test-byok-gemini",
                   openai_key: str | None = None,
                   plan_tier: str | None = None) -> None:
    """Set an org's live judge routing (and optionally its plan tier)."""
    db = SessionLocal()
    try:
        oid = uuid.UUID(str(org_id))
        row = db.get(OrgPolicy, oid) or OrgPolicy(org_id=oid)
        row.judge_provider = provider
        row.judge_key_mode = key_mode
        row.gemini_key_enc = encrypt_secret(gemini_key) if gemini_key else None
        row.openai_key_enc = encrypt_secret(openai_key) if openai_key else None
        db.add(row)
        if plan_tier is not None:
            db.get(Organization, oid).plan_tier = plan_tier
        db.commit()
    finally:
        db.close()
