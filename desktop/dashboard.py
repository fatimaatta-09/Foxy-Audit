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
• Still fully token-driven, so all 14 FoxSettings themes apply — but the
  structure is flattened (small radii, 1px borders, minimal shadow) so it
  looks clean in every palette.
• Public slots are unchanged from the previous version, so the omni_fox
  wiring (on_hardware / on_hash_ok / on_policy_breach / set_connected /
  refresh_requested / show_animated) needs no edits.

Note on derived figures: counts, risk, score, uptime and CPU/RAM/battery
are real. The ledger block height + chain-hash preview are recomputed
locally from the live hash stream so the auditor view stays self-consistent
until the FastAPI ledger endpoints are wired in.
"""

from __future__ import annotations

import os
import sys
import time
import json
import hashlib
import urllib.error
import urllib.request
from datetime import datetime
from collections import deque

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QStackedWidget, QTableWidget, QTableWidgetItem, QHeaderView,
    QApplication, QSizePolicy, QButtonGroup, QAbstractItemView,
    QGraphicsDropShadowEffect, QTextEdit, QLineEdit,
)
from PyQt6.QtCore import (
    Qt, QPoint, QRectF, QTimer, QThread, QPropertyAnimation,
    QParallelAnimationGroup, QEasingCurve, pyqtSignal, pyqtProperty,
)
from PyQt6.QtGui import (
    QColor, QPainter, QPen, QBrush, QFont, QPainterPath, QPixmap, QFontDatabase,
    QLinearGradient,
)

from fox_settings import FoxSettings


# ── Backend HTTP workers (run off-thread so the GUI never freezes) ───────────
class VerifyWorker(QThread):
    """Calls GET /v1/verify and emits the result."""
    succeeded = pyqtSignal(dict)   # {ok, count, first_broken_seq, detail}
    failed    = pyqtSignal(str)    # error message

    def __init__(self, backend_url: str, org_key: str, parent=None):
        super().__init__(parent)
        self.backend_url = backend_url.rstrip("/")
        self.org_key = org_key

    def run(self):
        url = f"{self.backend_url}/v1/verify"
        req = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {self.org_key}"}
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                self.succeeded.emit(data)
        except urllib.error.HTTPError as e:
            self.failed.emit(f"HTTP {e.code}: {e.reason}")
        except Exception as e:
            self.failed.emit(str(e))


class StatsWorker(QThread):
    """Calls GET /v1/stats and emits the aggregate dashboard stats (7B)."""
    succeeded = pyqtSignal(dict)   # {total_logged, breaches, clean_rate, judge_model, avg_seconds_to_verdict, ...}
    failed    = pyqtSignal(str)

    def __init__(self, backend_url: str, org_key: str, parent=None):
        super().__init__(parent)
        self.backend_url = backend_url.rstrip("/")
        self.org_key = org_key

    def run(self):
        url = f"{self.backend_url}/v1/stats"
        req = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {self.org_key}"}
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                self.succeeded.emit(data)
        except urllib.error.HTTPError as e:
            self.failed.emit(f"HTTP {e.code}: {e.reason}")
        except Exception as e:
            self.failed.emit(str(e))


class RefreshWorker(QThread):
    """Calls GET /v1/logs?page=1&limit=50 and emits the rows."""
    succeeded = pyqtSignal(dict)   # {items, total, page, limit}
    failed    = pyqtSignal(str)

    def __init__(self, backend_url: str, org_key: str,
                 page: int = 1, limit: int = 50, parent=None):
        super().__init__(parent)
        self.backend_url = backend_url.rstrip("/")
        self.org_key = org_key
        self.page = page
        self.limit = limit

    def run(self):
        url = f"{self.backend_url}/v1/logs?page={self.page}&limit={self.limit}"
        req = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {self.org_key}"}
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                self.succeeded.emit(data)
        except urllib.error.HTTPError as e:
            self.failed.emit(f"HTTP {e.code}: {e.reason}")
        except Exception as e:
            self.failed.emit(str(e))


class ThreatAnalyticsWorker(QThread):
    """Calls GET /v1/analytics/threats and emits the analytics data."""
    succeeded = pyqtSignal(dict)
    failed    = pyqtSignal(str)

    def __init__(self, backend_url: str, org_key: str, parent=None):
        super().__init__(parent)
        self.backend_url = backend_url.rstrip("/")
        self.org_key = org_key

    def run(self):
        url = f"{self.backend_url}/v1/analytics/threats"
        req = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {self.org_key}"}
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                self.succeeded.emit(data)
        except urllib.error.HTTPError as e:
            self.failed.emit(f"HTTP {e.code}: {e.reason}")
        except Exception as e:
            self.failed.emit(str(e))


class _SandboxFetchWorker(QThread):
    """Fetch GET /v1/logs/{seq} and compare stored hashes to locally computed ones.

    Emits result(matched: bool, detail: str).
    """
    result = pyqtSignal(bool, str)

    def __init__(self, backend_url: str, org_key: str, seq: int,
                 local_prompt_hash: str, local_response_hash: str, parent=None):
        super().__init__(parent)
        self.backend_url = backend_url.rstrip("/")
        self.org_key = org_key
        self.seq = seq
        self.local_ph = local_prompt_hash
        self.local_rh = local_response_hash

    def run(self):
        url = f"{self.backend_url}/v1/logs/{self.seq}"
        req = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {self.org_key}"}
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            stored_ph = data.get("prompt_hash", "")
            stored_rh = data.get("response_hash", "")
            if stored_ph == self.local_ph and stored_rh == self.local_rh:
                self.result.emit(True, "")
            else:
                detail = []
                if stored_ph != self.local_ph:
                    detail.append(f"Prompt hash: ledger={stored_ph[:16]}… local={self.local_ph[:16]}…")
                if stored_rh != self.local_rh:
                    detail.append(f"Response hash: ledger={stored_rh[:16]}… local={self.local_rh[:16]}…")
                self.result.emit(False, "\n".join(detail))
        except urllib.error.HTTPError as e:
            self.result.emit(False, f"HTTP {e.code}: {e.reason} — seq {self.seq} not found")
        except Exception as e:
            self.result.emit(False, f"Error: {e}")


# ── Colour helpers ──────────────────────────────────────────────────────────
def _is_dark(color_str: str) -> bool:
    s = color_str.strip().lstrip("#")
    if len(s) == 6:
        try:
            r, g, b = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
            return (r * 299 + g * 587 + b * 114) / 1000 < 128
        except ValueError:
            pass
    return False


def _with_alpha(color: str, alpha: int) -> str:
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


def _mix(a: str, b: str, t: float) -> str:
    """Linear blend of two #RRGGBB colours, t in [0,1]."""
    ca, cb = _qcolor(a), _qcolor(b)
    r = int(ca.red() * (1 - t) + cb.red() * t)
    g = int(ca.green() * (1 - t) + cb.green() * t)
    bl = int(ca.blue() * (1 - t) + cb.blue() * t)
    return f"#{r:02X}{g:02X}{bl:02X}"


