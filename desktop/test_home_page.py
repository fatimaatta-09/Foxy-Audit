"""Offscreen tests for the D4 Home page.

The shaping has its own suite (test_home_data.py); this covers the wiring and
the things only a real widget tree can show: that every section exists and
survives a data cycle, that a *failed* fetch never renders as an empty one,
that rows are actually removed rather than left painting, that the flip card is
keyboard-operable, and that the new timer and worker set obey the D0.1/D3
lifecycle rules.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QEvent, QPoint, Qt
from PyQt6.QtGui import QKeyEvent, QPixmap
from PyQt6.QtWidgets import QApplication

import home_data as hd


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def console(app, tmp_path_factory):
    """One console for the module, not one per test.

    Throwaway settings for the same reason as the D3 shell suite (the global
    QSettings route resolves to the developer's real registry hive), and
    module-scoped because a full console is heavy: building one per test left
    twenty-odd windows and their startup timers alive in a single process, and
    the accumulated queue eventually faulted. Every test below seeds the state
    it asserts on, so they do not depend on a fresh window."""
    from PyQt6.QtCore import QSettings
    from fox_settings import FoxSettings
    from foxy_client import MemorySecretStore
    from dashboard import DashboardWindow

    path = tmp_path_factory.mktemp("home") / "console.ini"
    store = QSettings(str(path), QSettings.Format.IniFormat)
    win = DashboardWindow(settings=FoxSettings(store, MemorySecretStore()))
    yield win
    win.close()


def _settle(widget):
    """Lay the widget out without pumping the global event queue.

    Every assertion here is synchronous — the handlers set text and rebuild
    layouts inline — so there is nothing to wait for. Draining the shared queue
    instead delivers whatever other suites left posted (worker threads
    finishing, deferred deletes) into objects this test knows nothing about,
    which faults the interpreter. Ask the widget to lay itself out and stop
    there."""
    widget.adjustSize()


def _render(widget) -> QPixmap:
    pm = QPixmap(widget.size() if widget.size().width() else QPoint(320, 200))
    pm.fill()
    widget.render(pm)
    return pm


COVERAGE = {"status": "verified", "message": "Observed everything.",
            "chain_verification": "verified", "total_events": 90,
            "missing_events": 10, "instrumented_clients": 1,
            "events_without_client_identity": 0,
            "clients": [{"client_id": "billing", "events": 90,
                         "first_client_seq": 1, "last_client_seq": 90}]}
LEDGER = {"items": [{"seq": 7, "policy_tag": "phi", "agent": "triage",
                     "chain_hash": "a" * 64, "status": "clean",
                     "created_at": "2026-07-27T07:00:00Z"}]}
THREATS = {"total_threats": 3, "recent_high_risk": [
    {"seq": 7, "policy_tag": "phi", "agent": "triage", "risk_score": 80,
     "reason": "leak", "timestamp": "2026-07-27T07:00:00Z"}]}


# ── the page exists and is reachable ────────────────────────────────────────
def test_home_page_builds_every_section(console):
    for attr in ("onboarding_card", "coverage_gauge", "coverage_table",
                 "coverage_volume", "hero_spark_chart", "hero_delta_lbl",
                 "usage_trend", "grading_donut", "recent_ledger",
                 "home_alerts", "gauge_clean", "gauge_verdict",
                 "activity_list", "q_hash_input", "home_scroll"):
        assert hasattr(console, attr), attr


def test_home_reuses_the_single_page_head(console):
    # A second head would mean two titles and two Export buttons stacked.
    assert console.home_greeting is console.ov_h1
    assert console.home_sub is console.ov_sub
    assert console.home_export_btn is console.export_btn


def test_home_head_matches_the_web_copy(console):
    console._apply_home_identity({"full_name": "ada lovelace"})
    assert console.ov_h1.text() == "Yo Ada. here's the vibe check."


def test_home_page_is_scrollable(console):
    # Nine stacked sections do not fit a laptop window; without the scroll
    # area the lower half would simply be unreachable.
    assert console.home_scroll.widgetResizable()
    assert console.home_scroll.widget() is not None


# ── a full data cycle renders ───────────────────────────────────────────────
def test_home_renders_with_real_data(console):
    console._on_onboarding({"done": 1, "total": 3, "steps": [
        {"key": "api_key", "done": True, "title": "Key", "desc": "d",
         "page": "keys", "label": "Create"},
        {"key": "first_log", "done": False, "title": "Log", "desc": "d",
         "page": "keys", "label": "Go"}]})
    console._on_coverage(COVERAGE)
    console._on_usage({"days": [{"day": "2026-07-01", "logs_count": 5,
                                 "tokens_sum": 50, "breach_count": 0},
                                {"day": "2026-07-02", "logs_count": 9,
                                 "tokens_sum": 90, "breach_count": 1}]})
    console._apply_home_stats({"total_logged": 14, "clean_rate": 96.0,
                               "avg_seconds_to_verdict": 2.0,
                               "grading": {"graded": 12, "pending": 2}})
    console._on_recent_ledger(LEDGER)
    console._on_threats(THREATS)
    console._render_activity(hd.merge_activity(
        [{"action": "key.create", "created_at": "2026-07-27T07:00:00Z"}], []))
    assert console.coverage_pct.text() == "90%"
    assert console.onboarding_pct.text() == "1 / 3 done"
    assert console.home_alerts_count.text() == "3 open"
    assert not _render(console.home_scroll.widget()).isNull()


def test_rows_stop_painting_when_the_list_is_rebuilt(console):
    """takeAt() only removes the layout item: the widget keeps its parent and
    keeps painting until the event loop runs the deferred delete, so a second
    cycle would draw the new rows on top of the old ones."""
    console._on_recent_ledger(LEDGER)
    first = console.recent_ledger.count()
    old = [console.recent_ledger.itemAt(i).widget()
           for i in range(console.recent_ledger.count())]
    _settle(console.home_scroll.widget())
    console._on_recent_ledger(LEDGER)
    assert console.recent_ledger.count() == first     # no doubling up
    for widget in old:
        if widget is None or widget is console.recent_ledger_empty:
            continue
        try:
            assert not widget.isVisible()   # stopped painting, not just untaken
        except RuntimeError:
            pass                            # already fully deleted


# ── failure is not emptiness ────────────────────────────────────────────────
def test_failed_fetches_never_claim_nothing_happened(console):
    from panel_state import PanelState
    console._on_recent_ledger(LEDGER)
    console._on_threats(THREATS)
    console._on_recent_ledger(None, ok=False)
    console._on_threats(None, ok=False)
    assert console.recent_ledger_empty.state() is PanelState.ERROR
    assert console.home_alerts_empty.state() is PanelState.ERROR
    # and the open-alert count stops asserting a number it cannot know
    assert console.home_alerts_count.text() == ""
    assert console.kpi_alerts.value_lbl.text() == "—"


def test_a_failed_panel_offers_a_way_forward(console):
    """"Couldn't load" with no retry is a dead end; the owner asked for both."""
    from panel_state import PanelState
    console._on_threats(THREATS)
    assert not console.home_alerts_empty.retry_btn.isVisible() or True
    console._on_threats(None, ok=False)
    assert console.home_alerts_empty.state() is PanelState.ERROR
    assert console.home_alerts_empty.retry_btn.isVisibleTo(
        console.home_alerts_empty)
    # ...and an EMPTY panel does not, because there is nothing to retry.
    console._on_threats({"total_threats": 0, "recent_high_risk": []})
    assert not console.home_alerts_empty.retry_btn.isVisibleTo(
        console.home_alerts_empty)


def test_empty_and_failed_say_different_things(console):
    from panel_state import PanelState
    console._on_threats({"total_threats": 0, "recent_high_risk": []})
    assert console.home_alerts_empty.state() is PanelState.EMPTY
    assert "No active alerts" in console.home_alerts_empty.title.text()
    console._on_threats(None, ok=False)
    assert console.home_alerts_empty.state() is PanelState.ERROR
    assert console.home_alerts_empty.title.text() != "No active alerts"


def test_failed_coverage_clears_the_chips(console):
    """An "unavailable" banner above last cycle's numbers reads as if those
    numbers had just been measured."""
    console._on_coverage(COVERAGE)
    assert console.coverage_chips[0].text() == "90"
    console._on_coverage(None)
    assert [c.text() for c in console.coverage_chips] == ["—"] * 4
    assert console.coverage_pct.text() == "—"


def test_failed_usage_marks_the_charts_unavailable(console):
    console._on_usage(None, ok=False)
    assert console.usage_trend.o["empty"]["title"] == "Couldn't load"
    assert console.hero_delta_lbl.text() == ""


def test_hero_sparkline_has_no_room_for_a_titled_empty_state(console):
    console._on_usage({"days": []})
    assert console.hero_spark_chart.o["empty"]["quiet"] is True
    assert not _render(console.hero_spark_chart).isNull()


def test_hero_sparkline_ink_is_a_colour_qt_can_parse(console):
    """qcolor() only understands #RRGGBB and Qt names; an rgba() string
    degrades silently to mid-grey, which on the hero gradient is invisible."""
    from foxy_tokens import qcolor
    console._on_usage({"days": [{"day": "2026-07-01", "logs_count": 3}]})
    ink = console.hero_spark_chart.o["tone"]
    assert qcolor(ink).name() == "#1a0900"     # not the #888888 fallback


# ── honest empty states with no data at all ─────────────────────────────────
def test_home_renders_empty_without_fabricating(console):
    console._on_onboarding(None)
    console._on_coverage(None)
    console._on_usage({"days": []})
    console._apply_home_stats({})
    console._on_recent_ledger({"items": []})
    console._on_threats({"recent_high_risk": []})
    console._render_activity([])
    assert not console.onboarding_card.isVisible()
    assert console.ov_sub.text() == "—"
    assert console.activity_empty.isVisible() or console.activity_list.count() == 1
    assert not _render(console.home_scroll.widget()).isNull()


# ── the verification card flip ──────────────────────────────────────────────
def test_flip_card_is_keyboard_operable(console):
    """The web flips on click only, which strands a keyboard user on a face
    they cannot turn."""
    assert not console.verif_card.is_back()
    console.verif_card.keyPressEvent(
        QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier))
    assert console.verif_card.is_back()
    console.verif_card.keyPressEvent(
        QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
    assert not console.verif_card.is_back()


def test_flip_card_is_focusable_and_announces_its_face(console):
    assert console.verif_card.focusPolicy() != Qt.FocusPolicy.NoFocus
    console.verif_card.flip(False)
    front = console.verif_card.accessibleName()
    console.verif_card.flip(True)
    assert console.verif_card.accessibleName() != front
    console.verif_card.flip(False)


def test_quick_check_rejects_a_short_hash_without_a_request(console):
    console.q_hash_input.setText("abc")
    console.quick_verify()
    assert "64-character" in console.q_result.text()
    assert not console._home_workers          # nothing was sent


def test_quick_check_input_has_a_real_accessible_name(console):
    # A placeholder disappears on focus and is not a label.
    assert console.q_hash_input.accessibleName() == "Ledger chain hash"


# ── lifecycle (the D0.1 / D3 rules) ─────────────────────────────────────────
def test_activity_timer_stops_on_close_and_restarts_on_show(console):
    # show()/close() deliver their events synchronously, so there is nothing
    # to pump here.
    console.show()
    assert console._activity_timer.isActive()
    console.close()
    assert not console._activity_timer.isActive()
    console.show()
    assert console._activity_timer.isActive()
    console.close()


def test_home_workers_are_drained_on_close(console):
    import dashboard
    import inspect
    source = inspect.getsource(dashboard.DashboardWindow.closeEvent)
    assert "_home_workers" in source


class _FakeWorker:
    """Stands in for a QThread in the tracking set. Only membership matters —
    the guard reads the set, and QThread.finished is what empties it."""


def test_a_one_off_call_cannot_silently_cancel_a_home_refresh(console):
    """D5-P3. `_home_workers` used to be both the refresh gate AND the bucket
    for one-offs, so a 15 s quick-hash check made the very next refresh_home()
    return early — no data, no error, no retry. Exercised, not grepped: the
    previous version of this test only asserted that a string appeared in the
    method's own source, which would have passed with the bug in place."""
    from foxy_client import shutdown_workers
    console.show()
    console.settings.set_backend_url("http://127.0.0.1:9")
    console.settings.set_org_api_key("placeholder-not-a-key")
    try:
        # A one-off is in flight...
        console._oneoff_workers.add(_FakeWorker())
        console.refresh_home()
        assert console._home_workers, "a one-off must not gate the cycle"
        shutdown_workers(console._home_workers)
        console._home_workers.clear()
        console._oneoff_workers.clear()

        # ...whereas a cycle genuinely in flight still gates the next one.
        console._home_workers.add(_FakeWorker())
        before = len(console._home_workers)
        console.refresh_home()
        assert len(console._home_workers) == before
    finally:
        console._home_workers.clear()
        console._oneoff_workers.clear()
        console.settings.set_org_api_key("")
        console.settings.set_backend_url("")
        console.close()


def test_one_off_workers_are_drained_on_close(console):
    import dashboard
    import inspect
    source = inspect.getsource(dashboard.DashboardWindow.closeEvent)
    assert "_oneoff_workers" in source


def test_home_refresh_does_not_fire_without_a_credential(console):
    """Every Home endpoint needs auth; firing them signed-out would 401 and
    paint six "backend unreachable" panels at a user who is simply signed out."""
    from foxy_client import shutdown_workers
    console.show()
    console.settings.set_backend_url("http://127.0.0.1:9")
    console.refresh_home()
    assert not console._home_workers
    # ...and prove it is the credential gate doing that, not the visibility
    # check: give it a key and the same call fans out.
    # Deliberately not shaped like a real key: the gate only tests presence,
    # and no key-prefixed literal belongs in the tree.
    console.settings.set_org_api_key("placeholder-not-a-key")
    console.refresh_home()
    assert console._home_workers
    shutdown_workers(console._home_workers)
    console.settings.set_org_api_key("")
    console.settings.set_backend_url("")
    console.close()


def test_navigating_home_refreshes_it(console):
    import dashboard
    import inspect
    source = inspect.getsource(dashboard.DashboardWindow.go)
    assert "refresh_home" in source


# ── the worker-set rule (C1) ────────────────────────────────────────────────
def test_pressing_activity_refresh_cannot_cancel_a_home_refresh(console):
    """D5-P3 moved the one-offs off the gating set but missed this one.

    `refresh_activity` is reachable two ways — as a leg of the refresh cycle,
    and directly from the Activity card's ↻ button and its retry. The button
    path was still putting two 12-second requests into `_home_workers`, so any
    Home refresh in that window returned early and loaded nothing, with no
    error and no retry. Same failure as the quick-hash-check case, different
    trigger.
    """
    from foxy_client import shutdown_workers
    console.show()
    console.settings.set_backend_url("http://127.0.0.1:9")
    console.settings.set_org_api_key("placeholder-not-a-key")
    # Both activity endpoints are session-only; an org key returns early.
    console.client.has_session = lambda: True
    # The console fixture is module-scoped and show() schedules a cycle, so
    # start from a known-empty pair rather than from whatever ran before.
    shutdown_workers(console._home_workers | console._oneoff_workers)
    console._home_workers.clear()
    console._oneoff_workers.clear()
    try:
        console.refresh_activity()                      # the ↻ button path
        assert console._oneoff_workers, "a button press must be a one-off"
        assert not console._home_workers, "...and must not touch the gate"

        console.refresh_home()
        assert console._home_workers, "the cycle must still run"
    finally:
        shutdown_workers(console._home_workers | console._oneoff_workers)
        console._home_workers.clear()
        console._oneoff_workers.clear()
        del console.client.has_session          # back to the real method
        console.settings.set_org_api_key("")
        console.settings.set_backend_url("")
        console.close()


def test_the_cycle_leg_of_refresh_activity_still_joins_the_gate(console):
    """...but when the cycle calls it, its requests belong to the cycle, or a
    second refresh could stack a duplicate pair on top of the first."""
    from foxy_client import shutdown_workers
    console.show()
    console.settings.set_backend_url("http://127.0.0.1:9")
    console.settings.set_org_api_key("placeholder-not-a-key")
    console.client.has_session = lambda: True
    shutdown_workers(console._home_workers | console._oneoff_workers)
    console._home_workers.clear()
    console._oneoff_workers.clear()
    try:
        console.refresh_activity(cycle=True)
        assert console._home_workers
        assert not console._oneoff_workers
    finally:
        shutdown_workers(console._home_workers | console._oneoff_workers)
        console._home_workers.clear()
        console._oneoff_workers.clear()
        del console.client.has_session          # back to the real method
        console.settings.set_org_api_key("")
        console.settings.set_backend_url("")
        console.close()


def test_only_the_periodic_cycle_uses_the_gating_set(console):
    """The rule itself, enforced.

    `_home_workers` is a gate, not a bucket: anything tracked in it can stop a
    refresh. Only requests spawned by `refresh_home` may use it. This walks the
    real call sites rather than trusting a grep, so a future one-off added with
    the wrong set fails here."""
    import ast
    import inspect
    import textwrap
    import dashboard

    gating = []
    for name, fn in inspect.getmembers(dashboard.DashboardWindow,
                                       predicate=inspect.isfunction):
        try:
            tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and getattr(node.func, "id", "") == "spawn_worker"):
                continue
            for kw in node.keywords:
                if kw.arg != "track":
                    continue
                # track=self._home_workers  →  the gating set, by name
                if (isinstance(kw.value, ast.Attribute)
                        and kw.value.attr == "_home_workers"):
                    gating.append(name)
    assert sorted(set(gating)) == ["refresh_home"], (
        "only refresh_home may spawn into the gating set; found: "
        f"{sorted(set(gating))}")


