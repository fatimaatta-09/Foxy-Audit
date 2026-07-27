"""D5 — Threats + Ledger: shaping logic and the built pages.

The shaping half needs no screen. The widget half covers what only a real tree
shows: that a failed fetch never renders as an empty one, that the ledger's
verdict vocabulary survives to the pill, that paging maths and the expandable
detail behave, and that neither page's workers can gate anything.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent, QPixmap
from PyQt6.QtWidgets import QApplication

import ledger_data as ld
import threats_data as td
from panel_state import PanelState

_HERE = Path(__file__).resolve().parent


# ══ shaping: threats ════════════════════════════════════════════════════════
def test_stat_row_takes_alerts_from_threats_not_stats():
    """The web splits these two sources deliberately (html:2228): breaches is
    every breach ever, active alerts is the ones still live."""
    row = td.stat_row({"total_logged": 100, "breaches": 7, "clean_rate": 96.2},
                      {"total_threats": 3})
    assert [r[1] for r in row] == ["100", "7", "3", "96.2%"]


def test_stat_row_never_prints_a_zero_it_did_not_measure():
    assert [r[1] for r in td.stat_row(None, None)] == ["—", "—", "—", "—"]


def test_open_alert_count_prefers_the_total_and_is_none_on_failure():
    assert td.open_alert_count({"total_threats": 12}) == 12
    assert td.open_alert_count({"recent_high_risk": [{}, {}]}) == 2
    assert td.open_alert_count(None) is None


def test_activity_bars_pad_the_whole_week():
    """The API only returns days that had events; a day with none is a fact
    worth drawing, and dropping it would compress the axis."""
    bars = td.activity_bars({"activity_7d": []})
    assert len(bars) == 7
    assert all(b["value"] == 0 for b in bars)
    assert all(b["sub"] == "no events" for b in bars)


def test_activity_bar_turns_red_on_a_day_with_a_breach():
    from datetime import datetime
    today = datetime(2026, 7, 27)
    bars = td.activity_bars(
        {"activity_7d": [{"date": "2026-07-27", "count": 9, "breaches": 2}]},
        today=today)
    assert bars[-1]["tone"] == "bad" and bars[-1]["sub"] == "2 breach"


def test_timeline_series_carries_all_three_bands_in_order():
    labels, series = td.timeline_series(
        {"days": [{"day": "2026-07-01", "high": 2, "medium": 1, "low": 0}]})
    assert labels == ["2026-07-01"]
    assert [s["name"] for s in series] == ["High", "Medium", "Low"]
    assert [s["tone"] for s in series] == ["bad", "warn", "mute"]


def test_timeline_of_no_days_is_empty_not_zeroed():
    assert td.timeline_series({"days": []}) == ([], [])
    assert td.timeline_series(None) == ([], [])


def test_agent_and_policy_rows_are_capped_and_labelled():
    rows = td.agent_rows({"agents": [{"agent": None, "count": 4}]
                          + [{"agent": f"a{i}", "count": i} for i in range(12)]})
    assert len(rows) == td.TOP_N
    assert rows[0]["label"] == "unattributed"      # missing key, not NULL
    assert td.hbar_height(rows) == td.TOP_N * td.ROW_PX


def test_untyped_threat_rows_cannot_crash_the_page():
    """/v1/analytics/threats has no response_model — it hand-builds its rows,
    so a non-dict item is skipped rather than trusted."""
    junk = {"recent_high_risk": ["nope", None, 42,
                                 {"seq": 1, "risk_score": 90}],
            "top_policies": ["x", {"tag": "pci", "count": 2}],
            "agents": None}
    assert len(td.alert_table_rows(junk)) == 1
    assert len(td.policy_rows(junk)) == 1
    assert td.agent_rows(junk) == []


def test_avg_risk_tone_flips_at_forty_and_seventy():
    assert td.avg_risk_view({"avg_risk_score": 80})["tone"] == "bad"
    assert td.avg_risk_view({"avg_risk_score": 55})["tone"] == "warn"
    assert td.avg_risk_view({"avg_risk_score": 10})["tone"] == "ok"


def test_avg_risk_unknown_is_a_dash_not_a_zero():
    view = td.avg_risk_view({"avg_risk_score": None, "total_threats": 0})
    assert view["text"] == "—" and view["tone"] == "mute"
    assert view["note"] == "no breaches yet"


def test_avg_risk_note_counts_the_breaches_it_averaged():
    assert td.avg_risk_view({"avg_risk_score": 50,
                             "total_threats": 1})["note"] == "across 1 breach"


# ══ shaping: ledger ═════════════════════════════════════════════════════════
def test_enforcement_outcomes_outrank_the_judge_verdict():
    """A blocked call never reached a model. That is a stronger statement than
    "the judge found nothing wrong", so it is reported first and never green."""
    assert ld.verdict_of({"event_type": "blocked",
                          "gemini_verdict": {"policy_breach": True}}) == \
        ("blocked", "blue")
    assert ld.verdict_of({"event_type": "redacted"}) == ("redacted", "pink")


def test_evaluator_non_answers_are_unknown_not_clean():
    for reason in ("evaluator_unavailable: timeout", "evaluator_unknown"):
        assert ld.verdict_of({"grading_status": "graded",
                              "gemini_verdict": {"reason": reason}})[0] == "unknown"
    assert ld.verdict_of({"grading_status": "graded",
                          "gemini_verdict": {"decision": "unknown"}})[0] == "unknown"


def test_the_remaining_verdicts():
    assert ld.verdict_of({"grading_status": "graded",
                          "gemini_verdict": {"policy_breach": True}})[0] == "breach"
    assert ld.verdict_of({"grading_status": "graded"})[0] == "safe"
    assert ld.verdict_of({"grading_status": "failed"})[0] == "flag"
    assert ld.verdict_of({})[0] == "pending"
    assert ld.verdict_of("not a dict")[0] == "pending"


def test_clean_excludes_prevented_egress_and_non_answers():
    """Folding blocked/redacted/unknown into "clean" would report a safety
    result the system never reached (html:3641-3644)."""
    slices, total = ld.verdict_slices({
        "breaches": 2, "blocked": 3, "redacted": 1, "evaluator_unknown": 4,
        "grading": {"graded": 20, "pending": 5, "in_progress": 1, "failed": 2}})
    by = {s["label"]: s["value"] for s in slices}
    assert by["Clean"] == 20 - 2 - 3 - 1 - 4
    assert by["Pending"] == 6 and by["Failed"] == 2
    assert total == by["Clean"] + 2 + 3 + 1 + 4 + 6 + 2


def test_clean_never_goes_negative():
    slices, _ = ld.verdict_slices({"breaches": 99, "grading": {"graded": 1}})
    assert slices[0]["value"] == 0


def test_query_string_encodes_and_omits_blanks():
    assert ld.query_string(page=2, q="ab cd", verdict="breach") == \
        "?page=2&limit=50&q=ab%20cd&verdict=breach"
    assert ld.query_string() == "?page=1&limit=50"
    assert ld.query_string(page=0) == "?page=1&limit=50"      # clamped


def test_paging_maths_never_reports_page_one_of_zero():
    assert ld.max_page(0) == 1 and ld.page_label(1, 0) == "page 1 / 1"
    assert ld.max_page(50) == 1 and ld.max_page(51) == 2
    assert ld.count_label(1) == "1 record" and ld.count_label(1240) == "1,240 records"


def test_empty_message_separates_a_filter_miss_from_an_empty_ledger():
    assert "match" in ld.empty_message(0, 1, True)[0].lower()
    assert "yet" in ld.empty_message(0, 1, False)[0].lower()
    assert "page" in ld.empty_message(500, 9, False)[0].lower()


def test_table_row_carries_its_own_evidence():
    rows = ld.table_rows({"items": [{
        "seq": 7, "policy_tag": "pci", "agent": "bot",
        "chain_hash": "a" * 64, "grading_status": "graded",
        "gemini_verdict": {"policy_breach": True, "decision": "breach",
                           "risk_score": 88, "reason": "card echoed"},
        "pii_signals": ["credit_card"], "created_at": "2026-07-27T07:00:00Z"}]})
    d = rows[0]["detail"]
    assert (d["risk"], d["reason"], d["pii"]) == (88, "card echoed", "credit_card")
    assert d["chain_hash"] == "a" * 64          # the FULL hash, not the short one
    assert rows[0]["hash_short"] != d["chain_hash"]


def test_ungraded_row_says_awaiting_rather_than_no_issues():
    rows = ld.table_rows({"items": [{"seq": 1, "grading_status": "pending"}]})
    assert rows[0]["detail"]["reason"] == "awaiting grading"
    assert rows[0]["detail"]["pii"] == "none detected"


def test_verify_result_wording():
    assert ld.verify_result({"found": True, "verified": True})[1] == "ok"
    assert ld.verify_result({"found": True, "verified": False})[1] == "bad"
    assert ld.verify_result({"found": False})[1] == "bad"
    assert ld.verify_result(None)[1] == "warn"     # could not ask ≠ altered


# ══ the built pages ═════════════════════════════════════════════════════════
@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def console(app, tmp_path_factory):
    """Module-scoped: a full console is heavy, and one per test leaves enough
    windows and startup timers alive in a process to fault the event queue."""
    from PyQt6.QtCore import QSettings
    from fox_settings import FoxSettings
    from foxy_client import MemorySecretStore
    from dashboard import DashboardWindow

    path = tmp_path_factory.mktemp("d5") / "console.ini"
    store = QSettings(str(path), QSettings.Format.IniFormat)
    win = DashboardWindow(settings=FoxSettings(store, MemorySecretStore()))
    yield win
    win.close()


def _render(widget) -> QPixmap:
    pm = QPixmap(max(widget.width(), 320), max(widget.height(), 200))
    pm.fill()
    widget.render(pm)
    return pm


THREATS = {"total_threats": 6, "avg_risk_score": 74,
           "top_policies": [{"tag": "pci", "count": 4}],
           "recent_high_risk": [{"seq": 9, "policy_tag": "pci", "agent": "bot",
                                 "risk_score": 88, "reason": "leak",
                                 "timestamp": "2026-07-27T07:00:00Z"}]}
LOGS = {"total": 120, "items": [
    {"seq": 9, "policy_tag": "pci", "agent": "bot", "chain_hash": "a" * 64,
     "grading_status": "graded",
     "gemini_verdict": {"policy_breach": True, "decision": "breach",
                        "risk_score": 88, "reason": "card echoed"},
     "pii_signals": ["credit_card"], "created_at": "2026-07-27T07:00:00Z"}]}


def test_both_pages_build_every_panel(console):
    for attr in ("threat_stats", "threat_timeline", "threat_range_buttons",
                 "threat_table", "threat_activity", "threat_by_agent",
                 "avg_risk_gauge", "threat_policies", "threats_scroll",
                 "ledger_stats", "ledger_volume", "ledger_donut", "ledger_q",
                 "ledger_verdict", "ledger_chips", "ledger_rows",
                 "ledger_count", "ledger_page_lbl", "ledger_scroll"):
        assert hasattr(console, attr), attr


def test_threats_renders_with_data(console):
    console._on_threat_analytics(THREATS)
    console._on_threat_stats({"total_logged": 100, "breaches": 6,
                              "clean_rate": 94.0, "activity_7d": []})
    console._on_timeline({"days": [{"day": "2026-07-01", "high": 1,
                                    "medium": 0, "low": 0}]})
    console._on_by_agent({"agents": [{"agent": "bot", "count": 6}]})
    assert [l.text() for l in console.threat_stats] == ["100", "6", "6", "94%"]
    assert console.avg_risk_num.text() == "74"
    assert not _render(console.threats_scroll.widget()).isNull()


def test_a_failed_threats_fetch_never_reads_as_empty(console):
    from panel_state import ERROR_TITLE
    console._on_threat_analytics(THREATS)
    console._on_threat_analytics(None, ok=False)
    assert console.threat_table_empty.state() is PanelState.ERROR
    assert console.threat_table_empty.title.text() == ERROR_TITLE
    assert console.threat_table_empty.retry_btn.isVisibleTo(
        console.threat_table_empty)
    assert console.avg_risk_num.text() == "—"
    assert console.threat_timeline.o["empty"]["state"] != "empty"


def test_a_zero_data_org_is_empty_not_broken(console):
    console._on_threat_analytics({"total_threats": 0, "recent_high_risk": [],
                                  "top_policies": [], "avg_risk_score": None})
    assert console.threat_table_empty.state() is PanelState.EMPTY
    assert "No high-risk breaches" in console.threat_table_empty.title.text()
    assert not console.threat_table_empty.retry_btn.isVisibleTo(
        console.threat_table_empty)


def test_the_range_switcher_marks_exactly_one_option(console):
    console.set_threat_range(90)
    checked = [d for d, b in console.threat_range_buttons.items() if b.isChecked()]
    assert checked == [90] and console._threat_days == 90
    console.set_threat_range(999)                 # ignored, not crashed
    assert console._threat_days == 90
    console.set_threat_range(td.DEFAULT_RANGE)


def test_ledger_renders_rows_and_paging(console):
    console._on_ledger_rows(LOGS)
    assert console.ledger_count.text() == "120 records"
    assert console.ledger_page_lbl.text() == "page 1 / 3"
    assert not console.ledger_prev.isEnabled() and console.ledger_next.isEnabled()
    assert not _render(console.ledger_scroll.widget()).isNull()


def test_ledger_row_detail_opens_by_keyboard(console):
    """The web toggles on click only, which leaves a keyboard user unable to
    reach the evidence panel at all."""
    from ledger_page import LedgerRow
    console._on_ledger_rows(LOGS)
    rows = console.ledger_scroll.widget().findChildren(LedgerRow)
    assert rows, "expected a ledger row"
    row = rows[0]
    assert not row.is_expanded()
    row.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Space,
                                Qt.KeyboardModifier.NoModifier))
    assert row.is_expanded()
    assert "expanded" in row.accessibleName()
    row.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape,
                                Qt.KeyboardModifier.NoModifier))
    assert not row.is_expanded()


def test_a_failed_ledger_fetch_disables_paging_and_offers_retry(console):
    from panel_state import ERROR_TITLE
    console._on_ledger_rows(LOGS)
    console._on_ledger_rows(None, ok=False)
    assert console.ledger_empty.title.text() == ERROR_TITLE
    assert console.ledger_empty.retry_btn.isVisibleTo(console.ledger_empty)
    assert not console.ledger_prev.isEnabled()
    assert not console.ledger_next.isEnabled()
    assert console.ledger_count.text() == "—"


def test_an_empty_ledger_says_so_without_a_retry(console):
    console._on_ledger_rows({"total": 0, "items": []})
    assert console.ledger_empty.state() is PanelState.EMPTY
    assert not console.ledger_empty.retry_btn.isVisibleTo(console.ledger_empty)
    assert console.ledger_page_lbl.text() == "page 1 / 1"


def test_quick_chips_drive_the_verdict_filter(console):
    console.quick_ledger("breach")
    assert console.ledger_verdict.currentData() == "breach"
    assert console._ledger_page == 1
    console.clear_ledger_filters()
    assert console.ledger_verdict.currentData() == ""
    assert console.ledger_q.text() == "" and console.ledger_policy.text() == ""


def test_changing_a_filter_returns_to_page_one(console):
    """Staying on page 7 of a result set that no longer has seven pages shows
    an empty table and blames the data for it."""
    console._ledger_total = 500
    console._ledger_page = 7
    console.apply_ledger_filters()
    assert console._ledger_page == 1


def test_paging_is_clamped_to_the_real_range(console):
    console._ledger_total = 120           # 3 pages
    console._ledger_page = 1
    console.ledger_page(-1)
    assert console._ledger_page == 1      # never below 1
    console._ledger_page = 3
    console.ledger_page(1)
    assert console._ledger_page == 3      # never past the last


def test_neither_page_spawns_into_the_gating_set(console):
    """`_home_workers` is a gate: anything tracked in it can stop a refresh.
    D5's pages reload on navigation, so theirs are plain buckets."""
    import ast
    import inspect
    import textwrap
    import dashboard

    for name in ("refresh_threats", "refresh_ledger", "_load_timeline",
                 "_verify_record"):
        src = textwrap.dedent(inspect.getsource(
            getattr(dashboard.DashboardWindow, name)))
        for node in ast.walk(ast.parse(src)):
            if (isinstance(node, ast.Call)
                    and getattr(node.func, "id", "") == "spawn_worker"):
                tracks = [kw.value.attr for kw in node.keywords
                          if kw.arg == "track"
                          and isinstance(kw.value, ast.Attribute)]
                assert "_home_workers" not in tracks, name


