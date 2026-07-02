"""
OmniAware Fox — Chat Popup
━━━━━━━━━━━━━━━━━━━━━━━━━━
Drop next to omni_fox.py.  Import and wire up:

    from clay_chat_popup import ChatPopup

    # In OmniAwareFox.__init__:
    self.chat_popup = None

    # In mouseReleaseEvent (if click, not drag):
    def open_chat(self):
        if self.chat_popup is None:
            self.chat_popup = ChatPopup(self, settings=self.settings)
        self.chat_popup.popup_near(self)
        self.chat_popup.show_animated()   # ← replaces bare .show()

Design notes
────────────
• Every theme (14 total) is handled by a unified token-driven stylesheet
  builder — no theme ever special-cases the widget tree.
• Hard-shadow themes (Neobrutalism, Win95, Pixelmorphism) use
  QGraphicsDropShadowEffect with blur=0, giving the characteristic
  flat offset box-shadow.  Neon/glow themes (Cyberpunk, Synthwave,
  Terminal) use coloured shadows.  Soft themes use standard warm shadows.
• Popup shows via a QPropertyAnimation on windowOpacity (fade-in) plus a
  Y-translate via QPropertyAnimation on geometry (slide-up), 180 ms.
• _AICallWorker is a QThread subclass that auto-disconnects signals on
  finish so there are no dangling connections or ghost threads on long runs.
• TypingDots uses a QTimer to animate "·", "··", "···" — cleaner than
  a static "...".
• Conversation history is capped at 20 turns so memory never grows unbounded.
"""

from __future__ import annotations

import os
import sys
import json
import time
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QFrame, QGraphicsDropShadowEffect,
    QApplication, QSizePolicy, QStackedWidget,
)
from PyQt6.QtCore import (
    Qt, QPoint, QPointF, QRect, QEvent, QThread, pyqtSignal, QPropertyAnimation,
    QEasingCurve, QTimer, QParallelAnimationGroup,
)
from PyQt6.QtGui import (
    QColor, QFont, QPixmap, QFontDatabase,
    QPainter, QPen, QBrush, QLinearGradient, QPolygonF,
)

from fox_settings import FoxSettings
import ai_providers
import window_tracker


# ─────────────────────────── glass skin (matches the Auditor Console) ───────
_FOX, _FOX2, _FOX3 = "#ff7a2e", "#ff9d52", "#d65a16"
_INK, _MUTED = "#f7f1e8", "#8c8174"
# colourful frosted backdrop the chat surfaces frost over
# near-black frosted-glass backdrop — a calm hint of transparency, readable
_PANEL_WASH = ("qlineargradient(x1:0, y1:0, x2:1, y2:1,"
               " stop:0 rgba(11,11,13,240), stop:0.5 rgba(15,15,18,240),"
               " stop:1 rgba(8,8,10,240))")

_FONT_CACHE: dict[str, str] = {}
_FONTS_REGISTERED = False


def _register_bundled_fonts():
    global _FONTS_REGISTERED
    if _FONTS_REGISTERED:
        return
    _FONTS_REGISTERED = True
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
    try:
        for fn in os.listdir(d):
            if fn.lower().endswith((".ttf", ".otf")):
                QFontDatabase.addApplicationFont(os.path.join(d, fn))
    except Exception:
        pass


def _pick_font(kind: str) -> str:
    if kind in _FONT_CACHE:
        return _FONT_CACHE[kind]
    _register_bundled_fonts()
    try:
        fams = set(QFontDatabase.families())
    except Exception:
        fams = set()
    cands = (["Unbounded", "Segoe UI Variable Display", "Segoe UI"] if kind == "disp"
             else ["Space Mono", "Cascadia Mono", "Consolas"])
    pick = next((c for c in cands if c in fams),
                "Segoe UI" if kind == "disp" else "Consolas")
    _FONT_CACHE[kind] = pick
    return pick


def _glass_tokens() -> dict:
    """Fixed frosted-glass skin for the chat popup — mirrors the dashboard, so
    any chat theme passed in is ignored (the copilot always reads as branded)."""
    return {
        "bg": "#11101a", "panel": "rgba(176,174,255,20)",
        "radius": 22, "border": "1px solid rgba(190,186,255,16)",
        "accent": "#c96a2f", "accent_dark": "#a8551f",
        "text": _INK, "text_muted": _MUTED,
        "font": _pick_font("disp"), "font_mono": _pick_font("mono"),
        "bubble_user": "#c96a2f", "bubble_fox": "rgba(176,174,255,22)",
        "header_bg": "transparent", "header_text": _INK,
        "input_border": "1px solid rgba(190,186,255,16)", "outline_focus": "#c96a2f",
        "shadow_blur": 30, "shadow_dx": 0, "shadow_dy": 12,
        "shadow_color": (0, 0, 0), "shadow_alpha": 110,
    }


# ─────────────────────────────────────────────── background AI worker ──────
class _AICallWorker(QThread):
    """Runs ai_providers.call_ai() off the UI thread.

    Signals are explicitly disconnected in _cleanup() to prevent the
    common Qt6 memory leak where finished QThreads keep a live reference
    because a connected signal holds a reference to a lambda closure that
    captures `self`.
    """
    succeeded = pyqtSignal(str)
    failed    = pyqtSignal(str)

    def __init__(self, history: list[dict], system_prompt: str,
                 settings: FoxSettings, parent: QWidget | None = None):
        super().__init__(parent)
        self._history       = history
        self._system_prompt = system_prompt
        self._settings      = settings
        self.finished.connect(self._cleanup)

    def run(self):
        try:
            reply = ai_providers.call_ai(
                self._history, self._system_prompt, self._settings)
            self.succeeded.emit(reply)
        except Exception as exc:
            self.failed.emit(str(exc))

    def _cleanup(self):
        try:
            self.succeeded.disconnect()
            self.failed.disconnect()
        except RuntimeError:
            pass  # already disconnected


