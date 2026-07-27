"""Foxy Audit desktop — the Audit ledger page (D5).

The web's `#page-ledger` (foxy-audit-premium.html:1175-1233 + the loaders
listed in ledger_data.py), rebuilt with FoxChart and foxy_tokens.

The table is the product's whole claim made visible: every row is a commitment
in the hash chain, and its expanded detail carries the verdict, the reason, the
PII signals and the full chain hash — with a button that re-verifies that exact
record against the server. Nothing here is re-fetched on expand; a row shows
what the list returned, so what you read is what was recorded.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from charts import FoxChart
from console_chrome import local_time
from foxy_tokens import (
    BAD_RED, INFO_BLUE, OK_GREEN, RADIUS, WARN_AMBER, WEB, pick_font,
)
from home_page import _card, _label, _stat_tile, scroll_qss, seg_button, seg_container
from panel_state import PanelState, StatusStrip
import ledger_data as ld

#: Verdict → colour. `blocked` and `redacted` get their own hues rather than a
#: green/red split, because prevented egress is neither a pass nor a breach.
VERDICT_COLOURS = {"ok": OK_GREEN, "bad": BAD_RED, "warn": WARN_AMBER,
                   "blue": INFO_BLUE, "pink": "#ff9ec4", "mute": WEB["muted"]}


class LedgerSections:
    """Builds the Ledger page onto `owner` (the DashboardWindow)."""

    def __init__(self, owner):
        self.o = owner

    def build(self, t: dict, live_table: QWidget | None = None) -> QWidget:
        """`live_table` is built by the owner (it needs Card/AuditTable, which
        live in dashboard.py) and handed in — the same shape HomeSections uses
        for the pre-D4 overview, and it keeps this module free of a circular
        import."""
        o = self.o
        self._t = t
        self._live = live_table
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
        v.setSpacing(14)

        v.addWidget(self._head())
        v.addWidget(self._stats())
        v.addLayout(self._charts())
        v.addWidget(self._filters())
        v.addWidget(self._chips())
        v.addWidget(self._table())
        if live_table is not None:
            v.addWidget(live_table)
        v.addStretch()

        scroll.setWidget(body)
        outer.addWidget(scroll)
        o.ledger_scroll = scroll
        return page

    # ── head (html:1176-1180) ──
    def _head(self) -> QWidget:
        o = self.o
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        col = QVBoxLayout()
        col.setSpacing(3)
        col.addWidget(_label("● NO PROMPTS STORED", size=10, bold=True,
                             colour=WEB["fox2"], mono=True, spacing=1.5))
        col.addWidget(_label("Audit ledger.", size=26, bold=True))
        col.addWidget(_label(
            "Only the verdict + a tamper-proof fingerprint of each interaction "
            "— never the raw prompt or response.", size=12,
            colour=WEB["muted"], wrap=True))
        lay.addLayout(col, 1)
        o.ledger_refresh = QPushButton("↻ refresh")
        o.ledger_refresh.setObjectName("ghostBtn")
        o.ledger_refresh.setMinimumHeight(44)
        o.ledger_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        o.ledger_refresh.clicked.connect(lambda: o.apply_ledger_filters())
        lay.addWidget(o.ledger_refresh, 0, Qt.AlignmentFlag.AlignTop)
        return row

    # ── summary tiles (html:1181-1187) ──
    def _stats(self) -> QWidget:
        o = self.o
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        o.ledger_stats = []
        for label, value, tone in ld.summary_tiles(None):
            tile, value_lbl = _stat_tile(label, value, tone)
            o.ledger_stats.append(value_lbl)
            lay.addWidget(tile)
        return row

    # ── volume + verdict mix (html:1188-1197) ──
    def _charts(self) -> QHBoxLayout:
        o = self.o
        row = QHBoxLayout()
        row.setSpacing(14)

        card, lay = _card()
        head = QHBoxLayout()
        head.setSpacing(6)
        head.addWidget(_label("Volume", size=12, bold=True))
        head.addWidget(_label("· logged / day, last 90", size=9.5,
                              colour=WEB["muted"], mono=True))
        head.addStretch()
        lay.addLayout(head)
        o.ledger_volume = FoxChart("area", height=140, tone="fox")
        lay.addWidget(o.ledger_volume)
        row.addWidget(card, 3)

        card2, lay2 = _card()
        lay2.addWidget(_label("Verdict mix", size=12, bold=True))
        o.ledger_donut = FoxChart("donut", height=150)
        lay2.addWidget(o.ledger_donut)
        row.addWidget(card2, 2)
        return row

    # ── filter bar (html:1198-1204) ──
    def _filters(self) -> QWidget:
        o = self.o
        card = QFrame()
        card.setObjectName("clayCard")
        card.setStyleSheet(
            f"QFrame#clayCard {{ background: {WEB['surf']};"
            f" border: 2.5px solid {WEB['bc']};"
            f" border-radius: {RADIUS['lg']}px; }}")
        lay = QHBoxLayout(card)
        lay.setContentsMargins(16, 11, 16, 11)
        lay.setSpacing(8)

        o.ledger_q = _line_edit("Search hash or agent…", "Search hash or agent")
        # Without a floor the flexible field collapses under the fixed-width
        # ones beside it and the placeholder elides to "Sea…", which tells the
        # user nothing about what the box is for.
        o.ledger_q.setMinimumWidth(170)
        o.ledger_q.returnPressed.connect(lambda: o.apply_ledger_filters())
        lay.addWidget(o.ledger_q, 1)

        o.ledger_policy = _line_edit("policy tag", "Policy tag")
        o.ledger_policy.setFixedWidth(130)
        o.ledger_policy.returnPressed.connect(lambda: o.apply_ledger_filters())
        lay.addWidget(o.ledger_policy)

        o.ledger_verdict = QComboBox()
        o.ledger_verdict.setAccessibleName("Verdict filter")
        o.ledger_verdict.setFixedWidth(150)
        o.ledger_verdict.setMinimumHeight(44)
        o.ledger_verdict.setCursor(Qt.CursorShape.PointingHandCursor)
        for value, label in ld.VERDICT_OPTIONS:
            o.ledger_verdict.addItem(label, value)
        o.ledger_verdict.setStyleSheet(
            f"QComboBox {{ background: {WEB['surf2']}; color: {WEB['ink']};"
            f" border: 2.5px solid {WEB['bc']}; border-radius: {RADIUS['sm']}px;"
            f" padding: 6px 10px; font-family: '{pick_font('mono')}';"
            f" font-size: 11px; }}"
            f"QComboBox:focus {{ border-color: {WEB['fox']}; }}"
            f"QComboBox::drop-down {{ border: none; width: 18px; }}"
            f"QComboBox QAbstractItemView {{ background: {WEB['surf']};"
            f" color: {WEB['ink']}; border: 2px solid {WEB['bc']};"
            f" selection-background-color: {WEB['fox']};"
            f" selection-color: #1a0900; outline: none; }}")
        lay.addWidget(o.ledger_verdict)

        o.ledger_filter_btn = QPushButton("Filter")
        o.ledger_filter_btn.setObjectName("ctaBtn")
        o.ledger_filter_btn.setMinimumHeight(44)
        o.ledger_filter_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        o.ledger_filter_btn.clicked.connect(lambda: o.apply_ledger_filters())
        lay.addWidget(o.ledger_filter_btn)

        o.ledger_clear_btn = QPushButton("Clear")
        o.ledger_clear_btn.setObjectName("ghostBtn")
        o.ledger_clear_btn.setMinimumHeight(44)
        o.ledger_clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        o.ledger_clear_btn.clicked.connect(lambda: o.clear_ledger_filters())
        lay.addWidget(o.ledger_clear_btn)
        return card

    # ── quick chips (html:1205-1212) ──
    def _chips(self) -> QWidget:
        o = self.o
        wrap = QWidget()
        lay = QHBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        o.ledger_chip_seg, seg_lay, o.ledger_chip_group = seg_container()
        o.ledger_chips = {}
        for label, value in ld.QUICK_CHIPS:
            btn = seg_button(label, accessible=f"Show {label.lower()}")
            btn.setChecked(value == "")
            btn.clicked.connect(lambda _c=False, v=value: o.quick_ledger(v))
            o.ledger_chip_group.addButton(btn)
            o.ledger_chips[value] = btn
            seg_lay.addWidget(btn)
        lay.addWidget(o.ledger_chip_seg)
        lay.addStretch()
        return wrap

    # ── table + pagination (html:1213-1232) ──
    def _table(self) -> QWidget:
        o = self.o
        card, lay = _card()
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 9)
        header.setSpacing(10)
        for name, width in zip(ld.TABLE_COLUMNS, (None, 130, 92, 84)):
            cell = _label(name.upper(), size=9, bold=True, colour=WEB["muted"],
                          mono=True, spacing=1.0)
            if width:
                cell.setFixedWidth(width)
                header.addWidget(cell)
            else:
                header.addWidget(cell, 1)
        lay.addLayout(header)

        o.ledger_rows = QVBoxLayout()
        o.ledger_rows.setSpacing(0)
        o.ledger_empty = StatusStrip()
        o.ledger_empty.retry.connect(lambda: o.refresh_ledger())
        o.ledger_rows.addWidget(o.ledger_empty)
        lay.addLayout(o.ledger_rows)

        foot = QHBoxLayout()
        foot.setSpacing(8)
        o.ledger_count = _label("—", size=11, colour=WEB["muted"])
        foot.addWidget(o.ledger_count)
        foot.addStretch()
        o.ledger_prev = _ghost("← prev")
        o.ledger_prev.clicked.connect(lambda: o.ledger_page(-1))
        foot.addWidget(o.ledger_prev)
        o.ledger_page_lbl = _label("", size=10, colour=WEB["muted"], mono=True)
        foot.addWidget(o.ledger_page_lbl)
        o.ledger_next = _ghost("next →")
        o.ledger_next.clicked.connect(lambda: o.ledger_page(1))
        foot.addWidget(o.ledger_next)
        lay.addLayout(foot)
        return card



# ── small builders ──────────────────────────────────────────────────────────
def _line_edit(placeholder: str, accessible: str) -> QLineEdit:
    field = QLineEdit()
    field.setPlaceholderText(placeholder)
    # A placeholder vanishes on focus and is not a label.
    field.setAccessibleName(accessible)
    field.setMinimumHeight(44)
    field.setStyleSheet(
        f"QLineEdit {{ background: {WEB['surf2']}; color: {WEB['ink']};"
        f" border: 2.5px solid {WEB['bc']}; border-radius: {RADIUS['sm']}px;"
        f" padding: 6px 11px; font-family: '{pick_font('mono')}';"
        f" font-size: 11px; }}"
        f"QLineEdit:focus {{ border-color: {WEB['fox']}; }}")
    return field


def _ghost(text: str) -> QPushButton:
    btn = QPushButton(text)
    btn.setObjectName("ghostBtn")
    btn.setMinimumHeight(44)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    return btn


class LedgerRow(QFrame):
    """One ledger row plus its expandable detail.

    The web toggles a hidden <tr>; here the detail is a child widget that
    starts hidden. Click or Space/Enter expands it — the web is click-only,
    which leaves a keyboard user unable to open the evidence panel at all.
    """

    def __init__(self, row: dict, on_verify, parent=None):
        super().__init__(parent)
        self.row = row
        self._on_verify = on_verify
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self._summary())
        self.detail = self._detail()
        self.detail.hide()
        lay.addWidget(self.detail)
        self._announce()

    def _summary(self) -> QWidget:
        row = self.row
        frame = QWidget()
        lay = QHBoxLayout(frame)
        lay.setContentsMargins(0, 9, 0, 9)
        lay.setSpacing(10)
        colour = VERDICT_COLOURS[row["tone"]]

        tenant = QHBoxLayout()
        tenant.setSpacing(8)
        seq = _label(f"#{row['seq']}", size=9, bold=True, mono=True,
                     colour="#1a0900")
        seq.setAlignment(Qt.AlignmentFlag.AlignCenter)
        seq.setMinimumWidth(38)
        seq.setStyleSheet(seq.styleSheet() + f"background: {colour};"
                          f" border-radius: 6px; padding: 3px 5px;")
        tenant.addWidget(seq)
        name = row["policy"] + (f"  · {row['agent']}" if row["agent"] else "")
        tenant.addWidget(_label(name, size=11.5, bold=True), 1)
        lay.addLayout(tenant, 1)

        hash_lbl = _label(row["hash_short"], size=10, colour=WEB["muted"],
                          mono=True)
        hash_lbl.setFixedWidth(130)
        hash_lbl.setToolTip(row["chain_hash"])
        lay.addWidget(hash_lbl)

        pill = _label(row["verdict"], size=9.5, bold=True, mono=True,
                      colour="#1a0900")
        pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pill.setFixedWidth(92)
        pill.setStyleSheet(pill.styleSheet() + f"background: {colour};"
                           f" border-radius: 9px; padding: 3px 0;")
        lay.addWidget(pill)

        when = _label(local_time(row["when"]) if row["when"] else "",
                      size=10, colour=WEB["muted"], mono=True)
        when.setFixedWidth(84)
        lay.addWidget(when)
        return frame

    def _detail(self) -> QWidget:
        d = self.row["detail"]
        frame = QFrame()
        frame.setStyleSheet(
            f"QFrame {{ border: 2px dashed {WEB['bc']};"
            f" border-radius: 10px; background: transparent; }}")
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(13, 11, 13, 11)
        lay.setSpacing(4)

        verdict = d["verdict"]
        if d["decision"]:
            verdict += f" ({d['decision']})"
        if d["risk"] is not None:
            verdict += f" · risk {d['risk']}"
        for title, value in (("verdict", verdict), ("reason", d["reason"]),
                             ("PII signals", d["pii"]),
                             ("grading", _grading_line(d))):
            line = QHBoxLayout()
            line.setSpacing(6)
            line.addWidget(_label(f"{title}:", size=11, bold=True))
            line.addWidget(_label(value, size=11, colour=WEB["ink2"], wrap=True), 1)
            lay.addLayout(line)

        chain = _label(f"chain hash: {d['chain_hash']}", size=9.5,
                       colour=WEB["muted"], mono=True, wrap=True)
        lay.addWidget(chain)

        self.verify_btn = _ghost("verify this record")
        self.verify_btn.clicked.connect(self._verify)
        lay.addWidget(self.verify_btn, 0, Qt.AlignmentFlag.AlignLeft)
        return frame

    # ── expand / collapse ──
    def toggle(self, expand: bool | None = None):
        # isHidden(), not isVisible(): isVisible() is False whenever ANY
        # ancestor is hidden, so on a page the user has not navigated to yet
        # this would read "collapsed" for an already-open row and flip it the
        # wrong way. isHidden() asks what this widget was actually told to be.
        want = self.detail.isHidden() if expand is None else bool(expand)
        self.detail.setVisible(want)
        self._announce()

    def is_expanded(self) -> bool:
        return not self.detail.isHidden()

    def _announce(self):
        expanded = self.is_expanded()
        state = "expanded" if expanded else "collapsed"
        self.setAccessibleName(
            f"Record {self.row['seq']}, {self.row['verdict']}, {state}. "
            f"Activate to {'collapse' if expanded else 'expand'}.")

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.toggle()
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.toggle()
            return
        if e.key() == Qt.Key.Key_Escape and self.is_expanded():
            self.toggle(False)
            return
        super().keyPressEvent(e)

    def _verify(self):
        self.verify_btn.setEnabled(False)
        self.verify_btn.setText("verifying…")
        self._on_verify(self.row["detail"]["chain_hash"], self)

    def set_verify_result(self, text: str, tone: str):
        self.verify_btn.setEnabled(True)
        self.verify_btn.setText(text)
        colour = VERDICT_COLOURS.get(tone, WEB["ink"])
        self.verify_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {colour};"
            f" border: 2px solid {colour}; border-radius: {RADIUS['sm']}px;"
            f" padding: 6px 12px; font-family: '{pick_font('mono')}';"
            f" font-size: 10.5px; font-weight: 800; }}")


def _grading_line(d: dict) -> str:
    parts = [d["grading"]]
    if d["agent"]:
        parts.append(f"agent {d['agent']}")
    parts.append(f"policy {d['policy']}")
    return " · ".join(parts)