def test_both_worker_sets_are_drained_on_close(console):
    import inspect
    import dashboard
    src = inspect.getsource(dashboard.DashboardWindow.closeEvent)
    assert "_threat_workers" in src and "_ledger_workers" in src


def test_neither_page_fetches_without_a_credential(console):
    console.show()
    console.settings.set_backend_url("http://127.0.0.1:9")
    try:
        console.refresh_threats()
        console.refresh_ledger()
        assert not console._threat_workers and not console._ledger_workers
    finally:
        console.settings.set_backend_url("")
        console.close()


# ── the cascade bug, pinned (TASK 007 advisory) ─────────────────────────────
#
# The flagship D5 fix shipped without a test: reverting all three
# `QWidget#pageBody` selectors to a bare `background: transparent;` left every
# relevant test passing. That exact bug had already shipped silently once in
# D4, where the Home trend switcher never showed which metric was selected.

def test_no_page_body_sets_a_selectorless_background():
    """A stylesheet with no selector applies to the widget AND every
    descendant, and as the closest ancestor sheet it outranks the shell's
    `QPushButton#segBtn:checked { background: fox }`. Every page body must
    scope its rule."""
    import re
    # Resolved against THIS file, not the working directory: CI runs
    # `python -m pytest desktop -q` from the repo root, where a bare
    # "home_page.py" does not exist. Running from inside desktop/ is what hid
    # it locally — the same shape as the pynput incident, where a green local
    # suite said nothing because the invocation differed from the runner's.
    for module in ("home_page.py", "threats_page.py", "ledger_page.py"):
        with open(_HERE / module, encoding="utf-8") as fh:
            source = fh.read()
        for call in re.findall(r"setStyleSheet\(\s*([\"'].*?[\"'])\s*\)", source):
            assert "{" in call, (
                f"{module}: selector-less stylesheet {call} — it will cascade "
                f"to every child widget")


