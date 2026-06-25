"""
OmniAware Fox — Settings Dialog
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
A premium, token-driven settings UI. Three tabs:
  • Appearance  — 14 animated theme swatches with live preview
  • AI Brain    — provider/key/model/URL with inline connection test
  • Behaviour   — cooldown, pat sensitivity, roaming, glance toggles

Design rules
────────────
• Zero plain Qt defaults — every widget is fully styled via QSS that reads
  directly from the active theme tokens.
• ThemeSwatch cards show bg + accent + text exactly as they will look in
  the chat popup — the user picks by sight, not by label.
• Tab bar uses a custom pill-style selector so it never looks like a
  default QTabWidget.
• Sliders use a custom groove + handle that matches the accent colour.
• All QThread workers (connection test) are properly cleaned up via
  finished.connect(_cleanup) so there are no ghost threads on repeated
  test clicks.
• The dialog re-skins itself whenever the user selects a new theme so
  the settings UI itself previews the choice.
"""

from __future__ import annotations

import sys
from PyQt6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QHBoxLayout, QWidget,
    QLabel, QLineEdit, QPushButton, QSlider, QCheckBox,
    QComboBox, QScrollArea, QFrame, QGraphicsDropShadowEffect,
    QMessageBox, QSizePolicy, QStackedWidget,
)
from PyQt6.QtCore import Qt, pyqtSignal, QThread, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QColor, QFont

from fox_settings import FoxSettings, THEMES, AI_PROVIDERS
import window_tracker
import ai_providers


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


