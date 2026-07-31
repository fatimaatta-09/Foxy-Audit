"""Stable, content-blind policy snapshots for individual audit records."""

from __future__ import annotations

import hashlib
import json

from .models import OrgPolicy

POLICY_SNAPSHOT_SCHEMA = "foxy-policy-v1"


def capture_policy_snapshot(policy: OrgPolicy) -> dict:
    """The org's policy AS CONFIGURED at the moment this interaction was recorded.

    This is a record of settings, not a list of inputs to the verdict, and the
    difference matters because the snapshot travels inside an evidence export.
    Of the seven fields here:

    * ``pii_detection``, ``prompt_injection``, ``regulated_data_mode``,
      ``max_token_threshold`` and ``confidence_threshold`` DO govern the
      assessment. They are exactly the set :func:`judge_policy_config` projects
      for the judges; ``pii_detection`` and ``max_token_threshold`` are also read
      directly by :mod:`policy_engine`, and ``confidence_threshold`` tunes how
      conservatively the judge grades an ambiguous event (P4 Phase A).
    * ``notify_on_breach`` and ``enforcement_mode`` govern the RESPONSE to a
      finding, not the finding itself — who gets told, after the grading is
      already done. Since P5 §A.2 both are read by the two breach senders
      (:mod:`org_notifications` org-level, :mod:`user_notifications` per seat)
      off the org's LIVE policy row at send time: ``monitor`` suppresses the
      email entirely, ``block`` also delivers to a tenant who chose batching,
      ``flag`` is the ordinary path. Neither reaches a judge —
      :func:`judge_policy_config` drops both on its projection line — so neither
      is evidence of how THIS interaction was judged. They are captured so an
      auditor can see what the tenant had configured at the time.

      Note the deliberate asymmetry: the consumer reads the live row, this
      snapshot records the historical one. Delivery follows the tenant's current
      setting; the export records what it was. Neither is the other's source.

    That distinction is the point of this docstring, so keep it accurate. A
    snapshot field described as inert while the judge acts on it — or the reverse
    — misleads whoever reads the evidence, which is worse than no note at all.

    Do not drop the recorded-only fields to "tidy" this. ``foxy-policy-v1`` has to
    stay structurally stable or new snapshots stop being comparable with every
    snapshot already written, and the schema hash changes underneath exports that
    have already been handed to auditors.

    Notification destinations (email, webhook URL) are intentionally excluded:
    they do not affect the decision and would leak operational contact details
    into an evidence export.
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
    """Convert a validated stored snapshot to the policy flags consumed by judges.

    `confidence_threshold` is carried through but NOT required. Snapshots written
    before it was surfaced do not have the key, and rejecting them would stop
    grading historical evidence — so an absent value reads as "balanced", which is
    the behaviour those rows were graded under in the first place (P4 §A).

    The projection is deliberate: only fields a judge should act on cross this
    boundary. Adding one here is what makes it reachable at all — the field was
    stored, snapshotted and displayed for months while being dropped on this line.
    """
    if not isinstance(snapshot, dict) or snapshot.get("schema") != POLICY_SNAPSHOT_SCHEMA:
        return None
    required = ("pii_detection", "prompt_injection", "regulated_data_mode", "max_token_threshold")
    if any(key not in snapshot for key in required):
        return None
    config = {key: snapshot[key] for key in required}
    config["confidence_threshold"] = snapshot.get("confidence_threshold") or "balanced"
    return config
