"""Foxy Audit desktop — the Threat analytics page (D5).

The web's `#page-analytics` (foxy-audit-premium.html:1118-1172 + the loaders
listed in threats_data.py), rebuilt with FoxChart and foxy_tokens.

Same shape as home_page: the builder attaches its widgets to the
DashboardWindow that owns them, so the console's existing wiring keeps
working. Shaping lives in `threats_data`; this file lays out and paints.

Every panel carries a `panel_state.PanelState` from the start rather than
inferring emptiness from truthy data — the D4 lesson, applied at build time
instead of retrofitted.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QSizePolicy, QVBoxLayout, QWidget,
)

from charts import FoxChart
from console_chrome import local_time
from foxy_tokens import BAD_RED, OK_GREEN, RADIUS, WARN_AMBER, WEB, pick_font
from home_page import (
    _card, _label, _section_title, _stat_tile, scroll_qss, seg_button,
    seg_container,
)
from panel_state import PanelState, StatusStrip
import threats_data as td


class ThreatsSections:
    """Builds the Threats page onto `owner` (the DashboardWindow)."""

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
        # Scoped to the body itself. A selector-LESS "background: transparent"
        # is applied by Qt to this widget AND every descendant, and being the
        # closest ancestor sheet it beat the shell's
        # `QPushButton#segBtn:checked { background: fox }` — so the active
        # segment of every segmented control rendered with no pill at all.
        # Measured: 108 orange pixels before, 697 after.
        body.setStyleSheet("QWidget#pageBody { background: transparent; }")
        v = QVBoxLayout(body)
        v.setContentsMargins(22, 18, 22, 22)
        v.setSpacing(16)

        v.addWidget(self._head())
        v.addWidget(self._stats())
        v.addWidget(self._timeline())
        v.addLayout(self._alerts_and_activity())
        v.addLayout(self._agents_and_risk())
        v.addWidget(self._policies())
        v.addStretch()

        scroll.setWidget(body)
        outer.addWidget(scroll)
        o.threats_scroll = scroll
        return page

    # ── head (web pg-head, html:1119-1121) ──
    def _head(self) -> QWidget:
        row = QWidget()
        lay = QVBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)
        lay.addWidget(_label("● SECURITY POSTURE", size=10, bold=True,
                             colour=WEB["fox2"], mono=True, spacing=1.5))
        lay.addWidget(_label("Threat analytics.", size=26, bold=True))
        lay.addWidget(_label("unresolved alerts and weekly trend", size=12,
                             colour=WEB["muted"]))
        return row

    # ── stat row (html:1122-1127) ──
    def _stats(self) -> QWidget:
        o = self.o
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        o.threat_stats = []
        for label, value, tone in td.stat_row(None, None):
            tile, value_lbl = _stat_tile(label, value, tone)
            o.threat_stats.append(value_lbl)
            lay.addWidget(tile)
        return row

    # ── breaches over time, stacked by risk band (html:1129-1143) ──
    def _timeline(self) -> QWidget:
        o = self.o
        card, lay = _card()
        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(_label("Breaches over time", size=12, bold=True))
        head.addWidget(_label("· by risk band", size=9.5, colour=WEB["muted"],
                              mono=True))
        head.addStretch()
        o.threat_range_seg, seg_lay, o.threat_range_group = seg_container()
        o.threat_range_buttons = {}
        for days in td.RANGES:
            btn = seg_button(f"{days}d", accessible=f"Show the last {days} days")
            btn.setChecked(days == td.DEFAULT_RANGE)
            btn.clicked.connect(lambda _c=False, d=days: o.set_threat_range(d))
            o.threat_range_group.addButton(btn)
            o.threat_range_buttons[days] = btn
            seg_lay.addWidget(btn)
        head.addWidget(o.threat_range_seg)
        lay.addLayout(head)

        o.threat_timeline = FoxChart("stacked", height=180)
        o.threat_timeline.setSizePolicy(QSizePolicy.Policy.Expanding,
                                        QSizePolicy.Policy.Fixed)
        lay.addWidget(o.threat_timeline)

        # The legend is static markup on the web (html:1142) — the bands never
        # change, so it does not wait on data.
        legend = QHBoxLayout()
        legend.setSpacing(14)
        for text, colour in (("High (≥70)", BAD_RED), ("Medium (40–69)", WARN_AMBER),
                             ("Low (<40)", WEB["muted2"])):
            chip = QHBoxLayout()
            chip.setSpacing(5)
            dot = QLabel()
            dot.setFixedSize(9, 9)
            dot.setStyleSheet(f"background: {colour}; border-radius: 2px;")
            chip.addWidget(dot)
            chip.addWidget(_label(text, size=9.5, colour=WEB["muted"], mono=True))
            legend.addLayout(chip)
        legend.addStretch()
        lay.addLayout(legend)
        return card

    # ── recent high-risk table + 7-day activity (html:1144-1157) ──
    def _alerts_and_activity(self) -> QHBoxLayout:
        o = self.o
        row = QHBoxLayout()
        row.setSpacing(16)

        left = QVBoxLayout()
        left.setSpacing(8)
        left.addWidget(_section_title("Recent high-risk")[0])
        card, lay = _card()
        o.threat_table = QVBoxLayout()
        o.threat_table.setSpacing(0)
        o.threat_table.addWidget(_table_header(td.ALERT_COLUMNS,
                                               (46, None, 110, 60, 96)))
        o.threat_rows_start = o.threat_table.count()
        o.threat_table_empty = StatusStrip()
        o.threat_table_empty.retry.connect(lambda: o.refresh_threats())
        o.threat_table.addWidget(o.threat_table_empty)
        lay.addLayout(o.threat_table)
        left.addWidget(card)
        row.addLayout(left, 3)

        right = QVBoxLayout()
        right.setSpacing(8)
        right.addWidget(_section_title("Activity — last 7 days")[0])
        card2, lay2 = _card()
        o.threat_activity = FoxChart("bar", height=120)
        lay2.addWidget(o.threat_activity)
        right.addWidget(card2)
        right.addStretch()
        row.addLayout(right, 2)
        return row

    # ── breaches by agent + average risk (html:1158-1170) ──
    def _agents_and_risk(self) -> QHBoxLayout:
        o = self.o
        row = QHBoxLayout()
        row.setSpacing(16)

        left = QVBoxLayout()
        left.setSpacing(8)
        left.addWidget(_section_title("Breaches by agent")[0])
        card, lay = _card()
        o.threat_by_agent = FoxChart("hbar", height=150)
        lay.addWidget(o.threat_by_agent)
        left.addWidget(card)
        row.addLayout(left, 1)

        right = QVBoxLayout()
        right.setSpacing(8)
        right.addWidget(_section_title("Average risk")[0])
        card2, lay2 = _card()
        inner = QHBoxLayout()
        inner.setSpacing(16)
        num_col = QVBoxLayout()
        num_col.setSpacing(4)
        o.avg_risk_num = _label("—", size=34, bold=True)
        num_col.addWidget(o.avg_risk_num)
        num_col.addWidget(_label("AVG BREACH RISK", size=9, colour=WEB["muted"],
                                 mono=True, spacing=0.6))
        inner.addLayout(num_col)
        gauge_col = QVBoxLayout()
        gauge_col.setSpacing(6)
        o.avg_risk_gauge = FoxChart("gauge", height=14)
        gauge_col.addWidget(o.avg_risk_gauge)
        o.avg_risk_note = _label("", size=10, colour=WEB["muted"], wrap=True)
        gauge_col.addWidget(o.avg_risk_note)
        inner.addLayout(gauge_col, 1)
        lay2.addLayout(inner)
        right.addWidget(card2)
        row.addLayout(right, 1)
        return row

    # ── top flagged policies (html:1171-1174) ──
    def _policies(self) -> QWidget:
        o = self.o
        wrap = QWidget()
        col = QVBoxLayout(wrap)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(8)
        head = QHBoxLayout()
        head.addWidget(_label("Top flagged policies", size=12, bold=True))
        head.addStretch()
        o.avg_risk_label = _label("", size=9.5, colour=WEB["muted"], mono=True)
        head.addWidget(o.avg_risk_label)
        head_w = QWidget()
        head_w.setLayout(head)
        col.addWidget(head_w)
        card, lay = _card()
        o.threat_policies = FoxChart("hbar", height=td.MIN_HBAR_PX)
        lay.addWidget(o.threat_policies)
        col.addWidget(card)
        return wrap


# ── row builders ────────────────────────────────────────────────────────────
def _table_header(columns, widths) -> QWidget:
    frame = QWidget()
    lay = QHBoxLayout(frame)
    lay.setContentsMargins(0, 0, 0, 9)
    lay.setSpacing(10)
    for name, width in zip(columns, widths):
        cell = _label(name.upper(), size=9, bold=True, colour=WEB["muted"],
                      mono=True, spacing=1.0)
        if width:
            cell.setFixedWidth(width)
            lay.addWidget(cell)
        else:
            lay.addWidget(cell, 1)
    return frame


def alert_table_row(row: dict) -> QWidget:
    """One line of "Recent high-risk" (web alertTableRow, html:3331-3340)."""
    frame = QFrame()
    lay = QHBoxLayout(frame)
    lay.setContentsMargins(0, 8, 0, 8)
    lay.setSpacing(10)
    tone = {"bad": BAD_RED, "warn": WARN_AMBER, "mute": WEB["muted"]}[row["tone"]]

    seq = _label(f"#{row['seq']}", size=10, bold=True, mono=True,
                 colour=WEB["ink2"])
    seq.setFixedWidth(46)
    lay.addWidget(seq)

    lay.addWidget(_label(row["policy"], size=11.5, bold=True), 1)

    agent = _label(row["agent"], size=10, colour=WEB["muted"], mono=True)
    agent.setFixedWidth(110)
    lay.addWidget(agent)

    # A badge, not bare text: risk is the column an auditor scans, and the
    # number is stated as well as coloured.
    risk = _label(str(row["risk"]), size=10.5, bold=True, mono=True,
                  colour="#1a0900" if row["tone"] != "mute" else WEB["ink"])
    risk.setAlignment(Qt.AlignmentFlag.AlignCenter)
    risk.setFixedWidth(60)
    risk.setStyleSheet(risk.styleSheet() + f"background: {tone};"
                       f" border-radius: {RADIUS['sm']}px; padding: 3px 0;")
    lay.addWidget(risk)

    when = _label(local_time(row["when"]) if row["when"] else "",
                  size=9.5, colour=WEB["muted"], mono=True)
    when.setFixedWidth(96)
    lay.addWidget(when)
    return frame
