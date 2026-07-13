"""Pure decision logic for the desktop breach poller (Phase 5 · 5A.1b).

No PyQt import here on purpose: the QThread that does the HTTP polling lives in
omni_fox.py (BreachPollWorker); this module just decides *what to react to* given
what the backend returned, so it can be unit-tested without a display.

The desktop polls  GET /v1/logs/breaches?since_seq={cursor}  which already returns
only graded breaches with seq > cursor, ascending. plan_reactions() turns that
into (breaches_to_fire, new_cursor).
"""

from __future__ import annotations


def plan_reactions(breaches: list[dict], cursor: int, first_poll: bool):
    """Decide which breaches the fox should react to, and advance the cursor.

    - `breaches`: rows from /v1/logs/breaches (ascending by seq, already seq>cursor).
    - `cursor`: the highest breach seq already handled.
    - `first_poll`: True on the poller's very first tick — absorb existing breaches
      as a baseline (don't fire) so the fox never floods with stale alerts on launch.

    Returns (to_fire, new_cursor). The cursor only ever advances (monotonic), so a
    breach can never re-fire.
    """
    if not breaches:
        return [], cursor
    new_cursor = max(cursor, max(b["seq"] for b in breaches))
    if first_poll:
        return [], new_cursor
    return list(breaches), new_cursor