# ────────────────────────────────────────────── animated typing indicator ──
class TypingDots(QLabel):
    """Animates ·  ··  ···  in an infinite cycle to signal AI thinking."""

    def __init__(self, tokens: dict, parent: QWidget | None = None):
        super().__init__("·", parent)
        self._step  = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(420)
        self._apply(tokens)

    def _tick(self):
        self._step = (self._step + 1) % 3
        self.setText("·" * (self._step + 1))

    def _apply(self, tokens: dict):
        self.setStyleSheet(
            f"color: {tokens['text_muted']}; "
            f"font-size: 20px; "
            f"font-family: '{tokens.get('font', 'Segoe UI')}'; "
            f"background: transparent; border: none; "
            f"padding: 6px 14px;"
        )

    def stop(self):
        self._timer.stop()
        self.deleteLater()


# ─────────────────────────────────────────────────── chat bubble widget ────
class ChatBubble(QLabel):
    """A single message bubble.  Adapts instantly to any theme tokens."""

    MAX_WIDTH = 240

    def __init__(self, text: str, is_user: bool,
                 tokens: dict, parent: QWidget | None = None):
        super().__init__(text, parent)
        self.is_user = is_user
        self.setWordWrap(True)
        self.setMaximumWidth(self.MAX_WIDTH)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self.apply_tokens(tokens)

    def apply_tokens(self, tokens: dict):
        face = tokens.get("font", "Segoe UI")   # same display font as the dashboard
        r = 16
        if self.is_user:                                  # sent — glassy paprika bubble
            bg = ("qlineargradient(x1:0, y1:0, x2:0, y2:1, "
                  "stop:0 rgba(255,164,106,242), stop:0.45 rgba(226,118,54,228), "
                  "stop:1 rgba(193,89,33,232))")
            text_col = "#ffffff"
            border = "1px solid rgba(255,255,255,82)"      # bright glass rim
            radius_css = (f"border-top-left-radius:{r}px;border-top-right-radius:{r}px;"
                          f"border-bottom-right-radius:4px;border-bottom-left-radius:{r}px;")
            pad = "9px 14px"
        else:                                             # received — plain text, no bubble
            bg = "transparent"
            text_col = tokens.get("text", "#f7f1e8")
            border = "none"
            radius_css = ""
            pad = "2px 2px"
        self.setStyleSheet(f"""
            QLabel {{
                background: {bg};
                color: {text_col};
                {radius_css}
                padding: {pad};
                font-size: 12.5px;
                font-family: '{face}';
                border: {border};
            }}
        """)


def _is_dark(color_str: str) -> bool:
    """Crude luminance check — returns True for colors darker than mid-grey."""
    s = color_str.strip().lstrip("#")
    if len(s) == 6:
        try:
            r, g, b = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
            return (r * 299 + g * 587 + b * 114) / 1000 < 128
        except ValueError:
            pass
    return False  # can't tell → assume light


# ──────────────────────────────────────────────── main panel (ClayPanel) ───
def _make_shadow(tokens: dict) -> QGraphicsDropShadowEffect:
    """Build a QGraphicsDropShadowEffect from theme tokens."""
    eff = QGraphicsDropShadowEffect()
    eff.setBlurRadius(tokens.get("shadow_blur", 20))
    eff.setOffset(tokens.get("shadow_dx", 0), tokens.get("shadow_dy", 8))
    r, g, b = tokens.get("shadow_color", (60, 40, 20))
    eff.setColor(QColor(r, g, b, tokens.get("shadow_alpha", 80)))
    return eff


