"""Foxy Audit desktop — Verify + Anchors data shaping (D6).

Everything `#page-verify` decides before it paints, with no Qt in it. Ported
from the live web loaders:

    hash hint           foxy-audit-premium.html:1901-1909  (vHashValidate)
    single-record       :runVerify — six states, all preserved
    whole ledger        :2422-2439  (verifyChain)
    anchor now          :2440-2454  (anchorNow)
    receipts + SLA      :3300-3315  (loadAnchors)

This page is where the product's core claim is tested, so its wording is the
most load-bearing in the app. Two distinctions the web draws carefully and this
keeps exactly:

* a **gap** (a record deleted) is reported differently from a **mutation** (a
  record altered) — the backend says which, and collapsing them would tell an
  auditor the wrong thing about what happened;
* a record can be **intact AND a policy breach**. Verification is about whether
  the ledger was tampered with; the verdict is about what the model did. A
  breach is not a verification failure and must never be coloured like one.
"""

from __future__ import annotations

import re

from home_data import _num, dict_rows, thousands

HASH_LENGTH = 64
_HEX = re.compile(r"[^a-fA-F0-9]")

#: The web shows at most 8 receipts (html:3312).
RECEIPT_LIMIT = 8
ETHERSCAN_TX = "https://sepolia.etherscan.io/tx/{tx_hash}"


def normalize_hash(text: str | None) -> str:
    return _HEX.sub("", (text or "").strip()).lower()


def hash_hint(text: str | None) -> tuple[str, str]:
    """(hint, tone) under the input, live as the user types (web:1901).

    Counting up to 64 rather than only complaining on submit: the user is
    pasting a 64-character hex string, and "47 / 64" tells them they clipped
    it, where "invalid" would not.
    """
    n = len(normalize_hash(text))
    if n == 0:
        return "", "muted"
    if n < HASH_LENGTH:
        return f"{n} / {HASH_LENGTH} hex characters", "muted"
    if n == HASH_LENGTH:
        return "✓ looks like a valid chain hash", "ok"
    return "too long — a chain hash is exactly 64 hex characters", "bad"


def verify_precheck(text: str | None) -> tuple[str, str, str] | None:
    """The local decision before any request. None means "go ahead and ask"."""
    if len(normalize_hash(text)) != HASH_LENGTH:
        return ("warn", "Enter a 64-character chain hash",
                "Paste the full hash from a ledger row (64 hex characters).")
    return None


def record_result(data: dict | None, *, status: int | None = None) -> tuple[str, str, str]:
    """(tone, title, detail) for the six-state result panel.

    Order matters and mirrors the web exactly: not-found, then tampering, then
    breach, then verified. Checking "verified" first would report a missing
    record as verified-false and lose the distinction.
    """
    if status == 401:
        return ("warn", "Sign in to check the ledger",
                "This check runs against your workspace's ledger.")
    if not isinstance(data, dict):
        return ("bad", "Could not reach the server",
                "Please try again in a moment.")
    if not data.get("found"):
        return ("bad", "Not in this ledger",
                "No record with this chain hash exists in your workspace.")
    seq = data.get("seq")
    if not data.get("verified"):
        return ("bad", "Tampering detected",
                f"Verification failed at seq {seq} — this record no longer "
                f"matches what was recorded.")
    if data.get("status") == "breach":
        return ("warn", "Record intact · policy breach",
                f"Seq {seq} — the record is untampered, but the Judge flagged "
                f"a policy breach.")
    return ("ok", "Record verified — untampered",
            f"Seq {seq} · verdict {data.get('status') or 'clean'}.")


CHECKING = ("checking", "Checking the ledger…",
            "Recomputing this record with its real predecessor.")


# ── whole-ledger check (web verifyChain, html:2422) ─────────────────────────
_GAP = re.compile(r"sequence gap|previous hash mismatch", re.I)


def chain_result(data: dict | None) -> tuple[str, str, str]:
    """(tone, title, detail) for the full-ledger check.

    A gap and a mutation are different incidents — one says a record was
    removed, the other that one was changed — and the backend distinguishes
    them in `detail`. Reporting both as "tampering" would misdescribe the first.
    """
    if not isinstance(data, dict):
        return ("bad", "Could not reach the server", "")
    if data.get("ok"):
        count = int(_num(data.get("count")))
        return ("ok", f"✓ chain intact — {thousands(count)} blocks verified",
                partial_window_note(data))
    detail = str(data.get("detail") or "")
    seq = data.get("first_broken_seq")
    title = ("⚠ record missing — the ledger has a gap" if _GAP.search(detail)
             else "⚠ tampering detected — a record was altered")
    return ("bad", f"{title} at seq {seq}", detail.replace("<", "").replace(">", ""))


