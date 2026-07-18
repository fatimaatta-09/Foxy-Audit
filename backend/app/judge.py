"""Combine independent judge results without allowing disagreement to hide risk."""

from __future__ import annotations

from .schemas import Verdict


def _decision(verdict: Verdict) -> str:
    # Treat a contradictory clean/policy_breach response conservatively.
    if verdict.decision == "clean" and verdict.policy_breach:
        return "breach"
    return verdict.decision


def combine(first: Verdict, second: Verdict) -> Verdict:
    """Merge two provider results; any known breach wins, unknown is not clean."""
    results = [(first, _decision(first)), (second, _decision(second))]
    known = [(verdict, decision) for verdict, decision in results
             if decision in {"clean", "breach"}]
    if not known:
        return first

    breach = any(decision == "breach" for _, decision in known)
    rules: list[str] = []
    for verdict, _ in known:
        for rule in verdict.rules:
            if rule not in rules:
                rules.append(rule)
    reasons = [verdict.reason for verdict, _ in known if verdict.reason]
    decision = "breach" if breach else "clean"
    prefix = "multi_judge_breach" if breach else "multi_judge_clean"
    return Verdict(
        policy_breach=breach,
        reason=f"{prefix}: " + "; ".join(reasons),
        risk_score=max(verdict.risk_score for verdict, _ in known),
        decision=decision,
        rules=rules,
    )
