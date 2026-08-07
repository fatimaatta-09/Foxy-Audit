"""Foxy Audit desktop — the Usage & billing page (D10).

The web's `#page-billing` (foxy-audit-premium.html:1479-1539), rebuilt with
foxy_tokens. Read-only: nothing here writes, and the two buttons that leave
the app (`Manage billing`, an invoice's `PDF ↗`) hand off to the browser
rather than trying to be Stripe.

Every amount is formatted in `billing_data` from the server's own cents and
currency; this module only places them. The one behavioural rule worth naming
here is the button gating: `POST /v1/billing/portal` and
`GET /v1/invoices/{id}/link` are both admin-only, so a member never sees a
control that would answer 403 — the same stance D7 takes on the ruleset.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QSizePolicy, QVBoxLayout, QWidget,
)

from charts import FoxChart
from export_page import _combo_qss, _field_label
from foxy_tokens import BAD_RED, OK_GREEN, RADIUS, WARN_AMBER, WEB, pick_font
from home_page import _card, _elide_to_width, _label, _stat_tile, scroll_qss
from chrome_widgets import AskAdminBlock
from panel_state import StatusStrip
import billing_data as bd

TONES = {"ok": OK_GREEN, "bad": BAD_RED, "warn": WARN_AMBER,
         "mute": WEB["muted"]}


class BillingSections:
    """Builds the Billing page onto `owner` (the DashboardWindow)."""

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
        v.addWidget(self._stats())
        v.addWidget(self._entitlements())
        v.addWidget(self._warning())
        row = QHBoxLayout()
        row.setSpacing(16)
        # Equal halves, like the web's `.g2` — the invoice table has four
        # columns plus a link and does not fit in a narrow rail.
        row.addWidget(self._usage(), 1)
        row.addLayout(self._right(), 1)
        v.addLayout(row)
        v.addStretch()

        scroll.setWidget(body)
        outer.addWidget(scroll)
        o.billing_scroll = scroll
        return page

    def _head(self) -> QWidget:
        o = self.o
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        col = QVBoxLayout()
        col.setSpacing(3)
        o.bil_eyebrow = _label("● PLAN & CONSUMPTION", size=10, bold=True,
                               colour=WEB["fox2"], mono=True, spacing=1.5)
        col.addWidget(o.bil_eyebrow)
        col.addWidget(_label("Usage & billing.", size=26, bold=True))
        col.addWidget(_label("Daily rollups from the audit ledger · card invoices "
                             "synced from Paddle.", size=12,
                             colour=WEB["muted"], wrap=True))
        lay.addLayout(col, 1)

        o.bil_refresh = QPushButton("↻ refresh")
        o.bil_refresh.setObjectName("ghostBtn")
        o.bil_refresh.setMinimumHeight(44)
        o.bil_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        o.bil_refresh.setAccessibleName("Refresh billing")
        o.bil_refresh.clicked.connect(lambda: o.refresh_billing())
        lay.addWidget(o.bil_refresh, 0, Qt.AlignmentFlag.AlignTop)
        return row

    def _stats(self) -> QWidget:
        o = self.o
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        # (frame, value label, caption label) — the captions are rewritten when
        # the org turns out to be on evaluation credits rather than a plan.
        o.bil_tiles = []
        for label, _value, tone in bd.stat_row(None, None):
            frame, value = _stat_tile(label, bd.MISSING, tone)
            caption = frame.findChildren(QLabel)[-1]
            o.bil_tiles.append((frame, value, caption))
            lay.addWidget(frame, 1)
        return row

    def _entitlements(self) -> QWidget:
        o = self.o
        card, lay = _card()
        card.setObjectName("entCard")
        card.setStyleSheet(
            f"QFrame#entCard {{ background: {WEB['surf2']};"
            f" border: 2px solid {WEB['bc']};"
            f" border-radius: {RADIUS['lg']}px; }}")
        o.bil_entitlements = _label("", size=11, colour=WEB["muted"], wrap=True)
        lay.addWidget(o.bil_entitlements)
        o.bil_ent_card = card
        card.hide()
        return card

    def _warning(self) -> QWidget:
        """The over-quota banner. Amber-on-dark rather than the web's
        black-on-amber block: the console is dark, and a solid amber slab is
        the only element on any page that would shout."""
        o = self.o
        card, lay = _card()
        card.setObjectName("warnCard")
        card.setStyleSheet(
            f"QFrame#warnCard {{ background: {WEB['surf2']};"
            f" border: 2px solid {WARN_AMBER};"
            f" border-radius: {RADIUS['lg']}px; }}")
        o.bil_warning = _label("", size=12, bold=True, colour=WARN_AMBER,
                               wrap=True)
        lay.addWidget(o.bil_warning)
        o.bil_warn_card = card
        card.hide()
        return card

    # ── left: headroom, trend, daily table ──────────────────────────────────
    def _usage(self) -> QWidget:
        o = self.o
        card, lay = _card()
        lay.addWidget(_label("Daily usage — last 30 days", size=12, bold=True))

        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(_label("Quota headroom", size=10.5,
                              colour=WEB["muted"]))
        head.addStretch()
        o.bil_gauge_lbl = _label(bd.MISSING, size=11, bold=True)
        head.addWidget(o.bil_gauge_lbl)
        lay.addLayout(head)
        o.bil_gauge = FoxChart()
        o.bil_gauge.set_options(type="gauge", height=14, value=0, max=100,
                                aria="Monthly quota used")
        lay.addWidget(o.bil_gauge)

        o.bil_trend = FoxChart()
        o.bil_trend.set_options(type="area", height=130, tone="fox", data=[],
                                aria="Audited interactions per day, last 30 days")
        lay.addWidget(o.bil_trend)

        lay.addWidget(_table_head(bd.USAGE_COLUMNS, (3, 2, 2, 2)))
        o.bil_usage_rows = QVBoxLayout()
        o.bil_usage_rows.setSpacing(0)
        lay.addLayout(o.bil_usage_rows)
        o.bil_usage_empty = StatusStrip(compact=True)
        o.bil_usage_empty.retry.connect(lambda: o.refresh_billing())
        lay.addWidget(o.bil_usage_empty)
        lay.addStretch()
        return card

    # ── right: plan card + invoice history ──────────────────────────────────
    def _right(self) -> QVBoxLayout:
        col = QVBoxLayout()
        col.setSpacing(16)
        col.addWidget(self._plan())
        col.addWidget(self._invoices())
        col.addStretch()
        return col

    def _plan(self) -> QWidget:
        o = self.o
        card, lay = _card()
        lay.addWidget(_label("Current plan", size=12, bold=True))
        o.bil_plan_rows = {}
        for key, label in (("tier", "plan"), ("status", "status"),
                           ("quota", "included / month"),
                           ("trial_ends", "trial ends")):
            line = QHBoxLayout()
            line.setSpacing(8)
            name = _label(label, size=10.5, colour=WEB["muted"])
            line.addWidget(name)
            line.addStretch()
            value = _label(bd.MISSING, size=11, bold=True)
            line.addWidget(value)
            holder = QWidget()
            holder.setLayout(line)
            o.bil_plan_rows[key] = (holder, value)
            lay.addWidget(holder)
        # Only present when a trial actually exists (web html:1517).
        o.bil_plan_rows["trial_ends"][0].hide()

        o.bil_manage = QPushButton("Manage billing ↗")
        o.bil_manage.setObjectName("ctaBtn")
        o.bil_manage.setMinimumHeight(44)
        o.bil_manage.setCursor(Qt.CursorShape.PointingHandCursor)
        o.bil_manage.clicked.connect(lambda: o.open_billing_portal())
        o.bil_manage.hide()
        lay.addWidget(o.bil_manage)

        o.bil_upgrade = QPushButton("Upgrade plan ↗")
        o.bil_upgrade.setObjectName("ghostBtn")
        o.bil_upgrade.setMinimumHeight(44)
        o.bil_upgrade.setCursor(Qt.CursorShape.PointingHandCursor)
        o.bil_upgrade.clicked.connect(lambda: o.open_upgrade())
        o.bil_upgrade.hide()
        lay.addWidget(o.bil_upgrade)

        # P5 · #107. E3 hid `bil_upgrade` from members on a sound rule — a
        # control that cannot work is worse than no control — and left nothing
        # in its place, so a member saw no upgrade path and no explanation. This
        # is what stands there instead: the reason, and the one thing they can
        # actually do about it.
        o.bil_ask = AskAdminBlock()
        o.bil_ask.ask.connect(lambda: o.ask_admin_to_upgrade(o.bil_ask))
        lay.addWidget(o.bil_ask)

        o.bil_plan_state = StatusStrip(compact=True)
        o.bil_plan_state.retry.connect(lambda: o.refresh_billing())
        lay.addWidget(o.bil_plan_state)
        return card

    def _invoices(self) -> QWidget:
        o = self.o
        card, lay = _card()
        lay.addWidget(_label("Invoice history", size=12, bold=True))
        o.bil_inv_chart = FoxChart()
        o.bil_inv_chart.set_options(type="bar", height=130, tone="blue",
                                    data=[], aria="Invoice totals over time")
        lay.addWidget(o.bil_inv_chart)
        lay.addWidget(_label(bd.NO_LINE_ITEMS, size=10, colour=WEB["muted"],
                             wrap=True))
        # Said once on this page, next to the invoices, because this is where a
        # customer looks when a charge on their statement is not a name they
        # recognise. See bd.STATEMENT_NOTE for why only the fixed prefix is named.
        lay.addWidget(_label(bd.STATEMENT_NOTE, size=10, colour=WEB["muted"],
                             wrap=True))
        lay.addWidget(_table_head(bd.INVOICE_COLUMNS, (3, 2, 2, 3)))
        o.bil_inv_rows = QVBoxLayout()
        o.bil_inv_rows.setSpacing(0)
        lay.addLayout(o.bil_inv_rows)
        o.bil_inv_empty = StatusStrip(compact=True)
        o.bil_inv_empty.retry.connect(lambda: o.refresh_billing())
        lay.addWidget(o.bil_inv_empty)
        return card


# ── rows ────────────────────────────────────────────────────────────────────
_WEIGHTS_USAGE = (3, 2, 2, 2)
_WEIGHTS_INVOICE = (3, 3, 2, 4)


class PlanDialog(QDialog):
    """Which plan, asked before Checkout opens (E3 · #36).

    The desktop still hands the payment itself to the browser — it does not try
    to be Stripe. What it stopped handing over is the QUESTION of whose
    workspace is being bought for: the pricing page this button used to open
    checks out anonymously, and the webhook answers that by creating a second,
    empty organisation. The tiers come from `GET /v1/billing/plans`, so this
    only ever offers what the deployment can complete, and it shows no price —
    the amount lives in Stripe and is confirmed at checkout.
    """

    def __init__(self, choices: list[dict], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Choose a plan")
        self.setModal(True)
        self.setMinimumWidth(440)
        self.setStyleSheet(f"QDialog {{ background: {WEB['surf']}; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 20)
        lay.setSpacing(10)
        lay.addWidget(_label("Choose a plan", size=15, bold=True))
        lay.addWidget(_label(bd.UPGRADE_BLURB, size=10.5, colour=WEB["muted"],
                             wrap=True))

        lay.addWidget(_field_label("Plan"))
        self.plans = QComboBox()
        self.plans.setAccessibleName("Plan to buy for this workspace")
        self.plans.setMinimumHeight(44)
        self.plans.setCursor(Qt.CursorShape.PointingHandCursor)
        self.plans.setStyleSheet(_combo_qss())
        for choice in choices:
            self.plans.addItem(choice["label"], choice["tier"])
        lay.addWidget(self.plans)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch()
        cancel = QPushButton("Cancel")
        cancel.setObjectName("ghostBtn")
        cancel.setMinimumHeight(44)
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        self.go = QPushButton("Continue to checkout ↗")
        self.go.setObjectName("ctaBtn")
        self.go.setMinimumHeight(44)
        self.go.setDefault(True)
        self.go.setCursor(Qt.CursorShape.PointingHandCursor)
        self.go.clicked.connect(self.accept)
        buttons.addWidget(self.go)
        lay.addLayout(buttons)

    def tier(self) -> str:
        return str(self.plans.currentData() or "")


def usage_row(row: dict) -> QWidget:
    frame = _row_frame()
    lay = frame.layout()
    lay.addWidget(_cell(row["day"], mono=True, colour=WEB["muted"]),
                  _WEIGHTS_USAGE[0])
    lay.addWidget(_cell(row["logs"], align_right=True), _WEIGHTS_USAGE[1])
    lay.addWidget(_cell(row["tokens"], mono=True, align_right=True),
                  _WEIGHTS_USAGE[2])
    # A breach count carries a tone; a zero stays muted so a quiet day does not
    # read as an incident.
    lay.addWidget(_cell(row["breaches"], align_right=True,
                        colour=BAD_RED if row["breached"] else WEB["muted"],
                        bold=row["breached"]), _WEIGHTS_USAGE[3])
    return frame


def invoice_row(row: dict, on_open) -> QWidget:
    frame = _row_frame()
    lay = frame.layout()
    lay.addWidget(_cell(row["date"], mono=True, colour=WEB["muted"]),
                  _WEIGHTS_INVOICE[0])
    # shrink=False: the amount column never gives way (see _cell).
    lay.addWidget(_cell(row["amount"], align_right=True, bold=True,
                        shrink=False), _WEIGHTS_INVOICE[1])
    lay.addWidget(_status_chip(row["status"], row["tone"]), _WEIGHTS_INVOICE[2])
    tail = QHBoxLayout()
    tail.setContentsMargins(0, 0, 0, 0)
    tail.setSpacing(6)
    tail.addWidget(_cell(row["period"], mono=True, colour=WEB["muted"]), 1)
    if on_open is not None:
        link = QPushButton("PDF ↗")
        link.setObjectName("linkBtn")
        link.setMinimumHeight(44)
        link.setCursor(Qt.CursorShape.PointingHandCursor)
        link.setAccessibleName(f"Open the invoice dated {row['date']}")
        link.setStyleSheet(
            f"QPushButton#linkBtn {{ background: transparent; border: none;"
            f" color: {WEB['fox2']}; font-family: '{pick_font('mono')}';"
            f" font-size: 10px; font-weight: 800; }}"
            f"QPushButton#linkBtn:hover {{ color: {WEB['fox']}; }}"
            f"QPushButton#linkBtn:focus {{ color: {WEB['fox']};"
            f" text-decoration: underline; }}")
        link.clicked.connect(lambda _c=False, i=row["id"]: on_open(i))
        tail.addWidget(link, 0)
    holder = QWidget()
    holder.setLayout(tail)
    lay.addWidget(holder, _WEIGHTS_INVOICE[3])
    return frame


def _row_frame() -> QFrame:
    frame = QFrame()
    frame.setObjectName("billRow")
    frame.setStyleSheet(
        f"QFrame#billRow {{ background: transparent; border: none;"
        f" border-bottom: 2px solid {WEB['bc']}; }}")
    lay = QHBoxLayout(frame)
    lay.setContentsMargins(10, 9, 10, 9)
    lay.setSpacing(10)
    return frame


def _cell(text: str, *, mono: bool = False, align_right: bool = False,
          colour: str | None = None, bold: bool = False,
          shrink: bool = True) -> QLabel:
    """One table cell.

    `shrink=False` keeps the label at its natural width. Money uses it: an
    `Ignored` policy lets Qt clip a label with no ellipsis and no tooltip, and
    a right-aligned amount clips from the LEFT — "€49.00" rendered as ":49.00"
    and "€129.00" as "|29.00". A partly-drawn amount is a wrong amount, so this
    column is never allowed to be the one that gives way.
    """
    label = _label(text, size=10.5 if mono else 11.5, mono=mono, bold=bold,
                   colour=colour)
    if align_right:
        label.setAlignment(Qt.AlignmentFlag.AlignRight
                           | Qt.AlignmentFlag.AlignVCenter)
    if shrink:
        # Elided rather than hard-clipped, with the full value on hover — the
        # house pattern from D4's coverage table.
        _elide_to_width(label, text)
        label.setSizePolicy(QSizePolicy.Policy.Ignored,
                            QSizePolicy.Policy.Preferred)
    return label


def _status_chip(text: str, tone: str) -> QWidget:
    holder = QWidget()
    lay = QHBoxLayout(holder)
    lay.setContentsMargins(0, 0, 0, 0)
    colour = TONES.get(tone, WEB["muted"])
    chip = QLabel(text.upper())
    chip.setObjectName("invChip")
    chip.setStyleSheet(
        f"QLabel#invChip {{ color: {colour}; background: {WEB['surf3']};"
        f" border: 1.5px solid {colour}; border-radius: 10px;"
        f" padding: 2px 9px; font-family: '{pick_font('mono')}';"
        f" font-size: 9px; font-weight: 800; }}")
    lay.addWidget(chip)
    lay.addStretch()
    return holder


def _table_head(columns, weights) -> QWidget:
    frame = QFrame()
    frame.setObjectName("billHead")
    frame.setStyleSheet(
        f"QFrame#billHead {{ background: transparent; border: none;"
        f" border-bottom: 2px solid {WEB['bc']}; }}")
    lay = QHBoxLayout(frame)
    lay.setContentsMargins(10, 4, 10, 9)
    lay.setSpacing(10)
    for index, name in enumerate(columns):
        cell = _label(name.upper(), size=9, bold=True, mono=True,
                      colour=WEB["muted"], spacing=1.0)
        if index and name.lower() in ("logs", "tokens", "breaches", "amount"):
            cell.setAlignment(Qt.AlignmentFlag.AlignRight
                              | Qt.AlignmentFlag.AlignVCenter)
        cell.setSizePolicy(QSizePolicy.Policy.Ignored,
                           QSizePolicy.Policy.Preferred)
        lay.addWidget(cell, weights[index])
    return frame
