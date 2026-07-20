"""Deterministic, local policy checks for content-blind audit metadata.

Hashes cannot tell an evaluator whether a response contains unsafe advice or a
data leak. This engine therefore makes only claims supported by metadata and
marks the semantic part unknown unless an optional evaluator provides it.
"""

from __future__ import annotations

from typing import Any

from .schemas import Verdict

# Terminal, locally-decided event types. The host already enforced policy and no
# model response left the machine, so these are NEVER sent to an external judge —
# there is nothing to grade. They are recorded as prevented egress, not a breach.
ENFORCEMENT_EVENT_TYPES = {"blocked", "redacted"}


def evaluate_enforcement(meta: dict[str, Any]) -> Verdict:
    """Deterministic verdict for a host-side enforcement event (blocked/redacted).

    Built only from the content-blind enforcement labels the host recorded — the
    terminal event_type, the policy rules that fired, and a short blocked_reason.
    A prevented egress is NOT a model breach: policy_breach is always False and the
    risk score is 0, because nothing unsafe was allowed to leave the host.
    """
    metadata = meta.get("event_metadata") or {}
    event_type = str(meta.get("event_type") or "")
    # event_type is the frozen wire signal that routed us here; trust it, then fall
    # back to the metadata decision label if it is somehow absent.
    decision = event_type if event_type in ENFORCEMENT_EVENT_TYPES else str(
        metadata.get("decision") or "")
    if decision not in ENFORCEMENT_EVENT_TYPES:
        decision = "blocked"
    rules = [str(rule)[:80] for rule in (metadata.get("policy_rules") or [])
             if isinstance(rule, str)]
    label = str(metadata.get("blocked_reason") or "").strip()[:200]
    if decision == "redacted":
        reason = (f"host_redacted_response:{label}" if label
                  else "host redacted the response before it left the host")
    else:
        reason = (f"host_blocked_egress:{label}" if label
                  else "host blocked the prompt before it left the host")
    return Verdict(
        policy_breach=False,
        reason=reason,
        risk_score=0,
        decision=decision,
        rules=rules,
    )


def evaluate(meta: dict[str, Any], policy_config: dict[str, Any] | None = None) -> Verdict:
    config = policy_config or {}
    rules: list[str] = []
    token_count = int(meta.get("token_count") or 0)
    pii_signals = [str(v) for v in (meta.get("pii_signals") or [])]
    max_tokens = int(config.get("max_token_threshold") or 50_000)

    if config.get("pii_detection", True) and pii_signals:
        rules.append("local_pii_signal")
    if token_count > max_tokens:
        rules.append("token_threshold_exceeded")

    if rules:
        return Verdict(
            policy_breach=True,
            reason="deterministic metadata policy rule matched",
            risk_score=min(100, 50 + 10 * len(rules)),
            decision="breach",
            rules=rules,
        )
    return Verdict(
        policy_breach=False,
        reason="deterministic metadata checks passed; semantic content not evaluated",
        risk_score=0,
        decision="clean",
        rules=[],
    )
