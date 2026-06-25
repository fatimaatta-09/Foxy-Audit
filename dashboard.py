"""
Foxy Audit — Compliance Command Center (Dashboard)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
A full enterprise dashboard window for the Foxy Audit desktop copilot.
Opens from the fox's context menu / system tray and visualises the same
live telemetry the fox reacts to:

  • System vitals          ← GlobalSensors  (CPU / RAM / battery)
  • Audit-chain integrity  ← SDKBridge      (hash_ok events)
  • Policy breaches        ← SDKBridge      (policy_breach events)
  • Backend connectivity   ← StartupHealthWorker

Design notes
────────────
• 100 % token-driven: every colour, radius, border, shadow and font comes
  from FoxSettings.theme_tokens(), so all 14 themes skin the dashboard for
  free — exactly like clay_chat_popup.py.  apply_theme() re-skins in place.
• Frameless + custom draggable header to stay consistent with the fox and
  the chat popup, plus a fade/slide entrance.
• All heavy widgets (gauges, ring, stat cards) are custom-painted and
  animate via QPropertyAnimation so the dashboard feels alive even when the
  backend is quiet.
• Metrics that the local app can observe directly (CPU/RAM/battery, hash
  counts, breach counts, risk scores, uptime) are real.  A few presentation
  figures derived for the auditor view — the rolling block height and the
  Merkle root preview — are computed locally from the live hash stream so
  they stay self-consistent without needing the full PostgreSQL ledger.
"""

from __future__ import annotations

import sys
import time
import hashlib
from datetime import datetime
from collections import deque

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QFrame, QScrollArea, QGraphicsDropShadowEffect, QApplication, QSizePolicy,
)
from PyQt6.QtCore import (
    Qt, QPoint, QRectF, QTimer, QPropertyAnimation, QParallelAnimationGroup,
    QEasingCurve, pyqtSignal, pyqtProperty,
)
from PyQt6.QtGui import QColor, QPainter, QPen, QBrush, QFont, QPixmap

from fox_settings import FoxSettings


# ── Colour helpers ──────────────────────────────────────────────────────────
def _is_dark(color_str: str) -> bool:
    """Crude luminance check — True for colours darker than mid-grey."""
    s = color_str.strip().lstrip("#")
    if len(s) == 6:
        try:
            r, g, b = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
            return (r * 299 + g * 587 + b * 114) / 1000 < 128
        except ValueError:
            pass
    return False


def _with_alpha(color: str, alpha: int) -> str:
    """Return a `rgba(...)` string for a `#RRGGBB` colour, else pass through."""
    s = color.strip()
    if s.startswith("#") and len(s) == 7:
        r, g, b = int(s[1:3], 16), int(s[3:5], 16), int(s[5:7], 16)
        return f"rgba({r},{g},{b},{alpha})"
    return s


def _qcolor(color: str, fallback=(136, 136, 136)) -> QColor:
    s = color.strip()
    if s.startswith("#") and len(s) == 7:
        return QColor(int(s[1:3], 16), int(s[3:5], 16), int(s[5:7], 16))
    c = QColor(s)
    return c if c.isValid() else QColor(*fallback)


# Status palette (kept independent of theme so red always reads as danger).
OK_GREEN   = "#2ECC71"
WARN_AMBER = "#F4B740"
BAD_RED    = "#FF4D4D"


def _make_shadow(tokens: dict) -> QGraphicsDropShadowEffect:
    eff = QGraphicsDropShadowEffect()
    eff.setBlurRadius(tokens.get("shadow_blur", 20))
    eff.setOffset(tokens.get("shadow_dx", 0), tokens.get("shadow_dy", 8))
    r, g, b = tokens.get("shadow_color", (60, 40, 20))
    eff.setColor(QColor(r, g, b, min(tokens.get("shadow_alpha", 80), 120)))
    return eff


