"""Foxy Audit desktop — the Settings page, admin half (D11b).

The four admin cards of the web's `#page-settings`
(foxy-audit-premium.html:1669-1706): team, account activity, outbound
webhooks and enterprise SSO. Built onto the same `SettingsSections` owner as
the account half, from `foxy_tokens` only.

**Where this deliberately differs from the web, and why.**

*A member sees the cards.* The web sets `display:none` on all four for a
non-admin (html:2914-2916, 2971), so a member has no way to know team
management exists. Every endpoint behind them is `require_role("admin")`, so
each card renders here with the reason in its status strip — the app's own
convention from Policy and Billing. The three cards with write controls also
disable them ("+ add auditor", `wh_form`, `sso_form`); Account activity's only
control is its refresh, which is idempotent and re-states the same notice.

*Adding a user is a dialog, not two prompts.* `addUser` chains two
`window.prompt` calls; D9 already replaced the same pattern for API keys.

*The SSO callback URL comes from the configured backend.* The web prints
`location.origin`; a desktop app has no origin, and the server derives the
real value from the request it receives.

No secret arriving from the server is rendered by anything here: rows carry
the 11-character `secret_prefix` and never the value, and a temp password or
`whsec_` secret goes from its response straight into D9's `ShownOnceDialog`.
The one field that holds a secret is the SSO client secret the admin types —
password echo, never populated from a response (the route does not return it),
and cleared by the save handler either way.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QSizePolicy, QVBoxLayout, QWidget,
)

from foxy_tokens import BAD_RED, OK_GREEN, RADIUS, WARN_AMBER, WEB, pick_font
from home_page import _card, _elide_to_width, _label, _section_title
from export_page import _combo_qss, _field_label
from policy_page import _input_qss
from panel_state import StatusStrip
import settings_admin as sa

TONES = {"ok": OK_GREEN, "bad": BAD_RED, "warn": WARN_AMBER,
         "mute": WEB["muted"]}


# ── cards ───────────────────────────────────────────────────────────────────
def team_card(o) -> QWidget:
    """Team — dashboard users (html:1669-1672)."""
    card, lay = _card()
    head, o.team_add_btn = _section_title("Team — dashboard users",
                                          "+ add auditor")
    o.team_add_btn.setAccessibleName("Add a dashboard user to this workspace")
    o.team_add_btn.clicked.connect(lambda: o.add_team_user())
    lay.addWidget(head)
    lay.addWidget(_label(sa.INVITE_BLURB, size=10.5, colour=WEB["muted"],
                         wrap=True))
    o.team_rows = QVBoxLayout()
    o.team_rows.setSpacing(0)
    lay.addLayout(o.team_rows)
    o.team_strip = StatusStrip(compact=True)
    o.team_strip.retry.connect(lambda: o.refresh_team())
    lay.addWidget(o.team_strip)
    return card


def audit_card(o) -> QWidget:
    """Account activity (html:1673-1677)."""
    card, lay = _card()
    head, o.acct_audit_btn = _section_title("Account activity", "↻ refresh")
    o.acct_audit_btn.setAccessibleName("Reload the account activity trail")
    o.acct_audit_btn.clicked.connect(lambda: o.refresh_account_audit())
    lay.addWidget(head)
    lay.addWidget(_label(sa.AUDIT_BLURB, size=10.5, colour=WEB["muted"],
                         wrap=True))
    o.acct_audit_rows = QVBoxLayout()
    o.acct_audit_rows.setSpacing(0)
    lay.addLayout(o.acct_audit_rows)
    o.acct_audit_strip = StatusStrip(compact=True)
    o.acct_audit_strip.retry.connect(lambda: o.refresh_account_audit())
    lay.addWidget(o.acct_audit_strip)
    return card


def webhook_card(o) -> QWidget:
    """Outbound webhooks (html:1678-1688)."""
    card, lay = _card()
    head, o.wh_refresh_btn = _section_title("Outbound webhooks", "↻ refresh")
    o.wh_refresh_btn.setAccessibleName("Reload the webhook list")
    o.wh_refresh_btn.clicked.connect(lambda: o.refresh_webhooks())
    lay.addWidget(head)
    lay.addWidget(_label(sa.WEBHOOK_BLURB, size=10.5, colour=WEB["muted"],
                         wrap=True))
    o.wh_rows = QVBoxLayout()
    o.wh_rows.setSpacing(0)
    lay.addLayout(o.wh_rows)
    o.wh_strip = StatusStrip(compact=True)
    o.wh_strip.retry.connect(lambda: o.refresh_webhooks())
    lay.addWidget(o.wh_strip)

    lay.addWidget(_divider())
    # Grouped so one `setEnabled(False)` covers the whole add form for a
    # member — Qt greys every descendant, which is the disabled-state rule.
    o.wh_form = QWidget()
    form = QVBoxLayout(o.wh_form)
    form.setContentsMargins(0, 0, 0, 0)
    form.setSpacing(8)
    form.addWidget(_field_label("Endpoint URL"))
    o.wh_url = QLineEdit()
    o.wh_url.setPlaceholderText("https://yourapp.com/foxy-events")
    o.wh_url.setAccessibleName("Webhook endpoint URL")
    o.wh_url.setMinimumHeight(44)
    o.wh_url.setStyleSheet(_input_qss())
    o.wh_url.returnPressed.connect(lambda: o.add_webhook())
    form.addWidget(o.wh_url)

    row = QHBoxLayout()
    row.setSpacing(14)
    o.wh_events = {}
    for value, label, default in sa.WEBHOOK_EVENTS:
        box = QCheckBox(label)
        box.setChecked(default)
        box.setAccessibleName(f"Send {label} events")
        box.setCursor(Qt.CursorShape.PointingHandCursor)
        box.setStyleSheet(_checkbox_qss())
        o.wh_events[value] = box
        row.addWidget(box)
    row.addStretch()
    o.wh_add_btn = QPushButton("Add webhook")
    o.wh_add_btn.setObjectName("ctaBtn")
    o.wh_add_btn.setMinimumHeight(44)
    o.wh_add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    o.wh_add_btn.clicked.connect(lambda: o.add_webhook())
    row.addWidget(o.wh_add_btn)
    form.addLayout(row)
    o.wh_status = _label("", size=10.5, colour=BAD_RED, wrap=True)
    o.wh_status.hide()
    form.addWidget(o.wh_status)
    lay.addWidget(o.wh_form)
    return card


def sso_card(o) -> QWidget:
    """Enterprise SSO (OIDC) (html:1689-1706)."""
    card, lay = _card()
    head = QHBoxLayout()
    head.setSpacing(8)
    head.addWidget(_label("Enterprise SSO (OIDC)", size=12, bold=True))
    head.addStretch()
    o.sso_status_chip = _status_chip(sa.SSO_NOT_SET_UP, "mute")
    head.addWidget(o.sso_status_chip)
    lay.addLayout(head)
    lay.addWidget(_label(sa.SSO_BLURB, size=10.5, colour=WEB["muted"],
                         wrap=True))
    # This card is a form, not a list, so it carries its own strip: it still
    # has to be able to say "couldn't ask" and offer a retry rather than show
    # empty fields that look like "no SSO configured".
    o.sso_strip = StatusStrip(compact=True)
    o.sso_strip.retry.connect(lambda: o.refresh_sso())
    lay.addWidget(o.sso_strip)

    # Grouped for the same reason the webhook add form is: one setEnabled call
    # greys the whole thing for a member.
    o.sso_form = QWidget()
    lay.addWidget(o.sso_form)
    lay = QVBoxLayout(o.sso_form)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(8)

    o.sso_fields = {}
    for key, label, placeholder in (
            ("domain", "Email domain", "acme.com"),
            ("issuer", "Issuer URL", "https://your-idp.okta.com"),
            ("client_id", "Client ID", "client id from your IdP")):
        lay.addWidget(_field_label(label))
        field = QLineEdit()
        field.setPlaceholderText(placeholder)
        field.setAccessibleName(label)
        field.setMinimumHeight(44)
        field.setStyleSheet(_input_qss())
        o.sso_fields[key] = field
        lay.addWidget(field)

    lay.addWidget(_field_label("Client secret"))
    o.sso_secret = QLineEdit()
    o.sso_secret.setEchoMode(QLineEdit.EchoMode.Password)
    o.sso_secret.setAccessibleName("Client secret")
    o.sso_secret.setMinimumHeight(44)
    o.sso_secret.setStyleSheet(_input_qss())
    lay.addWidget(o.sso_secret)
    # Placeholder and helper are filled by `_apply_sso`: whether this field is
    # optional depends on whether a secret is already stored.
    o.sso_secret_help = _label("", size=10, colour=WEB["muted"], wrap=True)
    lay.addWidget(o.sso_secret_help)

    o.sso_active = QCheckBox("Enabled — route this domain to the IdP")
    o.sso_active.setChecked(True)
    o.sso_active.setCursor(Qt.CursorShape.PointingHandCursor)
    o.sso_active.setStyleSheet(_checkbox_qss())
    lay.addWidget(o.sso_active)

    lay.addWidget(_field_label("Redirect / callback URL for your IdP"))
    o.sso_callback = QLabel(sa.SSO_CALLBACK_UNKNOWN)
    o.sso_callback.setWordWrap(True)
    o.sso_callback.setTextInteractionFlags(
        Qt.TextInteractionFlag.TextSelectableByMouse)
    o.sso_callback.setStyleSheet(
        f"color: {WEB['fox2']}; font-family: '{pick_font('mono')}';"
        f" font-size: 10.5px; background: {WEB['surf2']};"
        f" border: 2px solid {WEB['bc']}; border-radius: {RADIUS['sm']}px;"
        f" padding: 8px 10px;")
    lay.addWidget(o.sso_callback)

    o.sso_status = _label("", size=10.5, colour=BAD_RED, wrap=True)
    o.sso_status.hide()
    lay.addWidget(o.sso_status)

    row = QHBoxLayout()
    row.setSpacing(8)
    o.sso_save_btn = QPushButton("Save SSO")
    o.sso_save_btn.setObjectName("ctaBtn")
    o.sso_save_btn.setMinimumHeight(44)
    o.sso_save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    o.sso_save_btn.clicked.connect(lambda: o.save_sso())
    row.addWidget(o.sso_save_btn)
    o.sso_remove_btn = QPushButton("Remove")
    o.sso_remove_btn.setObjectName("dangerBtn")
    o.sso_remove_btn.setMinimumHeight(44)
    o.sso_remove_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    o.sso_remove_btn.setStyleSheet(_danger_qss())
    o.sso_remove_btn.clicked.connect(lambda: o.remove_sso())
    row.addWidget(o.sso_remove_btn)
    row.addStretch()
    lay.addLayout(row)
    return card


# ── rows ────────────────────────────────────────────────────────────────────
def user_row(row: dict, handlers: dict) -> QWidget:
    """One dashboard user. Only the actions the server would actually accept
    are drawn — `team_rows()` decided which those are."""
    frame = QFrame()
    frame.setObjectName("userRow")
    frame.setStyleSheet(
        f"QFrame#userRow {{ background: transparent; border: none;"
        f" border-bottom: 2px solid {WEB['bc']}; }}")
    lay = QHBoxLayout(frame)
    lay.setContentsMargins(2, 9, 2, 9)
    lay.setSpacing(10)

    col = QVBoxLayout()
    col.setSpacing(2)
    # A disabled account is history, not access — dimmed, exactly like a
    # revoked key, so the distinction never depends on reading the pill.
    ink = WEB["muted2"] if row["disabled"] else WEB["ink"]
    name = _label(row["email"], size=11.5, bold=True, colour=ink)
    name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    _elide_to_width(name, row["email"])
    col.addWidget(name)
    col.addWidget(_label(row["meta"], size=9.5, mono=True,
                         colour=WEB["muted"]))
    lay.addLayout(col, 1)

    if row["is_me"]:
        lay.addWidget(_status_chip("you", "ok"))
    else:
        lay.addWidget(_status_chip(row["status"], row["tone"]))

    if row["last_admin"]:
        note = _label("last admin", size=9, mono=True, colour=WARN_AMBER)
        note.setToolTip("An org must keep one active admin, so this account "
                        "cannot be demoted.")
        lay.addWidget(note)

    if row["can_change_role"]:
        lay.addWidget(_row_btn(
            f"make {row['next_role']}", "ghostBtn",
            f"Change {row['email']} to {row['next_role']}",
            lambda: handlers["role"](row)))
    if row["can_reinvite"]:
        lay.addWidget(_row_btn(
            "re-invite", "ghostBtn", f"Re-send the invite email to {row['email']}",
            lambda: handlers["reinvite"](row)))
    if row["can_disable"]:
        lay.addWidget(_row_btn(
            "disable", "dangerBtn", f"Disable {row['email']}",
            lambda: handlers["disable"](row), danger=True))
    if row["can_enable"]:
        lay.addWidget(_row_btn(
            "enable", "ghostBtn", f"Re-enable {row['email']}",
            lambda: handlers["enable"](row)))
    return frame


def audit_row(row: dict) -> QWidget:
    """One account change. Absolute local time, as the web renders it
    (`toLocaleString`, html:3051) — this is a record, not a feed."""
    frame = QFrame()
    frame.setObjectName("auditRow")
    frame.setStyleSheet(
        f"QFrame#auditRow {{ background: transparent; border: none;"
        f" border-bottom: 2px solid {WEB['bc']}; }}")
    lay = QHBoxLayout(frame)
    lay.setContentsMargins(2, 8, 2, 8)
    lay.setSpacing(10)
    col = QVBoxLayout()
    col.setSpacing(2)
    head = QHBoxLayout()
    head.setSpacing(6)
    head.addWidget(_label(row["label"], size=11.5, bold=True))
    if row["target"]:
        target = _label(f"· {row['target']}", size=11, colour=WEB["muted"])
        target.setSizePolicy(QSizePolicy.Policy.Ignored,
                             QSizePolicy.Policy.Preferred)
        _elide_to_width(target, f"· {row['target']}")
        head.addWidget(target, 1)
    else:
        head.addStretch()
    col.addLayout(head)
    if row["actor"]:
        col.addWidget(_label(row["actor"], size=9.5, mono=True,
                             colour=WEB["muted"]))
    lay.addLayout(col, 1)
    from console_chrome import local_datetime
    when = _label(local_datetime(row["when"]) or "", size=9.5, mono=True,
                  colour=WEB["muted"])
    lay.addWidget(when, 0, Qt.AlignmentFlag.AlignTop)
    return frame


def webhook_row(row: dict, handlers: dict) -> QWidget:
    """One subscription. Carries the secret PREFIX only — the full `whsec_`
    value exists once, in the create response, and never reaches a row."""
    frame = QFrame()
    frame.setObjectName("whRow")
    frame.setStyleSheet(
        f"QFrame#whRow {{ background: transparent; border: none;"
        f" border-bottom: 2px solid {WEB['bc']}; }}")
    lay = QHBoxLayout(frame)
    lay.setContentsMargins(2, 9, 2, 9)
    lay.setSpacing(10)
    col = QVBoxLayout()
    col.setSpacing(2)
    url = _label(row["url"], size=10.5, mono=True)
    url.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    _elide_to_width(url, row["url"])
    col.addWidget(url)
    sub = QHBoxLayout()
    sub.setSpacing(6)
    sub.addWidget(_label(row["status_text"], size=9.5, mono=True,
                         colour=TONES.get(row["tone"], WEB["muted"])))
    meta = _label(row["meta"], size=9.5, mono=True, colour=WEB["muted"])
    meta.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    _elide_to_width(meta, "· " + row["meta"])
    sub.addWidget(meta, 1)
    col.addLayout(sub)
    lay.addLayout(col, 1)
    lay.addWidget(_row_btn("test", "ghostBtn", f"Send a test event to {row['url']}",
                           lambda: handlers["test"](row)))
    lay.addWidget(_row_btn("remove", "dangerBtn", f"Remove the webhook {row['url']}",
                           lambda: handlers["remove"](row), danger=True))
    return frame


# ── dialogs ─────────────────────────────────────────────────────────────────
class AddUserDialog(QDialog):
    """Email + role. Replaces the web's two chained `window.prompt` calls,
    which cannot validate, cannot be cancelled halfway sensibly, and offer no
    hint that "admin" and "member" are the only two words that work."""

    def __init__(self, parent=None):
        super().__init__(parent)
        from PyQt6.QtWidgets import QComboBox
        self.setWindowTitle("Add a dashboard user")
        self.setModal(True)
        self.setMinimumWidth(440)
        self.setStyleSheet(f"QDialog {{ background: {WEB['surf']}; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 20)
        lay.setSpacing(10)
        lay.addWidget(_label("Add a dashboard user", size=15, bold=True))
        lay.addWidget(_label(sa.INVITE_BLURB, size=10.5, colour=WEB["muted"],
                             wrap=True))

        lay.addWidget(_field_label("Email"))
        self.email = QLineEdit()
        self.email.setPlaceholderText("auditor@acme.com")
        self.email.setAccessibleName("Email address of the new user")
        self.email.setMinimumHeight(44)
        self.email.setStyleSheet(_input_qss())
        lay.addWidget(self.email)

        lay.addWidget(_field_label("Role"))
        self.role = QComboBox()
        self.role.setAccessibleName("Role for the new user")
        self.role.setMinimumHeight(44)
        self.role.setCursor(Qt.CursorShape.PointingHandCursor)
        self.role.setStyleSheet(_combo_qss())
        for value in sa.VALID_ROLES:
            self.role.addItem(value, value)
        lay.addWidget(self.role)

        self.problem = _label("", size=10.5, colour=BAD_RED, wrap=True)
        self.problem.hide()
        lay.addWidget(self.problem)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch()
        cancel = QPushButton("Cancel")
        cancel.setObjectName("ghostBtn")
        cancel.setMinimumHeight(44)
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        self.add = QPushButton("Send invite")
        self.add.setObjectName("ctaBtn")
        self.add.setMinimumHeight(44)
        self.add.setDefault(True)
        self.add.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add.clicked.connect(self._submit)
        buttons.addWidget(self.add)
        lay.addLayout(buttons)
        self.email.returnPressed.connect(self._submit)

    def _submit(self):
        problem = sa.invite_problem(self.email.text(), self.values()[1])
        # Validating here rather than after the round-trip: the field is still
        # on screen and still focusable, which it is not once the dialog closes.
        self.problem.setText(problem or "")
        self.problem.setVisible(bool(problem))
        self.problem.setAccessibleName(problem or "")
        if problem:
            self.email.setFocus()
            return
        self.accept()

    def values(self) -> tuple[str, str]:
        return self.email.text().strip(), str(self.role.currentData() or "member")


# ── pieces ──────────────────────────────────────────────────────────────────
def _row_btn(text: str, object_name: str, accessible: str, on_click,
             *, danger: bool = False) -> QPushButton:
    btn = QPushButton(text)
    btn.setObjectName(object_name)
    btn.setMinimumHeight(44)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setAccessibleName(accessible)
    if danger:
        btn.setStyleSheet(_danger_qss())
    btn.clicked.connect(lambda _checked=False: on_click())
    return btn


def _status_chip(text: str, tone: str) -> QLabel:
    chip = QLabel()
    chip.setObjectName("adminChip")
    set_status_chip(chip, text, tone)
    return chip


def set_status_chip(chip: QLabel, text: str, tone: str):
    """Retint an existing chip. Shared with the SSO card's live status so the
    built look and the updated look cannot drift apart."""
    colour = TONES.get(tone, WEB["muted"])
    chip.setText(text.upper())
    chip.setStyleSheet(
        f"QLabel#adminChip {{ color: {colour}; background: {WEB['surf3']};"
        f" border: 1.5px solid {colour}; border-radius: 10px;"
        f" padding: 2px 8px; font-family: '{pick_font('mono')}';"
        f" font-size: 9px; font-weight: 800; }}")


def _checkbox_qss() -> str:
    """The auth window's checkbox, restated for the console. Same 44 px row and
    the same visible focus ring — a checkbox reachable by keyboard has to show
    where the keyboard is."""
    return (
        f"QCheckBox {{ color: {WEB['ink2']}; font-size: 11px;"
        f" font-family: '{pick_font('disp')}'; spacing: 9px;"
        f" min-height: 44px; background: transparent; }}"
        f"QCheckBox::indicator {{ width: 16px; height: 16px;"
        f" border-radius: 4px; border: 2px solid {WEB['line']};"
        f" background: {WEB['bg']}; }}"
        f"QCheckBox::indicator:checked {{ background: {WEB['fox']};"
        f" border-color: {WEB['fox']}; }}"
        f"QCheckBox::indicator:focus {{ border: 2px solid {WEB['fox']}; }}"
        f"QCheckBox:focus {{ color: {WEB['fox2']}; }}"
        f"QCheckBox:disabled {{ color: {WEB['muted2']}; }}"
        f"QCheckBox::indicator:disabled {{ border-color: {WEB['surf3']};"
        f" background: {WEB['surf2']}; }}")


def _divider() -> QFrame:
    line = QFrame()
    line.setObjectName("adminDivider")
    line.setFixedHeight(2)
    line.setStyleSheet(f"QFrame#adminDivider {{ background: {WEB['bc']};"
                       f" border: none; }}")
    return line


def _danger_qss() -> str:
    """The account half's destructive-button skin, reused rather than restated
    — two copies would drift and one of them would stop looking destructive
    when disabled. Imported inside the call because `settings_page` imports
    this module at the top to assemble the page."""
    from settings_page import _danger_qss as shared
    return shared()
