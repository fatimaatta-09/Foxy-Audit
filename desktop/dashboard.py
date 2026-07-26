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
from datetime import datetime

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QStackedWidget, QTableWidget, QTableWidgetItem, QHeaderView,
    QApplication, QSizePolicy, QButtonGroup, QAbstractItemView,
    QTextEdit, QLineEdit,
)
from PyQt6.QtCore import (
    Qt, QPoint, QRectF, QTimer, QPropertyAnimation,
    QEasingCurve, pyqtSignal, pyqtProperty,
)
from PyQt6.QtGui import (
    QColor, QPainter, QPen, QBrush, QFont, QPixmap, QLinearGradient,
)

from fox_settings import FoxSettings
from foxy_client import FoxyClient, spawn_worker, shutdown_workers
from foxy_tokens import (
    OK_GREEN, WARN_AMBER, BAD_RED, INFO_BLUE, DARK_TX,
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
        self.setFixedSize(30, 26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_tokens(self, tokens):
        self._tokens = tokens
        self.update()

    def enterEvent(self, e): self.update()
    def leaveEvent(self, e): self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        t = self._tokens
        if self.underMouse():
            p.setPen(Qt.PenStyle.NoPen)
            hl = BAD_RED if self._danger else t.get("panel3", t.get("panel"))
            p.setBrush(QBrush(_qcolor(hl)))
            p.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), 7, 7)
            col = _qcolor("#FFFFFF" if self._danger else t["text"])
        else:
            col = _qcolor(t.get("text_muted", "#888"))
        paint_icon(p, QRectF(7, 4, 16, 18), self.icon_name, col, 1.6)
        p.end()


