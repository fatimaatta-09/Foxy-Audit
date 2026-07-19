"""Stable, content-blind policy snapshots for individual audit records."""

from __future__ import annotations

import hashlib
import json

from .models import OrgPolicy

POLICY_SNAPSHOT_SCHEMA = "foxy-policy-v1"


def capture_policy_snapshot(policy: OrgPolicy) -> dict:
    """Return the safe policy fields that govern an interaction's assessment.

    Notification destinations are intentionally excluded: they do not affect the
    decision and could reveal operational contact information in an evidence
    export.
    """
    return {
        "schema": POLICY_SNAPSHOT_SCHEMA,
        "pii_detection": policy.pii_detection,
        "prompt_injection": policy.prompt_injection,
        "regulated_data_mode": policy.regulated_data_mode,
        "max_token_threshold": policy.max_token_threshold,
        "enforcement_mode": policy.enforcement_mode,
        "confidence_threshold": policy.confidence_threshold,
        "notify_on_breach": policy.notify_on_breach,
    }


def policy_snapshot_hash(snapshot: dict) -> str:
    """Hash canonical JSON so every verifier obtains the same policy digest."""
    canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def judge_policy_config(snapshot: object) -> dict | None:
    """Convert a validated stored snapshot to the policy flags consumed by judges."""
    if not isinstance(snapshot, dict) or snapshot.get("schema") != POLICY_SNAPSHOT_SCHEMA:
        return None
    required = ("pii_detection", "prompt_injection", "regulated_data_mode", "max_token_threshold")
    if any(key not in snapshot for key in required):
        return None
    return {key: snapshot[key] for key in required}