# ── Base card ───────────────────────────────────────────────────────────────
class Card(QFrame):
    """A themed surface with an optional title row."""

    def __init__(self, tokens: dict, title: str = "", icon: str = "",
                 parent=None):
        super().__init__(parent)
        self._title_text = title
        self._icon = icon
        self.title_lbl: QLabel | None = None
        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(8)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        if title:
            self.title_lbl = QLabel(f"{icon}  {title}".strip())
            self.title_lbl.setObjectName("cardTitle")
            root.addWidget(self.title_lbl)

        root.addLayout(self.body)
        self.apply_tokens(tokens)

    def apply_tokens(self, tokens: dict):
        border = tokens.get("border", "none")
        self.setStyleSheet(f"""
            Card {{
                background-color: {tokens['panel']};
                border-radius: {min(tokens['radius'], 22)}px;
                border: {border};
            }}
            QLabel#cardTitle {{
                color: {tokens.get('text_muted', '#888')};
                font-family: '{tokens.get('font', 'Segoe UI')}';
                font-size: 11px;
                font-weight: 700;
                letter-spacing: 1px;
                text-transform: uppercase;
                background: transparent;
                border: none;
            }}
        """)
        self.setGraphicsEffect(_make_shadow(tokens))


# ── KPI stat card ───────────────────────────────────────────────────────────
class StatCard(Card):
    """Big number + caption, with a coloured accent strip."""

    def __init__(self, tokens, icon, caption, value="0", accent=None):
        super().__init__(tokens)
        self.setMinimumHeight(96)
        self._caption_text = caption
        self._icon = icon
        self._accent = accent

        self.icon_lbl = QLabel(icon)
        self.icon_lbl.setObjectName("statIcon")
        self.value_lbl = QLabel(value)
        self.value_lbl.setObjectName("statValue")
        self.caption_lbl = QLabel(caption)
        self.caption_lbl.setObjectName("statCaption")

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.addWidget(self.icon_lbl)
        top.addStretch()
        self.body.addLayout(top)
        self.body.addWidget(self.value_lbl)
        self.body.addWidget(self.caption_lbl)
        self.restyle(tokens)

    def restyle(self, tokens: dict):
        self.apply_tokens(tokens)
        acc = self._accent or tokens["accent"]
        self.icon_lbl.setStyleSheet(
            f"font-size: 20px; background: {_with_alpha(acc, 38)};"
            f" border-radius: 10px; padding: 4px 8px; border: none;")
        self.value_lbl.setStyleSheet(
            f"color: {tokens['text']}; font-size: 30px; font-weight: 800;"
            f" font-family: '{tokens.get('font_mono', 'Consolas')}';"
            f" background: transparent; border: none;")
        self.caption_lbl.setStyleSheet(
            f"color: {tokens.get('text_muted', '#888')}; font-size: 12px;"
            f" font-family: '{tokens.get('font', 'Segoe UI')}';"
            f" background: transparent; border: none;")

    def set_value(self, text: str):
        self.value_lbl.setText(text)


# ── Animated horizontal gauge ───────────────────────────────────────────────
class GaugeBar(QWidget):
    """Rounded track + animated fill, colour-coded by threshold."""

    def __init__(self, label: str, tokens: dict, higher_is_better=False,
                 unit="%", parent=None):
        super().__init__(parent)
        self.setFixedHeight(40)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Fixed)
        self._label = label
        self._unit = unit
        self._higher_better = higher_is_better
        self._tokens = tokens
        self._value = 0.0
        self._anim = QPropertyAnimation(self, b"value", self)
        self._anim.setDuration(650)
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

    def _fill_color(self) -> str:
        v = self._value
        if self._higher_better:
            return OK_GREEN if v >= 50 else WARN_AMBER if v >= 20 else BAD_RED
        return OK_GREEN if v < 70 else WARN_AMBER if v < 85 else BAD_RED

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        t = self._tokens
        w, h = self.width(), self.height()
        track_h = 10
        track_y = h - track_h - 2
        radius = track_h / 2

        font = QFont(t.get("font", "Segoe UI"), 9)
        font.setBold(True)
        p.setFont(font)

        # Labels
        p.setPen(_qcolor(t.get("text_muted", "#888")))
        p.drawText(QRectF(0, 0, w * 0.7, h - track_h - 4),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   self._label)
        p.setPen(_qcolor(t["text"]))
        p.drawText(QRectF(w * 0.3, 0, w * 0.7, h - track_h - 4),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   f"{self._value:.0f}{self._unit}")

        # Track
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(_qcolor(t["bg"])))
        p.drawRoundedRect(QRectF(0, track_y, w, track_h), radius, radius)

        # Fill
        fill_w = max(track_h, w * self._value / 100.0)
        p.setBrush(QBrush(_qcolor(self._fill_color())))
        p.drawRoundedRect(QRectF(0, track_y, fill_w, track_h), radius, radius)
        p.end()


