"""Foxy Audit desktop — the Policy ruleset page (D7).

The web's `#page-policy` (foxy-audit-premium.html:1301-1381), rebuilt with
foxy_tokens. This is the one page in the console that WRITES tenant security
configuration, so two things are structural rather than left to care:

**Writes are gated before they are attempted, not after they are refused.**
`PUT /v1/policies` is admin-humans-only (policies.py:129-133) — an org API key
or a member session can read the ruleset and cannot change it. Rather than let
someone edit a form for a minute and meet a 403, every control is disabled and
a notice says who can change this. The server is still the authority; the UI
just stops setting people up to fail.

**A judge provider key is only ever in this process while it is being typed.**
The two key fields are write-only in both directions: nothing on a response
can reach them (`GET /v1/policies` returns booleans, never a key), and after a
successful save they are cleared, because the value they held has been handed
to the server and this app has no reason to keep it. No key is written to
QSettings, to a file, or to a log line — the same rule D9 holds for the org
key, and `test_d7_policy.py` pins it the same way.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QScrollArea, QSizePolicy, QSpinBox, QVBoxLayout, QWidget,
)

from foxy_tokens import BAD_RED, OK_GREEN, RADIUS, WARN_AMBER, WEB, pick_font
from home_page import _card, _label, scroll_qss
from export_page import _combo_qss, _field_label
from panel_state import StatusStrip
import policy_data as pd

TONES = {"ok": OK_GREEN, "bad": BAD_RED, "warn": WARN_AMBER,
         "mute": WEB["muted"]}


class PolicySections:
    """Builds the Policy page onto `owner` (the DashboardWindow)."""

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
        # Scoped: a selector-less `background: transparent` here cascades to
        # every descendant and outranks the ancestor sheet — that is what blanked
        # the segmented controls' checked pill from D4 until D5 found it.
        body.setStyleSheet("QWidget#pageBody { background: transparent; }")
        v = QVBoxLayout(body)
        v.setContentsMargins(22, 18, 22, 22)
        v.setSpacing(16)

        v.addWidget(self._head())
        v.addWidget(self._status_line())
        v.addWidget(self._read_only_notice())

        # Until the GET answers there is no ruleset to show, and a form full of
        # defaults would read as one — the D4 lesson, applied to a page where
        # the wrong reading is "this is what your workspace enforces".
        o.pol_state = StatusStrip()
        o.pol_state.retry.connect(lambda: o.refresh_policy())
        v.addWidget(o.pol_state)

        o.pol_form = QWidget()
        form = QVBoxLayout(o.pol_form)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(16)
        row = QHBoxLayout()
        row.setSpacing(16)
        row.addWidget(self._safeguards(), 1)
        row.addWidget(self._sensitivity(), 1)
        form.addLayout(row)
        form.addWidget(self._judge())
        v.addWidget(o.pol_form)
        o.pol_form.hide()

        v.addWidget(self._privacy())
        v.addStretch()

        scroll.setWidget(body)
        outer.addWidget(scroll)
        o.policy_scroll = scroll
        return page

    # ── head + save bar ─────────────────────────────────────────────────────
    def _head(self) -> QWidget:
        o = self.o
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        col = QVBoxLayout()
        col.setSpacing(3)
        col.addWidget(_label("● AI JUDGE", size=10, bold=True,
                             colour=WEB["fox2"], mono=True, spacing=1.5))
        col.addWidget(_label("Policy ruleset.", size=26, bold=True))
        col.addWidget(_label("What the Judge flags or blocks across your "
                             "workspace.", size=12, colour=WEB["muted"],
                             wrap=True))
        lay.addLayout(col, 1)

        o.pol_save = QPushButton("save ruleset")
        o.pol_save.setObjectName("ctaBtn")
        o.pol_save.setMinimumHeight(44)
        o.pol_save.setCursor(Qt.CursorShape.PointingHandCursor)
        o.pol_save.clicked.connect(lambda: o.save_policy())
        lay.addWidget(o.pol_save, 0, Qt.AlignmentFlag.AlignVCenter)
        return row

    def _status_line(self) -> QWidget:
        """The dirty/saving/saved line.

        The web keeps it beside the button (html:1306) because its strings are
        four words long. Ours has to be able to say why a save was refused —
        the missing-KEK case is three sentences — and squeezed into the header
        column that wrapped to six lines and crowded the title. Full width,
        directly under the head, is the same information in a shape that fits.
        """
        o = self.o
        o.pol_status = _label("", size=10.5, mono=True, colour=WEB["muted"],
                              wrap=True)
        o.pol_status.setSizePolicy(QSizePolicy.Policy.Ignored,
                                   QSizePolicy.Policy.Preferred)
        return o.pol_status

    def _read_only_notice(self) -> QWidget:
        """Why the form is disabled — shown instead of letting a 403 explain it.

        A member can see exactly what is enforced on their workspace; hiding
        the page would leave them unable to answer "what are we blocking?",
        which is a fair question for anyone whose prompts run through it.
        """
        o = self.o
        card, lay = _card()
        card.setObjectName("noticeCard")
        card.setStyleSheet(
            f"QFrame#noticeCard {{ background: {WEB['surf2']};"
            f" border: 2px solid {WEB['bc']};"
            f" border-radius: {RADIUS['lg']}px; }}")
        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(_label("read only", size=9, bold=True, mono=True,
                              colour=WARN_AMBER, spacing=0.8))
        head.addStretch()
        lay.addLayout(head)
        o.pol_notice = _label(pd.MEMBER_NOTICE, size=11, colour=WEB["muted"],
                              wrap=True)
        lay.addWidget(o.pol_notice)
        o.pol_notice_card = card
        card.hide()
        return card

    # ── content safeguards ──────────────────────────────────────────────────
    def _safeguards(self) -> QWidget:
        o = self.o
        card, lay = _card()
        lay.addWidget(_label("Content safeguards", size=12, bold=True))

        o.pol_search = QLineEdit()
        o.pol_search.setPlaceholderText("Search safeguards…")
        o.pol_search.setAccessibleName("Search safeguards")
        o.pol_search.setMinimumHeight(44)
        o.pol_search.setStyleSheet(_input_qss())
        o.pol_search.textChanged.connect(lambda _t: o.filter_safeguards())
        lay.addWidget(o.pol_search)

        o.pol_no_match = _label(pd.NO_MATCH, size=11, colour=WEB["muted"],
                                wrap=True)
        o.pol_no_match.hide()
        lay.addWidget(o.pol_no_match)

        o.pol_rows = {}
        for spec in pd.SAFEGUARDS:
            row = SafeguardRow(spec)
            row.toggle.toggled.connect(lambda _v: o.mark_policy_dirty())
            o.pol_rows[spec["key"]] = row
            lay.addWidget(row)
        lay.addStretch()
        return card

    # ── judge sensitivity ───────────────────────────────────────────────────
    def _sensitivity(self) -> QWidget:
        o = self.o
        card, lay = _card()
        lay.addWidget(_label("Judge sensitivity", size=12, bold=True))

        lay.addWidget(_field_label("Max tokens per interaction"))
        o.pol_tokens = QSpinBox()
        o.pol_tokens.setRange(pd.MIN_TOKENS, pd.MAX_TOKENS)
        o.pol_tokens.setSingleStep(1000)
        o.pol_tokens.setValue(pd.DEFAULT_TOKENS)
        o.pol_tokens.setGroupSeparatorShown(True)
        o.pol_tokens.setAccessibleName("Max tokens per interaction")
        o.pol_tokens.setMinimumHeight(44)
        o.pol_tokens.setStyleSheet(_spin_qss())
        o.pol_tokens.valueChanged.connect(lambda _v: o.mark_policy_dirty())
        lay.addWidget(o.pol_tokens)

        for attr, label, options in (
                ("pol_enforcement", "Enforcement mode", pd.ENFORCEMENT),
                ("pol_confidence", "Confidence threshold", pd.CONFIDENCE)):
            lay.addWidget(_field_label(label))
            combo = _select(label, options)
            combo.currentIndexChanged.connect(lambda _i: o.mark_policy_dirty())
            setattr(o, attr, combo)
            lay.addWidget(combo)

        lay.addWidget(_divider())
        alerts = QHBoxLayout()
        alerts.setSpacing(8)
        o.pol_alerts_btn = QPushButton()
        o.pol_alerts_btn.setObjectName("ghostBtn")
        o.pol_alerts_btn.setCheckable(True)
        o.pol_alerts_btn.setMinimumHeight(44)
        o.pol_alerts_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        o.pol_alerts_btn.setStyleSheet(_summary_qss())
        # The caret IS the state. This sheet styles every state the same, so
        # without it the control would look identical open and closed — the
        # same defect a checkable ghostBtn had on the Export page.
        o.pol_alerts_btn.toggled.connect(
            lambda on, b=o.pol_alerts_btn: _sync_disclosure(b, on))
        o.pol_alerts_btn.setChecked(True)          # web: <details open>
        _sync_disclosure(o.pol_alerts_btn, True)
        alerts.addWidget(o.pol_alerts_btn)
        alerts.addWidget(_label("· where breach alerts go", size=10.5,
                                colour=WEB["muted"]))
        alerts.addStretch()
        lay.addLayout(alerts)

        o.pol_alerts = QWidget()
        inner = QVBoxLayout(o.pol_alerts)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.setSpacing(8)
        inner.addWidget(_field_label("Notification on breach"))
        o.pol_notify = _select("Notification on breach", pd.NOTIFY)
        o.pol_notify.currentIndexChanged.connect(lambda _i: o.mark_policy_dirty())
        inner.addWidget(o.pol_notify)

        inner.addWidget(_field_label("Alert email  (blank = workspace contact)"))
        o.pol_email = QLineEdit()
        o.pol_email.setPlaceholderText("security@yourco.com")
        o.pol_email.setAccessibleName("Alert email")
        o.pol_email.setMinimumHeight(44)
        o.pol_email.setStyleSheet(_input_qss())
        o.pol_email.textEdited.connect(lambda _t: o.mark_policy_dirty())
        inner.addWidget(o.pol_email)

        inner.addWidget(_field_label(
            "Webhook URL  (optional — POSTed on each breach)"))
        o.pol_webhook = QLineEdit()
        o.pol_webhook.setPlaceholderText("https://yourco.com/foxy-hook")
        o.pol_webhook.setAccessibleName("Breach webhook URL")
        o.pol_webhook.setMinimumHeight(44)
        o.pol_webhook.setStyleSheet(_input_qss())
        o.pol_webhook.textEdited.connect(lambda _t: o.mark_policy_dirty())
        inner.addWidget(o.pol_webhook)

        # One line under the two fields that can be wrong. It is placed here,
        # next to them, rather than only in the save bar at the top of a
        # scrolled page where the user would never see it.
        o.pol_field_error = _label("", size=10.5, colour=BAD_RED, wrap=True)
        o.pol_field_error.hide()
        inner.addWidget(o.pol_field_error)

        lay.addWidget(o.pol_alerts)
        o.pol_alerts_btn.toggled.connect(o.pol_alerts.setVisible)
        lay.addStretch()
        return card

    # ── AI judge: provider, key source, BYOK ────────────────────────────────
    def _judge(self) -> QWidget:
        o = self.o
        card, lay = _card()
        lay.addWidget(_label("AI Judge — provider & keys", size=12, bold=True))
        lay.addWidget(_label(pd.JUDGE_BLURB, size=11, colour=WEB["muted"],
                             wrap=True))

        row = QHBoxLayout()
        row.setSpacing(16)

        left = QVBoxLayout()
        left.setSpacing(8)
        left.addWidget(_field_label("Judge provider"))
        o.pol_provider = _select("Judge provider", pd.PROVIDERS)
        o.pol_provider.currentIndexChanged.connect(
            lambda _i: o.on_judge_provider_changed())
        left.addWidget(o.pol_provider)

        left.addWidget(_field_label("Whose API key pays for grading"))
        seg = QWidget()
        seg.setObjectName("seg")
        seg_lay = QHBoxLayout(seg)
        seg_lay.setContentsMargins(4, 4, 4, 4)
        seg_lay.setSpacing(4)
        o.pol_mode_btns = {}
        for mode, text in (("own", "my own key"),
                           ("platform", "Foxy's managed keys")):
            btn = QPushButton(text)
            btn.setObjectName("segBtn")
            btn.setCheckable(True)
            btn.setMinimumHeight(44)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setAccessibleName(f"Judge key source: {text}")
            btn.clicked.connect(
                lambda _c=False, m=mode: o.set_judge_key_mode(m))
            o.pol_mode_btns[mode] = btn
            seg_lay.addWidget(btn)
        # The track hugs its two options; the stretch is OUTSIDE it, so the
        # control does not render with an empty third slot.
        seg_row = QHBoxLayout()
        seg_row.setContentsMargins(0, 0, 0, 0)
        seg_row.addWidget(seg)
        seg_row.addStretch()
        left.addLayout(seg_row)

        o.pol_mode_hint = _label(pd.OWN_HINT, size=10.5, colour=WEB["muted"],
                                 wrap=True)
        left.addWidget(o.pol_mode_hint)
        left.addStretch()
        row.addLayout(left, 1)

        right = QVBoxLayout()
        right.setSpacing(8)
        o.pol_key_rows = {}
        for provider, title in (("gemini", "Gemini API key"),
                                ("openai", "OpenAI API key")):
            key_row = KeyRow(provider, title)
            key_row.field.textEdited.connect(lambda _t: o.mark_policy_dirty())
            key_row.remove_btn.clicked.connect(
                lambda _c=False, p=provider: o.clear_judge_key(p))
            o.pol_key_rows[provider] = key_row
            right.addWidget(key_row)
        right.addStretch()
        row.addLayout(right, 1)

        lay.addLayout(row)
        return card

    def _privacy(self) -> QWidget:
        card, lay = _card()
        lay.addWidget(_label(pd.PRIVACY_NOTE, size=11.5, colour=WEB["muted"],
                             wrap=True))
        link = QLabel(f'<a href="{pd.PRIVACY_URL}" style="color:{WEB["fox2"]};">'
                      f'Privacy Policy ↗</a>')
        link.setOpenExternalLinks(True)
        link.setStyleSheet(
            f"font-family: '{pick_font('disp')}'; font-size: 11.5px;"
            f" background: transparent;")
        lay.addWidget(link)
        return card


# ── pieces ──────────────────────────────────────────────────────────────────
class SafeguardRow(QFrame):
    """One safeguard: name, description, severity chip, on/off.

    The chip carries a word ("high severity"), not only a colour — the whole
    row must still be readable to someone who cannot tell the red from the
    amber.
    """

    def __init__(self, spec: dict, parent=None):
        super().__init__(parent)
        self.spec = spec
        self.setObjectName("safeguardRow")
        self.setStyleSheet(
            f"QFrame#safeguardRow {{ background: {WEB['surf2']};"
            f" border: 2px solid {WEB['bc']};"
            f" border-radius: {RADIUS['sm']}px; }}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(13, 11, 13, 11)
        lay.setSpacing(11)

        col = QVBoxLayout()
        col.setSpacing(4)
        col.addWidget(_label(spec["name"], size=11.5, bold=True, wrap=True))
        col.addWidget(_label(spec["desc"], size=10.5, colour=WEB["muted"],
                             wrap=True))
        # D11 reuses this row for the Settings toggles, which have no severity
        # — the chip is the only part that assumes one.
        severity = pd.SEVERITY.get(spec.get("severity"))
        text, tone = severity if severity else ("", "mute")
        chip = QLabel(text.upper())
        chip.setVisible(bool(text))
        chip.setObjectName("sevChip")
        colour = TONES[tone]
        chip.setStyleSheet(
            f"QLabel#sevChip {{ color: {colour}; background: {WEB['surf3']};"
            f" border: 1.5px solid {colour}; border-radius: 10px;"
            f" padding: 3px 10px; font-family: '{pick_font('mono')}';"
            f" font-size: 9px; font-weight: 800; letter-spacing: 0.5px; }}")
        chip_row = QHBoxLayout()
        chip_row.setContentsMargins(0, 0, 0, 0)
        chip_row.addWidget(chip)
        chip_row.addStretch()
        col.addLayout(chip_row)
        lay.addLayout(col, 1)

        self.toggle = QPushButton()
        self.toggle.setObjectName("toggleBtn")
        self.toggle.setCheckable(True)
        self.toggle.setMinimumHeight(44)
        self.toggle.setMinimumWidth(64)
        self.toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        # #toggleBtn, not #segBtn — see the rule in foxy_tokens: a lone
        # checkable button needs a pill in BOTH states or "off" reads as text.
        self.toggle.toggled.connect(self._sync)
        lay.addWidget(self.toggle, 0, Qt.AlignmentFlag.AlignVCenter)
        self._sync(False)

    def _sync(self, on: bool):
        self.toggle.setText("ON" if on else "OFF")
        # The state is in the word as well as the fill: a checked segBtn is an
        # orange pill, but "on" and "off" must not be a colour judgement.
        self.toggle.setAccessibleName(
            f"{self.spec['name']} — {'enabled' if on else 'disabled'}")


class KeyRow(QWidget):
    """One provider's write-only key field.

    `field` is a password box that is never populated from a response — there
    is nothing to populate it with. The "key set ✓" chip and the remove link
    are driven entirely by the presence boolean the server returns.
    """

    def __init__(self, provider: str, title: str, parent=None):
        super().__init__(parent)
        self.provider = provider
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(_field_label(title))
        self.badge = QLabel("KEY SET ✓")
        self.badge.setObjectName("keySetChip")
        self.badge.setStyleSheet(
            f"QLabel#keySetChip {{ color: {OK_GREEN}; background: {WEB['surf3']};"
            f" border: 1.5px solid {OK_GREEN}; border-radius: 10px;"
            f" padding: 2px 8px; font-family: '{pick_font('mono')}';"
            f" font-size: 9px; font-weight: 800; }}")
        self.badge.hide()
        head.addWidget(self.badge)
        head.addStretch()
        # "remove key" belongs to THIS provider, so it sits in this provider's
        # header. Below the help paragraph it floated between two key rows and
        # read as if it belonged to the next one — on a control that deletes a
        # stored credential, that ambiguity is not acceptable.
        self.remove_btn = QPushButton("remove key")
        self.remove_btn.setObjectName("linkBtn")
        self.remove_btn.setMinimumHeight(44)
        self.remove_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.remove_btn.setAccessibleName(f"Remove the stored {title}")
        self.remove_btn.setStyleSheet(
            f"QPushButton#linkBtn {{ background: transparent; border: none;"
            f" color: {WEB['fox2']}; font-family: '{pick_font('mono')}';"
            f" font-size: 10px; font-weight: 800; }}"
            f"QPushButton#linkBtn:hover {{ color: {WEB['fox']}; }}"
            f"QPushButton#linkBtn:focus {{ color: {WEB['fox']};"
            f" text-decoration: underline; }}")
        self.remove_btn.hide()
        head.addWidget(self.remove_btn)
        lay.addLayout(head)

        self.field = QLineEdit()
        self.field.setEchoMode(QLineEdit.EchoMode.Password)
        self.field.setAccessibleName(title)
        self.field.setMinimumHeight(44)
        # The server's cap (policies.py:52-53). Without it an over-length
        # paste became a request, and the 422 that came back quoted the
        # rejected key — putting it on screen in cleartext.
        self.field.setMaxLength(pd.KEY_MAX)
        self.field.setStyleSheet(_input_qss())
        lay.addWidget(self.field)

        self.help = _label(pd.KEY_HELP, size=10, colour=WEB["muted"], wrap=True)
        lay.addWidget(self.help)
        # Its own error line: a key problem reported under the webhook field
        # in the other card is an error message about something the user is
        # not looking at.
        self.error = _label("", size=10.5, colour=BAD_RED, wrap=True)
        self.error.hide()
        lay.addWidget(self.error)

    def apply(self, view: dict, *, editable: bool = True):
        """Render one `policy_data.key_field()` result."""
        self.setVisible(view["used"])
        self.field.setEnabled(view["enabled"] and editable)
        self.field.setPlaceholderText(view["placeholder"])
        self.badge.setVisible(view["badge"])
        self.remove_btn.setVisible(view["remove"] and editable)

    def typed(self) -> str:
        return self.field.text().strip()

    def clear_field(self):
        """Drop whatever was typed. Called after every save, success or not —
        a key that has been handed to the server has no reason to stay here."""
        self.field.clear()


def _sync_disclosure(button: QPushButton, open_: bool):
    # "&&" — a single ampersand in a QPushButton label is a mnemonic marker and
    # is eaten, which rendered this as "Alerts  webhooks".
    button.setText(("▾ " if open_ else "▸ ") + "Alerts && webhooks")
    button.setAccessibleName(
        "Alerts and webhooks — " + ("expanded" if open_ else "collapsed"))


def _select(name: str, options) -> QComboBox:
    combo = QComboBox()
    combo.setAccessibleName(name)
    combo.setMinimumHeight(44)
    combo.setCursor(Qt.CursorShape.PointingHandCursor)
    for value, label in options:
        combo.addItem(label, value)
    combo.setStyleSheet(_combo_qss())
    return combo


def _divider() -> QFrame:
    line = QFrame()
    line.setObjectName("polDivider")
    line.setFixedHeight(2)
    line.setStyleSheet(f"QFrame#polDivider {{ background: {WEB['bc']};"
                       f" border: none; }}")
    return line


def _input_qss() -> str:
    return (f"QLineEdit {{ background: {WEB['surf2']}; color: {WEB['ink']};"
            f" border: 2.5px solid {WEB['bc']}; border-radius: {RADIUS['sm']}px;"
            f" padding: 6px 10px; font-family: '{pick_font('mono')}';"
            f" font-size: 11px; }}"
            f"QLineEdit:focus {{ border-color: {WEB['fox']}; }}"
            f"QLineEdit:disabled {{ color: {WEB['muted2']};"
            f" background: {WEB['surf3']}; }}"
            # After the :focus rule AND matching it, because in Qt a
            # pseudo-class selector outranks a plain type selector whatever
            # the order — so a bare `QLineEdit { border-color: red }` loses to
            # `QLineEdit:focus`, and the error handler focuses the field it
            # just marked. Same pattern, same reason, as auth_windows.py:203.
            f"QLineEdit[invalid=\"true\"], QLineEdit[invalid=\"true\"]:focus"
            f" {{ border-color: {BAD_RED}; }}")


def _spin_qss() -> str:
    return (f"QSpinBox {{ background: {WEB['surf2']}; color: {WEB['ink']};"
            f" border: 2.5px solid {WEB['bc']}; border-radius: {RADIUS['sm']}px;"
            f" padding: 6px 10px; font-family: '{pick_font('mono')}';"
            f" font-size: 11px; }}"
            f"QSpinBox:focus {{ border-color: {WEB['fox']}; }}"
            f"QSpinBox:disabled {{ color: {WEB['muted2']};"
            f" background: {WEB['surf3']}; }}"
            f"QSpinBox::up-button, QSpinBox::down-button {{ width: 16px;"
            f" background: {WEB['surf3']}; border: none; }}")


def _summary_qss() -> str:
    """The web's `<summary>` — a disclosure control, so it reads as a heading
    and carries the ▸/▾ that says which way it will move."""
    return (f"QPushButton#ghostBtn {{ background: transparent; border: none;"
            f" color: {WEB['ink']}; font-family: '{pick_font('disp')}';"
            f" font-size: 11.5px; font-weight: 800; text-align: left;"
            f" padding: 0px; }}"
            f"QPushButton#ghostBtn:hover {{ color: {WEB['fox2']}; }}"
            f"QPushButton#ghostBtn:focus {{ color: {WEB['fox2']};"
            f" text-decoration: underline; }}")