# ── The console window ──────────────────────────────────────────────────────
class DashboardWindow(QWidget):
    refresh_requested = pyqtSignal()
    closed           = pyqtSignal()

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
        self._logs_total     = 0
        self._flagged_total  = 0
        self._connected      = None
        self._org_name       = ""       # real org from /v1/health — never faked
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
        self.stack.addWidget(self._page_overview(t))   # 0
        self.stack.addWidget(self._page_audit(t))      # 1
        self.stack.addWidget(self._page_system(t))     # 2
        self.stack.addWidget(self._page_sandbox(t))    # 3
        self.stack.addWidget(self._page_analytics(t))  # 4
        self.stack.currentChanged.connect(self._sync_title)
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
        for i, (icon, label) in enumerate(
                [("overview", "Overview"), ("log", "Audit Log"),
                 ("system", "System"), ("shield", "Sandbox"),
                 ("analytics", "Threat Analytics")]):
            btn = NavButton(icon, label, t)
            btn.clicked.connect(lambda _c, idx=i: self.stack.setCurrentIndex(idx))
            self.nav_group.addButton(btn, i)
            self.nav_buttons.append(btn)
            v.addWidget(btn)
        self.nav_buttons[0].setChecked(True)
        v.addStretch()

        self.org_lbl = QLabel("ORGANIZATION")
        self.org_lbl.setObjectName("navMeta")
        # Real org name arrives from GET /v1/health; "—" until then — never faked.
        self.org_val = QLabel("—")
        self.org_val.setObjectName("navMetaVal")
        v.addWidget(self.org_lbl)
        v.addWidget(self.org_val)
        return self.sidebar

    def _build_topbar(self, t: dict) -> QWidget:
        self.topbar = QFrame()
        self.topbar.setObjectName("topbar")
        self.topbar.setFixedHeight(58)
        h = QHBoxLayout(self.topbar)
        h.setContentsMargins(22, 0, 14, 0)
        h.setSpacing(12)

        self.page_title = QLabel("Overview")
        self.page_title.setObjectName("pageTitle")
        self.stack_titles = ["Overview", "Blind Audit Log", "System Health", "Verification Sandbox", "Threat Analytics"]
        h.addWidget(self.page_title)
        h.addStretch()

        self.refresh_btn = CtrlButton("refresh", t)
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
        head = QHBoxLayout()
        htxt = QVBoxLayout()
        htxt.setSpacing(3)
        self.ov_eyebrow = QLabel("● GOVERNANCE-AS-CODE")
        self.ov_eyebrow.setObjectName("eyebrow")
        self.ov_h1 = QLabel("Org‑wide compliance, live.")
        self.ov_h1.setObjectName("h1Head")
        # Populated with the real org + real sync time once /v1/stats answers.
        self.ov_sub = QLabel("not connected")
        self.ov_sub.setObjectName("subHead")
        htxt.addWidget(self.ov_eyebrow)
        htxt.addWidget(self.ov_h1)
        htxt.addWidget(self.ov_sub)
        head.addLayout(htxt)
        head.addStretch()
        self.export_btn = QPushButton("Export passport")
        self.export_btn.setObjectName("ctaBtn")
        self.export_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        head.addWidget(self.export_btn, 0, Qt.AlignmentFlag.AlignTop)
        v.addLayout(head)

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
        self.hero_num = QLabel("—")       # real clean-rate arrives from /v1/stats
        self.hero_num.setObjectName("heroNum")
        hfoot = QLabel("compliance score")
        hfoot.setObjectName("heroFoot")
        hl.addLayout(htop)
        hl.addStretch()
        hl.addWidget(self.hero_num)
        hl.addWidget(hfoot)
        _glass_shadow(self.hero, 10, 26, 110)
        hero_row.addWidget(self.hero, stretch=3)

        tiles = QVBoxLayout()
        tiles.setSpacing(14)
        self.tile_threats = self._feature_tile(
            "Blind Audit\nLog", "tileBlue", lambda: self.stack.setCurrentIndex(1))
        self.tile_system = self._feature_tile(
            "System\nHealth", "tilePink", lambda: self.stack.setCurrentIndex(2))
        tiles.addWidget(self.tile_threats)
        tiles.addWidget(self.tile_system)
        hero_row.addLayout(tiles, stretch=2)
        v.addLayout(hero_row)

        # ── stats row (4 KPIs) ──
        kpis = QHBoxLayout()
        kpis.setSpacing(14)
        self.kpi_logs = KpiTile(t, "Interactions", "0", "logged to ledger", INFO_BLUE)
        self.kpi_flagged = KpiTile(t, "Policy Breaches", "0", "flagged by AI judge", BAD_RED)
        self.kpi_risk = KpiTile(t, "Time to Verdict", "—", "ingest → judged", WARN_AMBER)
        self.kpi_chain = KpiTile(t, "Ledger Blocks", "0", "hash-chained", OK_GREEN)
        for wgt in (self.kpi_logs, self.kpi_flagged, self.kpi_risk, self.kpi_chain):
            kpis.addWidget(wgt)
        v.addLayout(kpis)

        # ── verification card (website credit-card style) + ledger integrity ──
        body = QHBoxLayout()
        body.setSpacing(16)

        verif_col = QVBoxLayout()
        verif_col.setSpacing(11)
        verif_cap = QLabel("VERIFICATION CARD")
        verif_cap.setObjectName("sectionCap")
        verif_col.addWidget(verif_cap)

        self.verif_card = QFrame()
        self.verif_card.setObjectName("verifCard")
        self.verif_card.setMinimumHeight(186)
        vc = QVBoxLayout(self.verif_card)
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
        for k in (self.kpi_logs, self.kpi_flagged, self.kpi_risk, self.kpi_chain):
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
        # Immediate feedback from live UDP events; /v1/stats + /v1/verify overwrite
        # these with the real ledger-wide numbers on the next poll.
        self.kpi_logs.set_value(f"{self._logs_total:,}")
        self.kpi_flagged.set_value(f"{self._flagged_total:,}")

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
        total = int(s.get("total_logged", 0))
        self.kpi_logs.set_value(f"{total:,}")
        self.kpi_flagged.set_value(f"{int(s.get('breaches', 0)):,}")
        clean = float(s.get("clean_rate", 100.0))
        if hasattr(self, "hero_num"):
            self.hero_num.setText(f"{clean:.0f}")          # real compliance score
        ttv = s.get("avg_seconds_to_verdict")
        self.kpi_risk.set_value(
            "—" if ttv is None else f"{float(ttv):.1f}s",
            accent=OK_GREEN if (ttv is not None and float(ttv) < 5) else WARN_AMBER)
        if hasattr(self, "verif_num"):
            self.verif_num.setText(f"{total:,} events")
        # Honest header: the real org (once /v1/health answered) + the real
        # time this stats payload landed.
        synced = datetime.now().strftime("%H:%M:%S")
        self.ov_sub.setText(f"{self._org_name} · synced {synced}" if self._org_name
                            else f"synced {synced}")

    def _on_verify_stats(self, v: dict):
        count = int(v.get("count", 0))
        intact = bool(v.get("ok", False))
        anchor = v.get("last_anchor") or {}
        root = anchor.get("root_hash")
        self.kpi_chain.set_value(f"{count:,}")
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
            if item.widget():
                item.widget().deleteLater()
        
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
            if item.widget():
                item.widget().deleteLater()
                
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

    def _sync_title(self):
        self.page_title.setText(self.stack_titles[self.stack.currentIndex()])

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
        self.settings.set_console_geometry(self.saveGeometry())
        shutdown_workers(self._workers | self._poll_workers)
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
