"""Foxy Audit desktop — Audit ledger data shaping (D5).

Everything `#page-ledger` decides before it paints, with no Qt in it. Ported
from the live web loaders:

    stat row + charts   foxy-audit-premium.html:3628-3655  (loadLedgerCharts)
    verdictOf           :2211-2221
    ledgerRow + detail  :2284-2308
    query + paging      :2315-2327  (_ledgerQuery, ledgerPage)
    quick chips         :3627       (quickLedger)

The verdict vocabulary is the subtle part and is preserved exactly: `blocked`
and `redacted` are host-side enforcement — egress that never happened — and
`unknown` is an evaluator non-answer. None of the three is "clean", and the web
is careful never to colour them green. Neither are we.
"""

from __future__ import annotations

from urllib.parse import quote

from home_data import _num, dict_rows, fmt_pct, short_hash, thousands

PAGE_LIMIT = 50            # the web's _ledgerLimit (html:2209)

#: The verdict <select>, in the web's order (html:1200).
VERDICT_OPTIONS = (("", "any verdict"), ("breach", "breach"), ("clean", "clean"),
                   ("unknown", "unknown"), ("pending", "pending"),
                   ("blocked", "blocked"), ("redacted", "redacted"))

#: The quick chips (html:1204-1210). "All" clears the verdict filter.
QUICK_CHIPS = (("All", ""), ("Breaches", "breach"), ("Clean", "clean"),
               ("Blocked", "blocked"), ("Redacted", "redacted"),
               ("Pending", "pending"))

TABLE_COLUMNS = ("tenant", "hash", "verdict", "time")


def verdict_of(item: dict) -> tuple[str, str]:
    """(label, tone) for one ledger row — the web's verdictOf, order intact.

    The ordering matters: enforcement outcomes are terminal and locally
    decided, so they are reported before any judge verdict is consulted. A
    blocked call never reached a model, which is a stronger statement than
    "the judge found nothing wrong".
    """
    if not isinstance(item, dict):
        return "pending", "warn"
    event_type = item.get("event_type")
    if event_type == "blocked":
        return "blocked", "blue"
    if event_type == "redacted":
        return "redacted", "pink"
    verdict = item.get("gemini_verdict") or {}
    if not isinstance(verdict, dict):
        verdict = {}
    reason = str(verdict.get("reason") or "")
    if (reason.startswith("evaluator_unavailable")
            or reason.startswith("evaluator_unknown")
            or verdict.get("decision") == "unknown"):
        return "unknown", "warn"
    if verdict.get("policy_breach"):
        return "breach", "bad"
    if item.get("grading_status") == "graded":
        return "safe", "ok"
    if item.get("grading_status") == "failed":
        return "flag", "warn"
    return "pending", "warn"


def query_string(*, page: int = 1, q: str = "", policy_tag: str = "",
                 verdict: str = "", limit: int = PAGE_LIMIT) -> str:
    """The web's _ledgerQuery (html:2315-2324), percent-encoded the same way."""
    parts = [f"?page={max(1, int(page))}", f"&limit={int(limit)}"]
    for key, value in (("q", q), ("policy_tag", policy_tag), ("verdict", verdict)):
        text = (value or "").strip()
        if text:
            parts.append(f"&{key}={quote(text, safe='')}")
    return "".join(parts)


