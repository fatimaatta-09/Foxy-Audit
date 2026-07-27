"""Foxy Audit desktop — the Export page (D8).

The web's `#page-export` (foxy-audit-premium.html:1384-1435 + the loaders in
export_data.py), rebuilt with foxy_tokens.

One deliberate divergence: the web triggers a browser download, the desktop
asks where to save with QFileDialog and then opens the file with the system
handler. Same endpoints and the same bytes — but a native app that wrote a file
somewhere without saying where would be worse than the browser, not better.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox, QDateEdit, QFrame, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from foxy_tokens import BAD_RED, OK_GREEN, RADIUS, WEB, pick_font
from home_page import _card, _label, scroll_qss, seg_button, seg_container
from panel_state import StatusStrip
import export_data as ed


class ExportSections:
    """Builds the Export page onto `owner` (the DashboardWindow)."""

    def __init__(self, owner):
        self.o = owner

    def build(self, t: dict) -> QWidget:
        o = self.o
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(scroll_qss())
        body = QWidget()
        body.setObjectName("pageBody")
        body.setStyleSheet("QWidget#pageBody { background: transparent; }")
        v = QVBoxLayout(body)
        v.setContentsMargins(22, 18, 22, 22)
        v.setSpacing(16)

        v.addWidget(self._head())
        v.addLayout(self._config_and_meta())
        v.addWidget(self._history())
        v.addStretch()

        scroll.setWidget(body)
        outer.addWidget(scroll)
        o.export_scroll = scroll
        return page

    def _head(self) -> QWidget:
        row = QWidget()
        lay = QVBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)
        lay.addWidget(_label("● EVIDENCE PACKAGE", size=10, bold=True,
                             colour=WEB["fox2"], mono=True, spacing=1.5))
        lay.addWidget(_label("Export.", size=26, bold=True))
        lay.addWidget(_label("A signed compliance passport, or the raw ledger "
                             "for your own tooling.", size=12,
                             colour=WEB["muted"], wrap=True))
        return row

    def _config_and_meta(self) -> QHBoxLayout:
        o = self.o
        row = QHBoxLayout()
        row.setSpacing(16)

        card, lay = _card()
        lay.addWidget(_label("Report configuration", size=12, bold=True))

        lay.addWidget(_field_label("Range"))
        o.exp_range_seg, seg_lay, o.exp_range_group = seg_container()
        o.exp_range_buttons = {}
        for label, days in ed.RANGE_PRESETS:
            btn = seg_button(label, accessible=f"Set the range to {label.lower()}")
            btn.clicked.connect(lambda _c=False, d=days: o.set_export_range(d))
            o.exp_range_group.addButton(btn)
            o.exp_range_buttons[days] = btn
            seg_lay.addWidget(btn)
        lay.addWidget(o.exp_range_seg)

        dates = QHBoxLayout()
        dates.setSpacing(10)
        for name, attr in (("from", "exp_from"), ("to", "exp_to")):
            col = QVBoxLayout()
            col.setSpacing(4)
            col.addWidget(_field_label(name))
            edit = QDateEdit()
            edit.setCalendarPopup(True)
            edit.setDisplayFormat("yyyy-MM-dd")
            edit.setMinimumHeight(44)
            edit.setAccessibleName(f"Export range {name}")
            edit.setStyleSheet(_date_qss())
            setattr(o, attr, edit)
            col.addWidget(edit)
            dates.addLayout(col, 1)
        lay.addLayout(dates)
        # "All time" has no start date; a picker cannot express that, so a
        # checkbox-free explicit control does.
        o.exp_all_time = QPushButton("no start date (all time)")
        # segBtn, not ghostBtn: a checkable ghost button renders the same
        # whether or not it is checked, so "all time" looked permanently
        # pressed. The seg treatment makes checked mean something.
        o.exp_all_time.setObjectName("segBtn")
        o.exp_all_time.setCheckable(True)
        o.exp_all_time.setMinimumHeight(44)
        o.exp_all_time.setCursor(Qt.CursorShape.PointingHandCursor)
        o.exp_all_time.toggled.connect(lambda on: o.exp_from.setDisabled(on))
        lay.addWidget(o.exp_all_time)

        lay.addWidget(_field_label("What to export"))
        o.exp_type = QComboBox()
        o.exp_type.setAccessibleName("Export type")
        o.exp_type.setMinimumHeight(44)
        o.exp_type.setCursor(Qt.CursorShape.PointingHandCursor)
        for value, label in ed.EXPORT_TYPES:
            o.exp_type.addItem(label, value)
        o.exp_type.setStyleSheet(_combo_qss())
        lay.addWidget(o.exp_type)

        o.exp_run = QPushButton("Generate export")
        o.exp_run.setObjectName("ctaBtn")
        o.exp_run.setMinimumHeight(44)
        o.exp_run.setCursor(Qt.CursorShape.PointingHandCursor)
        o.exp_run.clicked.connect(lambda: o.run_export())
        lay.addWidget(o.exp_run)

        o.exp_progress = ProgressStrip()
        lay.addWidget(o.exp_progress)
        row.addWidget(card, 3)

        card2, lay2 = _card()
        lay2.addWidget(_label("Chain metadata", size=12, bold=True))
        o.exp_meta = {}
        for key, label in (("records", "records"),
                           ("integrity", "chain integrity"),
                           ("head", "chain head"), ("org", "org id"),
                           ("sla", "anchor SLA")):
            line = QHBoxLayout()
            line.setSpacing(8)
            line.addWidget(_label(label, size=10.5, colour=WEB["muted"]))
            line.addStretch()
            value = _label("—", size=11, bold=True,
                           mono=key in ("head", "org"))
            if key in ("head", "org"):
                # A revealed chain head is 64 unbroken characters and pushed
                # this card past its column, squeezing the one beside it — the
                # masked form fits, so nothing showed it until the preference
                # could start revealed. Wrap inside the card instead.
                value.setWordWrap(True)
                value.setAlignment(Qt.AlignmentFlag.AlignRight
                                   | Qt.AlignmentFlag.AlignVCenter)
                # A 64-character hash has no break opportunity, so the label's
                # minimum size hint is the whole string and it drags the card
                # wider than its column no matter the stretch factors. Ignored
                # lets the layout size it down; word wrap then splits it.
                value.setSizePolicy(QSizePolicy.Policy.Ignored,
                                    QSizePolicy.Policy.Preferred)
            o.exp_meta[key] = value
            # Only the wrapping values take the spare width; the short ones
            # stay hard against the right edge like every other stat line.
            line.addWidget(value, 1 if key in ("head", "org") else 0)
            lay2.addLayout(line)

        # The head and org id identify a workspace and this console is often on
        # screen while someone shares it, so they are masked by default and
        # revealed deliberately.
        o.exp_reveal = QPushButton("reveal")
        # Checkable → segBtn, for the same reason `exp_all_time` is one: a
        # checkable ghostBtn has no :checked rule, so Qt falls back to the
        # native "on" look — which rendered as a bright cyan button that
        # belongs to no palette in this app. Caught by rendering it checked.
        o.exp_reveal.setObjectName("segBtn")
        o.exp_reveal.setCheckable(True)
        o.exp_reveal.setMinimumHeight(44)
        o.exp_reveal.setCursor(Qt.CursorShape.PointingHandCursor)
        o.exp_reveal.toggled.connect(o.on_reveal_metadata)
        o.exp_copy = QPushButton("copy head")
        o.exp_copy.setObjectName("ghostBtn")
        o.exp_copy.setMinimumHeight(44)
        o.exp_copy.setCursor(Qt.CursorShape.PointingHandCursor)
        o.exp_copy.clicked.connect(lambda: o.copy_chain_head())
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addWidget(o.exp_reveal)
        buttons.addWidget(o.exp_copy)
        buttons.addStretch()
        lay2.addLayout(buttons)
        lay2.addStretch()
        row.addWidget(card2, 2)
        return row

    def _history(self) -> QWidget:
        o = self.o
        card, lay = _card()
        lay.addWidget(_label("Export history", size=12, bold=True))
        header = QHBoxLayout()
        header.setContentsMargins(0, 6, 0, 8)
        header.setSpacing(10)
        for name, width in zip(ed.HISTORY_COLUMNS, (None, 190, 160, 130)):
            cell = _label(name.upper(), size=9, bold=True, colour=WEB["muted"],
                          mono=True, spacing=1.0)
            if width:
                cell.setFixedWidth(width)
                header.addWidget(cell)
            else:
                header.addWidget(cell, 1)
        lay.addLayout(header)
        o.exp_history = QVBoxLayout()
        o.exp_history.setSpacing(0)
        o.exp_history_empty = StatusStrip()
        o.exp_history_empty.retry.connect(lambda: o.refresh_exports())
        o.exp_history.addWidget(o.exp_history_empty)
        lay.addLayout(o.exp_history)
        return card


# ── pieces ──────────────────────────────────────────────────────────────────
def _field_label(text: str) -> QLabel:
    return _label(text.upper(), size=9, bold=True, colour=WEB["muted"],
                  mono=True, spacing=0.8)


def _date_qss() -> str:
    return (f"QDateEdit {{ background: {WEB['surf2']}; color: {WEB['ink']};"
            f" border: 2.5px solid {WEB['bc']}; border-radius: {RADIUS['sm']}px;"
            f" padding: 6px 10px; font-family: '{pick_font('mono')}';"
            f" font-size: 11px; }}"
            f"QDateEdit:focus {{ border-color: {WEB['fox']}; }}"
            f"QDateEdit:disabled {{ color: {WEB['muted2']}; }}"
            f"QDateEdit::drop-down {{ border: none; width: 18px; }}")


def _combo_qss() -> str:
    return (f"QComboBox {{ background: {WEB['surf2']}; color: {WEB['ink']};"
            f" border: 2.5px solid {WEB['bc']}; border-radius: {RADIUS['sm']}px;"
            f" padding: 6px 10px; font-family: '{pick_font('mono')}';"
            f" font-size: 11px; }}"
            f"QComboBox:focus {{ border-color: {WEB['fox']}; }}"
            f"QComboBox::drop-down {{ border: none; width: 18px; }}"
            f"QComboBox QAbstractItemView {{ background: {WEB['surf']};"
            f" color: {WEB['ink']}; border: 2px solid {WEB['bc']};"
            f" selection-background-color: {WEB['fox']};"
            f" selection-color: #1a0900; outline: none; }}")


class ProgressStrip(QWidget):
    """The web's pbar/plbl.

    Each step is set when that step actually begins — no timer pretending to be
    progress. A fake bar on an export is worse than none: it invites the user
    to close the window at 90%.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 8, 0, 0)
        lay.setSpacing(6)
        self.track = QFrame()
        self.track.setFixedHeight(8)
        self.track.setStyleSheet(
            f"background: {WEB['surf3']}; border-radius: 4px;")
        fill_lay = QHBoxLayout(self.track)
        fill_lay.setContentsMargins(0, 0, 0, 0)
        self.fill = QFrame()
        self.fill.setStyleSheet(
            f"background: {WEB['fox']}; border-radius: 4px;")
        fill_lay.addWidget(self.fill)
        fill_lay.addStretch()
        lay.addWidget(self.track)
        self.label = _label("", size=10.5, colour=WEB["muted"], mono=True,
                            wrap=True)
        lay.addWidget(self.label)
        self.hide()

    def set_stage(self, stage: str):
        percent, text = ed.progress_for(stage)
        self.set_progress(percent, text)

    def set_progress(self, percent: int, text: str, *, tone: str = "fox"):
        width = max(0, min(100, int(percent)))
        self.fill.setFixedWidth(int(self.track.width() * width / 100)
                                if self.track.width() else 0)
        colour = {"ok": OK_GREEN, "bad": BAD_RED}.get(tone, WEB["fox"])
        self.fill.setStyleSheet(
            f"background: {colour}; border-radius: 4px;")
        self.label.setText(text)
        self.setAccessibleName(text)
        self.show()

    def fail(self, text: str):
        self.set_progress(100, text, tone="bad")

    def finish(self, text: str):
        self.set_progress(100, text, tone="ok")


def history_row(row: dict) -> QWidget:
    from console_chrome import local_datetime
    frame = QFrame()
    lay = QHBoxLayout(frame)
    lay.setContentsMargins(0, 8, 0, 8)
    lay.setSpacing(10)
    lay.addWidget(_label(row["type"], size=11.5, bold=True), 1)
    rng = _label(row["range"], size=10, colour=WEB["muted"], mono=True)
    rng.setFixedWidth(190)
    lay.addWidget(rng)
    by = _label(row["by"], size=10, colour=WEB["muted"], mono=True)
    by.setFixedWidth(160)
    lay.addWidget(by)
    when = _label(local_datetime(row["when"]) if row["when"] else "",
                  size=10, colour=WEB["muted"], mono=True)
    when.setFixedWidth(130)
    lay.addWidget(when)
    return frame
