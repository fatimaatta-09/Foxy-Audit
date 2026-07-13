"""
OmniAware Fox — Settings Dialog
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
A premium settings UI in the app's fixed matte skin. Three tabs:
  • AI Brain    — provider/key/model/URL with inline connection test
  • Behaviour   — cooldown, pat sensitivity, roaming, glance toggles
  • Foxy Audit  — org API key + backend URL with a live health probe

Design rules
────────────
• One fixed matte skin (`_matte_tokens()`) that mirrors the Auditor Console
  and chat popup — there is no theme picker any more, the look is constant.
• Zero plain Qt defaults — every widget is fully styled via QSS built from
  the matte tokens, using the bundled Unbounded / Space Mono fonts.
• Tab bar uses a custom pill-style selector so it never looks like a
  default QTabWidget.
• Sliders use a custom groove + handle that matches the accent colour.
• All QThread workers (connection test) are properly cleaned up via
  finished.connect(_cleanup) so there are no ghost threads on repeated
  test clicks.
"""

from __future__ import annotations

import os
import sys
import urllib.request
import urllib.error
from PyQt6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QHBoxLayout, QWidget,
    QLabel, QLineEdit, QPushButton, QSlider, QCheckBox,
    QComboBox, QScrollArea, QFrame, QGraphicsDropShadowEffect,
    QMessageBox, QSizePolicy, QStackedWidget,
)
from PyQt6.QtCore import Qt, pyqtSignal, QThread, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QColor, QFont, QFontDatabase

from fox_settings import FoxSettings, AI_PROVIDERS
from clay_chat_popup import GradientText, _IconButton
import window_tracker
import ai_providers


# ───────────────────────────── bundled fonts + fixed matte skin ─────────────
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


def _matte_tokens() -> dict:
    """Fixed matte skin for the settings dialog — mirrors the Auditor Console
    and chat popup so the whole app reads as one branded surface. There is no
    theme switching any more; the look is constant."""
    return {
        "bg": "#0c0c0e", "radius": 16,
        "accent": "#c96a2f", "accent_dark": "#a8551f",
        "panel": "rgba(255,255,255,16)",
        "text": "#f4efe8", "text_muted": "#9b9aae",
        "font": _pick_font("disp"), "font_disp": _pick_font("disp"),
        "header_bg": "rgba(255,255,255,10)", "header_text": "#f4efe8",
        "border": "2px solid rgba(255,255,255,55)",
        "input_border": "1px solid rgba(255,255,255,45)",
        "outline_focus": "#c96a2f",
        "shadow_blur": 34, "shadow_dx": 0, "shadow_dy": 14,
        "shadow_color": (0, 0, 0), "shadow_alpha": 150,
    }


# ───────────────────────────────────────── background connection tester ────
class _TestConnectionWorker(QThread):
    succeeded = pyqtSignal(str)
    failed    = pyqtSignal(str)

    def __init__(self, settings: FoxSettings, parent=None):
        super().__init__(parent)
        self._settings = settings
        self.finished.connect(self._cleanup)

    def run(self):
        try:
            reply = ai_providers.call_ai(
                [{"role": "user", "content": "Say hi in three words."}],
                "You are a connection test probe. Reply briefly.",
                self._settings,
            )
            self.succeeded.emit(reply)
        except Exception as exc:
            self.failed.emit(str(exc))

    def _cleanup(self):
        try:
            self.succeeded.disconnect()
            self.failed.disconnect()
        except RuntimeError:
            pass


class _FoxyBackendWorker(QThread):
    succeeded = pyqtSignal()
    failed    = pyqtSignal(str)

    def __init__(self, backend_url: str, org_key: str, parent=None):
        super().__init__(parent)
        self.backend_url = backend_url.rstrip("/")
        self.org_key = org_key
        self.finished.connect(self._cleanup)

    def run(self):
        url = f"{self.backend_url}/v1/health"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.org_key}"})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    self.succeeded.emit()
                else:
                    self.failed.emit(f"HTTP {resp.status}")
        except urllib.error.URLError as e:
            self.failed.emit(str(e.reason))
        except Exception as e:
            self.failed.emit(str(e))

    def _cleanup(self):
        try:
            self.succeeded.disconnect()
            self.failed.disconnect()
        except RuntimeError:
            pass


