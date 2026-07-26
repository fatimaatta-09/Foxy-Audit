"""Foxy Audit desktop — sign-in surfaces (D1).

The desktop counterpart of the web dashboard's `#authGate`
(foxy-dashboard/foxy-audit-premium.html:1766-1830 + its controller IIFE at
:2490-2570). Same steps, same copy, same failure wording — rebuilt in Qt with
the web's own token values (foxy_tokens.WEB / RADIUS / CLAY_SHADOW), so the
two gates read as one product. Fonts stay Unbounded + Space Mono, the single
permitted deviation.

  LoginWindow   email + password  →  (optional) 6-digit email OTP  →  session
                forgot-password   →  neutral "if that email has an account…"
                organization API key → /v1/auth/handoff → /redeem → session
  StepUpDialog  6-digit re-auth for danger actions, wired to the client's
                step_up_required signal; the caller retries the blocked request
                once after it is accepted.

Deltas from the web gate, and why (plan §5.3):
* No Google / company-SSO buttons. Both are browser redirect flows the desktop
  cannot host, so instead of a button that cannot work, the screen says so and
  points to the API-key path.
* The API-key path is desktop-only (the browser has a live session already).
* The reset-password step is NOT ported: the emailed link opens the web
  dashboard, which the screen states plainly.
* The web's 🔑 emoji becomes a vector glyph (foxy_tokens.paint_icon "key" /
  "mail") — no emoji as iconography.

Every network call runs on an ApiWorker thread; the UI never blocks. Buttons
disable while a request is in flight and re-enable on every outcome.
"""

from __future__ import annotations

import re
import sys

from PyQt6.QtCore import (
    Qt, QEasingCurve, QPropertyAnimation, QRectF, QSize, QThread, QTimer, pyqtSignal,
)
from PyQt6.QtGui import (
    QColor, QFont, QGuiApplication, QPainter, QPalette, QPixmap,
)
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)

from foxy_client import ApiError, FoxyClient, shutdown_workers, spawn_worker
from foxy_tokens import (
    BAD_RED, OK_GREEN, RADIUS, WEB, paint_icon, pick_font, qcolor, resource_path,
)

# Same shape the web validates with (foxy-audit-premium.html:2496).
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _reduced_motion() -> bool:
    """True when the OS asks for reduced motion.

    Qt exposes no cross-platform hint, so read the real setting where we can:
    on Windows SPI_GETCLIENTAREAANIMATION (0x1042) is what the "Show animations
    in Windows" toggle writes. Elsewhere assume motion is fine rather than
    guess — it is only a 180 ms fade either way."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        enabled = ctypes.c_int()
        if ctypes.windll.user32.SystemParametersInfoW(
                0x1042, 0, ctypes.byref(enabled), 0):
            return not bool(enabled.value)
    except Exception:
        pass
    return False


def _glyph(name: str, size: int = 34) -> QPixmap:
    """A fox-orange rounded tile with a vector glyph — the web gate's icon
    chip, without the emoji."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(qcolor(WEB["fox"]))
    p.drawRoundedRect(QRectF(0, 0, size, size), size * 0.29, size * 0.29)
    paint_icon(p, QRectF(size * 0.2, size * 0.2, size * 0.6, size * 0.6),
               name, qcolor("#1a0900"), 2.0)
    p.end()
    return pm


class _Field(QWidget):
    """A labelled input, mirroring the web's `.flabel` + `.cin` pair.

    The label is a real always-visible QLabel: a placeholder vanishes the
    moment someone types, which is exactly when the label is still needed."""

    def __init__(self, label: str, *, password: bool = False,
                 placeholder: str = "", code: bool = False, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        self.label = QLabel(label.upper())
        self.label.setObjectName("flabel")
        v.addWidget(self.label)

        self.input = QLineEdit()
        self.input.setObjectName("codeInput" if code else "cin")
        self.input.setMinimumHeight(44)          # 44 px minimum hit target
        self.input.setPlaceholderText(placeholder)
        # Qt has no ::placeholder selector — the colour is a palette role
        # (the web sets .cin::placeholder to --muted2).
        pal = self.input.palette()
        pal.setColor(QPalette.ColorRole.PlaceholderText, qcolor(WEB["muted2"]))
        self.input.setPalette(pal)
        self.input.setAccessibleName(label)
        if password:
            self.input.setEchoMode(QLineEdit.EchoMode.Password)
        if code:
            self.input.setMaxLength(6)
            self.input.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self.input)

    def text(self) -> str:
        return self.input.text().strip()

    def focus(self):
        self.input.setFocus()
        self.input.selectAll()