# Status palette — matches foxy-audit-premium.html :root (clay / paprika site),
# fixed & theme-independent so danger always reads as danger.
OK_GREEN   = "#3ddc84"
WARN_AMBER = "#ffc83d"
BAD_RED    = "#ff4d4d"
INFO_BLUE  = "#5b8cff"
DARK_TX    = "#160d08"   # near-black ink used on the coloured pills


# ── Foxy Audit "premium" clay palette (1:1 with the website :root) ───────────
CLAY = {
    "bg": "#0e0c0a", "bg2": "#161310", "surf": "#1c1815", "surf2": "#221d18",
    "surf3": "#2a241d", "line": "#322b23",
    "ink": "#f7f1e8", "ink2": "#cdc2b3", "muted": "#8c8174", "muted2": "#5f564a",
    "fox": "#ff7a2e", "fox2": "#ff9d52", "fox3": "#d65a16", "foxdeep": "#8a3611",
}

_FONT_CACHE: dict[str, str] = {}
_FONTS_REGISTERED = False


def _register_bundled_fonts():
    """Load the bundled Unbounded / Space Mono TTFs (the site's brand fonts) so the
    console uses them instead of a generic system fallback."""
    global _FONTS_REGISTERED
    if _FONTS_REGISTERED:
        return
    _FONTS_REGISTERED = True
    font_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
    try:
        for fn in os.listdir(font_dir):
            if fn.lower().endswith((".ttf", ".otf")):
                QFontDatabase.addApplicationFont(os.path.join(font_dir, fn))
    except Exception:
        pass


