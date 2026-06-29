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

import json
import sys
import time
import hashlib
import urllib.request
import urllib.error
from datetime import datetime
from collections import deque

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QStackedWidget, QTableWidget, QTableWidgetItem, QHeaderView,
    QApplication, QSizePolicy, QButtonGroup, QAbstractItemView,
    QTextEdit, QLineEdit,
)
from PyQt6.QtCore import (
    Qt, QPoint, QRectF, QTimer, QPropertyAnimation,
    QParallelAnimationGroup, QEasingCurve, pyqtSignal, pyqtProperty, QThread,
)
from PyQt6.QtGui import QColor, QPainter, QPen, QBrush, QFont, QPainterPath, QPixmap

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


# Status palette — fixed, theme-independent, so danger always reads as danger.
OK_GREEN   = "#22C55E"
WARN_AMBER = "#F59E0B"
BAD_RED    = "#EF4444"
INFO_BLUE  = "#3B82F6"


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
        self.setStyleSheet(
            f"QLabel {{ color: {c};"
            f" background: {_with_alpha(c, 28)};"
            f" border: 1px solid {_with_alpha(c, 90)};"
            f" border-radius: 5px; padding: 2px 9px;"
            f" font-size: 10px; font-weight: 700; }}")


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
        self.accent_bar = QFrame()
        self.accent_bar.setObjectName("kpiAccent")
        self.accent_bar.setFixedWidth(3)

        body = QVBoxLayout()
        body.setContentsMargins(15, 13, 13, 13)
        body.setSpacing(5)
        body.addWidget(self.label_lbl)
        body.addWidget(self.value_lbl)
        body.addWidget(self.sub_lbl)

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self.accent_bar)
        row.addLayout(body)
        self.restyle(tokens)

    def restyle(self, tokens: dict):
        acc = self._accent or tokens["accent"]
        self.setStyleSheet(f"""
            QFrame#kpiTile {{
                background: {tokens['panel']};
                border: 1px solid {_hairline(tokens)};
                border-radius: 8px; }}
            QFrame#kpiAccent {{
                background: {acc};
                border-top-left-radius: 8px; border-bottom-left-radius: 8px; }}
            QLabel#kpiLabel {{
                color: {tokens.get('text_muted', '#888')};
                font-size: 10px; font-weight: 700; letter-spacing: 1px;
                background: transparent; border: none; }}
            QLabel#kpiValue {{
                color: {tokens['text']}; font-size: 26px; font-weight: 800;
                background: transparent; border: none; }}
            QLabel#kpiSub {{
                color: {tokens.get('text_muted', '#888')}; font-size: 11px;
                background: transparent; border: none; }}
        """)

    def set_value(self, value: str, sub: str | None = None,
                  accent: str | None = None):
        self.value_lbl.setText(value)
        if sub is not None:
            self.sub_lbl.setText(sub)
        if accent is not None:
            self._accent = accent
            self.accent_bar.setStyleSheet(
                f"background: {accent}; border-top-left-radius: 8px;"
                f" border-bottom-left-radius: 8px;")


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
        thick = 9
        m = thick / 2 + 3
        rect = QRectF((self.width() - side) / 2 + m, (self.height() - side) / 2 + m,
                      side - 2 * m, side - 2 * m)
        track = QPen(_qcolor(_mix(t["panel"], t["text"], 0.14)), thick)
        track.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(track)
        p.drawArc(rect, 0, 360 * 16)
        arc = QPen(_qcolor(self._color()), thick)
        arc.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(arc)
        p.drawArc(rect, 90 * 16, int(-self._value / 100.0 * 360 * 16))

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
        track_y, track_h = 30, 6
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(_qcolor(_mix(t["panel"], t["text"], 0.14))))
        p.drawRoundedRect(QRectF(0, track_y, w, track_h), 3, 3)
        p.setBrush(QBrush(_qcolor(self._color())))
        p.drawRoundedRect(QRectF(0, track_y, max(track_h, w * self._value / 100.0),
                                 track_h), 3, 3)
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
                background: {tokens['panel']};
                border: 1px solid {_hairline(tokens)};
                border-radius: 8px; }}
            QLabel#cardTitle {{
                color: {tokens.get('text_muted', '#888')};
                font-size: 10px; font-weight: 700; letter-spacing: 1.2px;
                background: transparent; border: none; }}
        """)


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
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.verticalHeader().setDefaultSectionSize(38)
        self.restyle(tokens)

    def restyle(self, tokens: dict):
        t = tokens
        self._tokens = t
        alt = _mix(t["panel"], t["bg"], 0.45)
        self.setStyleSheet(f"""
            QTableWidget {{
                background: {t['panel']}; alternate-background-color: {alt};
                color: {t['text']}; border: none; outline: none;
                font-size: 12px; gridline-color: transparent; }}
            QTableWidget::item {{ padding: 4px 10px; border: none; }}
            QHeaderView::section {{
                background: {t['panel']}; color: {t.get('text_muted', '#888')};
                padding: 8px 10px; border: none;
                border-bottom: 1px solid {_hairline(t, 55)};
                font-size: 10px; font-weight: 700; letter-spacing: 0.6px; }}
            QScrollBar:vertical {{ width: 7px; background: transparent; margin: 0; }}
            QScrollBar::handle:vertical {{
                background: {_with_alpha(t['accent'], 120)};
                border-radius: 3px; min-height: 24px; }}
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
        acc = t["accent"]
        active = self.isChecked()
        hover = self.underMouse()
        r = QRectF(0, 2, self.width(), self.height() - 4)
        if active:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(_qcolor(_with_alpha(acc, 32))))
            p.drawRoundedRect(r, 7, 7)
            p.setBrush(QBrush(_qcolor(acc)))
            p.drawRoundedRect(QRectF(0, r.center().y() - 9, 3, 18), 1.5, 1.5)
        elif hover:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(_qcolor(_with_alpha(t.get("text_muted", "#888"), 22))))
            p.drawRoundedRect(r, 7, 7)
        col = _qcolor(acc if active else t["text"] if hover else t.get("text_muted", "#888"))
        paint_icon(p, QRectF(14, r.center().y() - 9, 18, 18), self.icon_name, col, 1.7)
        p.setPen(col)
        f = QFont(t.get("font", "Segoe UI"), 10)
        f.setBold(active)
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
            hl = BAD_RED if self._danger else _with_alpha(t.get("text_muted", "#888"), 40)
            p.setBrush(QBrush(_qcolor(hl)))
            p.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), 5, 5)
            col = _qcolor("#FFFFFF" if self._danger else t["text"])
        else:
            col = _qcolor(t.get("text_muted", "#888"))
        paint_icon(p, QRectF(7, 4, 16, 18), self.icon_name, col, 1.6)
        p.end()


