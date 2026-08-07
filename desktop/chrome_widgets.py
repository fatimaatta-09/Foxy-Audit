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

from PyQt6.QtCore import (
    QEasingCurve, QPropertyAnimation, QRectF, QSize, Qt, QTimer, pyqtSignal,
)
from PyQt6.QtGui import QFont, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from console_chrome import (
    SHORTCUT_PAIRS, TOAST_MS, badge_text, palette_entries, relative_time,
)
import panel_state
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
        self.act.setMinimumHeight(44)
        self.act.setCursor(Qt.CursorShape.PointingHandCursor)
        self.act.clicked.connect(self._on_act)
        self.close_btn = QPushButton("✕")
        self.close_btn.setObjectName("annX")
        self.close_btn.setFixedSize(44, 44)
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
        # Paint at the screen's device pixel ratio: a fixed 18×18 pixmap is
        # visibly soft on a HiDPI display.
        ratio = self.devicePixelRatioF() or 1.0
        pm = QPixmap(int(18 * ratio), int(18 * ratio))
        pm.setDevicePixelRatio(ratio)
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
        self.read_all.setMinimumHeight(44)
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
        # Takes from the top but keeps the trailing footer row.
        panel_state.clear_rows(self.rows, start=0, floor=1)
        if not items:
            empty = QLabel("No notifications yet\nBreaches, quota and account "
                           "events will show up here.")
            empty.setWordWrap(True)
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet(
                f"color: {WEB['muted']}; font-family: '{pick_font('disp')}';"
                f" font-size: 11px; padding: 22px 10px; background: transparent;")
            panel_state.insert_visible(self.rows, 0, empty)
            self.sizeToContents()
            return
        for n in items:
            panel_state.insert_visible(self.rows,
                                       self.rows.count() - 1, self._row(n))
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
            f" border: 1.5px solid {tone if unread else WEB['muted']};"
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
        when.setStyleSheet(f"color: {WEB['muted']}; background: transparent;")
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
            item = QListWidgetItem(entry["label"])
            item.setSizeHint(QSize(0, 44))          # 44 px minimum hit target
            self.list.addItem(item)
        if self._entries:
            self.list.setCurrentRow(0)
            self.list.setAccessibleDescription(
                "%d result%s" % (len(self._entries),
                                 "" if len(self._entries) == 1 else "s"))
        else:
            empty = QListWidgetItem(
                'No matches — try a page name, a ledger hash, or "copy org".')
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            empty.setSizeHint(QSize(0, 44))
            self.list.addItem(empty)
            self.list.setAccessibleDescription("No results")

    def _accept_current(self):
        row = self.list.currentRow()
        if not self._entries:
            return                       # the empty-state row isn't selectable
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

        head = QHBoxLayout()
        title = QLabel("Keyboard shortcuts")
        title.setFont(_disp(13))
        title.setStyleSheet(f"color: {WEB['ink']}; background: transparent;")
        head.addWidget(title)
        head.addStretch()
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(44, 44)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setAccessibleName("Close shortcuts")
        close_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {WEB['muted']};"
            f" border: none; border-radius: 10px; font-size: 13px; }}"
            f"QPushButton:hover {{ background: {WEB['surf3']};"
            f" color: {WEB['ink']}; }}"
            f"QPushButton:focus {{ border: 1px solid {WEB['fox']}; }}")
        close_btn.clicked.connect(self.close)
        head.addWidget(close_btn)
        body.addLayout(head)
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

        hint = QLabel("Esc or ✕ closes")
        hint.setFont(_mono(7))
        hint.setStyleSheet(f"color: {WEB['muted']}; background: transparent;")
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


