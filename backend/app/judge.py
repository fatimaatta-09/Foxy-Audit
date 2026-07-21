"""Combine independent judge results without allowing disagreement to hide risk."""

from __future__ import annotations

import logging

from .schemas import Verdict

log = logging.getLogger("foxy.judge")

# A definite decision the audit report is allowed to trust from an AI judge. Host
# enforcement outcomes (blocked/redacted) are decided locally and must never arrive
# from a judge, so they are treated as out-of-schema here.
_JUDGE_DECISIONS = {"clean", "breach"}
# A usable reason must carry at least this much signal; a blank/near-blank reason
# from an affirmative judge is low-confidence noise, not audit evidence.
_MIN_REASON_LEN = 3


def _quarantine(problems: list[str]) -> Verdict:
    """An honest 'we could not determine this' — never a clean pass, never a breach."""
    return Verdict(
        policy_breach=False,
        reason="evaluator_unknown:" + ",".join(problems),
        risk_score=0,
        decision="unknown",
        rules=[],
    )


def validate(verdict: Verdict) -> Verdict:
    """Gate an AI-judge verdict before it is persisted as audit evidence.

    An evaluator can hallucinate a self-contradictory or empty answer. Rather than
    launder that into a confident clean/breach grade, quarantine it as
    evaluator_unknown — an honest non-pass state that keeps the audit report from
    trusting a bad verdict. A verdict that is already an honest unknown (e.g. an
    unavailable evaluator) is returned untouched so the worker's deterministic
    fallback still applies.
    """
    if verdict.decision == "unknown":
        return verdict

    problems: list[str] = []
    if verdict.decision not in _JUDGE_DECISIONS:
        problems.append("decision_out_of_schema")
    if verdict.policy_breach and verdict.decision == "clean":
        problems.append("breach_flag_with_clean_decision")
    if not verdict.policy_breach and verdict.decision == "breach":
        problems.append("clean_flag_with_breach_decision")
    if len((verdict.reason or "").strip()) < _MIN_REASON_LEN:
        problems.append("empty_or_low_confidence_reason")

    if problems:
        log.warning("quarantining judge verdict as evaluator_unknown: %s", problems)
        return _quarantine(problems)
    return verdict


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