class _AuthCard(QDialog):
    """Shared frameless card chrome: the web's `.clay pad` surface, a brand
    header, drag-to-move and a 180 ms fade."""

    # Wide enough that the web's remember-me sentence fits on one line in
    # Unbounded (the web card is 340 px, but its display face is narrower).
    CARD_WIDTH = 410

    def __init__(self, window_title: str, parent=None):
        super().__init__(parent)
        self._drag_pos = None
        self.setWindowTitle(window_title)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 18, 18, 18)   # room for the clay shadow
        self.card = QFrame()
        self.card.setObjectName("clay")
        self.card.setFixedWidth(self.CARD_WIDTH)
        outer.addWidget(self.card)

        self.body = QVBoxLayout(self.card)
        self.body.setContentsMargins(24, 24, 24, 24)   # .clay.pad
        self.body.setSpacing(0)
        self._apply_qss()

    def add_header(self, icon: str, title: str, eyebrow: str) -> tuple:
        """The web gate's header: icon chip + title + eyebrow."""
        row = QHBoxLayout()
        row.setSpacing(10)
        chip = QLabel()
        chip.setFixedSize(36, 36)
        chip.setPixmap(_glyph(icon, 36))
        row.addWidget(chip, 0, Qt.AlignmentFlag.AlignTop)

        col = QVBoxLayout()
        col.setSpacing(2)
        t = QLabel(title)
        t.setObjectName("cardTitle")
        e = QLabel(eyebrow.upper())
        e.setObjectName("eyebrow")
        col.addWidget(t)
        col.addWidget(e)
        row.addLayout(col)
        row.addStretch()
        self.body.addLayout(row)
        self.body.addSpacing(16)
        return t, e

    def _apply_qss(self):
        disp, mono = pick_font("disp"), pick_font("mono")
        self.setStyleSheet(f"""
            QFrame#clay {{
                background: {WEB['surf']};
                border: 2.5px solid {WEB['bc']};
                border-radius: {RADIUS['lg']}px;
            }}
            QLabel {{ background: transparent; color: {WEB['ink']};
                      font-family: '{disp}'; font-size: 12.5px; }}
            QLabel#cardTitle {{ font-size: 15px; font-weight: 800; }}
            QLabel#eyebrow {{ font-family: '{mono}'; font-size: 10px;
                font-weight: 700; letter-spacing: 1.5px; color: {WEB['fox2']}; }}
            QLabel#flabel {{ font-family: '{mono}'; font-size: 9.5px;
                font-weight: 700; letter-spacing: 0.8px; color: {WEB['muted']}; }}
            QLabel#hint {{ font-family: '{mono}'; font-size: 10px;
                color: {WEB['muted']}; }}
            QLabel#fbBad {{ font-family: '{mono}'; font-size: 10px;
                font-weight: 700; color: {BAD_RED}; }}
            QLabel#fbOk {{ font-family: '{mono}'; font-size: 10px;
                font-weight: 700; color: {OK_GREEN}; }}
            QLineEdit#cin, QLineEdit#codeInput {{
                background: {WEB['bg']}; color: {WEB['ink']};
                border: 2.5px solid {WEB['line']}; border-radius: 12px;
                padding: 11px 13px; font-size: 12.5px; font-family: '{disp}';
                selection-background-color: {WEB['fox']};
            }}
            QLineEdit#codeInput {{ font-family: '{mono}'; font-size: 18px;
                font-weight: 700; letter-spacing: 7px; }}
            QLineEdit#cin:focus, QLineEdit#codeInput:focus {{
                border-color: {WEB['fox']}; }}
            /* after the :focus rule so an invalid field stays red while the
               error handler puts the caret back into it */
            QLineEdit[invalid="true"], QLineEdit[invalid="true"]:focus {{
                border-color: {BAD_RED}; }}
            QLineEdit:disabled {{ color: {WEB['muted']}; }}
            QPushButton#pri {{
                background: {WEB['fox']}; color: #1a0900;
                border: 3px solid {WEB['bc']}; border-radius: 13px;
                font-family: '{disp}'; font-weight: 800; font-size: 12.5px;
                padding: 12px 18px;
            }}
            QPushButton#pri:hover {{ background: {WEB['fox2']}; }}
            QPushButton#pri:disabled {{ background: {WEB['surf3']};
                color: {WEB['ink2']}; }}
            QPushButton#pri:focus {{ border: 3px solid {WEB['ink']}; }}
            QPushButton#link {{
                background: transparent; color: {WEB['ink2']}; border: none;
                font-family: '{mono}'; font-size: 10px; text-align: left;
                padding: 12px 4px;
            }}
            QPushButton#link:hover {{ color: {WEB['fox2']}; }}
            QPushButton#link:focus {{ color: {WEB['fox2']};
                border: 1px solid {WEB['fox']}; border-radius: 6px; }}
            QPushButton#xBtn {{ background: transparent; color: {WEB['muted']};
                border: none; border-radius: 9px; font-size: 13px; }}
            QPushButton#xBtn:hover {{ background: {WEB['surf3']};
                color: {WEB['ink']}; }}
            QCheckBox {{ color: {WEB['ink2']}; font-size: 11px;
                         font-family: '{disp}'; spacing: 9px; }}
            QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px;
                border: 2px solid {WEB['line']}; background: {WEB['bg']}; }}
            QCheckBox::indicator:checked {{ background: {WEB['fox']};
                border-color: {WEB['fox']}; }}
            QCheckBox:focus {{ color: {WEB['fox2']}; }}
        """)

    # ── frameless drag ──
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag_pos and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, e):
        self._drag_pos = None

    def center_on_screen(self):
        scr = QGuiApplication.primaryScreen()
        if scr is None:
            return
        g = scr.availableGeometry()
        self.adjustSize()
        self.move(g.center().x() - self.width() // 2,
                  g.center().y() - self.height() // 2)

    def show_animated(self):
        self.show()
        self.raise_()
        self.activateWindow()
        if _reduced_motion():
            self.setWindowOpacity(1.0)
            return
        self.setWindowOpacity(0.0)
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(180)              # 150-300 ms band
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade.start()

    # ── shared feedback slot (the web's .fb-res, height reserved so the card
    #    never jumps when a message appears) ──
    @staticmethod
    def _feedback_label() -> QLabel:
        lbl = QLabel("")
        lbl.setObjectName("fbBad")
        lbl.setWordWrap(True)
        lbl.setMinimumHeight(16)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setAccessibleName("status message")
        return lbl

    @staticmethod
    def _say(label: QLabel, text: str, ok: bool = False):
        label.setObjectName("fbOk" if ok else "fbBad")
        label.setText(text)
        # Announce to assistive tech: the accessible description is what a
        # screen reader reads when focus lands back on the form.
        label.setAccessibleDescription(text)
        label.style().unpolish(label)
        label.style().polish(label)

    @staticmethod
    def _mark(field: _Field, invalid: bool, message: str = ""):
        field.input.setProperty("invalid", invalid)
        # The message lives on the CONTROL too, not just the shared slot, so a
        # screen reader reads it when focus lands back in the field.
        field.input.setAccessibleDescription(message if invalid else "")
        field.input.style().unpolish(field.input)
        field.input.style().polish(field.input)

    @staticmethod
    def _detail(err: str) -> str:
        """'HTTP 401: Invalid email or password' → 'Invalid email or password'."""
        return err.split(": ", 1)[-1] if ": " in err else err


class StepUpDialog(_AuthCard):
    """Re-auth for danger actions. Mirrors the backend's own wording: the code
    is 6 digits, expires in 5 minutes, and a wrong one comes back as
    'invalid or expired code'."""

    def __init__(self, client: FoxyClient, parent=None, *, auto_request: bool = True):
        super().__init__("Confirm it's you", parent)
        self.client = client
        self._workers: set = set()

        self.add_header("key", "Confirm it's you", "security check")

        blurb = QLabel("This change needs a fresh check. We'll email you a "
                       "6-digit code — it expires in 5 minutes.")
        blurb.setObjectName("hint")
        blurb.setWordWrap(True)
        self.body.addWidget(blurb)
        self.body.addSpacing(10)

        self.code = _Field("verification code", placeholder="123456", code=True)
        self.code.input.returnPressed.connect(self._confirm)
        self.body.addWidget(self.code)

        self.fb = self._feedback_label()
        self.body.addSpacing(10)
        self.body.addWidget(self.fb)

        self.confirm_btn = QPushButton("Confirm")
        self.confirm_btn.setObjectName("pri")
        self.confirm_btn.setMinimumHeight(46)
        self.confirm_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.confirm_btn.setDefault(True)
        self.confirm_btn.clicked.connect(self._confirm)
        self.body.addSpacing(6)
        self.body.addWidget(self.confirm_btn)

        row = QHBoxLayout()
        self.resend_btn = QPushButton("Resend code")
        self.resend_btn.setObjectName("link")
        self.resend_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.resend_btn.clicked.connect(self._send_code)
        row.addWidget(self.resend_btn)
        row.addStretch()
        cancel = QPushButton("Cancel (Esc)")
        cancel.setObjectName("link")
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        self.body.addLayout(row)

        self.code.input.setFocus()
        if auto_request:
            QTimer.singleShot(0, self._send_code)

    def _send_code(self):
        self.resend_btn.setEnabled(False)
        self._say(self.fb, "Sending a code to your email…", ok=True)
        spawn_worker(self.client, "POST", "/v1/auth/step-up/request", timeout=15,
                     parent=self, track=self._workers,
                     on_ok=self._on_sent, on_err=self._on_send_failed)

    def _on_sent(self, data):
        self.resend_btn.setEnabled(True)
        to = data.get("email") if isinstance(data, dict) else None
        self._say(self.fb, f"Code sent to {to}." if to else "Code sent.", ok=True)

    def _on_send_failed(self, err: str):
        self.resend_btn.setEnabled(True)
        self._say(self.fb, f"Couldn't send the code: {self._detail(err)}")

    def _confirm(self):
        if not self.confirm_btn.isEnabled():
            return
        code = self.code.text()
        if len(code) != 6 or not code.isdigit():
            self._mark(self.code, True)
            self._say(self.fb, "Enter the 6-digit code from your email")
            self.code.focus()
            return
        self._mark(self.code, False)
        self.confirm_btn.setEnabled(False)
        self.confirm_btn.setText("Confirming…")
        spawn_worker(self.client, "POST", "/v1/auth/step-up/confirm",
                     body={"code": code}, timeout=15, parent=self,
                     track=self._workers, on_ok=self._on_ok, on_err=self._on_err)

    def _reset_button(self):
        self.confirm_btn.setText("Confirm")
        self.confirm_btn.setEnabled(True)

    def _on_ok(self, _data):
        self._reset_button()
        self.accept()

    def _on_err(self, err: str):
        self._reset_button()
        self._mark(self.code, True)
        # Server wording + the recovery path next to it.
        self._say(self.fb, f"{self._detail(err)} — resend to get a fresh one")
        self.code.focus()

    def done(self, result: int):
        shutdown_workers(self._workers, wait_ms=600)
        super().done(result)


class LoginWindow(_AuthCard):
    """The desktop auditor sign-in: password (+ optional emailed OTP), forgot
    password, or an organization API key bridged to a session."""

    signed_in = pyqtSignal(dict)

    PAGE_LOGIN, PAGE_MFA, PAGE_FORGOT, PAGE_KEY = 0, 1, 2, 3

    def __init__(self, client: FoxyClient, settings=None, parent=None):
        super().__init__("Sign in — Foxy Audit", parent)
        self.client = client
        self.settings = settings
        self._workers: set = set()
        self._mfa_email = None
        self._remember = True

        self.title_lbl, self.eyebrow_lbl = self.add_header(
            "key", "Foxy Audit", "auditor sign-in")

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_login())
        self.stack.addWidget(self._build_mfa())
        self.stack.addWidget(self._build_forgot())
        self.stack.addWidget(self._build_key())
        self.body.addWidget(self.stack)
        self._fit_stack()

        self.email.input.setFocus()

    # ── page 1 — email + password (the web's #authForm) ──
    def _build_login(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        self.email = _Field("email", placeholder="you@company.com")
        self.email.input.returnPressed.connect(self._login)
        v.addWidget(self.email)
        v.addSpacing(13)

        self.password = _Field("password", password=True, placeholder="••••••••")
        self.password.input.returnPressed.connect(self._login)
        v.addWidget(self.password)

        self.remember = QCheckBox("Keep me signed in on this device (30 days)")
        # Unchecked by default, matching the web gate's #authRemember — a 30-day
        # session is opt-in, not something a shared machine gets by surprise.
        self.remember.setChecked(False)
        self.remember.setMinimumHeight(28)
        v.addSpacing(13)
        v.addWidget(self.remember)

        self.login_fb = self._feedback_label()
        v.addSpacing(10)
        v.addWidget(self.login_fb)

        self.login_btn = QPushButton("Sign in")
        self.login_btn.setObjectName("pri")
        self.login_btn.setMinimumHeight(46)
        self.login_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.login_btn.setDefault(True)
        self.login_btn.clicked.connect(self._login)
        v.addSpacing(6)
        v.addWidget(self.login_btn)

        self.forgot_link = QPushButton("Forgot password?")
        self.forgot_link.setObjectName("link")
        self.forgot_link.setCursor(Qt.CursorShape.PointingHandCursor)
        self.forgot_link.clicked.connect(self._go_forgot)
        v.addSpacing(6)
        v.addWidget(self.forgot_link)

        self.key_link = QPushButton("Sign in with an organization API key")
        self.key_link.setObjectName("link")
        self.key_link.setCursor(Qt.CursorShape.PointingHandCursor)
        self.key_link.clicked.connect(lambda: self._go(
            self.PAGE_KEY, "Sign in with a key", "organization api key"))
        v.addWidget(self.key_link)

        # Honest limitation (plan §5.3) — no button that cannot work.
        sso = QLabel("Google or company SSO sign-in happens in a browser, so it "
                     "isn't available here. Use an API key, or set a password in "
                     "the web dashboard.")
        sso.setObjectName("hint")
        sso.setWordWrap(True)
        v.addSpacing(8)
        v.addWidget(sso)
        return page

    # ── page 2 — emailed OTP (the web's #authMfa) ──
    def _build_mfa(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        self.mfa_code = _Field("verification code", placeholder="123456", code=True)
        self.mfa_code.input.returnPressed.connect(self._verify_mfa)
        v.addWidget(self.mfa_code)

        self.mfa_fb = self._feedback_label()
        v.addSpacing(10)
        v.addWidget(self.mfa_fb)

        self.mfa_btn = QPushButton("Verify & sign in")
        self.mfa_btn.setObjectName("pri")
        self.mfa_btn.setMinimumHeight(46)
        self.mfa_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mfa_btn.clicked.connect(self._verify_mfa)
        v.addSpacing(6)
        v.addWidget(self.mfa_btn)

        back = QPushButton("← back to sign-in")
        back.setObjectName("link")
        back.setCursor(Qt.CursorShape.PointingHandCursor)
        back.clicked.connect(self._back_to_login)
        v.addSpacing(6)
        v.addWidget(back)
        return page

    # ── page 3 — forgot password (the web's #authForgot) ──
    def _build_forgot(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        self.forgot_email = _Field("email", placeholder="you@company.com")
        self.forgot_email.input.returnPressed.connect(self._forgot)
        v.addWidget(self.forgot_email)

        self.forgot_fb = self._feedback_label()
        v.addSpacing(10)
        v.addWidget(self.forgot_fb)

        self.forgot_btn = QPushButton("Send reset link")
        self.forgot_btn.setObjectName("pri")
        self.forgot_btn.setMinimumHeight(46)
        self.forgot_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.forgot_btn.clicked.connect(self._forgot)
        v.addSpacing(6)
        v.addWidget(self.forgot_btn)

        note = QLabel("The reset link opens the web dashboard in your browser. "
                      "Come back here once your new password is set.")
        note.setObjectName("hint")
        note.setWordWrap(True)
        v.addSpacing(8)
        v.addWidget(note)

        back = QPushButton("← back to sign-in")
        back.setObjectName("link")
        back.setCursor(Qt.CursorShape.PointingHandCursor)
        back.clicked.connect(self._back_to_login)
        v.addWidget(back)
        return page

    # ── page 4 — organization API key (desktop-only) ──
    def _build_key(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        self.key = _Field("organization api key", password=True,
                          placeholder="paste your key")
        self.key.input.returnPressed.connect(self._key_sign_in)
        v.addWidget(self.key)

        self.key_fb = self._feedback_label()
        v.addSpacing(10)
        v.addWidget(self.key_fb)

        self.key_btn = QPushButton("Connect")
        self.key_btn.setObjectName("pri")
        self.key_btn.setMinimumHeight(46)
        self.key_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.key_btn.clicked.connect(self._key_sign_in)
        v.addSpacing(6)
        v.addWidget(self.key_btn)

        note = QLabel("Foxy exchanges the key for a signed-in session. The key "
                      "is stored in your operating system's keychain — never in "
                      "a settings file.")
        note.setObjectName("hint")
        note.setWordWrap(True)
        v.addSpacing(8)
        v.addWidget(note)

        back = QPushButton("← back to sign-in")
        back.setObjectName("link")
        back.setCursor(Qt.CursorShape.PointingHandCursor)
        back.clicked.connect(self._back_to_login)
        v.addWidget(back)
        return page

    # ── navigation ──
    def _fit_stack(self):
        """A QStackedWidget reserves the tallest page's height, which left the
        short pages (MFA, key) floating in a void. Let only the visible page
        claim space so the card shrinks to whatever step you're on."""
        current = self.stack.currentWidget()
        for i in range(self.stack.count()):
            w = self.stack.widget(i)
            policy = w.sizePolicy()
            policy.setVerticalPolicy(
                QSizePolicy.Policy.Preferred if w is current
                else QSizePolicy.Policy.Ignored)
            w.setSizePolicy(policy)
        if current is not None:
            current.adjustSize()
        self.stack.adjustSize()

    def _go(self, page: int, title: str, eyebrow: str):
        self.stack.setCurrentIndex(page)
        self.title_lbl.setText(title)
        self.eyebrow_lbl.setText(eyebrow.upper())
        self._fit_stack()
        {self.PAGE_LOGIN: self.email, self.PAGE_MFA: self.mfa_code,
         self.PAGE_FORGOT: self.forgot_email, self.PAGE_KEY: self.key}[page].focus()
        self.card.adjustSize()
        self.adjustSize()

    def _back_to_login(self):
        # Mirrors the web's mfaBack: clear the password and the error slot.
        self._mfa_email = None
        self.password.input.clear()
        self._say(self.login_fb, "")
        self._go(self.PAGE_LOGIN, "Foxy Audit", "auditor sign-in")

    def _go_forgot(self):
        self.forgot_email.input.setText(self.email.text())
        self._say(self.forgot_fb, "")
        self._go(self.PAGE_FORGOT, "Reset your password", "we'll email you a link")

    # ── sign in ──
    def _login(self):
        if not self.login_btn.isEnabled():
            return              # a request is already in flight (Enter held / double-click)
        email, pw = self.email.text(), self.password.input.text()
        if not _EMAIL_RE.match(email):
            self._mark(self.email, True, "Enter a valid email address")
            self._say(self.login_fb, "Enter a valid email address")
            self.email.focus()
            return
        self._mark(self.email, False)
        if not pw:
            self._mark(self.password, True, "Enter your password")
            self._say(self.login_fb, "Enter your password")
            self.password.focus()
            return
        self._mark(self.password, False)
        self._remember = self.remember.isChecked()
        self._mfa_email = email
        self._say(self.login_fb, "")
        self.login_btn.setEnabled(False)
        self.login_btn.setText("Signing in…")
        spawn_worker(self.client, "POST", "/v1/auth/login",
                     body={"email": email, "password": pw,
                           "remember_me": self._remember},
                     timeout=15, parent=self, track=self._workers,
                     on_ok=self._on_login_ok, on_err=self._on_login_err)

    def _on_login_ok(self, data):
        self.login_btn.setText("Sign in")
        self.login_btn.setEnabled(True)
        if isinstance(data, dict) and data.get("mfa_required"):
            self._mfa_email = data.get("email") or self._mfa_email
            self.mfa_code.input.clear()
            self._say(self.mfa_fb, "")
            self._go(self.PAGE_MFA, "Check your email", "enter the 6-digit code")
            return
        self.password.input.clear()
        self.signed_in.emit(data if isinstance(data, dict) else {})
        self.accept()

    def _on_login_err(self, err: str):
        self.login_btn.setText("Sign in")
        self.login_btn.setEnabled(True)
        # Same rule as the web gate: a 403 carries an actionable detail (IP
        # allow-list, disabled account, suspended/deleted workspace) and is
        # shown verbatim; anything else stays deliberately vague so the form
        # never becomes an account-enumeration oracle.
        if err.startswith("HTTP 403"):
            self._say(self.login_fb, self._detail(err))
        elif err.startswith("HTTP "):
            self._say(self.login_fb, "Invalid email or password")
            self.password.focus()
        else:
            self._say(self.login_fb, "Could not reach the server")

    # ── MFA ──
    def _verify_mfa(self):
        if not self.mfa_btn.isEnabled():
            return
        code = self.mfa_code.text()
        if len(code) != 6 or not code.isdigit():
            self._mark(self.mfa_code, True, "Enter the 6-digit code")
            self._say(self.mfa_fb, "Enter the 6-digit code")
            self.mfa_code.focus()
            return
        self._mark(self.mfa_code, False)
        self.mfa_btn.setEnabled(False)
        self.mfa_btn.setText("Verifying…")
        spawn_worker(self.client, "POST", "/v1/auth/mfa",
                     body={"email": self._mfa_email, "code": code,
                           "remember_me": self._remember},
                     timeout=15, parent=self, track=self._workers,
                     on_ok=self._on_mfa_ok, on_err=self._on_mfa_err)

    def _on_mfa_ok(self, data):
        self.mfa_btn.setText("Verify & sign in")
        self.mfa_btn.setEnabled(True)
        self.password.input.clear()
        self.signed_in.emit(data if isinstance(data, dict) else {})
        self.accept()

    def _on_mfa_err(self, err: str):
        self.mfa_btn.setText("Verify & sign in")
        self.mfa_btn.setEnabled(True)
        self._mark(self.mfa_code, True)
        self._say(self.mfa_fb, self._detail(err) if err.startswith("HTTP ")
                  else "Could not reach the server")
        self.mfa_code.focus()

    # ── forgot password ──
    def _forgot(self):
        if not self.forgot_btn.isEnabled():
            return
        email = self.forgot_email.text()
        if not _EMAIL_RE.match(email):
            self._mark(self.forgot_email, True, "Enter a valid email address")
            self._say(self.forgot_fb, "Enter a valid email address")
            self.forgot_email.focus()
            return
        self._mark(self.forgot_email, False)
        self.forgot_btn.setEnabled(False)
        self.forgot_btn.setText("Sending…")
        spawn_worker(self.client, "POST", "/v1/auth/forgot-password",
                     body={"email": email}, timeout=15, parent=self,
                     track=self._workers, on_ok=self._on_forgot_ok,
                     on_err=self._on_forgot_err)

    def _reset_forgot_button(self):
        self.forgot_btn.setText("Send reset link")
        self.forgot_btn.setEnabled(True)

    def _on_forgot_ok(self, _data):
        self._reset_forgot_button()
        # Identical whether or not the address exists — the endpoint is
        # enumeration-safe and the UI must not leak what it hides.
        self._say(self.forgot_fb,
                  "If that email has an account, a reset link is on its way.",
                  ok=True)

    def _on_forgot_err(self, err: str):
        self._reset_forgot_button()
        # Only a transport failure is reported; an HTTP error still gets the
        # neutral message, because saying more would reveal the account exists.
        self._say(self.forgot_fb, "Could not reach the server"
                  if not err.startswith("HTTP ") else
                  "If that email has an account, a reset link is on its way.",
                  ok=err.startswith("HTTP "))

    # ── organization API key ──
    def _key_sign_in(self):
        if not self.key_btn.isEnabled():
            return              # the handoff worker is still running
        key = self.key.text()
        if not key:
            self._mark(self.key, True, "Paste your organization API key")
            self._say(self.key_fb, "Paste your organization API key")
            self.key.focus()
            return
        self._mark(self.key, False)
        self.key_btn.setEnabled(False)
        self.key_btn.setText("Connecting…")
        self._say(self.key_fb, "")
        self._submitted_key = key      # what we actually authenticated with
        self._key_worker = _KeySignInWorker(self.client, key, self)
        self._workers.add(self._key_worker)
        self._key_worker.succeeded.connect(self._on_key_ok)
        self._key_worker.failed.connect(self._on_key_err)
        self._key_worker.finished.connect(self._forget_key_worker)
        self._key_worker.start()

    def _forget_key_worker(self):
        w = getattr(self, "_key_worker", None)
        if w is not None:
            self._workers.discard(w)
            w.deleteLater()

    def _reset_key_button(self):
        self.key_btn.setText("Connect")
        self.key_btn.setEnabled(True)

    def _on_key_ok(self, data: dict):
        self._reset_key_button()
        if self.settings is not None and not self.settings.set_org_api_key(self._submitted_key):
            # Signed in, but the key did not persist — say so rather than let
            # the user discover it on the next launch.
            self._say(self.key_fb, "Signed in, but the key couldn't be saved to "
                                   "the OS keychain — you'll need to paste it again.")
            QTimer.singleShot(2500, lambda: self._finish_key(data))
            return
        self._finish_key(data)

    def _finish_key(self, data: dict):
        self.signed_in.emit(data if isinstance(data, dict) else {})
        self.accept()

    def _on_key_err(self, err: str):
        self._reset_key_button()
        self._mark(self.key, True)
        self._say(self.key_fb, f"{self._detail(err)} — check the key and the "
                               f"backend URL in Settings")
        self.key.focus()

    def done(self, result: int):
        shutdown_workers(self._workers, wait_ms=800)
        super().done(result)


class _KeySignInWorker(QThread):
    """Runs the two-step handoff mint + redeem off the UI thread."""

    succeeded = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, client: FoxyClient, org_key: str, parent=None):
        super().__init__(parent)
        self._client = client
        self._key = org_key

    def run(self):
        try:
            data = self._client.sign_in_with_org_key(self._key)
            self.succeeded.emit(data if isinstance(data, dict) else {})
        except Exception as e:            # noqa: BLE001 — surface, never crash the UI
            self.failed.emit(str(e))


# ── standalone preview ──────────────────────────────────────────────────────
if __name__ == "__main__":
    app = QApplication(sys.argv)
    from fox_settings import FoxSettings
    s = FoxSettings()
    w = LoginWindow(FoxyClient(s), settings=s)
    w.center_on_screen()
    w.show_animated()
    sys.exit(app.exec())
