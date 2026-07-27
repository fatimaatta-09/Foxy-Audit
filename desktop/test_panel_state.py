"""The per-panel state contract (owner decision, 2026-07-27).

A panel never shows a value we are not sure about. It either shows what it
just measured, says it measured nothing, or says it could not ask and offers a
retry. These tests pin the distinction that D4 got wrong: a failed request
called its handler with None, which was indistinguishable from a successful
empty response.
"""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QApplication

import panel_state
from panel_state import PanelState, StatusStrip, chart_empty, message_for, resolve


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


# ── resolve ─────────────────────────────────────────────────────────────────
def test_resolve_separates_measured_nothing_from_could_not_ask():
    assert resolve(True, False) is PanelState.EMPTY     # we looked, nothing there
    assert resolve(False, False) is PanelState.ERROR    # we never looked
    assert resolve(True, True) is PanelState.OK


def test_a_failed_request_is_an_error_even_if_it_carried_rows():
    """Stale rows from a previous cycle must not upgrade a failure to OK."""
    assert resolve(False, True) is PanelState.ERROR


# ── wording ─────────────────────────────────────────────────────────────────
def test_error_wording_is_the_same_everywhere():
    title, body = message_for(PanelState.ERROR)
    assert title == panel_state.ERROR_TITLE
    assert "couldn't reach" in body.lower()
    assert "nothing is claimed" in body.lower()


def test_empty_wording_is_the_callers_own():
    title, body = message_for(PanelState.EMPTY, empty_title="No usage yet",
                              empty_body="Appears as your SDK logs calls.")
    assert (title, body) == ("No usage yet", "Appears as your SDK logs calls.")


def test_error_never_borrows_the_empty_wording():
    title, _body = message_for(PanelState.ERROR, empty_title="No usage yet")
    assert title != "No usage yet"


# ── chart bag ───────────────────────────────────────────────────────────────
def test_chart_empty_carries_the_state_for_the_painter():
    bag = chart_empty(PanelState.ERROR)
    assert bag["state"] == "error" and bag["title"] == panel_state.ERROR_TITLE


def test_chart_empty_can_be_quiet_for_an_inline_strip():
    assert chart_empty(PanelState.EMPTY, quiet=True)["quiet"] is True


# ── the widget ──────────────────────────────────────────────────────────────
def test_retry_is_offered_only_where_there_is_something_to_retry(app):
    strip = StatusStrip()
    strip.set_state(PanelState.ERROR)
    assert strip.retry_btn.isVisibleTo(strip)
    for state in (PanelState.EMPTY, PanelState.LOADING, PanelState.OK):
        strip.set_state(state)
        assert not strip.retry_btn.isVisibleTo(strip), state


def test_retry_button_emits(app):
    strip = StatusStrip()
    seen = []
    strip.retry.connect(lambda: seen.append(1))
    strip.set_state(PanelState.ERROR)
    strip.retry_btn.click()
    assert seen == [1]


def test_the_failure_is_readable_without_looking_at_it(app):
    """Qt has no aria-live; the accessible description is what gets read."""
    strip = StatusStrip()
    strip.set_state(PanelState.ERROR)
    assert panel_state.ERROR_TITLE in strip.accessibleDescription()
    assert "nothing is claimed" in strip.accessibleDescription().lower()


def test_starts_in_loading_not_empty(app):
    """Before the first answer a panel knows nothing — and must not open by
    announcing that there is nothing."""
    assert StatusStrip().state() is PanelState.LOADING