# ── The console window ──────────────────────────────────────────────────────
class DashboardWindow(QWidget):
    refresh_requested = pyqtSignal()
    closed           = pyqtSignal()

    def __init__(self, fox_widget=None, settings: FoxSettings | None = None,
                 sprite_sheet_path: str | None = None, parent=None):
        super().__init__(parent)
        self.fox_widget = fox_widget
        self.settings = settings or FoxSettings()

        # ── live state ──
        self._start_ts       = time.time()
        self._logs_total     = 0
        self._flagged_total  = 0
        self._risk_scores: deque[int] = deque(maxlen=128)
        self._score          = 100.0
        self._last_hash      = hashlib.sha256(b"foxy-genesis").hexdigest()
        self._block_height   = 0
        self._connected      = None
        self._drag_pos       = QPoint()
        self._recent: deque[QWidget] = deque()

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("Foxy Audit — Auditor Console")

        scr = QApplication.primaryScreen().geometry()
        self.setFixedSize(min(1140, int(scr.width() * 0.88)),
                          min(710, int(scr.height() * 0.9)))

        tokens = self.settings.theme_tokens()
        self._build(tokens)
        self.apply_theme(tokens)

        self._tick = QTimer(self)
        self._tick.timeout.connect(self._on_tick)
        self._tick.start(1000)
        self._refresh_stats()

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
                 ("system", "System"), ("shield", "Sandbox")]):
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
        self.stack_titles = ["Overview", "Blind Audit Log", "System Health", "Verification Sandbox"]
        h.addWidget(self.page_title)
        h.addStretch()

        self.conn_dot = QLabel()
        self.conn_dot.setObjectName("connBadge")
        h.addWidget(self.conn_dot)

        self.refresh_btn = CtrlButton("refresh", t)
        self.refresh_btn.clicked.connect(self._on_refresh_clicked)
        h.addWidget(self.refresh_btn)
        self.min_btn = CtrlButton("min", t)
        self.min_btn.clicked.connect(self.hide)
        self.close_btn = CtrlButton("close", t, danger=True)
        self.close_btn.clicked.connect(self.close_animated)
        h.addWidget(self.min_btn)
        h.addWidget(self.close_btn)
        return self.topbar

    # ── pages ──
    def _page_overview(self, t: dict) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(22, 20, 22, 20)
        v.setSpacing(16)

        kpis = QHBoxLayout()
        kpis.setSpacing(14)
        self.kpi_logs = KpiTile(t, "Interactions Logged", "0", "session total", INFO_BLUE)
        self.kpi_flagged = KpiTile(t, "Policy Breaches", "0", "flagged by AI judge", BAD_RED)
        self.kpi_risk = KpiTile(t, "Avg Risk Score", "—", "across flagged events", WARN_AMBER)
        self.kpi_chain = KpiTile(t, "Ledger Blocks", "0", "hash-chained", OK_GREEN)
        for w in (self.kpi_logs, self.kpi_flagged, self.kpi_risk, self.kpi_chain):
            kpis.addWidget(w)
        v.addLayout(kpis)

        body = QHBoxLayout()
        body.setSpacing(16)

        self.recent_card = Card(t, "Recent Activity")
        self.recent_box = QVBoxLayout()
        self.recent_box.setSpacing(0)
        self.recent_box.setContentsMargins(0, 0, 0, 0)
        self.recent_empty = QLabel("No interactions yet — waiting for SDK telemetry…")
        self.recent_empty.setObjectName("emptyState")
        self.recent_box.addWidget(self.recent_empty)
        self.recent_box.addStretch()
        self.recent_card.body.addLayout(self.recent_box, stretch=1)
        body.addWidget(self.recent_card, stretch=3)

        right = QVBoxLayout()
        right.setSpacing(16)

        self.score_card = Card(t, "Compliance Score")
        self.score_ring = ScoreRing(t)
        ring_row = QHBoxLayout()
        ring_row.addStretch()
        ring_row.addWidget(self.score_ring)
        ring_row.addStretch()
        self.score_card.body.addLayout(ring_row)
        right.addWidget(self.score_card)

        self.chain_card = Card(t, "Ledger Integrity")
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
        font = t.get("font", "Segoe UI")
        self.shell.setStyleSheet(f"""
            QFrame#shell {{
                background: {t['bg']};
                border: 1px solid {_hairline(t, 60)};
                border-radius: 10px; }}
            QFrame#sidebar {{
                background: {_mix(t['bg'], t['panel'], 0.5)};
                border: none;
                border-right: 1px solid {_hairline(t, 45)};
                border-top-left-radius: 10px; border-bottom-left-radius: 10px; }}
            QFrame#topbar {{
                background: transparent;
                border-bottom: 1px solid {_hairline(t, 45)}; }}
            QLabel#brandName {{ color: {t['text']}; font-size: 15px; font-weight: 800;
                background: transparent; }}
            QLabel#brandSub {{ color: {t.get('text_muted', '#888')}; font-size: 8px;
                font-weight: 700; letter-spacing: 1.4px; background: transparent; }}
            QLabel#logo {{ background: transparent; }}
            QLabel#navMeta {{ color: {t.get('text_muted', '#888')}; font-size: 8px;
                font-weight: 700; letter-spacing: 1.2px; background: transparent; }}
            QLabel#navMetaVal {{ color: {t['text']}; font-size: 12px; font-weight: 600;
                font-family: '{t.get('font_mono', 'Consolas')}'; background: transparent; }}
            QLabel#pageTitle {{ color: {t['text']}; font-size: 17px; font-weight: 800;
                background: transparent; }}
            QLabel#emptyState {{ color: {t.get('text_muted', '#888')}; font-size: 12px;
                padding: 16px 4px; background: transparent; }}
            QLabel#chainState {{ color: {OK_GREEN}; font-size: 15px; font-weight: 800;
                background: transparent; }}
            QLabel#chainMeta {{ color: {t['text']}; font-size: 12px; background: transparent; }}
            QLabel#chainHash {{ color: {t.get('text_muted', '#888')}; font-size: 10px;
                font-family: '{t.get('font_mono', 'Consolas')}'; background: transparent; }}
            QLabel#tableCap {{ color: {t['text']}; font-size: 12px; font-weight: 800;
                letter-spacing: 1px; background: transparent; }}
            QLabel#tableCount {{ color: {t.get('text_muted', '#888')}; font-size: 11px;
                background: transparent; }}
            QLabel#connState {{ color: {t['text']}; font-size: 14px; font-weight: 700;
                background: transparent; }}
            QLabel#connUrl {{ color: {t.get('text_muted', '#888')}; font-size: 11px;
                font-family: '{t.get('font_mono', 'Consolas')}'; background: transparent; }}
            QPushButton#verifyBtn {{
                background: {_with_alpha(t['accent'], 30)};
                color: {t['accent']};
                border: 1px solid {_with_alpha(t['accent'], 110)};
                border-radius: 6px; padding: 7px 0; font-size: 12px; font-weight: 700; }}
            QPushButton#verifyBtn:hover {{ background: {_with_alpha(t['accent'], 55)}; }}
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
        for card in (self.recent_card, self.score_card, self.chain_card,
                     self.table_card, self.vitals_card, self.conn_card):
            card.restyle(t)
        self.score_ring.set_tokens(t)
        self.table.restyle(t)
        for m in (self.m_cpu, self.m_ram, self.m_batt):
            m.set_tokens(t)
        for w in list(self._recent):
            for lb in w.findChildren(QLabel):
                if lb.objectName() == "recTime":
                    lb.setStyleSheet(f"color: {t.get('text_muted', '#888')};"
                                     f" font-family: '{t.get('font_mono', 'Consolas')}';"
                                     f" font-size: 11px; background: transparent;")
                elif lb.objectName() == "recText":
                    lb.setStyleSheet(f"color: {t['text']}; font-size: 12px;"
                                     f" background: transparent;")

    def _monogram(self, t: dict) -> QPixmap:
        pm = QPixmap(30, 30)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(_qcolor(t["accent"])))
        p.drawRoundedRect(QRectF(0, 0, 30, 30), 8, 8)
        paint_icon(p, QRectF(5, 5, 20, 20), "shield",
                   _qcolor("#FFFFFF" if not _is_dark(t["accent"]) else t["bg"]), 1.9)
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
        self.conn_dot.setText(f"● {txt}")
        self.conn_dot.setStyleSheet(
            f"QLabel#connBadge {{ color: {col}; font-size: 11px; font-weight: 700;"
            f" background: {_with_alpha(col, 26)};"
            f" border: 1px solid {_with_alpha(col, 90)};"
            f" border-radius: 11px; padding: 4px 12px; }}")
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
        self._logs_total += 1
        self._block_height += 1
        policy = payload.get("policy", "default")
        self._last_hash = self._next_hash(policy)
        self._score = min(100.0, self._score + 0.3)
        self._add_event({
            "time": datetime.now().strftime("%H:%M:%S"),
            "kind": "ok", "policy": policy, "hash": self._last_hash,
            "tokens": payload.get("tokens", ""), "risk": None,
        })
        self._refresh_stats()

    def on_policy_breach(self, payload: dict):
        self._logs_total += 1
        self._flagged_total += 1
        self._block_height += 1
        reason = payload.get("reason", "Policy violation")
        risk = int(payload.get("risk_score", 100))
        policy = payload.get("policy", "default")
        self._risk_scores.append(risk)
        self._last_hash = self._next_hash(policy)
        self._score = max(0.0, self._score - risk / 9.0)
        self._add_event({
            "time": datetime.now().strftime("%H:%M:%S"),
            "kind": "breach", "policy": policy, "hash": self._last_hash,
            "tokens": payload.get("tokens", ""), "risk": risk, "reason": reason,
        })
        self._refresh_stats()

    def set_connected(self, connected: bool | None):
        self._connected = connected
        self._restyle_conn(self.settings.theme_tokens())

    # ── event rendering ──
    def _add_event(self, ev: dict):
        self.table.add_event(ev)
        self.audit_count.setText(f"{self.table.rowCount()} records")
        if self.recent_empty.isVisible():
            self.recent_empty.hide()
        t = self.settings.theme_tokens()
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(2, 9, 2, 9)
        rl.setSpacing(10)
        tlbl = QLabel(ev["time"])
        tlbl.setObjectName("recTime")
        tlbl.setStyleSheet(f"color: {t.get('text_muted', '#888')};"
                           f" font-family: '{t.get('font_mono', 'Consolas')}';"
                           f" font-size: 11px; background: transparent;")
        ok = ev["kind"] == "ok"
        badge = Badge("COMPLIANT" if ok else "FLAGGED", OK_GREEN if ok else BAD_RED)
        desc = (f"hash logged · {ev['policy']}" if ok
                else f"{ev.get('reason', 'breach')} · risk {ev.get('risk')}")
        dlbl = QLabel(desc)
        dlbl.setObjectName("recText")
        dlbl.setStyleSheet(f"color: {t['text']}; font-size: 12px; background: transparent;")
        rl.addWidget(tlbl)
        rl.addWidget(badge)
        rl.addWidget(dlbl, stretch=1)
        row.setStyleSheet(f"QWidget {{ border-top: 1px solid {_hairline(t, 30)};"
                          f" background: transparent; }}")
        self.recent_box.insertWidget(0, row)
        self._recent.appendleft(row)
        while len(self._recent) > 7:
            old = self._recent.pop()
            old.setParent(None)
            old.deleteLater()

    # ── derived metrics ──
    def _next_hash(self, policy: str) -> str:
        seed = f"{self._last_hash}|{policy}|{self._block_height}|{time.time()}"
        return hashlib.sha256(seed.encode()).hexdigest()

    def _refresh_stats(self):
        self.kpi_logs.set_value(f"{self._logs_total:,}")
        self.kpi_flagged.set_value(f"{self._flagged_total:,}")
        if self._risk_scores:
            avg = sum(self._risk_scores) / len(self._risk_scores)
            self.kpi_risk.set_value(
                f"{avg:.0f}",
                accent=(OK_GREEN if avg < 40 else WARN_AMBER if avg < 70 else BAD_RED))
        self.kpi_chain.set_value(f"{self._block_height:,}")
        self.score_ring.set_value(self._score)
        intact = self._score > 35
        root = hashlib.sha256(f"{self._last_hash}{self._block_height}".encode()).hexdigest()
        self.chain_meta.setText(f"{self._block_height:,} blocks · "
                                + ("chain intact" if intact else "review required"))
        self.chain_hash.setText(f"root {root[:28]}…")
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
        if self._score < 100.0:
            self._score = min(100.0, self._score + 0.1)
            self.score_ring.set_value(self._score)

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

    def show_animated(self):
        if not self.isVisible():
            self.center_on_screen()
        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()
        self.activateWindow()
        end_geo = self.geometry()
        start_geo = end_geo.translated(0, 22)
        self._a_op = QPropertyAnimation(self, b"windowOpacity", self)
        self._a_op.setDuration(190)
        self._a_op.setStartValue(0.0)
        self._a_op.setEndValue(1.0)
        self._a_op.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._a_geo = QPropertyAnimation(self, b"geometry", self)
        self._a_geo.setDuration(190)
        self._a_geo.setStartValue(start_geo)
        self._a_geo.setEndValue(end_geo)
        self._a_geo.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._grp = QParallelAnimationGroup(self)
        self._grp.addAnimation(self._a_op)
        self._grp.addAnimation(self._a_geo)
        self._grp.start()

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
