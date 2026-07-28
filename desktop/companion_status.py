"""Foxy Audit desktop — what the fox's quick status panel says (D12, §9.1).

Pure shaping for the popover the fox opens on middle-click and the tray opens
on a left click: chain state, capture credits, unread alerts, today's logs.
No Qt, no HTTP.

**The panel is allowed to say "I don't know".** It has four independent
readings and any of them can be missing — the panel opened before a poll came
back, the endpoint is session-only and this is a key-only user, the backend is
down. Each line therefore carries its own `panel_state`-style state rather
than a value that might be nothing dressed up as zero. That is the same rule
D4 got wrong across six Home panels and D5 fixed for good: EMPTY is a
measurement, ERROR is the absence of one, and "0 credits used" is a claim.

The chain line is the one that matters most and is the easiest to get wrong.
`GET /v1/verify` answers `ok=False, first_broken_seq=None` when the ledger is
too long to check (verify.py:107-112) — that is NOT tampering, and both the
web and the desktop have shipped a bug saying it was. The third outcome is
kept here too.
"""

from __future__ import annotations

MISSING = "—"

#: Line states, mirroring `panel_state.PanelState` without importing Qt.
LOADING, OK, EMPTY, ERROR = "loading", "ok", "empty", "error"


def line(state: str, value: str, note: str = "", tone: str = "mute") -> dict:
    return {"state": state, "value": value, "note": note, "tone": tone}


def chain_line(data, ok: bool = True) -> dict:
    """`GET /v1/verify`.

    Three outcomes, not two. A skipped verification is not a pass and not a
    tamper finding — it is "nobody checked", and the absence of
    `first_broken_seq` is what distinguishes it from a real break.
    """
    if not ok:
        return line(ERROR, MISSING, "couldn't check the chain", "mute")
    if not isinstance(data, dict):
        return line(ERROR, MISSING, "couldn't check the chain", "mute")
    count = data.get("count")
    if data.get("ok"):
        return line(OK, "intact ✓",
                    f"{_int(count):,} records verified" if count is not None
                    else "", "ok")
    broken = data.get("first_broken_seq")
    if broken is None:
        # ok=False with nothing broken = the check did not run.
        return line(EMPTY, "not checked",
                    str(data.get("detail") or "the ledger is too long for a "
                                              "full check"), "warn")
    return line(ERROR, "BROKEN", f"first break at #{_int(broken)}", "bad")


def credits_line(usage, ok: bool = True) -> dict:
    """`GET /v1/usage?days=1` — the `quota` block (account.py:231)."""
    if not ok or not isinstance(usage, dict):
        return line(ERROR, MISSING, "couldn't read your plan", "mute")
    quota = usage.get("quota")
    if not isinstance(quota, dict):
        return line(EMPTY, MISSING, "no quota on this plan", "mute")
    limit, used = _int(quota.get("monthly_log_quota")), _int(quota.get("used_this_month"))
    if limit <= 0:
        return line(OK, "unmetered", f"{used:,} recorded this month", "ok")
    left = max(0, limit - used)
    pct = used / limit * 100.0
    tone = "bad" if pct >= 100 else "warn" if pct >= 90 else "ok"
    return line(OK, f"{left:,} left", f"{used:,} of {limit:,} used", tone)


def alerts_line(notifications, ok: bool = True) -> dict:
    """`GET /v1/notifications` — session-only, so a key-only user genuinely
    cannot have this and is told so rather than shown a reassuring zero."""
    if not ok:
        return line(ERROR, MISSING, "couldn't read your alerts", "mute")
    items = notifications.get("items") if isinstance(notifications, dict) \
        else notifications
    if not isinstance(items, list):
        return line(ERROR, MISSING, "couldn't read your alerts", "mute")
    unread = sum(1 for n in items
                 if isinstance(n, dict) and not n.get("read_at"))
    if not unread:
        return line(EMPTY, "none unread", f"{len(items)} in your inbox", "ok")
    return line(OK, f"{unread} unread",
                "critical" if _has_critical(items) else "", "bad")


def _has_critical(items) -> bool:
    return any(str((n or {}).get("level") or "").lower() in ("critical", "error")
               and not (n or {}).get("read_at")
               for n in items if isinstance(n, dict))


def today_line(usage, ok: bool = True) -> tuple[dict, list[dict]]:
    """(line, sparkline points) from `GET /v1/usage`'s `days` array.

    The sparkline is the last 14 days; the headline is TODAY, which is the
    last day the server returned — not `len(days)`, and not a zero invented
    for a day the server did not mention.
    """
    if not ok or not isinstance(usage, dict):
        return line(ERROR, MISSING, "couldn't read today's capture", "mute"), []
    days = usage.get("days")
    if not isinstance(days, list) or not days:
        return line(EMPTY, MISSING, "nothing captured yet", "mute"), []
    points = [{"label": str((d or {}).get("day") or ""),
               "value": _int((d or {}).get("logs_count"))}
              for d in days[-14:] if isinstance(d, dict)]
    today = points[-1]["value"] if points else 0
    return (line(OK, f"{today:,} today",
                 f"over the last {len(points)} day(s)", "ok"), points)


def _int(value) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


#: The three places the panel can send you. Section ids are the console's own
#: (`DashboardWindow.go`), so a typo here fails a test rather than silently
#: doing nothing.
PANEL_ROUTES = (("Open console", "home"), ("Threats", "threats"),
                ("Verify", "verify"))

PANEL_TITLE = "Foxy — status"
PANEL_SIGNED_OUT = ("Sign in to see this",
                    "The chain, your credits and your alerts all need a "
                    "workspace. Nothing is claimed until you do.")


def panel_view(verify=None, usage=None, notifications=None, *,
               verify_ok: bool = True, usage_ok: bool = True,
               notifications_ok: bool = True, loading: bool = False,
               signed_out: bool = False) -> dict:
    """Everything the popover draws, in one shape.

    `loading=True` is the state before any answer has come back — every line
    says so, because a panel that opens showing "0 unread" and then corrects
    itself has already told the user something false.

    `signed_out=True` is NOT loading, and conflating them is how the panel
    ended up saying "asking the backend" on all four lines when it had asked
    nothing and was never going to. Nothing is in flight; the lines say why.
    """
    if signed_out:
        blank = line(EMPTY, MISSING, "needs a workspace")
        return {"chain": blank, "credits": blank, "alerts": blank,
                "today": blank, "spark": []}
    if loading:
        blank = line(LOADING, "…", "asking the backend")
        return {"chain": blank, "credits": blank, "alerts": blank,
                "today": blank, "spark": []}
    today, spark = today_line(usage, usage_ok)
    return {
        "chain": chain_line(verify, verify_ok),
        "credits": credits_line(usage, usage_ok),
        "alerts": alerts_line(notifications, notifications_ok),
        "today": today,
        "spark": spark,
    }


__all__ = ["EMPTY", "ERROR", "LOADING", "MISSING", "OK", "PANEL_ROUTES",
           "PANEL_SIGNED_OUT", "PANEL_TITLE", "alerts_line", "chain_line",
           "credits_line", "line", "panel_view", "today_line"]