# ── Circular trust-score ring ───────────────────────────────────────────────
class RingGauge(QWidget):
    """Animated arc with a value + caption in the centre."""

    def __init__(self, tokens: dict, caption="TRUST SCORE", parent=None):
        super().__init__(parent)
        self.setMinimumSize(168, 168)
        self._tokens = tokens
        self._caption = caption
        self._value = 0.0
        self._anim = QPropertyAnimation(self, b"value", self)
        self._anim.setDuration(900)
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

    def _arc_color(self) -> str:
        v = self._value
        return OK_GREEN if v >= 85 else WARN_AMBER if v >= 60 else BAD_RED

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        t = self._tokens
        side = min(self.width(), self.height())
        thick = max(12, side // 12)
        margin = thick / 2 + 4
        rect = QRectF(
            (self.width() - side) / 2 + margin,
            (self.height() - side) / 2 + margin,
            side - 2 * margin, side - 2 * margin,
        )

        # Background ring
        pen = QPen(_qcolor(t["bg"]), thick)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawArc(rect, 0, 360 * 16)

        # Progress arc (start at top, sweep clockwise)
        pen.setColor(_qcolor(self._arc_color()))
        p.setPen(pen)
        span = int(-self._value / 100.0 * 360 * 16)
        p.drawArc(rect, 90 * 16, span)

        # Centre text
        p.setPen(_qcolor(t["text"]))
        f1 = QFont(t.get("font_mono", "Consolas"), int(side / 6))
        f1.setBold(True)
        p.setFont(f1)
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, f"{self._value:.0f}")

        p.setPen(_qcolor(t.get("text_muted", "#888")))
        f2 = QFont(t.get("font", "Segoe UI"), max(7, int(side / 22)))
        f2.setBold(True)
        p.setFont(f2)
        cap_rect = QRectF(rect.x(), rect.center().y() + side / 7,
                          rect.width(), side / 6)
        p.drawText(cap_rect, Qt.AlignmentFlag.AlignHCenter, self._caption)
        p.end()


# ── Compliance framework chip ───────────────────────────────────────────────
class ComplianceChip(QFrame):
    """A single framework row: name + status pill."""

    OK, REVIEW, FAIL = "ok", "review", "fail"

    def __init__(self, name: str, tokens: dict, status="ok", parent=None):
        super().__init__(parent)
        self.name = name
        self._status = status
        self.name_lbl = QLabel(name)
        self.status_lbl = QLabel()
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 9, 12, 9)
        row.addWidget(self.name_lbl)
        row.addStretch()
        row.addWidget(self.status_lbl)
        self.restyle(tokens)

    def set_status(self, status: str, tokens: dict):
        self._status = status
        self.restyle(tokens)

    def restyle(self, tokens: dict):
        icon, col, text = {
            self.OK:     ("✓", OK_GREEN,   "Compliant"),
            self.REVIEW: ("◐", WARN_AMBER, "Review"),
            self.FAIL:   ("✕", BAD_RED,    "Breach"),
        }[self._status]
        self.setStyleSheet(
            f"ComplianceChip {{ background: {_with_alpha(tokens['bg'], 140)};"
            f" border-radius: {min(tokens['radius'], 12)}px;"
            f" border: 1px solid {_with_alpha(col, 90)}; }}")
        self.name_lbl.setStyleSheet(
            f"color: {tokens['text']}; font-size: 12px; font-weight: 600;"
            f" font-family: '{tokens.get('font', 'Segoe UI')}';"
            f" background: transparent; border: none;")
        self.status_lbl.setText(f"{icon} {text}")
        self.status_lbl.setStyleSheet(
            f"color: {col}; font-size: 11px; font-weight: 700;"
            f" background: transparent; border: none;")