@pytest.mark.parametrize("page,attr", [("threats", "threat_range_buttons"),
                                       ("home", "trend_buttons")])
def test_the_checked_segment_actually_paints_its_pill(console, page, attr):
    """The behavioural half: the selected option must be visibly selected.

    Measured rather than asserted structurally — this is what the eye sees,
    and the structural version of this bug passed review twice.
    """
    from collections import Counter
    from foxy_tokens import WEB

    console.show()
    console.go(page)
    app_ = QApplication.instance()
    app_.processEvents()
    buttons = getattr(console, attr)
    seg = next(iter(buttons.values())).parentWidget()
    try:
        image = seg.grab().toImage()
        counts = Counter(image.pixelColor(x, y).name()
                         for x in range(seg.width())
                         for y in range(seg.height()))
        assert counts.get(WEB["fox"].lower(), 0) > 100, (
            f"{page}: the checked segment painted "
            f"{counts.get(WEB['fox'].lower(), 0)} accent pixels — the pill is "
            f"missing, so nothing shows which option is active")
    finally:
        console.close()


def test_ledger_rows_show_a_focus_ring(console):
    """StrongFocus without a focus rule is a cursor the user cannot see."""
    from foxy_tokens import clay_tokens, console_shell_qss
    from ledger_page import LedgerRow

    console._on_ledger_rows(LOGS)
    rows = console.ledger_scroll.widget().findChildren(LedgerRow)
    assert rows, "expected a ledger row"
    assert rows[0].objectName() == "ledgerRow"
    assert "QFrame#ledgerRow:focus" in console_shell_qss(clay_tokens())
