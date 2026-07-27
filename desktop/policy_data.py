"""Foxy Audit desktop — Policy ruleset data shaping (D7).

Everything `#page-policy` decides before it paints, with no Qt in it. Ported
from the live web (foxy-audit-premium.html:1301-1381 + loadPolicy /
loadJudgeSelection / renderJudgeKeyFields / savePolicy / filterPolicies) and
checked against the backend it writes to (`backend/app/routers/policies.py`).

**The hard rule this module exists under.** A judge provider key (Gemini /
OpenAI) is a tenant secret. It reaches this module in exactly one direction —
from the field the user just typed into, straight onto the wire in
`save_body()` — and it is never stored here, never logged, never formatted
into a message, and never read back: `GET /v1/policies` returns only
`gemini_key_set` / `openai_key_set` booleans (policies.py:33-36, 78-79), so
there is no stored key for this app to render even if it wanted to.

`save_body()` is where that rule is actually enforced, because the wire
protocol has three states and only one of them is safe by default:

    typed a key   → send it            (store)
    removed a key → send ""            (clear)
    touched neither → OMIT THE FIELD   (keep whatever is stored)

Omission is what "keep" means server-side (`policies.py::_store_key`). Sending
anything in the third case would either wipe a working key or require this app
to be holding one — and it never is.
"""

from __future__ import annotations

import re

#: What the server accepts for a provider key (policies.py:52-53). Mirrored
#: here so an over-length paste is refused before it becomes a request: the
#: 422 that came back for one carried the rejected key in its `input` field.
KEY_MAX = 512

#: The three content safeguards, in the web's order (html:1311-1313).
SAFEGUARDS = (
    {"key": "pii_detection",
     "name": "Block PII — names, SSNs, emails",
     "desc": "Judge rejects prompts/responses with detectable personal "
             "identifiers",
     "severity": "high"},
    {"key": "prompt_injection",
     "name": "Flag prompt injections",
     "desc": "marks jailbreak and indirect injection attempts for review",
     "severity": "high"},
    {"key": "regulated_data_mode",
     "name": "Regulated data mode — HIPAA / financial",
     "desc": "stricter grading for regulated workloads (PHI, cardholder, "
             "account data)",
     "severity": "med"},
)

#: `.sev.high` is the breach tone, `.sev.med` the warn tone (html:571-572).
SEVERITY = {"high": ("high severity", "bad"), "med": ("medium severity", "warn")}

NO_MATCH = "No safeguard matches your search."

# Option lists quoted verbatim from the web's <option> text so the two products
# describe the same setting the same way (html:1319-1332).
ENFORCEMENT = (("block", "block on breach — highest protection"),
               ("flag", "flag only, allow through"),
               ("monitor", "silent monitor (log only)"))
CONFIDENCE = (("high", "high — fewer false positives"),
              ("balanced", "balanced (default)"),
              ("low", "low — catch every edge case"))
NOTIFY = (("immediate", "immediate alert"),
          ("digest", "batch — hourly digest"),
          ("none", "no notification"))
PROVIDERS = (("gemini", "Google Gemini"), ("openai", "OpenAI"),
             ("both", "Both — graded twice, any breach wins"))

#: PolicyConfig's own bounds (policies.py:39). Enforced here too so the field
#: cannot send a value the server will only reject.
MIN_TOKENS, MAX_TOKENS, DEFAULT_TOKENS = 1, 10_000_000, 50_000


def search_hits(query: str | None) -> list[bool]:
    """One flag per safeguard row — the web's `filterPolicies` (html:2712).

    It matches the row's whole text, name and description together, so typing
    "HIPAA" finds the regulated-data row even though the word is only in its
    description.
    """
    q = (query or "").strip().lower()
    if not q:
        return [True] * len(SAFEGUARDS)
    return [q in f"{s['name']} {s['desc']} {SEVERITY[s['severity']][0]}".lower()
            for s in SAFEGUARDS]