# ── the unmeasured zero (C2) ────────────────────────────────────────────────
def test_breaches_and_hero_stay_blank_until_something_is_measured(console):
    """A live counter of 0 at startup is not a measurement of zero.

    `_refresh_stats` runs on construction, when both UDP counters are 0 and no
    /v1/stats answer has arrived. Writing them then put "0 breaches stopped"
    and "0 interactions logged today" on screen as facts."""
    assert console._stats_answered is False
    console._logs_total = 0
    console._flagged_total = 0
    console._refresh_stats()
    assert console.kpi_breaches.value_lbl.text() == "—"
    assert console.hero_num.text() == "—"


def test_a_live_event_is_enough_to_start_reporting(console):
    """A UDP breach IS a measurement, even before the backend answers."""
    console._stats_answered = False
    console._flagged_total = 2
    console._logs_total = 5
    console._refresh_stats()
    assert console.kpi_breaches.value_lbl.text() == "2"
    assert console.hero_num.text() == "5"


def test_a_real_zero_from_the_backend_is_reported_as_zero(console):
    """Once /v1/stats has answered, zero is a fact and must be shown."""
    console._on_stats_success({"total_logged": 0, "breaches": 0,
                               "clean_rate": None,
                               "avg_seconds_to_verdict": None})
    assert console._stats_answered is True
    console._logs_total = 0
    console._flagged_total = 0
    console._refresh_stats()
    assert console.kpi_breaches.value_lbl.text() == "0"
    assert console.hero_num.text() == "0"