def _pick_font(kind: str) -> str:
    """Brand fonts (bundled Unbounded / Space Mono) with sane fallbacks."""
    if kind in _FONT_CACHE:
        return _FONT_CACHE[kind]
    _register_bundled_fonts()
    try:
        fams = set(QFontDatabase.families())
    except Exception:
        fams = set()
    if kind == "disp":
        cands = ["Unbounded", "Nunito", "Poppins", "Montserrat",
                 "Segoe UI Variable Display", "Segoe UI Semibold", "Segoe UI"]
        default = "Segoe UI"
    else:
        cands = ["Space Mono", "Cascadia Mono", "JetBrains Mono",
                 "Consolas", "Courier New"]
        default = "Consolas"
    pick = next((c for c in cands if c in fams), default)
    _FONT_CACHE[kind] = pick
    return pick


def _clay_tokens() -> dict:
    """The console's design tokens.  The base palette (bg / accent / status) is
    the original clay/paprika scheme — unchanged — but the surfaces are now
    *glassmorphic*: translucent white frosting (rgba whites), bright 1px rims and
    soft shadows are layered over that palette.  Qt can't do CSS backdrop-filter,
    so 'glass' here = translucency + rim-light + soft shadow over the dark base.

    Alpha values are 0–255 (Qt stylesheet convention)."""
    return {
        "bg": CLAY["bg"], "bg2": CLAY["bg2"],
        "panel": CLAY["surf"], "panel2": CLAY["surf2"], "panel3": CLAY["surf3"],
        "line": CLAY["line"],
        "text": CLAY["ink"], "text2": CLAY["ink2"],
        "text_muted": CLAY["muted"], "text_muted2": CLAY["muted2"],
        "accent": CLAY["fox"], "accent2": CLAY["fox2"], "accent3": CLAY["fox3"],
        # ── glass layer (frosted-white over the dark palette) ──
        "glass_hi":  "rgba(255,255,255,34)",   # top sheen of a panel
        "glass_lo":  "rgba(255,255,255,10)",   # bottom of a panel
        "glass_brd": "rgba(255,255,255,50)",   # bright 1px rim
        "glass_brd_soft": "rgba(255,255,255,28)",
        "glass_str_hi": "rgba(255,255,255,60)",  # raised glass (nav active / button)
        "glass_str_lo": "rgba(255,255,255,24)",
        "glass_fill": "rgba(255,255,255,16)",   # flat translucent fill
        "font": _pick_font("disp"), "font_mono": _pick_font("mono"),
    }


def _glass_shadow(widget, dy: int = 9, blur: int = 26, alpha: int = 90):
    """Soft ambient shadow that lifts a frosted-glass panel off the backdrop."""
    eff = QGraphicsDropShadowEffect(widget)
    eff.setBlurRadius(blur)
    eff.setOffset(0, dy)
    eff.setColor(QColor(0, 0, 0, alpha))
    widget.setGraphicsEffect(eff)


def _hairline(tokens: dict, alpha: int = 38) -> str:
    return _with_alpha(tokens.get("text_muted", "#888"), alpha)


