"""Foxy Audit desktop — Export data shaping (D8).

Everything `#page-export` decides before it paints, with no Qt in it. Ported
from the live web loaders (foxy-audit-premium.html:1384-1435 + runExportFlow /
loadExportHistory / setExportRange).

The desktop diverges from the web in one place, deliberately: the web triggers
a browser download, the desktop asks where to save and then opens the file with
the system handler. Same bytes, same endpoints — a native app that dropped a
file somewhere without saying where would be worse than the browser.
"""

from __future__ import annotations

from datetime import date, timedelta

from home_data import dict_rows

#: (label, days) — 0 means "all time", which sends no date_from (web:2457).
RANGE_PRESETS = (("30 days", 30), ("90 days", 90), ("1 year", 365),
                 ("All time", 0))

#: (value, label) for the type select (html:1404).
EXPORT_TYPES = (("passport", "Compliance passport · HTML/PDF"),
                ("logs_csv", "Audit logs · CSV"),
                ("logs_json", "Audit logs · JSON"))

TYPE_LABELS = {"passport": "Passport · HTML", "logs_csv": "Logs · CSV",
               "logs_json": "Logs · JSON"}

HISTORY_COLUMNS = ("Type", "Range", "By", "When")


def preset_range(days: int, *, today: date | None = None) -> tuple[str, str]:
    """(date_from, date_to) as ISO dates. 0 days ⇒ open-ended (web:2456-2462)."""
    today = today or date.today()
    if days <= 0:
        return "", today.isoformat()
    return (today - timedelta(days=days)).isoformat(), today.isoformat()


def export_params(date_from: str, date_to: str) -> dict:
    """The body the web posts to /v1/exports (html:runExportFlow).

    With neither bound it sends `days: 365` rather than nothing — an unbounded
    export of a large ledger is not what "generate" usually means, and the
    history row then records what was actually asked for.
    """
    params: dict = {}
    if (date_from or "").strip():
        params["date_from"] = date_from.strip()
    if (date_to or "").strip():
        params["date_to"] = date_to.strip()
    if not params:
        params["days"] = 365
    return params


def passport_query(date_from: str, date_to: str) -> str:
    from urllib.parse import quote
    parts = []
    for key, value in (("date_from", date_from), ("date_to", date_to)):
        text = (value or "").strip()
        if text:
            parts.append(f"{key}={quote(text, safe='')}")
    return ("?" + "&".join(parts)) if parts else ""


def logs_endpoint(export_type: str) -> tuple[str, str]:
    """(path, suggested file extension) for the two log formats."""
    fmt = "csv" if export_type == "logs_csv" else "json"
    return f"/v1/logs/export?format={fmt}", fmt


def suggested_filename(export_type: str, content_type: str | None = None) -> str:
    """What to put in the save dialog.

    The passport endpoint returns a PDF *or* HTML depending on whether the PDF
    renderer is available server-side, so the extension is sniffed from the
    response's Content-Type rather than assumed — saving HTML as .pdf would
    produce a file the OS opens into an error.
    """
    if export_type == "passport":
        ctype = (content_type or "").lower()
        ext = "pdf" if "pdf" in ctype else "html"
        return f"foxy-compliance-passport.{ext}"
    return f"foxy-audit-logs.{'csv' if export_type == 'logs_csv' else 'json'}"


def file_filter(export_type: str, content_type: str | None = None) -> str:
    name = suggested_filename(export_type, content_type)
    ext = name.rsplit(".", 1)[-1]
    label = {"pdf": "PDF document", "html": "HTML document",
             "csv": "CSV spreadsheet", "json": "JSON file"}[ext]
    return f"{label} (*.{ext})"


# ── progress (the web's pbar/plbl, html:1408-1410) ──────────────────────────
#: (percent, label) per stage. Real stages, not a fake timer — each one is
#: emitted when that step actually starts.
PROGRESS = {
    "requesting": (25, "aggregating hash chain…"),
    "received": (70, "signing and packaging…"),
    "saving": (90, "writing the file…"),
    "done": (100, "export ready"),
}


def progress_for(stage: str) -> tuple[int, str]:
    return PROGRESS.get(stage, (0, ""))


# ── chain metadata card (web loadChainMeta, html:3348-3360) ─────────────────
def integrity_text(verify: dict | None) -> tuple[str, str]:
    """(text, tone) for chain integrity."""
    if not isinstance(verify, dict):
        return "—", "mute"
    if verify.get("ok"):
        return "100% — no gaps", "ok"
    return f"broken at seq {verify.get('first_broken_seq')}", "bad"


def records_text(verify: dict | None) -> str:
    from home_data import _num, thousands
    if not isinstance(verify, dict) or verify.get("count") is None:
        return "—"
    return f"{thousands(int(_num(verify.get('count'))))} hash entries"


def mask(value: str | None, *, head: int = 6, tail: int = 4) -> str:
    """`9f2c1a…44ab` — the masked form of a chain head or org id.

    These are not secrets, but they identify a workspace, and the console is
    frequently on screen while someone screen-shares. Masked by default per the
    `hide_sensitive_metadata` preference, revealable on request.
    """
    text = (value or "").strip()
    if not text:
        return "—"
    if len(text) <= head + tail:
        return text
    return f"{text[:head]}…{text[-tail:]}"


# ── export history (web loadExportHistory) ──────────────────────────────────
def history_rows(data: dict | None) -> list[dict]:
    items = dict_rows((data or {}).get("items") if isinstance(data, dict) else None)
    rows = []
    for job in items:
        params = job.get("params") if isinstance(job.get("params"), dict) else {}
        rows.append({
            "type": TYPE_LABELS.get(job.get("type"), str(job.get("type") or "—")),
            "range": range_label(params),
            "by": str(job.get("requested_by") or "—"),
            "when": job.get("created_at"),
        })
    return rows


def range_label(params: dict) -> str:
    """"2026-01-01 → now" / "last 365d" / "all time" (web loadExportHistory)."""
    if not isinstance(params, dict):
        return "all time"
    if params.get("date_from"):
        return f"{params['date_from']} → {params.get('date_to') or 'now'}"
    if params.get("days"):
        return f"last {params['days']}d"
    return "all time"
