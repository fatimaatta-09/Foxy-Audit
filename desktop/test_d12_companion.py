"""D12 — the companion event layer: what the fox reacts to, and what it claims.

A mascot is a UI element that speaks in the first person, so the honesty rules
bite harder here than anywhere else in the app. Four things carry the weight:

* a reaction is a CLAIM — the fox never cheers, warns or reassures about
  something the backend did not actually say;
* an event fires ONCE — every repeating stream carries a cursor, because a fox
  that re-announces the same breach every ten seconds is worse than a mute one;
* the quick status panel can say "I don't know" per line, and does;
* the payload keys are the ones the routers really send (checked against the
  backend where it is in the tree).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import companion_events as ce
import companion_status as cs

_HERE = Path(__file__).resolve().parent


# ══ the router shape ════════════════════════════════════════════════════════
def test_the_router_has_no_qt_and_no_http():
    """The whole point of the split: the interesting half is testable without
    a display, and cannot quietly grow a network call."""
    tree = ast.parse((_HERE / "companion_events.py").read_text(encoding="utf-8"))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            modules.add((node.module or "").split(".")[0])
        elif isinstance(node, ast.Import):
            modules |= {a.name.split(".")[0] for a in node.names}
    assert not modules & {"PyQt6", "requests", "urllib", "http", "socket"}


def test_every_state_the_router_can_ask_for_exists_as_a_sprite_row():
    """A typo in STATES would paint the wrong animal, or nothing."""
    source = (_HERE / "omni_fox.py").read_text(encoding="utf-8")
    for state, row in ce.STATES.items():
        assert f"\n{row} " in source or f"\n{row}=" in source, \
            f"{state} maps to {row}, which omni_fox does not define"


def test_an_unknown_state_or_overlay_is_a_programming_error():
    with pytest.raises(ValueError):
        ce.reaction("SMUG", 1.0)
    with pytest.raises(ValueError):
        ce.reaction("CHEERING", 1.0, overlay="chartreuse")


# ══ breach ══════════════════════════════════════════════════════════════════
BREACH = {"reason": "PHI in prompt", "risk_score": 82, "policy": "hipaa"}


def test_a_breach_below_the_users_threshold_does_not_interrupt():
    """It is still real and still in the ledger — the fox just does not shout."""
    assert ce.on_breach(BREACH, threshold=90) is None
    assert ce.on_breach(BREACH, threshold=82) is not None
    assert ce.on_breach(BREACH, threshold=0) is not None


def test_an_unscored_breach_is_treated_as_the_worst_case():
    """A breach the grader could not score is not a quiet one."""
    react = ce.on_breach({"reason": "x"}, threshold=99)
    assert react is not None and "100" in react["bubble"]


def test_turning_breach_alerts_off_silences_the_whole_reaction():
    assert ce.on_breach(BREACH, alerts_enabled=False) is None


def test_sound_and_toast_are_separately_suppressible():
    quiet = ce.on_breach(BREACH, sound=False, toasts=False)
    assert quiet["sound"] is False and quiet["toast"] is None
    assert quiet["state"] == "ALERTING" and quiet["overlay"] == "red"


def test_a_breach_routes_to_the_page_that_can_act_on_it():
    assert ce.on_breach(BREACH)["route"] == "threats"


def test_a_non_dict_payload_produces_no_reaction():
    assert ce.on_breach(None) is None
    assert ce.on_breach("boom") is None


# ══ anchors ═════════════════════════════════════════════════════════════════
def _anchor(ident, status="confirmed"):
    return {"id": ident, "status": status, "tx_hash": "0x" + ident}


def test_only_a_confirmed_anchor_is_cheered():
    """Pending is a promise. The whole point of anchoring is that the head is
    ON a public chain — cheering before it is would be a mascot breaking the
    no-fake-data rule."""
    react, seen = ce.on_anchors([_anchor("a1", "pending")], "prev")
    assert react is None and seen == "prev"


def test_the_first_look_adopts_a_baseline_instead_of_cheering_history():
    """An anchor that landed while the app was closed is not news."""
    react, seen = ce.on_anchors([_anchor("a1")], "")
    assert react is None and seen == "a1"


def test_a_new_confirmed_anchor_fires_exactly_once():
    react, seen = ce.on_anchors([_anchor("a2")], "a1")
    assert react is not None and seen == "a2"
    assert react["route"] == "verify" and react["state"] == "CHEERING"
    again, seen2 = ce.on_anchors([_anchor("a2")], seen)
    assert again is None and seen2 == "a2"


def test_a_disabled_anchor_alert_absorbs_rather_than_defers():
    """Turning the toggle back on must not unleash every anchor that landed
    while it was off."""
    _react, seen = ce.on_anchors([_anchor("a2")], "a1", enabled=False)
    assert seen == "a2"
    react, _ = ce.on_anchors([_anchor("a2")], seen)
    assert react is None


def test_an_unreadable_anchor_list_changes_nothing():
    for payload in (None, {}, "nope", [None, "x"]):
        react, seen = ce.on_anchors(payload, "a1")
        assert react is None and seen == "a1"


# ══ quota ═══════════════════════════════════════════════════════════════════
def _usage(used, limit=1000):
    return {"quota": {"monthly_log_quota": limit, "used_this_month": used}}


def test_the_quota_warning_fires_once_per_crossing_not_once_per_poll():
    react, warned = ce.on_quota(_usage(950))
    assert react is not None and warned is True
    again, warned = ce.on_quota(_usage(960), already_warned=warned)
    assert again is None and warned is True


def test_dropping_back_under_the_line_re_arms_the_warning():
    """Topping up is what makes the fox stop, not the fox giving up."""
    _r, warned = ce.on_quota(_usage(950))
    react, warned = ce.on_quota(_usage(100), already_warned=warned)
    assert react is None and warned is False
    react, _ = ce.on_quota(_usage(999), already_warned=warned)
    assert react is not None


def test_being_over_says_capture_has_stopped_not_that_it_is_nearly_full():
    react, _ = ce.on_quota(_usage(1200))
    assert "Out of capture credits" == react["bubble"]
    assert "not being recorded" in react["toast"][1]
    assert react["route"] == "billing"


def test_an_unmetered_plan_can_never_run_out():
    react, warned = ce.on_quota(_usage(99999, limit=0))
    assert react is None and warned is False


def test_a_missing_quota_block_is_not_a_full_one():
    for payload in (None, {}, {"quota": "nope"}, {"quota": {}}):
        react, _ = ce.on_quota(payload)
        assert react is None


# ══ grading failures ════════════════════════════════════════════════════════
def _stats(failed):
    return {"grading": {"pending": 0, "in_progress": 0, "graded": 9,
                        "failed": failed}}


def test_failed_is_read_from_the_grading_block_the_server_actually_sends():
    """`/v1/stats` nests the counts under `grading` (GradingCounts,
    logs.py:533/574) — a top-level `failed` does not exist."""
    _r, _s, failed = ce.on_grading(_stats(4), prev_failed=0, streak=0)
    assert failed == 4
    # A top-level `failed` is not a reading at all, so the baseline must stay
    # where it was rather than being taken as zero.
    _r, streak, failed = ce.on_grading({"failed": 4}, prev_failed=7, streak=2)
    assert failed == 7 and streak == 2


def test_an_old_pile_of_failures_is_a_number_not_a_problem():
    """A workspace sitting at twelve failures that is not growing has nothing
    new wrong with it."""
    streak, prev = 0, 12
    for _ in range(5):
        react, streak, prev = ce.on_grading(_stats(12), prev_failed=prev,
                                            streak=streak)
        assert react is None
    assert streak == 0


def test_the_fox_speaks_once_a_run_of_failures_reaches_the_spike():
    streak, prev, fired = 0, 0, []
    for n in range(1, 7):
        react, streak, prev = ce.on_grading(_stats(n), prev_failed=prev,
                                            streak=streak)
        if react:
            fired.append(n)
    assert fired == [ce.GRADING_SPIKE]      # once, not every tick after


def test_the_first_poll_of_a_session_cannot_start_a_run():
    """With no predecessor there is no "it went up" — only a first reading."""
    react, streak, _ = ce.on_grading(_stats(50), prev_failed=None, streak=0)
    assert react is None and streak == 0


def test_an_unreadable_stats_payload_does_not_move_the_baseline():
    """Advancing `prev_failed` on a failed read would make the next real
    reading look like a jump."""
    react, streak, prev = ce.on_grading(None, prev_failed=7, streak=2)
    assert react is None and streak == 2 and prev == 7


def test_grading_streak_is_defined_once():
    """`on_grading` calls it rather than counting for itself — two copies of
    "what counts as a run" is how the screen stops matching the test."""
    assert ce.grading_streak(None, 5, 3) == 0
    assert ce.grading_streak(4, 5, 3) == 4
    assert ce.grading_streak(5, 5, 3) == 0
    assert ce.grading_streak(6, 5, 3) == 0


# ══ connectivity ════════════════════════════════════════════════════════════
def test_only_the_transition_gets_a_reaction():
    """A fox that yawns every ten seconds while the wifi is off is noise."""
    assert ce.on_connectivity(False, False) is None
    assert ce.on_connectivity(True, True) is None
    assert ce.on_connectivity(False, True)["state"] == "SLEEP"
    assert ce.on_connectivity(True, False)["state"] == "CHEERING"


def test_the_first_check_ever_is_a_silent_baseline():
    assert ce.on_connectivity(True, None) is None
    assert ce.on_connectivity(False, None) is None


def test_going_offline_says_nothing_is_being_checked():
    """Not "you are safe" — the fox has stopped being able to tell."""
    assert "Offline" in ce.on_connectivity(False, True)["bubble"]


# ══ the SDK bridge ══════════════════════════════════════════════════════════
def test_a_pending_grading_is_never_green():
    """Green would claim a verdict before the judge has run."""
    react = ce.on_sdk_evaluating()
    assert react["state"] == "THINKING" and react["overlay"] is None


def test_a_local_hash_says_local_hash_and_not_audited():
    assert "queued" in ce.on_sdk_hash({"delivery": "queued"})["bubble"]
    assert ce.on_sdk_hash({})["bubble"] == "local hash only"


# ══ the quick status panel ══════════════════════════════════════════════════
def test_a_skipped_verification_is_neither_a_pass_nor_a_tamper_finding():
    """`/v1/verify` answers ok=False + first_broken_seq=None when the ledger is
    too long to check (verify.py:107-112). Calling that tampering is the exact
    bug that shipped to the live web dashboard."""
    view = cs.chain_line({"ok": False, "first_broken_seq": None,
                          "detail": "ledger too long"})
    assert view["state"] == cs.EMPTY and view["tone"] == "warn"
    assert "BROKEN" not in view["value"] and "not checked" == view["value"]


def test_a_real_break_is_named_with_its_sequence():
    view = cs.chain_line({"ok": False, "first_broken_seq": 42})
    assert view["value"] == "BROKEN" and "#42" in view["note"]
    assert view["tone"] == "bad"


def test_an_unreachable_verify_is_not_an_intact_chain():
    for view in (cs.chain_line(None, ok=False), cs.chain_line("nope")):
        assert view["state"] == cs.ERROR
        assert "intact" not in view["value"]


def test_credits_left_is_computed_not_guessed():
    view = cs.credits_line({"quota": {"monthly_log_quota": 1000,
                                      "used_this_month": 940}})
    assert view["value"] == "60 left" and view["tone"] == "warn"
    assert cs.credits_line({"quota": {"monthly_log_quota": 1000,
                                      "used_this_month": 1200}})["tone"] == "bad"
    assert cs.credits_line({"quota": {"monthly_log_quota": 0,
                                      "used_this_month": 5}})["value"] == "unmetered"


def test_a_key_only_user_is_told_alerts_are_unreadable_not_that_there_are_none():
    """`/v1/notifications` is session-only. "none unread" would be a claim
    about an inbox we cannot see."""
    view = cs.alerts_line(None, ok=False)
    assert view["state"] == cs.ERROR and "none" not in view["value"]


def test_unread_counts_only_the_unread():
    items = {"items": [{"read_at": "x"}, {"read_at": None},
                       {"read_at": None, "level": "critical"}]}
    view = cs.alerts_line(items)
    assert view["value"] == "2 unread" and view["note"] == "critical"
    assert cs.alerts_line({"items": [{"read_at": "x"}]})["state"] == cs.EMPTY


def test_today_is_the_last_day_the_server_returned():
    """Not `len(days)`, and not a zero invented for a day it did not mention."""
    usage = {"days": [{"day": "2026-07-27", "logs_count": 5},
                      {"day": "2026-07-28", "logs_count": 9}]}
    view, spark = cs.today_line(usage)
    assert view["value"] == "9 today" and len(spark) == 2
    assert spark[-1] == {"label": "2026-07-28", "value": 9}


def test_the_sparkline_is_capped_at_fourteen_days():
    usage = {"days": [{"day": str(d), "logs_count": d} for d in range(60)]}
    _view, spark = cs.today_line(usage)
    assert len(spark) == 14 and spark[-1]["value"] == 59


def test_a_loading_panel_claims_nothing_at_all():
    """A panel that opens showing "0 unread" and then corrects itself has
    already told the user something false."""
    view = cs.panel_view(loading=True)
    assert all(view[k]["state"] == cs.LOADING
               for k in ("chain", "credits", "alerts", "today"))
    assert view["spark"] == []


def test_signed_out_is_not_loading():
    """Rendering it caught this: all four lines said "asking the backend" when
    nothing was in flight and nothing was going to be."""
    view = cs.panel_view(signed_out=True)
    assert all(view[k]["state"] == cs.EMPTY
               for k in ("chain", "credits", "alerts", "today"))
    assert not any("asking" in view[k]["note"]
                   for k in ("chain", "credits", "alerts", "today"))


def test_the_signed_out_panel_is_reachable_from_the_fox():
    """`_panel_view` must consult `_can_poll` BEFORE the has-it-answered
    check, or a signed-out user gets the loading state forever."""
    source = (_HERE / "omni_fox.py").read_text(encoding="utf-8")
    body = source.split("def _panel_view(")[1].split("\n    def ")[0]
    assert body.index("_can_poll") < body.index('s["asked"]')


def test_a_verified_count_is_readable():
    assert "18,422" in cs.chain_line({"ok": True, "count": 18422})["note"]


def test_each_line_fails_independently():
    """One dead endpoint must not blank the three that answered."""
    view = cs.panel_view({"ok": True, "count": 12}, None, {"items": []},
                         usage_ok=False)
    assert view["chain"]["state"] == cs.OK
    assert view["credits"]["state"] == cs.ERROR
    assert view["alerts"]["state"] == cs.EMPTY


def test_the_panel_routes_are_real_console_sections():
    source = (_HERE / "dashboard.py").read_text(encoding="utf-8")
    for _label, section in cs.PANEL_ROUTES:
        assert f'"{section}":' in source, f"{section} is not a console section"


# ══ the wiring ══════════════════════════════════════════════════════════════
def test_the_alert_defaults_are_what_the_fox_already_did():
    """Adding the keys must not change behaviour on its own — D13 adds the UI
    that can turn them off."""
    from PyQt6.QtCore import QSettings
    from PyQt6.QtWidgets import QApplication
    from fox_settings import FoxSettings
    from foxy_client import MemorySecretStore
    import tempfile, os
    QApplication.instance() or QApplication([])
    path = os.path.join(tempfile.mkdtemp(), "s.ini")
    s = FoxSettings(QSettings(path, QSettings.Format.IniFormat),
                    MemorySecretStore())
    assert s.breach_alerts_enabled() is True
    assert s.alert_sound_enabled() is True
    assert s.native_toasts_enabled() is True
    assert s.alert_min_risk() == 0          # interrupt for every breach
    assert s.quota_alerts_enabled() is True
    assert s.anchor_alerts_enabled() is True
    assert s.grading_alerts_enabled() is True
    s.set_alert_min_risk(700)
    assert s.alert_min_risk() == 100        # clamped, never a nonsense gate


def test_the_breach_tally_counts_breaches_not_interruptions():
    """The weekly summary would understate the week if a below-threshold
    breach went uncounted — it still happened, and it is still in the ledger."""
    source = (_HERE / "omni_fox.py").read_text(encoding="utf-8")
    body = source.split("def _on_policy_breach(")[1].split("\n    def ")[0]
    tally = body.index("bump_weekly_breaches")
    early_return = body.index("if react is None:")
    assert tally < early_return, \
        "a suppressed breach stops being counted for the weekly summary"
