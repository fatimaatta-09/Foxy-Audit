"""Deterministic, local policy checks for content-blind audit metadata.

Hashes cannot tell an evaluator whether a response contains unsafe advice or a
data leak. This engine therefore makes only claims supported by metadata and
marks the semantic part unknown unless an optional evaluator provides it.
"""

from __future__ import annotations

from typing import Any

from .schemas import Verdict


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
