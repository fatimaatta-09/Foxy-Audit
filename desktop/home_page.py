"""Foxy Audit desktop — the Home page sections (D4).

The web's `#page-dashboard` (foxy-audit-premium.html:925-1115 + the loaders
listed in home_data.py), rebuilt with FoxChart and foxy_tokens.

The builder attaches its widgets to the DashboardWindow that owns them, so the
console's existing wiring (stats/verify pollers, theme re-apply, the D3 shell)
keeps working untouched — Home grew, it wasn't replaced. Data shaping lives in
`home_data`; this file only lays out and paints.

Order matches the web: head · onboarding · capture coverage · hero + tiles ·
stat row · usage trend + grading donut · verification/quick-check + recent
ledger · active alerts + gauges + quick actions · recent activity.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QFontMetrics
from PyQt6.QtWidgets import (
    QButtonGroup, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton,
    QScrollArea, QSizePolicy, QStackedLayout, QVBoxLayout, QWidget,
)

from charts import FoxChart
from console_chrome import local_datetime, local_time, relative_time
from foxy_tokens import BAD_RED, OK_GREEN, RADIUS, WARN_AMBER, WEB, pick_font
from panel_state import PanelState, StatusStrip
import home_data as hd


def _label(text: str, *, size: float = 12.0, bold: bool = False,
           colour: str | None = None, mono: bool = False, wrap: bool = False,
           spacing: float = 0.0) -> QLabel:
    lbl = QLabel(text)
    lbl.setWordWrap(wrap)
    family = pick_font("mono" if mono else "disp")
    weight = "800" if bold else "400"
    track = f" letter-spacing: {spacing}px;" if spacing else ""
    lbl.setStyleSheet(
        f"color: {colour or WEB['ink']}; font-family: '{family}';"
        f" font-size: {size}px; font-weight: {weight};{track}"
        f" background: transparent;")
    return lbl


def _elide_to_width(label: QLabel, text: str):
    """Keep `text` readable in a narrow, stretch-sized column.

    An `Ignored` size policy lets QLabel shrink below its hint, but it then
    hard-clips mid-character with no ellipsis and no way to read the rest.
    Eliding marks the truncation; the tooltip recovers the full value."""
    def _apply(width: int):
        if width > 0:
            label.setText(QFontMetrics(label.font()).elidedText(
                text, Qt.TextElideMode.ElideRight, width))

    label.setText(text)
    label.setToolTip(text)
    original_resize = label.resizeEvent

    def _on_resize(event):
        original_resize(event)
        _apply(event.size().width())

    label.resizeEvent = _on_resize


def _card(*, padded: bool = True) -> tuple[QFrame, QVBoxLayout]:
    """The web's `.clay pad` surface."""
    frame = QFrame()
    frame.setObjectName("clayCard")
    frame.setStyleSheet(
        f"QFrame#clayCard {{ background: {WEB['surf']};"
        f" border: 2.5px solid {WEB['bc']}; border-radius: {RADIUS['lg']}px; }}")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(*(20, 18, 20, 18) if padded else (0, 0, 0, 0))
    lay.setSpacing(10)
    return frame, lay


def _section_title(text: str, action: str | None = None) -> tuple[QWidget, QPushButton | None]:
    row = QWidget()
    lay = QHBoxLayout(row)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(8)
    lay.addWidget(_label(text, size=12, bold=True))
    lay.addStretch()
    btn = None
    if action:
        btn = QPushButton(action)
        btn.setObjectName("linkAct")
        btn.setMinimumHeight(44)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(
            f"QPushButton#linkAct {{ background: transparent; color: {WEB['fox2']};"
            f" border: none; font-family: '{pick_font('mono')}'; font-size: 9.5px;"
            f" font-weight: 700; padding: 4px 6px; }}"
            f"QPushButton#linkAct:hover {{ color: {WEB['fox']}; }}"
            f"QPushButton#linkAct:focus {{ border: 1px solid {WEB['fox']};"
            f" border-radius: 6px; }}")
        lay.addWidget(btn)
    return row, btn