def max_page(total: int, limit: int = PAGE_LIMIT) -> int:
    """`Math.max(1, Math.ceil(total/limit))` — never zero, so "page 1 / 1" is
    what an empty ledger says rather than "page 1 / 0"."""
    return max(1, -(-int(_num(total)) // max(1, int(limit))))


def page_label(page: int, total: int, limit: int = PAGE_LIMIT) -> str:
    return f"page {page} / {max_page(total, limit)}"


def count_label(total) -> str:
    """"1,240 records" / "1 record" (html:2338)."""
    n = int(_num(total))
    return f"{thousands(n)} record{'' if n == 1 else 's'}"


def empty_message(total: int, page: int, filtered: bool) -> tuple[str, str]:
    """(title, body) for an empty table — the web distinguishes these two
    (html:2334), and so must we: a filter that matched nothing is the user's
    doing, an empty page is ours."""
    if filtered:
        return ("No records match", "No audit rows match these filters.")
    if int(_num(total)) == 0 and page == 1:
        return ("No audit rows yet",
                "Records appear here as your SDK reports interactions.")
    return ("Nothing on this page", "Try an earlier page.")


def table_rows(data: dict | None) -> list[dict]:
    """One dict per row, plus everything its expandable detail needs.

    The detail is built here rather than on expand so that a row carries its
    own evidence: nothing is re-fetched, and what the panel shows is exactly
    what the list returned.
    """
    items = dict_rows((data or {}).get("items") if isinstance(data, dict) else None)
    out = []
    for item in items:
        label, tone = verdict_of(item)
        verdict = item.get("gemini_verdict") or {}
        if not isinstance(verdict, dict):
            verdict = {}
        risk = verdict.get("risk_score")
        signals = item.get("pii_signals")
        signals = [str(x) for x in signals] if isinstance(signals, list) else []
        graded = item.get("grading_status") == "graded"
        out.append({
            "seq": item.get("seq"),
            "policy": str(item.get("policy_tag") or ""),
            "agent": str(item.get("agent") or ""),
            "hash_short": short_hash(item.get("chain_hash")),
            "chain_hash": str(item.get("chain_hash") or ""),
            "verdict": label, "tone": tone,
            "when": item.get("created_at"),
            "detail": {
                "verdict": label,
                "decision": str(verdict.get("decision") or ""),
                "risk": None if risk is None else int(_num(risk)),
                "reason": str(verdict.get("reason")
                              or ("no issues found" if graded
                                  else "awaiting grading")),
                "pii": ", ".join(signals) if signals else "none detected",
                "grading": str(item.get("grading_status") or "—"),
                "agent": str(item.get("agent") or ""),
                "policy": str(item.get("policy_tag") or "—"),
                "chain_hash": str(item.get("chain_hash") or ""),
            },
        })
    return out


# ── summary tiles + verdict mix (web loadLedgerCharts, html:3634-3652) ──────
def summary_tiles(stats: dict | None) -> list[tuple]:
    stats = stats if isinstance(stats, dict) else {}
    grading = stats.get("grading") or {}
    if not isinstance(grading, dict):
        grading = {}
    clean = stats.get("clean_rate")
    total = stats.get("total_logged")
    breaches = stats.get("breaches")
    pending = None
    if grading:
        pending = int(_num(grading.get("pending")) + _num(grading.get("in_progress")))
    return [
        ("Records", "—" if total is None else thousands(total), "fox"),
        ("Breaches", "—" if breaches is None else thousands(breaches), "bad"),
        ("Clean rate", fmt_pct(clean), None),
        ("Pending grading", "—" if pending is None else thousands(pending), None),
    ]


def verdict_slices(stats: dict | None) -> tuple[list[dict], int]:
    """(slices, total) for the verdict-mix donut.

    "Clean" is graded MINUS breaches, blocked, redacted and evaluator-unknown.
    Blocked and redacted are prevented egress and unknown is a non-answer —
    folding any of them into clean would report a safety result the system
    never actually reached (html:3641-3644).
    """
    stats = stats if isinstance(stats, dict) else {}
    grading = stats.get("grading") or {}
    if not isinstance(grading, dict):
        grading = {}
    breaches = _num(stats.get("breaches"))
    graded = _num(grading.get("graded"))
    blocked = _num(stats.get("blocked"))
    redacted = _num(stats.get("redacted"))
    unknown = _num(stats.get("evaluator_unknown"))
    clean = max(0.0, graded - breaches - blocked - redacted - unknown)
    pending = _num(grading.get("pending")) + _num(grading.get("in_progress"))
    failed = _num(grading.get("failed"))
    slices = [
        {"label": "Clean", "value": clean, "tone": "ok"},
        {"label": "Breach", "value": breaches, "tone": "bad"},
        {"label": "Blocked", "value": blocked, "tone": "blue"},
        {"label": "Redacted", "value": redacted, "tone": "pink"},
        {"label": "Evaluator unknown", "value": unknown, "tone": "warn"},
        {"label": "Pending", "value": pending, "tone": "fox"},
        {"label": "Failed", "value": failed, "tone": "mute"},
    ]
    return slices, int(sum(s["value"] for s in slices))


def volume_rows(usage: dict | None) -> list[dict]:
    """Logged interactions per day for the 90-day area chart."""
    days = dict_rows((usage or {}).get("days") if isinstance(usage, dict) else None)
    return [{"label": d.get("day"), "value": _num(d.get("logs_count"))}
            for d in days]


def verify_result(data: dict | None) -> tuple[str, str]:
    """(button text, tone) after "verify this record" (html:2310-2313)."""
    if not isinstance(data, dict):
        return "could not verify", "warn"
    if not data.get("found"):
        return "not in this ledger", "bad"
    if data.get("verified"):
        return "✓ verified — untampered", "ok"
    return "⚠ mismatch — altered", "bad"
