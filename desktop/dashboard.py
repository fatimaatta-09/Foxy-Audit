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
from foxy_client import FoxyClient, spawn_worker, shutdown_workers
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
from panel_state import PanelState, chart_empty

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
        builders = {
            "home": lambda t: self._home.build(self._page_overview(t)),
            "threats": self._page_analytics,
            "ledger": self._page_audit,
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

    def _page_audit(self, t: dict) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(22, 20, 22, 20)
        v.setSpacing(12)
        head = QHBoxLayout()
        cap = QLabel("BLIND AUDIT LOG")
        cap.setObjectName("tableCap")
        self.audit_count = QLabel("0 records")
        self.audit_count.setObjectName("tableCount")
        head.addWidget(cap)
        head.addStretch()
        head.addWidget(self.audit_count)
        v.addLayout(head)
        self.table_card = Card(t)
        self.table = AuditTable(t)
        self.table_card.body.addWidget(self.table, stretch=1)
        v.addWidget(self.table_card, stretch=1)
        return page

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
        anchor = v.get("last_anchor") or {}
        root = anchor.get("root_hash")
        self.chain_meta.setText(f"{count:,} blocks · "
                                + ("chain intact" if intact else "review required"))
        self.chain_hash.setText(f"root {root[:28]}…" if root else "root — (not yet anchored)")
        if hasattr(self, "verif_hash"):
            self.verif_hash.setText(f"{root[:19]}…" if root else "•••• •••• •••• ••••")
        self.chain_state.setText("VERIFIED" if intact else "REVIEW")
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

        # Trigger Analytics + org-name updates as well
        spawn_worker(self.client, "GET", "/v1/analytics/threats", parent=self,
                     on_ok=self._on_analytics_success, track=self._workers)
        self._refresh_org()

    def _on_analytics_success(self, data: dict):
        self.analytics_kpi_threats.set_value(str(data.get("total_threats", 0)))
        self.analytics_kpi_risk.set_value(str(data.get("avg_risk_score", 0)))

        # Update high risk list
        # Clear existing items
        while self.analytics_recent_box.count() > 1:
            item = self.analytics_recent_box.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()          # stop painting now, not next event loop
                widget.deleteLater()
        
        recent = data.get("recent_high_risk", [])
        if recent:
            self.analytics_recent_empty.hide()
            t = _clay_tokens()
            for ev in recent:
                row = QWidget()
                rl = QHBoxLayout(row)
                rl.setContentsMargins(2, 6, 2, 6)
                
                tlbl = QLabel(ev["timestamp"][:19].replace("T", " "))
                tlbl.setStyleSheet(f"color: {t.get('text_muted', '#888')}; font-size: 11px;")
                badge = Badge(f"RISK {ev['risk_score']}", BAD_RED)
                dlbl = QLabel(f"Seq {ev['seq']} · {ev['policy_tag']} · {ev['reason']}")
                dlbl.setStyleSheet(f"color: {t['text']}; font-size: 12px;")
                
                rl.addWidget(tlbl)
                rl.addWidget(badge)
                rl.addWidget(dlbl, stretch=1)
                row.setStyleSheet(f"border-top: 1px solid {_hairline(t, 30)};")
                self.analytics_recent_box.insertWidget(self.analytics_recent_box.count()-1, row)
        else:
            self.analytics_recent_empty.show()

        # Update policies list
        while self.analytics_policies_box.count() > 1:
            item = self.analytics_policies_box.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()          # stop painting now, not next event loop
                widget.deleteLater()
                
        policies = data.get("top_policies", [])
        if policies:
            self.analytics_policies_empty.hide()
            t = _clay_tokens()
            for p in policies:
                row = QWidget()
                rl = QHBoxLayout(row)
                rl.setContentsMargins(2, 6, 2, 6)
                plbl = QLabel(p["tag"])
                plbl.setStyleSheet(f"color: {t['text']}; font-size: 12px;")
                clbl = QLabel(f"{p['count']} breaches")
                clbl.setStyleSheet(f"color: {WARN_AMBER}; font-size: 12px; font-weight: bold;")
                
                rl.addWidget(plbl, stretch=1)
                rl.addWidget(clbl)
                row.setStyleSheet(f"border-top: 1px solid {_hairline(t, 30)};")
                self.analytics_policies_box.insertWidget(self.analytics_policies_box.count()-1, row)
        else:
            self.analytics_policies_empty.show()

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

    def _page_analytics(self, t: dict) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(22, 20, 22, 20)
        v.setSpacing(16)
        
        kpis = QHBoxLayout()
        kpis.setSpacing(14)
        self.analytics_kpi_threats = KpiTile(t, "Total Threats", "0", "policy breaches", BAD_RED)
        self.analytics_kpi_risk = KpiTile(t, "Avg Risk Score", "0", "across threats", WARN_AMBER)
        kpis.addWidget(self.analytics_kpi_threats)
        kpis.addWidget(self.analytics_kpi_risk)
        kpis.addStretch()
        v.addLayout(kpis)

        body = QHBoxLayout()
        body.setSpacing(16)

        self.analytics_recent_card = Card(t, "Recent High-Risk Events")
        self.analytics_recent_box = QVBoxLayout()
        self.analytics_recent_box.setSpacing(0)
        self.analytics_recent_empty = QLabel("No high-risk threats detected.")
        self.analytics_recent_empty.setObjectName("emptyState")
        self.analytics_recent_box.addWidget(self.analytics_recent_empty)
        self.analytics_recent_box.addStretch()
        self.analytics_recent_card.body.addLayout(self.analytics_recent_box, stretch=1)
        body.addWidget(self.analytics_recent_card, stretch=2)

        self.analytics_policies_card = Card(t, "Top Breached Policies")
        self.analytics_policies_box = QVBoxLayout()
        self.analytics_policies_box.setSpacing(0)
        self.analytics_policies_empty = QLabel("All policies compliant.")
        self.analytics_policies_empty.setObjectName("emptyState")
        self.analytics_policies_box.addWidget(self.analytics_policies_empty)
        self.analytics_policies_box.addStretch()
        self.analytics_policies_card.body.addLayout(self.analytics_policies_box, stretch=1)
        body.addWidget(self.analytics_policies_card, stretch=1)

        v.addLayout(body, stretch=1)
        return page

    # ── D3 chrome behaviour ────────────────────────────────────────────────
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
        self.refresh_activity()

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
        while self.onboarding_steps.count():
            item = self.onboarding_steps.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()          # stop painting now, not next event loop
                widget.deleteLater()
        for step in view["steps"]:
            self.onboarding_steps.addWidget(onboarding_step_row(step, self.go))
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
        while self.coverage_table.count() > self.coverage_rows_start:
            item = self.coverage_table.takeAt(self.coverage_rows_start)
            widget = item.widget()
            if widget is not None:
                widget.hide()          # stop painting now, not next event loop
                widget.deleteLater()
        for client in view["clients"]:
            self.coverage_table.addWidget(coverage_row(client))
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
                         "sign in to check the ledger" if getattr(err, "status", None) == 401
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
        while self.recent_ledger.count():
            item = self.recent_ledger.takeAt(0)
            widget = item.widget()
            if widget is not None and widget is not self.recent_ledger_empty:
                widget.hide()
                widget.deleteLater()
        if not rows:
            self.recent_ledger.addWidget(self.recent_ledger_empty)
            self.recent_ledger_empty.show()
            return
        self.recent_ledger_empty.hide()
        for row in rows:
            self.recent_ledger.addWidget(ledger_row(row))

    # ── active alerts ──
    def _on_threats(self, data, ok: bool = True):
        rows = hd.alert_rows(data)
        self.home_alerts_empty.set_state(
            panel_state.resolve(ok, bool(rows)),
            empty_title="No active alerts",
            empty_body="Every graded interaction is within policy.")
        while self.home_alerts.count():
            item = self.home_alerts.takeAt(0)
            widget = item.widget()
            if widget is not None and widget is not self.home_alerts_empty:
                widget.hide()
                widget.deleteLater()
        if not rows:
            self.home_alerts.addWidget(self.home_alerts_empty)
            self.home_alerts_empty.show()
        else:
            self.home_alerts_empty.hide()
            for row in rows:
                self.home_alerts.addWidget(alert_row(row))
        # Open alerts ≠ the "Policy Breaches" KPI tile: that one counts every
        # breach ever recorded, this counts the ones still live. It rides the
        # section header rather than overwriting a differently-labelled tile.
        count = hd.open_alert_count(data)
        self.home_alerts_count.setText("" if count is None else f"{count:,} open")
        # The web's dv[1] (html:3350): the live count, not the all-time total.
        self.kpi_alerts.set_value(hd.stat_row(None, count)[1][1])

    # ── recent activity ──
    def refresh_activity(self):
        """Both endpoints are admin-only: a member simply gets fewer rows, so
        each side degrades on its own rather than failing the whole feed."""
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
                         track=self._home_workers,
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
        while self.activity_list.count():
            item = self.activity_list.takeAt(0)
            widget = item.widget()
            if widget is not None and widget is not self.activity_empty:
                widget.hide()
                widget.deleteLater()
        if not rows:
            self.activity_list.addWidget(self.activity_empty)
            self.activity_empty.show()
            self.activity_stamp.setText("")
            return
        self.activity_empty.hide()
        for item in rows:
            self.activity_list.addWidget(activity_row(item))
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

    def _page_stub(self, t: dict, section_id: str, title: str) -> QWidget:
        """An honest placeholder for a section whose real page lands later.

        It states plainly what it is and which phase builds it — no invented
        numbers, no skeleton pretending to be data (the no-fake-data rule)."""
        phase = {"verify": "D6", "policy": "D7", "export": "D8",
                 "access": "D9", "billing": "D10", "settings": "D11"}.get(section_id, "")
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
                         | self._home_workers | self._oneoff_workers)
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
