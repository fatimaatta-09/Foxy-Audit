"""
Foxy Audit — Auditor Console
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
The enterprise dashboard a compliance officer / auditor actually works in.
It renders the platform's "Blind Audit Log": a real-time, tamper-evident
stream of AI-interaction *metadata* (hashes, policy tags, Gemini verdicts,
risk scores) — never raw prompt/response text, in keeping with the
zero-knowledge payload design.

Opens from the desktop fox (context menu / tray) and is fed by the same
live telemetry the fox reacts to:

  • System health        ← GlobalSensors  (CPU / RAM / battery)
  • Hash confirmations   ← SDKBridge      (hash_ok  → compliant log rows)
  • Policy breaches      ← SDKBridge      (policy_breach → flagged rows)
  • Backend connectivity ← StartupHealthWorker

Design goals
────────────
• Reads as professional B2B software, not a desktop toy: left nav rail,
  top bar, KPI tiles, a real data table, hairline borders, restrained
  status colour, monospaced hashes, and no emoji.
• One fixed look — the design tokens, fonts and QSS all come from
  foxy_tokens.py (the app-wide single source); there is no theme picker.
• Public slots are unchanged from the previous version, so the omni_fox
  wiring (on_hardware / on_hash_ok / on_policy_breach / set_connected /
  refresh_requested / show_animated) needs no edits.

All backend traffic goes through foxy_client (one FoxyClient + the generic
ApiWorker) — no bespoke HTTP code in this file.
"""

from __future__ import annotations

import sys
import time
import hashlib
from datetime import datetime, timezone
from urllib.parse import quote

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QStackedWidget, QTableWidget, QTableWidgetItem, QHeaderView,
    QApplication, QSizePolicy, QButtonGroup, QAbstractItemView,
    QTextEdit, QLineEdit,
)
from PyQt6.QtCore import (
    Qt, QAbstractAnimation, QPoint, QRectF, QTimer, QPropertyAnimation,
    QEasingCurve, pyqtSignal, pyqtProperty,
)
from PyQt6.QtGui import (
    QColor, QPainter, QPen, QBrush, QFont, QPixmap, QLinearGradient,
)

from fox_settings import FoxSettings
from foxy_client import (
    FoxyClient, spawn_worker, shutdown_workers, status_of, detail_of,
)
from console_chrome import (
    relative_time,
    ALL_SECTIONS, BREACH_SCAN_LIMIT, EXTRA_SECTIONS, HEALTH_PING_SECONDS,
    NOTIF_LIMIT, QUICK_NAV,
    QUICK_NAV_WINDOW_MS, SECTIONS, announcement, avatar_initial, badge_text,
    breach_pip, notification_target,
)
from chrome_widgets import (
    AnnouncementBanner, CommandPalette, LiveDot, NotificationsPanel, Pip,
    ShortcutsOverlay, Toast,
)
from charts import FoxChart
import home_data as hd

import panel_state
from panel_state import PanelState, chart_empty, resolve

from access_page import (
    AccessSections, CodeBox, NewKeyDialog, ShownOnceDialog, key_row,
)
from export_page import ExportSections, history_row
import policy_page
from policy_page import PolicySections
from billing_page import BillingSections, invoice_row, usage_row
from settings_page import SettingsSections, device_row
from ledger_page import LedgerRow, LedgerSections
from verify_page import VerifySections, anchor_row
import access_data as ad
import policy_data as pd
import billing_data as bd
import settings_data as sd
import export_data as ed
import verify_data as vd
from threats_page import ThreatsSections, alert_table_row
import ledger_data as ld
import threats_data as td
from home_page import (
    FlipCard, HomeSections, activity_row, alert_row, coverage_row, ledger_row,
    onboarding_step_row, quick_check_face,
)
from foxy_tokens import (
    OK_GREEN, WARN_AMBER, BAD_RED, INFO_BLUE, DARK_TX, WEB,
    clay_tokens as _clay_tokens,
    console_shell_qss,
    glass_shadow as _glass_shadow,
    hairline as _hairline,
    is_dark as _is_dark,
    paint_icon,
    pick_font as _pick_font,
    qcolor as _qcolor,
    resource_path,
    with_alpha as _with_alpha,
)

# Kept as module names because the tests and the page both read them. The
# wording now lives in panel_state, so every page from D5 on fails in one voice.
UNAVAILABLE_TEXT = panel_state.ERROR_BODY
UNAVAILABLE = chart_empty(PanelState.ERROR)

#: Tone → tile accent for the web's Home stat row (html:1030-1033: `.v fox`,
#: `.v danger`, then two unmodified, which take the theme accent).
_STAT_ACCENT = {"fox": None, "bad": BAD_RED, "ok": OK_GREEN, "warn": WARN_AMBER}

#: The hero sparkline's ink. The hero is a warm gradient (#c96a2f → #6e3411)
#: and the site draws over it in near-black — the same ink as QLabel#heroNum.
#: It must stay 7-char hex: qcolor() cannot parse rgba(), and an unparseable
#: tone degrades silently to mid-grey rather than raising.
HERO_SPARK_INK = "#1a0900"

#: ...and a faint scrim under it. The ink alone is 5.16:1 at the gradient's
#: light end but 2.00:1 at its dark end, under the 3:1 floor for meaningful
#: graphics. A 26% white backing lifts the local background enough that the
#: SAME ink clears the bar across the whole run — 7.50:1 light, 4.06:1 dark —
#: so the stroke stays the colour the website uses instead of being recoloured.
HERO_SPARK_BACKING = {"color": "#ffffff", "alpha": 0.26, "radius": 6}

#: The web caps the time-to-verdict gauge at 10 seconds (html:2236); anything
#: slower pins the bar full while the label still states the true figure.
VERDICT_GAUGE_MAX = 10


# ── Status badge (pill) ─────────────────────────────────────────────────────
class Badge(QLabel):
    def __init__(self, text: str, color: str, parent=None):
        super().__init__(text, parent)
        self._color = color
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.restyle(color)

    def restyle(self, color: str | None = None):
        if color:
            self._color = color
        c = self._color
        tx = "#FFFFFF" if _is_dark(c) else DARK_TX
        mono = _pick_font("mono")
        self.setStyleSheet(
            f"QLabel {{ color: {tx};"
            f" background: {c};"
            f" border: 1px solid rgba(0,0,0,55);"
            f" border-radius: 9px; padding: 3px 12px;"
            f" font-family: '{mono}'; font-size: 9px; font-weight: 700;"
            f" letter-spacing: 0.5px; }}")


# ── KPI tile ────────────────────────────────────────────────────────────────
class KpiTile(QFrame):
    def __init__(self, tokens: dict, label: str, value="—", sub="", accent=None):
        super().__init__()
        self._accent = accent
        self.setObjectName("kpiTile")
        self.label_lbl = QLabel(label.upper())
        self.label_lbl.setObjectName("kpiLabel")
        self.value_lbl = QLabel(value)
        self.value_lbl.setObjectName("kpiValue")
        self.sub_lbl = QLabel(sub)
        self.sub_lbl.setObjectName("kpiSub")
        body = QVBoxLayout(self)
        body.setContentsMargins(17, 15, 15, 16)
        body.setSpacing(5)
        body.addWidget(self.label_lbl)
        body.addWidget(self.value_lbl)
        body.addWidget(self.sub_lbl)
        self.restyle(tokens)

    def restyle(self, tokens: dict):
        self._tokens = tokens
        acc = self._accent or tokens["accent"]
        mono = tokens.get("font_mono", "Consolas")
        self.setStyleSheet(f"""
            QFrame#kpiTile {{
                background: rgba(170,170,255,15);
                border: 1px solid {_with_alpha(acc, 48)};
                border-radius: 16px; }}
            QLabel#kpiLabel {{
                color: {tokens.get('text_muted', '#888')};
                font-family: '{mono}';
                font-size: 9px; font-weight: 700; letter-spacing: 0.8px;
                background: transparent; border: none; }}
            QLabel#kpiValue {{
                color: {tokens['text']}; font-size: 27px; font-weight: 800;
                letter-spacing: -1px; background: transparent; border: none; }}
            QLabel#kpiSub {{
                color: {tokens.get('text_muted2', tokens.get('text_muted', '#888'))};
                font-family: '{mono}'; font-size: 9px;
                background: transparent; border: none; }}
        """)
        # no Qt drop-shadow on glass panels — the effect darkens nested rows

    def set_value(self, value: str, sub: str | None = None,
                  accent: str | None = None):
        self.value_lbl.setText(value)
        if sub is not None:
            self.sub_lbl.setText(sub)
        if accent is not None:
            self._accent = accent
            # re-skin so the card tint + top strip both track the new status colour
            self.restyle(getattr(self, "_tokens", _clay_tokens()))


# ── Slim labelled meter (system vitals) ─────────────────────────────────────
class MiniMeter(QWidget):
    def __init__(self, label: str, tokens: dict, higher_is_better=False, parent=None):
        super().__init__(parent)
        self.setFixedHeight(46)
        self._label = label
        self._tokens = tokens
        self._higher = higher_is_better
        self._value = 0.0
        self._anim = QPropertyAnimation(self, b"value", self)
        self._anim.setDuration(600)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    @pyqtProperty(float)
    def value(self) -> float:
        return self._value

    @value.setter
    def value(self, v: float):
        self._value = v
        self.update()

    def set_value(self, v: float):
        v = max(0.0, min(100.0, float(v)))
        self._anim.stop()
        self._anim.setStartValue(self._value)
        self._anim.setEndValue(v)
        self._anim.start()

    def set_tokens(self, tokens: dict):
        self._tokens = tokens
        self.update()

    def _color(self) -> str:
        v = self._value
        if self._higher:
            return OK_GREEN if v >= 50 else WARN_AMBER if v >= 20 else BAD_RED
        return OK_GREEN if v < 70 else WARN_AMBER if v < 85 else BAD_RED

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        t = self._tokens
        w = self.width()
        f = QFont(t.get("font", "Segoe UI"), 9)
        f.setBold(True)
        p.setFont(f)
        p.setPen(_qcolor(t.get("text_muted", "#888")))
        p.drawText(QRectF(0, 4, w * 0.7, 16), Qt.AlignmentFlag.AlignLeft, self._label)
        p.setPen(_qcolor(t["text"]))
        p.drawText(QRectF(w * 0.3, 4, w * 0.7, 16),
                   Qt.AlignmentFlag.AlignRight, f"{self._value:.0f}%")
        track_y, track_h = 28, 10
        p.setPen(QPen(_qcolor("#000000"), 2))
        p.setBrush(QBrush(_qcolor(t.get("bg2", t["bg"]))))
        p.drawRoundedRect(QRectF(1, track_y, w - 2, track_h), 5, 5)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(_qcolor(self._color())))
        fill_w = max(track_h - 2, (w - 6) * self._value / 100.0)
        p.drawRoundedRect(QRectF(3, track_y + 2, fill_w, track_h - 4), 3, 3)
        p.end()


# ── Card container ──────────────────────────────────────────────────────────
class Card(QFrame):
    def __init__(self, tokens: dict, title: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self._title = title
        self.title_lbl = QLabel(title.upper()) if title else None
        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(10)
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 16)
        root.setSpacing(12)
        if self.title_lbl is not None:
            self.title_lbl.setObjectName("cardTitle")
            root.addWidget(self.title_lbl)
        root.addLayout(self.body)
        self.restyle(tokens)

    def restyle(self, tokens: dict):
        self.setStyleSheet(f"""
            QFrame#card {{
                background: rgba(176,174,255,21);
                border: 1px solid rgba(190,186,255,16);
                border-radius: 18px; }}
            QLabel#cardTitle {{
                color: {tokens.get('text_muted', '#888')};
                font-family: '{tokens.get('font_mono', 'Consolas')}';
                font-size: 9px; font-weight: 700; letter-spacing: 1.2px;
                background: transparent; border: none; }}
        """)
        # no Qt drop-shadow on glass panels — the effect darkens nested rows


# ── Audit-log data table ────────────────────────────────────────────────────
class AuditTable(QTableWidget):
    MAX_ROWS = 250
    COLS = ["TIME", "POLICY", "PROMPT HASH", "TOKENS", "VERDICT", "RISK"]

    def __init__(self, tokens: dict, parent=None):
        super().__init__(0, len(self.COLS), parent)
        self._tokens = tokens
        self.setHorizontalHeaderLabels(self.COLS)
        self.verticalHeader().setVisible(False)
        self.setShowGrid(False)
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAlternatingRowColors(True)
        self.setWordWrap(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        hh = self.horizontalHeader()
        hh.setHighlightSections(False)
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)   # chunky verdict pill
        hh.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.setColumnWidth(4, 124)
        self.verticalHeader().setDefaultSectionSize(40)
        self.restyle(tokens)

    def restyle(self, tokens: dict):
        t = tokens
        self._tokens = t
        mono = t.get("font_mono", "Consolas")
        self.setStyleSheet(f"""
            QTableWidget {{
                background: transparent;
                alternate-background-color: rgba(190,186,255,15);
                color: {t['text']}; border: none; outline: none;
                font-family: '{mono}'; font-size: 12px;
                gridline-color: transparent; }}
            QTableWidget::item {{ padding: 5px 12px; border: none; }}
            QHeaderView::section {{
                background: transparent; color: {t.get('text_muted', '#888')};
                padding: 10px 12px; border: none;
                border-bottom: 1px solid rgba(255,255,255,32);
                font-family: '{mono}';
                font-size: 9px; font-weight: 700; letter-spacing: 0.8px; }}
            QScrollBar:vertical {{ width: 8px; background: transparent; margin: 6px 2px; }}
            QScrollBar::handle:vertical {{
                background: rgba(255,255,255,60);
                border-radius: 4px; min-height: 28px; }}
            QScrollBar::handle:vertical:hover {{ background: rgba(255,255,255,95); }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """)
        for r in range(self.rowCount()):
            cell = self.cellWidget(r, 4)
            if cell is not None:
                for b in cell.findChildren(Badge):
                    b.restyle()

    def _item(self, text: str, color: str, mono=False, align=None) -> QTableWidgetItem:
        it = QTableWidgetItem(text)
        it.setForeground(_qcolor(color))
        if mono:
            it.setFont(QFont(self._tokens.get("font_mono", "Consolas"), 10))
        if align:
            it.setTextAlignment(align | Qt.AlignmentFlag.AlignVCenter)
        else:
            it.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        return it

    def add_event(self, ev: dict):
        t = self._tokens
        self.insertRow(0)
        muted = t.get("text_muted", "#888")
        self.setItem(0, 0, self._item(ev["time"], muted, mono=True))
        self.setItem(0, 1, self._item(ev["policy"], t["text"]))
        self.setItem(0, 2, self._item(ev["hash"][:22] + "…", muted, mono=True))
        self.setItem(0, 3, self._item(str(ev.get("tokens", "")), t["text"],
                                      align=Qt.AlignmentFlag.AlignRight))
        ok = ev["kind"] == "ok"
        captured = ev["kind"] == "captured"
        badge = Badge(
            "COMPLIANT" if ok else "CAPTURED" if captured else "FLAGGED",
            OK_GREEN if ok or captured else BAD_RED,
        )
        cell = QWidget()
        lay = QHBoxLayout(cell)
        lay.setContentsMargins(8, 0, 8, 0)
        lay.addWidget(badge)
        lay.addStretch()
        self.setCellWidget(0, 4, cell)
        risk = ev.get("risk")
        rc = (OK_GREEN if (risk or 0) < 40 else WARN_AMBER if (risk or 0) < 70 else BAD_RED)
        self.setItem(0, 5, self._item("—" if risk is None else str(risk),
                                      muted if risk is None else rc,
                                      align=Qt.AlignmentFlag.AlignRight))
        while self.rowCount() > self.MAX_ROWS:
            self.removeRow(self.rowCount() - 1)

    def populate_from_backend(self, items: list):
        """Replace table contents with rows fetched from GET /v1/logs.

        Each item is a dict with the LogListItem shape from the backend.
        Newest-first ordering is preserved (the backend already sorts desc).
        """
        self.setRowCount(0)  # clear existing rows
        for item in items:
            verdict = item.get("gemini_verdict") or {}
            breach = verdict.get("policy_breach", False)
            risk_score = verdict.get("risk_score", None) if breach else None
            # Parse ISO timestamp from backend → HH:MM:SS
            ts_str = item.get("created_at", "")
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                time_str = ts.strftime("%H:%M:%S")
            except (ValueError, AttributeError):
                time_str = ts_str[:8] if ts_str else "—"
            ev = {
                "time":   time_str,
                "kind":   "breach" if breach else "ok",
                "policy": item.get("policy_tag", "—"),
                "hash":   item.get("prompt_hash", "—"),
                "tokens": item.get("token_count", ""),
                "risk":   risk_score,
                "reason": verdict.get("reason", ""),
            }
            self.add_event(ev)


