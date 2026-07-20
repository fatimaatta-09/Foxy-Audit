"""Client-side PII detection (Phase 5 · 5J).

Runs in the SDK — the ONLY place that ever sees raw prompt/response text (the
backend receives hashes + signals only). A lightweight, dependency-free regex
layer runs ALWAYS; Microsoft Presidio (deep NLP: names, locations, MRNs) is used
ONLY when the optional extra is installed (`pip install foxy-audit[pii]`), so the
base SDK stays tiny. Detection emits SIGNAL LABELS (never the raw values), which
ride along in the audit metadata for the judge to weigh.
"""

from __future__ import annotations

import re

_EMAIL_RE = re.compile(r"[\w.\-]+@[\w\-]+\.\w+")
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d{1,3}[ .\-]?)?\(?\d{3}\)?[ .\-]?\d{3}[ .\-]?\d{4}(?!\d)")
_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_CARD_CANDIDATE_RE = re.compile(r"(?<!\d)(?:\d[ \-]?){13,19}(?!\d)")


def _luhn_ok(digits: str) -> bool:
    """Standard Luhn checksum — separates real card numbers from any 13–19 digit run."""
    if not (13 <= len(digits) <= 19):
        return False
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:                       # double every second digit from the right
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _has_card(text: str) -> bool:
    for m in _CARD_CANDIDATE_RE.finditer(text):
        if _luhn_ok(re.sub(r"\D", "", m.group())):
            return True
    return False


# ── optional Presidio (deep NLP) — only if the [pii] extra is installed ───────
_PRESIDIO = None   # None = not tried yet, False = unavailable, else the engine


def _presidio_signals(text: str) -> list[str]:
    global _PRESIDIO
    if _PRESIDIO is None:
        try:
            from presidio_analyzer import AnalyzerEngine
            _PRESIDIO = AnalyzerEngine()
        except Exception:                    # not installed / model missing → never retry
            _PRESIDIO = False
    if not _PRESIDIO:
        return []
    try:
        results = _PRESIDIO.analyze(text=text, language="en")
        return [f"presidio:{r.entity_type.lower()}" for r in results if r.score >= 0.5]
    except Exception:
        return []


# Ordered so multi-digit spans (cards) are handled before the narrower patterns.
# Reuses the exact regexes above so redaction and detection never drift apart.
_REDACTIONS = (
    (_SSN_RE, "ssn"),
    (_PHONE_RE, "phone"),
    (_EMAIL_RE, "email"),
    (_IPV4_RE, "ip_address"),
)


def redact(text: str) -> str:
    """Replace detected PII spans with content-blind ``[REDACTED:<label>]`` markers.

    Runs entirely in-process on the host (like detection); the raw values never
    leave. Credit cards are Luhn-gated exactly as in detection so we never mangle
    an unrelated long digit run.
    """
    def _card_sub(match: "re.Match[str]") -> str:
        digits = re.sub(r"\D", "", match.group())
        return "[REDACTED:credit_card]" if _luhn_ok(digits) else match.group()

    out = _CARD_CANDIDATE_RE.sub(_card_sub, str(text))
    for regex, label in _REDACTIONS:
        out = regex.sub(f"[REDACTED:{label}]", out)
    return out


def detect_pii(prompt_s: str, response_s: str) -> list[str]:
    """De-duplicated PII SIGNAL labels (never raw values) for prompt+response.
    Lightweight regex always; Presidio signals appended when the extra is present."""
    combined = f"{prompt_s} {response_s}"
    signals: list[str] = []
    if _EMAIL_RE.search(combined):
        signals.append("email")
    if _SSN_RE.search(combined):
        signals.append("ssn_pattern")
    if _PHONE_RE.search(combined):
        signals.append("phone")
    if _IPV4_RE.search(combined):
        signals.append("ip_address")
    if _has_card(combined):
        signals.append("credit_card")
    signals.extend(_presidio_signals(combined))
    return sorted(set(signals))
