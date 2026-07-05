"""Pure cursor/dedup logic for the desktop breach poller (Phase 5 · 5A.1b).

The desktop fox polls GET /v1/logs/breaches?since_seq=N and reacts to new
breaches. This module holds the *decision* — what to fire and how to advance the
cursor — with NO PyQt import, so it's unit-testable without a display. The
QThread glue lives in omni_fox.py (BreachPollWorker) and calls plan_reactions().

Run:  python -m pytest test_breach_poll.py -q
"""

from breach_poll import plan_reactions


def _b(seq):
    return {"seq": seq, "policy_tag": "hipaa", "reason": "tripped", "risk_score": 90}


def test_first_poll_is_baseline_no_fire():
    """On launch, absorb any existing breaches as the baseline — don't flood the
    fox with stale alerts — but advance the cursor past them."""
    to_fire, cursor = plan_reactions([_b(3), _b(5)], cursor=0, first_poll=True)
    assert to_fire == []
    assert cursor == 5


def test_subsequent_poll_fires_new_breaches():
    """After the baseline, every new breach the backend returns fires the fox (the
    endpoint already filtered to seq > cursor), and the cursor advances."""
    to_fire, cursor = plan_reactions([_b(6), _b(7)], cursor=5, first_poll=False)
    assert [b["seq"] for b in to_fire] == [6, 7]
    assert cursor == 7


def test_no_breaches_fires_nothing_and_holds_cursor():
    to_fire, cursor = plan_reactions([], cursor=7, first_poll=False)
    assert to_fire == []
    assert cursor == 7
