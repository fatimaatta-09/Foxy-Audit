"""Foxy Audit desktop — console chrome widgets (D3).

The Qt half of the shell: live dot, count pips, announcement banner,
notifications panel, command palette, shortcuts overlay and toasts. All
decision logic lives in `console_chrome` (no Qt in it, so it is testable
without a screen); these classes only render it and raise signals.

Everything paints from foxy_tokens. Icon-only controls carry accessible names,
interactive targets are >= 44 px, focus is always visible, and the only motion
is a short fade that is skipped under OS reduced-motion.
"""

from __future__ import annotations

from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from console_chrome import (
    SHORTCUT_PAIRS, TOAST_MS, badge_text, palette_entries, relative_time,
)
from foxy_tokens import (
    BAD_RED, OK_GREEN, RADIUS, WARN_AMBER, WEB, paint_icon, pick_font, qcolor,
    reduced_motion,
)


def _mono(size: int = 9, bold: bool = True) -> QFont:
    f = QFont(pick_font("mono"), size)
    f.setBold(bold)
    return f


def _disp(size: int = 11, bold: bool = True) -> QFont:
    f = QFont(pick_font("disp"), size)
    f.setBold(bold)
    return f


class Toast(QLabel):
    """One toast at a time, bottom-centre of its parent (~2.7 s, the web's)."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setAccessibleName("notification")
        self.setStyleSheet(
            f"background: {WEB['surf3']}; color: {WEB['ink']};"
            f" border: 2px solid {WEB['bc']}; border-radius: 12px;"
            f" padding: 10px 16px; font-family: '{pick_font('disp')}';"
            f" font-size: 12px; font-weight: 700;")
        self.hide()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_message(self, text: str, ms: int = TOAST_MS):
        self.setText(text)
        self.adjustSize()
        parent = self.parentWidget()
        if parent is not None:
            self.move(max(8, (parent.width() - self.width()) // 2),
                      max(8, parent.height() - self.height() - 26))
        self.show()
        self.raise_()
        self._timer.start(ms)


class LiveDot(QWidget):
    """Connection state as a coloured pip PLUS the word — never colour alone."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._online: bool | None = None
        self.setFixedHeight(24)
        self.setMinimumWidth(64)
        self.set_online(None)

    def set_online(self, online: bool | None):
        self._online = online
        state = ("Connected to Foxy Audit" if online
                 else "Offline — trying to reconnect…" if online is False
                 else "Checking the connection…")
        self.setToolTip(state)
        self.setAccessibleName(state)
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        colour = (OK_GREEN if self._online else
                  BAD_RED if self._online is False else WARN_AMBER)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(qcolor(colour))
        p.drawEllipse(QRectF(0, self.height() / 2 - 3.5, 7, 7))
        p.setPen(qcolor(WEB["muted"]))
        p.setFont(_mono(8))
        label = ("live" if self._online else
                 "offline" if self._online is False else "…")
        p.drawText(QRectF(12, 0, self.width() - 12, self.height()),
                   int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                   label)
        p.end()