# ── Sidebar nav button ──────────────────────────────────────────────────────
class NavButton(QPushButton):
    def __init__(self, icon: str, text: str, tokens: dict, parent=None):
        super().__init__(parent)
        self.icon_name = icon
        self.text_label = text
        self._tokens = tokens
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(42)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_tokens(self, tokens: dict):
        self._tokens = tokens
        self.update()

    def enterEvent(self, e):
        self.update()

    def leaveEvent(self, e):
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        t = self._tokens
        active = self.isChecked()
        hover = self.underMouse()
        r = QRectF(1, 2, self.width() - 2, self.height() - 4)
        if active:
            # Match the console's other orange controls (hero / verifCard /
            # verifyBtn / ctaBtn): a muted paprika gradient with a soft
            # translucent-white rim — NOT the old neobrutalist black border.
            grad = QLinearGradient(r.topLeft(), r.bottomLeft())
            grad.setColorAt(0.0, _qcolor("#c96a2f"))
            grad.setColorAt(1.0, _qcolor("#a4521d"))
            p.setPen(QPen(QColor(255, 255, 255, 60), 1))
            p.setBrush(QBrush(grad))
            p.drawRoundedRect(r, 12, 12)
        elif hover:
            # Frosted highlight to match the glass surfaces, not an opaque chip.
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor(255, 255, 255, 20)))
            p.drawRoundedRect(r, 12, 12)
        txt_col = _qcolor("#ffffff" if active
                          else t["text"] if hover else t.get("text_muted", "#888"))
        ic_col = txt_col
        paint_icon(p, QRectF(16, r.center().y() - 9, 18, 18), self.icon_name, ic_col, 1.8)
        p.setPen(txt_col)
        # Draw with a crisp pixel size and the real bundled Unbounded faces
        # (700 / 500) rather than a point-size + synthesized bold — the synth
        # bold at a fractional point size is what made the label look muddy.
        f = QFont(t.get("font", "Segoe UI"))
        f.setPixelSize(13)
        f.setWeight(QFont.Weight.Bold if active else QFont.Weight.Medium)
        p.setFont(f)
        p.drawText(QRectF(44, 0, self.width() - 50, self.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   self.text_label)
        p.end()


# ── Window control button (min / close / refresh) ───────────────────────────
class CtrlButton(QPushButton):
    def __init__(self, icon: str, tokens: dict, danger=False, parent=None):
        super().__init__(parent)
        self.icon_name = icon
        self._tokens = tokens
        self._danger = danger
        # 44×44 minimum hit target. The glyph stays its old size and is simply
        # centred in a bigger button, so the top bar looks the same while being
        # reachable by an imprecise pointer.
        self.setFixedSize(44, 44)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def set_tokens(self, tokens):
        self._tokens = tokens
        self.update()

    def enterEvent(self, e): self.update()
    def leaveEvent(self, e): self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        t = self._tokens
        box = QRectF(2, 2, self.width() - 4, self.height() - 4)
        if self.underMouse():
            p.setPen(Qt.PenStyle.NoPen)
            hl = BAD_RED if self._danger else t.get("panel3", t.get("panel"))
            p.setBrush(QBrush(_qcolor(hl)))
            p.drawRoundedRect(box, 9, 9)
            col = _qcolor("#FFFFFF" if self._danger else t["text"])
        else:
            col = _qcolor(t.get("text_muted", "#888"))
        if self.hasFocus():
            # Keyboard users need to see where they are, not just hover users.
            p.setPen(QPen(_qcolor(t.get("accent", "#ff7a2e")), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(box, 9, 9)
            col = _qcolor(t["text"])
        cx, cy = self.width() / 2, self.height() / 2
        paint_icon(p, QRectF(cx - 8, cy - 9, 16, 18), self.icon_name, col, 1.6)
        p.end()

    def focusInEvent(self, e):
        self.update()
        super().focusInEvent(e)

    def focusOutEvent(self, e):
        self.update()
        super().focusOutEvent(e)


# ── The console window ──────────────────────────────────────────────────────
class DashboardWindow(QWidget):
    refresh_requested = pyqtSignal()
    closed           = pyqtSignal()
    sign_in_requested  = pyqtSignal()   # D1 — the shell owns the auth windows
    sign_out_requested = pyqtSignal()

    # Set True for real Windows "acrylic" backdrop blur behind the window (Win10+
    # / Win11).  Off by default: it can make dragging the frameless window laggy
    # on some systems, and the self-contained glass already matches the reference.
    GLASS_ACRYLIC = False

    def __init__(self, fox_widget=None, settings: FoxSettings | None = None,
                 sprite_sheet_path: str | None = None,
                 client: FoxyClient | None = None, parent=None):
        super().__init__(parent)
        self.fox_widget = fox_widget
        self.settings = settings or FoxSettings()
        self.client = client or FoxyClient(self.settings, parent=self)
        # sprite sheet for the little Foxy portrait on the verification card
        self._sprite_path = sprite_sheet_path or resource_path("ultimate_fox_spritesheet.png")

        # ── live state ──
        # Session-live counters give instant feedback on UDP events; the tiles are
        # authoritative from the backend (/v1/stats + /v1/verify), polled below.
        # No local score/block-height/hash synthesis — those are the real chain's.
        self._start_ts       = time.time()
        self._stats_answered = False   # has /v1/stats ever replied?
        self._logs_total     = 0
        self._flagged_total  = 0
        self._connected      = None
        self._org_name       = ""       # real org from /v1/health — never faked
        self._signed_in      = False    # session state, pushed in by the shell
        self._drag_pos       = QPoint()
        # Live ApiWorkers, tracked so replaced polls can't leak threads and
        # closeEvent can wait on everything in flight (see foxy_client.spawn_worker).
        self._workers: set = set()
        self._poll_workers: set = set()

        # NOTE: deliberately NOT a Qt.WindowType.Tool window.  A Tool window is a
        # non-activating auxiliary palette: on Windows it refuses to come to the
        # foreground above the currently-active app, so "Open Dashboard" looked
        # like it did nothing (the window existed and rendered, but stayed hidden
        # behind whatever the user was working in).  A normal top-level window
        # plus the explicit raise/activate in show_animated() fixes that.
        #
        # It is a *normal* window, NOT always-on-top: the console is a full app
        # surface, so it must sit in the regular z-order (the user can click
        # another app to send it behind) and minimize to the taskbar like any
        # other window.  WindowMinimizeButtonHint + WindowSystemMenuHint give a
        # frameless window a real taskbar button + minimize/restore on Windows.
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowSystemMenuHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("Foxy Audit — Auditor Console")

        scr = QApplication.primaryScreen().geometry()
        self.setFixedSize(min(1140, int(scr.width() * 0.88)),
                          min(710, int(scr.height() * 0.9)))

        tokens = _clay_tokens()
        self._build(tokens)
        self.apply_theme(tokens)
        self._init_home()            # D4: Home's workers + activity re-tick
        self._init_threats()         # D5: Threats page state
        self._init_ledger()          # D5: Ledger page state
        self._init_verify()          # D6
        self._init_export()          # D8
        self._init_access()          # D9
        self._init_policy()          # D7
        self._init_billing()         # D10
        self._init_settings()        # D11a
        self._init_chrome()          # D3: banner, bell, palette, shortcuts, toasts
        self._sync_title()

        self._tick = QTimer(self)
        self._tick.timeout.connect(self._on_tick)
        self._tick.start(1000)
        self._refresh_stats()

        # Poll the backend for the authoritative tile values (/v1/stats + /v1/verify).
        # /v1/verify recomputes the whole chain, so keep the cadence gentle (15s),
        # not on the 1s animation tick.
        self._backend_poll = QTimer(self)
        self._backend_poll.timeout.connect(self._refresh_backend)
        self._backend_poll.start(15000)
        QTimer.singleShot(400, self._refresh_backend)   # initial fill shortly after open
        QTimer.singleShot(400, self._refresh_org)       # real org name for the sidebar
        QTimer.singleShot(800, self.refresh_home)       # D4 Home sections

    # ── construction ────────────────────────────────────────────────────────
    def _build(self, t: dict):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.shell = QFrame()
        self.shell.setObjectName("shell")
        outer.addWidget(self.shell)

        h = QHBoxLayout(self.shell)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        h.addWidget(self._build_sidebar(t))

        main = QVBoxLayout()
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)
        main.addWidget(self._build_topbar(t))

        self.stack = QStackedWidget()
        # D3: the web's nine sections in its order, plus the two desktop-only
        # extras. Existing pages are REMAPPED, not duplicated — Overview→Home,
        # Threat Analytics→Threats, Audit Log→Ledger — and sections whose real
        # pages land in later phases get an honest stub, never fake data.
        self._page_index: dict[str, int] = {}
        self._home = HomeSections(self)
        self._threats = ThreatsSections(self)
        self._ledger = LedgerSections(self)
        self._verify = VerifySections(self)
        self._export = ExportSections(self)
        self._access = AccessSections(self)
        self._policy = PolicySections(self)
        self._billing = BillingSections(self)
        self._settings_page = SettingsSections(self)
        builders = {
            "home": lambda t: self._home.build(self._page_overview(t)),
            "threats": lambda t: self._threats.build(t),
            "ledger": lambda t: self._ledger.build(t, self._live_capture(t)),
            "verify": lambda t: self._verify.build(t),
            "export": lambda t: self._export.build(t),
            "access": lambda t: self._access.build(t),
            "policy": lambda t: self._policy.build(t),
            "billing": lambda t: self._billing.build(t),
            "settings": lambda t: self._settings_page.build(t),
            "system": self._page_system,
            "sandbox": self._page_sandbox,
        }
        for section_id, _label, title, _crumb, _icon in ALL_SECTIONS:
            build = builders.get(section_id)
            page = build(t) if build else self._page_stub(t, section_id, title)
            self._page_index[section_id] = self.stack.count()
            self.stack.addWidget(page)
        self.stack.currentChanged.connect(self._sync_title)
        # The announcement strip sits between the top bar and the page, exactly
        # where the web puts it — it must never cover content.
        self._banner_slot = QVBoxLayout()
        self._banner_slot.setContentsMargins(22, 10, 22, 0)
        main.addLayout(self._banner_slot)
        main.addWidget(self.stack, stretch=1)
        h.addLayout(main, stretch=1)

    def _build_sidebar(self, t: dict) -> QWidget:
        self.sidebar = QFrame()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(212)
        v = QVBoxLayout(self.sidebar)
        v.setContentsMargins(16, 18, 16, 16)
        v.setSpacing(6)

        brand = QHBoxLayout()
        brand.setSpacing(10)
        self.logo = QLabel()
        self.logo.setObjectName("logo")
        self.logo.setFixedSize(30, 30)
        brand.addWidget(self.logo)
        name_box = QVBoxLayout()
        name_box.setSpacing(0)
        self.brand_name = QLabel("Foxy Audit")
        self.brand_name.setObjectName("brandName")
        self.brand_sub = QLabel("AUDITOR CONSOLE")
        self.brand_sub.setObjectName("brandSub")
        name_box.addWidget(self.brand_name)
        name_box.addWidget(self.brand_sub)
        brand.addLayout(name_box)
        brand.addStretch()
        v.addLayout(brand)
        v.addSpacing(18)

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self.nav_buttons = []
        self._nav_by_id: dict[str, NavButton] = {}
        for i, (section_id, label, _title, _crumb, icon) in enumerate(ALL_SECTIONS):
            if section_id == EXTRA_SECTIONS[0][0]:
                # The desktop-only extras sit below the web's nine, separated.
                v.addStretch()
                sep = QLabel("DESKTOP")
                sep.setObjectName("navMeta")
                v.addWidget(sep)
            btn = NavButton(icon, label, t)
            btn.setAccessibleName(label)
            btn.clicked.connect(lambda _c, sid=section_id: self.go(sid))
            self.nav_group.addButton(btn, i)
            self.nav_buttons.append(btn)
            self._nav_by_id[section_id] = btn
            v.addWidget(btn)
        self.nav_buttons[0].setChecked(True)

        # Unseen-breach pip rides the Threats entry (mirrored in the top bar).
        self.threat_pip = Pip(parent=self._nav_by_id["threats"])
        self.threat_pip.move(178, 13)
        v.addSpacing(4)

        self.org_lbl = QLabel("ORGANIZATION")
        self.org_lbl.setObjectName("navMeta")
        # Real org name arrives from GET /v1/health; "—" until then — never faked.
        self.org_val = QLabel("—")
        self.org_val.setObjectName("navMetaVal")
        v.addWidget(self.org_lbl)
        v.addWidget(self.org_val)

        # ── user area (D1): who is signed in, and the way out ──
        self.user_lbl = QLabel("SIGNED IN")
        self.user_lbl.setObjectName("navMeta")
        self.user_val = QLabel("not signed in")
        self.user_val.setObjectName("navMetaVal")
        self.user_val.setWordWrap(True)
        self.auth_btn = QPushButton("Sign in")
        self.auth_btn.setObjectName("navAuthBtn")
        self.auth_btn.setMinimumHeight(34)
        self.auth_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.auth_btn.clicked.connect(self._on_auth_clicked)
        v.addSpacing(10)
        v.addWidget(self.user_lbl)
        v.addWidget(self.user_val)
        v.addWidget(self.auth_btn)
        return self.sidebar

    # ── session state, driven by omni_fox (D1) ──
    def on_signed_in(self, me: dict):
        who = (me or {}).get("email") or ""
        role = (me or {}).get("role") or ""
        self.user_val.setText(f"{who}\n{role}" if role else (who or "signed in"))
        self.auth_btn.setText("Sign out")
        self._signed_in = True
        self._apply_identity(me or {})      # D3 top-bar chip + palette org id
        self._apply_home_identity(me or {})  # D4 greeting
        self._refresh_chrome()              # notifications only exist with a session

    def on_signed_out(self):
        self.user_val.setText("not signed in")
        self.auth_btn.setText("Sign in")
        self._signed_in = False
        self._apply_identity({})
        if hasattr(self, "notif_panel"):
            self.notif_panel.hide()
            self.notif_pip.set_count(0)

    def set_signing_out(self, busy: bool):
        """Sign-out is a network call — say so, and don't let it be fired twice."""
        self.auth_btn.setEnabled(not busy)
        if busy:
            self.auth_btn.setText("Signing out…")
        elif not self._signed_in:
            self.auth_btn.setText("Sign in")

    def _on_auth_clicked(self):
        self.sign_out_requested.emit() if self._signed_in else self.sign_in_requested.emit()

    def _build_topbar(self, t: dict) -> QWidget:
        self.topbar = QFrame()
        self.topbar.setObjectName("topbar")
        self.topbar.setFixedHeight(58)
        h = QHBoxLayout(self.topbar)
        h.setContentsMargins(22, 0, 14, 0)
        h.setSpacing(12)

        titles = QVBoxLayout()
        titles.setSpacing(0)
        self.page_title = QLabel("Overview")
        self.page_title.setObjectName("pageTitle")
        self.page_crumb = QLabel("Foxy Audit · Home")
        self.page_crumb.setObjectName("navMeta")
        titles.addWidget(self.page_title)
        titles.addWidget(self.page_crumb)
        h.addLayout(titles)
        h.addStretch()

        # ── D3 chrome: live dot · notifications bell (+pips) · user chip ──
        self.live_dot = LiveDot()
        h.addWidget(self.live_dot)

        self.notif_btn = CtrlButton("log", t)
        self.notif_btn.setAccessibleName("Notifications")
        self.notif_btn.setToolTip("Notifications")
        self.notif_btn.clicked.connect(self._toggle_notifications)
        self.notif_pip = Pip(parent=self.notif_btn)
        h.addWidget(self.notif_btn)

        # D5-P10: the web's topBreachBtn — a shield icon carrying a bare count
        # pip (html:885-888), not a labelled "⚠ N breaches" chip. D3 spelled
        # the count out to tell it apart from the bell's badge; the site simply
        # gives the two buttons different icons, which is enough. The button
        # itself is always present (the site only hides the PIP), so the top
        # bar does not reflow the moment a breach lands.
        self.top_breach_btn = CtrlButton("shield", t)
        self.top_breach_btn.setAccessibleName("Open threats")
        self.top_breach_btn.setToolTip("Threats")
        self.top_breach_btn.clicked.connect(lambda: self.go("threats"))
        self.breach_pip_widget = Pip(parent=self.top_breach_btn)
        h.insertWidget(h.count() - 1, self.top_breach_btn)

        self.user_btn = QPushButton("?")
        self.user_btn.setObjectName("userChip")
        self.user_btn.setFixedSize(46, 44)
        self.user_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.user_btn.setAccessibleName("Account menu")
        self.user_btn.setToolTip("Account")
        self.user_btn.clicked.connect(self._show_user_menu)
        h.addWidget(self.user_btn)

        self.refresh_btn = CtrlButton("refresh", t)
        self.refresh_btn.setAccessibleName("Refresh")
        self.refresh_btn.clicked.connect(self._on_refresh_clicked)
        h.addWidget(self.refresh_btn)
        self.min_btn = CtrlButton("min", t)
        self.min_btn.clicked.connect(self.showMinimized)
        self.close_btn = CtrlButton("close", t, danger=True)
        self.close_btn.clicked.connect(self.close_animated)
        h.addWidget(self.min_btn)
        h.addWidget(self.close_btn)
        return self.topbar

    # ── pages ──
    def _feature_tile(self, text: str, obj: str, on_click) -> QPushButton:
        tile = QPushButton(text)
        tile.setObjectName(obj)
        tile.setCursor(Qt.CursorShape.PointingHandCursor)
        tile.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        tile.clicked.connect(lambda: on_click())
        _glass_shadow(tile, 8, 22, 80)
        return tile

    def _page_overview(self, t: dict) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(24, 20, 24, 22)
        v.setSpacing(16)

        # ── in-page header (mirrors the website "vibe check" header) ──
        # Lifted into its own widget so D4 can slot the onboarding stepper and
        # the coverage card between it and the hero, exactly as the web does.
        self.ov_head = QWidget()
        head = QHBoxLayout(self.ov_head)
        head.setContentsMargins(0, 0, 0, 0)
        htxt = QVBoxLayout()
        htxt.setSpacing(3)
        # D4: web parity — the live page head is eyebrow "Compliance overview",
        # "Yo <name>. here's the vibe check." and a stats subtitle (html:926-936).
        self.ov_eyebrow = QLabel("● COMPLIANCE OVERVIEW")
        self.ov_eyebrow.setObjectName("eyebrow")
        self.ov_h1 = QLabel("Yo there. here's the vibe check.")
        self.ov_h1.setObjectName("h1Head")
        # Real counts once /v1/stats answers; an em dash until then, never a 0.
        self.ov_sub = QLabel("—")
        self.ov_sub.setObjectName("subHead")
        # D4 aliases: one head, two names, so the Home handlers read as the web.
        self.home_greeting, self.home_sub = self.ov_h1, self.ov_sub
        htxt.addWidget(self.ov_eyebrow)
        htxt.addWidget(self.ov_h1)
        htxt.addWidget(self.ov_sub)
        head.addLayout(htxt)
        head.addStretch()
        self.export_btn = QPushButton("Export passport")
        self.export_btn.setObjectName("ctaBtn")
        self.export_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        head.addWidget(self.export_btn, 0, Qt.AlignmentFlag.AlignTop)
        self.export_btn.clicked.connect(lambda: self.go("export"))
        self.home_export_btn = self.export_btn

        # ── hero + feature tiles ──
        hero_row = QHBoxLayout()
        hero_row.setSpacing(16)
        self.hero = QFrame()
        self.hero.setObjectName("hero")
        hl = QVBoxLayout(self.hero)
        hl.setContentsMargins(26, 22, 26, 24)
        hl.setSpacing(6)
        htop = QHBoxLayout()
        htag = QLabel("ORG-WIDE AI HEALTH")
        htag.setObjectName("heroTag")
        self.live_badge = QLabel("● —")   # LIVE/OFFLINE tracks the real connection state
        self.live_badge.setObjectName("liveBadge")
        htop.addWidget(htag)
        htop.addStretch()
        htop.addWidget(self.live_badge)
        self.hero_num = QLabel("—")       # real total_logged arrives from /v1/stats
        self.hero_num.setObjectName("heroNum")
        # D5-P8: the headline is total_logged now, so the caption is the
        # web's (html:1011). "compliance score" over a count was the
        # mismatch this phase set out to remove.
        hfoot = QLabel("interactions logged today")
        hfoot.setObjectName("heroFoot")
        hl.addLayout(htop)
        hl.addStretch()
        hl.addWidget(self.hero_num)
        hl.addWidget(hfoot)
        # D4: the web pairs the hero number with a 30-day volume sparkline and
        # the delta against the preceding week — a score with no direction is
        # half a signal.
        spark_row = QHBoxLayout()
        spark_row.setSpacing(10)
        self.hero_spark_chart = FoxChart("sparkline", height=28)
        self.hero_spark_chart.setMaximumWidth(210)
        self.hero_delta_lbl = QLabel("")
        # heroFoot's near-black ink is tuned for the light end of the hero
        # gradient; the delta sits at the dark end, so it gets its own colour.
        self.hero_delta_lbl.setStyleSheet(
            f"color: rgba(255,255,255,235); font-family: '{_pick_font('mono')}';"
            f" font-size: 10px; font-weight: 800; background: transparent;")
        spark_row.addWidget(self.hero_spark_chart, 1)
        spark_row.addWidget(self.hero_delta_lbl, 0,
                            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom)
        hl.addLayout(spark_row)
        _glass_shadow(self.hero, 10, 26, 110)
        hero_row.addWidget(self.hero, stretch=3)

        tiles = QVBoxLayout()
        tiles.setSpacing(14)
        # Route by SECTION ID, never by stack index: D3 reordered the stack, so
        # the old literal 1/2 silently sent these tiles to the wrong pages.
        self.tile_threats = self._feature_tile(
            "Blind Audit\nLog", "tileBlue", lambda: self.go("ledger"))
        self.tile_system = self._feature_tile(
            "System\nHealth", "tilePink", lambda: self.go("system"))
        tiles.addWidget(self.tile_threats)
        tiles.addWidget(self.tile_system)
        hero_row.addLayout(tiles, stretch=2)
        v.addLayout(hero_row)

        # ── stats row (4 KPIs) ──
        kpis = QHBoxLayout()
        kpis.setSpacing(14)
        # D5-P1: the web's four Home tiles, in its order and wording
        # (html:1029-1034). The pre-D4 row ("Interactions / Policy Breaches /
        # Time to Verdict / Ledger Blocks") diverged on three of four, and
        # "Ledger Blocks" had no counterpart on the site at all — the ledger
        # count still shows on the verification card, which is where the web
        # puts it. Values come from home_data.stat_row().
        self.kpi_tiles = []
        for label, value, tone in hd.stat_row(None, None):
            tile = KpiTile(t, label, value, "", _STAT_ACCENT.get(tone))
            self.kpi_tiles.append(tile)
            kpis.addWidget(tile)
        # Named handles for the two tiles other code writes through.
        self.kpi_breaches, self.kpi_alerts = self.kpi_tiles[0], self.kpi_tiles[1]
        self.kpi_clean, self.kpi_verdict = self.kpi_tiles[2], self.kpi_tiles[3]
        v.addLayout(kpis)

        # ── verification card (website credit-card style) + ledger integrity ──
        body = QHBoxLayout()
        body.setSpacing(16)

        verif_col = QVBoxLayout()
        verif_col.setSpacing(11)
        verif_cap = QLabel("VERIFICATION CARD")
        verif_cap.setObjectName("sectionCap")
        verif_col.addWidget(verif_cap)

        # D4: the web card flips to a "Quick ledger check" back face, so the
        # long-standing "tap to verify ↻" hint finally does something.
        self.verif_card = FlipCard()
        self.verif_card.setObjectName("verifCard")
        self.verif_card.setMinimumHeight(186)
        verif_front = QWidget()
        vc = QVBoxLayout(verif_front)
        vc.setContentsMargins(22, 20, 22, 20)
        vc.setSpacing(0)
        vtop = QHBoxLayout()
        chip = QLabel()
        chip.setObjectName("verifFox")
        chip.setFixedSize(50, 50)
        chip.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        _foxpix = self._fox_pixmap(50)
        if _foxpix is not None:
            chip.setPixmap(_foxpix)
        vhint = QLabel("tap to verify ↻")
        vhint.setObjectName("verifHint")
        vtop.addWidget(chip)
        vtop.addStretch()
        vtop.addWidget(vhint)
        vc.addLayout(vtop)
        vc.addStretch()
        veye = QLabel("AUDIT HEALTH")
        veye.setObjectName("verifEye")
        self.verif_num = QLabel("0 events")
        self.verif_num.setObjectName("verifNum")
        vc.addWidget(veye)
        vc.addWidget(self.verif_num)
        vc.addStretch()
        vbot = QHBoxLayout()
        self.verif_hash = QLabel("•••• •••• •••• ••••")
        self.verif_hash.setObjectName("verifBottom")
        vfoxy = QLabel("FOXY")
        vfoxy.setObjectName("verifBottom")
        vbot.addWidget(self.verif_hash)
        vbot.addStretch()
        vbot.addWidget(vfoxy)
        vc.addLayout(vbot)
        self.verif_card.add_faces(verif_front, quick_check_face(self))
        verif_col.addWidget(self.verif_card)
        verif_col.addStretch()
        body.addLayout(verif_col, stretch=3)

        right = QVBoxLayout()
        right.setSpacing(16)
        self.chain_card = Card(t, "Ledger integrity")
        state_row = QHBoxLayout()
        self.chain_icon = QLabel()
        self.chain_icon.setFixedSize(16, 16)
        # No verdict is claimed until /v1/verify has actually run.
        self.chain_state = QLabel("—")
        self.chain_state.setObjectName("chainState")
        state_row.addWidget(self.chain_icon)
        state_row.addWidget(self.chain_state)
        state_row.addStretch()
        self.chain_meta = QLabel("awaiting first verification")
        self.chain_meta.setObjectName("chainMeta")
        self.chain_hash = QLabel("root —")
        self.chain_hash.setObjectName("chainHash")
        self.chain_hash.setWordWrap(True)
        self.verify_btn = QPushButton("Verify chain")
        self.verify_btn.setObjectName("verifyBtn")
        self.verify_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.verify_btn.clicked.connect(self._verify_chain)
        self.chain_card.body.addLayout(state_row)
        self.chain_card.body.addWidget(self.chain_meta)
        self.chain_card.body.addWidget(self.chain_hash)
        self.chain_card.body.addWidget(self.verify_btn)
        right.addWidget(self.chain_card)
        right.addStretch()
        body.addLayout(right, stretch=2)
        v.addLayout(body, stretch=1)
        return page


    def _live_capture(self, t: dict) -> QWidget:
        """The blind audit log the fox streams into over UDP.

        No web counterpart and not part of the D5 spec, but real working
        functionality: the paginated ledger above it is a page of server state,
        while this is interactions arriving as they happen on this machine. It
        lives here rather than in ledger_page because Card and AuditTable are
        defined in this module."""
        wrap = QWidget()
        col = QVBoxLayout(wrap)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(8)
        head = QHBoxLayout()
        cap = QLabel("LIVE CAPTURE")
        cap.setObjectName("tableCap")
        self.audit_count = QLabel("0 records")
        self.audit_count.setObjectName("tableCount")
        head.addWidget(cap)
        head.addStretch()
        head.addWidget(self.audit_count)
        col.addLayout(head)
        self.table_card = Card(t)
        self.table = AuditTable(t)
        self.table_card.body.addWidget(self.table, stretch=1)
        col.addWidget(self.table_card, stretch=1)
        return wrap

    def _page_system(self, t: dict) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(22, 20, 22, 20)
        v.setSpacing(16)
        row = QHBoxLayout()
        row.setSpacing(16)

        self.vitals_card = Card(t, "Host Resources")
        self.m_cpu = MiniMeter("CPU", t)
        self.m_ram = MiniMeter("Memory", t)
        self.m_batt = MiniMeter("Battery", t, higher_is_better=True)
        for m in (self.m_cpu, self.m_ram, self.m_batt):
            self.vitals_card.body.addWidget(m)
        self.vitals_card.body.addStretch()
        row.addWidget(self.vitals_card, stretch=1)

        self.conn_card = Card(t, "Backend Connection")
        self.conn_state = QLabel("Connecting…")
        self.conn_state.setObjectName("connState")
        self.conn_url = QLabel(self.settings.backend_url())
        self.conn_url.setObjectName("connUrl")
        self.conn_url.setWordWrap(True)
        self.uptime_lbl = QLabel("Uptime 00:00:00")
        self.uptime_lbl.setObjectName("connUrl")
        self.conn_card.body.addWidget(self.conn_state)
        self.conn_card.body.addWidget(self.conn_url)
        self.conn_card.body.addWidget(self.uptime_lbl)
        self.conn_card.body.addStretch()
        row.addWidget(self.conn_card, stretch=1)
        v.addLayout(row)
        v.addStretch()
        return page

    # ── Verification Sandbox (Core Requirement #3) ───────────────────────────
    def _page_sandbox(self, t: dict) -> QWidget:
        """Zero-knowledge proof sandbox: compute hashes locally, compare to ledger.

        The SHA-256 computation runs entirely in the client (no server call).
        Only the 'Compare to Ledger' button makes a network request—to fetch
        the stored hashes for the given seq number from GET /v1/logs/{seq}.
        """
        from PyQt6.QtWidgets import QTextEdit, QLineEdit, QGroupBox
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(22, 20, 22, 20)
        v.setSpacing(16)

        # Header
        hdr = QHBoxLayout()
        cap = QLabel("VERIFICATION SANDBOX")
        cap.setObjectName("tableCap")
        sub = QLabel("Compute hashes locally — no server call — then compare to the immutable ledger.")
        sub.setObjectName("tableCount")
        hdr.addWidget(cap)
        hdr.addStretch()
        hdr.addWidget(sub)
        v.addLayout(hdr)

        # Input group
        inputs_row = QHBoxLayout()
        inputs_row.setSpacing(14)

        prompt_box = QVBoxLayout()
        prompt_lbl = QLabel("ORIGINAL PROMPT")
        prompt_lbl.setObjectName("kpiLabel")
        self.sb_prompt = QTextEdit()
        self.sb_prompt.setPlaceholderText("Paste the original prompt text here…")
        self.sb_prompt.setFixedHeight(130)
        prompt_box.addWidget(prompt_lbl)
        prompt_box.addWidget(self.sb_prompt)
        inputs_row.addLayout(prompt_box)

        response_box = QVBoxLayout()
        response_lbl = QLabel("ORIGINAL RESPONSE")
        response_lbl.setObjectName("kpiLabel")
        self.sb_response = QTextEdit()
        self.sb_response.setPlaceholderText("Paste the original response text here…")
        self.sb_response.setFixedHeight(130)
        response_box.addWidget(response_lbl)
        response_box.addWidget(self.sb_response)
        inputs_row.addLayout(response_box)
        v.addLayout(inputs_row)

        # Compute button
        compute_btn = QPushButton("Compute Hashes Locally")
        compute_btn.setObjectName("verifyBtn")
        compute_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        compute_btn.setFixedHeight(40)
        compute_btn.clicked.connect(self._sb_compute)
        v.addWidget(compute_btn)

        # Hash display
        hash_row = QHBoxLayout()
        hash_row.setSpacing(12)
        ph_box = QVBoxLayout()
        ph_lbl = QLabel("PROMPT HASH (SHA-256)")
        ph_lbl.setObjectName("kpiLabel")
        self.sb_prompt_hash = QLabel("—")
        self.sb_prompt_hash.setObjectName("chainHash")
        self.sb_prompt_hash.setWordWrap(True)
        ph_box.addWidget(ph_lbl)
        ph_box.addWidget(self.sb_prompt_hash)
        hash_row.addLayout(ph_box)

        rh_box = QVBoxLayout()
        rh_lbl = QLabel("RESPONSE HASH (SHA-256)")
        rh_lbl.setObjectName("kpiLabel")
        self.sb_response_hash = QLabel("—")
        self.sb_response_hash.setObjectName("chainHash")
        self.sb_response_hash.setWordWrap(True)
        rh_box.addWidget(rh_lbl)
        rh_box.addWidget(self.sb_response_hash)
        hash_row.addLayout(rh_box)
        v.addLayout(hash_row)

        # Ledger comparison row
        ledger_row = QHBoxLayout()
        ledger_row.setSpacing(10)
        seq_lbl = QLabel("LEDGER SEQ #")
        seq_lbl.setObjectName("kpiLabel")
        self.sb_seq = QLineEdit()
        self.sb_seq.setPlaceholderText("e.g. 3")
        self.sb_seq.setFixedWidth(110)
        self.sb_compare_btn = QPushButton("Compare to Ledger")
        self.sb_compare_btn.setObjectName("verifyBtn")
        self.sb_compare_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sb_compare_btn.setFixedHeight(38)
        self.sb_compare_btn.clicked.connect(self._sb_compare)
        ledger_row.addWidget(seq_lbl)
        ledger_row.addWidget(self.sb_seq)
        ledger_row.addWidget(self.sb_compare_btn)
        ledger_row.addStretch()
        v.addLayout(ledger_row)

        # Result display
        self.sb_result = QLabel("")
        self.sb_result.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sb_result.setWordWrap(True)
        self.sb_result.setMinimumHeight(60)
        v.addWidget(self.sb_result)
        v.addStretch()
        return page

    # ── Sandbox logic ──
    def _sb_compute(self):
        """Compute SHA-256 hashes locally — zero server calls."""
        prompt = self.sb_prompt.toPlainText()
        response = self.sb_response.toPlainText()
        ph = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        rh = hashlib.sha256(response.encode("utf-8")).hexdigest()
        self.sb_prompt_hash.setText(ph)
        self.sb_response_hash.setText(rh)
        self.sb_result.setText("Hashes computed locally. Enter a Ledger Seq # and click Compare.")
        self.sb_result.setStyleSheet(f"color: {INFO_BLUE}; font-size: 14px; font-weight: 700;")

    def _sb_compare(self):
        """Fetch GET /v1/logs/{seq} and compare stored hashes to locally computed ones."""
        ph = self.sb_prompt_hash.text()
        rh = self.sb_response_hash.text()
        if ph in ("", "—") or rh in ("", "—"):
            self.sb_result.setText("⚠ Compute hashes first before comparing.")
            self.sb_result.setStyleSheet(f"color: {WARN_AMBER}; font-size: 14px; font-weight: 700;")
            return
        seq_text = self.sb_seq.text().strip()
        if not seq_text.isdigit():
            self.sb_result.setText("⚠ Enter a valid sequence number.")
            self.sb_result.setStyleSheet(f"color: {WARN_AMBER}; font-size: 14px; font-weight: 700;")
            return
        url = self.settings.backend_url()
        key = self.settings.org_api_key()
        if not (url and key):
            self.sb_result.setText("⚠ Configure Backend URL and API Key in Settings first.")
            self.sb_result.setStyleSheet(f"color: {WARN_AMBER}; font-size: 14px; font-weight: 700;")
            return
        self.sb_compare_btn.setText("Fetching…")
        self.sb_compare_btn.setEnabled(False)
        self._sb_seq = int(seq_text)
        spawn_worker(self.client, "GET", f"/v1/logs/{self._sb_seq}", parent=self,
                     on_ok=self._sb_on_fetched, on_err=self._sb_on_fetch_failed,
                     track=self._workers)

    def _sb_on_fetched(self, data: dict):
        """Compare the ledger's stored hashes to the locally computed ones."""
        stored_ph = data.get("prompt_hash", "")
        stored_rh = data.get("response_hash", "")
        local_ph = self.sb_prompt_hash.text()
        local_rh = self.sb_response_hash.text()
        if stored_ph == local_ph and stored_rh == local_rh:
            self._sb_on_result(True, "")
        else:
            detail = []
            if stored_ph != local_ph:
                detail.append(f"Prompt hash: ledger={stored_ph[:16]}… local={local_ph[:16]}…")
            if stored_rh != local_rh:
                detail.append(f"Response hash: ledger={stored_rh[:16]}… local={local_rh[:16]}…")
            self._sb_on_result(False, "\n".join(detail))

    def _sb_on_fetch_failed(self, err: str):
        # Only an HTTP status means the ledger actually answered; a transport
        # failure must not claim the record is missing.
        if err.startswith("HTTP "):
            self._sb_on_result(False, f"{err} — seq {self._sb_seq} not found")
        else:
            self._sb_on_result(False, f"Error: {err}")

    def _sb_on_result(self, matched: bool, detail: str):
        self.sb_compare_btn.setText("Compare to Ledger")
        self.sb_compare_btn.setEnabled(True)
        if matched:
            self.sb_result.setText(
                "✓  MATCH — Interaction certified authentic.\n"
                "The hashes you computed locally are identical to the immutable ledger."
            )
            self.sb_result.setStyleSheet(
                f"color: {OK_GREEN}; font-size: 16px; font-weight: 800;"
                " border: 2px solid " + OK_GREEN + "; border-radius: 8px; padding: 14px;"
            )
        else:
            self.sb_result.setText(
                "✗  MISMATCH — Log may have been tampered with.\n" + detail
            )
            self.sb_result.setStyleSheet(
                f"color: {BAD_RED}; font-size: 16px; font-weight: 800;"
                " border: 2px solid " + BAD_RED + "; border-radius: 8px; padding: 14px;"
            )

    # ── theming ──────────────────────────────────────────────────────────────
    def apply_theme(self, t: dict):
        # The auditor console wears a fixed glassmorphic skin over the original
        # clay/paprika palette, so any chat theme passed in is ignored.
        t = _clay_tokens()
        self.shell.setStyleSheet(
            console_shell_qss(t, acrylic=getattr(self, "_acrylic_on", False)))
        self.logo.setPixmap(self._monogram(t))
        self._paint_chain_icon(True)
        self._restyle_conn(t)

        for b in self.nav_buttons:
            b.set_tokens(t)
        for c in (self.refresh_btn, self.min_btn, self.close_btn):
            c.set_tokens(t)
        for k in self.kpi_tiles:
            k.restyle(t)
        for card in (self.chain_card,
                     self.table_card, self.vitals_card, self.conn_card):
            card.restyle(t)
        if self.verif_card.graphicsEffect() is None:
            _glass_shadow(self.verif_card, 12, 28, 110)
        self.table.restyle(t)
        for m in (self.m_cpu, self.m_ram, self.m_batt):
            m.set_tokens(t)

    def _fox_pixmap(self, size: int):
        """A little Foxy portrait — the first idle sprite cell (192×208), scaled."""
        pm = QPixmap(self._sprite_path)
        if pm.isNull():
            return None
        frame = pm.copy(0, 0, 192, 208)
        return frame.scaled(size, size,
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation)

    def _monogram(self, t: dict) -> QPixmap:
        pm = QPixmap(30, 30)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(QPen(_qcolor("#000000"), 2))
        p.setBrush(QBrush(_qcolor(t["accent"])))
        p.drawRoundedRect(QRectF(1, 1, 28, 28), 9, 9)
        paint_icon(p, QRectF(5, 5, 20, 20), "shield", _qcolor("#1a0900"), 2.0)
        p.end()
        return pm

    def _paint_chain_icon(self, ok: bool):
        pm = QPixmap(16, 16)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        paint_icon(p, QRectF(0, 0, 16, 16), "link",
                   _qcolor(OK_GREEN if ok else WARN_AMBER), 1.5)
        p.end()
        self.chain_icon.setPixmap(pm)

    def _restyle_conn(self, t: dict):
        if self._connected is True:
            col, txt, badge = OK_GREEN, "Connected", "● LIVE"
        elif self._connected is False:
            col, txt, badge = BAD_RED, "Offline", "● OFFLINE"
        else:
            col, txt, badge = WARN_AMBER, "Connecting", "● —"
        # (top-bar connection badge removed; status still shown on the System page)
        if hasattr(self, "conn_state"):
            self.conn_state.setText(txt)
            self.conn_state.setStyleSheet(
                f"color: {col}; font-size: 14px; font-weight: 700; background: transparent;")
        if hasattr(self, "live_badge"):
            self.live_badge.setText(badge)

    # ── live slots ────────────────────────────────────────────────────────────
    def on_hardware(self, hw: dict):
        if hasattr(self, "m_cpu"):
            self.m_cpu.set_value(hw.get("cpu", 0))
            self.m_ram.set_value(hw.get("ram", 0))
            self.m_batt.set_value(hw.get("battery", 100))

    def on_hash_ok(self, payload: dict):
        # Live UDP event → session counter + table row for instant feedback. The
        # real chain hash for this interaction is computed server-side; a live ping
        # doesn't carry it, so we don't fabricate one (it arrives via the /v1/logs
        # refresh). The tiles are corrected by the next /v1/stats poll.
        self._logs_total += 1
        policy = payload.get("policy", "default")
        self._add_event({
            "time": datetime.now().strftime("%H:%M:%S"),
            "kind": "captured", "policy": policy, "hash": "",
            "tokens": payload.get("tokens", ""), "risk": None,
        })
        self._refresh_stats()

    def on_policy_breach(self, payload: dict):
        self._logs_total += 1
        self._flagged_total += 1
        reason = payload.get("reason", "Policy violation")
        risk = int(payload.get("risk_score", 100))
        policy = payload.get("policy", "default")
        self._add_event({
            "time": datetime.now().strftime("%H:%M:%S"),
            "kind": "breach", "policy": policy, "hash": "",
            "tokens": payload.get("tokens", ""), "risk": risk, "reason": reason,
        })
        self._refresh_stats()

    def set_connected(self, connected: bool | None):
        self._connected = connected
        self._restyle_conn(_clay_tokens())

    # ── event rendering ──
    def _add_event(self, ev: dict):
        self.table.add_event(ev)
        self.audit_count.setText(f"{self.table.rowCount()} records")

    # ── session-live counters (backend poll is authoritative for everything else) ──
    def _refresh_stats(self):
        # Immediate feedback from live UDP events; /v1/stats overwrites this with
        # the real ledger-wide number on the next poll. Only "Breaches stopped"
        # has a live counter — the other three tiles are backend-only
        # measurements and must not be guessed at from the local event stream.
        # ...but only once there is something to report. At startup both
        # counters are 0 and nothing has been measured, so writing them would
        # put "0 breaches stopped" and "0 interactions logged today" on screen
        # as if we had asked. An em dash until a real event or a real answer
        # arrives (owner decision: never show a value we aren't sure about).
        if self._flagged_total or self._stats_answered:
            self.kpi_breaches.set_value(f"{self._flagged_total:,}")
        if self._logs_total or self._stats_answered:
            self.hero_num.setText(f"{self._logs_total:,}")

    # ── real backend tiles (7B) ──
    def _refresh_backend(self):
        """Poll /v1/stats + /v1/verify and drive the tiles from real data."""
        url = self.settings.backend_url()
        key = self.settings.org_api_key()
        if not (url and key):
            return
        if self._poll_workers:
            return  # previous poll still in flight — skip the tick, never stack
        spawn_worker(self.client, "GET", "/v1/stats", parent=self,
                     on_ok=self._on_stats_success, track=self._poll_workers)
        spawn_worker(self.client, "GET", "/v1/verify", parent=self,
                     on_ok=self._on_verify_stats, track=self._poll_workers)

    def _refresh_org(self):
        """Fetch the real org name from GET /v1/health (Bearer mode's only
        org-shaped endpoint) for the sidebar + overview header."""
        url = self.settings.backend_url()
        key = self.settings.org_api_key()
        if not (url and key):
            return
        spawn_worker(self.client, "GET", "/v1/health", timeout=5, parent=self,
                     force_bearer=True,  # /v1/health is Bearer-only on the backend
                     on_ok=self._on_health_info, track=self._workers)

    def _on_health_info(self, data: dict):
        org = data.get("org") if isinstance(data, dict) else None
        if org:
            self._org_name = org
            self.org_val.setText(org)

    def _on_stats_success(self, s: dict):
        self._stats_answered = True
        total = int(s.get("total_logged", 0))
        # Tiles 0/2/3 only. Tile 1 ("Open alerts") is deliberately left to
        # _on_threats, exactly as the site leaves dv[1] to loadThreats
        # (html:2228) — open alerts and all-time breaches are different
        # measurements and neither may stand in for the other.
        rows = hd.stat_row(s, None)
        for index in (0, 2, 3):
            self.kpi_tiles[index].set_value(rows[index][1])
        # D5-P8: the hero headline is the web's `money(s.total_logged)` under
        # "interactions logged today" (html:1010-1011). It used to show
        # clean_rate under "compliance score" while the sparkline beneath it
        # plotted logs_count — a percentage headline over a count trend, which
        # implied a relationship that did not exist. Now both are the count.
        if hasattr(self, "hero_num"):
            self.hero_num.setText(hd.thousands(total))
        if hasattr(self, "verif_num"):
            self.verif_num.setText(f"{total:,} events")
        # D4: the head subtitle is now the web's ("N events logged · M pending
        # grading"), written by _apply_home_stats. The org name lives in the D3
        # top-bar chip and liveness in the LiveDot, so the old
        # "org · synced HH:MM" string was saying what the chrome already says.
        self._apply_home_stats(s)

    def _on_verify_stats(self, v: dict):
        count = int(v.get("count", 0))     # ledger blocks live on the verify card
        intact = bool(v.get("ok", False))
        # `ok=False` alone does not mean a break was found — above 50k rows the
        # server declines the recompute and answers ok=False with no sequence
        # number (see verify_data.chain_skipped). Telling the user "review
        # required" for a check that never ran sends them after nothing.
        skipped = vd.chain_skipped(v)
        anchor = v.get("last_anchor") or {}
        root = anchor.get("root_hash")
        self.chain_meta.setText(f"{count:,} blocks · " + (
            "chain intact" if intact else
            "not verified" if skipped else "review required"))
        self.chain_hash.setText(f"root {root[:28]}…" if root else "root — (not yet anchored)")
        if hasattr(self, "verif_hash"):
            self.verif_hash.setText(f"{root[:19]}…" if root else "•••• •••• •••• ••••")
        self.chain_state.setText(
            "VERIFIED" if intact else "NOT VERIFIED" if skipped else "REVIEW")
        self.chain_state.setStyleSheet(
            f"color: {OK_GREEN if intact else WARN_AMBER}; font-size: 15px;"
            f" font-weight: 800; background: transparent;")
        self._paint_chain_icon(intact)

    def _verify_chain(self):
        """Real chain verify — calls GET /v1/verify on the backend."""
        url = self.settings.backend_url()
        key = self.settings.org_api_key()
        if not (url and key):
            self.verify_btn.setText("⚠ No backend configured")
            QTimer.singleShot(2500, lambda: self.verify_btn.setText("Verify chain"))
            return
        self.verify_btn.setText("Verifying…")
        self.verify_btn.setEnabled(False)
        spawn_worker(self.client, "GET", "/v1/verify", parent=self,
                     on_ok=self._on_verify_success, on_err=self._on_verify_failed,
                     track=self._workers)

    def _on_verify_success(self, data: dict):
        ok: bool = data.get("ok", False)
        count: int = data.get("count", 0)
        broken: int | None = data.get("first_broken_seq")
        if ok:
            self.verify_btn.setText(f"✓ Chain intact · {count:,} blocks")
            self.chain_state.setText("VERIFIED")
            self.chain_state.setStyleSheet(
                f"color: {OK_GREEN}; font-size: 15px; font-weight: 800; background: transparent;")
        elif vd.chain_skipped(data):
            # This button used to answer "⚠ Broken at seq None / TAMPERED" here
            # — announcing an alteration for a check the server never ran. Same
            # bug the Verify page carried; it is worse on a button labelled
            # "Verify chain", so both now report no result rather than a break.
            self.verify_btn.setText(f"Not verified · {count:,} blocks")
            self.chain_state.setText("NOT VERIFIED")
            self.chain_state.setStyleSheet(
                f"color: {WARN_AMBER}; font-size: 15px; font-weight: 800; background: transparent;")
        else:
            self.verify_btn.setText(f"⚠ Broken at seq {broken}")
            self.chain_state.setText("TAMPERED")
            self.chain_state.setStyleSheet(
                f"color: {BAD_RED}; font-size: 15px; font-weight: 800; background: transparent;")
        self.verify_btn.setEnabled(True)
        QTimer.singleShot(4000, lambda: self.verify_btn.setText("Verify chain"))

    def _on_verify_failed(self, err: str):
        self.verify_btn.setText(f"⚠ Error: {err[:40]}")
        self.verify_btn.setEnabled(True)
        QTimer.singleShot(3500, lambda: self.verify_btn.setText("Verify chain"))

    def _on_tick(self):
        elapsed = int(time.time() - self._start_ts)
        hh, rem = divmod(elapsed, 3600)
        mm, ss = divmod(rem, 60)
        if hasattr(self, "uptime_lbl"):
            self.uptime_lbl.setText(f"Uptime {hh:02d}:{mm:02d}:{ss:02d}")

    def _on_refresh_clicked(self):
        """Fetch real history from GET /v1/logs and repopulate the table."""
        self.refresh_requested.emit()  # still notify the fox (for health recheck)
        url = self.settings.backend_url()
        key = self.settings.org_api_key()
        if not (url and key):
            return
        spawn_worker(self.client, "GET", "/v1/logs?page=1&limit=50", parent=self,
                     on_ok=self._on_refresh_success, on_err=self._on_refresh_failed,
                     track=self._workers)

        # D5: the Threats page owns its own fetches and reloads on navigation,
        # so this no longer pulls /v1/analytics/threats on behalf of a stub
        # page that no longer exists.
        self.refresh_threats()
        self._refresh_org()


    def _on_refresh_success(self, data: dict):
        items: list = data.get("items", [])
        total: int = data.get("total", 0)
        self.table.populate_from_backend(items)
        self.audit_count.setText(f"{self.table.rowCount()} records  (backend total: {total:,})")
        # Sync KPI counters from real data
        breach_count = sum(
            1 for it in items
            if (it.get("gemini_verdict") or {}).get("policy_breach", False)
        )
        self._logs_total = max(self._logs_total, total)
        self._flagged_total = max(self._flagged_total, breach_count)
        self._refresh_stats()

    def _on_refresh_failed(self, err: str):
        # Silently log — the table keeps its existing data
        print(f"[Dashboard] Refresh failed: {err}")


    def _init_chrome(self):
        """Banner, palette, shortcut overlay, toast + the chrome pollers.

        Built ONCE and torn down with the window; the pollers only run while
        the console is visible (D0.1's teardown rule)."""
        self.toast = Toast(self)
        self.banner = AnnouncementBanner()
        self.banner.action_clicked.connect(self.go)
        self.banner.dismissed.connect(self._dismiss_announcement)
        self._banner_slot.addWidget(self.banner)

        self.notif_panel = NotificationsPanel(self)
        self.notif_panel.item_clicked.connect(self._on_notification_clicked)
        self.notif_panel.read_all_clicked.connect(self._mark_all_notifications_read)

        self.cmd_palette = CommandPalette(self)
        self.cmd_palette.chosen.connect(self._on_palette_choice)
        self.shortcuts = ShortcutsOverlay(self)

        self._g_pending = False
        self._breach_max_seq = 0
        self._ann_inputs = {"breaches": None, "plan": None, "usage": None}
        # Its own set so the banner's in-flight guard can't be confused by the
        # other one-shot workers sharing self._workers.
        self._ann_workers: set = set()
        self._g_timer = QTimer(self)
        self._g_timer.setSingleShot(True)
        self._g_timer.setInterval(QUICK_NAV_WINDOW_MS)
        self._g_timer.timeout.connect(self._clear_quick_nav)

        # D5-P9: the live dot gets the web's own 60 s ping (html:4025) and
        # that is the ONLY chrome timer. Notifications, the breach pip and the
        # announcement banner now refresh when the console opens and when you
        # navigate — exactly as the site does. D3 polled all four on the 15 s
        # backend timer: three requests per cycle the web never makes. Live
        # breach alerts do not depend on this — the fox companion keeps its
        # own 10 s breach poller.
        self._health_timer = QTimer(self)
        self._health_timer.timeout.connect(self._ping_health)
        self._health_timer.start(HEALTH_PING_SECONDS * 1000)
        QTimer.singleShot(600, self._refresh_chrome)

    def _clear_quick_nav(self):
        self._g_pending = False

    def show_toast(self, text: str):
        if hasattr(self, "toast"):
            self.toast.show_message(text)

    def _ping_health(self):
        """Any HTTP answer proves the server is reachable; only a transport
        error is 'offline' (the web says so at html:3895)."""
        if not self.settings.backend_url():
            self.live_dot.set_online(False)
            return
        if self.settings.org_api_key():
            path, force = "/v1/health", True
        else:
            path, force = "/health/ready", False
        spawn_worker(self.client, "GET", path, timeout=8, parent=self,
                     force_bearer=force, track=self._workers,
                     on_ok=self._on_health_ping_ok,
                     on_err=self._on_health_ping_failed)

    def _on_health_ping_ok(self, _data):
        self.live_dot.set_online(True)

    def _on_health_ping_failed(self, err: str):
        # A status code still means the server answered (403/503 included);
        # only a status-less transport failure is genuinely offline.
        self.live_dot.set_online(err.startswith("HTTP "))

    def _refresh_chrome(self):
        """Pull the real signals the chrome shows. Never runs while hidden."""
        if not self.isVisible():
            return
        self._ping_health()
        self._refresh_breach_pip()
        self._refresh_announcement()
        if self.client.has_session():
            self._refresh_notifications()

    # ── notifications (session-only endpoint) ──
    def _refresh_notifications(self):
        spawn_worker(self.client, "GET", "/v1/notifications?limit=%d" % NOTIF_LIMIT,
                     timeout=10, parent=self, track=self._workers,
                     on_ok=self._on_notifications)

    def _place_notif_pip(self):
        """Anchor inside the bell's own box — a hand-picked offset clipped once
        the button grew to its 44 px hit target."""
        pip, host = self.notif_pip, self.notif_btn
        pip.move(max(0, host.width() - pip.width() - 3), 2)
        pip.raise_()

    def _place_breach_pip(self):
        """Same anchoring rule as the bell's pip: inside the button's own box,
        measured, never a hand-picked offset."""
        pip, host = self.breach_pip_widget, self.top_breach_btn
        pip.move(max(0, host.width() - pip.width() - 3), 2)
        pip.raise_()

    def _on_notifications(self, data):
        if not isinstance(data, dict):
            return
        items = data.get("items")
        self.notif_pip.set_count(int(data.get("unread") or 0))
        self._place_notif_pip()
        self.notif_panel.set_items(items if isinstance(items, list) else [])

    def _toggle_notifications(self):
        if self.notif_panel.isVisible():
            self.notif_panel.hide()
            return
        if not self.client.has_session():
            self.show_toast("Sign in to see your notifications.")
            return
        self._refresh_notifications()
        corner = self.notif_btn.mapTo(self, self.notif_btn.rect().bottomRight())
        self.notif_panel.move(max(8, corner.x() - self.notif_panel.width()),
                              corner.y() + 6)
        self.notif_panel.show()
        self.notif_panel.raise_()

    def _on_notification_clicked(self, notif_id: str, kind: str):
        if notif_id:
            spawn_worker(self.client, "POST",
                         "/v1/notifications/%s/read" % quote(str(notif_id), safe=""),
                         timeout=8,
                         parent=self, track=self._workers,
                         on_ok=self._on_notif_marked)
        self.notif_panel.hide()
        target = notification_target(kind)
        if target:
            self.go(target)

    def _on_notif_marked(self, _data):
        self._refresh_notifications()

    def _mark_all_notifications_read(self):
        spawn_worker(self.client, "POST", "/v1/notifications/read-all", timeout=8,
                     parent=self, track=self._workers, on_ok=self._on_notif_marked)

    # ── breach pip ──
    def _refresh_breach_pip(self):
        if not self.settings.backend_url():
            return
        if not (self.settings.org_api_key() or self.client.has_session()):
            return
        spawn_worker(self.client, "GET",
                     "/v1/logs/breaches?limit=%d" % BREACH_SCAN_LIMIT, timeout=10,
                     parent=self, track=self._workers, on_ok=self._on_breach_rows)

    def _on_breach_rows(self, rows):
        unread, self._breach_max_seq = breach_pip(
            rows, self.settings.breach_seen_seq())
        self._set_breach_count(unread)

    def _set_breach_count(self, unread: int):
        self.threat_pip.set_count(unread)
        # Anchor the nav pip to the button's real width — a fixed x clips it
        # off the edge once the sidebar or font metrics change.
        nav = self._nav_by_id["threats"]
        self.threat_pip.move(max(8, nav.width() - self.threat_pip.width() - 12),
                             (nav.height() - self.threat_pip.height()) // 2)
        self.threat_pip.raise_()
        # The pip carries the number; the button carries the meaning. A count
        # with no words is unreadable to a screen reader, so the accessible
        # name still spells it out even though the label does not.
        self.breach_pip_widget.set_count(unread)
        self._place_breach_pip()
        if unread:
            word = "breach" if unread == 1 else "breaches"
            self.top_breach_btn.setToolTip(
                f"{unread} unreviewed policy {word} — open Threats")
            self.top_breach_btn.setAccessibleName(
                f"Open threats — {unread} unreviewed policy {word}")
        else:
            self.top_breach_btn.setToolTip("Threats")
            self.top_breach_btn.setAccessibleName("Open threats")

    def _mark_breaches_read(self):
        if self._breach_max_seq:
            self.settings.set_breach_seen_seq(self._breach_max_seq)
        self._set_breach_count(0)

    # ── announcement banner ──
    def _refresh_announcement(self):
        """Three real signals, resolved in the web's priority order.

        The in-flight guard keys off the WORKER SET, exactly like
        `_refresh_backend`/`_poll_workers`: membership is cleared by QThread's
        `finished`, which fires on success AND on failure, so the guard cannot
        latch. A counter decremented only from the success callback could — and
        did: one blip on /v1/billing/plan left the count above zero and froze
        the banner for the rest of the session."""
        if not self.settings.backend_url():
            return
        if self._ann_workers:
            return
        self._ann_inputs = {"breaches": None, "plan": None, "usage": None}
        for path, handler in (
                ("/v1/logs/breaches?limit=1", self._on_ann_breaches),
                ("/v1/billing/plan", self._on_ann_plan),
                ("/v1/usage?days=1", self._on_ann_usage)):
            spawn_worker(self.client, "GET", path, timeout=10, parent=self,
                         track=self._ann_workers, on_ok=handler,
                         # A failed leg still repaints from what we DO know,
                         # instead of leaving the banner on stale inputs.
                         on_err=lambda _err, h=handler: h(None))

    def _on_ann_breaches(self, data):
        self._ann_part("breaches", data)

    def _on_ann_plan(self, data):
        self._ann_part("plan", data)

    def _on_ann_usage(self, data):
        self._ann_part("usage", data)

    def _ann_part(self, key: str, data):
        self._ann_inputs[key] = data
        self.banner.show_message(announcement(
            breaches=self._ann_inputs.get("breaches"),
            plan=self._ann_inputs.get("plan"),
            usage=self._ann_inputs.get("usage"),
            dismissed=self.settings.dismissed_announcements()))

    def _dismiss_announcement(self, ident: str):
        self.settings.dismiss_announcement(ident)

    # ── user chip ──
    def _apply_identity(self, me: dict):
        self.user_btn.setText(avatar_initial(me))
        who = (me or {}).get("email") or ""
        role = (me or {}).get("role") or ""
        # D7 needs this: PUT /v1/policies is admin-humans-only, and the page
        # disables its controls rather than letting a member discover that
        # through a 403 after filling the form in.
        self._role = role
        if hasattr(self, "pol_save"):
            self._apply_policy_permission()
        self.user_btn.setToolTip(("%s · %s" % (who, role)) if role else (who or "Account"))
        self.user_btn.setAccessibleName(
            ("Account menu for %s" % who) if who else "Account menu")
        if hasattr(self, "cmd_palette"):
            self.cmd_palette.set_org_id((me or {}).get("org_id"))

    def _show_user_menu(self):
        from PyQt6.QtWidgets import QMenu
        from foxy_tokens import matte_menu_qss
        menu = QMenu(self)
        menu.setStyleSheet(matte_menu_qss())
        menu.addAction("Settings", lambda: self.go("settings"))
        menu.addAction("Devices & sessions", lambda: self.go("settings"))
        menu.addSeparator()
        if self._signed_in:
            menu.addAction("Log out", self.sign_out_requested.emit)
        else:
            menu.addAction("Sign in", self.sign_in_requested.emit)
        menu.exec(self.user_btn.mapToGlobal(self.user_btn.rect().bottomLeft()))

    # ── command palette + keyboard ──
    def _on_palette_choice(self, entry: dict):
        kind = entry.get("kind")
        arg = entry.get("arg")
        if kind == "page":
            self.go(arg)
        elif kind == "verify-hash":
            self._pending_verify_hash = arg
            self.go("verify")
            self.show_toast("Hash noted — verification arrives in D6 (%s…)" % arg[:12])
        elif kind == "ledger-seq":
            self.go("ledger")
            self.show_toast("Record #%s — ledger lookup arrives in D5" % arg)
        elif kind == "verify-focus":
            self.go("verify")
        elif kind == "copy-org":
            if arg:
                QApplication.clipboard().setText(str(arg))
                self.show_toast("Copied organization ID")
            else:
                self.show_toast("Organization ID not loaded yet")
        elif kind == "shortcuts":
            self.shortcuts.show_centered(self)

    def keyPressEvent(self, event):
        key = event.key()
        mods = event.modifiers()
        if (mods & Qt.KeyboardModifier.ControlModifier) and key == Qt.Key.Key_K:
            self.cmd_palette.open_fresh()
            event.accept()
            return
        if mods & (Qt.KeyboardModifier.ControlModifier
                   | Qt.KeyboardModifier.AltModifier
                   | Qt.KeyboardModifier.MetaModifier):
            super().keyPressEvent(event)
            return
        if key == Qt.Key.Key_Escape:
            self.notif_panel.hide()
            self._g_pending = False
            event.accept()
            return
        if self._is_typing():
            super().keyPressEvent(event)
            return
        if key == Qt.Key.Key_Question:
            self.shortcuts.show_centered(self)
            event.accept()
            return
        if self._g_pending:
            self._g_pending = False
            self._g_timer.stop()
            target = QUICK_NAV.get(event.text().lower())
            if target:
                self.go(target)
                event.accept()
                return
        elif event.text().lower() == "g":
            self._g_pending = True
            self._g_timer.start()
            event.accept()
            return
        super().keyPressEvent(event)

    def _is_typing(self) -> bool:
        """Quick-nav must never steal a keystroke from an input (web isTyping),
        nor from an open dropdown that is currently taking the keyboard."""
        if isinstance(QApplication.focusWidget(), (QLineEdit, QTextEdit)):
            return True
        panel = getattr(self, "notif_panel", None)
        return panel is not None and panel.isVisible()

    # ── Home page data (D4) ────────────────────────────────────────────────
    def _init_home(self):
        """Home's own worker set and its activity re-tick.

        Its own set so the banner/chrome guards stay independent, and — per the
        D3.2 lesson — every guard below keys off set membership, which
        QThread.finished clears on success AND failure."""
        # Two buckets on purpose. `_home_workers` is the refresh CYCLE — its
        # emptiness is the "no cycle in flight" gate. One-off calls (dismiss,
        # quick hash check, the ↻ activity button) go in `_oneoff_workers`, or
        # a 15 s hash lookup would silently make the next Home refresh return
        # early and skip everything, with no retry.
        self._home_workers: set = set()
        self._oneoff_workers: set = set()
        self._home_days: list = []
        self._trend_metric = "logs"
        self._activity_rows: list = []
        self._activity_at = None
        self._activity_failed: set = set()
        self._home_usage_ok = True
        # Relative times go stale silently, so re-tick them like the web does.
        self._activity_timer = QTimer(self)
        self._activity_timer.timeout.connect(self._retick_activity)
        self._activity_timer.start(hd.ACTIVITY_TICK_SECONDS * 1000)

    def refresh_home(self):
        """Pull everything the Home page shows. Never runs while hidden.

        Every endpoint below needs a credential. Firing them anyway would 401
        and paint six "couldn't reach the backend" panels at a signed-out user,
        which is both wrong and alarming — so we use the same gate the breach
        pip does and leave the page on its first-run empty states instead."""
        if not self.isVisible() or not self.settings.backend_url():
            return
        if not (self.settings.org_api_key() or self.client.has_session()):
            return
        if self._home_workers:
            return                      # a cycle is still in flight
        spawn_worker(self.client, "GET", "/v1/onboarding", timeout=10, parent=self,
                     track=self._home_workers, on_ok=self._on_onboarding,
                     on_err=lambda _e: self._on_onboarding(None))
        spawn_worker(self.client, "GET", "/v1/coverage", timeout=12, parent=self,
                     track=self._home_workers, on_ok=self._on_coverage,
                     on_err=lambda _e: self._on_coverage(None))
        spawn_worker(self.client, "GET", "/v1/usage?days=90", timeout=12, parent=self,
                     track=self._home_workers, on_ok=self._on_usage,
                     on_err=lambda _e: self._on_usage(None, ok=False))
        spawn_worker(self.client, "GET",
                     f"/v1/logs?limit={hd.RECENT_LEDGER_LIMIT}", timeout=12,
                     parent=self, track=self._home_workers,
                     on_ok=self._on_recent_ledger,
                     on_err=lambda _e: self._on_recent_ledger(None, ok=False))
        spawn_worker(self.client, "GET", "/v1/analytics/threats", timeout=12,
                     parent=self, track=self._home_workers, on_ok=self._on_threats,
                     on_err=lambda _e: self._on_threats(None, ok=False))
        self.refresh_activity(cycle=True)

    # ── greeting + subtitle ──
    def _apply_home_identity(self, me: dict):
        self.home_greeting.setText(
            f"Yo {hd.greeting_name(me)}. here's the vibe check.")

    # ── onboarding ──
    def _on_onboarding(self, data):
        view = hd.onboarding_view(data)
        if view is None:
            self.onboarding_card.hide()
            return
        self.onboarding_title.setText(view["title"])
        self.onboarding_sub.setText(view["sub"])
        self.onboarding_pct.setText(view["progress_text"])
        self.onboarding_bar.set_options(
            value=view["done"], max=view["total"], height=12, tone=view["tone"],
            aria=f"onboarding progress, {view['progress_text']}",
            tip=view["progress_text"])
        panel_state.clear_rows(self.onboarding_steps)
        panel_state.fill_visible(
            self.onboarding_steps,
            (onboarding_step_row(step, self.go) for step in view["steps"]))
        self.onboarding_card.show()

    def _dismiss_onboarding(self):
        self.onboarding_card.hide()
        spawn_worker(self.client, "PUT", "/v1/onboarding", body={"dismissed": True},
                     timeout=10, parent=self, track=self._oneoff_workers)

    # ── capture coverage ──
    def _on_coverage(self, data):
        view = hd.coverage_view(data)
        tone = {"ok": OK_GREEN, "warn": WARN_AMBER}.get(view["status_tone"], WARN_AMBER)
        self.coverage_status.setText(f"● {view['status_label']}")
        self.coverage_status.setStyleSheet(
            f"color: {tone}; font-family: '{_pick_font('mono')}'; font-size: 9.5px;"
            f" font-weight: 700; background: transparent;")
        self.coverage_message.setText(view["message"])
        self.coverage_pct.setText(view["pct_text"])
        self.coverage_note.setText(view["note"])
        self.coverage_gauge.set_options(
            value=view["pct"] or 0, max=100, height=14, tone=view["gauge_tone"],
            aria=f"observed capture {view['pct_text']}",
            tip=view["note"] or None)
        # A "could not be loaded" message above last cycle's numbers reads as
        # if those numbers were just measured. Blank them with the message.
        chips = view["chips"] or [(None, "—", False)] * len(self.coverage_chips)
        for label, (_name, value, _danger) in zip(self.coverage_chips, chips):
            label.setText(value)
        panel_state.clear_rows(self.coverage_table,
                               start=self.coverage_rows_start)
        panel_state.fill_visible(
            self.coverage_table,
            (coverage_row(client) for client in view["clients"]))
        self.coverage_empty.set_state(
            panel_state.resolve(view["ok"], bool(view["clients"])),
            empty_title="No identified SDK clients yet",
            empty_body="Clients appear once the SDK reports a client id.")
        self.coverage_empty.setVisible(not view["clients"])

    # ── usage trend + hero spark + coverage volume ──
    def _on_usage(self, data, ok: bool = True):
        days = (data or {}).get("days") if isinstance(data, dict) else None
        self._home_days = days if isinstance(days, list) else []
        self._home_usage_ok = ok
        self._render_trend()
        self.coverage_volume.set_options(
            height=120, tone="blue",
            data=[{"label": d.get("day"), "value": d.get("logs_count") or 0}
                  for d in self._home_days],
            aria="capture volume per day, last 90 days",
            empty=chart_empty(
                panel_state.resolve(ok, bool(self._home_days)),
                empty_title="No captured events yet",
                empty_body="Daily capture volume appears as your SDK reports "
                           "interactions."))
        spark = hd.hero_spark(self._home_days)
        if hasattr(self, "hero_spark_chart"):
            # HERO_SPARK_INK, not an rgba() string: qcolor() only parses
            # #RRGGBB and Qt colour names, so "rgba(26,9,0,.55)" silently fell
            # back to mid-grey #888888 — 1.06:1 against the light end of the
            # hero gradient, i.e. invisible. quiet: a 28px strip has no room
            # for a titled empty state.
            self.hero_spark_chart.set_options(
                height=28, data=spark, tone=HERO_SPARK_INK,
                backing=HERO_SPARK_BACKING,
                aria="interactions per day, last 30 days",
                empty=chart_empty(
                    panel_state.resolve(ok, bool(spark)), quiet=True))
        if hasattr(self, "hero_delta_lbl"):
            self.hero_delta_lbl.setText("" if not ok else hd.hero_delta(self._home_days))

    def set_trend_metric(self, metric: str):
        if metric not in hd.TREND_METRICS:
            return
        self._trend_metric = metric
        for name, button in self.trend_buttons.items():
            button.setChecked(name == metric)
        self._render_trend()

    def _render_trend(self):
        rows, spec = hd.trend_rows(self._home_days, self._trend_metric)
        self.usage_trend.set_options(
            height=200, data=rows, name=spec["label"], tone=spec["tone"],
            aria=f"{spec['label'].lower()} per day, last 90 days",
            empty=chart_empty(
                panel_state.resolve(self._home_usage_ok, bool(rows)),
                empty_title="No usage yet",
                empty_body="Daily volume appears as your SDK logs "
                           "interactions."))

    # ── grading donut + gauges, fed by the existing /v1/stats poll ──
    def _apply_home_stats(self, stats: dict):
        self.home_sub.setText(hd.head_subtitle(stats))
        slices, total = hd.grading_slices(stats)
        self.grading_donut.set_options(
            height=170, legend=True,
            center=hd.thousands(total) if total else None,
            data=slices if total else [],
            aria="grading status breakdown",
            empty=chart_empty(
                panel_state.resolve(True, bool(total)),
                empty_title="Nothing graded yet",
                empty_body="Grading status appears once the Judge processes "
                           "interactions."))
        # Both gauges match the site exactly (html:2231, 2236): same labels,
        # same scales, same untoned fox fill. The desktop previously showed a
        # rounded percent, and inverted the verdict gauge so it emptied as
        # latency rose, capped at 30 s with ok/warn tones — three silent
        # disagreements with the website about the same two numbers.
        clean = stats.get("clean_rate")
        self.gauge_clean_value.setText(hd.fmt_pct(clean))
        self.gauge_clean.set_options(
            value=hd._num(clean), max=100, height=12,
            aria=f"clean rate {hd.fmt_pct(clean)}",
            tip=f"Clean rate — {hd.fmt_pct(clean)}")
        ttv = stats.get("avg_seconds_to_verdict")
        self.gauge_verdict_value.setText(hd.fmt_verdict(ttv))
        self.gauge_verdict.set_options(
            value=0 if ttv is None else min(VERDICT_GAUGE_MAX, hd._num(ttv)),
            max=VERDICT_GAUGE_MAX, height=12,
            aria=f"average time to verdict {hd.fmt_verdict(ttv)}",
            tip=f"Avg time to verdict — {hd.fmt_verdict(ttv)}")

    # ── quick ledger check (verification card back face) ──
    def quick_verify(self):
        action, message, tone = hd.quick_verify_request(self.q_hash_input.text())
        self._set_quick_result(message, tone)
        if action != "check":
            return
        digest = hd.normalize_hash(self.q_hash_input.text())
        spawn_worker(self.client, "GET", f"/v1/verify/hash/{digest}", timeout=15,
                     parent=self, track=self._oneoff_workers,
                     on_ok=lambda d: self._set_quick_result(*hd.quick_verify_result(d)),
                     on_err=lambda err: self._set_quick_result(
                         "sign in to check the ledger" if status_of(err) == 401
                         else "could not reach the server", "bad"))

    def _set_quick_result(self, message: str, tone: str):
        colour = {"ok": OK_GREEN, "bad": BAD_RED}.get(tone, WEB["muted"])
        self.q_result.setStyleSheet(
            f"color: {colour}; font-family: '{_pick_font('mono')}';"
            f" font-size: 10.5px; background: transparent;")
        self.q_result.setText(message)

    # ── recent ledger preview ──
    def _on_recent_ledger(self, data, ok: bool = True):
        rows = hd.recent_ledger_rows(data)
        state = panel_state.resolve(ok, bool(rows))
        self.recent_ledger_empty.set_state(
            state, empty_title="No ledger records yet",
            empty_body="Rows appear as your SDK reports interactions.")
        panel_state.clear_rows(self.recent_ledger,
                               keep=(self.recent_ledger_empty,))
        if not rows:
            panel_state.add_visible(self.recent_ledger,
                                    self.recent_ledger_empty)
            return
        self.recent_ledger_empty.hide()
        panel_state.fill_visible(self.recent_ledger,
                                 (ledger_row(row) for row in rows))

    # ── active alerts ──
    def _on_threats(self, data, ok: bool = True):
        rows = hd.alert_rows(data)
        self.home_alerts_empty.set_state(
            panel_state.resolve(ok, bool(rows)),
            empty_title="No active alerts",
            empty_body="Every graded interaction is within policy.")
        panel_state.clear_rows(self.home_alerts,
                               keep=(self.home_alerts_empty,))
        if not rows:
            panel_state.add_visible(self.home_alerts, self.home_alerts_empty)
        else:
            self.home_alerts_empty.hide()
            panel_state.fill_visible(self.home_alerts,
                                     (alert_row(row) for row in rows))
        # Open alerts ≠ the "Policy Breaches" KPI tile: that one counts every
        # breach ever recorded, this counts the ones still live. It rides the
        # section header rather than overwriting a differently-labelled tile.
        count = hd.open_alert_count(data)
        self.home_alerts_count.setText("" if count is None else f"{count:,} open")
        # The web's dv[1] (html:3350): the live count, not the all-time total.
        self.kpi_alerts.set_value(hd.stat_row(None, count)[1][1])

    # ── recent activity ──
    def refresh_activity(self, *, cycle: bool = False):
        """Both endpoints are admin-only: a member simply gets fewer rows, so
        each side degrades on its own rather than failing the whole feed.

        `cycle` says who is asking, and it decides which worker set the two
        requests join. THE RULE: only the periodic refresh cycle uses
        `_home_workers`, because that set's emptiness is the "no cycle in
        flight" gate. This method is reachable two ways — as a leg of
        `refresh_home()`, and directly from the Activity card's ↻ button and
        its retry — and the user-triggered paths must not be able to gate
        anything. D5-P3 moved the other one-offs across but missed this one, so
        pressing ↻ still put two 12-second requests in the gating set and any
        Home refresh in that window returned early without loading anything.
        """
        track = self._home_workers if cycle else self._oneoff_workers
        if not self.client.has_session():
            # An org-key session cannot read these admin endpoints. That is a
            # permission boundary, not a failure and not an empty result.
            self.activity_empty.set_state(
                PanelState.EMPTY, empty_title="Sign in to see account activity",
                empty_body="Key changes and sign-ins are visible to signed-in "
                           "members.")
            self._render_activity([])
            return
        self._activity_parts = {"audit": None, "logins": None}
        self._activity_failed: set = set()
        for path, key in (("/v1/account/audit?limit=50", "audit"),
                          ("/v1/auth/login-history", "logins")):
            spawn_worker(self.client, "GET", path, timeout=12, parent=self,
                         track=track,
                         on_ok=lambda d, k=key: self._on_activity_part(k, d),
                         on_err=lambda _e, k=key: self._on_activity_part(
                             k, [], ok=False))

    def _on_activity_part(self, key: str, data, ok: bool = True):
        parts = getattr(self, "_activity_parts", None)
        if parts is None:
            return
        parts[key] = data if isinstance(data, list) else []
        if not ok:
            self._activity_failed.add(key)
        if parts["audit"] is not None and parts["logins"] is not None:
            # Both legs down is "we could not ask", not "nothing happened".
            both_down = len(self._activity_failed) == len(parts)
            rows = hd.merge_activity(parts["audit"], parts["logins"])
            self.activity_empty.set_state(
                panel_state.resolve(not both_down, bool(rows)),
                empty_title="No account activity yet",
                empty_body="Key changes and sign-ins show up here as they "
                           "happen.")
            self._render_activity(rows)

    def _render_activity(self, rows: list):
        self._activity_rows = rows
        panel_state.clear_rows(self.activity_list,
                               keep=(self.activity_empty,))
        if not rows:
            panel_state.add_visible(self.activity_list, self.activity_empty)
            self.activity_stamp.setText("")
            return
        self.activity_empty.hide()
        panel_state.fill_visible(self.activity_list,
                                 (activity_row(item) for item in rows))
        self._activity_at = datetime.now(timezone.utc)
        self.activity_stamp.setText("updated just now")

    def _retick_activity(self):
        """Relative times must not silently rot while the page sits open."""
        if not self.isVisible() or not self._activity_rows:
            return
        for i in range(self.activity_list.count()):
            widget = self.activity_list.itemAt(i).widget()
            if widget is None:
                continue
            for child in widget.findChildren(QLabel):
                iso = child.property("iso")
                if iso:
                    child.setText(relative_time(iso))
        if self._activity_at is not None:
            self.activity_stamp.setText(
                f"updated {relative_time(self._activity_at.isoformat())}")

    # ── Threats page (D5) ──────────────────────────────────────────────────
    def _init_threats(self):
        """Its own worker set, so a Threats fetch cannot gate a Home refresh
        and vice versa. Only the periodic cycle uses a gating set (the C1
        rule); these pages refresh on navigation, so theirs is a plain bucket
        drained at teardown."""
        self._threat_workers: set = set()
        self._threat_days = td.DEFAULT_RANGE
        self._threat_stats_payload = None
        self._threat_payload = None

    def refresh_threats(self):
        if not self._can_fetch():
            return
        spawn_worker(self.client, "GET", "/v1/analytics/threats", timeout=12,
                     parent=self, track=self._threat_workers,
                     on_ok=self._on_threat_analytics,
                     on_err=lambda _e: self._on_threat_analytics(None, ok=False))
        spawn_worker(self.client, "GET", "/v1/stats", timeout=12, parent=self,
                     track=self._threat_workers, on_ok=self._on_threat_stats,
                     on_err=lambda _e: self._on_threat_stats(None, ok=False))
        spawn_worker(self.client, "GET", "/v1/analytics/by-agent", timeout=12,
                     parent=self, track=self._threat_workers,
                     on_ok=self._on_by_agent,
                     on_err=lambda _e: self._on_by_agent(None, ok=False))
        self._load_timeline()

    def set_threat_range(self, days: int):
        """7d / 30d / 90d. Only the timeline re-fetches — the other panels are
        not windowed, so refetching them would be wasted work."""
        if days not in td.RANGES:
            return
        self._threat_days = days
        for value, button in self.threat_range_buttons.items():
            button.setChecked(value == days)
        self._load_timeline()

    def _load_timeline(self):
        if not self._can_fetch():
            return
        spawn_worker(self.client, "GET",
                     f"/v1/analytics/timeseries?days={self._threat_days}",
                     timeout=12, parent=self, track=self._threat_workers,
                     on_ok=self._on_timeline,
                     on_err=lambda _e: self._on_timeline(None, ok=False))

    def _on_threat_analytics(self, data, ok: bool = True):
        self._threat_payload = data if ok else None
        self._apply_threat_stats()

        rows = td.alert_table_rows(data) if ok else []
        state = PanelState.OK if rows else (
            PanelState.EMPTY if ok else PanelState.ERROR)
        self._fill_rows(self.threat_table, self.threat_rows_start,
                        self.threat_table_empty, rows, alert_table_row, state,
                        empty_title="No high-risk breaches",
                        empty_body="Every graded interaction is within policy.")

        view = td.avg_risk_view(data if ok else None)
        self.avg_risk_num.setText(view["text"] if ok else "—")
        self.avg_risk_label.setText(view["label"] if ok else "")
        self.avg_risk_note.setText(view["note"] if ok else UNAVAILABLE_TEXT)
        self.avg_risk_gauge.set_options(
            value=view["value"] if ok else 0, max=100, height=14,
            tone=view["tone"] if ok else "mute", tip=view["tip"],
            aria="average breach risk out of 100",
            empty=chart_empty(PanelState.OK if ok else PanelState.ERROR))

        policies = td.policy_rows(data) if ok else []
        self.threat_policies.set_options(
            data=policies, height=td.hbar_height(policies), limit=td.TOP_N,
            aria="top flagged policies",
            empty=chart_empty(
                resolve(ok, bool(policies)),
                empty_title="No flagged policies",
                empty_body="Nothing has breached policy yet."))

    def _on_threat_stats(self, data, ok: bool = True):
        self._threat_stats_payload = data if ok else None
        self._apply_threat_stats()
        bars = td.activity_bars(data) if ok else []
        # Padded zero-days are real, so "has rows" means the window itself
        # exists, not that anything happened in it.
        self.threat_activity.set_options(
            data=bars, height=120, aria="interactions per day, last 7 days",
            empty=chart_empty(
                resolve(ok, bool(bars)),
                empty_title="No activity yet",
                empty_body="Weekly volume appears as your SDK logs graded "
                           "interactions."))

    def _apply_threat_stats(self):
        for label, (_name, value, _tone) in zip(
                self.threat_stats,
                td.stat_row(self._threat_stats_payload, self._threat_payload)):
            label.setText(value)

    def _on_timeline(self, data, ok: bool = True):
        labels, series = td.timeline_series(data) if ok else ([], [])
        self.threat_timeline.set_options(
            labels=labels, series=series, height=180, legend=False,
            aria="breaches per day by risk band",
            empty=chart_empty(
                resolve(ok, bool(labels)),
                empty_title="No breaches in this window",
                empty_body="Breaches appear here as the Judge flags "
                           "interactions."))

    def _on_by_agent(self, data, ok: bool = True):
        rows = td.agent_rows(data) if ok else []
        self.threat_by_agent.set_options(
            data=rows, height=td.hbar_height(rows), limit=td.TOP_N,
            aria="breaches by agent",
            empty=chart_empty(
                resolve(ok, bool(rows)),
                empty_title="No breaches by agent yet",
                empty_body="Attribution appears once flagged interactions "
                           "carry an agent."))

    # ── Ledger page (D5) ───────────────────────────────────────────────────
    def _init_ledger(self):
        self._ledger_workers: set = set()
        self._ledger_page = 1
        self._ledger_total = 0

    def _ledger_filters(self) -> tuple[str, str, str]:
        return (self.ledger_q.text(), self.ledger_policy.text(),
                self.ledger_verdict.currentData() or "")

    def apply_ledger_filters(self):
        """Any filter change resets to page 1 — staying on page 7 of a result
        set that no longer has seven pages would show an empty table and blame
        the data for it."""
        self._ledger_page = 1
        self.refresh_ledger()

    def clear_ledger_filters(self):
        self.ledger_q.clear()
        self.ledger_policy.clear()
        self.ledger_verdict.setCurrentIndex(0)
        if "" in self.ledger_chips:
            self.ledger_chips[""].setChecked(True)
        self.apply_ledger_filters()

    def quick_ledger(self, verdict: str):
        index = self.ledger_verdict.findData(verdict)
        if index >= 0:
            self.ledger_verdict.setCurrentIndex(index)
        self.apply_ledger_filters()

    def ledger_page(self, delta: int):
        top = ld.max_page(self._ledger_total)
        self._ledger_page = min(top, max(1, self._ledger_page + delta))
        self.refresh_ledger()

    def refresh_ledger(self):
        if not self._can_fetch():
            return
        q, policy, verdict = self._ledger_filters()
        query = ld.query_string(page=self._ledger_page, q=q,
                                policy_tag=policy, verdict=verdict)
        spawn_worker(self.client, "GET", f"/v1/logs{query}", timeout=15,
                     parent=self, track=self._ledger_workers,
                     on_ok=self._on_ledger_rows,
                     on_err=lambda _e: self._on_ledger_rows(None, ok=False))
        spawn_worker(self.client, "GET", "/v1/stats", timeout=12, parent=self,
                     track=self._ledger_workers, on_ok=self._on_ledger_stats,
                     on_err=lambda _e: self._on_ledger_stats(None, ok=False))
        spawn_worker(self.client, "GET", "/v1/usage?days=90", timeout=12,
                     parent=self, track=self._ledger_workers,
                     on_ok=self._on_ledger_volume,
                     on_err=lambda _e: self._on_ledger_volume(None, ok=False))

    def _on_ledger_rows(self, data, ok: bool = True):
        rows = ld.table_rows(data) if ok else []
        self._ledger_total = int(hd._num((data or {}).get("total"))) if ok else 0
        q, policy, verdict = self._ledger_filters()
        filtered = any(x.strip() for x in (q, policy, verdict))
        title, body = ld.empty_message(self._ledger_total, self._ledger_page,
                                       filtered)
        self._fill_rows(
            self.ledger_rows, 0, self.ledger_empty, rows,
            lambda row: LedgerRow(row, self._verify_record),
            resolve(ok, bool(rows)), empty_title=title, empty_body=body)

        self.ledger_count.setText(ld.count_label(self._ledger_total) if ok else "—")
        self.ledger_page_lbl.setText(
            ld.page_label(self._ledger_page, self._ledger_total) if ok else "")
        top = ld.max_page(self._ledger_total)
        self.ledger_prev.setEnabled(ok and self._ledger_page > 1)
        self.ledger_next.setEnabled(ok and self._ledger_page < top)

    def _on_ledger_stats(self, data, ok: bool = True):
        for label, (_name, value, _tone) in zip(
                self.ledger_stats, ld.summary_tiles(data if ok else None)):
            label.setText(value)
        slices, total = ld.verdict_slices(data) if ok else ([], 0)
        self.ledger_donut.set_options(
            data=slices if total else [], height=150, legend=True,
            center=hd.thousands(total) if total else None,
            aria="verdict distribution",
            empty=chart_empty(
                resolve(ok, bool(total)),
                empty_title="Nothing graded yet",
                empty_body="Verdicts appear once the Judge processes "
                           "interactions."))

    def _on_ledger_volume(self, data, ok: bool = True):
        rows = ld.volume_rows(data) if ok else []
        self.ledger_volume.set_options(
            data=rows, height=140, tone="fox",
            aria="records logged per day, last 90 days",
            empty=chart_empty(
                resolve(ok, bool(rows)),
                empty_title="No records yet",
                empty_body="Logged interactions appear here as your SDK "
                           "reports them."))

    def _verify_record(self, chain_hash: str, row_widget):
        """Re-verify one record against the server. A one-off, so it uses the
        one-off bucket — never a set another panel gates on."""
        if not chain_hash:
            row_widget.set_verify_result("no hash on this record", "warn")
            return
        spawn_worker(
            self.client, "GET", f"/v1/verify/hash/{quote(chain_hash, safe='')}",
            timeout=15, parent=self, track=self._oneoff_workers,
            on_ok=lambda d: row_widget.set_verify_result(*ld.verify_result(d)),
            on_err=lambda _e: row_widget.set_verify_result(
                "could not verify", "warn"))

    # ── shared list plumbing ───────────────────────────────────────────────
    def _can_fetch(self) -> bool:
        """Same gate the breach pip and Home use: every endpoint behind these
        pages needs a credential, and firing them signed-out would paint
        "backend unreachable" at someone who is simply signed out."""
        if not self.isVisible() or not self.settings.backend_url():
            return False
        return bool(self.settings.org_api_key() or self.client.has_session())

    def _fill_rows(self, layout, start: int, strip, rows: list, build,
                   state, *, empty_title: str, empty_body: str):
        """Rebuild a row list and put its status strip in the right state.

        One place, because the D4 retrofit showed how easily two of these drift
        apart — and every one of them has to distinguish "nothing" from
        "couldn't ask"."""
        panel_state.clear_rows(layout, start=start, keep=(strip,))
        if state is PanelState.OK:
            strip.hide()
            # add_visible, because `addWidget` does not show a child — see
            # panel_state. This helper is one of SEVEN rebuild sites; the other
            # six are Home's five and the notifications panel, and they all use
            # the same helpers now.
            panel_state.fill_visible(layout, (build(row) for row in rows))
        else:
            strip.set_state(state, empty_title=empty_title,
                            empty_body=empty_body)
            strip.show()
            layout.addWidget(strip)

    # ── Verify + Anchors (D6) ──────────────────────────────────────────────
    def _init_verify(self):
        """One worker bucket for all three late pages. None of them is a
        periodic cycle, so none of them gates anything — the C1 rule."""
        if not hasattr(self, "_page_workers"):
            self._page_workers: set = set()

    def on_verify_hash_changed(self, text: str):
        hint, tone = vd.hash_hint(text)
        colour = {"ok": OK_GREEN, "bad": BAD_RED}.get(tone, WEB["muted"])
        self.v_hint.setStyleSheet(
            f"color: {colour}; font-family: '{_pick_font('mono')}';"
            f" font-size: 10px; background: transparent;")
        self.v_hint.setText(hint)

    def clear_verify(self):
        self.v_hash.clear()
        self.v_hint.setText("")
        self.v_result.clear()
        self.v_hash.setFocus()

    def run_verify(self):
        precheck = vd.verify_precheck(self.v_hash.text())
        if precheck:
            self.v_result.show_result(*precheck)
            return
        digest = vd.normalize_hash(self.v_hash.text())
        self.v_result.show_result(*vd.CHECKING)
        spawn_worker(
            self.client, "GET", f"/v1/verify/hash/{quote(digest, safe='')}",
            timeout=20, parent=self, track=self._page_workers,
            on_ok=lambda d: self.v_result.show_result(*vd.record_result(d)),
            on_err=lambda err: self.v_result.show_result(
                *vd.record_result(None, status=status_of(err))))

    def run_quick_verify(self):
        digest = vd.normalize_hash(self.v_quick_hash.text())
        if len(digest) != vd.HASH_LENGTH:
            self._set_quick_verify("enter a 64-character chain hash", "bad")
            return
        self._set_quick_verify("checking ledger…", "muted")
        spawn_worker(
            self.client, "GET", f"/v1/verify/hash/{quote(digest, safe='')}",
            timeout=20, parent=self, track=self._page_workers,
            on_ok=lambda d: self._set_quick_verify(*ld.verify_quick(d)),
            on_err=lambda _e: self._set_quick_verify(
                "could not reach the server", "bad"))

    def _set_quick_verify(self, message: str, tone: str):
        colour = {"ok": OK_GREEN, "bad": BAD_RED}.get(tone, WEB["muted"])
        self.v_quick_result.setStyleSheet(
            f"color: {colour}; font-family: '{_pick_font('mono')}';"
            f" font-size: 10.5px; background: transparent;")
        self.v_quick_result.setText(message)

    def run_chain_check(self):
        """The whole-ledger check.

        Three outcomes: intact, a named break, or no result at all — above 50k
        records the server declines the recompute, and `chain_result` reports
        that as "not verified" rather than as a break it never found. (This
        docstring used to say the check "only covered a window"; it does not
        cover one — nothing is checked at that size.)
        """
        self.v_chain_result.show_result(
            "checking", "Checking the chain…",
            "Re-deriving every record from its predecessor.")
        spawn_worker(
            self.client, "GET", "/v1/verify", timeout=45, parent=self,
            track=self._page_workers,
            on_ok=lambda d: self.v_chain_result.show_result(*vd.chain_result(d)),
            on_err=lambda _e: self.v_chain_result.show_result(
                *vd.chain_result(None)))

    def anchor_now(self):
        self.v_anchor_btn.setEnabled(False)
        self.v_anchor_link.hide()
        self.v_chain_result.show_result(
            "checking", "⚓ anchoring the current chain head to Sepolia…", "")

        def finish(tone, title, tx_hash):
            self.v_anchor_btn.setEnabled(True)
            self.v_chain_result.show_result(tone, title, "")
            url = vd.tx_url(tx_hash)
            if url:
                self.v_anchor_link.setText(
                    f'<a href="{url}" style="color:{WEB["fox2"]};">'
                    f'view tx on Etherscan ↗</a>')
                self.v_anchor_link.show()
            if tone == "ok":
                self.refresh_anchors()

        spawn_worker(
            self.client, "POST", "/v1/anchors", body={}, timeout=30, parent=self,
            track=self._page_workers,
            on_ok=lambda d: finish(*vd.anchor_now_result(d)),
            on_err=lambda err: finish(*vd.anchor_now_result(
                None, status=status_of(err))))

    def refresh_anchors(self):
        if not self._can_fetch():
            return
        spawn_worker(self.client, "GET", "/v1/anchors/sla", timeout=10,
                     parent=self, track=self._page_workers,
                     on_ok=self._on_anchor_sla,
                     on_err=lambda _e: self._on_anchor_sla(None))
        spawn_worker(self.client, "GET", "/v1/anchors", timeout=12, parent=self,
                     track=self._page_workers, on_ok=self._on_anchors,
                     on_err=lambda _e: self._on_anchors(None, ok=False))

    def _on_anchor_sla(self, data):
        text = vd.sla_text(data)
        base = ("Each receipt records your ledger head on a public blockchain "
                "— independent, third-party proof no one (not even us) can "
                "alter.")
        self.v_anchor_sla.setText(f"{base} {text}".strip())

    def _on_anchors(self, data, ok: bool = True):
        rows = vd.receipt_rows(data) if ok else []
        self.v_anchor_fresh.setText(vd.freshness(data) if ok else "")
        self._fill_rows(
            self.v_anchor_list, 0, self.v_anchor_empty, rows, anchor_row,
            resolve(ok, bool(rows)),
            empty_title="No anchors yet",
            empty_body="Your chain head is anchored automatically on your "
                       "plan's cadence, or use “anchor now” above to publish "
                       "it immediately.")

    # ── Export (D8) ────────────────────────────────────────────────────────
    def _init_export(self):
        self._init_verify()                 # shares the bucket
        self._export_head = ""
        self._export_org = ""
        # None until /v1/auth/me answers. Masked while unknown: revealing a
        # workspace identifier before we know whether the user asked us to hide
        # it gets the order wrong, and the cost of waiting is one render.
        self._hide_metadata: bool | None = None
        self.set_export_range(90)

    def set_export_range(self, days: int):
        from PyQt6.QtCore import QDate
        date_from, date_to = ed.preset_range(days)
        self.exp_to.setDate(QDate.fromString(date_to, "yyyy-MM-dd"))
        if date_from:
            self.exp_from.setDate(QDate.fromString(date_from, "yyyy-MM-dd"))
        # All time has no start date, and the greyed-out FROM picker is what
        # says so now that the separate toggle is gone.
        self.exp_from.setDisabled(days <= 0)
        for value, button in self.exp_range_buttons.items():
            button.setChecked(value == days)

    def _all_time_selected(self) -> bool:
        button = self.exp_range_buttons.get(0)
        return bool(button is not None and button.isChecked())

    def _export_dates(self) -> tuple[str, str]:
        date_from = ("" if self._all_time_selected()
                     else self.exp_from.date().toString("yyyy-MM-dd"))
        return date_from, self.exp_to.date().toString("yyyy-MM-dd")

    def run_export(self):
        """Fetch the bytes, ask where to save, then open with the system
        handler. The web downloads to the browser's folder; a desktop app that
        did the equivalent silently would be worse, not more native."""
        if not self._can_fetch():
            self.exp_progress.fail("Sign in to export.")
            return
        export_type = self.exp_type.currentData() or "passport"
        date_from, date_to = self._export_dates()
        self.exp_run.setEnabled(False)
        self.exp_progress.set_stage("requesting")

        # Record the request in history/audit. Fire-and-forget, exactly as the
        # web does — it must never delay or block the bytes.
        spawn_worker(self.client, "POST", "/v1/exports",
                     body={"type": export_type,
                           "params": ed.export_params(date_from, date_to)},
                     timeout=10, parent=self, track=self._page_workers,
                     on_ok=lambda _d: self.refresh_exports())

        if export_type == "passport":
            path = f"/v1/passport{ed.passport_query(date_from, date_to)}"
            method = "POST"
        else:
            path, _fmt = ed.logs_endpoint(export_type)
            method = "GET"
        spawn_worker(self.client, method, path, timeout=120, parent=self,
                     track=self._page_workers, raw=True,
                     on_ok=lambda payload: self._on_export_bytes(
                         export_type, payload),
                     on_err=lambda err: self._on_export_failed(err))

    def _on_export_failed(self, err):
        self.exp_run.setEnabled(True)
        self.exp_progress.fail(f"Export failed — {err}")

    def _on_export_bytes(self, export_type: str, payload):
        from PyQt6.QtWidgets import QFileDialog
        from PyQt6.QtGui import QDesktopServices
        from PyQt6.QtCore import QUrl

        self.exp_progress.set_stage("received")
        data = payload.get("body") if isinstance(payload, dict) else payload
        content_type = (payload.get("content_type")
                        if isinstance(payload, dict) else None)
        if not data:
            self._on_export_failed("the server returned an empty file")
            return
        suggested = ed.suggested_filename(export_type, content_type)
        target, _sel = QFileDialog.getSaveFileName(
            self, "Save export", suggested,
            ed.file_filter(export_type, content_type))
        if not target:
            self.exp_run.setEnabled(True)
            self.exp_progress.hide()
            return
        self.exp_progress.set_stage("saving")
        try:
            with open(target, "wb") as fh:
                fh.write(data if isinstance(data, (bytes, bytearray))
                         else str(data).encode("utf-8"))
        except OSError as exc:
            self._on_export_failed(f"could not write the file — {exc}")
            return
        self.exp_run.setEnabled(True)
        self.exp_progress.finish(f"saved to {target}")
        QDesktopServices.openUrl(QUrl.fromLocalFile(target))

    def refresh_export_meta(self):
        if not self._can_fetch():
            return
        spawn_worker(self.client, "GET", "/v1/verify", timeout=30, parent=self,
                     track=self._page_workers, on_ok=self._on_export_verify,
                     on_err=lambda _e: self._on_export_verify(None))
        spawn_worker(self.client, "GET", "/v1/logs?limit=1", timeout=12,
                     parent=self, track=self._page_workers,
                     on_ok=self._on_export_head,
                     on_err=lambda _e: self._on_export_head(None))
        spawn_worker(self.client, "GET", "/v1/auth/me", timeout=10, parent=self,
                     track=self._page_workers, on_ok=self._on_export_org,
                     on_err=lambda _e: self._on_export_org(None))
        spawn_worker(self.client, "GET", "/v1/anchors/sla", timeout=10,
                     parent=self, track=self._page_workers,
                     on_ok=lambda d: self.exp_meta["sla"].setText(
                         vd.sla_text(d) or "—"),
                     on_err=lambda _e: self.exp_meta["sla"].setText("—"))

    def _on_export_verify(self, data):
        self.exp_meta["records"].setText(ed.records_text(data))
        text, tone = ed.integrity_text(data)
        colour = {"ok": OK_GREEN, "bad": BAD_RED,
                  "warn": WARN_AMBER}.get(tone, WEB["muted"])
        self.exp_meta["integrity"].setStyleSheet(
            f"color: {colour}; font-family: '{_pick_font('disp')}';"
            f" font-size: 11px; font-weight: 800; background: transparent;")
        self.exp_meta["integrity"].setText(text)

    def _on_export_head(self, data):
        items = hd.dict_rows((data or {}).get("items")
                             if isinstance(data, dict) else None)
        self._export_head = str(items[0].get("chain_hash") or "") if items else ""
        self._render_export_metadata()

    def _on_export_org(self, data):
        me = data if isinstance(data, dict) else {}
        self._export_org = str(me.get("org_id") or "")
        # The web drives the initial mask state off this preference
        # (html:2170 `masked = !!prefs.hide_sensitive_metadata`, fed by
        # /v1/auth/me). We were fetching the payload and discarding
        # `preferences`, so a user who had turned the setting on still got
        # their chain head and org id in plain text on this card.
        prefs = me.get("preferences")
        if isinstance(prefs, dict):
            self._hide_metadata = bool(prefs.get("hide_sensitive_metadata"))
            # setChecked only emits `toggled` on a real change; the explicit
            # render below covers the case where it does not.
            self.exp_reveal.setChecked(not self._hide_metadata)
        self._render_export_metadata()

    def on_reveal_metadata(self, revealed: bool):
        self.exp_reveal.setText("hide" if revealed else "reveal")
        self._render_export_metadata()

    def _render_export_metadata(self):
        revealed = self.exp_reveal.isChecked()
        for key, value in (("head", self._export_head), ("org", self._export_org)):
            if not revealed:
                self.exp_meta[key].setText(ed.mask(value))
            elif key == "head":
                # Grouped so the label has somewhere to wrap (see ed.grouped);
                # the org id is short and its own separators already break it.
                self.exp_meta[key].setText(ed.grouped(value))
            else:
                self.exp_meta[key].setText(value or "—")

    def copy_chain_head(self):
        from PyQt6.QtWidgets import QApplication
        if not self._export_head:
            self.toast.show_message("No chain head yet")
            return
        QApplication.clipboard().setText(self._export_head)
        self.toast.show_message("Chain head copied")

    def refresh_exports(self):
        if not self._can_fetch():
            return
        spawn_worker(self.client, "GET", "/v1/exports", timeout=12, parent=self,
                     track=self._page_workers, on_ok=self._on_exports,
                     on_err=lambda _e: self._on_exports(None, ok=False))

    def _on_exports(self, data, ok: bool = True):
        rows = ed.history_rows(data) if ok else []
        self._fill_rows(
            self.exp_history, 0, self.exp_history_empty, rows, history_row,
            resolve(ok, bool(rows)),
            empty_title="No exports yet",
            empty_body="Generated exports are recorded here with who asked "
                       "for them and over what range.")

    # ── Access / keys (D9) ─────────────────────────────────────────────────
    def _init_access(self):
        self._init_verify()                 # shares the bucket
        self._key_quota = None

    def refresh_keys(self):
        if not self._can_fetch():
            return
        spawn_worker(self.client, "GET", "/v1/keys", timeout=12, parent=self,
                     track=self._page_workers, on_ok=self._on_keys,
                     on_err=lambda err: self._on_keys(
                         None, ok=False, status=status_of(err)))
        # The key limit rides on /v1/usage's `quota` block (account.py:231),
        # not a quota endpoint of its own — days=1 keeps the payload small.
        spawn_worker(self.client, "GET", "/v1/usage?days=1", timeout=10,
                     parent=self, track=self._page_workers,
                     on_ok=lambda d: self._on_key_quota(
                         (d or {}).get("quota") if isinstance(d, dict) else None),
                     on_err=lambda _e: self._on_key_quota(None))

    def _on_key_quota(self, data):
        self._key_quota = data if isinstance(data, dict) else None
        self._apply_key_stats(getattr(self, "_key_payload", None))

    def _apply_key_stats(self, payload):
        for label, (_name, value, _tone) in zip(
                self.key_stats, ad.stat_row(payload, self._key_quota)):
            label.setText(value)

    def _on_keys(self, data, ok: bool = True, status: int | None = None):
        # 403 is not a failure: a member simply may not manage keys, and the
        # page says so rather than showing them an error they cannot act on.
        member = status == 403
        self.key_member_notice.setVisible(member)
        for button in (self.key_new_btn, self.key_regen_btn):
            button.setEnabled(not member)

        self._key_payload = data if ok else None
        self._apply_key_stats(self._key_payload)
        rows = ad.key_rows(data) if ok else []
        if member:
            state, title, body = (PanelState.EMPTY, "Managed by an admin",
                                  ad.MEMBER_NOTICE)
        else:
            state = resolve(ok, bool(rows))
            title, body = ("No API keys yet",
                           "Create one to start reporting interactions from "
                           "your SDK.")
        self._fill_rows(self.key_rows, 0, self.key_empty, rows,
                        lambda row: key_row(row, self.confirm_revoke_key),
                        state, empty_title=title, empty_body=body)

    def create_key(self):
        dialog = NewKeyDialog(self)
        if dialog.exec() != NewKeyDialog.DialogCode.Accepted:
            return
        name, days = dialog.values()
        spawn_worker(self.client, "POST", "/v1/keys",
                     body=ad.create_body(name, days), timeout=15, parent=self,
                     track=self._page_workers,
                     on_ok=lambda d: self._show_new_key("New API key", d),
                     on_err=self._on_key_create_failed)

    def _on_key_create_failed(self, err):
        # The 402 body is a structured detail ({code, message, used, included});
        # only its `message` survives the worker's string-only error contract,
        # so prefer the server's own sentence and fall back to ours when the
        # detail did not arrive at all (transport failure, empty body).
        if status_of(err) == 402:
            self.toast.show_message(detail_of(err) or ad.LIMIT_REACHED_FALLBACK)
            return
        self.toast.show_message(f"Create failed — {err}")

    def _show_new_key(self, title: str, data):
        """The ONE place a plaintext key is displayed.

        The value goes straight from the response into the dialog and is never
        stored on this window, never logged, never written to QSettings or a
        file. The dialog blanks its own field on close.
        """
        key = (data or {}).get("api_key") if isinstance(data, dict) else None
        if not key:
            self.toast.show_message("The server did not return a key")
            return
        name = (data or {}).get("name") or ""
        dialog = ShownOnceDialog(f"{title}{f' · {name}' if name else ''}",
                                 key, self)
        del key                      # no plaintext lingering in this frame
        dialog.exec()
        self.toast.show_message("API key created")
        self.refresh_keys()

    def confirm_revoke_key(self, row: dict):
        """Irreversible and it breaks whatever is using the key, so it is
        confirmed first — and then step-up gated by the server."""
        from PyQt6.QtWidgets import QMessageBox
        box = QMessageBox(self)
        box.setWindowTitle(ad.REVOKE_CONFIRM_TITLE)
        box.setText(ad.REVOKE_CONFIRM_TITLE)
        box.setInformativeText(ad.revoke_confirm_body(row["name"]))
        box.setIcon(QMessageBox.Icon.Warning)
        box.setStandardButtons(QMessageBox.StandardButton.Cancel
                               | QMessageBox.StandardButton.Yes)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        box.button(QMessageBox.StandardButton.Yes).setText("Revoke key")
        if box.exec() != QMessageBox.StandardButton.Yes:
            return
        self._revoke_key(row, retry=True)

    def _revoke_key(self, row: dict, *, retry: bool):
        spawn_worker(
            self.client, "DELETE", f"/v1/keys/{quote(row['id'], safe='')}",
            timeout=15, parent=self, track=self._page_workers,
            on_ok=lambda _d: self._after_revoke(),
            on_err=lambda err: self._on_revoke_error(row, err, retry))

    def _after_revoke(self):
        self.toast.show_message("Access revoked")
        self.refresh_keys()

    def _on_revoke_error(self, row: dict, err, _retry: bool):
        """A 403 step_up_required is the server asking to re-authenticate, not
        a failure.

        The client already emits `step_up_required` for it and the app's
        existing handler (omni_fox._on_step_up_required) runs the D1 dialog and
        replays the request — so this must NOT open a second dialog. It says
        what is happening and re-reads the list once the replay has had time to
        land."""
        # 403 + the step-up detail. The client emits `step_up_required` for
        # it and the app's existing handler runs the D1 dialog and replays the
        # request — so this must NOT open a second one.
        if status_of(err) == 403 and "step_up" in str(err):
            self.toast.show_message("Confirm your identity to revoke this key")
            QTimer.singleShot(6000, self.refresh_keys)
            return
        self.toast.show_message(f"Revoke failed — {err}")

    def regenerate_key(self):
        from PyQt6.QtWidgets import QInputDialog, QMessageBox
        if QMessageBox.question(
                self, "Regenerate the API key?", ad.REGENERATE_CONFIRM,
                QMessageBox.StandardButton.Cancel
                | QMessageBox.StandardButton.Yes,
                QMessageBox.StandardButton.Cancel
        ) != QMessageBox.StandardButton.Yes:
            return

        def code_step(_data):
            code, ok = QInputDialog.getText(
                self, "Check your email",
                "Enter the 6-digit code we just emailed you:")
            if not ok or not code.strip():
                return
            spawn_worker(
                self.client, "POST", "/v1/keys/regenerate/confirm",
                body={"code": code.strip()}, timeout=15, parent=self,
                track=self._page_workers,
                on_ok=lambda d: self._show_new_key("Regenerated API key", d),
                on_err=lambda err: self.toast.show_message(f"Regenerate failed — {err}"))

        spawn_worker(self.client, "POST", "/v1/keys/regenerate/request",
                     body={}, timeout=15, parent=self,
                     track=self._page_workers, on_ok=code_step,
                     on_err=lambda err: self.toast.show_message(f"Could not send a code — {err}"))

    def test_sdk_connection(self):
        """Uses the stored org key against /v1/health, which is Bearer-only."""
        self.sdk_test_out.setText("testing…")
        self.sdk_test_out.setStyleSheet(
            f"color: {WEB['muted']}; font-family: '{_pick_font('mono')}';"
            f" font-size: 11px; background: transparent;")

        def finish(ok, status=None):
            message, tone = ad.connection_result(ok, status=status)
            colour = OK_GREEN if tone == "ok" else BAD_RED
            self.sdk_test_out.setStyleSheet(
                f"color: {colour}; font-family: '{_pick_font('mono')}';"
                f" font-size: 11px; font-weight: 800; background: transparent;")
            self.sdk_test_out.setText(message)

        if not self.settings.org_api_key():
            finish(False)
            self.sdk_test_out.setText("no org key saved on this machine")
            return
        spawn_worker(self.client, "GET", "/v1/health", timeout=10, parent=self,
                     track=self._page_workers, force_bearer=True,
                     on_ok=lambda _d: finish(True),
                     on_err=lambda err: finish(
                         False, status_of(err)))

    # ── Policy ruleset (D7) ────────────────────────────────────────────────
    def _init_policy(self):
        self._init_verify()                 # shares the page-worker bucket
        # Judge key state. `_pol_typed` is NOT kept here — a typed key lives in
        # its QLineEdit and nowhere else, and is read only at the moment the
        # save body is built. What we do keep is which providers the user asked
        # to REMOVE, which is a decision, not a secret.
        self._pol_judge = pd.judge_view(None)
        self._pol_cleared: set = set()
        self._pol_loaded = False
        self._pol_dirty = False
        self._role = getattr(self, "_role", "")
        self._set_policy_panel(PanelState.LOADING)
        self._apply_policy_permission()
        self._render_judge_keys()

    # -- permission -----------------------------------------------------
    def _can_edit_policy(self) -> bool:
        """PUT /v1/policies needs an admin HUMAN session (policies.py:129-133).

        An org API key authenticates the SDK, not a person, so key-only mode is
        read-only here no matter how the workspace is configured.
        """
        return bool(self.client.has_session()) and self._role == "admin"

    def _apply_policy_permission(self):
        editable = self._can_edit_policy()
        for widget in (self.pol_save, self.pol_tokens, self.pol_enforcement,
                       self.pol_confidence, self.pol_notify, self.pol_email,
                       self.pol_webhook, self.pol_provider):
            widget.setEnabled(editable)
        for row in self.pol_rows.values():
            row.toggle.setEnabled(editable)
        for button in self.pol_mode_btns.values():
            button.setEnabled(editable)
        self.pol_save.setToolTip(
            "" if editable else "Changing the ruleset needs an admin account")
        self.pol_notice.setText(self._policy_denied())
        self.pol_notice_card.setVisible(not editable)
        # The search box stays live for everyone — reading what is enforced is
        # not a privileged act, and it is the point of showing members the page.
        self._render_judge_keys()

    # -- load -----------------------------------------------------------
    def refresh_policy(self):
        if not self._can_fetch():
            return
        if not self._pol_loaded:
            self._set_policy_panel(PanelState.LOADING)
        spawn_worker(self.client, "GET", "/v1/policies", timeout=12,
                     parent=self, track=self._page_workers,
                     on_ok=self._on_policy, on_err=self._on_policy_failed)
        # The role decides whether this page is editable and only /v1/auth/me
        # knows it; in key-only mode this 401s, which is itself the answer.
        spawn_worker(self.client, "GET", "/v1/auth/me", timeout=10, parent=self,
                     track=self._page_workers,
                     on_ok=lambda d: self._on_policy_role(d),
                     on_err=lambda _e: self._on_policy_role(None))

    def _on_policy_role(self, data):
        if isinstance(data, dict):
            self._role = str(data.get("role") or "")
        # A failed /v1/auth/me is not evidence of a demotion — keep whatever
        # sign-in told us. Downgrading an admin's page because one request
        # timed out would be the same "absence of an answer read as an answer"
        # mistake the panel states exist to prevent.
        self._apply_policy_permission()

    def _on_policy(self, data):
        view = pd.policy_view(data)
        self._pol_judge = pd.judge_view(data)
        self._pol_cleared.clear()
        for key, row in self.pol_rows.items():
            row.toggle.setChecked(bool(view[key]))
        self.pol_tokens.setValue(view["max_token_threshold"])
        for combo, key in ((self.pol_enforcement, "enforcement_mode"),
                           (self.pol_confidence, "confidence_threshold"),
                           (self.pol_notify, "notify_on_breach"),
                           (self.pol_provider, "judge_provider")):
            index = combo.findData(view[key])
            if index >= 0:
                combo.setCurrentIndex(index)
        self.pol_email.setText(view["notify_email"])
        self.pol_webhook.setText(view["notify_webhook_url"])
        # Every setter above emits its change signal, so the form is "dirty"
        # from loading it. Clearing here is what makes the indicator mean
        # "you changed something" rather than "this page exists".
        self._pol_loaded = True
        self._set_policy_status("", "mute")
        self._pol_dirty = False
        self._set_policy_panel(PanelState.OK)
        # `_on_policy_failed` turns Save off. Without this, a load that failed
        # once left the button dead for the rest of the session even after a
        # retry succeeded — found by rendering the retry, not by a test, and
        # the test that "covered" it was calling this helper itself.
        self._apply_policy_permission()
        self._render_judge_keys()

    def _on_policy_failed(self, err):
        self._pol_loaded = False
        # The FORM IS HIDDEN, not merely disabled. A greyed-out "Block PII: ON"
        # still reads as a fact about the workspace, and we did not learn it —
        # this is the D4 false-empty-state lesson on a page where the wrong
        # reading is "here is what protects you".
        status = status_of(err)
        self._set_policy_panel(PanelState.ERROR, detail=str(err),
                               code=f"HTTP {status}" if status else "")
        self._set_policy_status("", "mute")
        self.pol_save.setEnabled(False)

    def _set_policy_panel(self, state, *, detail: str = "", code: str = ""):
        # A transport error can be a paragraph of OpenSSL — the house voice
        # says what it means, and the tooltip keeps the raw text for a report.
        self.pol_state.set_state(
            state, error_detail=(
                f"Foxy couldn't read this workspace's ruleset"
                f"{f' ({code})' if code else ''}. Nothing on this page is "
                f"claimed about what your workspace enforces."))
        self.pol_state.setToolTip(detail or "")
        self.pol_state.setVisible(state is not PanelState.OK)
        self.pol_form.setVisible(state is PanelState.OK)

    # -- judge block ----------------------------------------------------
    def on_judge_provider_changed(self):
        self._render_judge_keys()
        self.mark_policy_dirty()

    def set_judge_key_mode(self, mode: str):
        # Re-checked here, not just on the disabled button — same stance as
        # save_policy(). Neither this nor clear_judge_key() sends anything, so
        # nothing today can reach them past the gate; the point is that the
        # rule lives in the methods rather than in the widgets' state.
        if not self._can_edit_policy():
            return
        if mode == "platform" and not self._pol_judge.get("platform_allowed"):
            # The button is disabled, so this is only reachable if the plan
            # changed under us. Say what the plan allows, don't just refuse.
            self.toast.show_message(pd.PLATFORM_LOCKED)
            self._render_judge_keys()
            return
        self._pol_judge["mode"] = mode
        self._render_judge_keys()
        self.mark_policy_dirty()

    def clear_judge_key(self, provider: str):
        """Mark a stored key for removal. Nothing is sent until save — the web
        says the same ("Key will be removed when you save the ruleset")."""
        if not self._can_edit_policy():
            return
        self._pol_cleared.add(provider)
        self.pol_key_rows[provider].clear_field()
        self._render_judge_keys()
        self.mark_policy_dirty()
        self.toast.show_message("Key will be removed when you save the ruleset")

    def _render_judge_keys(self):
        judge = self._pol_judge
        provider = self.pol_provider.currentData() or "gemini"
        editable = self._can_edit_policy()
        for mode, button in self.pol_mode_btns.items():
            button.setChecked(mode == judge["mode"])
        platform = self.pol_mode_btns["platform"]
        allowed = bool(judge.get("platform_allowed"))
        platform.setEnabled(editable and allowed)
        # The web puts a `.lockchip` span inside the button (html:2669). A
        # button label is one string here, so the plan rides in the text — in
        # real letters, because small-caps unicode is not readable text and a
        # screen reader would spell it out.
        platform.setText("Foxy's managed keys"
                         + ("" if allowed else "  (Premium)"))
        platform.setToolTip("" if allowed else "Available on the Premium plan")
        self.pol_mode_hint.setText(pd.mode_hint(judge["mode"]))
        for name, row in self.pol_key_rows.items():
            row.apply(pd.key_field(name, judge_provider=provider,
                                   mode=judge["mode"],
                                   key_set=judge[f"{name}_key_set"],
                                   cleared=name in self._pol_cleared),
                      editable=editable)

    # -- dirty state ----------------------------------------------------
    def mark_policy_dirty(self):
        if not self._pol_loaded:
            return          # loading the form is not the user changing it
        self._pol_dirty = True
        self._set_policy_status(pd.DIRTY, "warn")

    def _set_policy_status(self, text: str, tone: str = "mute", *, secrets=()):
        """The ONE funnel for this line, so redaction cannot be forgotten.

        A server message can quote what it rejected: FastAPI's 422 body echoes
        the offending value, which put a mis-pasted API key on screen in
        cleartext — through the password field it was typed into, and into the
        accessibility tree with it. `secrets` is what the key fields held at
        the moment of the failure; `redact` also sweeps for anything
        key-shaped that arrived some other way.
        """
        safe = pd.redact(text, secrets)
        colour = {"ok": OK_GREEN, "bad": BAD_RED,
                  "warn": WARN_AMBER}.get(tone, WEB["muted"])
        self.pol_status.setStyleSheet(
            f"color: {colour}; font-family: '{_pick_font('mono')}';"
            f" font-size: 10px; font-weight: 800; background: transparent;")
        self.pol_status.setText(safe)
        self.pol_status.setAccessibleName(safe)

    def _markable_policy_fields(self):
        return (self.pol_email, self.pol_webhook,
                *(row.field for row in self.pol_key_rows.values()))

    def _mark_policy_field(self, target):
        """Put the error on the FIELD, not only in a sentence about it.

        "check the highlighted field" is a promise that something is
        highlighted; the focus ring alone is the same orange every focused
        input gets, so it says "you are here", not "this is wrong".

        Driven by a dynamic property, because appending a bare
        `QLineEdit { border-color: red }` rule loses to the `:focus` rule
        already in the sheet — Qt ranks a pseudo-class above a plain type
        selector regardless of order, and this handler focuses the field it
        just marked, so the red never showed. Qt does not re-evaluate property
        selectors on setProperty either; unpolish/polish is what applies it.
        """
        for widget in self._markable_policy_fields():
            bad = widget is target
            if widget.property("invalid") != ("true" if bad else "false"):
                widget.setProperty("invalid", "true" if bad else "false")
                widget.style().unpolish(widget)
                widget.style().polish(widget)
            widget.setAccessibleDescription(
                self._policy_error_label(widget).text() if bad else "")

    def filter_safeguards(self):
        query = self.pol_search.text()
        for spec, hit in zip(pd.SAFEGUARDS, pd.search_hits(query)):
            self.pol_rows[spec["key"]].setVisible(hit)
        self.pol_no_match.setVisible(pd.no_match(query))

    # -- save -----------------------------------------------------------
    def _policy_form(self) -> dict:
        form = {spec["key"]: self.pol_rows[spec["key"]].toggle.isChecked()
                for spec in pd.SAFEGUARDS}
        form.update({
            "max_token_threshold": self.pol_tokens.value(),
            "enforcement_mode": self.pol_enforcement.currentData(),
            "confidence_threshold": self.pol_confidence.currentData(),
            "notify_on_breach": self.pol_notify.currentData(),
            "notify_email": self.pol_email.text(),
            "notify_webhook_url": self.pol_webhook.text(),
            "judge_provider": self.pol_provider.currentData(),
        })
        return form

    def _policy_denied(self):
        """One place decides what "you may not" says, so the toast and the
        on-page card cannot disagree about why."""
        return (pd.MEMBER_NOTICE if self.client.has_session()
                else pd.KEY_ONLY_NOTICE)

    def _policy_error_label(self, target):
        """The error line that belongs to `target`.

        Each key row carries its own, because a key problem reported under the
        webhook field in the other card is a message about something the user
        is not looking at.
        """
        for row in self.pol_key_rows.values():
            if row.field is target:
                return row.error
        return self.pol_field_error

    def _clear_policy_errors(self):
        self.pol_field_error.hide()
        for row in self.pol_key_rows.values():
            row.error.hide()
        self._mark_policy_field(None)

    def _refuse_policy_field(self, target, message: str):
        label = self._policy_error_label(target)
        label.setText(message)
        label.show()
        self._mark_policy_field(target)
        target.setFocus()
        self._set_policy_status("Not saved — check the highlighted field", "bad")

    def save_policy(self):
        if not self._can_edit_policy():
            self.toast.show_message(self._policy_denied())
            return
        form = self._policy_form()
        self._clear_policy_errors()
        problem = pd.validate(form)
        if problem:
            field, message = problem
            self.pol_alerts_btn.setChecked(True)     # the field is in there
            self._refuse_policy_field({"notify_email": self.pol_email,
                                       "notify_webhook_url": self.pol_webhook
                                       }[field], message)
            return
        # The ONLY read of a typed key, and it goes straight into the request
        # body. Nothing between here and the wire holds it.
        typed = {name: row.typed() for name, row in self.pol_key_rows.items()
                 if pd.key_field(name,
                                 judge_provider=form["judge_provider"],
                                 mode=self._pol_judge["mode"],
                                 key_set=False)["used"]}
        # The server's own cap, checked here so an over-length paste never
        # becomes a request: its 422 quotes the value it rejected, and that is
        # how a key ended up rendered in cleartext.
        over = pd.key_too_long(typed)
        if over:
            self._refuse_policy_field(self.pol_key_rows[over].field,
                                      pd.KEY_TOO_LONG)
            return
        body = pd.save_body(form, self._pol_judge, typed=typed,
                            cleared=tuple(self._pol_cleared))
        self.pol_save.setEnabled(False)
        self._set_policy_status(pd.SAVING, "mute")
        spawn_worker(self.client, "PUT", "/v1/policies", body=body, timeout=20,
                     parent=self, track=self._page_workers,
                     on_ok=self._on_policy_saved,
                     on_err=self._on_policy_save_failed)

    def _on_policy_saved(self, data):
        self.pol_save.setEnabled(True)
        self._pol_dirty = False
        self._pol_cleared.clear()
        # Re-read from the RESPONSE so the key-set chips reflect what the
        # server actually stored, and drop the typed keys — they have been
        # handed over and this app has no reason to still hold them.
        self._pol_judge = pd.judge_view(data)
        for row in self.pol_key_rows.values():
            row.clear_field()
        self._render_judge_keys()
        message, tone = pd.save_result(None)
        self._set_policy_status(message, tone)
        self.toast.show_message(pd.save_toast(None))

    def _on_policy_save_failed(self, err):
        self.pol_save.setEnabled(True)
        # Read the fields BEFORE clearing them: a rejected value can come back
        # quoted in the server's message, and these are what to redact it
        # against. Held for the length of this call and never assigned to the
        # window — the same rule as the send path.
        secrets = [row.typed() for row in self.pol_key_rows.values()]
        # Cleared here too: the save did not happen, and a key sitting in a
        # box across a failure is a secret we are holding for no reason. The
        # user re-enters it, which is the safe direction to fail.
        for row in self.pol_key_rows.values():
            row.clear_field()
        self._pol_cleared.clear()
        self._render_judge_keys()
        status = status_of(err) or 0
        message, tone = pd.save_result(status, detail_of(err))
        self._set_policy_status(message, tone, secrets=secrets)
        self.toast.show_message(pd.save_toast(status))

    # ── Usage & billing (D10) ──────────────────────────────────────────────
    def _init_billing(self):
        self._init_verify()                 # shares the page-worker bucket
        self._bil_plan = bd.plan_view(None)
        # The last invoices we were given, so the rows can be rebuilt when the
        # role arrives — the PDF link is admin-only. None means "not asked".
        self._bil_invoices = None

    def refresh_billing(self):
        if not self._can_fetch():
            return
        spawn_worker(self.client, "GET", "/v1/usage?days=30", timeout=15,
                     parent=self, track=self._page_workers,
                     on_ok=self._on_billing_usage,
                     on_err=lambda _e: self._on_billing_usage(None, ok=False))
        spawn_worker(self.client, "GET", "/v1/invoices", timeout=15,
                     parent=self, track=self._page_workers,
                     on_ok=self._on_invoices,
                     on_err=lambda _e: self._on_invoices(None, ok=False))
        spawn_worker(self.client, "GET", "/v1/billing/plan", timeout=12,
                     parent=self, track=self._page_workers,
                     on_ok=self._on_billing_plan,
                     on_err=lambda _e: self._on_billing_plan(None, ok=False))
        # The role gates both billing side-doors and `self._role` is set once,
        # at sign-in. Policy re-fetches it per visit for exactly this reason:
        # otherwise a user promoted to admin mid-session keeps a hidden
        # "Manage billing" and no invoice links until they sign out and in.
        spawn_worker(self.client, "GET", "/v1/auth/me", timeout=10, parent=self,
                     track=self._page_workers, on_ok=self._on_billing_role,
                     on_err=lambda _e: self._on_billing_role(None))

    def _on_billing_role(self, data):
        if isinstance(data, dict):
            self._role = str(data.get("role") or "")
        # A failed /v1/auth/me is not evidence of a demotion — keep what we
        # already know and re-apply, same as the Policy page.
        self._apply_billing_buttons()
        # The invoice rows carry the PDF link, which is admin-only too, so they
        # are rebuilt against the role we just learned.
        if self._bil_invoices is not None:
            self._on_invoices(self._bil_invoices)

    def _on_billing_usage(self, data, ok: bool = True):
        payload = data if isinstance(data, dict) else {}
        quota = payload.get("quota") if ok else None
        days = payload.get("days") if ok else None
        for tile, (label, value, _tone) in zip(self.bil_tiles,
                                               bd.stat_row(quota, days)):
            _frame, value_lbl, caption = tile
            value_lbl.setText(value)
            caption.setText(label.upper())
        tier = (quota or {}).get("plan_tier") if isinstance(quota, dict) else None
        self.bil_eyebrow.setText(
            f"● PLAN & CONSUMPTION · {str(tier).upper()}" if tier
            else "● PLAN & CONSUMPTION")

        text = bd.entitlements(quota)
        self.bil_entitlements.setText(text)
        self.bil_ent_card.setVisible(bool(text))
        warning = bd.over_quota_banner(quota)
        self.bil_warning.setText(warning)
        self.bil_warn_card.setVisible(bool(warning))

        pct, label, tone = bd.headroom(quota)
        self.bil_gauge_lbl.setText(label)
        self.bil_gauge.set_options(
            type="gauge", height=14, value=pct, max=100,
            unlimited=bd.unlimited(quota), tone=tone,
            aria=f"Quota headroom — {label}")
        points = bd.trend_points(days)
        state = resolve(ok, bool(points))
        self.bil_trend.set_options(
            type="area", height=130, tone="fox", data=points,
            aria="Audited interactions per day, last 30 days",
            empty=chart_empty(state, empty_title=bd.USAGE_EMPTY[0],
                              empty_body=bd.USAGE_EMPTY[1]))
        rows = bd.usage_rows(days)
        self._fill_rows(self.bil_usage_rows, 0, self.bil_usage_empty, rows,
                        usage_row, resolve(ok, bool(rows)),
                        empty_title=bd.USAGE_EMPTY[0],
                        empty_body=bd.USAGE_EMPTY[1])

    def _on_invoices(self, data, ok: bool = True):
        self._bil_invoices = data if ok else None
        rows = bd.invoice_rows(data) if ok else []
        # The PDF link is admin-only (account.py:423-427), so a member gets
        # the row without a control that would answer 403.
        opener = self.open_invoice if self._is_billing_admin() else None
        self._fill_rows(self.bil_inv_rows, 0, self.bil_inv_empty, rows,
                        lambda row: invoice_row(row, opener),
                        resolve(ok, bool(rows)),
                        empty_title=bd.INVOICE_EMPTY[0],
                        empty_body=bd.INVOICE_EMPTY[1])
        points = bd.invoice_points(data) if ok else []
        self.bil_inv_chart.set_options(
            type="bar", height=130, tone="blue", data=points,
            aria="Invoice totals over time",
            empty=chart_empty(resolve(ok, bool(points)),
                              empty_title=bd.INVOICE_EMPTY[0],
                              empty_body=bd.INVOICE_EMPTY[1]))

    def _on_billing_plan(self, data, ok: bool = True):
        view = bd.plan_view(data) if ok else bd.plan_view(None)
        self._bil_plan = view
        for key, (holder, value) in self.bil_plan_rows.items():
            value.setText(view[key] or bd.MISSING)
            # The row keeps the width it computed for the previous text until
            # its layout re-activates, and a repaint in between drew "1,000"
            # as "1,0". A half-drawn number on a billing page is a wrong one.
            value.updateGeometry()
            holder.layout().activate()
        # Only when a trial exists (web html:1517) — an empty "trial ends" row
        # invites the reading that one is running.
        self.bil_plan_rows["trial_ends"][0].setVisible(bool(view["trial_ends"]))
        known = bool(ok and view["known"])
        self.bil_plan_state.set_state(
            PanelState.OK if known else
            PanelState.ERROR if not ok else PanelState.EMPTY,
            empty_title="No plan on file",
            empty_body="This workspace has no subscription record yet.")
        self.bil_plan_state.setVisible(not known)
        self._apply_billing_buttons()

    def _is_billing_admin(self) -> bool:
        """Both billing side-doors are admin-only: POST /v1/billing/portal
        (billing.py:583) and GET /v1/invoices/{id}/link (account.py:426)."""
        return bool(self.client.has_session()) and self._role == "admin"

    def _apply_billing_buttons(self):
        """Show a button only where it leads somewhere.

        The web keys both buttons off `has_billing_account` alone, so a member
        sees "Manage billing" and gets a 403 for pressing it. A control that
        cannot work is worse than no control, so this also requires the role
        the endpoint requires. Deliberate divergence from the web.
        """
        admin = self._is_billing_admin()
        has_account = bool(self._bil_plan["has_billing_account"])
        self.bil_manage.setVisible(admin and has_account)
        # Upgrade goes to the public pricing page — no account, no role needed.
        self.bil_upgrade.setVisible(bool(self._bil_plan["known"])
                                    and not has_account)

    def open_billing_portal(self):
        self.bil_manage.setEnabled(False)
        self.bil_manage.setText("opening…")

        def restore():
            self.bil_manage.setEnabled(True)
            self.bil_manage.setText("Manage billing ↗")

        def opened(data):
            restore()
            url = (data or {}).get("portal_url") if isinstance(data, dict) else ""
            if url:
                QDesktopServices.openUrl(QUrl(url))
                self.toast.show_message(
                    "Opening the billing portal in your browser")
            else:
                self.toast.show_message(bd.portal_result(None))

        def failed(err):
            restore()
            self.toast.show_message(bd.portal_result(status_of(err) or 0))

        spawn_worker(self.client, "POST", "/v1/billing/portal", timeout=20,
                     parent=self, track=self._page_workers, on_ok=opened,
                     on_err=failed)

    def open_invoice(self, invoice_id: str):
        def opened(data):
            url = (data or {}).get("url") if isinstance(data, dict) else ""
            if url:
                QDesktopServices.openUrl(QUrl(url))
            else:
                self.toast.show_message(bd.invoice_link_result(404))

        spawn_worker(self.client, "GET",
                     f"/v1/invoices/{quote(str(invoice_id), safe='')}/link",
                     timeout=20, parent=self, track=self._page_workers,
                     on_ok=opened,
                     on_err=lambda err: self.toast.show_message(
                         bd.invoice_link_result(status_of(err) or 0)))

    def open_pricing(self):
        QDesktopServices.openUrl(QUrl(bd.PRICING_URL))

    # -- Settings, account half (D11a) --------------------------------------
    def _init_settings(self):
        self._init_verify()                 # shares the page-worker bucket
        self._set_me = sd.identity_view(None)
        self._badge = sd.badge_view(None)
        self._pref_loading = False
        self._apply_mfa_state()

    def refresh_settings(self):
        if not self._can_fetch():
            return
        spawn_worker(self.client, "GET", "/v1/auth/me", timeout=12, parent=self,
                     track=self._page_workers, on_ok=self._on_settings_me,
                     on_err=lambda _e: self._on_settings_me(None))
        spawn_worker(self.client, "GET", "/v1/account/preferences", timeout=12,
                     parent=self, track=self._page_workers,
                     on_ok=self._on_preferences,
                     on_err=lambda _e: self._on_preferences(None))
        spawn_worker(self.client, "GET", "/v1/auth/sessions", timeout=12,
                     parent=self, track=self._page_workers,
                     on_ok=self._on_devices,
                     on_err=lambda _e: self._on_devices(None, ok=False))

    def _on_settings_me(self, data):
        view = sd.identity_view(data)
        if not view["known"]:
            return              # a failed /v1/auth/me tells us nothing new
        self._set_me = view
        if view["role"] != sd.MISSING:
            self._role = view["role"]
        if not self.set_name.hasFocus():
            self.set_name.setText(view["full_name"])
        for key, field in self.set_readonly.items():
            field.setText(view[key])
        self._apply_mfa_state()
        self._apply_policy_permission()
        self._apply_billing_buttons()

    def save_display_name(self):
        self._set_status(self.set_name_status, "saving...", "mute")
        spawn_worker(
            self.client, "PUT", "/v1/account/profile",
            body=sd.profile_body(self.set_name.text()), timeout=15,
            parent=self, track=self._page_workers,
            on_ok=lambda _d: self._set_status(self.set_name_status,
                                              *sd.save_result(None)),
            on_err=lambda err: self._set_status(
                self.set_name_status,
                *sd.save_result(status_of(err) or 0, detail_of(err))))

    def _set_status(self, label, message: str, tone: str = "mute"):
        colour = {"ok": OK_GREEN, "bad": BAD_RED,
                  "warn": WARN_AMBER}.get(tone, WEB["muted"])
        label.setStyleSheet(
            "color: %s; font-family: %s%s%s; font-size: 10px;"
            " font-weight: 800; background: transparent;"
            % (colour, chr(39), _pick_font("mono"), chr(39)))
        label.setText(message)
        label.setAccessibleName(message)
        label.setVisible(bool(message))

    def _on_preferences(self, data):
        values = sd.preference_values(data)
        self._pref_loading = True
        try:
            for key, row in self.pref_rows.items():
                row.toggle.setChecked(values[key])
        finally:
            self._pref_loading = False

    def save_preference(self, key: str, value: bool):
        """One key per request - the server merges (account.py:613), so a
        stale toggle here can never overwrite a change made elsewhere."""
        if self._pref_loading:
            return              # loading the form is not the user changing it
        spawn_worker(
            self.client, "PUT", "/v1/account/preferences",
            body=sd.preference_body(key, value), timeout=15, parent=self,
            track=self._page_workers,
            on_ok=lambda _d: self.toast.show_message("Preference saved"),
            on_err=lambda err: self._preference_failed(key, err))

    def _preference_failed(self, key: str, err):
        """Put the toggle back. A switch left flipped after a failed save
        would misdescribe the account until the next reload."""
        self.toast.show_message(
            sd.save_result(status_of(err) or 0, detail_of(err),
                           what="preference")[0])
        spawn_worker(self.client, "GET", "/v1/account/preferences", timeout=12,
                     parent=self, track=self._page_workers,
                     on_ok=self._on_preferences, on_err=lambda _e: None)

    def change_password(self):
        current = self.set_pw_fields["current"].text()
        new = self.set_pw_fields["new"].text()
        problem = sd.password_problem(current, new)
        self._set_status(self.set_pw_status, problem or "", "bad")
        if problem:
            self.set_pw_fields["current" if not current else "new"].setFocus()
            return
        # The only read of either value, and it goes straight into the body.
        self.set_pw_btn.setEnabled(False)
        spawn_worker(self.client, "POST", "/v1/auth/change-password",
                     body={"current_password": current, "new_password": new},
                     timeout=20, parent=self, track=self._page_workers,
                     on_ok=lambda _d: self._password_done(None, ""),
                     on_err=lambda err: self._password_done(
                         status_of(err) or 0, detail_of(err)))

    def _password_done(self, status, detail: str):
        # Cleared either way: a password sitting in a box after the request is
        # a secret held for no reason (the D9 rule, applied here).
        for field in self.set_pw_fields.values():
            field.clear()
        self.set_pw_btn.setEnabled(True)
        message, tone = sd.save_result(status, detail)
        ok = status is None
        self._set_status(self.set_pw_status,
                         "password changed" if ok else message,
                         "ok" if ok else tone)
        self.toast.show_message("Password changed" if ok else message)

    def _apply_mfa_state(self):
        on = bool(self._set_me.get("mfa_enabled"))
        self.mfa_state.setText(sd.MFA_ON if on else sd.MFA_OFF)
        self.mfa_off_box.setVisible(not on)
        self.mfa_on_box.setVisible(on)
        if not on:
            self.mfa_confirm_box.hide()

    def mfa_enroll(self):
        self.mfa_enroll_btn.setEnabled(False)

        def sent(_data):
            self.mfa_enroll_btn.setEnabled(True)
            self.mfa_confirm_box.show()
            self.mfa_code.setFocus()
            self.toast.show_message(sd.MFA_CODE_SENT)

        def failed(err):
            self.mfa_enroll_btn.setEnabled(True)
            self.toast.show_message(
                sd.save_result(status_of(err) or 0, detail_of(err))[0])

        spawn_worker(self.client, "POST", "/v1/auth/mfa/enroll", timeout=20,
                     parent=self, track=self._page_workers, on_ok=sent,
                     on_err=failed)

    def mfa_confirm(self):
        code = self.mfa_code.text().strip()
        if not sd.mfa_code_ok(code):
            self.toast.show_message("Enter the 6-digit code from the email.")
            self.mfa_code.setFocus()
            return

        def failed(err):
            self.mfa_code.clear()
            self.toast.show_message(
                sd.save_result(status_of(err) or 0, detail_of(err))[0])

        spawn_worker(self.client, "POST", "/v1/auth/mfa/enable",
                     body={"code": code}, timeout=20, parent=self,
                     track=self._page_workers,
                     on_ok=lambda _d: self._mfa_changed(True), on_err=failed)

    def mfa_disable(self):
        password = self.mfa_password.text()
        if not password:
            self.toast.show_message("Enter your password to turn off 2FA.")
            self.mfa_password.setFocus()
            return

        def failed(err):
            self.mfa_password.clear()
            self.toast.show_message(
                sd.save_result(status_of(err) or 0, detail_of(err))[0])

        spawn_worker(self.client, "POST", "/v1/auth/mfa/disable",
                     body={"password": password}, timeout=20, parent=self,
                     track=self._page_workers,
                     on_ok=lambda _d: self._mfa_changed(False), on_err=failed)

    def _mfa_changed(self, enabled: bool):
        # Both the code and the password are dropped here, whichever path we
        # arrived by - neither has any reason to outlive its request.
        self.mfa_code.clear()
        self.mfa_password.clear()
        self._set_me = dict(self._set_me, mfa_enabled=enabled)
        self._apply_mfa_state()
        self.toast.show_message("Two-factor is now on" if enabled
                                else "Two-factor turned off")

    def save_ip_allowlist(self):
        text = self.ip_allow.text()
        problem = sd.allowlist_problem(text)
        self._set_status(self.ip_status, problem or "", "bad")
        if problem:
            self.ip_allow.setFocus()
            return
        spawn_worker(
            self.client, "POST", "/v1/account/ip-allowlist",
            body=sd.allowlist_body(text), timeout=15, parent=self,
            track=self._page_workers,
            on_ok=lambda _d: self.toast.show_message("IP allow-list saved"),
            on_err=lambda err: self._set_status(
                self.ip_status, sd.save_result(status_of(err) or 0,
                                               detail_of(err))[0], "bad"))

    def _on_devices(self, data, ok: bool = True):
        rows = sd.device_rows(data) if ok else []
        self._fill_rows(
            self.device_rows, 0, self.device_empty, rows,
            lambda row: device_row(row, self.revoke_device),
            resolve(ok, bool(rows)),
            empty_title=sd.DEVICE_EMPTY[0], empty_body=sd.DEVICE_EMPTY[1])

    def revoke_device(self, row: dict):
        if not self._confirm_danger("Sign this device out?",
                                    sd.revoke_device_warning(row["agent"]),
                                    "Sign it out"):
            return
        spawn_worker(
            self.client, "POST",
            "/v1/auth/sessions/%s/revoke" % quote(str(row["id"]), safe=""),
            timeout=15, parent=self, track=self._page_workers,
            on_ok=lambda _d: self._device_revoked(),
            on_err=lambda err: self.toast.show_message(
                sd.save_result(status_of(err) or 0, detail_of(err))[0]))

    def _device_revoked(self):
        self.toast.show_message("Device signed out")
        self.refresh_settings()

    def logout_everywhere(self):
        if not self._confirm_danger("Log out everywhere?",
                                    sd.LOGOUT_ALL_WARNING, "Log out"):
            return
        spawn_worker(self.client, "POST", "/v1/auth/logout-all", timeout=20,
                     parent=self, track=self._page_workers,
                     on_ok=lambda _d: self._signed_out_everywhere(),
                     on_err=lambda _e: self._signed_out_everywhere())

    def _signed_out_everywhere(self):
        self.client.http.clear_session()
        self.toast.show_message("Signed out of every device")
        self._on_devices(None, ok=False)

    def mint_badge(self):
        self.badge_btn.setEnabled(False)

        def failed(err):
            self.badge_btn.setEnabled(True)
            self.toast.show_message(
                sd.save_result(status_of(err) or 0, detail_of(err))[0])

        spawn_worker(self.client, "POST", "/v1/account/badge", timeout=20,
                     parent=self, track=self._page_workers,
                     on_ok=self._on_badge, on_err=failed)

    def _on_badge(self, data):
        self.badge_btn.setEnabled(True)
        self._badge = sd.badge_view(data, self.settings.backend_url())
        has = bool(self._badge["token"])
        self.badge_btn.setText("Regenerate badge" if has else "Generate badge")
        self.badge_copy.setVisible(has)
        self.badge_revoke.setVisible(has)
        self.badge_link.setVisible(has)
        if has:
            self.badge_link.setText(
                sd.badge_link_html(self._badge, WEB["fox2"]))
        elif data is not None:
            self.toast.show_message("The server did not return a badge URL")

    def copy_badge_embed(self):
        from PyQt6.QtWidgets import QApplication
        if not self._badge["embed"]:
            return
        QApplication.clipboard().setText(self._badge["embed"])
        self.toast.show_message("Embed snippet copied")

    def revoke_badge(self):
        if not self._confirm_danger("Revoke the trust badge?",
                                    sd.BADGE_REVOKE_WARNING, "Revoke"):
            return
        spawn_worker(
            self.client, "DELETE", "/v1/account/badge", timeout=20,
            parent=self, track=self._page_workers,
            on_ok=lambda _d: self._on_badge(None),
            on_err=lambda err: self.toast.show_message(
                sd.save_result(status_of(err) or 0, detail_of(err))[0]))

    def export_account(self):
        spawn_worker(
            self.client, "GET", "/v1/account/export", timeout=60, parent=self,
            track=self._page_workers, raw=True,
            on_ok=lambda payload: self._save_bytes(
                payload, "foxy-account-export.json", "JSON file (*.json)"),
            on_err=lambda err: self.toast.show_message(
                sd.save_result(status_of(err) or 0, detail_of(err))[0]))

    def _save_bytes(self, payload, suggested: str, file_filter: str):
        from PyQt6.QtWidgets import QFileDialog
        data = (payload or {}).get("body") if isinstance(payload, dict) else payload
        if not data:
            self.toast.show_message("The server returned an empty file")
            return
        target, _ = QFileDialog.getSaveFileName(self, "Save", suggested,
                                                file_filter)
        if not target:
            return
        try:
            with open(target, "wb") as fh:
                fh.write(data if isinstance(data, (bytes, bytearray))
                         else str(data).encode("utf-8"))
        except OSError as exc:
            self.toast.show_message("Could not write the file - %s" % exc)
            return
        self.toast.show_message("Saved to %s" % target)

    def delete_workspace(self):
        from settings_page import ConfirmNameDialog
        name = self._org_name or ""
        dialog = ConfirmNameDialog(name, self)
        if not dialog.exec():
            return
        typed = dialog.typed()
        if not sd.delete_confirmed(typed, name):
            self.toast.show_message("The name did not match - nothing changed")
            return
        spawn_worker(
            self.client, "POST", "/v1/account/delete",
            body=sd.delete_body(typed), timeout=30, parent=self,
            track=self._page_workers,
            on_ok=lambda _d: self._signed_out_everywhere(),
            on_err=lambda err: self.toast.show_message(
                sd.save_result(status_of(err) or 0, detail_of(err))[0]))

    def _confirm_danger(self, title: str, body: str, verb: str) -> bool:
        """One confirmation for every destructive control on this page, so the
        wording and the button label cannot drift apart."""
        from PyQt6.QtWidgets import QMessageBox
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setText(title)
        box.setInformativeText(body)
        box.setIcon(QMessageBox.Icon.Warning)
        go = box.addButton(verb, QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        return box.clickedButton() is go

    def _page_stub(self, t: dict, section_id: str, title: str) -> QWidget:
        """An honest placeholder for a section whose real page lands later.

        It states plainly what it is and which phase builds it — no invented
        numbers, no skeleton pretending to be data (the no-fake-data rule)."""
        phase = {"settings": "D11"}.get(section_id, "")
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(22, 20, 22, 20)
        v.setSpacing(10)
        cap = QLabel(title.upper())
        cap.setObjectName("tableCap")
        v.addWidget(cap)
        note = QLabel(
            f"This section is coming in phase {phase}." if phase
            else "This section is coming in a later phase.")
        note.setObjectName("emptyState")
        note.setWordWrap(True)
        v.addWidget(note)
        hint = QLabel("Everything it will show already exists in the web "
                      "dashboard — nothing is simulated here in the meantime.")
        hint.setObjectName("connUrl")
        hint.setWordWrap(True)
        v.addWidget(hint)
        v.addStretch()
        return page

    # ── navigation (D3) ────────────────────────────────────────────────────
    def go(self, section_id: str):
        """Switch section by id — the desktop twin of the web's go()."""
        index = self._page_index.get(section_id)
        if index is None:
            return
        self.stack.setCurrentIndex(index)
        if hasattr(self, "notif_panel"):
            self.notif_panel.hide()      # a dropdown must not outlive its page
        btn = self._nav_by_id.get(section_id)
        if btn is not None and not btn.isChecked():
            btn.setChecked(True)
        if section_id == "threats":
            self._mark_breaches_read()
        if section_id == "threats":
            self.refresh_threats()
        if section_id == "ledger":
            self.refresh_ledger()
        if section_id == "verify":
            self.refresh_anchors()
        if section_id == "export":
            self.refresh_export_meta()
            self.refresh_exports()
        if section_id == "access":
            self.refresh_keys()
        if section_id == "policy":
            self.refresh_policy()
        if section_id == "billing":
            self.refresh_billing()
        if section_id == "settings":
            self.refresh_settings()
        if section_id == "home":
            # The web reloads a section's data on navigation; Home is the one
            # page whose numbers age while you sit in the ledger.
            self.refresh_home()
        # D5-P9: with the 15 s chrome poll gone, navigation is what keeps the
        # bell, the breach pip and the banner current — which is exactly when
        # the site refreshes them.
        self._refresh_chrome()

    def _sync_title(self):
        """Top-bar title + crumb follow the section (web setTopbarContext)."""
        index = self.stack.currentIndex()
        for section_id, _label, title, crumb, _icon in ALL_SECTIONS:
            if self._page_index.get(section_id) == index:
                self.page_title.setText(title)
                if hasattr(self, "page_crumb"):
                    self.page_crumb.setText(f"Foxy Audit · {crumb}")
                break

    # ── window chrome ──
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and e.position().y() < 58:
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            e.accept()

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton and not self._drag_pos.isNull():
            self.move(e.globalPosition().toPoint() - self._drag_pos)
            e.accept()

    def mouseReleaseEvent(self, e):
        self._drag_pos = QPoint()

    def center_on_screen(self):
        scr = QApplication.primaryScreen().geometry()
        self.move(scr.center().x() - self.width() // 2,
                  scr.center().y() - self.height() // 2)

    def _clamp_to_screen(self):
        """Keep a restored geometry on a screen that still exists — a saved
        position from a since-disconnected monitor would otherwise reopen the
        console off-screen (mirrors the pet-position clamp in omni_fox)."""
        screen = QApplication.screenAt(self.frameGeometry().center())
        if screen is not None:
            return                      # already on a connected screen
        avail = (QApplication.primaryScreen().availableGeometry()
                 if QApplication.primaryScreen() else None)
        if avail is None:
            return
        w = min(self.width(), avail.width())
        h = min(self.height(), avail.height())
        if (w, h) != (self.width(), self.height()):
            self.resize(w, h)
        self.move(max(avail.x(), min(self.x(), avail.right() - w)),
                  max(avail.y(), min(self.y(), avail.bottom() - h)))

    def _bring_to_front(self):
        """Reliably pull the window above the currently-active application.

        Qt's raise_/activateWindow alone are not enough on Windows when the
        request comes from a process that isn't already in the foreground
        (e.g. opening from the system-tray menu): Windows silently denies the
        focus change.  The window is no longer always-on-top, so this explicit
        SetForegroundWindow is what pulls it above the active app on open /
        restore (afterwards it behaves like any normal window in the z-order)."""
        self.raise_()
        self.activateWindow()
        if sys.platform != "win32":
            return
        try:
            import ctypes
            ctypes.windll.user32.SetForegroundWindow(int(self.winId()))
        except Exception:
            pass

    def _enable_acrylic(self):
        """Optional real frosted blur behind the window (Windows only).

        Uses the undocumented SetWindowCompositionAttribute API with
        ACCENT_ENABLE_ACRYLICBLURBEHIND, tinted with the dark base colour so the
        palette is preserved.  Best-effort: silently no-ops off-Windows or if the
        call fails.  Enable via DashboardWindow.GLASS_ACRYLIC = True."""
        if sys.platform != "win32" or getattr(self, "_acrylic_done", False):
            return
        try:
            import ctypes
            from ctypes import wintypes

            class ACCENTPOLICY(ctypes.Structure):
                _fields_ = [("AccentState", ctypes.c_int),
                            ("AccentFlags", ctypes.c_int),
                            ("GradientColor", ctypes.c_uint),
                            ("AnimationId", ctypes.c_int)]

            class WINCOMPATTRDATA(ctypes.Structure):
                _fields_ = [("Attribute", ctypes.c_int),
                            ("Data", ctypes.POINTER(ACCENTPOLICY)),
                            ("SizeOfData", ctypes.c_size_t)]

            # ABGR tint = warm-dark base at ~70% so the frosted blur stays visible
            # while the palette reads dark/on-brand.
            r, g, b = 0x18, 0x12, 0x0E
            gradient = (0xB2 << 24) | (b << 16) | (g << 8) | r
            accent = ACCENTPOLICY(4, 0, gradient, 0)   # 4 = ACRYLICBLURBEHIND
            data = WINCOMPATTRDATA(19, ctypes.pointer(accent),
                                   ctypes.sizeof(accent))   # 19 = ACCENT_POLICY
            ctypes.windll.user32.SetWindowCompositionAttribute(
                wintypes.HWND(int(self.winId())), ctypes.pointer(data))
            self._acrylic_done = True
        except Exception:
            pass

    def show_animated(self):
        was_minimized = self.isMinimized()
        if not self.isVisible():
            # Reopen where the user last had the console; first run centers.
            geo = self.settings.console_geometry()
            if geo is None or not self.restoreGeometry(geo):
                self.center_on_screen()
            self._clamp_to_screen()
        self.setWindowOpacity(0.0)
        # show() alone does NOT restore a window the user minimized to the
        # taskbar (Qt still reports a minimized window as "visible"), so
        # re-opening from the fox / tray must explicitly un-minimize it.
        if was_minimized:
            self.showNormal()
        else:
            self.show()
        if self.GLASS_ACRYLIC:
            self._enable_acrylic()
            # If the blur actually applied, switch the shell to its translucent
            # veil so the frost shows; if it failed, stay opaque (never see-through).
            if getattr(self, "_acrylic_done", False) and not getattr(self, "_acrylic_on", False):
                self._acrylic_on = True
                self.apply_theme(None)
        self._bring_to_front()
        # opacity-only fade (no geometry slide — avoids the high-DPI setGeometry
        # warnings on a fixed-size window).
        self._a_op = QPropertyAnimation(self, b"windowOpacity", self)
        self._a_op.setDuration(190)
        self._a_op.setStartValue(0.0)
        self._a_op.setEndValue(1.0)
        self._a_op.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._a_op.start()

    def close_animated(self):
        # Fade out, then close() — NOT hide(). close() is what runs closeEvent,
        # which stops the 1 s clock + 15 s poll timers and drains the workers;
        # hiding alone would leave them polling forever behind a hidden window.
        # The window is reused (no WA_DeleteOnClose), and show_animated restarts
        # the timers on the way back in.
        self._a_out = QPropertyAnimation(self, b"windowOpacity", self)
        self._a_out.setDuration(140)
        self._a_out.setStartValue(1.0)
        self._a_out.setEndValue(0.0)
        self._a_out.finished.connect(self.close)
        self._a_out.start()

    def showEvent(self, e):
        # Every close path (X, Alt+F4, taskbar) now runs closeEvent, which stops
        # the clock + poll timers. The window instance is reused, so any show
        # path must bring them back — idempotent via the isActive guard.
        if not self._backend_poll.isActive():
            self._tick.start(1000)
            self._backend_poll.start(15000)
            QTimer.singleShot(400, self._refresh_backend)
            QTimer.singleShot(400, self._refresh_org)
            QTimer.singleShot(600, self._refresh_chrome)
            QTimer.singleShot(800, self.refresh_home)
            self._activity_timer.start(hd.ACTIVITY_TICK_SECONDS * 1000)
            self._health_timer.start(HEALTH_PING_SECONDS * 1000)   # D5-P9
        super().showEvent(e)

    def hideEvent(self, e):
        # Remember where the console sits so it reopens in place next time.
        self.settings.set_console_geometry(self.saveGeometry())
        self.closed.emit()
        super().hideEvent(e)

    def closeEvent(self, e):
        """Centralized teardown: stop the timers, then wait for every tracked
        worker still in flight (nothing else quits threads any more).

        Reached from every close path — the in-window X (via close_animated),
        Alt+F4, the taskbar menu, and the app quitting."""
        self._tick.stop()
        self._backend_poll.stop()
        self._activity_timer.stop()     # D4 relative-time re-tick
        self._health_timer.stop()       # D5-P9 live-dot ping
        # Nothing may float or poll once the console is closed: the palette and
        # the shortcut overlay are shown non-modally, so an X-click while one is
        # open would otherwise leave it orphaned on screen.
        for floating in ("notif_panel", "cmd_palette", "shortcuts"):
            widget = getattr(self, floating, None)
            if widget is not None:
                widget.hide()
        self.settings.set_console_geometry(self.saveGeometry())
        # Hiding a window does not deliver hide events to its children, so a
        # chart's draw-in or a meter's sweep keeps running against Qt's
        # animation timer while the page is being torn down. Settle the charts
        # (so they repaint complete when reopened) and stop everything else.
        for chart in self.findChildren(FoxChart):
            chart.stop_animation()
        for anim in self.findChildren(QAbstractAnimation):
            anim.stop()
        shutdown_workers(self._workers | self._poll_workers | self._ann_workers
                         | self._home_workers | self._oneoff_workers
                         | self._threat_workers | self._ledger_workers
                         | self._page_workers)
        # Restore full opacity so the next show_animated fade starts from a
        # clean slate even though this instance is reused.
        self.setWindowOpacity(1.0)
        super().closeEvent(e)


# ── Standalone preview ──────────────────────────────────────────────────────
if __name__ == "__main__":
    # Launches the real console with honest empty states — no simulated
    # telemetry (the no-fake-data rule applies to dev previews too).  Point
    # Settings at a running backend to see live data.
    app = QApplication(sys.argv)
    d = DashboardWindow()
    d.show_animated()
    sys.exit(app.exec())
