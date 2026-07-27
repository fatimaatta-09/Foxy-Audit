"""Foxy Audit desktop — the Settings page, account half (D11a).

The web's `#page-settings` (foxy-audit-premium.html:1546-1670), rebuilt with
foxy_tokens: identity, password, two-factor, data & privacy, access control,
devices, recent logins, trust badge, what-Foxy-stores, and the danger zone.
The admin half (team, account activity, webhooks, SSO) lands in D11b.

**Not ported, on purpose:** the web's theme toggle and skin picker — the
desktop look is locked (`foxy_tokens` has no second palette) — and the web's
"download the desktop app" card, which is replaced by a line saying you are
already in it.

Secrets on this page (the current password, the 2FA code, the badge token)
live in their field until the request is built and are cleared afterwards,
success or failure. Nothing here writes one to QSettings or a log.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from foxy_tokens import BAD_RED, OK_GREEN, RADIUS, WARN_AMBER, WEB, pick_font
from home_page import _card, _label, scroll_qss
from export_page import _field_label
from policy_page import SafeguardRow, _input_qss
from panel_state import StatusStrip
import settings_data as sd

TONES = {"ok": OK_GREEN, "bad": BAD_RED, "warn": WARN_AMBER,
         "mute": WEB["muted"]}


class SettingsSections:
    """Builds the Settings page onto `owner` (the DashboardWindow)."""

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
        row = QHBoxLayout()
        row.setSpacing(16)
        left = QVBoxLayout()
        left.setSpacing(16)
        left.addWidget(self._identity())
        left.addWidget(self._password())
        left.addWidget(self._mfa())
        left.addWidget(self._access_control())
        left.addStretch()
        right = QVBoxLayout()
        right.setSpacing(16)
        right.addWidget(self._privacy())
        right.addWidget(self._devices())
        right.addWidget(self._badge())
        right.addWidget(self._stores())
        right.addStretch()
        row.addLayout(left, 1)
        row.addLayout(right, 1)
        v.addLayout(row)
        v.addWidget(self._danger())
        v.addStretch()

        scroll.setWidget(body)
        outer.addWidget(scroll)
        o.settings_scroll = scroll
        return page

    def _head(self) -> QWidget:
        col = QWidget()
        lay = QVBoxLayout(col)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)
        lay.addWidget(_label("● PREFERENCES", size=10, bold=True,
                             colour=WEB["fox2"], mono=True, spacing=1.5))
        lay.addWidget(_label("Settings.", size=26, bold=True))
        lay.addWidget(_label("Your account, this workspace's access rules, and "
                             "what Foxy keeps.", size=12, colour=WEB["muted"],
                             wrap=True))
        return col

    # ── identity ────────────────────────────────────────────────────────────
    def _identity(self) -> QWidget:
        o = self.o
        card, lay = _card()
        lay.addWidget(_label("Account & identity", size=12, bold=True))

        lay.addWidget(_field_label("Display name  (your avatar and greeting)"))
        row = QHBoxLayout()
        row.setSpacing(8)
        o.set_name = QLineEdit()
        o.set_name.setPlaceholderText("e.g. Ada Lovelace")
        o.set_name.setMaxLength(sd.NAME_MAX)
        o.set_name.setAccessibleName("Display name")
        o.set_name.setMinimumHeight(44)
        o.set_name.setStyleSheet(_input_qss())
        o.set_name.returnPressed.connect(lambda: o.save_display_name())
        row.addWidget(o.set_name, 1)
        o.set_name_btn = QPushButton("Save")
        o.set_name_btn.setObjectName("ctaBtn")
        o.set_name_btn.setMinimumHeight(44)
        o.set_name_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        o.set_name_btn.clicked.connect(lambda: o.save_display_name())
        row.addWidget(o.set_name_btn)
        lay.addLayout(row)
        o.set_name_status = _label("", size=10, mono=True, colour=WEB["muted"],
                                   wrap=True)
        lay.addWidget(o.set_name_status)

        o.set_readonly = {}
        for key, label in (("email", "Email"), ("role", "Role"),
                           ("org_id", "Organization id")):
            lay.addWidget(_field_label(label))
            field = QLineEdit()
            field.setReadOnly(True)
            field.setAccessibleName(f"{label} (read only)")
            field.setMinimumHeight(44)
            # A read-only field styled like an editable one invites typing and
            # then silently discards it. Same rule as a disabled button: if it
            # does not take input, it must not look like it does.
            field.setStyleSheet(
                _input_qss()
                + f"QLineEdit {{ background: {WEB["surf3"]};"
                  f" color: {WEB["muted"]}; border-style: dashed; }}")
            o.set_readonly[key] = field
            lay.addWidget(field)
        lay.addWidget(_label("Email, role and organization are set by a "
                             "workspace admin.", size=10,
                             colour=WEB["muted"], wrap=True))
        return card

    # ── password ────────────────────────────────────────────────────────────
    def _password(self) -> QWidget:
        o = self.o
        card, lay = _card()
        lay.addWidget(_label("Change password", size=12, bold=True))
        o.set_pw_fields = {}
        for key, label, hint in (
                ("current", "Current password", "current-password"),
                ("new", "New password", "new-password")):
            lay.addWidget(_field_label(label))
            field = QLineEdit()
            field.setEchoMode(QLineEdit.EchoMode.Password)
            field.setAccessibleName(label)
            field.setMinimumHeight(44)
            field.setStyleSheet(_input_qss())
            o.set_pw_fields[key] = field
            lay.addWidget(field)
        o.set_pw_status = _label("", size=10.5, colour=BAD_RED, wrap=True)
        o.set_pw_status.hide()
        lay.addWidget(o.set_pw_status)
        o.set_pw_btn = QPushButton("Change password")
        o.set_pw_btn.setObjectName("ctaBtn")
        o.set_pw_btn.setMinimumHeight(44)
        o.set_pw_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        o.set_pw_btn.clicked.connect(lambda: o.change_password())
        lay.addWidget(o.set_pw_btn)
        return card

    # ── two-factor ──────────────────────────────────────────────────────────
    def _mfa(self) -> QWidget:
        o = self.o
        card, lay = _card()
        lay.addWidget(_label("Two-factor authentication", size=12, bold=True))
        o.mfa_state = _label(sd.MFA_OFF, size=11, colour=WEB["muted"],
                             wrap=True)
        lay.addWidget(o.mfa_state)

        # OFF → enable
        o.mfa_off_box = QWidget()
        off = QVBoxLayout(o.mfa_off_box)
        off.setContentsMargins(0, 0, 0, 0)
        off.setSpacing(8)
        o.mfa_enroll_btn = QPushButton("Enable 2FA")
        o.mfa_enroll_btn.setObjectName("ctaBtn")
        o.mfa_enroll_btn.setMinimumHeight(44)
        o.mfa_enroll_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        o.mfa_enroll_btn.clicked.connect(lambda: o.mfa_enroll())
        off.addWidget(o.mfa_enroll_btn)
        o.mfa_confirm_box = QWidget()
        conf = QVBoxLayout(o.mfa_confirm_box)
        conf.setContentsMargins(0, 0, 0, 0)
        conf.setSpacing(8)
        conf.addWidget(_field_label("Enter the 6-digit code we emailed you"))
        o.mfa_code = QLineEdit()
        o.mfa_code.setMaxLength(6)
        o.mfa_code.setPlaceholderText("123456")
        o.mfa_code.setAccessibleName("Two-factor code")
        o.mfa_code.setMinimumHeight(44)
        o.mfa_code.setAlignment(Qt.AlignmentFlag.AlignCenter)
        o.mfa_code.setStyleSheet(
            _input_qss() + "QLineEdit { letter-spacing: 6px;"
                           " font-size: 16px; font-weight: 800; }")
        o.mfa_code.returnPressed.connect(lambda: o.mfa_confirm())
        conf.addWidget(o.mfa_code)
        o.mfa_confirm_btn = QPushButton("Confirm & turn on")
        o.mfa_confirm_btn.setObjectName("ctaBtn")
        o.mfa_confirm_btn.setMinimumHeight(44)
        o.mfa_confirm_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        o.mfa_confirm_btn.clicked.connect(lambda: o.mfa_confirm())
        conf.addWidget(o.mfa_confirm_btn)
        o.mfa_confirm_box.hide()
        off.addWidget(o.mfa_confirm_box)
        lay.addWidget(o.mfa_off_box)

        # ON → disable, behind the password
        o.mfa_on_box = QWidget()
        on = QVBoxLayout(o.mfa_on_box)
        on.setContentsMargins(0, 0, 0, 0)
        on.setSpacing(8)
        on.addWidget(_label(sd.MFA_DISABLE_WARNING, size=10.5,
                            colour=WARN_AMBER, wrap=True))
        on.addWidget(_field_label("Confirm your password to turn it off"))
        o.mfa_password = QLineEdit()
        o.mfa_password.setEchoMode(QLineEdit.EchoMode.Password)
        o.mfa_password.setAccessibleName("Password, to disable two-factor")
        o.mfa_password.setMinimumHeight(44)
        o.mfa_password.setStyleSheet(_input_qss())
        on.addWidget(o.mfa_password)
        o.mfa_disable_btn = QPushButton("Turn off 2FA")
        o.mfa_disable_btn.setObjectName("dangerBtn")
        o.mfa_disable_btn.setMinimumHeight(44)
        o.mfa_disable_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        o.mfa_disable_btn.setStyleSheet(_danger_qss())
        o.mfa_disable_btn.clicked.connect(lambda: o.mfa_disable())
        on.addWidget(o.mfa_disable_btn)
        o.mfa_on_box.hide()
        lay.addWidget(o.mfa_on_box)
        return card

    # ── data & privacy ──────────────────────────────────────────────────────
    def _privacy(self) -> QWidget:
        o = self.o
        card, lay = _card()
        lay.addWidget(_label("Data & privacy", size=12, bold=True))
        o.pref_rows = {}
        for key, title, desc, _default in sd.PREFERENCES:
            row = SafeguardRow({"key": key, "name": title, "desc": desc,
                                "severity": None})
            row.toggle.toggled.connect(
                lambda on, k=key: o.save_preference(k, on))
            o.pref_rows[key] = row
            lay.addWidget(row)
        return card

    # ── access control ──────────────────────────────────────────────────────
    def _access_control(self) -> QWidget:
        o = self.o
        card, lay = _card()
        lay.addWidget(_label("Access control", size=12, bold=True))
        lay.addWidget(_label(sd.IP_HELP, size=11, colour=WEB["muted"],
                             wrap=True))
        lay.addWidget(_label(sd.IP_LOCKOUT, size=10.5, colour=WARN_AMBER,
                             wrap=True))
        o.ip_allow = QLineEdit()
        o.ip_allow.setPlaceholderText("e.g. 203.0.113.4, 10.0.0.0/24")
        o.ip_allow.setAccessibleName("IP allow-list")
        o.ip_allow.setMinimumHeight(44)
        o.ip_allow.setStyleSheet(_input_qss())
        lay.addWidget(o.ip_allow)
        o.ip_status = _label("", size=10.5, colour=BAD_RED, wrap=True)
        o.ip_status.hide()
        lay.addWidget(o.ip_status)
        o.ip_save = QPushButton("Save IP allow-list")
        o.ip_save.setObjectName("ghostBtn")
        o.ip_save.setMinimumHeight(44)
        o.ip_save.setCursor(Qt.CursorShape.PointingHandCursor)
        o.ip_save.clicked.connect(lambda: o.save_ip_allowlist())
        lay.addWidget(o.ip_save)
        return card

    # ── devices ─────────────────────────────────────────────────────────────
    def _devices(self) -> QWidget:
        o = self.o
        card, lay = _card()
        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(_label("Devices & sessions", size=12, bold=True))
        head.addStretch()
        o.logout_all_btn = QPushButton("Log out everywhere")
        o.logout_all_btn.setObjectName("ghostBtn")
        o.logout_all_btn.setMinimumHeight(44)
        o.logout_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        o.logout_all_btn.clicked.connect(lambda: o.logout_everywhere())
        head.addWidget(o.logout_all_btn)
        lay.addLayout(head)
        o.device_rows = QVBoxLayout()
        o.device_rows.setSpacing(0)
        lay.addLayout(o.device_rows)
        o.device_empty = StatusStrip(compact=True)
        o.device_empty.retry.connect(lambda: o.refresh_settings())
        lay.addWidget(o.device_empty)
        return card

    # ── trust badge ─────────────────────────────────────────────────────────
    def _badge(self) -> QWidget:
        o = self.o
        card, lay = _card()
        lay.addWidget(_label("Trust badge", size=12, bold=True))
        lay.addWidget(_label(sd.BADGE_BLURB, size=11, colour=WEB["muted"],
                             wrap=True))
        row = QHBoxLayout()
        row.setSpacing(8)
        o.badge_btn = QPushButton("Generate badge")
        o.badge_btn.setObjectName("ctaBtn")
        o.badge_btn.setMinimumHeight(44)
        o.badge_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        o.badge_btn.clicked.connect(lambda: o.mint_badge())
        row.addWidget(o.badge_btn)
        o.badge_copy = QPushButton("Copy embed")
        o.badge_copy.setObjectName("ghostBtn")
        o.badge_copy.setMinimumHeight(44)
        o.badge_copy.setCursor(Qt.CursorShape.PointingHandCursor)
        o.badge_copy.clicked.connect(lambda: o.copy_badge_embed())
        o.badge_copy.hide()
        row.addWidget(o.badge_copy)
        o.badge_revoke = QPushButton("Revoke")
        o.badge_revoke.setObjectName("dangerBtn")
        o.badge_revoke.setMinimumHeight(44)
        o.badge_revoke.setCursor(Qt.CursorShape.PointingHandCursor)
        o.badge_revoke.setStyleSheet(_danger_qss())
        o.badge_revoke.clicked.connect(lambda: o.revoke_badge())
        o.badge_revoke.hide()
        row.addWidget(o.badge_revoke)
        row.addStretch()
        lay.addLayout(row)
        o.badge_link = QLabel("")
        o.badge_link.setOpenExternalLinks(True)
        o.badge_link.setWordWrap(True)
        o.badge_link.setStyleSheet(
            f"font-family: '{pick_font('mono')}'; font-size: 10px;"
            f" background: transparent;")
        o.badge_link.hide()
        lay.addWidget(o.badge_link)
        return card

    def _stores(self) -> QWidget:
        card, lay = _card()
        lay.addWidget(_label(sd.STORES_TITLE, size=12, bold=True))
        for title, body in sd.STORES:
            lay.addWidget(_label(title, size=11, bold=True))
            lay.addWidget(_label(body, size=10.5, colour=WEB["muted"],
                                 wrap=True))
        lay.addWidget(_divider())
        lay.addWidget(_label(sd.DESKTOP_CARD[0], size=11, bold=True,
                             colour=OK_GREEN))
        lay.addWidget(_label(sd.DESKTOP_CARD[1], size=10.5,
                             colour=WEB["muted"], wrap=True))
        return card

    # ── danger zone ─────────────────────────────────────────────────────────
    def _danger(self) -> QWidget:
        o = self.o
        card, lay = _card()
        card.setObjectName("dangerCard")
        card.setStyleSheet(
            f"QFrame#dangerCard {{ background: {WEB['surf']};"
            f" border: 2px solid {BAD_RED}; border-radius: {RADIUS['lg']}px; }}")
        lay.addWidget(_label("Danger zone", size=12, bold=True, colour=BAD_RED))
        lay.addWidget(_label(sd.DELETE_WARNING, size=11, colour=WEB["muted"],
                             wrap=True))
        row = QHBoxLayout()
        row.setSpacing(8)
        o.export_ledger_btn = QPushButton("Export the ledger")
        o.export_ledger_btn.setObjectName("ghostBtn")
        o.export_ledger_btn.setMinimumHeight(44)
        o.export_ledger_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        o.export_ledger_btn.clicked.connect(lambda: o.go("export"))
        row.addWidget(o.export_ledger_btn)
        o.export_account_btn = QPushButton("Export account data")
        o.export_account_btn.setObjectName("ghostBtn")
        o.export_account_btn.setMinimumHeight(44)
        o.export_account_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        o.export_account_btn.clicked.connect(lambda: o.export_account())
        row.addWidget(o.export_account_btn)
        row.addStretch()
        o.delete_workspace_btn = QPushButton("Delete workspace")
        o.delete_workspace_btn.setObjectName("dangerBtn")
        o.delete_workspace_btn.setMinimumHeight(44)
        o.delete_workspace_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        o.delete_workspace_btn.setStyleSheet(_danger_qss())
        o.delete_workspace_btn.clicked.connect(lambda: o.delete_workspace())
        row.addWidget(o.delete_workspace_btn)
        lay.addLayout(row)
        return card


# ── pieces ──────────────────────────────────────────────────────────────────
def device_row(row: dict, on_revoke) -> QWidget:
    frame = QFrame()
    frame.setObjectName("deviceRow")
    frame.setStyleSheet(
        f"QFrame#deviceRow {{ background: transparent; border: none;"
        f" border-bottom: 2px solid {WEB['bc']}; }}")
    lay = QHBoxLayout(frame)
    lay.setContentsMargins(2, 9, 2, 9)
    lay.setSpacing(10)
    col = QVBoxLayout()
    col.setSpacing(2)
    title = _label(row["agent"], size=11.5, bold=True)
    title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    title.setToolTip(row["agent"])
    col.addWidget(title)
    from console_chrome import local_date, relative_time
    seen = relative_time(row["last_seen_at"])
    meta = " · ".join(p for p in (
        row["ip"], f"signed in {local_date(row['created_at']) or sd.MISSING}",
        f"seen {seen}" if seen else "") if p)
    col.addWidget(_label(meta, size=9.5, mono=True, colour=WEB["muted"]))
    lay.addLayout(col, 1)
    if row["current"]:
        chip = QLabel("THIS DEVICE")
        chip.setObjectName("hereChip")
        chip.setStyleSheet(
            f"QLabel#hereChip {{ color: {OK_GREEN}; background: {WEB['surf3']};"
            f" border: 1.5px solid {OK_GREEN}; border-radius: 10px;"
            f" padding: 2px 8px; font-family: '{pick_font('mono')}';"
            f" font-size: 9px; font-weight: 800; }}")
        lay.addWidget(chip)
    elif on_revoke is not None:
        btn = QPushButton("revoke")
        btn.setObjectName("dangerBtn")
        btn.setMinimumHeight(44)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setAccessibleName(f"Sign out {row['agent']}")
        btn.setStyleSheet(_danger_qss())
        btn.clicked.connect(lambda _c=False, r=row: on_revoke(r))
        lay.addWidget(btn)
    return frame


def _divider() -> QFrame:
    line = QFrame()
    line.setObjectName("setDivider")
    line.setFixedHeight(2)
    line.setStyleSheet(f"QFrame#setDivider {{ background: {WEB['bc']};"
                       f" border: none; }}")
    return line


def _danger_qss() -> str:
    """Destructive controls read as destructive in every state — including
    disabled, which `#ctaBtn` had to be taught in D7."""
    return (f"QPushButton#dangerBtn {{ background: transparent;"
            f" color: {BAD_RED}; border: 2px solid {BAD_RED};"
            f" border-radius: {RADIUS['sm']}px; padding: 6px 14px;"
            f" font-family: '{pick_font('mono')}'; font-size: 10.5px;"
            f" font-weight: 800; }}"
            f"QPushButton#dangerBtn:hover {{ background: {BAD_RED};"
            f" color: #1a0900; }}"
            f"QPushButton#dangerBtn:focus {{ background: {WEB['surf3']}; }}"
            f"QPushButton#dangerBtn:disabled {{ color: {WEB['muted2']};"
            f" border-color: {WEB['surf3']}; background: transparent; }}")


class ConfirmNameDialog(QDialog):
    """Type-the-name confirmation for deleting the workspace.

    The web uses `window.prompt`, which a desktop app has no equivalent of and
    should not fake. What matters is preserved: the consequence is stated in
    full before the field, and the confirm button stays disabled until the
    typed name matches — so the gate cannot be passed by reflex.
    """

    def __init__(self, workspace: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(sd.DELETE_TITLE)
        self.setModal(True)
        self.setMinimumWidth(460)
        self.setStyleSheet(f"QDialog {{ background: {WEB['surf']}; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(10)
        lay.addWidget(_label(sd.DELETE_TITLE, size=14, bold=True,
                             colour=BAD_RED, wrap=True))
        lay.addWidget(_label(sd.DELETE_WARNING, size=11,
                             colour=WEB["ink2"], wrap=True))
        lay.addWidget(_label(sd.DELETE_PROMPT, size=10.5,
                             colour=WEB["muted"], wrap=True))
        self.field = QLineEdit()
        self.field.setPlaceholderText(workspace or "workspace name")
        self.field.setAccessibleName("Workspace name, to confirm deletion")
        self.field.setMinimumHeight(44)
        self.field.setStyleSheet(_input_qss())
        lay.addWidget(self.field)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addStretch()
        cancel = QPushButton("Cancel")
        cancel.setObjectName("ghostBtn")
        cancel.setMinimumHeight(44)
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        self.confirm = QPushButton("Delete workspace")
        self.confirm.setObjectName("dangerBtn")
        self.confirm.setMinimumHeight(44)
        self.confirm.setCursor(Qt.CursorShape.PointingHandCursor)
        self.confirm.setStyleSheet(_danger_qss())
        self.confirm.setEnabled(False)
        self.confirm.clicked.connect(self.accept)
        row.addWidget(self.confirm)
        lay.addLayout(row)

        self._workspace = workspace
        self.field.textChanged.connect(self._sync)

    def _sync(self, text: str):
        self.confirm.setEnabled(sd.delete_confirmed(text, self._workspace))

    def typed(self) -> str:
        return self.field.text().strip()
