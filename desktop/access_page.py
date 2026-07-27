"""Foxy Audit desktop — the Access / API keys page (D9).

The web's `#page-keys` (foxy-audit-premium.html:1438-1476 + the loaders in
access_data.py), rebuilt with foxy_tokens.

**The hard rule, and how the code holds to it.** A plaintext API key exists in
this process for exactly as long as `ShownOnceDialog` is open. It arrives as an
argument, is placed in one read-only field, and is dropped when the dialog
closes. It is never assigned to a DashboardWindow attribute, never passed to
`log`, never written to QSettings or a file, and never rendered on a table row —
rows carry `key_prefix`, the non-secret fragment the server hands out precisely
so a key can be identified without being revealed.

The dialog is also the only place the word "copy" appears next to a secret, and
it says plainly that it cannot be recovered.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea,
    QSpinBox, QVBoxLayout, QWidget,
)

from foxy_tokens import BAD_RED, OK_GREEN, RADIUS, WARN_AMBER, WEB, pick_font
from home_page import _card, _label, _stat_tile, scroll_qss
from panel_state import StatusStrip
import access_data as ad

TONES = {"ok": OK_GREEN, "bad": BAD_RED, "warn": WARN_AMBER,
         "mute": WEB["muted"]}


class AccessSections:
    """Builds the Access page onto `owner` (the DashboardWindow)."""

    def __init__(self, owner):
        self.o = owner

    def build(self, t: dict) -> QWidget:
        o = self.o
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(scroll_qss())
        body = QWidget()
        body.setObjectName("pageBody")
        body.setStyleSheet("QWidget#pageBody { background: transparent; }")
        v = QVBoxLayout(body)
        v.setContentsMargins(22, 18, 22, 22)
        v.setSpacing(16)

        v.addWidget(self._head())
        v.addWidget(self._member_notice())
        v.addWidget(self._stats())
        v.addWidget(self._keys())
        v.addWidget(self._sdk())
        v.addStretch()

        scroll.setWidget(body)
        outer.addWidget(scroll)
        o.access_scroll = scroll
        return page

    def _head(self) -> QWidget:
        o = self.o
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        col = QVBoxLayout()
        col.setSpacing(3)
        col.addWidget(_label("● SDK CREDENTIALS", size=10, bold=True,
                             colour=WEB["fox2"], mono=True, spacing=1.5))
        col.addWidget(_label("Access.", size=26, bold=True))
        col.addWidget(_label("Keys your SDK uses to report interactions. Each "
                             "is shown once, at creation.", size=12,
                             colour=WEB["muted"], wrap=True))
        lay.addLayout(col, 1)

        o.key_regen_btn = QPushButton("↻ regenerate (2FA)")
        o.key_regen_btn.setObjectName("ghostBtn")
        o.key_regen_btn.setMinimumHeight(44)
        o.key_regen_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        o.key_regen_btn.setToolTip("Mint a fresh key behind an emailed 2FA "
                                   "code — the old key is revoked")
        o.key_regen_btn.clicked.connect(lambda: o.regenerate_key())
        lay.addWidget(o.key_regen_btn, 0, Qt.AlignmentFlag.AlignTop)

        o.key_new_btn = QPushButton("+ New key")
        o.key_new_btn.setObjectName("ctaBtn")
        o.key_new_btn.setMinimumHeight(44)
        o.key_new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        o.key_new_btn.clicked.connect(lambda: o.create_key())
        lay.addWidget(o.key_new_btn, 0, Qt.AlignmentFlag.AlignTop)
        return row

    def _member_notice(self) -> QWidget:
        """Shown to non-admins instead of hiding the page.

        A member can see that keys exist and when they were used; they just
        cannot change them. Hiding the section entirely would leave them unable
        to tell whether the workspace is instrumented at all.
        """
        o = self.o
        card, lay = _card()
        card.setStyleSheet(card.styleSheet().replace(
            f"border: 2.5px solid {WEB['bc']}",
            f"border: 2.5px solid {WARN_AMBER}"))
        lay.addWidget(_label("Read-only", size=11.5, bold=True,
                             colour=WARN_AMBER))
        lay.addWidget(_label(ad.MEMBER_NOTICE, size=10.5, colour=WEB["muted"],
                             wrap=True))
        o.key_member_notice = card
        card.hide()
        return card

    def _stats(self) -> QWidget:
        o = self.o
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        o.key_stats = []
        for label, value, tone in ad.stat_row(None, None):
            tile, value_lbl = _stat_tile(label, value, tone)
            o.key_stats.append(value_lbl)
            lay.addWidget(tile)
        return row

    def _keys(self) -> QWidget:
        o = self.o
        card, lay = _card()
        head = QHBoxLayout()
        head.addWidget(_label("Your API keys", size=12, bold=True))
        head.addStretch()
        lay.addLayout(head)

        header = QHBoxLayout()
        header.setContentsMargins(0, 6, 0, 8)
        header.setSpacing(10)
        for name, width in zip(ad.TABLE_COLUMNS, (None, 150, 96, 130, 92)):
            cell = _label(name.upper(), size=9, bold=True, colour=WEB["muted"],
                          mono=True, spacing=1.0)
            if width:
                cell.setFixedWidth(width)
                header.addWidget(cell)
            else:
                header.addWidget(cell, 1)
        lay.addLayout(header)

        o.key_rows = QVBoxLayout()
        o.key_rows.setSpacing(0)
        o.key_empty = StatusStrip()
        o.key_empty.retry.connect(lambda: o.refresh_keys())
        o.key_rows.addWidget(o.key_empty)
        lay.addLayout(o.key_rows)
        return card

    def _sdk(self) -> QWidget:
        o = self.o
        card, lay = _card()
        lay.addWidget(_label("Connect the SDK", size=12, bold=True))
        lay.addWidget(_label("Three lines, and every model call in that "
                             "process is captured.", size=10.5,
                             colour=WEB["muted"], wrap=True))
        o.sdk_boxes = []
        for caption, code in ad.sdk_snippets():
            lay.addWidget(_label(caption.upper(), size=9, bold=True,
                                 colour=WEB["muted"], mono=True, spacing=0.8))
            box = CodeBox(code)
            o.sdk_boxes.append(box)
            lay.addWidget(box)

        test = QHBoxLayout()
        test.setSpacing(10)
        o.sdk_test_btn = QPushButton("Test connection")
        o.sdk_test_btn.setObjectName("ghostBtn")
        o.sdk_test_btn.setMinimumHeight(44)
        o.sdk_test_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        o.sdk_test_btn.clicked.connect(lambda: o.test_sdk_connection())
        test.addWidget(o.sdk_test_btn)
        o.sdk_test_out = _label("", size=11, bold=True, mono=True)
        test.addWidget(o.sdk_test_out)
        test.addStretch()
        lay.addLayout(test)
        return card


# ── pieces ──────────────────────────────────────────────────────────────────
class CodeBox(QFrame):
    """A selectable code snippet with a copy button."""

    def __init__(self, code: str, parent=None):
        super().__init__(parent)
        self._code = code
        self.setObjectName("codeBox")
        self.setStyleSheet(
            f"QFrame#codeBox {{ background: {WEB['surf2']};"
            f" border: 2px solid {WEB['bc']};"
            f" border-radius: {RADIUS['sm']}px; }}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(11, 9, 9, 9)
        lay.setSpacing(8)
        text = QLabel(code)
        text.setWordWrap(True)
        text.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        text.setStyleSheet(
            f"color: {WEB['ink2']}; font-family: '{pick_font('mono')}';"
            f" font-size: 10.5px; background: transparent; border: none;")
        lay.addWidget(text, 1)
        self.copy_btn = QPushButton("copy")
        self.copy_btn.setObjectName("ghostBtn")
        self.copy_btn.setMinimumHeight(44)
        self.copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_btn.setAccessibleName(f"Copy: {code.splitlines()[0]}")
        self.copy_btn.clicked.connect(self._copy)
        lay.addWidget(self.copy_btn, 0, Qt.AlignmentFlag.AlignTop)

    def _copy(self):
        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText(self._code)
        self.copy_btn.setText("copied")


def key_row(row: dict, on_revoke) -> QWidget:
    """One key. Carries the prefix only — a full key never reaches a row."""
    frame = QFrame()
    lay = QHBoxLayout(frame)
    lay.setContentsMargins(0, 9, 0, 9)
    lay.setSpacing(10)
    colour = TONES.get(row["tone"], WEB["muted"])
    # A revoked or expired key is history, not a credential — dimmed so the
    # distinction is never a matter of reading the status column closely.
    ink = WEB["muted2"] if row["dim"] else WEB["ink"]

    lay.addWidget(_label(row["name"], size=11.5, bold=True, colour=ink), 1)

    prefix = _label(f"{row['prefix']}…", size=10, colour=WEB["muted"], mono=True)
    prefix.setFixedWidth(150)
    prefix.setToolTip("The key's non-secret prefix. The key itself is shown "
                      "only once, when it is created.")
    lay.addWidget(prefix)

    pill = _label(row["status"], size=9.5, bold=True, mono=True,
                  colour="#1a0900" if row["tone"] != "mute" else WEB["ink"])
    pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
    pill.setFixedWidth(96)
    pill.setStyleSheet(pill.styleSheet() + f"background: {colour};"
                       f" border-radius: 9px; padding: 3px 0;")
    lay.addWidget(pill)

    from console_chrome import relative_time
    used = _label(relative_time(row["last_used"]) if row["last_used"] else "never",
                  size=10, colour=WEB["muted"], mono=True)
    used.setFixedWidth(130)
    lay.addWidget(used)

    if row["revocable"]:
        btn = QPushButton("revoke")
        btn.setObjectName("dangerBtn")
        btn.setMinimumHeight(44)
        btn.setFixedWidth(92)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setAccessibleName(f"Revoke the key named {row['name']}")
        btn.clicked.connect(lambda: on_revoke(row))
        lay.addWidget(btn)
    else:
        spacer = QLabel("")
        spacer.setFixedWidth(92)
        lay.addWidget(spacer)
    return frame


class NewKeyDialog(QDialog):
    """Name + optional expiry. Replaces the web's two `window.prompt` calls."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New API key")
        self.setModal(True)
        self.setMinimumWidth(400)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 20)
        lay.setSpacing(10)
        lay.addWidget(_label("New API key", size=15, bold=True))
        lay.addWidget(_label("Name it after where it will run, so a revoke "
                             "later is an easy decision.", size=10.5,
                             colour=WEB["muted"], wrap=True))

        lay.addWidget(_label("NAME", size=9, bold=True, colour=WEB["muted"],
                             mono=True, spacing=0.8))
        self.name = QLineEdit("SDK key")
        self.name.setAccessibleName("Key name")
        self.name.setMinimumHeight(44)
        self.name.setStyleSheet(_dialog_input_qss())
        lay.addWidget(self.name)

        lay.addWidget(_label("AUTO-EXPIRE AFTER (DAYS, 0 = NEVER)", size=9,
                             bold=True, colour=WEB["muted"], mono=True,
                             spacing=0.8))
        self.days = QSpinBox()
        self.days.setRange(0, 3650)
        self.days.setAccessibleName("Auto-expire after days, zero for never")
        self.days.setMinimumHeight(44)
        self.days.setStyleSheet(_dialog_input_qss().replace("QLineEdit",
                                                            "QSpinBox"))
        lay.addWidget(self.days)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        cancel = QPushButton("Cancel")
        cancel.setObjectName("ghostBtn")
        cancel.setMinimumHeight(44)
        cancel.clicked.connect(self.reject)
        create = QPushButton("Create key")
        create.setObjectName("ctaBtn")
        create.setMinimumHeight(44)
        create.setDefault(True)
        create.clicked.connect(self.accept)
        buttons.addStretch()
        buttons.addWidget(cancel)
        buttons.addWidget(create)
        lay.addLayout(buttons)
        self.setStyleSheet(f"QDialog {{ background: {WEB['surf']}; }}")

    def values(self) -> tuple[str, int]:
        return self.name.text(), self.days.value()


