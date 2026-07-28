"""Foxy Audit desktop — what the fox does about a real event (D12, plan §9.1).

One router, no Qt, no HTTP. The six event sources plan §9.1 names — breach,
anchor confirmed, quota, grading failures, connectivity, and the SDK bridge —
are decided here and returned as data; `omni_fox` does nothing but carry the
result out. That split exists so the interesting half — which events deserve a
reaction, which are noise, and what the fox is allowed to claim — is testable
without a display.

Deliberately NOT routed through here: the startup `/v1/health` probe, which
also sets the dashboard's connected flag and so is inseparable from the
window, and everything with no backend behind it at all — typing and scroll
reactions, CPU/RAM, idle breaks, the pat, the compliance tips. Those are
ambience; this file is the part that makes a claim about the customer's
evidence.

**Three rules the router is built around.**

*A reaction is a claim.* CHEERING says "this is fine"; the fox may only say it
about something the backend confirmed. Nothing here reacts to an absent, a
failed, or an unparseable answer — `None` means "say nothing", and that is the
default, not the exception.

*The fox is not the dashboard.* It interrupts for things that are worth
interrupting for and stays quiet otherwise: a breach at or above the user's
threshold, a quota that is about to stop their capture, an anchor landing, a
run of grading failures. Everything else lives in the console.

*An event fires once.* Every stream polled here carries a "what have I already
reacted to" value the caller keeps and the router advances: the last cheered
anchor id, the over-quota latch, the grading-failure run. (Breaches keep their
cursor in `breach_poll.plan_reactions`, which already had one — this file only
decides whether a breach the poller already de-duplicated is loud enough to
interrupt for.) The reason is the same in all four: a fox that re-announces
the same thing every ten seconds is worse than a silent one.
"""

from __future__ import annotations

#: Every state the router may ask for, mapped to the sprite row NAME. The
#: caller resolves the name to a row constant, so this module never imports
#: the spritesheet layout and a typo here fails a test rather than painting
#: the wrong animal.
STATES = {
    "ALERTING": "ROW_ALERT",
    "CHEERING": "ROW_CHEERING",
    "CRYING": "ROW_CRYING",
    "THINKING": "ROW_THINKING",
    "STANDING": "ROW_STANDING",
    "SLEEP": "ROW_SLEEP",
}

#: Overlay tints `SecurityOverlay` can actually paint.
OVERLAYS = ("red", "green", "amber", None)

QUOTA_WARN_PCT = 90.0
#: Consecutive failed gradings before the fox mentions it. One failure is a
#: transient provider blip; a run of them means the judge is not grading.
GRADING_SPIKE = 3


def reaction(state: str, duration: float, *, overlay: str | None = None,
             bubble: str | None = None, toast: tuple | None = None,
             sound: bool = False, route: str | None = None) -> dict:
    """One reaction. `route` is the console section the toast/bubble leads to,
    so a fox that says "you are nearly out of credits" can also be the way to
    go fix it."""
    if state not in STATES:
        raise ValueError(f"unknown fox state: {state}")
    if overlay not in OVERLAYS:
        raise ValueError(f"unknown overlay: {overlay}")
    return {"state": state, "row": STATES[state], "duration": float(duration),
            "overlay": overlay, "bubble": bubble, "toast": toast,
            "sound": bool(sound), "route": route}


# ── breach ──────────────────────────────────────────────────────────────────
def on_breach(payload, *, threshold: int = 0, alerts_enabled: bool = True,
              sound: bool = True, toasts: bool = True) -> dict | None:
    """A graded policy breach from the poller or the SDK bridge.

    The threshold is the user's, not ours: below it the event is still real and
    still in the ledger, the fox simply does not interrupt for it. A payload
    with no risk score at all is treated as maximum — a breach the grader could
    not score is not a quiet one.
    """
    if not alerts_enabled or not isinstance(payload, dict):
        return None
    risk = _score(payload.get("risk_score"), default=100)
    if risk < max(0, int(threshold)):
        return None
    reason = str(payload.get("reason") or "Policy breach")
    policy = str(payload.get("policy") or payload.get("policy_tag") or "")
    body = f"{reason} (risk {risk}/100)"
    return reaction(
        "ALERTING", 5.0, overlay="red",
        bubble=f"⚠ Breach — risk {risk}",
        toast=("🚨 Policy breach", body + (f" · {policy}" if policy else ""),
               "critical") if toasts else None,
        sound=sound, route="threats")


def _score(value, *, default: int) -> int:
    try:
        return max(0, min(100, int(float(value))))
    except (TypeError, ValueError):
        return default


# ── anchoring ───────────────────────────────────────────────────────────────
def on_anchors(data, seen_id: str, *, enabled: bool = True
               ) -> tuple[dict | None, str]:
    """`GET /v1/anchors` → (reaction, new seen id).

    Only a CONFIRMED anchor is cheered. A pending one is a promise, and the
    whole point of anchoring is that the chain head is on a public ledger — the
    fox saying so before it is would be the no-fake-data rule broken by a
    mascot.
    """
    rows = data.get("items") if isinstance(data, dict) else data
    latest = _latest_confirmed(rows)
    if latest is None:
        return None, seen_id
    ident = str(latest.get("id") or latest.get("tx_hash") or "")
    if not ident or ident == seen_id:
        return None, seen_id or ident
    if not enabled:
        return None, ident          # absorbed: never fires retroactively later
    if not seen_id:
        # First look: adopt the current head as the baseline rather than
        # cheering an anchor that landed while the app was closed.
        return None, ident
    return reaction("CHEERING", 3.0, overlay="green",
                    bubble="Chain head anchored ⚓",
                    toast=("Chain head anchored",
                           "The ledger head is now on a public chain.", "info"),
                    route="verify"), ident