class ClayPanel(QFrame):
    """The rounded/sharp frame that acts as the popup's main surface."""

    def __init__(self, tokens: dict, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("clayPanel")
        self.set_tokens(tokens)

    def set_tokens(self, tokens: dict):
        # the most external frame — a clear lavender-white rim around the window.
        # Scope the rule to #clayPanel: a bare `QFrame {…}` would cascade its
        # border/radius into every descendant QFrame *and QLabel* (QLabel is a
        # QFrame subclass), drawing a stray outline around the title/bubbles.
        self.setStyleSheet(f"""
            QFrame#clayPanel {{
                background: {_PANEL_WASH};
                border-radius: 22px;
                border: 2px solid rgba(255,255,255,60);
            }}
        """)
        self.setGraphicsEffect(_make_shadow(tokens))


# ─────────────────────────────────────────────── gradient wordmark ──────────
class GradientText(QLabel):
    """A QLabel whose text is painted with a horizontal brand gradient — the
    warm paprika→orange we use across the app — instead of a flat colour."""

    def __init__(self, text: str = "", parent: QWidget | None = None):
        super().__init__(text, parent)
        self._stops = [(0.0, "#ffb474"), (0.5, "#ff7a2e"), (1.0, "#d65a16")]

    def set_gradient(self, stops: list[tuple[float, str]]):
        self._stops = stops
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        rect = self.rect()
        grad = QLinearGradient(float(rect.left()), 0.0, float(rect.right()), 0.0)
        for pos, col in self._stops:
            grad.setColorAt(pos, QColor(col))
        p.setPen(QPen(QBrush(grad), 0))
        p.setFont(self.font())
        align = self.alignment() or (Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        p.drawText(rect, align, self.text())
        p.end()


# ─────────────────────────────────────────────── vector icon button ────────
class _IconButton(QPushButton):
    """A crisp, painted vector icon button (not an OS emoji glyph). The button
    background + hover come from its stylesheet; the glyph is drawn on top."""

    def __init__(self, kind: str, slot=None, size: int = 30,
                 color: str = "#f2ede6", parent: QWidget | None = None):
        super().__init__(parent)
        self._kind  = kind
        self._color = QColor(color)
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        if slot is not None:
            self.clicked.connect(slot)

    def set_icon_color(self, color: str):
        self._color = QColor(color)
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)                       # stylesheet bg + hover
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(self._color, 2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        cx, cy = self.width() / 2.0, self.height() / 2.0
        k = self._kind
        if k == "close":
            d = 4.5
            p.drawLine(QPointF(cx - d, cy - d), QPointF(cx + d, cy + d))
            p.drawLine(QPointF(cx - d, cy + d), QPointF(cx + d, cy - d))
        elif k == "minimize":                           # single lower bar
            d = 4.5
            p.drawLine(QPointF(cx - d, cy + 3), QPointF(cx + d, cy + 3))
        elif k == "chev_right":
            p.drawPolyline(QPolygonF([QPointF(cx - 2, cy - 5),
                                      QPointF(cx + 3, cy),
                                      QPointF(cx - 2, cy + 5)]))
        elif k == "chev_left":
            p.drawPolyline(QPolygonF([QPointF(cx + 2, cy - 5),
                                      QPointF(cx - 3, cy),
                                      QPointF(cx + 2, cy + 5)]))
        elif k == "settings":                           # tune / sliders
            xl, xr = cx - 6, cx + 6
            rows = ((cy - 5, cx + 2), (cy, cx - 3), (cy + 5, cx + 3))
            for y, _kx in rows:
                p.drawLine(QPointF(xl, y), QPointF(xr, y))
            p.setPen(QPen(self._color, 1.0))
            p.setBrush(QBrush(self._color))
            for y, kx in rows:
                p.drawEllipse(QPointF(kx, y), 2.4, 2.4)
        elif k == "send":
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(self._color))
            p.drawPolygon(QPolygonF([QPointF(cx - 6, cy - 6), QPointF(cx + 7, cy),
                                     QPointF(cx - 6, cy + 6), QPointF(cx - 2.5, cy)]))
        elif k == "trash":
            p.drawLine(QPointF(cx - 5.5, cy - 4), QPointF(cx + 5.5, cy - 4))   # lid
            p.drawPolyline(QPolygonF([QPointF(cx - 1.8, cy - 4), QPointF(cx - 1.6, cy - 6),
                                      QPointF(cx + 1.6, cy - 6), QPointF(cx + 1.8, cy - 4)]))
            p.drawLine(QPointF(cx - 4, cy - 4), QPointF(cx - 3.2, cy + 6))     # body
            p.drawLine(QPointF(cx + 4, cy - 4), QPointF(cx + 3.2, cy + 6))
            p.drawLine(QPointF(cx - 3.2, cy + 6), QPointF(cx + 3.2, cy + 6))
            p.drawLine(QPointF(cx - 1.4, cy - 1), QPointF(cx - 1.4, cy + 3.5))  # ribs
            p.drawLine(QPointF(cx + 1.4, cy - 1), QPointF(cx + 1.4, cy + 3.5))
        p.end()


# ─────────────────────────────────────────── chat-history persistence ──────
def _history_path() -> str:
    d = os.path.join(os.path.expanduser("~"), ".foxy_audit")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return os.path.join(d, "chat_history.json")


def _load_sessions() -> list:
    try:
        with open(_history_path(), encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_sessions(sessions: list):
    try:
        with open(_history_path(), "w", encoding="utf-8") as f:
            json.dump(sessions, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _rel_time(ts) -> str:
    try:
        d = time.time() - float(ts)
    except Exception:
        return ""
    if d < 60:      return "just now"
    if d < 3600:    return f"{int(d // 60)}m ago"
    if d < 86400:   return f"{int(d // 3600)}h ago"
    if d < 604800:  return f"{int(d // 86400)}d ago"
    return time.strftime("%b %d", time.localtime(float(ts)))


class _HistoryRow(QFrame):
    """A clickable saved-chat row (click loads it; the trash button deletes)."""
    clicked = pyqtSignal()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


# ──────────────────────────────────────────────────── the popup itself ─────
FOX_SYSTEM_PROMPT = (
    "You are Foxy Audit, an AI compliance officer and governance assistant "
    "who lives on the user's desktop as a friendly pixel-art fox mascot. "
    "You are part of the Foxy Audit platform — a Trust-as-a-Service product "
    "that provides Governance-as-Code for AI startups. "
    "Speak briefly (1-3 short sentences), professionally but warmly, "
    "like a knowledgeable compliance teammate. You help with: "
    "explaining compliance concepts (SOC 2, HIPAA, EU AI Act, NIST AI RMF), "
    "understanding audit trails and hash chains, "
    "monitoring system health, providing governance tips, "
    "security concepts (OWASP, defensive hardening, recon methodology), "
    "and answering questions about AI safety, cryptographic logging, and "
    "zero-knowledge payloads. "
    "You do not write exploit code or attack payloads — point to "
    "established tooling/documentation instead. "
    "You can also track tasks, nudge reminders, and check PC health."
)


class ChatPopup(QWidget):
    popup_closed = pyqtSignal()   # emitted when the popup is hidden/closed
    """
    Frameless translucent chat window.  Pops up next to the fox sprite.

    Call show_animated() instead of show() for the slide+fade entrance.
    Theme changes apply instantly via apply_theme().
    """

    def __init__(
        self,
        fox_widget: QWidget,
        settings: FoxSettings | None = None,
        sprite_sheet_path: str | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.fox_widget        = fox_widget
        self.settings          = settings or FoxSettings()
        self._history:  list[dict]       = []
        self._bubbles:  list[ChatBubble] = []
        self._ai_worker: _AICallWorker | None = None
        self._typing:   TypingDots | None     = None
        self._typing_wrapper: QWidget | None  = None
        self._empty_state: QWidget | None     = None
        self._sessions: list  = _load_sessions()   # saved until deleted
        self._current_session: dict | None = None

        # A normal frameless top-level window (NOT a Tool / always-on-top
        # palette): the user can click another app to send the chat behind it,
        # and it gets a real taskbar button so the minimize control below can
        # minimize/restore it.  WindowMinimizeButtonHint + WindowSystemMenuHint
        # enable that minimize/restore on a frameless window on Windows.
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowSystemMenuHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("Foxy Audit — Copilot")
        self.setMouseTracking(True)
        # Resizable + movable frameless window (drag the top bar to move,
        # drag any edge/corner to resize).
        screen_h = QApplication.primaryScreen().geometry().height()
        default_h = min(560, int(screen_h * 0.7))
        self.setMinimumSize(300, 380)
        self.resize(360, default_h)

        # drag / edge-resize state
        self._drag_active  = False
        self._resize_edge: str | None = None
        self._press_global = QPoint()
        self._press_geo    = QRect()

        tokens = _glass_tokens()
        self._build_ui(tokens, sprite_sheet_path)
        self.apply_theme(tokens)

        # Drag from the top bar, resize from the panel's outer margin. The title
        # has no click action, so it forwards to the header (it is transparent to
        # mouse); but the gear/close buttons, scroll area and input MUST stay
        # hittable — a transparent container hides its children from hit-testing —
        # so we route drag/resize via an event filter instead.
        self.title_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.panel.setMouseTracking(True)
        self.panel.installEventFilter(self)
        # Drag the window from anywhere non-interactive: the top bar AND the
        # whole message area (incl. bubbles). Buttons / input / scrollbar are
        # not registered, so they keep their own clicks.
        for w in (self.header_bar, self.scroll.viewport(), self.messages_container,
                  self.history_scroll.viewport(), self.history_container):
            self._make_draggable(w)

        # welcoming empty-state with quick-ask chips (hidden once you start chatting)
        self._empty_state = self._build_empty_state()
        self.messages_layout.insertWidget(0, self._empty_state)

    # ─────────────────────────────────────────── UI construction ───
    def _build_ui(self, tokens: dict, sprite_sheet_path: str | None):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self.panel = ClayPanel(tokens)
        outer.addWidget(self.panel)

        self.layout_ = QVBoxLayout(self.panel)
        self.layout_.setContentsMargins(11, 11, 11, 11)
        self.layout_.setSpacing(8)

        # ── header bar (centred title, balanced 70px side slots) ─────
        self.header_bar = QWidget()
        self.header_bar.setFixedHeight(46)
        self.header_bar.setObjectName("headerBar")
        header = QHBoxLayout(self.header_bar)
        header.setContentsMargins(10, 0, 10, 0)
        header.setSpacing(0)

        left = QWidget(); left.setFixedWidth(104)
        lh = QHBoxLayout(left); lh.setContentsMargins(0, 0, 0, 0); lh.setSpacing(6)
        self.back_btn = _IconButton("chev_left", self._show_chat)   # history → chat
        self.gear_btn = _IconButton("settings", self._open_settings)
        lh.addWidget(self.back_btn); lh.addWidget(self.gear_btn); lh.addStretch()
        self.back_btn.hide()
        header.addWidget(left)

        header.addStretch()
        self.title_label = GradientText("Foxy Audit")
        self.title_label.setObjectName("titleLabel")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.addWidget(self.title_label)
        header.addStretch()

        right = QWidget(); right.setFixedWidth(104)
        rh = QHBoxLayout(right); rh.setContentsMargins(0, 0, 0, 0); rh.setSpacing(6)
        self.history_btn = _IconButton("chev_right", self._show_history)  # chat → history
        self.min_btn     = _IconButton("minimize", self.showMinimized)
        self.close_btn   = _IconButton("close", self.hide)
        rh.addStretch(); rh.addWidget(self.history_btn)
        rh.addWidget(self.min_btn); rh.addWidget(self.close_btn)
        header.addWidget(right)

        self.layout_.addWidget(self.header_bar)

        # ── content stack: chat page (0) + history page (1) ──────────
        self.content_stack = QStackedWidget()
        self.content_stack.setStyleSheet("background: transparent;")
        self.layout_.addWidget(self.content_stack, stretch=1)

        # -- chat page --
        chat_page = QWidget(); chat_page.setStyleSheet("background: transparent;")
        cp = QVBoxLayout(chat_page); cp.setContentsMargins(0, 0, 0, 0); cp.setSpacing(8)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setObjectName("msgScroll")
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.messages_container = QWidget()
        self.messages_container.setObjectName("msgContainer")
        self.messages_container.setStyleSheet("background: transparent;")
        self.messages_layout = QVBoxLayout(self.messages_container)
        self.messages_layout.setContentsMargins(8, 8, 8, 8)
        self.messages_layout.setSpacing(6)
        self.messages_layout.addStretch()
        self.scroll.setWidget(self.messages_container)
        cp.addWidget(self.scroll, stretch=1)

        self.input_pill = QFrame()
        self.input_pill.setObjectName("inputPill")
        pill = QHBoxLayout(self.input_pill)
        pill.setContentsMargins(16, 5, 6, 5)
        pill.setSpacing(6)
        self.input_field = QLineEdit()
        self.input_field.setObjectName("inputField")
        self.input_field.setPlaceholderText("Ask Foxy Audit…")
        self.input_field.returnPressed.connect(self.send_message)
        pill.addWidget(self.input_field, stretch=1)
        self.send_btn = _IconButton("send", self.send_message, size=34, color="#ffffff")
        self.send_btn.setObjectName("sendBtn")
        pill.addWidget(self.send_btn)
        cp.addWidget(self.input_pill)
        self.content_stack.addWidget(chat_page)

        # -- history page --
        hist_page = QWidget(); hist_page.setStyleSheet("background: transparent;")
        hp = QVBoxLayout(hist_page); hp.setContentsMargins(2, 0, 2, 0); hp.setSpacing(8)
        cap_row = QHBoxLayout(); cap_row.setContentsMargins(4, 2, 4, 0)
        self.hist_cap = QLabel("SAVED CHATS"); self.hist_cap.setObjectName("histCap")
        cap_row.addWidget(self.hist_cap); cap_row.addStretch()
        self.new_chat_btn = QPushButton("＋ New chat"); self.new_chat_btn.setObjectName("newChatBtn")
        self.new_chat_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.new_chat_btn.clicked.connect(self._new_chat)
        cap_row.addWidget(self.new_chat_btn)
        hp.addLayout(cap_row)

        self.history_scroll = QScrollArea()
        self.history_scroll.setWidgetResizable(True)
        self.history_scroll.setObjectName("histScroll")
        self.history_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.history_container = QWidget()
        self.history_container.setStyleSheet("background: transparent;")
        self.history_layout = QVBoxLayout(self.history_container)
        self.history_layout.setContentsMargins(4, 4, 4, 4)
        self.history_layout.setSpacing(8)
        self.history_layout.addStretch()
        self.history_scroll.setWidget(self.history_container)
        hp.addWidget(self.history_scroll, stretch=1)
        self.content_stack.addWidget(hist_page)

    @staticmethod
    def _icon_btn(icon: str, slot) -> QPushButton:
        btn = QPushButton(icon)
        btn.setFixedSize(30, 30)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(slot)
        return btn

    # ─────────────────────────────────────────────── theming ───────
    def apply_theme(self, tokens: dict):
        """Re-skin the entire popup in place.  Safe to call at any time."""
        tokens = _glass_tokens()   # fixed frosted-glass skin (ignores chat themes)
        self.panel.set_tokens(tokens)
        r   = tokens["radius"]
        acc = tokens["accent"]
        acc_dark = tokens["accent_dark"]
        txt = tokens["text"]
        txt_m = tokens.get("text_muted", "#888888")
        panel_col = tokens["panel"]
        font = tokens.get("font", "Segoe UI")
        font_mono = tokens.get("font_mono", "Consolas")
        hdr_txt = tokens.get("header_text", txt)
        # frosted-glass fill for the pills — a soft translucent white tint with a
        # bright rim, sitting on the even backdrop; shared by header + input bars
        pill_fill   = "rgba(255,255,255,18)"
        pill_border = "1px solid rgba(255,255,255,52)"

        self.header_bar.setStyleSheet(f"""
            QWidget#headerBar {{
                background-color: {pill_fill};
                border-radius: 14px;
                border: {pill_border};
            }}
        """)
        self.title_label.setFont(QFont(font, 14, QFont.Weight.Bold))
        self.title_label.set_gradient([(0.0, "#ffb474"), (0.5, "#ff7a2e"), (1.0, "#d65a16")])
        self.title_label.setStyleSheet("QLabel#titleLabel { background: transparent; border: none; }")

        _icon_ss = f"""
            QPushButton {{
                background-color: rgba(255,255,255,15);
                color: {hdr_txt};
                border-radius: 15px;
                font-size: 18px; font-weight: bold;
                border: none;
            }}
            QPushButton:hover {{ background-color: {acc}; color: #FFFFFF; }}
            QPushButton:pressed {{ background-color: {acc_dark}; }}
        """
        for b in (self.gear_btn, self.close_btn, self.min_btn,
                  self.history_btn, self.back_btn):
            b.setStyleSheet(_icon_ss)

        # history page — caption, "new chat" button, scroll
        self.hist_cap.setStyleSheet(
            f"color: {txt_m}; font-family: '{font}'; font-size: 9px; "
            f"font-weight: 800; letter-spacing: 1.5px; background: transparent;")
        self.new_chat_btn.setStyleSheet(f"""
            QPushButton#newChatBtn {{
                background: rgba(255,255,255,14); color: {txt};
                border: 1px solid rgba(255,255,255,40); border-radius: 11px;
                padding: 5px 12px; font-family: '{font}';
                font-size: 11px; font-weight: 700;
            }}
            QPushButton#newChatBtn:hover {{ background: {acc}; color: #ffffff; border: 1px solid {acc}; }}
        """)
        _scroll_ss = f"""
            QScrollArea {{ background: transparent; border: none; }}
            QScrollBar:vertical {{ width: 5px; background: transparent; }}
            QScrollBar::handle:vertical {{ background: {acc}; border-radius: 2px; min-height: 20px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
        """
        self.history_scroll.setStyleSheet(_scroll_ss)

        self.scroll.setStyleSheet(f"""
            QScrollArea#msgScroll {{
                background: transparent;
                border: none;
            }}
            QScrollBar:vertical {{
                width: 5px;
                background: transparent;
            }}
            QScrollBar::handle:vertical {{
                background: {acc};
                border-radius: 2px;
                min-height: 20px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
        """)

        self.input_pill.setStyleSheet(f"""
            QFrame#inputPill {{
                background-color: {pill_fill};
                border-radius: 22px;
                border: {pill_border};
            }}
        """)
        self.input_field.setStyleSheet(f"""
            QLineEdit#inputField {{
                background: transparent;
                border: none;
                padding: 6px 0;
                font-size: 12.5px;
                color: {txt};
                font-family: '{font}';
            }}
        """)
        self.send_btn.setStyleSheet(f"""
            QPushButton#sendBtn {{
                background-color: {acc};
                color: #ffffff;
                border-radius: 17px;
                font-size: 14px; font-weight: bold;
                border: none;
            }}
            QPushButton#sendBtn:hover {{ background-color: {acc_dark}; }}
            QPushButton#sendBtn:pressed {{ background-color: {acc_dark}; }}
        """)

        for bubble in self._bubbles:
            bubble.apply_tokens(tokens)

    def _open_settings(self):
        from settings_dialog import SettingsDialog
        dlg = SettingsDialog(self.settings, self)
        dlg.settings_saved.connect(lambda: self.apply_theme(_glass_tokens()))
        dlg.exec()

    # ─────────────────────────────────── move + edge-resize ────────
    _RESIZE_MARGIN = 8

    _EDGE_CURSORS = {
        "l":  Qt.CursorShape.SizeHorCursor,  "r":  Qt.CursorShape.SizeHorCursor,
        "t":  Qt.CursorShape.SizeVerCursor,  "b":  Qt.CursorShape.SizeVerCursor,
        "tl": Qt.CursorShape.SizeFDiagCursor, "br": Qt.CursorShape.SizeFDiagCursor,
        "tr": Qt.CursorShape.SizeBDiagCursor, "bl": Qt.CursorShape.SizeBDiagCursor,
    }

    def _edge_at(self, pos: QPoint) -> str | None:
        m = self._RESIZE_MARGIN
        w, h = self.width(), self.height()
        x, y = pos.x(), pos.y()
        left, right = x <= m, x >= w - m
        top, bottom = y <= m, y >= h - m
        if top and left:     return "tl"
        if top and right:    return "tr"
        if bottom and left:  return "bl"
        if bottom and right: return "br"
        if left:   return "l"
        if right:  return "r"
        if top:    return "t"
        if bottom: return "b"
        return None

    def _make_draggable(self, widget: QWidget):
        """Route a non-interactive widget's mouse events to window-drag.
        Keep the normal arrow cursor — no move cursor over the chat."""
        widget.setMouseTracking(True)
        widget.setCursor(Qt.CursorShape.ArrowCursor)
        widget.installEventFilter(self)

    def _move_to(self, global_pos: QPoint):
        self.move(self._press_geo.topLeft() + (global_pos - self._press_global))

    def eventFilter(self, obj, event):
        et = event.type()
        left = bool(event.buttons() & Qt.MouseButton.LeftButton) \
            if et in (QEvent.Type.MouseMove, QEvent.Type.MouseButtonRelease) else True
        # ── panel outer margin: resize on an edge, move anywhere else ──
        if obj is self.panel:
            if et == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self._press_global = event.globalPosition().toPoint()
                self._press_geo    = self.geometry()
                self._resize_edge  = self._edge_at(event.position().toPoint())
                self._drag_active  = self._resize_edge is None
                return True
            if et == QEvent.Type.MouseMove:
                if left and self._resize_edge:
                    self._resize_to(event.globalPosition().toPoint())
                    return True
                if left and self._drag_active:
                    self._move_to(event.globalPosition().toPoint())
                    return True
                if not left:
                    edge = self._edge_at(event.position().toPoint())
                    self.panel.setCursor(self._EDGE_CURSORS.get(edge, Qt.CursorShape.ArrowCursor))
            elif et == QEvent.Type.MouseButtonRelease:
                self._resize_edge = None
                self._drag_active = False
            return super().eventFilter(obj, event)

        # ── any other tracked surface (header, chat area, bubbles): move ──
        if et == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            self._press_global = event.globalPosition().toPoint()
            self._press_geo    = self.geometry()
            self._drag_active  = True
            self._resize_edge  = None
            return True
        if et == QEvent.Type.MouseMove and left and self._drag_active:
            self._move_to(event.globalPosition().toPoint())
            return True
        if et == QEvent.Type.MouseButtonRelease:
            self._drag_active = False
        return super().eventFilter(obj, event)

    def _resize_to(self, global_pos: QPoint):
        delta = global_pos - self._press_global
        geo   = QRect(self._press_geo)
        e     = self._resize_edge
        minw, minh = self.minimumWidth(), self.minimumHeight()
        if "l" in e:
            geo.setLeft(min(geo.left() + delta.x(), geo.right() - minw))
        if "r" in e:
            geo.setRight(max(geo.right() + delta.x(), geo.left() + minw))
        if "t" in e:
            geo.setTop(min(geo.top() + delta.y(), geo.bottom() - minh))
        if "b" in e:
            geo.setBottom(max(geo.bottom() + delta.y(), geo.top() + minh))
        self.setGeometry(geo)

    def resizeEvent(self, event):
        # let bubbles grow/shrink with the window so the chat fills the space
        bubble_w = max(220, int(self.width() * 0.72))
        for b in self._bubbles:
            b.setMaximumWidth(bubble_w)
        super().resizeEvent(event)

    # ─────────────────────────────────────────── positioning ───────
    def popup_near(self, fox_widget: QWidget):
        fx, fy = fox_widget.x(), fox_widget.y()
        fw, fh = fox_widget.width(), fox_widget.height()
        screen = QApplication.primaryScreen().geometry()

        x = fx - self.width() - 12
        if x < screen.x():
            x = fx + fw + 12

        y = fy - self.height() + fh
        y = max(screen.y() + 10, min(y, screen.bottom() - self.height() - 10))
        self.move(x, y)

    # ──────────────────────────────────────────── animations ───────
    def show_animated(self):
        """Fade-in + slide-up entrance animation (180 ms)."""
        self.setWindowOpacity(0.0)
        # show() alone won't restore a chat the user minimized to the taskbar,
        # so re-opening from the fox must explicitly un-minimize it.
        if self.isMinimized():
            self.showNormal()
        else:
            self.show()
        self.raise_()
        self.activateWindow()

        start_geo = self.geometry().translated(0, 18)
        end_geo   = self.geometry()

        self._anim_opacity = QPropertyAnimation(self, b"windowOpacity", self)
        self._anim_opacity.setDuration(180)
        self._anim_opacity.setStartValue(0.0)
        self._anim_opacity.setEndValue(1.0)
        self._anim_opacity.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._anim_pos = QPropertyAnimation(self, b"geometry", self)
        self._anim_pos.setDuration(180)
        self._anim_pos.setStartValue(start_geo)
        self._anim_pos.setEndValue(end_geo)
        self._anim_pos.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._anim_group = QParallelAnimationGroup(self)
        self._anim_group.addAnimation(self._anim_opacity)
        self._anim_group.addAnimation(self._anim_pos)
        self._anim_group.start()

    # ─────────────────────────────────────────── empty state ───────
    def _build_empty_state(self) -> QWidget:
        """A welcoming intro with quick-ask chips, shown before any chatting."""
        t     = _glass_tokens()
        face  = t.get("font", "Segoe UI")
        ink   = t.get("text", "#f7f1e8")
        muted = "#9b9aae"
        acc   = t.get("accent", "#c96a2f")

        w = QWidget()
        w.setObjectName("emptyState")
        w.setStyleSheet("background: transparent;")
        v = QVBoxLayout(w)
        v.setContentsMargins(6, 14, 6, 6)
        v.setSpacing(7)

        emoji = QLabel("🦊")
        emoji.setStyleSheet("background: transparent; font-size: 30px;")
        v.addWidget(emoji)

        hi = QLabel("Hey, I'm Foxy Audit")
        hi.setStyleSheet(f"background: transparent; color: {ink}; "
                         f"font-family: '{face}'; font-size: 17px; font-weight: 800;")
        v.addWidget(hi)

        sub = QLabel("Your compliance copilot. Ask me about governance, "
                     "audit trails, or your PC's health.")
        sub.setWordWrap(True)
        sub.setStyleSheet(f"background: transparent; color: {muted}; "
                          f"font-family: '{face}'; font-size: 12px;")
        v.addWidget(sub)

        v.addSpacing(8)
        cap = QLabel("TRY ASKING")
        cap.setStyleSheet(f"background: transparent; color: {muted}; "
                          f"font-family: '{face}'; font-size: 9px; font-weight: 800; "
                          f"letter-spacing: 1.5px;")
        v.addWidget(cap)

        chip_ss = (
            "QPushButton#suggestChip {"
            "  background: rgba(255,255,255,14); color: %s;"
            "  border: 1px solid rgba(255,255,255,38); border-radius: 13px;"
            "  padding: 11px 14px; text-align: left;"
            "  font-family: '%s'; font-size: 12px; font-weight: 600; }"
            "QPushButton#suggestChip:hover {"
            "  background: rgba(255,255,255,28); border: 1px solid %s; }"
        ) % (ink, face, acc)

        for label, prompt in (
            ("🩺   How healthy is my PC right now?", "How healthy is my PC right now?"),
            ("📋   Explain my latest policy breach", "Explain my latest policy breach"),
            ("📂   Open a file or folder for me",    "open "),
        ):
            chip = QPushButton(label)
            chip.setObjectName("suggestChip")
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setStyleSheet(chip_ss)
            chip.clicked.connect(lambda _=False, p=prompt: self._use_suggestion(p))
            v.addWidget(chip)

        # let the intro text drag the window too (chips stay clickable)
        for lbl in (w, emoji, hi, sub, cap):
            self._make_draggable(lbl)
        return w

    def _use_suggestion(self, prompt: str):
        if prompt.endswith(" "):          # e.g. "open " — let the user finish the path
            self.input_field.setText(prompt)
            self.input_field.setFocus()
        else:
            self.input_field.setText(prompt)
            self.send_message()

    def _hide_empty_state(self):
        if self._empty_state is not None:
            self._empty_state.hide()

    # ────────────────────────────────────── history / navigation ───
    def _show_chat(self):
        self.back_btn.hide(); self.gear_btn.show(); self.history_btn.show()
        self.title_label.setText("Foxy Audit")
        self.content_stack.setCurrentIndex(0)

    def _show_history(self):
        self._refresh_history_list()
        self.gear_btn.hide(); self.history_btn.hide(); self.back_btn.show()
        self.title_label.setText("History")
        self.content_stack.setCurrentIndex(1)

    def _new_chat(self):
        self._clear_chat()
        self._current_session = None
        self._history = []
        if self._empty_state is not None:
            self._empty_state.show()
        self._show_chat()
        self.raise_()
        self.activateWindow()
        self.input_field.setFocus()

    def _clear_chat(self):
        self._remove_typing_indicator()
        for i in reversed(range(self.messages_layout.count())):
            w = self.messages_layout.itemAt(i).widget()
            if w is not None and w is not self._empty_state:
                w.setParent(None)
                w.deleteLater()
        self._bubbles = []

    def _record_message(self, text: str, is_user: bool):
        if self._current_session is None:
            self._current_session = {"id": str(time.time()), "ts": time.time(),
                                     "title": "", "messages": []}
            self._sessions.insert(0, self._current_session)
        self._current_session["messages"].append(
            {"role": "user" if is_user else "assistant", "content": text})
        if is_user and not self._current_session.get("title"):
            self._current_session["title"] = text[:48]
        self._current_session["ts"] = time.time()
        _save_sessions(self._sessions)

    def _load_session(self, session: dict):
        self._clear_chat()
        self._current_session = session
        msgs = session.get("messages", [])
        self._history = [{"role": m.get("role", "user"), "content": m.get("content", "")}
                         for m in msgs][-20:]
        self._hide_empty_state()
        for m in msgs:
            self._add_bubble(m.get("content", ""),
                             is_user=(m.get("role") == "user"), record=False)
        self._show_chat()

    def _delete_session(self, session: dict):
        if session in self._sessions:
            self._sessions.remove(session)
        if session is self._current_session:
            self._current_session = None
        _save_sessions(self._sessions)
        self._refresh_history_list()

    def _refresh_history_list(self):
        while self.history_layout.count():
            w = self.history_layout.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        if not self._sessions:
            empty = QLabel("No saved chats yet.\nYour conversations show up here.")
            empty.setWordWrap(True)
            empty.setStyleSheet(f"background: transparent; color: #9b9aae; border: none; "
                                f"font-family: '{_glass_tokens()['font']}'; font-size: 12px;")
            self.history_layout.addWidget(empty)
        else:
            for session in self._sessions:
                self.history_layout.addWidget(self._history_row(session))
        self.history_layout.addStretch()

    def _history_row(self, session: dict) -> QWidget:
        t = _glass_tokens()
        face = t["font"]; ink = t["text"]; muted = "#9b9aae"; acc = t["accent"]
        row = _HistoryRow(); row.setObjectName("histRow")
        row.setCursor(Qt.CursorShape.PointingHandCursor)
        row.setStyleSheet(f"""
            QFrame#histRow {{ background: rgba(255,255,255,12);
                border: 1px solid rgba(255,255,255,28); border-radius: 12px; }}
            QFrame#histRow:hover {{ background: rgba(255,255,255,24);
                border: 1px solid {acc}; }}
        """)
        h = QHBoxLayout(row); h.setContentsMargins(12, 9, 8, 9); h.setSpacing(8)
        col = QVBoxLayout(); col.setContentsMargins(0, 0, 0, 0); col.setSpacing(2)
        title = QLabel(session.get("title") or "New chat")
        title.setStyleSheet(f"background: transparent; color: {ink}; border: none; "
                            f"font-family: '{face}'; font-size: 12.5px; font-weight: 700;")
        title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        n = len(session.get("messages", []))
        meta = QLabel(f"{_rel_time(session.get('ts', 0))} · {n} messages")
        meta.setStyleSheet(f"background: transparent; color: {muted}; border: none; "
                           f"font-family: '{face}'; font-size: 10px;")
        meta.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        col.addWidget(title); col.addWidget(meta)
        h.addLayout(col, stretch=1)
        dele = _IconButton("trash", size=30, color="#b9b6c6")
        dele.setObjectName("histDel")
        dele.setStyleSheet("""
            QPushButton#histDel { background: transparent; border: none; border-radius: 8px; }
            QPushButton#histDel:hover { background: rgba(255,90,90,75); }
        """)
        dele.clicked.connect(lambda _=False, s=session: self._delete_session(s))
        h.addWidget(dele)
        row.clicked.connect(lambda s=session: self._load_session(s))
        return row

    # ──────────────────────────────────────────── chat logic ───────
    def _add_bubble(self, text: str, is_user: bool, record: bool = True) -> ChatBubble:
        if record:
            self._record_message(text, is_user)
        tokens = _glass_tokens()
        bubble = ChatBubble(text, is_user, tokens)
        bubble.setMaximumWidth(max(220, int(self.width() * 0.72)))
        self._bubbles.append(bubble)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        if is_user:
            row.addStretch()
            row.addWidget(bubble)
        else:
            row.addWidget(bubble)
            row.addStretch()

        wrapper = QWidget()
        wrapper.setStyleSheet("background: transparent;")
        wrapper.setLayout(row)
        self.messages_layout.insertWidget(self.messages_layout.count() - 1, wrapper)
        # dragging the window works over the message text too
        self._make_draggable(bubble)
        self._make_draggable(wrapper)
        QTimer.singleShot(30, self._scroll_to_bottom)
        return bubble

    def _scroll_to_bottom(self):
        sb = self.scroll.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _show_typing_indicator(self):
        tokens = _glass_tokens()
        self._typing = TypingDots(tokens)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self._typing)
        row.addStretch()

        self._typing_wrapper = QWidget()
        self._typing_wrapper.setStyleSheet("background: transparent;")
        self._typing_wrapper.setLayout(row)
        self.messages_layout.insertWidget(
            self.messages_layout.count() - 1, self._typing_wrapper)
        self._make_draggable(self._typing_wrapper)
        QTimer.singleShot(30, self._scroll_to_bottom)

    def _remove_typing_indicator(self):
        if self._typing:
            self._typing.stop()
            self._typing = None
        if self._typing_wrapper:
            self._typing_wrapper.deleteLater()
            self._typing_wrapper = None

    def send_message(self):
        text = self.input_field.text().strip()
        if not text:
            return
        self._hide_empty_state()
        self.input_field.clear()
        self._add_bubble(text, is_user=True)

        # Handle "open <path>" locally
        result = self._try_open_path(text)
        if result is not None:
            self._add_bubble(result, is_user=False)
            return

        self.input_field.setEnabled(False)
        self.send_btn.setEnabled(False)
        self._show_typing_indicator()

        # Small realistic delay before dispatching to AI
        QTimer.singleShot(400, lambda: self._dispatch_ai(text))

    def _dispatch_ai(self, text: str):
        self._history.append({"role": "user", "content": text})
        # Kill any previous worker that's somehow still alive
        if self._ai_worker and self._ai_worker.isRunning():
            self._ai_worker.quit()
            self._ai_worker.wait(400)

        self._ai_worker = _AICallWorker(
            list(self._history), FOX_SYSTEM_PROMPT, self.settings, self)
        self._ai_worker.succeeded.connect(self._on_ai_success)
        self._ai_worker.failed.connect(self._on_ai_failure)
        self._ai_worker.start()

    def _on_ai_success(self, reply: str):
        self._remove_typing_indicator()
        self._history.append({"role": "assistant", "content": reply})
        self._history = self._history[-20:]  # rolling window — no memory leak
        self._add_bubble(reply, is_user=False)
        self._re_enable_input()

    def _on_ai_failure(self, _err: str):
        self._remove_typing_indicator()
        fallback = (
            "(Can't reach the AI backend right now — "
            "set your key in Settings › AI Brain.)"
        )
        self._add_bubble(fallback, is_user=False)
        self._re_enable_input()

    def _re_enable_input(self):
        self.input_field.setEnabled(True)
        self.send_btn.setEnabled(True)
        self.input_field.setFocus()

    def _try_open_path(self, text: str) -> str | None:
        lower = text.strip().lower()
        if not lower.startswith("open "):
            return None
        path = text.strip()[5:].strip().strip('"\'')
        try:
            window_tracker.open_path(path)
            return "Opened it for you! 🦊"
        except FileNotFoundError:
            return f'Hmm, couldn\'t find "{path}" — double-check the path?'
        except Exception as exc:
            return f"Couldn't open that: {exc}"

    def showEvent(self, event):
        # always pop to the front of the z-order when shown
        super().showEvent(event)
        self.raise_()
        self.activateWindow()

    # ─────────────────────────────────────── clean shutdown ───────
    def hideEvent(self, event):
        self.popup_closed.emit()
        super().hideEvent(event)

    def closeEvent(self, event):
        self.popup_closed.emit()
        if self._ai_worker and self._ai_worker.isRunning():
            self._ai_worker.quit()
            self._ai_worker.wait(800)
        super().closeEvent(event)


# ────────────────────────────────────────────────────── standalone test ─────
if __name__ == "__main__":
    app = QApplication(sys.argv)

    class _FakeFox(QWidget):
        def __init__(self):
            super().__init__()
            self.resize(192, 208)
            self.move(900, 600)
            self.show()

    fox  = _FakeFox()
    popup = ChatPopup(fox)
    popup.popup_near(fox)
    popup.show_animated()
    sys.exit(app.exec())