def no_match(query: str | None) -> bool:
    return bool((query or "").strip()) and not any(search_hits(query))


# ── the GET → the form ──────────────────────────────────────────────────────
def _choice(value, options, fallback: str) -> str:
    allowed = {v for v, _ in options}
    text = str(value or "")
    return text if text in allowed else fallback


def policy_view(data: dict | None) -> dict:
    """`GET /v1/policies` → the form's values (web `loadPolicy`, html:2634).

    Falls back to the backend's own documented defaults rather than to blanks,
    so a field never implies "off" for a setting that is actually on.
    """
    d = data if isinstance(data, dict) else {}
    tokens = d.get("max_token_threshold", DEFAULT_TOKENS)
    try:
        tokens = int(tokens)
    except (TypeError, ValueError):
        tokens = DEFAULT_TOKENS
    return {
        "pii_detection": bool(d.get("pii_detection", True)),
        "prompt_injection": bool(d.get("prompt_injection", True)),
        "regulated_data_mode": bool(d.get("regulated_data_mode", False)),
        "max_token_threshold": clamp_tokens(tokens),
        "enforcement_mode": _choice(d.get("enforcement_mode"), ENFORCEMENT, "block"),
        "confidence_threshold": _choice(d.get("confidence_threshold"),
                                        CONFIDENCE, "balanced"),
        "notify_on_breach": _choice(d.get("notify_on_breach"), NOTIFY, "immediate"),
        "notify_email": str(d.get("notify_email") or ""),
        "notify_webhook_url": str(d.get("notify_webhook_url") or ""),
        "judge_provider": _choice(d.get("judge_provider"), PROVIDERS, "gemini"),
    }


def clamp_tokens(value) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return DEFAULT_TOKENS
    if number <= 0:                     # web: `mt>0 ? mt : 50000` (html:2727)
        return DEFAULT_TOKENS
    return max(MIN_TOKENS, min(MAX_TOKENS, number))


# ── the judge provider / key-source block ───────────────────────────────────
STORED_PLACEHOLDER = "•••••••••••• stored — type a new key to replace"
KEY_HINTS = {"gemini": "AIza…", "openai": "sk-…"}
KEY_HELP = ("Stored encrypted; never shown again and never included in "
            "exports or the audit chain.")

OWN_HINT = (
    "Your key, your provider bill. It is encrypted before storage and "
    "decrypted only in memory for the moment a verdict is produced — Foxy "
    "never returns it, logs it, or writes it into your audit chain.")
PLATFORM_HINT = (
    "Foxy's managed provider keys grade your events and the model calls are "
    "on us — included with Premium. No provider account needed.")
PLATFORM_LOCKED = (
    "Foxy's managed keys are a Premium feature — on this plan the Judge uses "
    "your own provider key.")


def judge_view(data: dict | None) -> dict:
    """The judge block's state from the GET (web `loadJudgeSelection`:2654).

    `platform` is only honoured when the plan actually allows it: the server
    re-checks the tier on write (policies.py:167-172) AND again at grading
    time, so showing "Foxy's keys" for a plan that cannot use them would be a
    promise the next save would refuse.
    """
    d = data if isinstance(data, dict) else {}
    allowed = bool(d.get("platform_keys_allowed"))
    mode = "platform" if (d.get("judge_key_mode") == "platform" and allowed) else "own"
    return {
        "mode": mode,
        "platform_allowed": allowed,
        "plan_tier": str(d.get("plan_tier") or ""),
        # Presence booleans — the only thing the server ever says about a key.
        "gemini_key_set": bool(d.get("gemini_key_set")),
        "openai_key_set": bool(d.get("openai_key_set")),
    }