def _latest_confirmed(rows):
    if not isinstance(rows, list):
        return None
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("status") or "").lower() == "confirmed":
            return row
    return None


# ── quota ───────────────────────────────────────────────────────────────────
def on_quota(usage, *, enabled: bool = True, already_warned: bool = False
             ) -> tuple[dict | None, bool]:
    """`GET /v1/usage` → (reaction, warned-now).

    Fires once per crossing, not once per poll: `already_warned` latches while
    the workspace stays over the line and clears when it drops back under, so
    topping up is what makes the fox stop rather than the fox giving up.
    """
    quota = (usage or {}).get("quota") if isinstance(usage, dict) else None
    if not isinstance(quota, dict):
        return None, already_warned
    try:
        limit = float(quota.get("monthly_log_quota") or 0)
        used = float(quota.get("used_this_month") or 0)
    except (TypeError, ValueError):
        return None, already_warned
    if limit <= 0:
        return None, False          # unmetered plan — nothing to run out of
    pct = used / limit * 100.0
    if pct < QUOTA_WARN_PCT:
        return None, False          # back under: the next crossing may fire
    if already_warned or not enabled:
        return None, True
    over = pct >= 100.0
    return reaction(
        "CRYING", 4.0, overlay="amber",
        bubble="Out of capture credits" if over else f"{pct:.0f}% of credits used",
        toast=("Capture credits" ,
               "This workspace is out of monthly capture credits — new events "
               "are not being recorded." if over else
               f"{pct:.0f}% of this month's capture credits are used.",
               "warning"),
        route="billing"), True


# ── grading failures ────────────────────────────────────────────────────────
def on_grading(stats, *, prev_failed: int | None = None, streak: int = 0,
               enabled: bool = True) -> tuple[dict | None, int, int | None]:
    """`GET /v1/stats` → (reaction, new streak).

    `failed` lives in the `grading` block (`GradingCounts`, logs.py:533/574),
    not at the top level. Watches it climbing rather than its absolute value: a
    workspace with twelve old failures has a number, not a problem. `streak`
    counts consecutive polls that added failures (see `grading_streak`); the
    fox speaks once the run reaches GRADING_SPIKE and then stays quiet until
    the run breaks.
    """
    grading = (stats or {}).get("grading") if isinstance(stats, dict) else None
    failed = grading.get("failed") if isinstance(grading, dict) else None
    try:
        failed = int(failed)
    except (TypeError, ValueError):
        # Nothing measured — nothing to conclude, and `prev_failed` must NOT
        # advance or the next real reading would look like a jump.
        return None, streak, prev_failed
    new_streak = grading_streak(prev_failed, failed, streak)
    if new_streak != GRADING_SPIKE or not enabled:
        # `!=`, not `<`: the reaction fires on the poll that reaches the run
        # and the streak keeps climbing, so it cannot repeat every tick.
        return None, new_streak, failed
    return reaction("THINKING", 3.5, overlay="amber",
                    bubble="Gradings are failing",
                    toast=("Grading failures",
                           f"{failed} event(s) could not be graded. Their "
                           f"verdicts are pending, not clean.", "warning"),
                    route="ledger"), new_streak, failed


def grading_streak(prev_failed: int | None, failed: int | None,
                   streak: int) -> int:
    """How many consecutive polls have ADDED a grading failure.

    The single definition of the rule — `on_grading` calls this rather than
    counting for itself, because two copies of "what counts as a run" is how
    the number on screen stops matching the number in the test. The first poll
    of a session has no predecessor and therefore no run.
    """
    if prev_failed is None or failed is None:
        return 0
    return streak + 1 if failed > prev_failed else 0


# ── connectivity ────────────────────────────────────────────────────────────
def on_connectivity(online: bool, was_online: bool | None
                    ) -> dict | None:
    """Only the TRANSITION is worth a reaction; a fox that yawns every ten
    seconds while the wifi is off is noise. `None` for `was_online` is the
    first check ever, which establishes the baseline silently."""
    if was_online is None or bool(online) == bool(was_online):
        return None
    if online:
        return reaction("CHEERING", 2.0, overlay="green",
                        bubble="Back online ✓")
    return reaction("SLEEP", 6.0, overlay=None,
                    bubble="Offline — nothing is being checked")


# ── SDK bridge ──────────────────────────────────────────────────────────────
def on_sdk_evaluating() -> dict:
    """The SDK logged a call and grading is PENDING. Neutral on purpose: green
    here would claim a verdict before the judge has run."""
    return reaction("THINKING", 1.5)


def on_sdk_hash(payload) -> dict:
    delivery = (payload or {}).get("delivery") if isinstance(payload, dict) else None
    queued = delivery == "queued"
    return reaction("STANDING", 1.5, overlay="green",
                    bubble="local hash queued" if queued else "local hash only")


__all__ = ["GRADING_SPIKE", "OVERLAYS", "QUOTA_WARN_PCT", "STATES",
           "grading_streak", "on_anchors", "on_breach", "on_connectivity",
           "on_grading", "on_quota", "on_sdk_evaluating", "on_sdk_hash",
           "reaction"]
