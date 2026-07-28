"""
OmniAware Fox — Settings Dialog
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
A premium settings UI in the app's fixed matte skin. Four tabs:
  • AI Brain    — provider/key/model/URL with inline connection test
  • Companion   — startup, the fox's size/opacity/behaviour, where it sits
  • Alerts      — what interrupts you, how, quiet hours, poll cadence
  • Foxy Audit  — org API key + backend URL with a live health probe

D13 folded the old Behaviour tab into Companion rather than adding a fifth:
§9.2 lists reaction cooldown and pat sensitivity under Fox, and keeping both
tabs would have shipped two separate switches for roaming and for glance —
two controls for one setting, guaranteed to disagree.

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

import sys
from PyQt6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QHBoxLayout, QWidget,
    QLabel, QLineEdit, QPushButton, QSlider, QCheckBox,
    QComboBox, QScrollArea, QFrame, QGraphicsDropShadowEffect,
    QSizePolicy, QStackedWidget, QTimeEdit,
)
from PyQt6.QtCore import Qt, pyqtSignal, QThread, QTime
from PyQt6.QtGui import QColor, QFont

import autostart as autostart_mod
import companion_prefs as cp
from autostart import Autostart
from fox_settings import FoxSettings, AI_PROVIDERS
from foxy_client import FoxyClient, shutdown_workers, spawn_worker
from foxy_tokens import matte_tokens as _matte_tokens
from clay_chat_popup import GradientText, _IconButton
import window_tracker
import ai_providers

# Ink for text sitting ON the accent — the tab pill, Save, and the two hover
# states. This used to be picked by `is_dark(acc, 140)`, a crude luminance test
# that called #c96a2f "dark" and chose white: 3.76:1, under AA. The picker was
# dead flexibility anyway (matte_tokens is a fixed skin, no theme switching), so
# it is gone and the web's own .btn.pri ink stands in its place — 5.16:1, the
# same answer f9b3cb3 gave #ctaBtn/#verifyBtn.
_ACCENT_INK = "#1a0900"


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
                    color: {_ACCENT_INK};
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
            /* Reachable by keyboard, so the box itself has to show it — the
               label recolouring alone was invisible to anyone tabbing. */
            QCheckBox::indicator:focus {{
                border: 2px solid {acc};
            }}
            /* Disabled must not look available. This lives HERE and not in
               the dialog-wide QSS because a per-widget stylesheet wins. */
            QCheckBox:disabled {{
                color: {tokens.get('text_muted', txt)};
            }}
            QCheckBox::indicator:disabled {{
                border-color: {tokens['panel']};
                background: {tokens.get('bg', tokens['panel'])};
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

    def __init__(self, settings: FoxSettings, parent=None,
                 client: FoxyClient | None = None,
                 autostart: Autostart | None = None):
        super().__init__(parent)
        self.settings = settings
        self.client = client or FoxyClient(settings, parent=self)
        # Injected in tests. Without this seam, ticking the autostart box in a
        # test would add a real login item to the developer's machine — the
        # exact class of accident the D3 round found in the shell tests, and a
        # worse one, because this store is outside the app entirely.
        self._autostart = autostart if autostart is not None else Autostart()
        self._conn_worker: _TestConnectionWorker | None = None
        self._workers: set = set()

        self.setWindowTitle("Foxy — Settings")
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.FramelessWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumSize(460, 560)
        self.setMaximumSize(520, 680)
        # Open at the full allowed height. D13's two tabs carry ~13 controls
        # each; at the 560px minimum most of Companion sits below the fold on
        # first open, and a settings screen you have to scroll before you can
        # see what is in it reads as unfinished.
        self.resize(500, 680)

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
            ["AI Brain", "Companion", "Alerts", "Foxy Audit"],
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
        self._stack.addWidget(self._build_companion_tab())
        self._stack.addWidget(self._build_alerts_tab())
        self._stack.addWidget(self._build_foxy_audit_tab())
        #: The Foxy Audit tab moved from index 2 to 4 when D13 inserted two
        #: tabs before it. `_show_secret_error` surfaces that tab by index.
        self._foxy_tab_index = self._stack.count() - 1

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
                color: {_ACCENT_INK};
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
            /* D13: the two companion tabs scroll, and their body must not
               paint an opaque default over the card. */
            QWidget#scrollBody {{ background: transparent; }}
            /* Time fields for quiet hours — QTimeEdit inherits none of the
               QLineEdit rule above, so without this it renders as a bare
               native spinbox inside a fully themed dialog. */
            QTimeEdit {{
                background-color: {panel};
                color: {txt};
                border: {in_bdr};
                border-radius: {min(r, 10)}px;
                padding: 6px 10px;
                font-size: 13px;
                font-family: '{font}';
            }}
            QTimeEdit:focus {{ border: 1px solid {acc}; }}
            /* Explicit, because the `color:` above beats Qt's disabled
               palette — without it the quiet-hours fields looked fully live
               while the toggle above them was off. */
            QTimeEdit:disabled {{
                color: {txt_m}; border: 1px solid {panel};
            }}
            QLabel:disabled {{ color: {txt_m}; }}
            /* No spin buttons. Styling them left two solid black rectangles
               beside the fields — the default arrows disappear the moment the
               sub-control is themed, and there is nothing to put back that
               reads at 16px. Typing and the scroll wheel both still work,
               which is how anyone actually sets a time. */
            QTimeEdit::up-button, QTimeEdit::down-button {{
                width: 0; height: 0; border: none;
            }}
            QPushButton#resetPosBtn {{
                background-color: {panel};
                color: {txt};
                border: 1px solid {t.get('outline_focus', acc)};
                border-radius: {min(r, 10)}px;
                font-size: 12px; font-weight: 600;
                font-family: '{font}';
            }}
            QPushButton#resetPosBtn:hover {{ border-color: {acc}; }}
            QPushButton#resetPosBtn:focus {{
                border-color: {acc}; background-color: {bg};
            }}
            QPushButton#resetPosBtn:disabled {{
                color: {txt_m}; border-color: {panel};
            }}
            /* Explanatory text under a control: quieter than a label, but it
               still has to clear the contrast floor against the card. */
            QLabel#alertsNote, QLabel#autostartNote {{
                color: {txt_m}; font-size: 11px;
            }}
            /* A disabled checkbox must not read as an available one — the
               autostart box is disabled outright on an unsupported OS. */
            QCheckBox:disabled {{ color: {txt_m}; }}
        """)

        # Sliders and checkboxes carry per-widget stylesheets, which beat the
        # dialog-wide QSS above — they have to be re-applied, not inherited.
        for child in self.findChildren(LabelledSlider):
            child.apply_tokens(t)
        for child in self.findChildren(ThemedCheckBox):
            child.apply_tokens(t)
        if hasattr(self, "_autostart_note"):
            self._restyle_autostart_note()

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
            QPushButton#testBtn:hover {{ background-color: {acc}; color: {_ACCENT_INK}; border: none; }}
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
        if self._conn_worker and self._conn_worker.isRunning():
            return  # guard BEFORE touching the button, or it sticks on "Testing…"
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

    # ─────────────────────────────── Companion tab (D13 · §9.2) ───
    @staticmethod
    def _scroll_page(name: str) -> tuple[QWidget, QVBoxLayout]:
        """A tab that can outgrow the dialog.

        The two D13 tabs carry ~13 controls each and the dialog is capped at
        680px tall, so without this the bottom third is simply unreachable —
        the existing three tabs are short enough that nobody had needed it.
        """
        page = QWidget()
        page.setObjectName(name)
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("scrollBody")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(20, 16, 20, 20)
        layout.setSpacing(14)
        scroll.setWidget(body)
        outer.addWidget(scroll)
        return page, layout

    def _check(self, label: str, checked: bool, tokens: dict) -> ThemedCheckBox:
        box = ThemedCheckBox(label)
        box.setChecked(bool(checked))
        box.setMinimumHeight(28)
        box.apply_tokens(tokens)
        return box

    def _combo(self, options, current: str, tokens: dict) -> QComboBox:
        combo = QComboBox()
        combo.setFixedHeight(34)
        combo.setCursor(Qt.CursorShape.PointingHandCursor)
        for value, label in options:
            combo.addItem(label, value)
        index = combo.findData(current)
        if index >= 0:
            combo.setCurrentIndex(index)
        return combo

    def _build_companion_tab(self) -> QWidget:
        tokens = _matte_tokens()
        page, layout = self._scroll_page("companionPage")
        s = self.settings

        layout.addWidget(_section_label("Startup", tokens))
        layout.addWidget(_divider(tokens))

        # The one control on this page whose state is NOT ours: it lives in
        # the OS, so it is read fresh here and therefore re-read every time
        # the dialog opens (plan §9.2's "state drift").
        self._autostart_check = self._check(
            "Start Foxy when the PC starts",
            self._autostart.is_enabled(), tokens)
        self._autostart_check.setEnabled(self._autostart.supported)
        self._autostart_check.toggled.connect(self._on_autostart_toggled)
        layout.addWidget(self._autostart_check)

        self._autostart_note = QLabel(
            self._autostart.location if self._autostart.supported
            else autostart_mod.UNSUPPORTED)
        self._autostart_note.setWordWrap(True)
        self._autostart_note.setObjectName("autostartNote")
        layout.addWidget(self._autostart_note)

        self._start_hidden_check = self._check(
            "Start hidden in the tray", s.start_hidden(), tokens)
        layout.addWidget(self._start_hidden_check)
        self._open_console_check = self._check(
            "Open the console on launch", s.open_console_on_launch(), tokens)
        layout.addWidget(self._open_console_check)
        self._close_tray_check = self._check(
            "Closing the console hides it instead of quitting",
            s.close_to_tray(), tokens)
        layout.addWidget(self._close_tray_check)

        layout.addSpacing(2)
        layout.addWidget(_section_label("The fox", tokens))
        layout.addWidget(_divider(tokens))

        self._scale_slider = LabelledSlider(
            "Size", cp.SCALE_MIN, cp.SCALE_MAX, s.fox_scale(), "%", tokens)
        layout.addWidget(self._scale_slider)
        self._opacity_slider = LabelledSlider(
            "Opacity", cp.OPACITY_MIN, cp.OPACITY_MAX, s.fox_opacity(), "%",
            tokens)
        layout.addWidget(self._opacity_slider)

        self._on_top_check = self._check(
            "Always on top of other windows", s.always_on_top(), tokens)
        layout.addWidget(self._on_top_check)
        self._roam_check = self._check(
            "Wander along the bottom of the screen", s.roaming_enabled(),
            tokens)
        layout.addWidget(self._roam_check)
        self._roam_speed_slider = LabelledSlider(
            "Wander speed", 1, 6, s.roam_speed(), " px/tick", tokens)
        layout.addWidget(self._roam_speed_slider)
        self._glance_check = self._check(
            "Glance at the cursor when it comes close",
            s.proximity_glance_enabled(), tokens)
        layout.addWidget(self._glance_check)

        # From the old Behaviour tab. §9.2 lists pat sensitivity and reaction
        # cooldown under Fox, and keeping a second tab with its own roaming
        # and glance switches would have shipped two controls for one setting.
        self._cooldown_slider = LabelledSlider(
            "Reaction cooldown", 0, 50,
            int(s.reaction_cooldown() * 10), "×0.1s", tokens)
        layout.addWidget(self._cooldown_slider)
        self._pat_slider = LabelledSlider(
            "Pat sensitivity", 5, 60, s.pat_sensitivity(), "px", tokens)
        layout.addWidget(self._pat_slider)

        self._idle_combo = self._combo(
            [(k, l) for k, l, _r in cp.FREQUENCIES],
            s.idle_break_frequency(), tokens)
        layout.addWidget(self._make_field_row("Idle poses", self._idle_combo))
        self._tip_combo = self._combo(
            [(k, l) for k, l, _r in cp.FREQUENCIES], s.tip_frequency(), tokens)
        layout.addWidget(self._make_field_row("Tip bubbles", self._tip_combo))
        self._click_combo = self._combo(cp.CLICK_ACTIONS, s.click_action(),
                                        tokens)
        layout.addWidget(self._make_field_row("Click the fox",
                                              self._click_combo))

        self._input_react_check = self._check(
            "React to typing and scrolling", s.input_reactions_enabled(),
            tokens)
        layout.addWidget(self._input_react_check)
        self._hw_react_check = self._check(
            "React to CPU, memory and battery", s.hardware_reactions_enabled(),
            tokens)
        layout.addWidget(self._hw_react_check)

        layout.addSpacing(2)
        layout.addWidget(_section_label("Where it sits", tokens))
        layout.addWidget(_divider(tokens))

        self._remember_pos_check = self._check(
            "Remember where I put it", s.remember_position(), tokens)
        layout.addWidget(self._remember_pos_check)

        self._monitor_combo = self._combo(self._monitor_options(),
                                          str(s.monitor_index()), tokens)
        layout.addWidget(self._make_field_row("Monitor", self._monitor_combo))

        self._reset_pos_btn = QPushButton("Reset the fox's position")
        self._reset_pos_btn.setObjectName("resetPosBtn")
        self._reset_pos_btn.setFixedHeight(34)
        self._reset_pos_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._reset_pos_btn.clicked.connect(self._reset_fox_position)
        layout.addWidget(self._reset_pos_btn)

        tracking_ok = window_tracker.supports_window_tracking()
        status_lbl = QLabel(
            f"{'✓' if tracking_ok else '✗'}  {window_tracker.PLATFORM} — "
            + ("window tracking available" if tracking_ok
               else "needs an extra package on this OS (see README)"))
        status_lbl.setWordWrap(True)
        status_lbl.setStyleSheet(
            f"color: {tokens['accent'] if tracking_ok else '#FF6B6B'};"
            f"font-size: 11px; background: transparent;"
            f"font-family: '{tokens.get('font', 'Segoe UI')}';")
        layout.addWidget(status_lbl)

        layout.addStretch()
        return page

    @staticmethod
    def _monitor_options() -> list[tuple[str, str]]:
        """Real screens only. The default follows the primary screen, which is
        what the fox did before there was a picker."""
        options = [("-1", "Follow the primary screen")]
        try:
            screens = QApplication.screens()
        except Exception:                       # noqa: BLE001
            screens = []
        for index, screen in enumerate(screens):
            size = screen.geometry()
            options.append((str(index),
                            f"{index + 1}. {screen.name()} "
                            f"({size.width()}×{size.height()})"))
        return options

    def _on_autostart_toggled(self, on: bool):
        """Applied IMMEDIATELY, unlike every other control here.

        Save/Cancel governs our own QSettings; this one writes to the OS. A
        toggle that only took effect on Save would leave the checkbox and the
        real login item disagreeing for as long as the dialog stayed open —
        and Cancel could not undo an OS write anyway. If the write is refused
        the box goes back, because a switch that shows a state the machine
        does not have is worse than no switch.
        """
        if not self._autostart.supported:
            return
        if self._autostart.set_enabled(on):
            self._autostart_note.setText(self._autostart.location)
            self._autostart_note.setProperty("failed", False)
        else:
            self._autostart_check.blockSignals(True)
            self._autostart_check.setChecked(not on)
            self._autostart_check.blockSignals(False)
            self._autostart_note.setText(autostart_mod.failure_message(on))
            self._autostart_note.setProperty("failed", True)
        self._restyle_autostart_note()

    def _restyle_autostart_note(self):
        tokens = _matte_tokens()
        failed = bool(self._autostart_note.property("failed"))
        colour = "#FF6B6B" if failed else tokens.get("text_muted",
                                                     tokens["text"])
        self._autostart_note.setStyleSheet(
            f"color: {colour}; font-size: 11px; background: transparent;"
            f"font-family: '{tokens.get('font', 'Segoe UI')}';")

    def _reset_fox_position(self):
        self.settings.clear_pet_pos()
        self._reset_pos_btn.setText("Position cleared — restart or drag to set")
        self._reset_pos_btn.setEnabled(False)

    # ─────────────────────────────── Alerts tab (D13 · §9.2) ──────
    def _build_alerts_tab(self) -> QWidget:
        tokens = _matte_tokens()
        page, layout = self._scroll_page("alertsPage")
        s = self.settings

        layout.addWidget(_section_label("What interrupts you", tokens))
        layout.addWidget(_divider(tokens))

        self._breach_alert_check = self._check(
            "Policy breaches", s.breach_alerts_enabled(), tokens)
        layout.addWidget(self._breach_alert_check)
        self._risk_slider = LabelledSlider(
            "Only at or above risk", 0, 100, s.alert_min_risk(), "", tokens)
        layout.addWidget(self._risk_slider)
        risk_note = QLabel(
            "A breach below this is still recorded in the ledger — the fox "
            "just doesn't interrupt for it.")
        risk_note.setWordWrap(True)
        risk_note.setObjectName("alertsNote")
        layout.addWidget(risk_note)

        self._quota_check = self._check(
            "Running out of capture credits", s.quota_alerts_enabled(), tokens)
        layout.addWidget(self._quota_check)
        self._anchor_check = self._check(
            "Chain head anchored", s.anchor_alerts_enabled(), tokens)
        layout.addWidget(self._anchor_check)
        self._grading_check = self._check(
            "Gradings failing", s.grading_alerts_enabled(), tokens)
        layout.addWidget(self._grading_check)
        self._weekly_check = self._check(
            "Weekly activity summary", s.weekly_summary_enabled(), tokens)
        layout.addWidget(self._weekly_check)

        layout.addSpacing(2)
        layout.addWidget(_section_label("How it tells you", tokens))
        layout.addWidget(_divider(tokens))

        self._sound_check = self._check(
            "Play a sound", s.alert_sound_enabled(), tokens)
        layout.addWidget(self._sound_check)
        self._toast_check = self._check(
            "Show a desktop notification", s.native_toasts_enabled(), tokens)
        layout.addWidget(self._toast_check)

        layout.addSpacing(2)
        layout.addWidget(_section_label("Quiet hours", tokens))
        layout.addWidget(_divider(tokens))

        self._quiet_check = self._check(
            "Silence sounds and notifications overnight",
            s.quiet_hours_enabled(), tokens)
        layout.addWidget(self._quiet_check)
        quiet_from, quiet_to = s.quiet_hours()
        row = QWidget()
        row.setStyleSheet("background: transparent;")
        row_lay = QHBoxLayout(row)
        row_lay.setContentsMargins(0, 0, 0, 0)
        row_lay.setSpacing(10)
        self._quiet_from = self._time_edit(quiet_from, "Quiet hours start")
        self._quiet_to = self._time_edit(quiet_to, "Quiet hours end")
        row_lay.addWidget(QLabel("from"))
        row_lay.addWidget(self._quiet_from)
        row_lay.addWidget(QLabel("to"))
        row_lay.addWidget(self._quiet_to)
        row_lay.addStretch()
        layout.addWidget(row)
        # The window only means something when quiet hours are ON. Leaving the
        # fields live invited someone to set 22:00-07:00, close the dialog, and
        # wonder why the fox still beeped — the times were saved and ignored.
        self._quiet_row = row
        row.setEnabled(self._quiet_check.isChecked())
        self._quiet_check.toggled.connect(row.setEnabled)
        quiet_note = QLabel(
            "The fox still reacts on screen and every event is still "
            "recorded — only the sound and the notification stop.")
        quiet_note.setWordWrap(True)
        quiet_note.setObjectName("alertsNote")
        layout.addWidget(quiet_note)

        layout.addSpacing(2)
        layout.addWidget(_section_label("How often it checks", tokens))
        layout.addWidget(_divider(tokens))

        self._poll_slider = LabelledSlider(
            "Check for breaches every", cp.POLL_MIN, cp.POLL_MAX,
            s.breach_poll_seconds(), "s", tokens)
        layout.addWidget(self._poll_slider)

        self._alerts_status = QLabel("")
        self._alerts_status.setWordWrap(True)
        self._alerts_status.setObjectName("alertsNote")
        self._alerts_status.hide()
        layout.addWidget(self._alerts_status)

        layout.addStretch()
        return page

    def _time_edit(self, value: str, accessible: str) -> QTimeEdit:
        edit = QTimeEdit()
        edit.setDisplayFormat("HH:mm")
        parsed = cp.parse_time(value)
        edit.setTime(QTime(*(parsed or (0, 0))))
        edit.setAccessibleName(accessible)
        edit.setFixedHeight(34)
        edit.setFixedWidth(84)
        # Matches the QSS above, which hides the sub-controls; without this Qt
        # still reserves their width and the field sits off-centre in its box.
        edit.setButtonSymbols(QTimeEdit.ButtonSymbols.NoButtons)
        return edit

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
        self._backend_url_field.setPlaceholderText("https://app.foxyaudit.tech")
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
            QPushButton#foxyTestBtn:hover {{ background-color: {acc}; color: {_ACCENT_INK}; border: none; }}
            QPushButton#foxyTestBtn:disabled {{ color: {tokens.get('text_muted', '#888')}; border-color: {tokens['panel']}; }}
        """)

        layout.addStretch()
        return page

    def _test_foxy_backend(self):
        if any(w.isRunning() for w in self._workers):
            return  # guard BEFORE touching the button, or it sticks on "Testing…"
        org_key = self._org_key_field.text().strip()
        url = self._backend_url_field.text().strip()

        # Save first so the shared client (which reads FoxSettings at request
        # time) probes exactly what's in the form.
        self.settings.set_org_api_key(org_key)
        self.settings.set_backend_url(url)

        self._foxy_test_btn.setEnabled(False)
        self._foxy_test_btn.setText("Testing…")
        tokens = _matte_tokens()
        self._foxy_test_status.setStyleSheet(
            f"color: {tokens.get('text_muted', '#888')}; font-size: 12px;"
        )
        self._foxy_test_status.setText("Connecting…")

        w = spawn_worker(self.client, "GET", "/v1/health", timeout=5, parent=self,
                         force_bearer=True,  # /v1/health is Bearer-only on the backend
                         on_ok=self._on_foxy_ok, on_err=self._on_foxy_fail,
                         track=self._workers)
        w.finished.connect(self._reset_foxy_btn)

    def _reset_foxy_btn(self):
        self._foxy_test_btn.setEnabled(True)
        self._foxy_test_btn.setText("Test Backend")

    def _on_foxy_ok(self, _data=None):
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
        # Secrets go to the OS keychain — a refused write is NOT silently
        # swallowed (and never falls back to plaintext settings), so the dialog
        # stays open and says which key failed to store.
        failed = []
        if not self.settings.set_api_key(provider, self._key_field.text()):
            failed.append("AI provider key")
        self.settings.set_model(provider, self._model_field.text())
        self.settings.set_base_url(provider, self._url_field.text())

        self.settings.set_reaction_cooldown(self._cooldown_slider.value() / 10.0)
        self.settings.set_pat_sensitivity(self._pat_slider.value())
        self.settings.set_roaming_enabled(self._roam_check.isChecked())
        self.settings.set_proximity_glance_enabled(self._glance_check.isChecked())

        # ── Companion (D13). Autostart is absent on purpose: it wrote to the
        # OS the moment it was toggled — see `_on_autostart_toggled`.
        s = self.settings
        s.set_start_hidden(self._start_hidden_check.isChecked())
        s.set_open_console_on_launch(self._open_console_check.isChecked())
        s.set_close_to_tray(self._close_tray_check.isChecked())
        s.set_fox_scale(self._scale_slider.value())
        s.set_fox_opacity(self._opacity_slider.value())
        s.set_always_on_top(self._on_top_check.isChecked())
        s.set_roam_speed(self._roam_speed_slider.value())
        s.set_idle_break_frequency(self._idle_combo.currentData())
        s.set_tip_frequency(self._tip_combo.currentData())
        s.set_click_action(self._click_combo.currentData())
        s.set_input_reactions_enabled(self._input_react_check.isChecked())
        s.set_hardware_reactions_enabled(self._hw_react_check.isChecked())
        s.set_remember_position(self._remember_pos_check.isChecked())
        s.set_monitor_index(int(self._monitor_combo.currentData() or -1))

        # ── Alerts (D13 completes the tab D12 laid the keys for)
        s.set_breach_alerts_enabled(self._breach_alert_check.isChecked())
        s.set_alert_min_risk(self._risk_slider.value())
        s.set_quota_alerts_enabled(self._quota_check.isChecked())
        s.set_anchor_alerts_enabled(self._anchor_check.isChecked())
        s.set_grading_alerts_enabled(self._grading_check.isChecked())
        s.set_weekly_summary_enabled(self._weekly_check.isChecked())
        s.set_alert_sound_enabled(self._sound_check.isChecked())
        s.set_native_toasts_enabled(self._toast_check.isChecked())
        s.set_quiet_hours_enabled(self._quiet_check.isChecked())
        s.set_quiet_hours(self._quiet_from.time().toString("HH:mm"),
                          self._quiet_to.time().toString("HH:mm"))
        s.set_breach_poll_seconds(self._poll_slider.value())

        if not self.settings.set_org_api_key(self._org_key_field.text().strip()):
            failed.append("org API key")
        self.settings.set_backend_url(self._backend_url_field.text().strip())

        if failed:
            self._show_secret_error(failed)
            return                      # keep the dialog open — nothing to celebrate
        self.settings_saved.emit()
        self.accept()

    def _show_secret_error(self, failed: list[str]):
        """Tell the user plainly that a secret did not persist."""
        what = " and ".join(failed)
        self._foxy_test_status.setStyleSheet(
            "color: #FF4C4C; font-size: 12px; font-weight: 600;")
        self._foxy_test_status.setText(
            f"✗ Couldn't store the {what} in the OS keychain — not saved.")
        self._tab_bar._select(self._foxy_tab_index)   # surface it with the message
        self._stack.setCurrentIndex(self._foxy_tab_index)

    def done(self, result: int):
        # Every UI close path on this frameless dialog (X → reject, Cancel,
        # Save → accept, Esc) funnels through QDialog.done(), which does NOT
        # emit closeEvent — so the worker teardown lives here.
        if self._conn_worker and self._conn_worker.isRunning():
            self._conn_worker.wait(600)
        shutdown_workers(self._workers, wait_ms=600)
        super().done(result)


# ────────────────────────────────────────────────── standalone preview ─────
if __name__ == "__main__":
    app = QApplication(sys.argv)
    s   = FoxSettings()
    dlg = SettingsDialog(s)
    dlg.show()
    sys.exit(app.exec())
