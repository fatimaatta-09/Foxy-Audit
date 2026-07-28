"""Foxy Audit desktop — the fox's quick status popover (D12, plan §9.1).

A small frameless card the fox opens on middle-click and the tray opens on a
left click: chain state, capture credits, unread alerts, today's capture with
a 14-day sparkline, and the three buttons that take you to the console section
that can act on each.

Styled from `foxy_tokens` and drawn with `charts.FoxChart`, so it is the same
product as the console rather than a second look. Every line renders whatever
`companion_status` decided, including "not checked" and "couldn't ask" — the
panel has no way to invent a value because it is never handed one.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from charts import FoxChart
from foxy_tokens import BAD_RED, OK_GREEN, RADIUS, WARN_AMBER, WEB, pick_font
import companion_status as cs

TONES = {"ok": OK_GREEN, "bad": BAD_RED, "warn": WARN_AMBER,
         "mute": WEB["muted"]}

PANEL_WIDTH = 300

#: The sparkline's non-data options, re-supplied on every refresh because
#: `FoxChart.set_options` replaces rather than merges.
_SPARK = {"height": 34, "tone": "fox",
          "aria": "Events captured per day, last 14 days",
          "empty": {"title": "", "desc": "", "quiet": True}}


class QuickStatusPanel(QFrame):
    """The popover. `route_requested` carries a console section id."""

    route_requested = pyqtSignal(str)
    refresh_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Popup)
        self.setObjectName("quickPanel")
        self.setFixedWidth(PANEL_WIDTH)
        self.setStyleSheet(
            f"QFrame#quickPanel {{ background: {WEB['surf']};"
            f" border: 2.5px solid {WEB['bc']};"
            f" border-radius: {RADIUS['lg']}px; }}")
        self.setAccessibleName(cs.PANEL_TITLE)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(9)

        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(_text(cs.PANEL_TITLE, size=11.5, bold=True))
        head.addStretch()
        self.refresh_btn = QPushButton("↻")
        self.refresh_btn.setObjectName("panelRefresh")
        self.refresh_btn.setFixedSize(44, 44)
        self.refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_btn.setAccessibleName("Refresh the status panel")
        self.refresh_btn.setStyleSheet(
            f"QPushButton#panelRefresh {{ background: transparent;"
            f" color: {WEB['fox2']}; border: none; font-size: 15px; }}"
            f"QPushButton#panelRefresh:hover {{ color: {WEB['fox']}; }}"
            f"QPushButton#panelRefresh:focus {{ border: 1px solid {WEB['fox']};"
            f" border-radius: 8px; }}")
        self.refresh_btn.clicked.connect(self.refresh_requested)
        head.addWidget(self.refresh_btn)
        lay.addLayout(head)

        self.rows = {}
        for key, label in (("chain", "Chain"), ("credits", "Credits"),
                           ("alerts", "Alerts"), ("today", "Captured")):
            lay.addWidget(self._row(key, label))

        self.spark = FoxChart("sparkline", self, **_SPARK)
        lay.addWidget(self.spark)

        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        for label, section in cs.PANEL_ROUTES:
            btn = QPushButton(label)
            btn.setObjectName("panelBtn")
            btn.setMinimumHeight(44)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(
                f"QPushButton#panelBtn {{ background: {WEB['surf2']};"
                f" color: {WEB['ink']}; border: 2px solid {WEB['bc']};"
                f" border-radius: {RADIUS['sm']}px; padding: 5px 8px;"
                f" font-family: '{pick_font('mono')}'; font-size: 9.5px;"
                f" font-weight: 800; }}"
                f"QPushButton#panelBtn:hover {{ border-color: {WEB['fox']}; }}"
                f"QPushButton#panelBtn:focus {{ border-color: {WEB['fox']};"
                f" background: {WEB['surf3']}; }}")
            btn.clicked.connect(
                lambda _c=False, s=section: self.route_requested.emit(s))
            buttons.addWidget(btn)
        lay.addLayout(buttons)

        self.notice = _text("", size=10.5, colour=WEB["muted"], wrap=True)
        self.notice.hide()
        lay.addWidget(self.notice)
        self.set_view(cs.panel_view(loading=True))

    def _row(self, key: str, label: str) -> QWidget:
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(1)
        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(_text(label, size=9.5, mono=True, colour=WEB["muted"]))
        top.addStretch()
        value = _text("", size=11.5, bold=True)
        top.addWidget(value)
        lay.addLayout(top)
        note = _text("", size=9.5, mono=True, colour=WEB["muted"], wrap=True)
        lay.addWidget(note)
        self.rows[key] = (value, note, box)
        return box

    def set_view(self, view: dict):
        for key, (value, note, box) in self.rows.items():
            data = view.get(key) or cs.line(cs.LOADING, "…")
            value.setText(data["value"])
            value.setStyleSheet(_value_qss(TONES.get(data["tone"],
                                                     WEB["muted"])))
            note.setText(data["note"])
            note.setVisible(bool(data["note"]))
            # Qt has no aria-live; the accessible description is what a screen
            # reader gets, so it carries the state word, not just the number.
            box.setAccessibleDescription(
                f"{data['value']}. {data['note']}" if data["note"]
                else data["value"])
        points = view.get("spark") or []
        # The FULL bag every time: `set_options` REPLACES, so refreshing with
        # `data=` alone would drop `aria` and the chart would stop announcing
        # itself (charts.py:255-264, the D5 call-site rule).
        self.spark.set_options(data=points, **_SPARK)
        self.spark.setVisible(bool(points))
        self.adjustSize()

    def set_notice(self, title: str = "", body: str = ""):
        """Shown instead of nothing when the whole panel cannot apply — signed
        out, or no backend configured."""
        text = f"{title} — {body}" if title and body else (title or body)
        self.notice.setText(text)
        self.notice.setVisible(bool(text))
        self.adjustSize()

    def popup_near(self, widget):
        """Open beside the fox, kept fully on the screen it lives on.

        When the fox is hidden in the tray its geometry is still whatever it
        was, which would put the panel in dead space — so an invisible anchor
        falls back to the cursor, which is where the click came from.
        """
        from PyQt6.QtGui import QCursor
        from PyQt6.QtWidgets import QApplication
        self.adjustSize()
        screen = (widget.screen() or QApplication.primaryScreen()).geometry()
        if widget.isVisible():
            top_left = widget.mapToGlobal(widget.rect().topRight())
        else:
            top_left = QCursor.pos()
            screen = (QApplication.screenAt(top_left)
                      or QApplication.primaryScreen()).geometry()
        x = min(top_left.x() + 6, screen.right() - self.width() - 8)
        y = min(max(screen.top() + 8, top_left.y()),
                screen.bottom() - self.height() - 8)
        self.move(max(screen.left() + 8, x), y)
        self.show()
        self.raise_()


def _text(text: str, *, size: float = 11.0, bold: bool = False,
          colour: str | None = None, mono: bool = False,
          wrap: bool = False) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(wrap)
    label.setStyleSheet(
        f"color: {colour or WEB['ink']};"
        f" font-family: '{pick_font('mono' if mono else 'disp')}';"
        f" font-size: {size}px; font-weight: {'800' if bold else '400'};"
        f" background: transparent;")
    return label


def _value_qss(colour: str) -> str:
    return (f"color: {colour}; font-family: '{pick_font('disp')}';"
            f" font-size: 11.5px; font-weight: 800; background: transparent;")