def scroll_qss() -> str:
    """The console's scroll-area skin. Shared so every long page scrolls the
    same way rather than each one restating it."""
    return (
        f"QScrollArea {{ background: transparent; border: none; }}"
        f"QScrollBar:vertical {{ width: 8px; background: transparent;"
        f" margin: 6px 2px; }}"
        f"QScrollBar::handle:vertical {{ background: {WEB['surf3']};"
        f" border-radius: 4px; min-height: 30px; }}"
        f"QScrollBar::handle:vertical:hover {{ background: {WEB['muted2']}; }}"
        f"QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical"
        f" {{ height: 0; }}")


def seg_container() -> tuple[QWidget, QHBoxLayout, QButtonGroup]:
    """The web's `.seg` pill (html:186-192): one container, exclusive buttons."""
    seg = QWidget()
    seg.setObjectName("seg")
    lay = QHBoxLayout(seg)
    lay.setContentsMargins(3, 3, 3, 3)
    lay.setSpacing(2)
    group = QButtonGroup(seg)
    group.setExclusive(True)
    return seg, lay, group


def seg_button(text: str, *, accessible: str | None = None) -> QPushButton:
    """One button inside a `.seg`. The checked pill is the only visual cue for
    which option is live, so the accessible name spells it out."""
    btn = QPushButton(text.upper())          # .seg uppercases in CSS
    btn.setObjectName("segBtn")
    btn.setCheckable(True)
    btn.setMinimumHeight(30)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setAccessibleName(accessible or f"Show {text}")
    return btn


def _stat_tile(label: str, value: str = "—", tone: str | None = None) -> tuple[QFrame, QLabel]:
    frame = QFrame()
    frame.setObjectName("statTile")
    frame.setStyleSheet(
        f"QFrame#statTile {{ background: {WEB['surf2']};"
        f" border: 2px solid {WEB['bc']}; border-radius: {RADIUS['sm']}px; }}")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(13, 11, 13, 11)
    lay.setSpacing(3)
    colour = {"fox": WEB["fox"], "bad": BAD_RED, "ok": OK_GREEN,
              "warn": WARN_AMBER}.get(tone or "", WEB["ink"])
    value_lbl = _label(value, size=20, bold=True, colour=colour)
    lay.addWidget(value_lbl)
    # ink2, not muted: muted #8c8174 on the #221d18 tile is 4.38:1, under the
    # 4.5:1 floor at 8.5px. ink2 #cdc2b3 is 9.52:1 on the same surface. The WEB
    # dict is a byte-for-byte port of the site's :root and must not drift, so
    # the fix is which token this picks — not what the token holds.
    lay.addWidget(_label(label.upper(), size=8.5, bold=True, colour=WEB["ink2"],
                         mono=True, spacing=0.5))
    return frame, value_lbl


class FlipCard(QFrame):
    """The web's verification card: a front face that flips to a quick ledger
    check (html:1058-1078).

    The web flips on click only. A card you can reach with Tab but cannot turn
    with the keyboard is a dead end, so this one also answers Space and Enter
    and announces which face is showing."""

    flipped = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._stack = QStackedLayout(self)
        self._stack.setContentsMargins(0, 0, 0, 0)

    def add_faces(self, front: QWidget, back: QWidget):
        self._stack.addWidget(front)
        self._stack.addWidget(back)
        self._announce()

    def is_back(self) -> bool:
        return self._stack.currentIndex() == 1

    def flip(self, to_back: bool | None = None):
        if self._stack.count() < 2:
            return
        want = (not self.is_back()) if to_back is None else bool(to_back)
        self._stack.setCurrentIndex(1 if want else 0)
        self._announce()
        self.flipped.emit(want)

    def _announce(self):
        self.setAccessibleName(
            "Quick ledger check" if self.is_back() else
            "Verification card — activate to check a ledger hash")

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.flip()
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.flip()
            return
        if e.key() == Qt.Key.Key_Escape and self.is_back():
            self.flip(False)
            return
        super().keyPressEvent(e)