class ShownOnceDialog(QDialog):
    """The only place a plaintext key is ever displayed.

    The value arrives as an argument and lives in one read-only field for as
    long as this dialog is open. It is not stored on the parent, not logged,
    not written anywhere. `clear_secret()` blanks the field on close so the
    string is not left sitting in a live widget behind a hidden dialog.
    """

    def __init__(self, title: str, secret: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 20)
        lay.setSpacing(10)
        lay.addWidget(_label(title, size=15, bold=True))
        # Verbatim from the web: this is the only warning the user gets.
        lay.addWidget(_label(ad.SHOWN_ONCE_TITLE, size=11.5, bold=True,
                             colour=WARN_AMBER, wrap=True))

        self.value = QLineEdit(secret)
        self.value.setReadOnly(True)
        self.value.setAccessibleName("Your new API key, shown once")
        self.value.setMinimumHeight(44)
        self.value.setStyleSheet(_dialog_input_qss())
        self.value.selectAll()
        lay.addWidget(self.value)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.copy_btn = QPushButton("Copy")
        self.copy_btn.setObjectName("ctaBtn")
        self.copy_btn.setMinimumHeight(44)
        self.copy_btn.setDefault(True)
        self.copy_btn.clicked.connect(self._copy)
        done = QPushButton("Done")
        done.setObjectName("ghostBtn")
        done.setMinimumHeight(44)
        done.clicked.connect(self.accept)
        buttons.addWidget(self.copy_btn)
        buttons.addWidget(done)
        buttons.addStretch()
        lay.addLayout(buttons)
        self.setStyleSheet(f"QDialog {{ background: {WEB['surf']}; }}")

    def _copy(self):
        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText(self.value.text())
        self.copy_btn.setText("Copied")

    def clear_secret(self):
        self.value.clear()

    def done(self, result):          # noqa: A003 — Qt's own name
        self.clear_secret()
        super().done(result)


def _dialog_input_qss() -> str:
    return (f"QLineEdit {{ background: {WEB['surf2']}; color: {WEB['ink']};"
            f" border: 2.5px solid {WEB['bc']}; border-radius: {RADIUS['sm']}px;"
            f" padding: 8px 11px; font-family: '{pick_font('mono')}';"
            f" font-size: 12px; }}"
            f"QLineEdit:focus {{ border-color: {WEB['fox']}; }}")