class ChainStrip(QWidget):
    """Five links of the hash chain, dashed from `solid` onward.

    The web's own device (`chain()`), and the chain is the product: how far it
    got is the one fact the locked card exists to state.

        5  every link solid    — capture is running
        4  the last one dashed — capture has stopped
        0  none of them solid  — capture has never started
    """

    def __init__(self, solid: int = 5, parent=None):
        super().__init__(parent)
        self._solid = solid
        self.setFixedSize(62, 14)

    def set_solid(self, solid: int):
        self._solid = solid
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setBrush(Qt.BrushStyle.NoBrush)
        pen = p.pen()
        pen.setColor(qcolor(WEB["ink2"]))
        pen.setWidthF(1.7)
        for i in range(5):
            pen.setStyle(Qt.PenStyle.SolidLine if i < self._solid
                         else Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.drawEllipse(QRectF(1 + i * 12, 1, 12, 12))
        p.end()


class LockOverlay(QWidget):
    """The billing lock, over the whole window (P5 · #106).

    A port of the web's `#billLock`, which is `position:fixed; inset:0` — it
    covers the console AND the chrome, deliberately: a locked console is not a
    console with one bad panel, and leaving the topbar reachable invites a
    customer to keep pressing controls that all answer 402.

    Before this, a locked workspace looked BROKEN. Every gated route answers
    402, so each panel resolved to `PanelState.ERROR` — "couldn't load" — and
    nothing on screen said the word locked until the customer tried to create
    something.

    Every sentence on it is the SERVER's, shaped by `billing_data.lock_view`.
    Nothing here decides what a customer's account is doing.
    """

    remedy = pyqtSignal(str)       # the reason's action: card / portal / upgrade
    ask = pyqtSignal()             # a member notifying the admins
    sign_out = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("lockOv")
        self.setAutoFillBackground(True)
        self._action = ""
        self._build()
        self.hide()

    # -- construction --------------------------------------------------------
    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.addStretch()
        row = QHBoxLayout()
        row.addStretch()

        self.card = QFrame()
        self.card.setObjectName("lockCard")
        self.card.setFixedWidth(470)                    # the web's .lockcard
        v = QVBoxLayout(self.card)
        v.setContentsMargins(24, 22, 24, 20)
        v.setSpacing(0)

        self.eyebrow = QLabel()
        self.eyebrow.setObjectName("lockEyebrow")
        self.title = QLabel()
        self.title.setObjectName("lockTitle")
        self.title.setWordWrap(True)
        self.body = QLabel()
        self.body.setObjectName("lockBody")
        self.body.setWordWrap(True)

        # The evidence strip. `capture_blocked` does NOT follow `locked`, and
        # rendering them as one state would tell a customer their audit trail
        # stopped when it had not — the worst sentence this product could show.
        self.ev = QFrame()
        self.ev.setObjectName("lockEv")
        ev_row = QHBoxLayout(self.ev)
        ev_row.setContentsMargins(14, 12, 14, 12)
        ev_row.setSpacing(12)
        self.chain = ChainStrip()
        ev_text = QVBoxLayout()
        ev_text.setSpacing(4)
        self.ev_title = QLabel()
        self.ev_title.setObjectName("lockEvT")
        self.ev_title.setWordWrap(True)
        self.ev_body = QLabel()
        self.ev_body.setObjectName("lockEvD")
        self.ev_body.setWordWrap(True)
        ev_text.addWidget(self.ev_title)
        ev_text.addWidget(self.ev_body)
        ev_row.addWidget(self.chain, 0, Qt.AlignmentFlag.AlignTop)
        ev_row.addLayout(ev_text, 1)

        # The member block (#107) — the web's own words, out of billing_data,
        # so the two products cannot describe one account two ways.
        self.ask_title = QLabel()
        self.ask_title.setObjectName("lockAskT")
        self.ask_title.setWordWrap(True)
        self.ask_body = QLabel()
        self.ask_body.setObjectName("lockAskD")
        self.ask_body.setWordWrap(True)

        self.status = QLabel("")
        self.status.setObjectName("lockErr")
        self.status.setWordWrap(True)
        self.status.setAccessibleName("Billing status")

        self.cta = QPushButton()
        self.cta.setObjectName("lockGo")
        # Named before it has a label. Until `show_lock` runs it has no text at
        # all, and a screen reader announces a textless button as "button" —
        # the exact hole `test_d15_a11y` walks this tree for. `_set_cta` keeps
        # the name equal to the label from then on, so the two cannot drift.
        self.cta.setAccessibleName("Billing action")
        self.cta.setMinimumHeight(44)
        self.cta.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cta.clicked.connect(self._on_cta)

        alt = QHBoxLayout()
        alt.setSpacing(16)
        alt.addStretch()
        self.out_btn = QPushButton("Sign out")
        self.out_btn.setObjectName("lockAlt")
        self.out_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.out_btn.setMinimumHeight(44)
        self.out_btn.clicked.connect(self.sign_out.emit)
        self.support = QLabel(
            '<a href="mailto:foxyaudit@gmail.com?subject=Dashboard%20locked">'
            "Contact support</a>")
        self.support.setObjectName("lockAlt")
        self.support.setOpenExternalLinks(True)
        self.support.setMinimumHeight(44)
        self.support.setAlignment(Qt.AlignmentFlag.AlignCenter)
        alt.addWidget(self.out_btn)
        alt.addWidget(self.support)
        alt.addStretch()

        # Every row carries its own gap, and the gap is REMEMBERED so it can
        # collapse with the row. Qt hides a widget but keeps a fixed spacer, and
        # four hidden rows here is the ordinary case — a pending workspace has
        # no ask, no status and no button — which left 48px of nothing above the
        # sign-out line. The render pass is what showed it.
        self._gaps: list[tuple] = []
        for widget, top in ((self.eyebrow, 0), (self.title, 7), (self.body, 9),
                            (self.ev, 16), (self.ask_title, 16),
                            (self.ask_body, 6), (self.status, 12),
                            (self.cta, 14)):
            v.addSpacing(top)
            self._gaps.append((v.itemAt(v.count() - 1), widget, top))
            v.addWidget(widget)
        v.addSpacing(15)
        v.addLayout(alt)

        row.addWidget(self.card)
        row.addStretch()
        outer.addLayout(row)
        outer.addStretch()
        self._style()

    def _style(self):
        disp, mono = pick_font("disp"), pick_font("mono")
        link = WEB["muted"]
        self.setStyleSheet(
            # rgba(6,5,4,.88) — the web's scrim, so the console reads as behind
            # this rather than beside it.
            "QWidget#lockOv { background: rgba(6, 5, 4, 224); }"
            f"QFrame#lockCard {{ background: {WEB['surf']};"
            f" border: 1px solid {WEB['line']}; border-radius: {RADIUS['lg']}px; }}"
            f"QLabel#lockEyebrow {{ color: {WEB['fox2']}; font-family: '{mono}';"
            " font-size: 9px; font-weight: 800; letter-spacing: 1.4px; }"
            f"QLabel#lockTitle {{ color: {WEB['ink']}; font-family: '{disp}';"
            " font-size: 19px; font-weight: 800; }"
            f"QLabel#lockBody {{ color: {WEB['ink2']}; font-family: '{disp}';"
            " font-size: 12.5px; }"
            f"QFrame#lockEv {{ border: 1px solid {WEB['line']};"
            f" border-radius: {RADIUS['sm']}px; background: {WEB['surf2']}; }}"
            f"QLabel#lockEvT {{ color: {WEB['ink']}; font-family: '{disp}';"
            " font-size: 12px; font-weight: 800; background: transparent; }"
            # --ink2, never --muted2: this is live text and muted2 is 3.01:1.
            f"QLabel#lockEvD {{ color: {WEB['ink2']}; font-family: '{disp}';"
            " font-size: 11.5px; background: transparent; }"
            f"QLabel#lockAskT {{ color: {WEB['ink']}; font-family: '{disp}';"
            " font-size: 12.5px; font-weight: 800; }"
            f"QLabel#lockAskD {{ color: {WEB['ink2']}; font-family: '{disp}';"
            " font-size: 11.5px; }"
            f"QLabel#lockErr {{ color: {WEB['breach_tx']}; font-family: '{disp}';"
            " font-size: 11.5px; font-weight: 600; }"
            f"QPushButton#lockGo {{ background: {WEB['fox']}; color: {WEB['bg']};"
            " border: none; border-radius: 14px; padding: 12px 18px;"
            f" font-family: '{disp}'; font-size: 12.5px; font-weight: 800; }}"
            f"QPushButton#lockGo:hover {{ background: {WEB['fox2']}; }}"
            f"QPushButton#lockGo:disabled {{ color: {WEB['muted']}; }}"
            f"QPushButton#lockGo:focus {{ border: 2px solid {WEB['ink']}; }}"
            # A TRANSPARENT border at rest, not `none`: adding one on :focus
            # grows the box and shoves the row along, so the control moves the
            # moment a keyboard reaches it. Reserving the pixel is what keeps
            # the focus ring free.
            f"QPushButton#lockAlt {{ background: transparent;"
            f" border: 1px solid transparent; border-radius: 4px;"
            f" color: {link}; font-family: '{mono}'; font-size: 10px;"
            " font-weight: 800; letter-spacing: .6px; padding: 0 6px; }"
            f"QPushButton#lockAlt:hover {{ color: {WEB['ink']}; }}"
            f"QPushButton#lockAlt:focus {{ border-color: {WEB['fox']}; }}"
            f"QLabel#lockAlt {{ background: transparent; font-family: '{mono}';"
            " font-size: 10px; font-weight: 800; letter-spacing: .6px; }")
        # A QLabel link takes its colour from the anchor, not the stylesheet.
        self.support.setText(
            '<a href="mailto:foxyaudit@gmail.com?subject=Dashboard%20locked" '
            f'style="color:{link};text-decoration:none;">Contact support</a>')

    # -- rendering -----------------------------------------------------------
    def show_lock(self, view: dict, ask: dict | None = None):
        """Paint one lock. `view` is `billing_data.lock_view`; `ask` is
        `billing_data.ask_view`, present only when this person cannot buy."""
        self.eyebrow.setText(str(view["eyebrow"]).upper())
        self.title.setText(view["lead"])
        self.body.setText(view["rest"])
        self.body.setVisible(bool(view["rest"]))

        note = view.get("evidence")
        if note:
            self.ev_title.setText(note[0])
            self.ev_body.setText(note[1])
            self.chain.set_solid(note[2])
        self.ev.setVisible(bool(note))

        # A member cannot complete ANY lock remedy — card-setup, portal and
        # upgrade-session are all admin-only — so the ask REPLACES the CTA
        # rather than sitting under a button that would answer 403.
        self._action = "" if ask else view["action"]
        for widget, text in ((self.ask_title, ask and ask["title"]),
                             (self.ask_body, ask and ask["body"])):
            widget.setText(text or "")
            widget.setVisible(bool(text))

        label = (ask["cta"] if ask else view["cta"]) or ""
        # Removed, not disabled. A disabled control still says "there is
        # something here for you, just not yet", and there is not: a workspace
        # waiting for a human reviewer has nothing to press, and neither has a
        # member who has already asked.
        self._set_cta(label)
        self.cta.setVisible(bool(label))
        self.cta.setEnabled(True)
        self.say("")

    def show_asked(self, ask: dict | None):
        """Redraw the member block from the SERVER's reply after a send.

        The button goes rather than greying out: there is nothing left to press
        for 24 hours, and a disabled control invites the press anyway. The
        wording comes from `billing_data`, which builds it round the server's
        own timestamp — so a second colleague who never touched the button
        reads the same true sentence.
        """
        if not ask:
            return
        self.ask_title.setText(ask["title"])
        self.ask_body.setText(ask["body"])
        self.ask_title.setVisible(True)
        self.ask_body.setVisible(True)
        self.cta.setVisible(bool(ask["cta"]))
        self._set_cta(ask["cta"])
        self.cta.setEnabled(True)
        self.say("")

    def _close_gaps(self):
        """A hidden row contributes no space. Called after every visibility
        change, because the card's shape is the sum of which rows apply."""
        for spacer, widget, top in self._gaps:
            spacer.changeSize(0, top if widget.isVisibleTo(self) else 0)
        self.card.layout().invalidate()

    def say(self, message: str):
        self.status.setText(message or "")
        self.status.setVisible(bool(message))
        self._close_gaps()

    def _set_cta(self, label: str):
        """Label and accessible name together, always. A button whose name
        says one thing and whose face says another is worse than either."""
        self.cta.setText(label)
        self.cta.setAccessibleName(label or "Billing action")

    def busy(self, label: str):
        self.cta.setEnabled(False)
        self._set_cta(label)

    def restore(self, label: str):
        self.cta.setEnabled(True)
        self._set_cta(label)

    def _on_cta(self):
        """One button, two meanings, and the empty action is the member's.
        `_action` is cleared whenever an ask block is showing, so this cannot
        fire a remedy the person is not allowed to complete."""
        if self._action:
            self.remedy.emit(self._action)
        else:
            self.ask.emit()

    def cover(self, host: QWidget):
        """Fill the host and take focus.

        Focus has to land inside a modal surface, and with no CTA the first real
        control is Sign out — which is also the only thing a pending workspace
        can act on."""
        self.setGeometry(host.rect())
        self.show()
        self.raise_()
        target = self.cta if self.cta.isVisible() else self.out_btn
        target.setFocus(Qt.FocusReason.OtherFocusReason)
class AskAdminBlock(QFrame):
    """"Only an admin can buy a plan" + the one thing a member CAN do (#107).

    The billing page's half. The lock overlay draws the same two sentences into
    its own card because it has to share one CTA with the reason's remedy — but
    both take every word from `billing_data`, which takes them from the web.
    Two renderings, one copy, so the products cannot drift into describing one
    account two ways.

    It answers the same four calls the overlay does (`busy` / `restore` / `say`
    / `show_asked`), so `ask_admin_to_upgrade` does not care which surface asked.
    """

    ask = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("askBlock")
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 13, 14, 13)
        v.setSpacing(6)
        self.title = QLabel()
        self.title.setObjectName("askT")
        self.title.setWordWrap(True)
        self.body = QLabel()
        self.body.setObjectName("askD")
        self.body.setWordWrap(True)
        self.status = QLabel("")
        self.status.setObjectName("askErr")
        self.status.setWordWrap(True)
        self.status.setAccessibleName("Upgrade request status")
        self.status.hide()
        self.cta = QPushButton()
        self.cta.setObjectName("askGo")
        self.cta.setAccessibleName("Notify the admins")
        self.cta.setMinimumHeight(44)
        self.cta.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cta.clicked.connect(self.ask.emit)
        for w in (self.title, self.body, self.status, self.cta):
            v.addWidget(w)
        disp = pick_font("disp")
        self.setStyleSheet(
            f"QFrame#askBlock {{ background: {WEB['surf2']};"
            f" border: 1px solid {WEB['line']}; border-radius: {RADIUS['sm']}px; }}"
            f"QLabel#askT {{ color: {WEB['ink']}; font-family: '{disp}';"
            " font-size: 12.5px; font-weight: 800; background: transparent; }"
            f"QLabel#askD {{ color: {WEB['ink2']}; font-family: '{disp}';"
            " font-size: 11.5px; background: transparent; }"
            f"QLabel#askErr {{ color: {WEB['breach_tx']}; font-family: '{disp}';"
            " font-size: 11.5px; font-weight: 600; background: transparent; }"
            f"QPushButton#askGo {{ background: {WEB['fox']}; color: {WEB['bg']};"
            " border: none; border-radius: 12px; padding: 10px 16px;"
            f" font-family: '{disp}'; font-size: 12px; font-weight: 800; }}"
            f"QPushButton#askGo:hover {{ background: {WEB['fox2']}; }}"
            f"QPushButton#askGo:disabled {{ color: {WEB['muted']}; }}"
            f"QPushButton#askGo:focus {{ border: 2px solid {WEB['ink']}; }}")
        self.hide()

    def show_ask(self, ask: dict | None):
        """`billing_data.ask_view`, or None for somebody who can just buy."""
        if not ask:
            self.hide()
            return
        self.title.setText(ask["title"])
        self.body.setText(ask["body"])
        self._set_cta(ask["cta"])
        self.cta.setVisible(bool(ask["cta"]))
        self.cta.setEnabled(True)
        self.say("")
        self.show()

    show_asked = show_ask        # same redraw; the payload says which state

    def _set_cta(self, label: str):
        self.cta.setText(label)
        self.cta.setAccessibleName(label or "Notify the admins")

    def say(self, message: str):
        self.status.setText(message or "")
        self.status.setVisible(bool(message))

    def busy(self, label: str):
        self.cta.setEnabled(False)
        self._set_cta(label)

    def restore(self, label: str):
        self.cta.setEnabled(True)
        self._set_cta(label)