def key_field(provider: str, *, judge_provider: str, mode: str,
              key_set: bool, cleared: bool = False) -> dict:
    """How one provider's key row renders (web `renderJudgeKeyFields`:2683).

    `placeholder` is the only thing that ever hints at a stored key, and it
    says "stored", not the key — there is nothing here to reveal.
    """
    used = mode == "own" and judge_provider in (provider, "both")
    still_set = key_set and not cleared
    return {
        "used": used,
        "enabled": used,
        "badge": still_set,                 # the "key set ✓" chip
        "remove": used and still_set,       # the "remove key" affordance
        "placeholder": (STORED_PLACEHOLDER if still_set
                        else KEY_HINTS.get(provider, "")),
    }


def mode_hint(mode: str) -> str:
    return PLATFORM_HINT if mode == "platform" else OWN_HINT


# ── the PUT body ────────────────────────────────────────────────────────────
def save_body(form: dict, judge: dict, *,
              typed: dict | None = None, cleared=()) -> dict:
    """The `PUT /v1/policies` body.

    `typed` maps provider → what the user just entered; `cleared` names the
    providers whose stored key they asked to remove. A provider in neither is
    OMITTED from the body entirely, which is how the server is told to keep
    what it has (policies.py::_store_key). This is the single place in the
    desktop where key material is put on the wire, and it never arrives here
    from anywhere but the field the user typed into.
    """
    typed = typed or {}
    body = {
        "pii_detection": bool(form.get("pii_detection")),
        "prompt_injection": bool(form.get("prompt_injection")),
        "regulated_data_mode": bool(form.get("regulated_data_mode")),
        "max_token_threshold": clamp_tokens(form.get("max_token_threshold")),
        "enforcement_mode": _choice(form.get("enforcement_mode"),
                                    ENFORCEMENT, "block"),
        "confidence_threshold": _choice(form.get("confidence_threshold"),
                                        CONFIDENCE, "balanced"),
        "notify_on_breach": _choice(form.get("notify_on_breach"), NOTIFY,
                                    "immediate"),
        "notify_email": str(form.get("notify_email") or "").strip() or None,
        "notify_webhook_url": str(form.get("notify_webhook_url") or "").strip()
                              or None,
        "judge_provider": _choice(form.get("judge_provider"), PROVIDERS,
                                  "gemini"),
        "judge_key_mode": "platform" if judge.get("mode") == "platform" else "own",
    }
    for provider, field in (("gemini", "gemini_api_key"),
                            ("openai", "openai_api_key")):
        entered = str(typed.get(provider) or "").strip()
        if entered:
            body[field] = entered           # store
        elif provider in cleared:
            body[field] = ""                # clear
        # else: omitted on purpose — see the docstring.
    return body


# ── keeping key material off the screen ─────────────────────────────────────
REDACTED = "<redacted>"

#: Both providers' key prefixes. A backstop only — the value-based pass below
#: is the one that actually knows what the secret is.
_KEY_SHAPED = re.compile(r"(?:AIza|sk-)[A-Za-z0-9_\-]{6,}")

KEY_TOO_LONG = (
    f"That does not look like an API key — it is at or over the "
    f"{KEY_MAX}-character limit. Check what you pasted; nothing was sent.")


def key_too_long(typed: dict | None) -> str | None:
    """The provider whose typed key is at or over the cap, or None.

    At the cap counts, not just over it: the field stops accepting text at
    `KEY_MAX`, so a paste that hit the limit was silently clipped, and a
    clipped key is a wrong key that the server would happily store.
    """
    for provider, value in (typed or {}).items():
        if len(str(value or "").strip()) >= KEY_MAX:
            return provider
    return None


def redact(text, secrets=()) -> str:
    """Strip key material out of anything on its way to a widget.

    Defence in depth, in the shape the backend already uses for the same
    problem (`backend/app/anchor.py::_redact`): replace the values we KNOW are
    secret, then sweep for anything key-shaped that arrived another way. The
    caller passes the field contents at the moment of the failure — this
    module never holds them.
    """
    out = str(text or "")
    for secret in secrets or ():
        value = str(secret or "").strip()
        # Short strings are not credentials and blanket-replacing one would
        # mangle ordinary words in the message.
        if len(value) >= 8 and value in out:
            out = out.replace(value, REDACTED)
    return _KEY_SHAPED.sub(REDACTED, out)


