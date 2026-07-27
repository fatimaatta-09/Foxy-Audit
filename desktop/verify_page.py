"""Foxy Audit desktop — the Verify + Anchors page (D6).

The web's `#page-verify` (foxy-audit-premium.html:1236-1298 + the loaders in
verify_data.py), rebuilt with foxy_tokens.

This is the page where the product's claim is actually tested, so the result
panel is the most carefully-worded surface in the app: six distinct states,
each with its own tone, and none of them collapsible into another. In
particular "intact but the Judge flagged a breach" is NOT a verification
failure and is never coloured like one.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea,
    QSizePolicy, QVBoxLayout, QWidget,
)

from foxy_tokens import BAD_RED, OK_GREEN, RADIUS, WARN_AMBER, WEB, pick_font
from home_page import _card, _label, scroll_qss
from panel_state import PanelState, StatusStrip
import verify_data as vd

TONES = {"ok": OK_GREEN, "bad": BAD_RED, "warn": WARN_AMBER,
         "checking": WEB["fox2"], "mute": WEB["muted"]}
ICONS = {"ok": "✓", "bad": "✕", "warn": "!", "checking": "…"}


class VerifySections:
    """Builds the Verify page onto `owner` (the DashboardWindow)."""

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
        v.addLayout(self._record_and_quick())
        v.addWidget(self._how_it_works())
        v.addWidget(self._whole_ledger())
        v.addWidget(self._anchors())
        v.addStretch()

        scroll.setWidget(body)
        outer.addWidget(scroll)
        o.verify_scroll = scroll
        return page

    def _head(self) -> QWidget:
        row = QWidget()
        lay = QVBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)
        lay.addWidget(_label("● PROVE IT", size=10, bold=True, colour=WEB["fox2"],
                             mono=True, spacing=1.5))
        lay.addWidget(_label("Verify a record.", size=26, bold=True))
        lay.addWidget(_label("Confirm any logged AI interaction is exactly as "
                             "recorded — untampered.", size=12,
                             colour=WEB["muted"], wrap=True))
        return row

    # ── the guided 3-step card + the quick check beside it ──
    def _record_and_quick(self) -> QHBoxLayout:
        o = self.o
        row = QHBoxLayout()
        row.setSpacing(16)

        card, lay = _card()
        lay.addWidget(_label("Verify a single record", size=12, bold=True))

        lay.addWidget(_step(1, "Paste a chain hash",
                            "Copy it from any row in your ledger. Raw prompts "
                            "and responses are never uploaded from this page."))
        o.v_hash = QLineEdit()
        o.v_hash.setPlaceholderText("paste chain hash from the ledger…")
        o.v_hash.setAccessibleName("Chain hash to verify")
        o.v_hash.setMinimumHeight(44)
        o.v_hash.setStyleSheet(_input_qss())
        o.v_hash.textChanged.connect(o.on_verify_hash_changed)
        o.v_hash.returnPressed.connect(lambda: o.run_verify())
        lay.addWidget(o.v_hash)
        # Live, not on-submit: the user is pasting 64 hex characters and a
        # running count tells them they clipped it.
        o.v_hint = _label("", size=10, colour=WEB["muted"], mono=True)
        o.v_hint.setMinimumHeight(14)
        lay.addWidget(o.v_hint)

        lay.addWidget(_step(2, "Run the check",
                            "Foxy recomputes this record with its real "
                            "predecessor and compares it to what was stored."))
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        o.v_run = QPushButton("Verify record")
        o.v_run.setObjectName("ctaBtn")
        o.v_run.setMinimumHeight(44)
        o.v_run.setCursor(Qt.CursorShape.PointingHandCursor)
        o.v_run.clicked.connect(lambda: o.run_verify())
        o.v_clear = QPushButton("Clear")
        o.v_clear.setObjectName("ghostBtn")
        o.v_clear.setMinimumHeight(44)
        o.v_clear.setCursor(Qt.CursorShape.PointingHandCursor)
        o.v_clear.clicked.connect(lambda: o.clear_verify())
        buttons.addWidget(o.v_run, 1)
        buttons.addWidget(o.v_clear, 1)
        lay.addLayout(buttons)

        lay.addWidget(_step(3, "Read the result",
                            "A clear pass / fail with the record's sequence "
                            "and verdict."))
        o.v_result = ResultPanel()
        lay.addWidget(o.v_result)
        row.addWidget(card, 3)

        card2, lay2 = _card()
        lay2.addWidget(_label("Quick check", size=12, bold=True))
        lay2.addWidget(_label("Paste a hash for a one-line answer.", size=10.5,
                              colour=WEB["muted"], wrap=True))
        o.v_quick_hash = QLineEdit()
        o.v_quick_hash.setPlaceholderText("paste hash from ledger…")
        o.v_quick_hash.setAccessibleName("Chain hash for the quick check")
        o.v_quick_hash.setMinimumHeight(44)
        o.v_quick_hash.setStyleSheet(_input_qss())
        o.v_quick_hash.returnPressed.connect(lambda: o.run_quick_verify())
        lay2.addWidget(o.v_quick_hash)
        o.v_quick_btn = QPushButton("check ledger")
        o.v_quick_btn.setObjectName("ghostBtn")
        o.v_quick_btn.setMinimumHeight(44)
        o.v_quick_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        o.v_quick_btn.clicked.connect(lambda: o.run_quick_verify())
        lay2.addWidget(o.v_quick_btn)
        o.v_quick_result = _label("", size=10.5, colour=WEB["muted"], mono=True,
                                  wrap=True)
        lay2.addWidget(o.v_quick_result)
        lay2.addStretch()
        row.addWidget(card2, 2)
        return row

    # ── collapsible explainer ──
    def _how_it_works(self) -> QWidget:
        o = self.o
        card, lay = _card()
        o.v_explain_btn = QPushButton("How the fingerprint works  ·  advanced")
        o.v_explain_btn.setObjectName("ghostBtn")
        o.v_explain_btn.setCheckable(True)
        o.v_explain_btn.setMinimumHeight(44)
        o.v_explain_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        lay.addWidget(o.v_explain_btn)

        o.v_explain = QWidget()
        inner = QVBoxLayout(o.v_explain)
        inner.setContentsMargins(0, 6, 0, 0)
        inner.setSpacing(8)
        inner.addWidget(_label(vd.HOW_IT_WORKS, size=10.5, colour=WEB["muted"],
                               wrap=True))
        inner.addWidget(_code(vd.CHAIN_FORMULA))
        inner.addWidget(_code(vd.OFFLINE_COMMAND))
        inner.addWidget(_label(vd.OFFLINE_NOTE, size=10.5, colour=WEB["muted"],
                               wrap=True))
        o.v_explain.hide()
        o.v_explain_btn.toggled.connect(o.v_explain.setVisible)
        # Collapsed panels are invisible to anything not looking at pixels.
        o.v_explain_btn.toggled.connect(
            lambda on: o.v_explain_btn.setAccessibleDescription(
                "expanded" if on else "collapsed"))
        lay.addWidget(o.v_explain)
        return card

    # ── whole-ledger check + anchor now ──
    def _whole_ledger(self) -> QWidget:
        o = self.o
        card, lay = _card()
        lay.addWidget(_label("Check the whole ledger", size=12, bold=True))
        lay.addWidget(_label("One click re-checks every record on the server "
                             "and flags any tampering.", size=10.5,
                             colour=WEB["muted"], wrap=True))
        o.v_chain_btn = QPushButton("verify live ledger chain")
        o.v_chain_btn.setObjectName("ctaBtn")
        o.v_chain_btn.setMinimumHeight(44)
        o.v_chain_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        o.v_chain_btn.clicked.connect(lambda: o.run_chain_check())
        lay.addWidget(o.v_chain_btn)

        o.v_anchor_btn = QPushButton("⚓ anchor now (publish head to Sepolia)")
        o.v_anchor_btn.setObjectName("ghostBtn")
        o.v_anchor_btn.setMinimumHeight(44)
        o.v_anchor_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        o.v_anchor_btn.clicked.connect(lambda: o.anchor_now())
        lay.addWidget(o.v_anchor_btn)

        o.v_chain_result = ResultPanel(compact=True)
        lay.addWidget(o.v_chain_result)
        o.v_anchor_link = _link("")
        o.v_anchor_link.hide()
        lay.addWidget(o.v_anchor_link)
        return card

    # ── anchor receipts ──
    def _anchors(self) -> QWidget:
        o = self.o
        card, lay = _card()
        head = QHBoxLayout()
        head.addWidget(_label("Public-chain anchors", size=12, bold=True))
        head.addStretch()
        o.v_anchor_fresh = _label("", size=10, colour=WEB["muted"], mono=True)
        head.addWidget(o.v_anchor_fresh)
        lay.addLayout(head)
        o.v_anchor_sla = _label("Each receipt records your ledger head on a "
                                "public blockchain — independent, third-party "
                                "proof no one (not even us) can alter.",
                                size=10.5, colour=WEB["muted"], wrap=True)
        lay.addWidget(o.v_anchor_sla)

        o.v_anchor_list = QVBoxLayout()
        o.v_anchor_list.setSpacing(0)
        o.v_anchor_empty = StatusStrip()
        o.v_anchor_empty.retry.connect(lambda: o.refresh_anchors())
        o.v_anchor_list.addWidget(o.v_anchor_empty)
        lay.addLayout(o.v_anchor_list)
        return card


# ── pieces ──────────────────────────────────────────────────────────────────
def _input_qss() -> str:
    return (f"QLineEdit {{ background: {WEB['surf2']}; color: {WEB['ink']};"
            f" border: 2.5px solid {WEB['bc']}; border-radius: {RADIUS['sm']}px;"
            f" padding: 8px 11px; font-family: '{pick_font('mono')}';"
            f" font-size: 11px; }}"
            f"QLineEdit:focus {{ border-color: {WEB['fox']}; }}")


def _step(number: int, title: str, detail: str) -> QWidget:
    """One numbered step. The number is a real visual anchor, not decoration —
    it is what makes this read as a procedure rather than a form."""
    row = QWidget()
    lay = QHBoxLayout(row)
    lay.setContentsMargins(0, 8, 0, 2)
    lay.setSpacing(10)
    badge = _label(str(number), size=11, bold=True, mono=True, colour="#1a0900")
    badge.setFixedSize(22, 22)
    badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
    badge.setStyleSheet(badge.styleSheet()
                        + f"background: {WEB['fox']}; border-radius: 11px;")
    lay.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
    col = QVBoxLayout()
    col.setSpacing(2)
    col.addWidget(_label(title, size=11.5, bold=True))
    col.addWidget(_label(detail, size=10.5, colour=WEB["muted"], wrap=True))
    lay.addLayout(col, 1)
    return row


def _code(text: str) -> QLabel:
    box = QLabel(text)
    box.setWordWrap(True)
    box.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    box.setStyleSheet(
        f"color: {WEB['ink2']}; background: {WEB['surf2']};"
        f" border: 2px solid {WEB['bc']}; border-radius: {RADIUS['sm']}px;"
        f" padding: 9px 11px; font-family: '{pick_font('mono')}';"
        f" font-size: 10.5px;")
    return box


def _link(text: str) -> QLabel:
    link = QLabel(text)
    link.setOpenExternalLinks(True)
    link.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
    link.setStyleSheet(f"color: {WEB['fox2']}; font-family: "
                       f"'{pick_font('mono')}'; font-size: 10.5px;"
                       f" background: transparent;")
    return link


class ResultPanel(QFrame):
    """The six-state verification result.

    Icon + title + detail, toned by outcome. Hidden until a check has actually
    run — an empty result box invites the reading that something was checked
    and found nothing.
    """

    def __init__(self, parent=None, *, compact: bool = False):
        super().__init__(parent)
        self._tone = "mute"
        # Named, because an unscoped `QFrame { border: … }` also matches every
        # QLabel inside it — QLabel subclasses QFrame — and drew a box around
        # each line of the result.
        self.setObjectName("resultPanel")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(13, 11, 13, 11)
        lay.setSpacing(11)
        self.icon = _label("", size=16, bold=True)
        self.icon.setFixedWidth(22)
        self.icon.setAlignment(Qt.AlignmentFlag.AlignTop
                               | Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(self.icon)
        col = QVBoxLayout()
        col.setSpacing(3)
        self.title = _label("", size=12, bold=True, wrap=True)
        self.detail = _label("", size=10.5, colour=WEB["muted"], wrap=True)
        col.addWidget(self.title)
        col.addWidget(self.detail)
        lay.addLayout(col, 1)
        if compact:
            lay.setContentsMargins(11, 9, 11, 9)
        self.hide()

    def tone(self) -> str:
        return self._tone

    def clear(self):
        self._tone = "mute"
        self.title.setText("")
        self.detail.setText("")
        self.hide()

    def show_result(self, tone: str, title: str, detail: str = ""):
        self._tone = tone
        colour = TONES.get(tone, WEB["muted"])
        self.icon.setText(ICONS.get(tone, "•"))
        self.icon.setStyleSheet(
            f"color: {colour}; font-family: '{pick_font('disp')}';"
            f" font-size: 16px; font-weight: 800; background: transparent;")
        self.title.setStyleSheet(
            f"color: {colour}; font-family: '{pick_font('disp')}';"
            f" font-size: 12px; font-weight: 800; background: transparent;")
        self.title.setText(title)
        self.detail.setText(detail)
        self.detail.setVisible(bool(detail))
        self.setStyleSheet(
            f"QFrame#resultPanel {{ background: {WEB['surf2']};"
            f" border: 2px solid {colour}; border-radius: {RADIUS['sm']}px; }}")
        # Qt has no aria-live; this is what a screen reader reads on focus.
        self.setAccessibleName(title)
        self.setAccessibleDescription(f"{title}. {detail}" if detail else title)
        self.show()


def anchor_row(row: dict) -> QWidget:
    """One anchor receipt."""
    frame = QFrame()
    lay = QHBoxLayout(frame)
    lay.setContentsMargins(0, 8, 0, 8)
    lay.setSpacing(10)
    colour = TONES.get(row["tone"], WEB["muted"])

    pill = _label(row["status"], size=9.5, bold=True, mono=True,
                  colour="#1a0900" if row["tone"] != "mute" else WEB["ink"])
    pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
    pill.setFixedWidth(84)
    pill.setStyleSheet(pill.styleSheet() + f"background: {colour};"
                       f" border-radius: 9px; padding: 3px 0;")
    lay.addWidget(pill)

    from console_chrome import relative_time
    when = relative_time(row["when"]) if row["when"] else ""
    lay.addWidget(_label(f"{row['chain']} · {when}", size=10.5,
                         colour=WEB["muted"], mono=True), 1)

    if row["url"]:
        lay.addWidget(_link(f'<a href="{row["url"]}" '
                            f'style="color:{WEB["fox2"]};">Etherscan ↗</a>'))
    elif row["detail"]:
        lay.addWidget(_label(row["detail"], size=9.5, colour=WEB["muted"],
                             mono=True))
    return frame
