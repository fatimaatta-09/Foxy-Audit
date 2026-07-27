"""Foxy Audit desktop — Threat analytics data shaping (D5).

Everything `#page-analytics` decides before it paints, with no Qt in it.
Ported from the live web loaders so the two products agree:

    stat row            foxy-audit-premium.html:1122-1127 + :2265-2270
    activity 7d bar     :2272-2281  (padded window, zero-days are honest)
    threat timeline     :3377-3387  (loadTimeline, stacked by risk band)
    breaches by agent   :3388-3394  (loadByAgent, top 8, "unattributed")
    avg risk + gauge    :3360-3366
    top policies        :3367-3373
    recent high-risk    :3324-3340  (alertTableRow)

`/v1/analytics/threats` is the one list source with **no `response_model`**
(backend/app/routers/analytics.py:25) — it hand-builds its rows — so every
list here goes through `home_data.dict_rows()` rather than trusting the wire.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from home_data import _num, dict_rows, fmt_pct, thousands

#: The web offers 7/30/90 and defaults to 30 (html:3379, `let _threatDays=30`).
RANGES = (7, 30, 90)
DEFAULT_RANGE = 30

#: Top-N for both hbar charts (html:3371, :3393 — `.slice(0,8)`).
TOP_N = 8
#: The web sizes those charts `Math.max(60, rows*34)`.
ROW_PX = 34
MIN_HBAR_PX = 60

#: Risk bands, as the backend computes them (analytics.py:87).
BANDS = (("High", "high", "bad"), ("Medium", "medium", "warn"),
         ("Low", "low", "mute"))

_WEEKDAYS = ("Su", "Mo", "Tu", "We", "Th", "Fr", "Sa")


def stat_row(stats: dict | None, threats: dict | None) -> list[tuple]:
    """The four tiles: (label, value, tone).

    Two sources on purpose, mirroring the web: `loadStats` fills three and
    deliberately leaves "Active alerts" for `loadThreats` (html:2270 comment),
    because a breach count and a live alert count are different questions.
    """
    stats = stats if isinstance(stats, dict) else {}
    clean = stats.get("clean_rate")
    alerts = open_alert_count(threats)
    return [
        ("Total logged", _count_or_dash(stats.get("total_logged")), "fox"),
        ("Breaches prevented", _count_or_dash(stats.get("breaches")), None),
        ("Active alerts", _count_or_dash(alerts), "bad"),
        ("Clean rate", fmt_pct(clean), None),
    ]


def _count_or_dash(value) -> str:
    """A count we were not given is an em dash, never a confident 0."""
    return "—" if value is None else thousands(value)


def open_alert_count(threats: dict | None) -> int | None:
    """`total_threats`, falling back to the page size (html:3355). None when
    the call did not answer — "no open alerts" and "we could not ask" are
    different statements."""
    if not isinstance(threats, dict):
        return None
    total = threats.get("total_threats")
    if total is not None:
        return int(_num(total))
    rows = threats.get("recent_high_risk")
    return len(dict_rows(rows)) if isinstance(rows, list) else None


# ── activity, last 7 days (web html:2272-2281) ──────────────────────────────
def activity_bars(stats: dict | None, today: datetime | None = None) -> list[dict]:
    """Seven bars, one per day, padded across the real window.

    The API only returns days that had events, so the web pads the gaps with
    zeroes — a day with no activity is a fact worth drawing, and leaving it out
    would silently compress the axis and imply activity that did not happen.
    """
    stats = stats if isinstance(stats, dict) else {}
    by_date = {row.get("date"): row
               for row in dict_rows(stats.get("activity_7d"))}
    today = today or datetime.now()
    out = []
    for offset in range(6, -1, -1):
        day = today - timedelta(days=offset)
        key = day.strftime("%Y-%m-%d")
        row = by_date.get(key) or {}
        count = int(_num(row.get("count")))
        breaches = int(_num(row.get("breaches")))
        out.append({
            "label": _WEEKDAYS[(day.weekday() + 1) % 7],   # Python Mon=0, JS Sun=0
            "value": count,
            "tone": "bad" if breaches > 0 else "ok",
            "sub": (f"{breaches} breach" if breaches > 0
                    else ("clean" if count else "no events")),
        })
    return out


# ── breaches over time, stacked by risk band (web html:3377-3387) ───────────
def timeline_series(data: dict | None) -> tuple[list[str], list[dict]]:
    """(labels, series) for the stacked chart. Empty labels ⇒ empty state."""
    days = dict_rows((data or {}).get("days") if isinstance(data, dict) else None)
    if not days:
        return [], []
    labels = [str(d.get("day") or "") for d in days]
    series = [{"name": name, "tone": tone,
               "values": [_num(d.get(key)) for d in days]}
              for name, key, tone in BANDS]
    return labels, series


def timeline_total(data: dict | None) -> int:
    """Breaches in the window — used to decide empty vs populated honestly,
    since a stacked chart of all-zero days is not the same as no days."""
    return int(sum(_num(d.get("total"))
                   for d in dict_rows((data or {}).get("days")
                                      if isinstance(data, dict) else None)))


# ── breaches by agent (web html:3388-3394) ──────────────────────────────────
def agent_rows(data: dict | None) -> list[dict]:
    """Top 8 agents by breach count. NULL agents already collapse to
    'unattributed' server-side (analytics.py:111); the fallback here is for a
    row that arrives with the key missing entirely."""
    agents = dict_rows((data or {}).get("agents") if isinstance(data, dict) else None)
    return [{"label": str(a.get("agent") or "unattributed"),
             "value": _num(a.get("count")), "tone": "bad"}
            for a in agents[:TOP_N]]


def policy_rows(threats: dict | None) -> list[dict]:
    """Top 8 flagged policies (web html:3370-3371)."""
    pols = dict_rows((threats or {}).get("top_policies")
                     if isinstance(threats, dict) else None)
    return [{"label": str(p.get("tag") or "—"),
             "value": _num(p.get("count")), "tone": "bad"}
            for p in pols[:TOP_N]]


def hbar_height(rows: list) -> int:
    """The web's `Math.max(60, rows*34)`."""
    return max(MIN_HBAR_PX, len(rows) * ROW_PX)


