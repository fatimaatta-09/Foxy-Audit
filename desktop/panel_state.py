"""Per-panel load state — the standing pattern for every page from D5 on.

**Owner decision, 2026-07-27:** a panel never shows a value we are not sure
about. No stale values, no "as of" timestamps. Each panel either shows what it
just measured, says it measured nothing, or says it could not ask — and when
it could not ask, it offers a retry.

This exists because of a real defect in D4. Every loader there keyed off
*truthiness*: a failed request called its handler with `None`, which is
indistinguishable from a successful empty response, so six Home panels said
"No usage yet" when nothing had ever been asked. The state is therefore
carried explicitly and never inferred from the shape of the data:

    LOADING  a request is in flight and we have nothing to show yet
    OK       the request answered and there is something to draw
    EMPTY    the request answered and there genuinely is nothing
    ERROR    the request did not answer; we know nothing, and say so

`EMPTY` is a measurement. `ERROR` is the absence of one. Collapsing them is the
no-fake-data rule violated in the one place it is easiest to miss.
"""

from __future__ import annotations

from enum import Enum

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from foxy_tokens import BAD_RED, RADIUS, WEB, paint_icon, pick_font


class PanelState(str, Enum):
    LOADING = "loading"
    OK = "ok"
    EMPTY = "empty"
    ERROR = "error"


#: What every surface says when the request itself failed. One string, so the
#: whole app fails in the same voice.
ERROR_TITLE = "Couldn't load"
ERROR_BODY = ("Foxy couldn't reach the backend. Nothing is claimed about this "
              "period.")
LOADING_TITLE = "Loading…"
LOADING_BODY = "Asking the backend."


def resolve(ok: bool, has_rows: bool) -> PanelState:
    """The only place a payload is turned into a state.

    Callers pass what they know — did the request answer, and did it carry
    anything — rather than handing over data for something else to guess at.
    """
    if not ok:
        return PanelState.ERROR
    return PanelState.OK if has_rows else PanelState.EMPTY


def message_for(state: PanelState, *, empty_title: str = "Nothing yet",
                empty_body: str = "", error_detail: str | None = None
                ) -> tuple[str, str]:
    """(title, body) for a non-OK state. OK panels draw their data instead."""
    if state is PanelState.ERROR:
        return ERROR_TITLE, (error_detail or ERROR_BODY)
    if state is PanelState.LOADING:
        return LOADING_TITLE, LOADING_BODY
    return empty_title, empty_body


def chart_empty(state: PanelState, *, empty_title: str = "Nothing yet",
                empty_body: str = "", quiet: bool = False) -> dict:
    """The `empty=` option a FoxChart should carry for `state`.

    Charts paint their own non-OK message, so they take the wording rather than
    a sibling widget. `quiet` suppresses it entirely — an inline strip such as
    the hero sparkline has no room for a titled message.
    """
    title, body = message_for(state, empty_title=empty_title,
                              empty_body=empty_body)
    return {"title": title, "desc": body, "quiet": quiet,
            "state": state.value}


def add_visible(layout, widget):
    """Add a row to a list layout and make it visible NOW.

    `QLayout.addWidget` does NOT show the child. Qt leaves it hidden until the
    layout next activates, which normally happens on the next event-loop pass
    and nobody notices. When a repaint lands first — a refill followed closely
    by a paint, a grab, or a resize — the panel draws as an empty card with
    every row present, hidden, at the default 640×480 geometry.

    Every list rebuild in the desktop goes through this or `fill_visible`;
    `test_row_lists.py` walks the source and fails if a new one does not.
    """
    layout.addWidget(widget)
    widget.show()
    return widget


def fill_visible(layout, widgets) -> int:
    """`add_visible` for a whole list, activating the layout once at the end."""
    count = 0
    for widget in widgets:
        add_visible(layout, widget)
        count += 1
    layout.activate()
    return count


def insert_visible(layout, index: int, widget):
    """`add_visible` for a positional insert — `insertWidget` hides too."""
    layout.insertWidget(index, widget)
    widget.show()
    return widget