#: The server verifies a bounded window on very large ledgers.
PARTIAL_WINDOW_AT = 50_000


def partial_window_note(data: dict | None) -> str:
    """Say so when the check covered a window rather than everything.

    "chain intact" over a partial window is a narrower claim than over the
    whole ledger, and an audit tool must not let the two read the same.
    """
    count = int(_num((data or {}).get("count")))
    if count < PARTIAL_WINDOW_AT:
        return ""
    return (f"Checked the most recent {thousands(count)} records — on a ledger "
            f"this size the server verifies a bounded window, so this result "
            f"covers that window rather than every record ever written.")


# ── anchors (web loadAnchors, html:3300) ────────────────────────────────────
def anchor_now_result(data: dict | None, *, status: int | None = None
                      ) -> tuple[str, str, str]:
    """(tone, title, tx_hash) after POST /v1/anchors."""
    if status == 409:
        return ("ok", "Nothing new to anchor — the current head is already "
                "anchored (or there are no logs yet).", "")
    if status is not None and status >= 400:
        return ("bad", f"Anchor failed (HTTP {status})", "")
    if not isinstance(data, dict):
        return ("bad", "Could not reach the server", "")
    chain = data.get("chain") or "chain"
    state = data.get("status") or "?"
    return ("ok", f"⚓ anchored on {chain} — status {state}",
            str(data.get("tx_hash") or ""))


def tx_url(tx_hash: str | None) -> str:
    return ETHERSCAN_TX.format(tx_hash=tx_hash) if tx_hash else ""


def receipt_rows(data) -> list[dict]:
    """The anchor receipts list, newest first, capped like the web."""
    rows = []
    for a in dict_rows(data)[:RECEIPT_LIMIT]:
        tx = str(a.get("tx_hash") or "")
        rows.append({
            "status": str(a.get("status") or "pending"),
            "tone": {"confirmed": "ok", "failed": "bad"}.get(
                str(a.get("status") or ""), "warn"),
            "chain": str(a.get("chain") or "chain"),
            "when": a.get("confirmed_at") or a.get("anchored_at"),
            "tx_hash": tx, "url": tx_url(tx),
            "detail": str(a.get("detail") or "")[:48],
            "head": str(a.get("chain_head") or a.get("head") or ""),
        })
    return rows


def freshness(data) -> str:
    """"last anchor 3h ago" / "no anchors yet" (web:3308-3310)."""
    rows = dict_rows(data)
    confirmed = [a for a in rows if a.get("status") == "confirmed"]
    last = (confirmed or rows or [None])[0]
    if not last:
        return "no anchors yet"
    from console_chrome import relative_time
    when = last.get("confirmed_at") or last.get("anchored_at")
    stamp = relative_time(when) if when else ""
    return f"last anchor {stamp}" if stamp else "anchored"


def sla_text(data: dict | None) -> str:
    """"Auto-anchored every 6 hours on pro." — empty when unknown, never a
    guessed cadence."""
    if not isinstance(data, dict):
        return ""
    cadence = data.get("cadence_human")
    if not cadence:
        return ""
    tier = data.get("plan_tier")
    return f"Auto-anchored every {cadence}{f' on {tier}' if tier else ''}."


#: The offline proof, quoted verbatim so the desktop and the site tell the user
#: to run the same command.
OFFLINE_COMMAND = "python verifier/foxy_verify.py foxy-audit-logs.json"

HOW_IT_WORKS = (
    "For current SDK capture, raw content stays local while customer-keyed "
    "commitments are sent to Foxy. The backend canonically serializes those "
    "commitments with operational metadata and the preceding chain hash. "
    "Altering a stored record breaks verification; the full-ledger check also "
    "detects gaps.")

CHAIN_FORMULA = "chain_hash = SHA256(canonical_record + prev_chain_hash)"

OFFLINE_NOTE = (
    "For independent offline proof, export your ledger and run the command "
    "above. Known-content checks require your customer-held commitment key and "
    "an events sidecar; neither is sent to Foxy.")