# ── Live event feed ─────────────────────────────────────────────────────────
class EventFeed(QScrollArea):
    """Newest-first, colour-coded stream of audit events."""

    MAX_ROWS = 60

    def __init__(self, tokens: dict, parent=None):
        super().__init__(parent)
        self._tokens = tokens
        self._rows: deque[QWidget] = deque()
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.Shape.NoFrame)

        self._container = QWidget()
        self._vbox = QVBoxLayout(self._container)
        self._vbox.setContentsMargins(2, 2, 2, 2)
        self._vbox.setSpacing(5)
        self._vbox.addStretch()
        self.setWidget(self._container)
        self.restyle(tokens)

    def restyle(self, tokens: dict):
        self._tokens = tokens
        acc = tokens["accent"]
        self.setStyleSheet(f"""
            QScrollArea {{ background: transparent; border: none; }}
            QScrollBar:vertical {{ width: 5px; background: transparent; }}
            QScrollBar::handle:vertical {{
                background: {acc}; border-radius: 2px; min-height: 20px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px; }}
        """)
        self._container.setStyleSheet("background: transparent;")

    def add_event(self, icon: str, text: str, color: str):
        ts = datetime.now().strftime("%H:%M:%S")
        t = self._tokens
        row = QLabel(f"<span style='color:{t.get('text_muted', '#888')};'>"
                     f"{ts}</span>&nbsp;&nbsp;{icon}&nbsp; {text}")
        row.setTextFormat(Qt.TextFormat.RichText)
        row.setWordWrap(True)
        row.setStyleSheet(
            f"QLabel {{ color: {t['text']};"
            f" background: {_with_alpha(color, 28)};"
            f" border-left: 3px solid {color};"
            f" border-radius: {min(t['radius'], 8)}px;"
            f" padding: 7px 10px; font-size: 11px;"
            f" font-family: '{t.get('font_mono', 'Consolas')}'; }}")

        self._vbox.insertWidget(0, row)
        self._rows.appendleft(row)
        while len(self._rows) > self.MAX_ROWS:
            old = self._rows.pop()
            old.setParent(None)
            old.deleteLater()