def clear_rows(layout, *, start: int = 0, floor: int | None = None, keep=()):
    """Drop the current rows.

    `takeAt` removes the layout item but does NOT unparent the widget, so it
    keeps painting until it is hidden — `hide()` before `deleteLater()`, which
    only runs on the next event-loop pass.

    `start` is where to take from; `floor` is how many items to leave behind
    when that differs (the notifications panel takes from 0 but keeps a
    trailing footer).
    """
    stop = start if floor is None else floor
    while layout.count() > stop:
        item = layout.takeAt(start)
        widget = item.widget()
        if widget is not None and widget not in keep:
            widget.hide()
            widget.deleteLater()


class StatusStrip(QWidget):
    """What a panel shows instead of its content while it is not OK.

    Carries a Retry button in the ERROR state — an error with no way forward is
    a dead end, and the owner asked for "couldn't load + retry" specifically.
    """

    retry = pyqtSignal()

    def __init__(self, parent=None, *, compact: bool = False):
        super().__init__(parent)
        self._state = PanelState.LOADING
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 6 if compact else 14, 0, 6 if compact else 14)
        lay.setSpacing(6)

        self.title = QLabel(LOADING_TITLE)
        self.title.setWordWrap(True)
        self.body = QLabel(LOADING_BODY)
        self.body.setWordWrap(True)
        lay.addWidget(self.title)
        lay.addWidget(self.body)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        self.retry_btn = QPushButton("Try again")
        self.retry_btn.setObjectName("retryBtn")
        self.retry_btn.setMinimumHeight(44)
        self.retry_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.retry_btn.clicked.connect(self.retry)
        self.retry_btn.hide()
        row.addWidget(self.retry_btn)
        row.addStretch()
        lay.addLayout(row)
        self._restyle()

    def state(self) -> PanelState:
        return self._state

    def set_state(self, state: PanelState, *, empty_title: str = "Nothing yet",
                  empty_body: str = "", error_detail: str | None = None):
        self._state = state
        title, body = message_for(state, empty_title=empty_title,
                                  empty_body=empty_body,
                                  error_detail=error_detail)
        self.title.setText(title)
        self.body.setText(body)
        self.body.setVisible(bool(body))
        self.retry_btn.setVisible(state is PanelState.ERROR)
        # Qt has no aria-live; the accessible description is what a screen
        # reader reads when focus lands here, so it must carry the failure.
        self.setAccessibleName(title)
        self.setAccessibleDescription(f"{title}. {body}" if body else title)
        self._restyle()

    def _restyle(self):
        colour = BAD_RED if self._state is PanelState.ERROR else WEB["ink2"]
        self.title.setStyleSheet(
            f"color: {colour}; font-family: '{pick_font('disp')}';"
            f" font-size: 12px; font-weight: 800; background: transparent;")
        self.body.setStyleSheet(
            f"color: {WEB['muted']}; font-family: '{pick_font('mono')}';"
            f" font-size: 10.5px; background: transparent;")
        self.retry_btn.setStyleSheet(
            f"QPushButton#retryBtn {{ background: {WEB['surf2']};"
            f" color: {WEB['ink']}; border: 2px solid {WEB['bc']};"
            f" border-radius: {RADIUS['sm']}px; padding: 6px 14px;"
            f" font-family: '{pick_font('mono')}'; font-size: 10.5px;"
            f" font-weight: 800; }}"
            f"QPushButton#retryBtn:hover {{ border-color: {WEB['fox']}; }}"
            f"QPushButton#retryBtn:focus {{ border-color: {WEB['fox']};"
            f" background: {WEB['surf3']}; }}")


__all__ = ["PanelState", "StatusStrip", "add_visible", "chart_empty",
           "clear_rows", "fill_visible", "message_for", "resolve",
           "ERROR_TITLE", "ERROR_BODY", "LOADING_TITLE", "LOADING_BODY"]