class Pip(QLabel):
    """Small count badge — unread notifications, unseen breaches."""

    def __init__(self, tone: str = BAD_RED, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet(
            f"background: {tone}; color: {WEB['breach_tx']};"
            f" border: 1.5px solid {WEB['bc']}; border-radius: 8px;"
            f" font-family: '{pick_font('mono')}'; font-size: 8px;"
            f" font-weight: 800; padding: 1px 5px;")
        self.hide()

    def set_count(self, count: int):
        text = badge_text(count)
        self.setText(text)
        self.setAccessibleName(f"{count} unread" if count else "")
        self.setVisible(bool(text))
        self.adjustSize()


class AnnouncementBanner(QFrame):
    """Real-signal-only status strip; dismissals persist per id."""

    action_clicked = pyqtSignal(str)      # target section id
    dismissed = pyqtSignal(str)           # announcement id

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("annBanner")
        self._id: str | None = None
        self._target = "home"
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 8, 10, 8)
        row.setSpacing(10)
        self.icon = QLabel()
        self.icon.setFixedSize(18, 18)
        self.text = QLabel()
        self.text.setWordWrap(True)
        self.act = QPushButton()
        self.act.setObjectName("annAct")
        self.act.setMinimumHeight(30)
        self.act.setCursor(Qt.CursorShape.PointingHandCursor)
        self.act.clicked.connect(self._on_act)
        self.close_btn = QPushButton("✕")
        self.close_btn.setObjectName("annX")
        self.close_btn.setFixedSize(30, 30)
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_btn.setAccessibleName("Dismiss announcement")
        self.close_btn.clicked.connect(self._on_dismiss)
        row.addWidget(self.icon)
        row.addWidget(self.text, 1)
        row.addWidget(self.act)
        row.addWidget(self.close_btn)
        self.hide()

    def show_message(self, msg: dict | None):
        if not msg:
            self._id = None
            self.hide()
            return
        self._id = msg["id"]
        self._target = msg["target"]
        tone = BAD_RED if msg["tone"] == "bad" else WARN_AMBER
        self.text.setText(msg["text"])
        self.text.setStyleSheet(
            f"color: {WEB['ink']}; font-family: '{pick_font('disp')}';"
            f" font-size: 12px; font-weight: 700; background: transparent;")
        self.act.setText(msg["action"])
        self.setStyleSheet(
            f"QFrame#annBanner {{ background: {WEB['surf2']};"
            f" border: 2px solid {tone}; border-radius: {RADIUS['sm']}px; }}"
            f"QPushButton#annAct {{ background: {tone}; color: {WEB['breach_tx']};"
            f" border: none; border-radius: 9px; padding: 6px 12px;"
            f" font-family: '{pick_font('disp')}'; font-size: 11px; font-weight: 800; }}"
            f"QPushButton#annAct:focus {{ border: 2px solid {WEB['ink']}; }}"
            f"QPushButton#annX {{ background: transparent; color: {WEB['muted']};"
            f" border: none; border-radius: 8px; font-size: 12px; }}"
            f"QPushButton#annX:hover {{ background: {WEB['surf3']};"
            f" color: {WEB['ink']}; }}"
            f"QPushButton#annX:focus {{ border: 1px solid {WEB['fox']}; }}")
        pm = QPixmap(18, 18)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        paint_icon(p, QRectF(0, 0, 18, 18),
                   "shield" if msg["icon"] == "warn" else "log", qcolor(tone), 2.0)
        p.end()
        self.icon.setPixmap(pm)
        self.setAccessibleName(msg["text"])
        self.show()

    def _on_act(self):
        if self._id:
            self.action_clicked.emit(self._target)

    def _on_dismiss(self):
        if self._id:
            self.dismissed.emit(self._id)
        self.hide()