# ── Minimal line-icon painter (replaces emoji throughout) ───────────────────
def paint_icon(p: QPainter, rect: QRectF, name: str, color: QColor,
               weight: float = 1.8):
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(color, weight)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()

    if name == "overview":      # 2x2 grid
        s = w * 0.36
        gap = w * 0.10
        for cx in (x + w * 0.16, x + w * 0.16 + s + gap):
            for cy in (y + h * 0.16, y + h * 0.16 + s + gap):
                p.drawRoundedRect(QRectF(cx, cy, s, s), 2, 2)
    elif name == "analytics":
        p.drawLine(int(x + w * 0.25), int(y + h * 0.85), int(x + w * 0.25), int(y + h * 0.4))
        p.drawLine(int(x + w * 0.5), int(y + h * 0.85), int(x + w * 0.5), int(y + h * 0.2))
        p.drawLine(int(x + w * 0.75), int(y + h * 0.85), int(x + w * 0.75), int(y + h * 0.55))
        p.drawLine(int(x + w * 0.1), int(y + h * 0.85), int(x + w * 0.9), int(y + h * 0.85))
    elif name == "log":         # list rows
        for i in range(3):
            ly = y + h * (0.30 + i * 0.20)
            p.setBrush(QBrush(color))
            p.drawEllipse(QRectF(x + w * 0.14, ly - w * 0.035, w * 0.07, w * 0.07))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawLine(int(x + w * 0.32), int(ly), int(x + w * 0.84), int(ly))
    elif name == "system":      # sliders
        for i, knob in zip(range(3), (0.7, 0.35, 0.6)):
            ly = y + h * (0.30 + i * 0.20)
            p.drawLine(int(x + w * 0.14), int(ly), int(x + w * 0.84), int(ly))
            p.setBrush(QBrush(color))
            p.drawEllipse(QRectF(x + w * (0.14 + knob * 0.70) - w * 0.05,
                                 ly - w * 0.05, w * 0.10, w * 0.10))
            p.setBrush(Qt.BrushStyle.NoBrush)
    elif name == "shield":
        path = QPainterPath()
        path.moveTo(x + w * 0.5, y + h * 0.12)
        path.lineTo(x + w * 0.84, y + h * 0.26)
        path.lineTo(x + w * 0.84, y + h * 0.55)
        path.cubicTo(x + w * 0.84, y + h * 0.78, x + w * 0.66, y + h * 0.86,
                     x + w * 0.5, y + h * 0.92)
        path.cubicTo(x + w * 0.34, y + h * 0.86, x + w * 0.16, y + h * 0.78,
                     x + w * 0.16, y + h * 0.55)
        path.lineTo(x + w * 0.16, y + h * 0.26)
        path.closeSubpath()
        p.drawPath(path)
    elif name == "link":        # chain links
        p.drawRoundedRect(QRectF(x + w * 0.12, y + h * 0.36, w * 0.44, h * 0.28),
                          h * 0.14, h * 0.14)
        p.drawRoundedRect(QRectF(x + w * 0.44, y + h * 0.36, w * 0.44, h * 0.28),
                          h * 0.14, h * 0.14)
    elif name == "refresh":
        p.drawArc(QRectF(x + w * 0.22, y + h * 0.22, w * 0.56, h * 0.56),
                  55 * 16, 250 * 16)
        p.setBrush(QBrush(color))
        ax, ay = x + w * 0.74, y + h * 0.26
        p.drawPolygon(QPoint(int(ax), int(ay - h * 0.05)),
                      QPoint(int(ax + w * 0.06), int(ay + h * 0.12)),
                      QPoint(int(ax - w * 0.12), int(ay + h * 0.06)))
    elif name == "close":
        p.drawLine(int(x + w * 0.3), int(y + h * 0.3),
                   int(x + w * 0.7), int(y + h * 0.7))
        p.drawLine(int(x + w * 0.7), int(y + h * 0.3),
                   int(x + w * 0.3), int(y + h * 0.7))
    elif name == "min":
        p.drawLine(int(x + w * 0.3), int(y + h * 0.6),
                   int(x + w * 0.7), int(y + h * 0.6))
    p.restore()


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