# ── local validation, mirroring the server's own rules ──────────────────────
def validate(form: dict) -> tuple[str, str] | None:
    """(field, message) for the first problem, or None.

    The same two checks the server makes (policies.py:155-162), run here first
    so the answer arrives next to the field instead of as a 422 after a round
    trip. The server still decides — a 422 is surfaced verbatim if these ever
    drift apart.
    """
    email = str(form.get("notify_email") or "").strip()
    if email and ("@" not in email or "." not in email.split("@")[-1]):
        return ("notify_email", "That is not a valid email address.")
    hook = str(form.get("notify_webhook_url") or "").strip()
    if hook and not hook.startswith(("https://", "http://")):
        return ("notify_webhook_url",
                "A webhook URL must start with https:// or http://")
    return None


# ── save outcomes ───────────────────────────────────────────────────────────
SAVED = "✓ Saved — the ruleset is propagated"
DIRTY = "● Unsaved changes"
SAVING = "saving…"

#: 503 from PUT /v1/policies means `PROVIDER_KEY_ENCRYPTION_KEY` is absent, so
#: `_store_key` refused rather than write a provider key in plaintext. The
#: whole request aborts before the commit, so NOTHING was saved — saying only
#: "the key failed" would leave the user believing the rest went through.
KEK_UNAVAILABLE = (
    "Not saved. This deployment cannot encrypt provider keys yet, and Foxy "
    "will not store one unencrypted — so nothing on this page was written. "
    "Ask your administrator to configure key encryption, then save again.")

FORBIDDEN_FALLBACK = (
    "You do not have permission to change the ruleset. Changing the rules the "
    "auditor runs on needs an admin account.")

MEMBER_NOTICE = (
    "The ruleset is managed by workspace admins. You can see exactly what is "
    "enforced on this workspace, but changing it needs an admin account.")

KEY_ONLY_NOTICE = (
    "You are signed in with an organization API key, which can read the "
    "ruleset but not change it. Sign in with an admin account to edit.")


def save_result(status: int | None, detail: str = "") -> tuple[str, str]:
    """(message, tone) after PUT /v1/policies.

    403 carries the server's own sentence rather than a guess: it means either
    "not an admin" or "this plan may not use Foxy's keys" (policies.py:167),
    and the server is the only side that knows which. The UI prevents both, so
    reaching one means the account or the plan changed under us.
    """
    if status is None:
        return (SAVED, "ok")
    if status == 503:
        return (KEK_UNAVAILABLE, "bad")
    if status == 403:
        return (detail.strip() or FORBIDDEN_FALLBACK, "bad")
    if status == 0:
        return ("Could not reach the server — nothing was saved.", "bad")
    return (f"Save failed — {detail.strip() or f'HTTP {status}'}", "bad")


def save_toast(status: int | None) -> str:
    """The one-line version, for the toast.

    The full explanation lives on the page, beside the button that produced
    it. Putting the same paragraph in a floating box as well covered the form
    with a wall of red text saying what the page already said.
    """
    if status is None:
        return "Policy ruleset saved and propagated"
    if status == 503:
        return "Not saved — provider key encryption is not configured"
    if status == 403:
        return "Not saved — this account may not change the ruleset"
    if status == 0:
        return "Not saved — could not reach the server"
    return f"Not saved — the server answered HTTP {status}"


PRIVACY_NOTE = (
    "These preferences are saved to your workspace as part of your compliance "
    "policy. Foxy Audit stores only a tamper-proof hash and the verdict of "
    "each interaction — never the raw prompt or response.")
PRIVACY_URL = "https://foxyaudit.tech/privacy.html"

JUDGE_BLURB = (
    "Choose which model grades your events and whose API key pays for those "
    "model calls. The Judge is content-blind either way — it receives hashes, "
    "token counts and policy tags, never your prompts or responses.")