class NotificationsPanel(QFrame):
    """The bell's dropdown: real rows, unread dots, honest empty state."""

    item_clicked = pyqtSignal(str, str)   # (notification id, kind)
    read_all_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("notifPanel")
        self.setFixedWidth(340)
        self.setMaximumHeight(420)
        self.setStyleSheet(
            f"QFrame#notifPanel {{ background: {WEB['surf']};"
            f" border: 2.5px solid {WEB['bc']}; border-radius: {RADIUS['md']}px; }}")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(8)

        head = QHBoxLayout()
        title = QLabel("Notifications")
        title.setFont(_disp(11))
        title.setStyleSheet(f"color: {WEB['ink']}; background: transparent;")
        self.read_all = QPushButton("Mark all read")
        self.read_all.setObjectName("readAll")
        self.read_all.setMinimumHeight(30)
        self.read_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self.read_all.setStyleSheet(
            f"QPushButton#readAll {{ background: transparent; color: {WEB['fox2']};"
            f" border: none; font-family: '{pick_font('mono')}'; font-size: 9px;"
            f" font-weight: 700; padding: 4px 6px; }}"
            f"QPushButton#readAll:hover {{ color: {WEB['fox']}; }}"
            f"QPushButton#readAll:focus {{ border: 1px solid {WEB['fox']};"
            f" border-radius: 6px; }}")
        self.read_all.clicked.connect(self.read_all_clicked.emit)
        head.addWidget(title)
        head.addStretch()
        head.addWidget(self.read_all)
        outer.addLayout(head)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setStyleSheet("background: transparent;")
        self.body = QWidget()
        self.body.setStyleSheet("background: transparent;")
        self.rows = QVBoxLayout(self.body)
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.rows.setSpacing(4)
        self.rows.addStretch()
        self.scroll.setWidget(self.body)
        outer.addWidget(self.scroll, 1)
        self.hide()

    def sizeToContents(self):
        """The panel floats over the console rather than sitting in a layout,
        so nothing else will give it a height — it has to claim one itself."""
        # Measure the rows themselves: the layout's trailing stretch and the
        # scroll area's own hint both inflate sizeHint into dead space.
        rows_h = 0
        for i in range(self.rows.count()):
            widget = self.rows.itemAt(i).widget()
            if widget is not None:
                rows_h += widget.sizeHint().height() + self.rows.spacing()
        wanted = rows_h + 62                            # header + margins
        self.resize(self.width(), max(130, min(self.maximumHeight(), wanted)))

    def set_items(self, items: list[dict]):
        while self.rows.count() > 1:
            item = self.rows.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if not items:
            empty = QLabel("No notifications yet\nBreaches, quota and account "
                           "events will show up here.")
            empty.setWordWrap(True)
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet(
                f"color: {WEB['muted']}; font-family: '{pick_font('disp')}';"
                f" font-size: 11px; padding: 22px 10px; background: transparent;")
            self.rows.insertWidget(0, empty)
            self.sizeToContents()
            return
        for n in items:
            self.rows.insertWidget(self.rows.count() - 1, self._row(n))
        self.sizeToContents()

    def _row(self, n: dict) -> QWidget:
        unread = not n.get("read")
        level = (n.get("level") or "info").lower()
        tone = {"critical": BAD_RED, "warn": WARN_AMBER}.get(level, WEB["fox"])
        row = QPushButton()
        row.setCursor(Qt.CursorShape.PointingHandCursor)
        row.setMinimumHeight(52)
        row.setStyleSheet(
            f"QPushButton {{ text-align: left; padding: 8px 10px; border: none;"
            f" border-radius: 10px; background: "
            f"{WEB['surf2'] if unread else 'transparent'}; }}"
            f"QPushButton:hover {{ background: {WEB['surf3']}; }}"
            f"QPushButton:focus {{ border: 1px solid {WEB['fox']}; }}")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(9)
        dot = QLabel()
        dot.setFixedSize(8, 8)
        dot.setStyleSheet(
            f"background: {tone if unread else 'transparent'};"
            f" border: 1.5px solid {tone if unread else WEB['muted2']};"
            f" border-radius: 4px;")
        col = QVBoxLayout()
        col.setSpacing(1)
        title = QLabel(str(n.get("title") or ""))
        title.setWordWrap(True)
        title.setFont(_disp(10))
        title.setStyleSheet(f"color: {WEB['ink']}; background: transparent;")
        col.addWidget(title)
        if n.get("body"):
            sub = QLabel(str(n["body"]))
            sub.setWordWrap(True)
            sub.setFont(_disp(9, bold=False))
            sub.setStyleSheet(f"color: {WEB['muted']}; background: transparent;")
            col.addWidget(sub)
        when = QLabel(relative_time(n.get("created_at")))
        when.setFont(_mono(7))
        when.setStyleSheet(f"color: {WEB['muted2']}; background: transparent;")
        col.addWidget(when)
        lay.addWidget(dot, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(col, 1)
        state = "unread" if unread else "read"
        row.setAccessibleName(f"{n.get('title') or 'notification'} — {state}")
        row.clicked.connect(
            lambda _c=False, i=str(n.get("id") or ""), k=str(n.get("kind") or ""):
            self.item_clicked.emit(i, k))
        return row


class CommandPalette(QDialog):
    """Ctrl+K — jump to a section, verify a pasted hash, open a record."""

    chosen = pyqtSignal(dict)

    def __init__(self, parent=None, *, org_id: str | None = None):
        super().__init__(parent)
        self._org_id = org_id
        self._entries: list[dict] = []
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)
        self.setFixedWidth(520)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card = QFrame()
        card.setObjectName("cmdCard")
        outer.addWidget(card)
        body = QVBoxLayout(card)
        body.setContentsMargins(14, 14, 14, 12)
        body.setSpacing(10)

        self.input = QLineEdit()
        self.input.setMinimumHeight(44)
        self.input.setPlaceholderText(
            "Jump to a section, or paste a ledger hash / #seq…")
        self.input.setAccessibleName("Command palette search")
        self.input.textChanged.connect(self._refresh)
        body.addWidget(self.input)

        self.list = QListWidget()
        self.list.setMaximumHeight(320)
        self.list.setAccessibleName("Command results")
        self.list.itemClicked.connect(lambda _i: self._accept_current())
        body.addWidget(self.list)

        hint = QLabel("↑↓ navigate · ↵ open · esc close")
        hint.setFont(_mono(8))
        hint.setStyleSheet(f"color: {WEB['muted']}; background: transparent;")
        body.addWidget(hint)

        card.setStyleSheet(
            f"QFrame#cmdCard {{ background: {WEB['surf']};"
            f" border: 2.5px solid {WEB['bc']}; border-radius: {RADIUS['md']}px; }}"
            f"QLineEdit {{ background: {WEB['bg']}; color: {WEB['ink']};"
            f" border: 2.5px solid {WEB['line']}; border-radius: 12px;"
            f" padding: 10px 13px; font-family: '{pick_font('disp')}';"
            f" font-size: 13px; }}"
            f"QLineEdit:focus {{ border-color: {WEB['fox']}; }}"
            f"QListWidget {{ background: transparent; border: none;"
            f" color: {WEB['ink']}; font-family: '{pick_font('disp')}';"
            f" font-size: 12.5px; }}"
            f"QListWidget::item {{ padding: 9px 10px; border-radius: 9px; }}"
            f"QListWidget::item:selected {{ background: {WEB['surf3']};"
            f" color: {WEB['fox2']}; }}")
        self._refresh("")

    def set_org_id(self, org_id: str | None):
        self._org_id = org_id

    def _refresh(self, query: str = ""):
        self._entries = palette_entries(query, org_id=self._org_id)
        self.list.clear()
        for entry in self._entries:
            self.list.addItem(QListWidgetItem(entry["label"]))
        if self._entries:
            self.list.setCurrentRow(0)

    def _accept_current(self):
        row = self.list.currentRow()
        if 0 <= row < len(self._entries):
            self.chosen.emit(self._entries[row])
            self.accept()

    def keyPressEvent(self, event):
        key = event.key()
        if key in (Qt.Key.Key_Down, Qt.Key.Key_Up) and self.list.count():
            step = 1 if key == Qt.Key.Key_Down else -1
            self.list.setCurrentRow(
                max(0, min(self.list.count() - 1, self.list.currentRow() + step)))
            event.accept()
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._accept_current()
            event.accept()
            return
        super().keyPressEvent(event)

    def open_fresh(self):
        self.input.clear()
        self._refresh("")
        self.show()
        self.raise_()
        self.activateWindow()
        self.input.setFocus()