# ── Compliance-score ring (thin, restrained) ────────────────────────────────
class ScoreRing(QWidget):
    def __init__(self, tokens: dict, parent=None):
        super().__init__(parent)
        self.setMinimumSize(132, 132)
        self._tokens = tokens
        self._value = 100.0
        self._anim = QPropertyAnimation(self, b"value", self)
        self._anim.setDuration(800)
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
        return OK_GREEN if self._value >= 85 else WARN_AMBER if self._value >= 60 else BAD_RED

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        t = self._tokens
        side = min(self.width(), self.height())
        thick = 12
        m = thick / 2 + 4
        rect = QRectF((self.width() - side) / 2 + m, (self.height() - side) / 2 + m,
                      side - 2 * m, side - 2 * m)
        col = self._color()
        span = int(-self._value / 100.0 * 360 * 16)
        track = QPen(_qcolor(t.get("panel3", _mix(t["panel"], t["text"], 0.14))), thick)
        track.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(track)
        p.drawArc(rect, 0, 360 * 16)
        arc = QPen(_qcolor(col), thick)
        arc.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(arc)
        p.drawArc(rect, 90 * 16, span)

        p.setPen(_qcolor(t["text"]))
        f1 = QFont(t.get("font", "Segoe UI"), int(side / 5.2))
        f1.setBold(True)
        p.setFont(f1)
        p.drawText(QRectF(rect.x(), rect.y() - side * 0.04, rect.width(), rect.height()),
                   Qt.AlignmentFlag.AlignCenter, f"{self._value:.0f}")
        p.setPen(_qcolor(t.get("text_muted", "#888")))
        f2 = QFont(t.get("font", "Segoe UI"), max(7, int(side / 16)))
        f2.setBold(True)
        p.setFont(f2)
        p.drawText(QRectF(rect.x(), rect.center().y() + side * 0.12, rect.width(), side / 5),
                   Qt.AlignmentFlag.AlignHCenter, "COMPLIANT")
        p.end()


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
        badge = Badge("COMPLIANT" if ok else "FLAGGED", OK_GREEN if ok else BAD_RED)
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
                 sprite_sheet_path: str | None = None, parent=None):
        super().__init__(parent)
        self.fox_widget = fox_widget
        self.settings = settings or FoxSettings()
        # sprite sheet for the little Foxy portrait on the verification card
        self._sprite_path = sprite_sheet_path or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "ultimate_fox_spritesheet.png")

        # ── live state ──
        # Session-live counters give instant feedback on UDP events; the tiles are
        # authoritative from the backend (/v1/stats + /v1/verify), polled below.
        # No local score/block-height/hash synthesis — those are the real chain's.
        self._start_ts       = time.time()
        self._logs_total     = 0
        self._flagged_total  = 0
        self._connected      = None
        self._drag_pos       = QPoint()
        self._recent_events: deque = deque(maxlen=7)

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
        self.org_val = QLabel("acme-health-ai")
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
        self.ov_sub = QLabel("org-4F2A9C · synced 2 min ago · all systems foxy")
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
        self.live_badge = QLabel("● LIVE")
        self.live_badge.setObjectName("liveBadge")
        htop.addWidget(htag)
        htop.addStretch()
        htop.addWidget(self.live_badge)
        self.hero_num = QLabel("100")
        self.hero_num.setObjectName("heroNum")
        hfoot = QLabel("compliance score · all systems foxy")
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
        self.chain_state = QLabel("VERIFIED")
        self.chain_state.setObjectName("chainState")
        state_row.addWidget(self.chain_icon)
        state_row.addWidget(self.chain_state)
        state_row.addStretch()
        self.chain_meta = QLabel("0 blocks · chain intact")
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
        self._sb_worker = _SandboxFetchWorker(
            url, key, int(seq_text), ph, rh, self
        )
        self._sb_worker.result.connect(self._sb_on_result)
        self._sb_worker.start()

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
        font = t.get("font", "Segoe UI")
        mono = t.get("font_mono", "Consolas")
        # When real acrylic blur is active the shell is a translucent veil so the
        # frosted blur of the backdrop shows through; otherwise it's an opaque
        # paprika backdrop (so the window is never see-through to the raw desktop).
        if getattr(self, "_acrylic_on", False):
            shell_bg = ("qradialgradient(cx:0.12, cy:0.0, radius:1.25, fx:0.12, fy:0.0,"
                        " stop:0 rgba(42,26,17,150), stop:0.5 rgba(16,13,10,125),"
                        " stop:1 rgba(11,10,9,125))")
        else:
            # colourful wash so the frosted panels have real colour to frost over
            shell_bg = ("qlineargradient(x1:0, y1:0, x2:1, y2:1,"
                        " stop:0 #6a3514, stop:0.30 #15100c, stop:0.52 #122a5e,"
                        " stop:0.74 #1c0e26, stop:1 #461630)")
        self.shell.setStyleSheet(f"""
            QFrame#shell {{
                background: {shell_bg};
                border: 1px solid rgba(255,255,255,22);
                border-radius: 22px; }}
            QFrame#sidebar {{
                background: rgba(190,186,255,11);
                border: none;
                border-right: 1px solid rgba(200,196,255,16);
                border-top-left-radius: 22px; border-bottom-left-radius: 22px; }}
            QFrame#topbar {{
                background: transparent;
                border-bottom: 1px solid rgba(255,255,255,12); }}
            QLabel#brandName {{ color: {t['text']}; font-size: 15px; font-weight: 800;
                background: transparent; }}
            QLabel#brandSub {{ color: {t['text_muted']}; font-family: '{mono}';
                font-size: 8px; font-weight: 700; letter-spacing: 1.4px;
                background: transparent; }}
            QLabel#logo {{ background: transparent; }}
            QLabel#navMeta {{ color: {t['text_muted']}; font-family: '{mono}';
                font-size: 8px; font-weight: 700; letter-spacing: 1.2px;
                background: transparent; }}
            QLabel#navMetaVal {{ color: {t['text']}; font-size: 12px; font-weight: 700;
                font-family: '{mono}'; background: transparent; }}
            QLabel#pageTitle {{ color: {t['text']}; font-size: 18px; font-weight: 800;
                letter-spacing: -0.5px; background: transparent; }}
            QLabel#emptyState {{ color: {t['text_muted']}; font-size: 12px;
                padding: 16px 4px; background: transparent; }}
            QLabel#sectionCap {{ color: {t['text_muted']}; font-family: '{mono}';
                font-size: 9px; font-weight: 700; letter-spacing: 1.2px;
                background: transparent; }}
            QFrame#verifCard {{
                background: qlineargradient(x1:0, y1:0, x2:0.55, y2:1,
                    stop:0 #c96a2f, stop:0.6 #a4521d, stop:1 #76360f);
                border: 1px solid rgba(255,255,255,16);
                border-radius: 20px; }}
            QLabel#verifFox {{ background: transparent; border: none; }}
            QLabel#verifHint {{ color: #ffffff; background: rgba(0,0,0,170);
                font-family: '{mono}'; font-size: 9px; font-weight: 700;
                border-radius: 11px; padding: 4px 11px; }}
            QLabel#verifEye {{ color: rgba(26,9,0,200); font-family: '{mono}';
                font-size: 10px; font-weight: 700; letter-spacing: 1px;
                background: transparent; }}
            QLabel#verifNum {{ color: #1a0900; font-size: 27px; font-weight: 800;
                letter-spacing: -1px; background: transparent; }}
            QLabel#verifBottom {{ color: rgba(26,9,0,205); font-family: '{mono}';
                font-size: 10px; font-weight: 700; letter-spacing: 1px;
                background: transparent; }}
            QLabel#chainState {{ color: {OK_GREEN}; font-size: 16px; font-weight: 800;
                letter-spacing: 0.5px; background: transparent; }}
            QLabel#chainMeta {{ color: {t['text2']}; font-size: 12px; background: transparent; }}
            QLabel#chainHash {{ color: {t['text_muted']}; font-size: 10px;
                font-family: '{mono}'; background: transparent; }}
            QLabel#tableCap {{ color: {t['text']}; font-size: 12px; font-weight: 800;
                letter-spacing: 1px; background: transparent; }}
            QLabel#tableCount {{ color: {t['text_muted']}; font-family: '{mono}';
                font-size: 10px; background: transparent; }}
            QLabel#connState {{ color: {t['text']}; font-size: 14px; font-weight: 700;
                background: transparent; }}
            QLabel#connUrl {{ color: {t['text_muted']}; font-size: 11px;
                font-family: '{mono}'; background: transparent; }}
            QPushButton#verifyBtn {{
                background: #c96a2f;
                color: #ffffff;
                border: 1px solid rgba(255,255,255,14);
                border-radius: 11px; padding: 9px 0; font-size: 12px; font-weight: 800; }}
            QPushButton#verifyBtn:hover {{ background: #d6743a; }}
            QPushButton#verifyBtn:pressed {{ background: #a8551f; }}
            QLabel#eyebrow {{ color: {t['accent2']}; font-family: '{mono}';
                font-size: 9px; font-weight: 800; letter-spacing: 1.6px;
                background: transparent; }}
            QLabel#h1Head {{ color: {t['text']}; font-size: 26px; font-weight: 800;
                letter-spacing: -1px; background: transparent; }}
            QLabel#subHead {{ color: {t['text_muted']}; font-size: 12px;
                background: transparent; }}
            QPushButton#ctaBtn {{ background: #c96a2f; color: #ffffff;
                border: 1px solid rgba(255,255,255,14); border-radius: 12px; padding: 9px 16px;
                font-size: 12px; font-weight: 800; }}
            QPushButton#ctaBtn:hover {{ background: #d6743a; }}
            QFrame#hero {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #c96a2f, stop:0.62 #a4521d, stop:1 #6e3411);
                border: 1px solid rgba(255,255,255,16);
                border-radius: 24px; }}
            QLabel#heroTag {{ color: rgba(26,9,0,210); font-family: '{mono}';
                font-size: 10px; font-weight: 800; letter-spacing: 1px;
                background: transparent; }}
            QLabel#liveBadge {{ color: #ffffff; background: rgba(0,0,0,160);
                font-family: '{mono}'; font-size: 9px; font-weight: 800;
                border-radius: 10px; padding: 3px 10px; }}
            QLabel#heroNum {{ color: #1a0900; font-size: 46px; font-weight: 800;
                letter-spacing: -2px; background: transparent; }}
            QLabel#heroFoot {{ color: rgba(26,9,0,200); font-family: '{mono}';
                font-size: 11px; font-weight: 700; background: transparent; }}
            QPushButton#tileBlue, QPushButton#tilePink {{
                color: #ffffff; border: 1px solid rgba(255,255,255,15);
                border-radius: 20px; padding: 18px; text-align: left;
                font-size: 15px; font-weight: 800; }}
            QPushButton#tileBlue {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 #4d6cba, stop:1 #39508f); }}
            QPushButton#tilePink {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 #c25c88, stop:1 #9c3c64); }}
            QPushButton#tileBlue:hover, QPushButton#tilePink:hover {{
                border: 1px solid rgba(255,255,255,40); }}
            QWidget {{ font-family: '{font}'; }}
        """)
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
            col, txt = OK_GREEN, "Connected"
        elif self._connected is False:
            col, txt = BAD_RED, "Offline"
        else:
            col, txt = WARN_AMBER, "Connecting"
        # (top-bar connection badge removed; status still shown on the System page)
        if hasattr(self, "conn_state"):
            self.conn_state.setText(txt)
            self.conn_state.setStyleSheet(
                f"color: {col}; font-size: 14px; font-weight: 700; background: transparent;")

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
            "kind": "ok", "policy": policy, "hash": "",
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
        self._stats_worker = StatsWorker(url, key, self)
        self._stats_worker.succeeded.connect(self._on_stats_success)
        self._stats_worker.start()
        self._stats_verify = VerifyWorker(url, key, self)
        self._stats_verify.succeeded.connect(self._on_verify_stats)
        self._stats_verify.start()

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
        self._verify_worker = VerifyWorker(url, key, self)
        self._verify_worker.succeeded.connect(self._on_verify_success)
        self._verify_worker.failed.connect(self._on_verify_failed)
        self._verify_worker.start()

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
        self._refresh_worker = RefreshWorker(url, key, parent=self)
        self._refresh_worker.succeeded.connect(self._on_refresh_success)
        self._refresh_worker.failed.connect(self._on_refresh_failed)
        self._refresh_worker.start()

        # Trigger Analytics update as well
        self._analytics_worker = ThreatAnalyticsWorker(url, key, parent=self)
        self._analytics_worker.succeeded.connect(self._on_analytics_success)
        self._analytics_worker.start()

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
            t = self.settings.theme_tokens()
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
            t = self.settings.theme_tokens()
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
            self.center_on_screen()
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
        self._a_out = QPropertyAnimation(self, b"windowOpacity", self)
        self._a_out.setDuration(140)
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
    d = DashboardWindow()
    d.show_animated()
    d.set_connected(True)

    def hw():
        d.on_hardware({"cpu": random.uniform(8, 92), "ram": random.uniform(28, 78),
                       "battery": random.uniform(20, 100), "plugged": True})

    def hsh():
        d.on_hash_ok({"policy": random.choice(
            ["hipaa_basic", "soc2", "eu_ai_act", "pci_dss"]),
            "tokens": random.randint(40, 900)})

    def breach():
        d.on_policy_breach({"policy": random.choice(["hipaa_basic", "soc2"]),
                            "reason": random.choice(
                                ["Prompt injection signature", "PII leakage detected",
                                 "Anomalous token count", "Replay hash detected"]),
                            "risk_score": random.randint(55, 97),
                            "tokens": random.randint(200, 1500)})

    for _ in range(9):
        hsh()
    breach()
    t1 = QTimer(); t1.timeout.connect(hw); t1.start(2000)
    t2 = QTimer(); t2.timeout.connect(hsh); t2.start(1600)
    t3 = QTimer(); t3.timeout.connect(breach); t3.start(8000)
    hw()
    sys.exit(app.exec())