def quick_check_face(owner) -> QWidget:
    """Back face of the verification card — paste a chain hash, get a verdict."""
    face = QWidget()
    lay = QVBoxLayout(face)
    lay.setContentsMargins(22, 20, 22, 20)
    lay.setSpacing(9)
    lay.addWidget(_label("Quick ledger check", size=13, bold=True))
    owner.q_hash_input = QLineEdit()
    owner.q_hash_input.setPlaceholderText("Paste a ledger hash…")
    # A placeholder is not a label: screen readers get the real name here.
    owner.q_hash_input.setAccessibleName("Ledger chain hash")
    owner.q_hash_input.setMinimumHeight(44)
    owner.q_hash_input.setStyleSheet(
        f"QLineEdit {{ background: {WEB['surf']}; color: {WEB['ink']};"
        f" border: 2.5px solid {WEB['bc']}; border-radius: {RADIUS['sm']}px;"
        f" padding: 8px 11px; font-family: '{pick_font('mono')}';"
        f" font-size: 11px; }}"
        f"QLineEdit:focus {{ border-color: {WEB['fox']}; }}")
    # Clicks inside the input must not reach the card, or typing would flip it.
    owner.q_hash_input.mouseReleaseEvent = lambda e: QLineEdit.mouseReleaseEvent(
        owner.q_hash_input, e)
    lay.addWidget(owner.q_hash_input)
    owner.q_check_btn = QPushButton("Check ledger")
    owner.q_check_btn.setObjectName("ctaBtn")
    owner.q_check_btn.setMinimumHeight(44)
    owner.q_check_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    owner.q_check_btn.clicked.connect(owner.quick_verify)
    owner.q_hash_input.returnPressed.connect(owner.quick_verify)
    lay.addWidget(owner.q_check_btn)
    owner.q_result = _label("", size=10.5, colour=WEB["muted"], mono=True, wrap=True)
    lay.addWidget(owner.q_result)
    lay.addStretch()
    return face


