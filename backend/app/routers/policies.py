"""GET /v1/policies  — fetch the org's active policy configuration.
PUT /v1/policies  — update policy toggles.

Core Requirement #1: Active Policy Configuration.

A row is auto-created with safe defaults on first GET so the table is never
empty. Gemini grading reads these flags via gemini.evaluate(meta, policy_config).
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..auth import require_role, resolve_org
from ..db import get_db
from ..models import OrgPolicy, Organization, User

router = APIRouter()


class PolicyConfig(BaseModel):
    pii_detection: bool = True
    prompt_injection: bool = True
    regulated_data_mode: bool = False
    max_token_threshold: int = Field(default=50_000, ge=1, le=10_000_000)
    # Judge sensitivity (Phase 5) — how strictly the Judge acts + how you're notified.
    enforcement_mode: Literal["block", "flag", "monitor"] = "block"
    confidence_threshold: Literal["high", "balanced", "low"] = "balanced"
    notify_on_breach: Literal["immediate", "digest", "none"] = "immediate"


def _to_config(row: OrgPolicy) -> "PolicyConfig":
    return PolicyConfig(
        pii_detection=row.pii_detection,
        prompt_injection=row.prompt_injection,
        regulated_data_mode=row.regulated_data_mode,
        max_token_threshold=row.max_token_threshold,
        enforcement_mode=row.enforcement_mode,
        confidence_threshold=row.confidence_threshold,
        notify_on_breach=row.notify_on_breach,
    )


def _get_or_create(org: Organization, db: Session) -> OrgPolicy:
    """Return the org's policy row, creating it with defaults if missing."""
    row = db.get(OrgPolicy, org.id)
    if row is None:
        row = OrgPolicy(org_id=org.id)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


@router.get("/v1/policies", response_model=PolicyConfig)
def get_policies(
    org: Organization = Depends(resolve_org),
    db: Session = Depends(get_db),
) -> PolicyConfig:
    """Return the org's current compliance policy settings."""
    row = _get_or_create(org, db)
    return _to_config(row)


@router.put("/v1/policies", response_model=PolicyConfig)
def update_policies(
    body: PolicyConfig,
    admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> PolicyConfig:
    """Update the org's compliance policy settings — ADMIN humans only.

    Hardened (Phase 4 #1): previously used resolve_org, which let a bare SDK
    Bearer key or any 'member' rewrite compliance policy. Writing the rules that
    govern the auditor is a privileged act, so it now requires an admin dashboard
    session. require_role already scoped RLS to the admin's org.

    Changes take effect for all interactions processed AFTER this call.
    The Gemini evaluator reads these flags on every grading job.
    """
    org = db.get(Organization, admin.org_id)
    row = _get_or_create(org, db)
    row.pii_detection = body.pii_detection
    row.prompt_injection = body.prompt_injection
    row.regulated_data_mode = body.regulated_data_mode
    row.max_token_threshold = body.max_token_threshold
    row.enforcement_mode = body.enforcement_mode
    row.confidence_threshold = body.confidence_threshold
    row.notify_on_breach = body.notify_on_breach
    db.commit()
    db.refresh(row)
    return _to_config(row)