# ───────────────────────────────────────────────── helper: dark check ─────
def _is_dark(color_str: str) -> bool:
    s = color_str.strip().lstrip("#")
    if len(s) == 6:
        try:
            r, g, b = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
            return (r * 299 + g * 587 + b * 114) / 1000 < 140
        except ValueError:
            pass
    return False


# ──────────────────────────────────────────── custom pill tab bar ──────────
class PillTabBar(QWidget):
    """
    A horizontal row of pill-shaped tab buttons.
    Replaces the default QTabWidget tab bar.
    """
    tab_changed = pyqtSignal(int)

    def __init__(self, labels: list[str], tokens: dict, parent=None):
        super().__init__(parent)
        self._buttons: list[QPushButton] = []
        self._current = 0
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        for i, label in enumerate(labels):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setChecked(i == 0)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedHeight(34)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.clicked.connect(lambda _, idx=i: self._select(idx))
            self._buttons.append(btn)
            layout.addWidget(btn)
        self.apply_tokens(tokens)

    def apply_tokens(self, tokens: dict):
        r   = min(tokens["radius"], 17)
        acc = tokens["accent"]
        acc_dark = tokens["accent_dark"]
        panel = tokens["panel"]
        txt  = tokens["text"]
        font = tokens.get("font", "Segoe UI")
        for btn in self._buttons:
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {panel};
                    color: {txt};
                    border-radius: {r}px;
                    font-size: 13px; font-weight: 600;
                    font-family: '{font}';
                    border: 1px solid transparent;
                    padding: 0 10px;
                }}
                QPushButton:checked {{
                    background-color: {acc};
                    color: {'#000' if not _is_dark(acc) else '#fff'};
                    border: 1px solid {acc_dark};
                }}
                QPushButton:hover:!checked {{
                    background-color: {acc_dark};
                    color: #ffffff;
                }}
            """)

    def _select(self, idx: int):
        self._current = idx
        for i, btn in enumerate(self._buttons):
            btn.setChecked(i == idx)
        self.tab_changed.emit(idx)

    def current(self) -> int:
        return self._current


# ──────────────────────────────────────────── labelled slider widget ───────
class LabelledSlider(QWidget):
    """Slider with a live numeric readout, fully themed."""

    valueChanged = pyqtSignal(int)

    def __init__(self, label: str, minimum: int, maximum: int,
                 value: int, suffix: str = "", tokens: dict | None = None,
                 parent=None):
        super().__init__(parent)
        self._suffix = suffix
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        top_row = QHBoxLayout()
        self._label = QLabel(label)
        self._value_lbl = QLabel(f"{value}{suffix}")
        self._value_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        top_row.addWidget(self._label)
        top_row.addStretch()
        top_row.addWidget(self._value_lbl)
        layout.addLayout(top_row)

        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(minimum, maximum)
        self._slider.setValue(value)
        self._slider.valueChanged.connect(self._on_change)
        layout.addWidget(self._slider)

        if tokens:
            self.apply_tokens(tokens)

    def _on_change(self, v: int):
        self._value_lbl.setText(f"{v}{self._suffix}")
        self.valueChanged.emit(v)

    def value(self) -> int:
        return self._slider.value()

    def setValue(self, v: int):
        self._slider.setValue(v)

    def apply_tokens(self, tokens: dict):
        txt   = tokens["text"]
        txt_m = tokens.get("text_muted", tokens["text"])
        acc   = tokens["accent"]
        panel = tokens["panel"]
        font  = tokens.get("font", "Segoe UI")

        self._label.setStyleSheet(
            f"color: {txt}; font-size: 12px; font-family: '{font}';"
            f"background: transparent;"
        )
        self._value_lbl.setStyleSheet(
            f"color: {acc}; font-size: 12px; font-weight: 700;"
            f"font-family: '{font}'; background: transparent;"
        )
        self._slider.setStyleSheet(f"""
            QSlider::groove:horizontal {{
                height: 5px;
                background: {panel};
                border-radius: 2px;
            }}
            QSlider::sub-page:horizontal {{
                background: {acc};
                border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                width: 16px; height: 16px;
                background: {acc};
                border-radius: 8px;
                margin: -6px 0;
                border: 2px solid {tokens['accent_dark']};
            }}
            QSlider::handle:horizontal:hover {{
                background: {tokens['accent_dark']};
            }}
        """)


# ──────────────────────────────────────────── themed checkbox ──────────────
class ThemedCheckBox(QCheckBox):
    def apply_tokens(self, tokens: dict):
        acc  = tokens["accent"]
        txt  = tokens["text"]
        font = tokens.get("font", "Segoe UI")
        r    = min(tokens["radius"], 5)
        self.setStyleSheet(f"""
            QCheckBox {{
                color: {txt};
                font-size: 13px;
                font-family: '{font}';
                spacing: 10px;
            }}
            QCheckBox::indicator {{
                width: 18px; height: 18px;
                border-radius: {r}px;
                border: 2px solid {tokens.get('outline_focus', acc)};
                background: {tokens['panel']};
            }}
            QCheckBox::indicator:checked {{
                background: {acc};
                border-color: {tokens['accent_dark']};
            }}
        """)


# ──────────────────────────────────────────── section header ──────────────
def _section_label(text: str, tokens: dict) -> QLabel:
    lbl = QLabel(text.upper())
    lbl.setStyleSheet(
        f"color: {tokens.get('text_muted', tokens['text'])};"
        f"font-size: 10px; font-weight: 700; letter-spacing: 1.5px;"
        f"font-family: '{tokens.get('font', 'Segoe UI')}';"
        f"background: transparent;"
    )
    return lbl


def _divider(tokens: dict) -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    col = tokens.get("text_muted", "#888888")
    line.setStyleSheet(f"color: {col}; background-color: {col}; border: none; max-height: 1px;")
    return line


# ══════════════════════════════════════════════════════ Settings Dialog ════
class SettingsDialog(QDialog):
    settings_saved = pyqtSignal()

    def __init__(self, settings: FoxSettings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self._conn_worker: _TestConnectionWorker | None = None
        self._foxy_worker: _FoxyBackendWorker | None = None

        self.setWindowTitle("Foxy — Settings")
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.FramelessWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumSize(460, 560)
        self.setMaximumSize(520, 680)

        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(0, 0, 0, 0)

        # Root card
        self._card = QFrame()
        self._card.setObjectName("settingsCard")
        self._outer.addWidget(self._card)

        self._card_layout = QVBoxLayout(self._card)
        self._card_layout.setContentsMargins(0, 0, 0, 0)
        self._card_layout.setSpacing(0)

        # Title bar
        self._title_bar = QWidget()
        self._title_bar.setObjectName("titleBar")
        self._title_bar.setFixedHeight(52)
        tb_layout = QHBoxLayout(self._title_bar)
        tb_layout.setContentsMargins(20, 0, 12, 0)
        self._title_lbl = GradientText("Foxy Settings")
        self._title_lbl.setObjectName("titleLbl")
        tb_layout.addWidget(self._title_lbl)
        tb_layout.addStretch()
        self._x_btn = _IconButton("close", self.reject, size=32)
        self._x_btn.setObjectName("xBtn")
        tb_layout.addWidget(self._x_btn)
        self._card_layout.addWidget(self._title_bar)

        # Tab bar
        self._tab_bar = PillTabBar(
            ["AI Brain", "Behaviour", "Foxy Audit"],
            _matte_tokens(),
        )
        tab_wrapper = QWidget()
        tab_wrapper.setObjectName("tabWrapper")
        tw_layout = QHBoxLayout(tab_wrapper)
        tw_layout.setContentsMargins(16, 10, 16, 10)
        tw_layout.addWidget(self._tab_bar)
        self._card_layout.addWidget(tab_wrapper)

        # Stacked pages
        self._stack = QStackedWidget()
        self._stack.setObjectName("pageStack")
        self._card_layout.addWidget(self._stack, stretch=1)

        # Build all tabs
        self._stack.addWidget(self._build_ai_tab())
        self._stack.addWidget(self._build_behaviour_tab())
        self._stack.addWidget(self._build_foxy_audit_tab())

        # Footer
        self._footer = QWidget()
        self._footer.setObjectName("footer")
        self._footer.setFixedHeight(60)
        footer_layout = QHBoxLayout(self._footer)
        footer_layout.setContentsMargins(20, 0, 20, 0)
        footer_layout.addStretch()

        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setObjectName("cancelBtn")
        self._cancel_btn.setFixedHeight(36)
        self._cancel_btn.setMinimumWidth(90)
        self._cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cancel_btn.clicked.connect(self.reject)
        footer_layout.addWidget(self._cancel_btn)

        self._save_btn = QPushButton("Save Changes")
        self._save_btn.setObjectName("saveBtn")
        self._save_btn.setFixedHeight(36)
        self._save_btn.setMinimumWidth(120)
        self._save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._save_btn.setDefault(True)
        self._save_btn.clicked.connect(self._save)
        footer_layout.addWidget(self._save_btn)
        self._card_layout.addWidget(self._footer)

        # Wire tab switching
        self._tab_bar.tab_changed.connect(self._stack.setCurrentIndex)

        # Initial full restyle
        self._apply_dialog_theme(_matte_tokens())

        # Drag-to-move (frameless dialog)
        self._drag_pos = None

    # ─────────────────────────────── drag-to-move (frameless) ─────
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if self._drag_pos and event.buttons() == Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None

    # ─────────────────────────────────── global QSS ───────────────
    def _apply_dialog_theme(self, tokens: dict):
        t       = tokens
        r       = t["radius"]
        acc     = t["accent"]
        acc_d   = t["accent_dark"]
        bg      = t["bg"]
        panel   = t["panel"]
        txt     = t["text"]
        txt_m   = t.get("text_muted", txt)
        font    = t.get("font", "Segoe UI")
        font_d  = t.get("font_disp", font)
        hdr_bg  = t.get("header_bg", bg)
        hdr_txt = t.get("header_text", txt)
        bdr     = t.get("border", "none")
        in_bdr  = t.get("input_border", bdr)
        focus   = t.get("outline_focus", acc)

        # Card shadow
        eff = QGraphicsDropShadowEffect(self._card)
        r2, g2, b2 = t.get("shadow_color", (30, 30, 60))
        eff.setBlurRadius(t.get("shadow_blur", 30))
        eff.setOffset(t.get("shadow_dx", 0), t.get("shadow_dy", 12))
        eff.setColor(QColor(r2, g2, b2, min(t.get("shadow_alpha", 100), 200)))
        self._card.setGraphicsEffect(eff)

        card_r = min(r, 18)

        self._card.setStyleSheet(f"""
            QFrame#settingsCard {{
                background-color: {bg};
                border-radius: {card_r}px;
                border: {bdr};
            }}
        """)
        self._title_bar.setStyleSheet(f"""
            QWidget#titleBar {{
                background-color: {hdr_bg};
                border-top-left-radius: {card_r}px;
                border-top-right-radius: {card_r}px;
            }}
        """)
        self._title_lbl.setFont(QFont(font_d, 15, QFont.Weight.Bold))
        self._title_lbl.set_gradient([(0.0, "#ffb474"), (0.5, "#ff7a2e"), (1.0, "#d65a16")])
        self._title_lbl.setStyleSheet("background: transparent; border: none;")
        self._x_btn.set_icon_color("#f4efe8")
        self._x_btn.setStyleSheet(f"""
            QPushButton#xBtn {{
                background: rgba(255,255,255,15);
                border-radius: {min(r,16)}px; border: none;
            }}
            QPushButton#xBtn:hover {{ background-color: {acc}; }}
        """)
        # Tab wrapper bg
        self._tab_bar.parentWidget().setStyleSheet(
            f"QWidget#tabWrapper {{ background-color: {bg}; }}"
        )
        self._tab_bar.apply_tokens(t)

        # Page stack background
        self._stack.setStyleSheet(
            f"QStackedWidget#pageStack {{ background-color: {bg}; border: none; }}"
        )

        # Footer
        self._footer.setStyleSheet(f"""
            QWidget#footer {{
                background-color: {bg};
                border-bottom-left-radius: {card_r}px;
                border-bottom-right-radius: {card_r}px;
                border-top: 1px solid {panel};
            }}
        """)
        self._cancel_btn.setStyleSheet(f"""
            QPushButton#cancelBtn {{
                background-color: {panel};
                color: {txt};
                border-radius: {min(r,18)}px;
                font-size: 13px; font-weight: 600;
                font-family: '{font}';
                border: 1px solid {panel};
                padding: 0 16px;
            }}
            QPushButton#cancelBtn:hover {{
                background-color: {acc_d}; color: #ffffff;
            }}
        """)
        self._save_btn.setStyleSheet(f"""
            QPushButton#saveBtn {{
                background-color: {acc};
                color: {'#000000' if not _is_dark(acc) else '#ffffff'};
                border-radius: {min(r,18)}px;
                font-size: 13px; font-weight: 700;
                font-family: '{font}';
                border: none;
                padding: 0 16px;
            }}
            QPushButton#saveBtn:hover {{ background-color: {acc_d}; }}
            QPushButton#saveBtn:pressed {{ background-color: {acc_d}; }}
        """)

        # Global QLineEdit + QComboBox inside dialog
        self.setStyleSheet(f"""
            QLineEdit {{
                background-color: {panel};
                color: {txt};
                border: {in_bdr};
                border-radius: {min(r,12)}px;
                padding: 9px 12px;
                font-size: 13px;
                font-family: '{font}';
                selection-background-color: {acc};
            }}
            QLineEdit:focus {{
                border: 2px solid {focus};
            }}
            QLineEdit:disabled {{
                color: {txt_m}; background-color: {bg};
            }}
            QComboBox {{
                background-color: {panel};
                color: {txt};
                border: {in_bdr};
                border-radius: {min(r,10)}px;
                padding: 8px 12px;
                font-size: 13px;
                font-family: '{font}';
            }}
            QComboBox::drop-down {{
                border: none; width: 24px;
            }}
            QComboBox::down-arrow {{
                width: 10px; height: 10px;
            }}
            QComboBox QAbstractItemView {{
                background-color: {panel};
                color: {txt};
                selection-background-color: {acc};
                border: 1px solid {acc};
                outline: none;
            }}
            QLabel {{
                background: transparent;
                color: {txt};
                font-family: '{font}';
                font-size: 13px;
            }}
            QScrollArea {{
                background: transparent;
                border: none;
            }}
            QScrollBar:vertical {{
                width: 5px; background: transparent;
            }}
            QScrollBar::handle:vertical {{
                background: {acc}; border-radius: 2px; min-height: 20px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0;
            }}
        """)

        # Update sliders + checkboxes on behaviour tab
        for child in self.findChildren(LabelledSlider):
            child.apply_tokens(t)
        for child in self.findChildren(ThemedCheckBox):
            child.apply_tokens(t)

    # ─────────────────────────────── AI Brain tab ─────────────────
    def _build_ai_tab(self) -> QWidget:
        tokens = _matte_tokens()
        page   = QWidget()
        page.setObjectName("aiPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(14)

        layout.addWidget(_section_label("AI Provider", tokens))
        layout.addWidget(_divider(tokens))

        info = QLabel(
            "Use a cloud API (Anthropic, OpenAI, or compatible), "
            "or point Foxy at a local model via Ollama / LM Studio — "
            "no key needed for local models."
        )
        info.setWordWrap(True)
        info.setStyleSheet(
            f"color: {tokens.get('text_muted', tokens['text'])};"
            f"font-size: 12px; font-family: '{tokens.get('font','Segoe UI')}';"
        )
        layout.addWidget(info)

        # Provider combo
        self._provider_combo = QComboBox()
        for p in AI_PROVIDERS:
            self._provider_combo.addItem(p.capitalize(), p)
        idx = AI_PROVIDERS.index(self.settings.ai_provider()) \
              if self.settings.ai_provider() in AI_PROVIDERS else 0
        self._provider_combo.setCurrentIndex(idx)
        self._provider_combo.currentIndexChanged.connect(self._load_provider_fields)
        layout.addWidget(self._make_field_row("Provider", self._provider_combo))

        # API key
        self._key_field = QLineEdit()
        self._key_field.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_field.setPlaceholderText("Leave blank for local models")
        layout.addWidget(self._make_field_row("API Key", self._key_field))

        # Model
        self._model_field = QLineEdit()
        self._model_field.setPlaceholderText("e.g. claude-sonnet-4-6")
        layout.addWidget(self._make_field_row("Model", self._model_field))

        # Endpoint URL
        self._url_field = QLineEdit()
        self._url_field.setPlaceholderText("https://…")
        layout.addWidget(self._make_field_row("Endpoint URL", self._url_field))

        # Test button + status label
        test_row = QHBoxLayout()
        self._test_btn = QPushButton("Test Connection")
        self._test_btn.setObjectName("testBtn")
        self._test_btn.setFixedHeight(36)
        self._test_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._test_btn.clicked.connect(self._test_connection)
        test_row.addWidget(self._test_btn)
        self._test_status = QLabel("")
        self._test_status.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        test_row.addWidget(self._test_status, stretch=1)
        layout.addLayout(test_row)

        self._style_test_btn(tokens)
        layout.addStretch()
        self._load_provider_fields()
        return page

    def _style_test_btn(self, tokens: dict):
        acc  = tokens["accent"]
        acc_d = tokens["accent_dark"]
        r    = min(tokens["radius"], 18)
        font = tokens.get("font", "Segoe UI")
        self._test_btn.setStyleSheet(f"""
            QPushButton#testBtn {{
                background-color: {tokens['panel']};
                color: {tokens['text']};
                border: 1px solid {acc};
                border-radius: {r}px;
                font-size: 13px; font-weight: 600;
                font-family: '{font}';
                padding: 0 16px;
            }}
            QPushButton#testBtn:hover {{ background-color: {acc}; color: #ffffff; border: none; }}
            QPushButton#testBtn:disabled {{ color: {tokens.get('text_muted', '#888')}; border-color: {tokens['panel']}; }}
        """)

    @staticmethod
    def _make_field_row(label_text: str, widget: QWidget) -> QWidget:
        """Returns a two-column row: label left, widget right."""
        row = QWidget()
        row.setStyleSheet("background: transparent;")
        hl  = QHBoxLayout(row)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(12)
        lbl = QLabel(label_text)
        lbl.setFixedWidth(124)
        lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        hl.addWidget(lbl)
        hl.addWidget(widget, stretch=1)
        return row

    def _current_provider(self) -> str:
        return self._provider_combo.currentData()

    def _load_provider_fields(self):
        provider = self._current_provider()
        self._key_field.setText(self.settings.api_key(provider))
        self._model_field.setText(self.settings.model(provider))
        self._url_field.setText(self.settings.base_url(provider))
        is_local = self.settings.is_local_provider(provider)
        self._key_field.setEnabled(not is_local)
        self._key_field.setPlaceholderText(
            "Not required for local models" if is_local else "sk-… / sk-ant-…"
        )

    def _test_connection(self):
        provider = self._current_provider()
        self.settings.set_ai_provider(provider)
        self.settings.set_api_key(provider, self._key_field.text())
        self.settings.set_model(provider, self._model_field.text())
        self.settings.set_base_url(provider, self._url_field.text())

        self._test_btn.setEnabled(False)
        self._test_btn.setText("Testing…")
        tokens = _matte_tokens()
        self._test_status.setStyleSheet(
            f"color: {tokens.get('text_muted', '#888')}; font-size: 12px;"
        )
        self._test_status.setText("Connecting…")

        if self._conn_worker and self._conn_worker.isRunning():
            return

        self._conn_worker = _TestConnectionWorker(self.settings, self)
        self._conn_worker.succeeded.connect(self._on_test_ok)
        self._conn_worker.failed.connect(self._on_test_fail)
        self._conn_worker.finished.connect(self._reset_test_btn)
        self._conn_worker.start()

    def _reset_test_btn(self):
        self._test_btn.setEnabled(True)
        self._test_btn.setText("Test Connection")

    def _on_test_ok(self, reply: str):
        tokens = _matte_tokens()
        self._test_status.setStyleSheet(
            f"color: {tokens['accent']}; font-size: 12px; font-weight: 600;"
        )
        self._test_status.setText(f"✓ Connected — '{reply[:40]}'")

    def _on_test_fail(self, err: str):
        self._test_status.setStyleSheet("color: #FF4C4C; font-size: 12px; font-weight: 600;")
        self._test_status.setText(f"✗ {err[:60]}")

    # ─────────────────────────────── Behaviour tab ────────────────
    def _build_behaviour_tab(self) -> QWidget:
        tokens = _matte_tokens()
        page   = QWidget()
        page.setObjectName("behaviourPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 16, 20, 20)
        layout.setSpacing(18)

        layout.addWidget(_section_label("Reaction controls", tokens))
        layout.addWidget(_divider(tokens))

        self._cooldown_slider = LabelledSlider(
            "Reaction cooldown",
            minimum=0, maximum=50,
            value=int(self.settings.reaction_cooldown() * 10),
            suffix="×0.1s", tokens=tokens,
        )
        layout.addWidget(self._cooldown_slider)

        self._pat_slider = LabelledSlider(
            "Pat sensitivity",
            minimum=5, maximum=60,
            value=self.settings.pat_sensitivity(),
            suffix="px", tokens=tokens,
        )
        layout.addWidget(self._pat_slider)

        layout.addSpacing(4)
        layout.addWidget(_section_label("Autonomous behaviour", tokens))
        layout.addWidget(_divider(tokens))

        self._roam_check = ThemedCheckBox("Wander the desktop when idle")
        self._roam_check.setChecked(self.settings.roaming_enabled())
        self._roam_check.apply_tokens(tokens)
        layout.addWidget(self._roam_check)

        self._glance_check = ThemedCheckBox("Glance over when the cursor gets close")
        self._glance_check.setChecked(self.settings.proximity_glance_enabled())
        self._glance_check.apply_tokens(tokens)
        layout.addWidget(self._glance_check)

        layout.addSpacing(4)
        layout.addWidget(_section_label("Platform status", tokens))
        layout.addWidget(_divider(tokens))

        tracking_ok = window_tracker.supports_window_tracking()
        status_icon = "✓" if tracking_ok else "✗"
        status_msg  = "Window tracking available" if tracking_ok \
                      else "Needs extra package on this OS (see README)"
        status_lbl  = QLabel(f"{status_icon}  {window_tracker.PLATFORM} — {status_msg}")
        col = tokens["accent"] if tracking_ok else "#FF6B6B"
        status_lbl.setStyleSheet(
            f"color: {col}; font-size: 12px;"
            f"font-family: '{tokens.get('font','Segoe UI')}';"
        )
        layout.addWidget(status_lbl)

        layout.addStretch()
        return page

    # ─────────────────────────────── Foxy Audit tab ───────────────
    def _build_foxy_audit_tab(self) -> QWidget:
        tokens = _matte_tokens()
        page   = QWidget()
        page.setObjectName("foxyAuditPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 16, 20, 20)
        layout.setSpacing(18)

        layout.addWidget(_section_label("Organization Settings", tokens))
        layout.addWidget(_divider(tokens))

        info = QLabel(
            "Foxy Audit telemetry requires an active organization API key. "
            "Get this from your Foxy Audit dashboard."
        )
        info.setWordWrap(True)
        info.setStyleSheet(
            f"color: {tokens.get('text_muted', tokens['text'])};"
            f"font-size: 12px; font-family: '{tokens.get('font','Segoe UI')}';"
        )
        layout.addWidget(info)

        # API key
        self._org_key_field = QLineEdit()
        self._org_key_field.setEchoMode(QLineEdit.EchoMode.Password)
        self._org_key_field.setText(self.settings.org_api_key())
        self._org_key_field.setPlaceholderText("org_key_...")
        layout.addWidget(self._make_field_row("Org API Key", self._org_key_field))

        # Backend URL
        self._backend_url_field = QLineEdit()
        self._backend_url_field.setText(self.settings.backend_url())
        self._backend_url_field.setPlaceholderText("https://api.foxyaudit.dev")
        layout.addWidget(self._make_field_row("Backend URL", self._backend_url_field))

        # Test button + status label
        test_row = QHBoxLayout()
        self._foxy_test_btn = QPushButton("Test Backend")
        self._foxy_test_btn.setObjectName("foxyTestBtn")
        self._foxy_test_btn.setFixedHeight(36)
        self._foxy_test_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._foxy_test_btn.clicked.connect(self._test_foxy_backend)
        test_row.addWidget(self._foxy_test_btn)
        
        self._foxy_test_status = QLabel("")
        self._foxy_test_status.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        test_row.addWidget(self._foxy_test_status, stretch=1)
        layout.addLayout(test_row)

        # Reuse the test button styling logic
        acc  = tokens["accent"]
        acc_d = tokens["accent_dark"]
        r    = min(tokens["radius"], 18)
        font = tokens.get("font", "Segoe UI")
        self._foxy_test_btn.setStyleSheet(f"""
            QPushButton#foxyTestBtn {{
                background-color: {tokens['panel']};
                color: {tokens['text']};
                border: 1px solid {acc};
                border-radius: {r}px;
                font-size: 13px; font-weight: 600;
                font-family: '{font}';
                padding: 0 16px;
            }}
            QPushButton#foxyTestBtn:hover {{ background-color: {acc}; color: #ffffff; border: none; }}
            QPushButton#foxyTestBtn:disabled {{ color: {tokens.get('text_muted', '#888')}; border-color: {tokens['panel']}; }}
        """)

        layout.addStretch()
        return page

    def _test_foxy_backend(self):
        org_key = self._org_key_field.text().strip()
        url = self._backend_url_field.text().strip()
        
        self.settings.set_org_api_key(org_key)
        self.settings.set_backend_url(url)

        self._foxy_test_btn.setEnabled(False)
        self._foxy_test_btn.setText("Testing…")
        tokens = _matte_tokens()
        self._foxy_test_status.setStyleSheet(
            f"color: {tokens.get('text_muted', '#888')}; font-size: 12px;"
        )
        self._foxy_test_status.setText("Connecting…")

        if self._foxy_worker and self._foxy_worker.isRunning():
            return

        self._foxy_worker = _FoxyBackendWorker(url, org_key, self)
        self._foxy_worker.succeeded.connect(self._on_foxy_ok)
        self._foxy_worker.failed.connect(self._on_foxy_fail)
        self._foxy_worker.finished.connect(self._reset_foxy_btn)
        self._foxy_worker.start()

    def _reset_foxy_btn(self):
        self._foxy_test_btn.setEnabled(True)
        self._foxy_test_btn.setText("Test Backend")

    def _on_foxy_ok(self):
        tokens = _matte_tokens()
        self._foxy_test_status.setStyleSheet(
            f"color: {tokens['accent']}; font-size: 12px; font-weight: 600;"
        )
        self._foxy_test_status.setText("✓ Organization active")

    def _on_foxy_fail(self, err: str):
        self._foxy_test_status.setStyleSheet("color: #FF4C4C; font-size: 12px; font-weight: 600;")
        self._foxy_test_status.setText(f"✗ {err[:60]}")

    # ─────────────────────────────────── Save ─────────────────────
    def _save(self):
        provider = self._current_provider()
        self.settings.set_ai_provider(provider)
        self.settings.set_api_key(provider, self._key_field.text())
        self.settings.set_model(provider, self._model_field.text())
        self.settings.set_base_url(provider, self._url_field.text())

        self.settings.set_reaction_cooldown(self._cooldown_slider.value() / 10.0)
        self.settings.set_pat_sensitivity(self._pat_slider.value())
        self.settings.set_roaming_enabled(self._roam_check.isChecked())
        self.settings.set_proximity_glance_enabled(self._glance_check.isChecked())

        self.settings.set_org_api_key(self._org_key_field.text().strip())
        self.settings.set_backend_url(self._backend_url_field.text().strip())

        self.settings_saved.emit()
        self.accept()

    def closeEvent(self, event):
        if self._conn_worker and self._conn_worker.isRunning():
            self._conn_worker.quit()
            self._conn_worker.wait(600)
        if self._foxy_worker and self._foxy_worker.isRunning():
            self._foxy_worker.quit()
            self._foxy_worker.wait(600)
        super().closeEvent(event)


# ────────────────────────────────────────────────── standalone preview ─────
if __name__ == "__main__":
    app = QApplication(sys.argv)
    s   = FoxSettings()
    dlg = SettingsDialog(s)
    dlg.show()
    sys.exit(app.exec())