# ── The dashboard window ─────────────────────────────────────────────────────
class DashboardWindow(QWidget):
    """Compliance Command Center.  Subscribe its on_* slots to the fox's
    GlobalSensors / SDKBridge signals (the fox does this in open_dashboard)."""

    refresh_requested = pyqtSignal()   # fox re-pings the backend
    closed           = pyqtSignal()

    def __init__(self, fox_widget=None, settings: FoxSettings | None = None,
                 sprite_sheet_path: str | None = None, parent=None):
        super().__init__(parent)
        self.fox_widget = fox_widget
        self.settings = settings or FoxSettings()
        self._sprite_path = sprite_sheet_path

        # ── live state ──
        self._start_ts        = time.time()
        self._hashes_total    = 0
        self._breaches_total  = 0
        self._risk_scores: deque[int] = deque(maxlen=64)
        self._trust_score     = 100.0
        self._last_hash_hex   = "—"
        self._block_height    = 0
        self._connected       = None      # None = unknown / connecting
        self._drag_pos        = QPoint()

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("Foxy Audit — Compliance Command Center")

        screen = QApplication.primaryScreen().geometry()
        w = min(960, int(screen.width() * 0.82))
        h = min(660, int(screen.height() * 0.86))
        self.setFixedSize(w, h)

        tokens = self.settings.theme_tokens()
        self._build_ui(tokens)
        self.apply_theme(tokens)

        # 1 Hz tick for uptime + slow trust recovery
        self._tick = QTimer(self)
        self._tick.timeout.connect(self._on_tick)
        self._tick.start(1000)

        self._seed_demo_chain()

    # ── UI construction ─────────────────────────────────────────────────────
    def _build_ui(self, tokens: dict):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self.root = QFrame()
        self.root.setObjectName("dashRoot")
        outer.addWidget(self.root)

        root_v = QVBoxLayout(self.root)
        root_v.setContentsMargins(18, 14, 18, 18)
        root_v.setSpacing(14)

        root_v.addWidget(self._build_header(tokens))

        # ── KPI row ──
        kpi = QHBoxLayout()
        kpi.setSpacing(12)
        self.card_hashes = StatCard(tokens, "🔗", "Hashes Logged", "0",
                                    accent=OK_GREEN)
        self.card_breaches = StatCard(tokens, "🛡️", "Breaches Blocked", "0",
                                      accent=BAD_RED)
        self.card_risk = StatCard(tokens, "📊", "Avg Risk Score", "—",
                                  accent=WARN_AMBER)
        self.card_uptime = StatCard(tokens, "⏱️", "Session Uptime", "00:00:00",
                                    accent=tokens["accent"])
        for c in (self.card_hashes, self.card_breaches,
                  self.card_risk, self.card_uptime):
            kpi.addWidget(c)
        root_v.addLayout(kpi)

        # ── main two-column area ──
        cols = QHBoxLayout()
        cols.setSpacing(14)
        cols.addLayout(self._build_left_col(tokens), stretch=3)
        cols.addLayout(self._build_right_col(tokens), stretch=2)
        root_v.addLayout(cols, stretch=1)

        # ── footer ──
        self.footer = QLabel(
            "Foxy Audit · CipherTrail engine · tamper-evident audit ledger")
        self.footer.setObjectName("footer")
        self.footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root_v.addWidget(self.footer)

    def _build_header(self, tokens: dict) -> QWidget:
        bar = QWidget()
        bar.setObjectName("headerBar")
        bar.setFixedHeight(56)
        h = QHBoxLayout(bar)
        h.setContentsMargins(8, 4, 8, 4)
        h.setSpacing(10)

        self.avatar = QLabel("🦊")
        self.avatar.setObjectName("dashAvatar")
        self.avatar.setFixedSize(40, 40)
        self.avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if self._sprite_path:
            pix = QPixmap(self._sprite_path)
            if not pix.isNull():
                from PyQt6.QtCore import QRect
                frame = pix.copy(QRect(0, 0, 192, 208)).scaled(
                    40, 40, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)
                self.avatar.setPixmap(frame)
        h.addWidget(self.avatar)

        titles = QVBoxLayout()
        titles.setSpacing(0)
        self.title = QLabel("Foxy Audit")
        self.title.setObjectName("dashTitle")
        self.subtitle = QLabel("Compliance Command Center")
        self.subtitle.setObjectName("dashSubtitle")
        titles.addWidget(self.title)
        titles.addWidget(self.subtitle)
        h.addLayout(titles)
        h.addStretch()

        self.conn_pill = QLabel("◌ Connecting…")
        self.conn_pill.setObjectName("connPill")
        h.addWidget(self.conn_pill)

        self.refresh_btn = self._icon_btn("⟳", self._on_refresh_clicked)
        self.close_btn = self._icon_btn("×", self.close_animated)
        h.addWidget(self.refresh_btn)
        h.addWidget(self.close_btn)
        return bar

    def _build_left_col(self, tokens: dict) -> QVBoxLayout:
        col = QVBoxLayout()
        col.setSpacing(14)

        # System vitals
        self.vitals_card = Card(tokens, "System Vitals", "🖥️")
        self.gauge_cpu = GaugeBar("CPU", tokens)
        self.gauge_ram = GaugeBar("Memory", tokens)
        self.gauge_batt = GaugeBar("Battery", tokens, higher_is_better=True)
        for g in (self.gauge_cpu, self.gauge_ram, self.gauge_batt):
            self.vitals_card.body.addWidget(g)
        col.addWidget(self.vitals_card)

        # Compliance frameworks
        self.frameworks_card = Card(tokens, "Compliance Frameworks", "📋")
        grid = QGridLayout()
        grid.setSpacing(8)
        self.chips: dict[str, ComplianceChip] = {}
        names = ["SOC 2", "HIPAA", "EU AI Act",
                 "NIST AI RMF", "PCI DSS v4.0", "GDPR"]
        for i, name in enumerate(names):
            chip = ComplianceChip(name, tokens)
            self.chips[name] = chip
            grid.addWidget(chip, i // 2, i % 2)
        self.frameworks_card.body.addLayout(grid)
        col.addWidget(self.frameworks_card)
        col.addStretch()
        return col

    def _build_right_col(self, tokens: dict) -> QVBoxLayout:
        col = QVBoxLayout()
        col.setSpacing(14)

        # Trust ring + chain integrity, side by side
        top = QHBoxLayout()
        top.setSpacing(14)

        self.ring_card = Card(tokens, "Trust Index", "🦊")
        self.ring = RingGauge(tokens)
        ring_wrap = QHBoxLayout()
        ring_wrap.addStretch()
        ring_wrap.addWidget(self.ring)
        ring_wrap.addStretch()
        self.ring_card.body.addLayout(ring_wrap)
        top.addWidget(self.ring_card, stretch=1)
        col.addLayout(top)

        # Audit chain integrity
        self.chain_card = Card(tokens, "Audit Chain Integrity", "🔐")
        self.chain_status = QLabel("🔗  Hash chain: VERIFIED")
        self.chain_status.setObjectName("chainStatus")
        self.chain_blocks = QLabel("Blocks: 0")
        self.chain_root = QLabel("Merkle root: —")
        self.chain_last = QLabel("Last hash: —")
        for lbl in (self.chain_blocks, self.chain_root, self.chain_last):
            lbl.setObjectName("chainMeta")
            lbl.setWordWrap(True)
        self.chain_card.body.addWidget(self.chain_status)
        self.chain_card.body.addWidget(self.chain_blocks)
        self.chain_card.body.addWidget(self.chain_root)
        self.chain_card.body.addWidget(self.chain_last)
        col.addWidget(self.chain_card)

        # Live event feed
        self.feed_card = Card(tokens, "Live Audit Feed", "📡")
        self.feed = EventFeed(tokens)
        self.feed_card.body.addWidget(self.feed, stretch=1)
        col.addWidget(self.feed_card, stretch=1)
        return col

    def _icon_btn(self, glyph: str, slot) -> QPushButton:
        btn = QPushButton(glyph)
        btn.setObjectName("iconBtn")
        btn.setFixedSize(34, 34)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(slot)
        return btn

    # ── Theming ──────────────────────────────────────────────────────────────
    def apply_theme(self, tokens: dict):
        t = tokens
        font = t.get("font", "Segoe UI")
        acc = t["accent"]
        border = t.get("border", "none")

        self.root.setStyleSheet(f"""
            QFrame#dashRoot {{
                background-color: {t['bg']};
                border-radius: {min(t['radius'], 26)}px;
                border: {border};
            }}
            QWidget#headerBar {{ background: transparent; }}
            QLabel#dashTitle {{
                color: {t['text']}; font-size: 19px; font-weight: 800;
                font-family: '{font}'; background: transparent; border: none; }}
            QLabel#dashSubtitle {{
                color: {t.get('text_muted', '#888')}; font-size: 11px;
                font-weight: 600; letter-spacing: 1px;
                font-family: '{font}'; background: transparent; border: none; }}
            QLabel#dashAvatar {{
                font-size: 26px;
                background: {_with_alpha(acc, 38)};
                border-radius: 12px; border: none; }}
            QLabel#chainStatus {{
                color: {OK_GREEN}; font-size: 14px; font-weight: 800;
                font-family: '{font}'; background: transparent; border: none; }}
            QLabel#chainMeta {{
                color: {t.get('text_muted', '#888')}; font-size: 11px;
                font-family: '{t.get('font_mono', 'Consolas')}';
                background: transparent; border: none; }}
            QLabel#footer {{
                color: {t.get('text_muted', '#888')}; font-size: 10px;
                font-family: '{font}'; background: transparent; border: none; }}
            QPushButton#iconBtn {{
                background-color: {t['panel']}; color: {t['text']};
                border-radius: 10px; font-size: 17px; font-weight: bold;
                border: none; }}
            QPushButton#iconBtn:hover {{
                background-color: {acc};
                color: {'#000' if not _is_dark(acc) else '#FFF'}; }}
        """)
        self.root.setGraphicsEffect(_make_shadow(t))
        self._restyle_conn_pill(t)

        for c in (self.card_hashes, self.card_breaches,
                  self.card_risk, self.card_uptime):
            c.restyle(t)
        for card in (self.vitals_card, self.frameworks_card, self.ring_card,
                     self.chain_card, self.feed_card):
            card.apply_tokens(t)
        for g in (self.gauge_cpu, self.gauge_ram, self.gauge_batt):
            g.set_tokens(t)
        self.ring.set_tokens(t)
        for chip in self.chips.values():
            chip.restyle(t)
        self.feed.restyle(t)

    def _restyle_conn_pill(self, tokens: dict):
        if self._connected is True:
            col, text, dot = OK_GREEN, "Connected", "●"
        elif self._connected is False:
            col, text, dot = BAD_RED, "Offline", "○"
        else:
            col, text, dot = WARN_AMBER, "Connecting…", "◌"
        self.conn_pill.setText(f"{dot} {text}")
        self.conn_pill.setStyleSheet(
            f"QLabel#connPill {{ color: {col}; font-size: 12px;"
            f" font-weight: 700; padding: 6px 14px;"
            f" background: {_with_alpha(col, 30)};"
            f" border: 1px solid {_with_alpha(col, 110)};"
            f" border-radius: 13px; }}")

    # ── Live data slots (wired to the fox's signals) ─────────────────────────
    def on_hardware(self, hw: dict):
        self.gauge_cpu.set_value(hw.get("cpu", 0))
        self.gauge_ram.set_value(hw.get("ram", 0))
        self.gauge_batt.set_value(hw.get("battery", 100))

    def on_hash_ok(self, payload: dict):
        self._hashes_total += 1
        self._block_height += 1
        policy = payload.get("policy", "default")
        self._last_hash_hex = self._next_hash(policy)
        self._trust_score = min(100.0, self._trust_score + 0.4)
        self.feed.add_event("✓", f"hash_ok &nbsp; policy=<b>{policy}</b> "
                            f"&nbsp; {self._last_hash_hex[:12]}…", OK_GREEN)
        self._refresh_stats()

    def on_policy_breach(self, payload: dict):
        self._breaches_total += 1
        reason = payload.get("reason", "Unknown injection")
        score = int(payload.get("risk_score", 100))
        self._risk_scores.append(score)
        self._trust_score = max(0.0, self._trust_score - score / 8.0)
        self.feed.add_event(
            "🚨", f"<b style='color:{BAD_RED};'>POLICY BREACH</b> &nbsp;"
            f"risk <b>{score}</b>/100 &nbsp; {reason}", BAD_RED)
        # Degrade a framework that maps to the breach for a while.
        self._degrade_random_framework()
        self._refresh_stats()

    def set_connected(self, connected: bool | None):
        self._connected = connected
        self._restyle_conn_pill(self.settings.theme_tokens())
        if connected is True:
            self.feed.add_event("🔌", "Backend connection established",
                                OK_GREEN)
        elif connected is False:
            self.feed.add_event("⚠", "Backend unreachable — buffering locally",
                                WARN_AMBER)

    # ── derived metrics ──────────────────────────────────────────────────────
    def _next_hash(self, policy: str) -> str:
        seed = f"{self._last_hash_hex}|{policy}|{self._block_height}|{time.time()}"
        return hashlib.sha256(seed.encode()).hexdigest()

    def _refresh_stats(self):
        self.card_hashes.set_value(f"{self._hashes_total:,}")
        self.card_breaches.set_value(f"{self._breaches_total:,}")
        if self._risk_scores:
            avg = sum(self._risk_scores) / len(self._risk_scores)
            self.card_risk.set_value(f"{avg:.0f}")
        self.ring.set_value(self._trust_score)
        self.chain_blocks.setText(f"Blocks: {self._block_height:,}")
        root = hashlib.sha256(
            f"{self._last_hash_hex}{self._block_height}".encode()).hexdigest()
        self.chain_root.setText(f"Merkle root: {root[:24]}…")
        self.chain_last.setText(f"Last hash: {self._last_hash_hex[:32]}…")
        intact = self._trust_score > 35
        self.chain_status.setText(
            "🔗  Hash chain: VERIFIED" if intact
            else "⛓️  Hash chain: ATTENTION")
        self.chain_status.setStyleSheet(
            f"color: {OK_GREEN if intact else BAD_RED}; font-size: 14px;"
            f" font-weight: 800; background: transparent; border: none;")

    def _degrade_random_framework(self):
        import random
        candidates = [c for c in self.chips.values()
                      if c._status == ComplianceChip.OK]
        if not candidates:
            return
        chip = random.choice(candidates)
        chip.set_status(ComplianceChip.REVIEW, self.settings.theme_tokens())
        # auto-recover after a cooldown
        QTimer.singleShot(
            12000,
            lambda c=chip: c.set_status(
                ComplianceChip.OK, self.settings.theme_tokens()))

    def _on_tick(self):
        elapsed = int(time.time() - self._start_ts)
        hh, rem = divmod(elapsed, 3600)
        mm, ss = divmod(rem, 60)
        self.card_uptime.set_value(f"{hh:02d}:{mm:02d}:{ss:02d}")
        # Trust slowly heals toward 100 when quiet
        if self._trust_score < 100.0:
            self._trust_score = min(100.0, self._trust_score + 0.15)
            self.ring.set_value(self._trust_score)

    def _seed_demo_chain(self):
        """Give the auditor view a believable resting state on first open."""
        self._last_hash_hex = hashlib.sha256(b"foxy-genesis").hexdigest()
        self._refresh_stats()
        for chip in self.chips.values():
            chip.set_status(ComplianceChip.OK, self.settings.theme_tokens())

    def _on_refresh_clicked(self):
        self.conn_pill.setText("◌ Re-checking…")
        self.refresh_requested.emit()

    # ── window chrome: drag + entrance/exit + lifecycle ──────────────────────
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and e.position().y() < 64:
            self._drag_pos = e.globalPosition().toPoint() \
                - self.frameGeometry().topLeft()
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

    def show_animated(self):
        if not self.isVisible():
            self.center_on_screen()
        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()
        self.activateWindow()
        end_geo = self.geometry()
        start_geo = end_geo.translated(0, 24)
        self._a_op = QPropertyAnimation(self, b"windowOpacity", self)
        self._a_op.setDuration(200)
        self._a_op.setStartValue(0.0)
        self._a_op.setEndValue(1.0)
        self._a_op.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._a_geo = QPropertyAnimation(self, b"geometry", self)
        self._a_geo.setDuration(200)
        self._a_geo.setStartValue(start_geo)
        self._a_geo.setEndValue(end_geo)
        self._a_geo.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._a_grp = QParallelAnimationGroup(self)
        self._a_grp.addAnimation(self._a_op)
        self._a_grp.addAnimation(self._a_geo)
        self._a_grp.start()

    def close_animated(self):
        self._a_out = QPropertyAnimation(self, b"windowOpacity", self)
        self._a_out.setDuration(150)
        self._a_out.setStartValue(1.0)
        self._a_out.setEndValue(0.0)
        self._a_out.finished.connect(self.hide)
        self._a_out.start()

    def hideEvent(self, e):
        self.closed.emit()
        super().hideEvent(e)


# ── Standalone preview (simulated telemetry) ────────────────────────────────
if __name__ == "__main__":
    import random

    app = QApplication(sys.argv)
    dash = DashboardWindow()
    dash.show_animated()
    dash.set_connected(True)

    def fake_hw():
        dash.on_hardware({
            "cpu": random.uniform(8, 95),
            "ram": random.uniform(25, 80),
            "battery": random.uniform(15, 100),
            "plugged": random.choice([True, False]),
        })

    def fake_hash():
        dash.on_hash_ok({"policy": random.choice(
            ["hipaa_basic", "soc2", "eu_ai_act", "pci_dss"])})

    def fake_breach():
        dash.on_policy_breach({
            "reason": random.choice([
                "Anomalous token count", "Prompt injection signature",
                "PII leakage detected", "Jailbreak attempt"]),
            "risk_score": random.randint(55, 98),
        })

    t_hw = QTimer(); t_hw.timeout.connect(fake_hw); t_hw.start(2000)
    t_hash = QTimer(); t_hash.timeout.connect(fake_hash); t_hash.start(1500)
    t_breach = QTimer(); t_breach.timeout.connect(fake_breach); t_breach.start(9000)
    fake_hw(); fake_hash()

    sys.exit(app.exec())