class HomeSections:
    """Builds the Home page onto `owner` (the DashboardWindow)."""

    def __init__(self, owner):
        self.o = owner

    # ── page ──
    def build(self, existing_overview: QWidget) -> QWidget:
        """Wrap the pre-D4 overview widgets in the full Home page.

        `existing_overview` carries the hero, the stat tiles and the
        verification card the console already polls — reusing it keeps every
        existing signal path intact instead of re-deriving it."""
        o = self.o
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

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

        # Web order (html:925-1115): head · onboarding · coverage · hero+stats
        # +verification · trend+donut · alerts+actions · activity. The head and
        # the hero block already exist from D1, so D4 slots around them rather
        # than rebuilding a second copy of either.
        v.addWidget(o.ov_head)
        v.addWidget(self._onboarding())
        v.addWidget(self._coverage())
        v.addWidget(existing_overview)          # hero + tiles + stats + verify card
        v.addLayout(self._trend_and_donut())
        v.addWidget(self._recent_ledger())
        v.addLayout(self._alerts_and_actions())
        v.addWidget(self._activity())
        v.addStretch()

        scroll.setWidget(body)
        outer.addWidget(scroll)
        o.home_scroll = scroll
        return page

    # ── onboarding stepper (web #firstRun) ──
    def _onboarding(self) -> QWidget:
        o = self.o
        card, lay = _card()
        o.onboarding_card = card
        head = QHBoxLayout()
        col = QVBoxLayout()
        col.setSpacing(3)
        col.addWidget(_label("● GET SET UP", size=10, bold=True,
                             colour=WEB["fox2"], mono=True, spacing=1.5))
        o.onboarding_title = _label("Let's get your audit trail live.",
                                    size=17, bold=True)
        o.onboarding_sub = _label("", size=11.5, colour=WEB["muted"], wrap=True)
        col.addWidget(o.onboarding_title)
        col.addWidget(o.onboarding_sub)
        head.addLayout(col, 1)
        o.onboarding_dismiss = QPushButton("✕")
        o.onboarding_dismiss.setFixedSize(44, 44)
        o.onboarding_dismiss.setCursor(Qt.CursorShape.PointingHandCursor)
        o.onboarding_dismiss.setAccessibleName("Dismiss onboarding")
        o.onboarding_dismiss.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {WEB['muted']};"
            f" border: none; border-radius: 10px; font-size: 13px; }}"
            f"QPushButton:hover {{ background: {WEB['surf3']}; color: {WEB['ink']}; }}"
            f"QPushButton:focus {{ border: 1px solid {WEB['fox']}; }}")
        o.onboarding_dismiss.clicked.connect(o._dismiss_onboarding)
        head.addWidget(o.onboarding_dismiss, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(head)

        bar = QHBoxLayout()
        bar.setSpacing(11)
        o.onboarding_bar = FoxChart("gauge", height=12)
        bar.addWidget(o.onboarding_bar, 1)
        o.onboarding_pct = _label("", size=10, colour=WEB["muted"], mono=True)
        bar.addWidget(o.onboarding_pct)
        lay.addLayout(bar)

        o.onboarding_steps = QVBoxLayout()
        o.onboarding_steps.setSpacing(0)
        lay.addLayout(o.onboarding_steps)
        card.hide()                       # shown only when the API says so
        return card

    # ── capture coverage (web #captureCoverage) ──
    def _coverage(self) -> QWidget:
        o = self.o
        card, lay = _card()
        head = QHBoxLayout()
        col = QVBoxLayout()
        col.setSpacing(3)
        col.addWidget(_label("● EVIDENCE BOUNDARY", size=10, bold=True,
                             colour=WEB["fox2"], mono=True, spacing=1.5))
        col.addWidget(_label("Capture coverage.", size=16, bold=True))
        o.coverage_message = _label("Loading observed SDK continuity…",
                                    size=11.5, colour=WEB["muted"], wrap=True)
        col.addWidget(o.coverage_message)
        head.addLayout(col, 1)
        o.coverage_status = _label("● checking", size=9.5, bold=True,
                                   colour=WARN_AMBER, mono=True)
        head.addWidget(o.coverage_status, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(head)

        pct_row = QHBoxLayout()
        pct_row.setSpacing(18)
        pct_col = QVBoxLayout()
        pct_col.setSpacing(2)
        o.coverage_pct = _label("—", size=34, bold=True)
        pct_col.addWidget(o.coverage_pct)
        pct_col.addWidget(_label("OBSERVED CAPTURE", size=8.5, bold=True,
                                 colour=WEB["muted"], mono=True, spacing=0.6))
        pct_row.addLayout(pct_col)
        gauge_col = QVBoxLayout()
        gauge_col.setSpacing(6)
        o.coverage_gauge = FoxChart("gauge", height=14)
        gauge_col.addWidget(o.coverage_gauge)
        o.coverage_note = _label("of client-reported events were observed by Foxy.",
                                 size=10.5, colour=WEB["muted"], wrap=True)
        gauge_col.addWidget(o.coverage_note)
        pct_row.addLayout(gauge_col, 1)
        lay.addLayout(pct_row)

        chips = QGridLayout()
        chips.setSpacing(10)
        o.coverage_chips = []
        for i, (name, tone) in enumerate((("Events observed", None),
                                          ("SDK clients", None),
                                          ("Missing events", "bad"),
                                          ("No client identity", None))):
            tile, value = _stat_tile(name, "—", tone)
            chips.addWidget(tile, 0, i)
            o.coverage_chips.append(value)
        lay.addLayout(chips)

        lay.addWidget(_label("Events captured per day · last 90 days",
                             size=12, bold=True))
        o.coverage_volume = FoxChart(
            "area", height=120, tone="blue",
            empty={"title": "No captured events yet",
                   "desc": "Daily capture volume appears as your SDK reports "
                           "interactions."})
        lay.addWidget(o.coverage_volume)

        lay.addWidget(_label("Per-client continuity", size=12, bold=True))
        o.coverage_table = QVBoxLayout()
        o.coverage_table.setSpacing(0)
        header = QHBoxLayout()
        for text, stretch in (("CLIENT", 3), ("EVENTS", 1), ("CLIENT SEQ", 2),
                              ("STATUS", 1)):
            header.addWidget(_label(text, size=8.5, bold=True, colour=WEB["muted"],
                                    mono=True, spacing=0.5), stretch)
        o.coverage_table.addLayout(header)
        o.coverage_rows_start = o.coverage_table.count()
        lay.addLayout(o.coverage_table)
        o.coverage_empty = StatusStrip(compact=True)
        o.coverage_empty.retry.connect(o.refresh_home)
        lay.addWidget(o.coverage_empty)

        lay.addWidget(_label(
            "This measures events that reached Foxy. It cannot detect an AI call "
            "that bypasses the SDK, and it never reads raw prompts or responses.",
            size=10, colour=WEB["muted"], wrap=True))
        return card

    # ── usage trend + grading donut (web .p5row) ──
    def _trend_and_donut(self) -> QHBoxLayout:
        o = self.o
        row = QHBoxLayout()
        row.setSpacing(16)

        card, lay = _card()
        head = QHBoxLayout()
        head.addWidget(_label("Usage — last 90 days", size=12, bold=True))
        head.addStretch()
        # A real segmented control (web `.seg`, html:186-192 / 1041-1045): one
        # pill-shaped container holding the three buttons, exclusive selection,
        # styled by #seg / #segBtn in console_shell_qss.
        o.trend_seg = QWidget()
        o.trend_seg.setObjectName("seg")
        seg_lay = QHBoxLayout(o.trend_seg)
        seg_lay.setContentsMargins(3, 3, 3, 3)
        seg_lay.setSpacing(2)
        o.trend_group = QButtonGroup(o.trend_seg)
        o.trend_group.setExclusive(True)
        o.trend_buttons = {}
        for metric, spec in hd.TREND_METRICS.items():
            btn = QPushButton(spec["label"].upper())   # .seg uppercases in CSS
            btn.setObjectName("segBtn")
            btn.setCheckable(True)
            btn.setMinimumHeight(30)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setChecked(metric == "logs")
            # The checked pill is the only selection cue, so it has to be
            # spelled out for anything not looking at pixels.
            btn.setAccessibleName(f"Show {spec['label'].lower()}")
            btn.clicked.connect(lambda _c=False, m=metric: o.set_trend_metric(m))
            o.trend_group.addButton(btn)
            o.trend_buttons[metric] = btn
            seg_lay.addWidget(btn)
        head.addWidget(o.trend_seg)
        lay.addLayout(head)
        o.usage_trend = FoxChart(
            "area", height=200,
            empty={"title": "No usage yet",
                   "desc": "Daily volume appears as your SDK logs interactions."})
        lay.addWidget(o.usage_trend)
        row.addWidget(card, 3)

        donut_card, donut_lay = _card()
        donut_lay.addWidget(_label("Grading status", size=12, bold=True))
        o.grading_donut = FoxChart(
            "donut", height=170, legend=True,
            empty={"title": "Nothing graded yet",
                   "desc": "Grading status appears once the Judge processes "
                           "interactions."})
        donut_lay.addWidget(o.grading_donut)
        row.addWidget(donut_card, 2)
        return row

    # ── recent ledger preview (web #recentLedger) ──
    def _recent_ledger(self) -> QWidget:
        o = self.o
        wrap = QWidget()
        col = QVBoxLayout(wrap)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(8)
        title, act = _section_title("Recent ledger", "view all")
        act.clicked.connect(lambda: o.go("ledger"))
        col.addWidget(title)
        card, lay = _card()
        o.recent_ledger = QVBoxLayout()
        o.recent_ledger.setSpacing(0)
        # A StatusStrip rather than a label: it distinguishes "nothing yet"
        # from "couldn't load", and carries the retry the second case needs.
        o.recent_ledger_empty = StatusStrip(compact=True)
        o.recent_ledger_empty.retry.connect(o.refresh_home)
        o.recent_ledger.addWidget(o.recent_ledger_empty)
        lay.addLayout(o.recent_ledger)
        col.addWidget(card)
        return wrap

    # ── alerts + gauges + quick actions (web .g2) ──
    def _alerts_and_actions(self) -> QHBoxLayout:
        o = self.o
        row = QHBoxLayout()
        row.setSpacing(16)

        left = QVBoxLayout()
        left.setSpacing(8)
        title, act = _section_title("Active alerts", "see all")
        act.clicked.connect(lambda: o.go("threats"))
        # Live count, blank until /v1/analytics/threats actually answers — a
        # zero we have not measured is a claim, not an empty state.
        o.home_alerts_count = _label("", size=9.5, bold=True,
                                     colour=WEB["muted"], mono=True)
        title.layout().insertWidget(1, o.home_alerts_count)
        left.addWidget(title)
        card, lay = _card()
        o.home_alerts = QVBoxLayout()
        o.home_alerts.setSpacing(6)
        o.home_alerts_empty = StatusStrip(compact=True)
        o.home_alerts_empty.retry.connect(o.refresh_home)
        o.home_alerts.addWidget(o.home_alerts_empty)
        lay.addLayout(o.home_alerts)

        for name, attr in (("Clean rate", "clean"), ("Time to verdict", "verdict")):
            gauge_row = QHBoxLayout()
            gauge_row.addWidget(_label(name, size=11, colour=WEB["ink2"]))
            gauge_row.addStretch()
            value = _label("—", size=11, bold=True, mono=True)
            gauge_row.addWidget(value)
            lay.addLayout(gauge_row)
            chart = FoxChart("gauge", height=12)
            lay.addWidget(chart)
            setattr(o, f"gauge_{attr}_value", value)
            setattr(o, f"gauge_{attr}", chart)
        left.addWidget(card)
        row.addLayout(left, 1)

        right = QVBoxLayout()
        right.setSpacing(8)
        right.addWidget(_section_title("Quick actions")[0])
        grid = QGridLayout()
        grid.setSpacing(10)
        actions = (("Policy rules", "configure the AI Judge", "policy"),
                   ("Export", "compliance passport pdf", "export"),
                   ("API keys", "manage tenants", "access"),
                   ("Audit ledger", "tamper-proof log", "ledger"))
        for i, (name, desc, target) in enumerate(actions):
            tool = QPushButton()
            tool.setObjectName("toolBtn")
            tool.setMinimumHeight(64)
            tool.setCursor(Qt.CursorShape.PointingHandCursor)
            tool.setAccessibleName(f"{name} — {desc}")
            tool.setStyleSheet(
                f"QPushButton#toolBtn {{ background: {WEB['surf']};"
                f" border: 2.5px solid {WEB['bc']};"
                f" border-radius: {RADIUS['sm']}px; text-align: left;"
                f" padding: 10px 13px; }}"
                f"QPushButton#toolBtn:hover {{ background: {WEB['surf2']}; }}"
                f"QPushButton#toolBtn:focus {{ border-color: {WEB['fox']}; }}")
            inner = QVBoxLayout(tool)
            inner.setContentsMargins(2, 2, 2, 2)
            inner.setSpacing(2)
            inner.addWidget(_label(name, size=12.5, bold=True))
            # Two tiles across a narrow console clips "configure the AI Judge"
            # mid-word unless the description is allowed to wrap.
            inner.addWidget(_label(desc, size=10.5, colour=WEB["muted"], wrap=True))
            tool.clicked.connect(lambda _c=False, t=target: o.go(t))
            grid.addWidget(tool, i // 2, i % 2)
        right.addLayout(grid)
        right.addStretch()
        row.addLayout(right, 1)
        return row

    # ── recent activity (web injectHomeCard + loadGlobalActivity) ──
    def _activity(self) -> QWidget:
        o = self.o
        card, lay = _card()
        head = QHBoxLayout()
        head.addWidget(_label("Recent activity", size=12, bold=True))
        head.addStretch()
        o.activity_refresh = QPushButton("↻ refresh")
        o.activity_refresh.setObjectName("linkAct")
        o.activity_refresh.setMinimumHeight(44)
        o.activity_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        o.activity_refresh.setStyleSheet(
            f"QPushButton#linkAct {{ background: transparent; color: {WEB['fox2']};"
            f" border: none; font-family: '{pick_font('mono')}'; font-size: 9.5px;"
            f" font-weight: 700; padding: 4px 6px; }}"
            f"QPushButton#linkAct:hover {{ color: {WEB['fox']}; }}"
            f"QPushButton#linkAct:focus {{ border: 1px solid {WEB['fox']};"
            f" border-radius: 6px; }}")
        # Lambdas, not the bound method: QPushButton.clicked passes its
        # `checked` bool positionally, and both of these are user-triggered —
        # they must never join the refresh-cycle gating set (see the RULE in
        # DashboardWindow.refresh_activity).
        o.activity_refresh.clicked.connect(lambda: o.refresh_activity())
        head.addWidget(o.activity_refresh)
        lay.addLayout(head)

        o.activity_list = QVBoxLayout()
        o.activity_list.setSpacing(0)
        o.activity_empty = StatusStrip(compact=True)
        o.activity_empty.retry.connect(lambda: o.refresh_activity())
        o.activity_list.addWidget(o.activity_empty)
        lay.addLayout(o.activity_list)
        o.activity_stamp = _label("", size=9, colour=WEB["muted"], mono=True)
        lay.addWidget(o.activity_stamp)
        return card


# ── row builders used by the refresh handlers ───────────────────────────────
def onboarding_step_row(step: dict, on_jump) -> QWidget:
    """One checklist row: marker · title/desc · jump button."""
    row = QWidget()
    lay = QHBoxLayout(row)
    lay.setContentsMargins(0, 8, 0, 8)
    lay.setSpacing(11)
    mark = QLabel("✓" if step["done"] else "")
    mark.setFixedSize(20, 20)
    mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
    mark.setStyleSheet(
        (f"background: {OK_GREEN}; color: #0a3d20; border-radius: 10px;"
         f" border: 2px solid {WEB['bc']}; font-weight: 900; font-size: 11px;")
        if step["done"] else
        (f"background: transparent; border: 2px dashed {WEB['line']};"
         f" border-radius: 10px;"))
    lay.addWidget(mark, 0, Qt.AlignmentFlag.AlignTop)

    col = QVBoxLayout()
    col.setSpacing(2)
    title = _label(step["title"], size=12.5, bold=True,
                   colour=WEB["muted"] if step["done"] else WEB["ink"])
    if step["done"]:
        font = title.font()
        font.setStrikeOut(True)
        title.setFont(font)
    col.addWidget(title)
    if not step["done"] and step["desc"]:
        col.addWidget(_label(step["desc"], size=10.5, colour=WEB["muted"], wrap=True))
    lay.addLayout(col, 1)

    if not step["done"]:
        btn = QPushButton(f"{step['label']} →")
        btn.setMinimumHeight(44)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(
            f"QPushButton {{ background: {WEB['surf3']}; color: {WEB['ink']};"
            f" border: 2px solid {WEB['bc']}; border-radius: 10px;"
            f" padding: 6px 12px; font-family: '{pick_font('disp')}';"
            f" font-size: 11px; font-weight: 700; }}"
            f"QPushButton:hover {{ background: {WEB['fox']}; color: #1a0900; }}"
            f"QPushButton:focus {{ border-color: {WEB['fox']}; }}")
        btn.clicked.connect(lambda _c=False, p=step["page"]: on_jump(p))
        lay.addWidget(btn, 0, Qt.AlignmentFlag.AlignTop)
    return row


def coverage_row(client: dict) -> QWidget:
    row = QWidget()
    lay = QHBoxLayout(row)
    lay.setContentsMargins(0, 7, 0, 7)
    lay.setSpacing(8)
    # A client id can be long and the column is narrow. Ignored size policy
    # alone let QLabel hard-clip mid-character with no ellipsis and no way to
    # read the rest; elide marks the truncation and the tooltip recovers it.
    ident = _label("", size=10.5, mono=True, colour=WEB["ink2"])
    ident.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    _elide_to_width(ident, client["client_id"])
    lay.addWidget(ident, 3)
    lay.addWidget(_label(client["events"], size=10.5, mono=True), 1)
    seq_text = client["seq"] + (f" ({client['gap_text']})" if client["gap_text"] else "")
    lay.addWidget(_label(seq_text, size=10.5, mono=True,
                         colour=BAD_RED if client["gap_text"] else WEB["muted"]), 2)
    tone = {"gap": BAD_RED, "dup seq": WARN_AMBER}.get(client["status"], OK_GREEN)
    badge = _label(client["status"], size=9, bold=True, mono=True)
    badge.setStyleSheet(
        f"color: {WEB['breach_tx'] if client['status'] != 'continuous' else '#07210f'};"
        f" background: {tone}; border: 1.5px solid {WEB['bc']};"
        f" border-radius: 8px; padding: 2px 7px;"
        f" font-family: '{pick_font('mono')}'; font-size: 9px; font-weight: 800;")
    lay.addWidget(badge, 1)
    return row


def activity_row(item: dict) -> QWidget:
    row = QWidget()
    lay = QHBoxLayout(row)
    lay.setContentsMargins(0, 7, 0, 7)
    lay.setSpacing(10)
    tone = {"danger": BAD_RED, "key": WEB["fox"], "login": OK_GREEN}.get(
        item["tone"], WEB["muted"])
    dot = QLabel()
    dot.setFixedSize(8, 8)
    dot.setStyleSheet(f"background: {tone}; border-radius: 4px;"
                      f" border: 1.5px solid {WEB['bc']};")
    lay.addWidget(dot, 0, Qt.AlignmentFlag.AlignTop)
    col = QVBoxLayout()
    col.setSpacing(1)
    col.addWidget(_label(item["title"], size=12, bold=True))
    if item["sub"]:
        col.addWidget(_label(item["sub"], size=10.5, colour=WEB["muted"], wrap=True))
    lay.addLayout(col, 1)
    when = _label(relative_time(item.get("when")), size=9, colour=WEB["muted"],
                  mono=True)
    when.setProperty("iso", item.get("when") or "")
    lay.addWidget(when, 0, Qt.AlignmentFlag.AlignTop)
    row.setProperty("when_label", True)
    return row


def ledger_row(row: dict) -> QWidget:
    """One line of the Home "Recent ledger" preview (web recentRow)."""
    frame = QFrame()
    lay = QHBoxLayout(frame)
    lay.setContentsMargins(0, 8, 0, 8)
    lay.setSpacing(10)
    tone = {"ok": OK_GREEN, "bad": BAD_RED, "warn": WARN_AMBER}[row["tone"]]
    tag = _label(f"#{row['seq']}", size=10, bold=True, colour=tone, mono=True)
    tag.setMinimumWidth(46)
    lay.addWidget(tag)
    body = QVBoxLayout()
    body.setSpacing(2)
    name = row["name"] + (f"  · {row['agent']}" if row["agent"] else "")
    body.addWidget(_label(name, size=11.5, bold=True))
    body.addWidget(_label(row["hash"], size=9.5, colour=WEB["muted"], mono=True))
    lay.addLayout(body, 1)
    right = QVBoxLayout()
    right.setSpacing(2)
    verdict = _label(row["verdict"], size=9.5, bold=True, colour=tone, mono=True)
    verdict.setAlignment(Qt.AlignmentFlag.AlignRight)
    # Absolute local time, like the web's recentRow (html:2322,
    # `new Date(it.created_at).toLocaleTimeString()`). Only the activity feed
    # is relative; a ledger row is evidence and wants a wall-clock stamp.
    when = _label(local_time(row["when"]), size=9, colour=WEB["muted"], mono=True)
    when.setAlignment(Qt.AlignmentFlag.AlignRight)
    right.addWidget(verdict)
    right.addWidget(when)
    lay.addLayout(right)
    return frame


def alert_row(row: dict) -> QWidget:
    """One active alert (web alertRow, html:3324-3332)."""
    frame = QFrame()
    lay = QHBoxLayout(frame)
    lay.setContentsMargins(0, 7, 0, 7)
    lay.setSpacing(9)
    tone = {"ok": OK_GREEN, "bad": BAD_RED, "warn": WARN_AMBER}[row["tone"]]
    dot = QLabel()
    dot.setFixedSize(10, 10)
    dot.setStyleSheet(f"background: {tone}; border-radius: 5px;")
    lay.addWidget(dot, 0, Qt.AlignmentFlag.AlignTop)
    body = QVBoxLayout()
    body.setSpacing(2)
    head = QHBoxLayout()
    head.setSpacing(6)
    head.addWidget(_label(row["policy"], size=11.5, bold=True))
    if row["agent"]:
        head.addWidget(_label(f"· {row['agent']}", size=9,
                              colour=WEB["muted"], mono=True))
    # Risk is stated in words as well as colour — colour alone is not a signal
    # for everyone reading this card.
    head.addWidget(_label(f"risk {row['risk']}", size=10.5, bold=True, colour=tone))
    head.addStretch()
    body.addLayout(head)
    if row["reason"]:
        body.addWidget(_label(row["reason"], size=10.5, colour=WEB["muted"],
                              wrap=True))
    # Date AND time, like the web's alertRow (html:3323, `toLocaleString()`).
    stamp = _label(f"#{row['seq']} · {local_datetime(row['when'])}",
                   size=9, colour=WEB["muted"], mono=True)
    body.addWidget(stamp)
    lay.addLayout(body, 1)
    return frame