# ── average risk (web html:3360-3366) ───────────────────────────────────────
def avg_risk_view(threats: dict | None) -> dict:
    """Number, gauge tone, and the note under it.

    Tone flips at the same thresholds the backend bands on: 40 and 70.
    """
    threats = threats if isinstance(threats, dict) else {}
    raw = threats.get("avg_risk_score")
    total = threats.get("total_threats")
    value = _num(raw)
    if raw is None:
        tone = "mute"
    elif value >= 70:
        tone = "bad"
    elif value >= 40:
        tone = "warn"
    else:
        tone = "ok"
    if total:
        count = int(_num(total))
        note = f"across {thousands(count)} breach{'' if count == 1 else 'es'}"
    else:
        note = "no breaches yet"
    return {
        "text": "—" if raw is None else str(int(value)),
        "value": value, "tone": tone, "note": note,
        "label": "" if raw is None else f"avg risk {int(value)}",
        "tip": f"Average breach risk {int(value)}",
    }


# ── recent high-risk table (web alertTableRow, html:3331-3340) ──────────────
ALERT_COLUMNS = ("#", "Policy", "Agent", "Risk", "When")
ALERT_LIMIT = 20


def alert_table_rows(threats: dict | None) -> list[dict]:
    """One dict per table row. Risk bands match `alertTableRow`: ≥70 bad,
    ≥40 warn, else neutral."""
    rows = dict_rows((threats or {}).get("recent_high_risk")
                     if isinstance(threats, dict) else None)
    out = []
    for alert in rows[:ALERT_LIMIT]:
        risk = int(_num(alert.get("risk_score")))
        out.append({
            "seq": alert.get("seq"),
            "policy": str(alert.get("policy_tag") or "policy"),
            "agent": str(alert.get("agent") or "—"),
            "risk": risk,
            "tone": "bad" if risk >= 70 else ("warn" if risk >= 40 else "mute"),
            "when": alert.get("timestamp"),
        })
    return out
