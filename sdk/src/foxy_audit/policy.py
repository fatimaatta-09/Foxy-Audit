"""Host-side policy evaluation for the preflight guard.

`evaluate(prompt_text, policy_tag)` inspects a prompt LOCALLY and returns a
`PolicyDecision(action, rules, signals)`. `redact(prompt_text, policy_tag)`
returns a locally-redacted copy of the prompt. Both run in-process, alongside
`pii.detect_pii` — the only place raw text is ever seen — and return SIGNAL
LABELS / rule ids ONLY. Raw offending text never leaves this module.

Policy map (which check families each policy tag runs):

    hipaa           -> PHI/PII   (via pii.detect_pii, rules prefixed ``phi.``)
    gdpr            -> PII       (via pii.detect_pii, rules prefixed ``pii.``)
    default / other -> prompt-injection + secret/key detection

The rule id vocabulary (``<family>.<name>``) and the coarse signal labels are
part of the frozen wire contract's ``policy_rules`` / ``pii_signals`` fields.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import hashing, pii

# ── prompt-injection rules ────────────────────────────────────────────────────
# Each entry: (rule_id, coarse_signal_label, compiled_regex). Rules are matched
# against the prompt text; only the label/id ever leaves the host.
_INJECTION_RULES = (
    ("injection.ignore_previous", "prompt_injection", re.compile(
        r"ignore\s+(?:all\s+|any\s+)?(?:previous|prior|earlier|above|preceding)\s+"
        r"(?:instructions?|prompts?|messages?|directions?|context)", re.IGNORECASE)),
    ("injection.override_instructions", "prompt_injection", re.compile(
        r"(?:disregard|forget|override|bypass|discard)\s+"
        r"(?:all\s+|your\s+|the\s+|any\s+)?"
        r"(?:previous\s+|prior\s+|above\s+|safety\s+|system\s+)?"
        r"(?:instructions?|rules?|guidelines?|guardrails?|filters?|restrictions?|policy|policies)",
        re.IGNORECASE)),
    ("injection.reveal_system_prompt", "prompt_injection", re.compile(
        r"(?:reveal|show|print|repeat|display|expose|leak|disclose|tell)\s+"
        r"(?:me\s+)?(?:your\s+|the\s+)?"
        r"(?:system|initial|original|developer|hidden|secret)\s+"
        r"(?:prompt|message|instructions?)", re.IGNORECASE)),
    ("injection.jailbreak", "prompt_injection", re.compile(
        r"\b(?:do\s+anything\s+now|jailbreak|developer\s+mode|unfiltered\s+mode)\b",
        re.IGNORECASE)),
    # DAN is matched case-sensitively so the ordinary name "Dan" is not flagged.
    ("injection.dan", "prompt_injection", re.compile(r"\bDAN\b")),
)

# ── secret / key rules ────────────────────────────────────────────────────────
_SECRET_RULES = (
    ("secret.openai_key", "secret_key", re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b")),
    ("secret.aws_access_key", "secret_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("secret.private_key", "secret_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("secret.bearer_token", "secret_key", re.compile(
        r"\bbearer\s+[A-Za-z0-9._\-]{20,}", re.IGNORECASE)),
)

# ── policy → check families ───────────────────────────────────────────────────
_POLICY_CHECKS = {
    "hipaa": ("phi",),   # PHI/PII via pii.detect_pii
    "gdpr": ("pii",),    # PII via pii.detect_pii
}
_DEFAULT_CHECKS = ("injection", "secrets")

# Priority order for the single "dominant" blocked_reason label.
_REASON_LABEL = {
    "secret": "secret_key",
    "injection": "prompt_injection",
    "phi": "phi",
    "pii": "pii",
}
_REASON_PRIORITY = ("secret", "injection", "phi", "pii")


@dataclass(frozen=True)
class PolicyDecision:
    """Outcome of a local policy evaluation. Content-blind by construction."""
    action: str                       # "allow" (nothing fired) | "flag" (a rule matched)
    rules: list[str] = field(default_factory=list)     # matched rule ids
    signals: list[str] = field(default_factory=list)   # coarse signal labels that fired

    @property
    def triggered(self) -> bool:
        return self.action != "allow"

    @property
    def reason(self) -> str:
        """Short, dominant reason label for the audit event / desktop ping."""
        families = {r.split(".", 1)[0] for r in self.rules}
        for family in _REASON_PRIORITY:
            if family in families:
                return _REASON_LABEL[family]
        return next(iter(families)) if families else "none"


def _as_text(prompt) -> str:
    """Coerce an extracted prompt (str or structured provider messages) to text."""
    if isinstance(prompt, str):
        return prompt
    try:
        return hashing.canonical_json(prompt)
    except Exception:
        return str(prompt)


def _checks_for(policy_tag: str) -> tuple[str, ...]:
    return _POLICY_CHECKS.get((policy_tag or "").strip().lower(), _DEFAULT_CHECKS)


def evaluate(prompt_text, policy_tag: str = "default") -> PolicyDecision:
    """Evaluate ``prompt_text`` under ``policy_tag``; return labels only."""
    text = _as_text(prompt_text)
    checks = _checks_for(policy_tag)
    rules: list[str] = []
    signals: list[str] = []

    if "phi" in checks or "pii" in checks:
        prefix = "phi" if "phi" in checks else "pii"
        for label in pii.detect_pii(text, ""):
            rules.append(f"{prefix}.{label}")
            signals.append(label)

    if "injection" in checks:
        for rule_id, signal, regex in _INJECTION_RULES:
            if regex.search(text):
                rules.append(rule_id)
                signals.append(signal)

    if "secrets" in checks:
        for rule_id, signal, regex in _SECRET_RULES:
            if regex.search(text):
                rules.append(rule_id)
                signals.append(signal)

    action = "flag" if rules else "allow"
    return PolicyDecision(action=action,
                          rules=sorted(set(rules)),
                          signals=sorted(set(signals)))


def redact(prompt_text, policy_tag: str = "default") -> str:
    """Return a locally-redacted copy of the prompt for the active policy.

    Applies the same check families as :func:`evaluate`, replacing offending
    spans with content-blind ``[REDACTED:<label>]`` markers so the wrapped
    function receives a scrubbed prompt while raw text never leaves the host.
    """
    out = _as_text(prompt_text)
    checks = _checks_for(policy_tag)

    if "phi" in checks or "pii" in checks:
        out = pii.redact(out)

    if "injection" in checks:
        for rule_id, _signal, regex in _INJECTION_RULES:
            out = regex.sub(f"[REDACTED:{rule_id.split('.', 1)[1]}]", out)

    if "secrets" in checks:
        for rule_id, _signal, regex in _SECRET_RULES:
            out = regex.sub(f"[REDACTED:{rule_id.split('.', 1)[1]}]", out)

    return out


def redact_value(value, policy_tag: str = "default"):
    """Recursively redact string leaves in a prompt of ANY shape.

    A plain string is redacted directly; a structured provider prompt (e.g. an
    OpenAI ``messages=[{"role":..., "content":...}]`` list) is walked and every
    string leaf is scrubbed, so the wrapped function receives a redacted prompt of
    the SAME shape instead of the raw original. Non-string leaves pass through
    unchanged. This keeps redact mode honest for structured prompts — ``redact()``
    alone returns a string, which cannot be substituted back into a list/dict slot.
    """
    if isinstance(value, str):
        return redact(value, policy_tag)
    if isinstance(value, dict):
        return {k: redact_value(v, policy_tag) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_value(v, policy_tag) for v in value]
    if isinstance(value, tuple):
        return tuple(redact_value(v, policy_tag) for v in value)
    return value