# ──────────────────────────────────────────── theme swatch card widget ─────
class ThemeSwatchCard(QFrame):
    """
    A richly styled clickable card that previews one theme.

    Layout:
      ┌─────────────────────────────────────┐
      │  ██  Label name              accent ▶│
      │  mini bubble preview                 │
      └─────────────────────────────────────┘
    """
    clicked = pyqtSignal(str)   # emits theme key

    _SELECTED_BORDER_WIDTH = 3

    def __init__(self, key: str, tokens: dict, selected: bool = False,
                 parent=None):
        super().__init__(parent)
        self.key      = key
        self._tokens  = tokens
        self._selected = selected
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(64)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._build(tokens)
        self._apply_style(selected)

    def _build(self, tokens: dict):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 8, 14, 8)
        layout.setSpacing(12)

        # Colour dot
        dot = QLabel()
        dot.setFixedSize(22, 22)
        dot.setStyleSheet(f"""
            background-color: {tokens['accent']};
            border-radius: 11px;
            border: 2px solid {tokens.get('accent_dark', tokens['accent'])};
        """)
        layout.addWidget(dot)

        # Theme name
        name = QLabel(tokens["label"])
        name.setStyleSheet(f"""
            font-size: 13px; font-weight: 600;
            color: {tokens['text']};
            font-family: '{tokens.get('font', 'Segoe UI')}';
            background: transparent;
        """)
        layout.addWidget(name, stretch=1)

        # Mini accent badge
        badge = QLabel("Aa")
        badge.setFixedSize(36, 22)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setStyleSheet(f"""
            background-color: {tokens['accent']};
            color: {'#000000' if not _is_dark(tokens['accent']) else '#FFFFFF'};
            border-radius: {min(tokens['radius'], 11)}px;
            font-size: 10px; font-weight: 700;
        """)
        layout.addWidget(badge)

    def _apply_style(self, selected: bool):
        t   = self._tokens
        r   = min(t["radius"], 12)
        bw  = self._SELECTED_BORDER_WIDTH if selected else 1
        bc  = t["accent_dark"] if selected else t.get("border", t["accent"])
        # strip the "Npx solid " prefix if border token is already a full string
        if isinstance(bc, str) and "solid" in bc:
            bc = t["accent"]

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(14 if selected else 6)
        shadow.setOffset(0, 4 if selected else 2)
        r2, g2, b2 = t.get("shadow_color", (60, 60, 80))
        shadow.setColor(QColor(r2, g2, b2, 120 if selected else 50))
        self.setGraphicsEffect(shadow)

        self.setStyleSheet(f"""
            QFrame {{
                background-color: {t['bg']};
                border-radius: {r}px;
                border: {bw}px solid {bc};
            }}
        """)

    def set_selected(self, selected: bool):
        self._selected = selected
        self._apply_style(selected)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.key)
        super().mousePressEvent(event)


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
    theme_changed  = pyqtSignal(str)
    settings_saved = pyqtSignal()

    def __init__(self, settings: FoxSettings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self._conn_worker: _TestConnectionWorker | None = None

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
        self._title_lbl = QLabel("⚙  Foxy Settings")
        self._title_lbl.setObjectName("titleLbl")
        tb_layout.addWidget(self._title_lbl)
        tb_layout.addStretch()
        self._x_btn = QPushButton("×")
        self._x_btn.setObjectName("xBtn")
        self._x_btn.setFixedSize(32, 32)
        self._x_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._x_btn.clicked.connect(self.reject)
        tb_layout.addWidget(self._x_btn)
        self._card_layout.addWidget(self._title_bar)

        # Tab bar
        self._tab_bar = PillTabBar(
            ["🎨  Appearance", "🤖  AI Brain", "🦊  Behaviour"],
            self.settings.theme_tokens(),
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
        self._swatch_cards: list[ThemeSwatchCard] = []
        self._stack.addWidget(self._build_appearance_tab())
        self._stack.addWidget(self._build_ai_tab())
        self._stack.addWidget(self._build_behaviour_tab())

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
        self._apply_dialog_theme(self.settings.theme_tokens())

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
        self._title_lbl.setStyleSheet(f"""
            font-size: 15px; font-weight: 700;
            color: {hdr_txt};
            font-family: '{font}';
            background: transparent;
        """)
        self._x_btn.setStyleSheet(f"""
            QPushButton#xBtn {{
                background: transparent; color: {hdr_txt};
                border-radius: {min(r,16)}px; font-size: 18px; font-weight: bold;
                border: none;
            }}
            QPushButton#xBtn:hover {{ background-color: {acc}; color: #ffffff; }}
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

        # Update swatches selection ring colour
        for card in self._swatch_cards:
            card.set_selected(card.key == self.settings.theme())

    # ─────────────────────────────── Appearance tab ───────────────
    def _build_appearance_tab(self) -> QWidget:
        page = QWidget()
        page.setObjectName("appearancePage")
        outer = QVBoxLayout(page)
        outer.setContentsMargins(16, 8, 16, 16)
        outer.setSpacing(12)

        tokens = self.settings.theme_tokens()
        outer.addWidget(_section_label("Select a theme", tokens))
        outer.addWidget(_divider(tokens))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        inner = QWidget()
        grid  = QVBoxLayout(inner)
        grid.setContentsMargins(4, 8, 4, 8)
        grid.setSpacing(10)

        current = self.settings.theme()
        for key, tk in THEMES.items():
            card = ThemeSwatchCard(key, tk, selected=(key == current))
            card.clicked.connect(self._select_theme)
            self._swatch_cards.append(card)
            grid.addWidget(card)

        grid.addStretch()
        scroll.setWidget(inner)
        outer.addWidget(scroll, stretch=1)
        return page

    def _select_theme(self, key: str):
        self.settings.set_theme(key)
        tokens = self.settings.theme_tokens()
        for card in self._swatch_cards:
            card.set_selected(card.key == key)
        self._apply_dialog_theme(tokens)
        self.theme_changed.emit(key)

    # ─────────────────────────────── AI Brain tab ─────────────────
    def _build_ai_tab(self) -> QWidget:
        tokens = self.settings.theme_tokens()
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
        self._test_btn = QPushButton("⚡  Test Connection")
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
        lbl.setFixedWidth(100)
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
        tokens = self.settings.theme_tokens()
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
        self._test_btn.setText("⚡  Test Connection")

    def _on_test_ok(self, reply: str):
        tokens = self.settings.theme_tokens()
        self._test_status.setStyleSheet(
            f"color: {tokens['accent']}; font-size: 12px; font-weight: 600;"
        )
        self._test_status.setText(f"✓ Connected — '{reply[:40]}'")

    def _on_test_fail(self, err: str):
        self._test_status.setStyleSheet("color: #FF4C4C; font-size: 12px; font-weight: 600;")
        self._test_status.setText(f"✗ {err[:60]}")

    # ─────────────────────────────── Behaviour tab ────────────────
    def _build_behaviour_tab(self) -> QWidget:
        tokens = self.settings.theme_tokens()
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

        self.settings_saved.emit()
        self.accept()

    def closeEvent(self, event):
        if self._conn_worker and self._conn_worker.isRunning():
            self._conn_worker.quit()
            self._conn_worker.wait(600)
        super().closeEvent(event)


# ────────────────────────────────────────────────── standalone preview ─────
if __name__ == "__main__":
    app = QApplication(sys.argv)
    s   = FoxSettings()
    dlg = SettingsDialog(s)
    dlg.show()
    sys.exit(app.exec())
