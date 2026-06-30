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

import sys
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QFrame, QGraphicsDropShadowEffect,
    QApplication, QSizePolicy,
)
from PyQt6.QtCore import (
    Qt, QPoint, QRect, QThread, pyqtSignal, QPropertyAnimation,
    QEasingCurve, QTimer, QParallelAnimationGroup,
)
from PyQt6.QtGui import QColor, QFont, QPixmap

from fox_settings import FoxSettings
import ai_providers
import window_tracker


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
        bg    = tokens["bubble_user"] if self.is_user else tokens["bubble_fox"]
        r     = min(tokens["radius"], 18)
        # User bubbles get a "sent" corner; fox bubbles get "received" corner
        if tokens["radius"] > 0:
            top_l  = r if not self.is_user else r
            top_r  = r
            bot_r  = 0 if self.is_user else r
            bot_l  = 0 if not self.is_user else r
            radius_css = (
                f"border-top-left-radius: {top_l}px; "
                f"border-top-right-radius: {top_r}px; "
                f"border-bottom-right-radius: {bot_r}px; "
                f"border-bottom-left-radius: {bot_l}px;"
            )
        else:
            radius_css = "border-radius: 0px;"

        # For themes with dark bubble_user, force white text in user bubbles
        user_text = (
            "#FFFFFF" if self.is_user and _is_dark(bg)
            else tokens["text"]
        )
        text_col = user_text if self.is_user else tokens["text"]

        self.setStyleSheet(f"""
            QLabel {{
                background-color: {bg};
                color: {text_col};
                {radius_css}
                padding: 9px 14px;
                font-size: 13px;
                font-family: '{tokens.get('font', 'Segoe UI')}';
                border: none;
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
        self.set_tokens(tokens)

    def set_tokens(self, tokens: dict):
        border = tokens.get("border", "none")
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {tokens['bg']};
                border-radius: {tokens['radius']}px;
                border: {border};
            }}
        """)
        self.setGraphicsEffect(_make_shadow(tokens))


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

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        # Responsive sizing based on screen height
        screen_h = QApplication.primaryScreen().geometry().height()
        popup_h = min(480, int(screen_h * 0.6))
        self.setFixedSize(320, popup_h)

        tokens = self.settings.theme_tokens()
        self._build_ui(tokens, sprite_sheet_path)
        self.apply_theme(tokens)
        self._add_bubble(
            "Hey! I'm Foxy Audit 🦊 Your compliance officer. Ask me about governance, audit trails, or PC health!",
            is_user=False,
        )

    # ─────────────────────────────────────────── UI construction ───
    def _build_ui(self, tokens: dict, sprite_sheet_path: str | None):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self.panel = ClayPanel(tokens)
        outer.addWidget(self.panel)

        self.layout_ = QVBoxLayout(self.panel)
        self.layout_.setContentsMargins(16, 14, 16, 14)
        self.layout_.setSpacing(10)

        # ── header bar ──────────────────────────────────────────────
        self.header_bar = QWidget()
        self.header_bar.setFixedHeight(44)
        self.header_bar.setObjectName("headerBar")
        header = QHBoxLayout(self.header_bar)
        header.setContentsMargins(10, 4, 8, 4)
        header.setSpacing(8)

        # Fox avatar (tiny crop from spritesheet col 0 row 0)
        self._avatar_lbl = QLabel()
        self._avatar_lbl.setFixedSize(32, 32)
        self._avatar_lbl.setObjectName("avatarLbl")
        if sprite_sheet_path:
            pix = QPixmap(sprite_sheet_path)
            if not pix.isNull():
                frame = pix.copy(QRect(0, 0, 192, 208)).scaled(
                    32, 32,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.FastTransformation,
                )
                self._avatar_lbl.setPixmap(frame)
        header.addWidget(self._avatar_lbl)

        self.title_label = QLabel("Foxy Audit — Security Copilot")
        self.title_label.setObjectName("titleLabel")
        header.addWidget(self.title_label, stretch=1)

        self.gear_btn = self._icon_btn("⚙", self._open_settings)
        header.addWidget(self.gear_btn)
        self.close_btn = self._icon_btn("×", self.hide)
        header.addWidget(self.close_btn)

        self.layout_.addWidget(self.header_bar)

        # ── message area ────────────────────────────────────────────
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
        self.layout_.addWidget(self.scroll, stretch=1)

        # ── input row ───────────────────────────────────────────────
        input_row = QHBoxLayout()
        input_row.setSpacing(8)
        self.input_field = QLineEdit()
        self.input_field.setObjectName("inputField")
        self.input_field.setPlaceholderText("Ask Foxy Audit…")
        self.input_field.returnPressed.connect(self.send_message)
        input_row.addWidget(self.input_field, stretch=1)

        self.send_btn = QPushButton("➤")
        self.send_btn.setObjectName("sendBtn")
        self.send_btn.setFixedSize(38, 38)
        self.send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_btn.clicked.connect(self.send_message)
        input_row.addWidget(self.send_btn)
        self.layout_.addLayout(input_row)

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
        self.panel.set_tokens(tokens)
        r   = tokens["radius"]
        acc = tokens["accent"]
        acc_dark = tokens["accent_dark"]
        txt = tokens["text"]
        txt_m = tokens.get("text_muted", "#888888")
        panel_col = tokens["panel"]
        font = tokens.get("font", "Segoe UI")
        hdr_bg  = tokens.get("header_bg",  tokens["bg"])
        hdr_txt = tokens.get("header_text", txt)
        border  = tokens.get("border", "none")
        in_bdr  = tokens.get("input_border", border)

        self.header_bar.setStyleSheet(f"""
            QWidget#headerBar {{
                background-color: {hdr_bg};
                border-radius: {r}px;
                border-bottom-left-radius: 0px;
                border-bottom-right-radius: 0px;
            }}
        """)
        self.title_label.setStyleSheet(f"""
            QLabel#titleLabel {{
                font-weight: 700; font-size: 14px;
                color: {hdr_txt};
                font-family: '{font}';
                background: transparent;
            }}
        """)
        self._avatar_lbl.setStyleSheet("background: transparent; border: none;")

        _icon_ss = f"""
            QPushButton {{
                background-color: {panel_col};
                color: {hdr_txt};
                border-radius: {min(r, 15)}px;
                font-size: 16px; font-weight: bold;
                border: none;
            }}
            QPushButton:hover {{ background-color: {acc}; color: #FFFFFF; }}
            QPushButton:pressed {{ background-color: {acc_dark}; }}
        """
        self.gear_btn.setStyleSheet(_icon_ss)
        self.close_btn.setStyleSheet(_icon_ss)

        self.scroll.setStyleSheet(f"""
            QScrollArea#msgScroll {{
                background-color: {panel_col};
                border-radius: {min(r, 16)}px;
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

        self.input_field.setStyleSheet(f"""
            QLineEdit#inputField {{
                background-color: {panel_col};
                border-radius: {min(r, 18)}px;
                padding: 10px 14px;
                font-size: 13px;
                color: {txt};
                border: {in_bdr};
                font-family: '{font}';
            }}
            QLineEdit#inputField:focus {{
                border: 2px solid {tokens.get('outline_focus', acc)};
            }}
        """)
        self.send_btn.setStyleSheet(f"""
            QPushButton#sendBtn {{
                background-color: {acc};
                color: {'#000000' if not _is_dark(acc) else '#FFFFFF'};
                border-radius: {min(r, 19)}px;
                font-size: 15px; font-weight: bold;
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
        dlg.theme_changed.connect(lambda _k: self.apply_theme(self.settings.theme_tokens()))
        dlg.settings_saved.connect(lambda: self.apply_theme(self.settings.theme_tokens()))
        dlg.exec()

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

    # ──────────────────────────────────────────── chat logic ───────
    def _add_bubble(self, text: str, is_user: bool) -> ChatBubble:
        tokens = self.settings.theme_tokens()
        bubble = ChatBubble(text, is_user, tokens)
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
        QTimer.singleShot(30, self._scroll_to_bottom)
        return bubble

    def _scroll_to_bottom(self):
        sb = self.scroll.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _show_typing_indicator(self):
        tokens = self.settings.theme_tokens()
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