class ShortcutsOverlay(QDialog):
    """The `?` cheat sheet."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)
        self.setFixedWidth(430)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card = QFrame()
        card.setObjectName("scCard")
        card.setStyleSheet(
            f"QFrame#scCard {{ background: {WEB['surf']};"
            f" border: 2.5px solid {WEB['bc']}; border-radius: {RADIUS['md']}px; }}")
        outer.addWidget(card)
        body = QVBoxLayout(card)
        body.setContentsMargins(20, 18, 20, 18)
        body.setSpacing(7)

        title = QLabel("Keyboard shortcuts")
        title.setFont(_disp(13))
        title.setStyleSheet(f"color: {WEB['ink']}; background: transparent;")
        body.addWidget(title)
        body.addSpacing(4)

        for keys, what in SHORTCUT_PAIRS:
            row = QHBoxLayout()
            k = QLabel(keys)
            k.setFont(_mono(8))
            k.setMinimumWidth(96)
            k.setStyleSheet(
                f"color: {WEB['ink2']}; background: {WEB['surf3']};"
                f" border: 1.5px solid {WEB['bc']}; border-radius: 6px;"
                f" padding: 3px 7px;")
            d = QLabel(what)
            d.setFont(_disp(10, bold=False))
            d.setStyleSheet(f"color: {WEB['muted']}; background: transparent;")
            row.addWidget(k)
            row.addWidget(d, 1)
            body.addLayout(row)

        hint = QLabel("Esc closes")
        hint.setFont(_mono(7))
        hint.setStyleSheet(f"color: {WEB['muted2']}; background: transparent;")
        body.addSpacing(4)
        body.addWidget(hint)

    def show_centered(self, parent: QWidget | None = None):
        host = parent or self.parentWidget()
        self.adjustSize()
        if host is not None:
            centre = host.geometry().center()
            self.move(centre.x() - self.width() // 2,
                      centre.y() - self.height() // 2)
        if reduced_motion():
            self.show()
            return
        self.setWindowOpacity(0.0)
        self.show()
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(160)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade.start()
