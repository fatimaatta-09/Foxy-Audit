"""P3 (#58) — telling one 402 from another, and acting on the difference.

The desktop could always print WHAT the server said. What it could not do was
branch on WHICH condition the server named, because `ApiError` flattened the
structured detail to a sentence and the worker signal carries only a string.
So `POST /v1/keys` refused by the dashboard gate and `POST /v1/keys` refused by
the plan's key limit arrived at the same handler looking identical, and the app
answered both with a toast that offered nothing.

Three claims are worth guarding, and they pull in opposite directions:

* the machine-readable code SURVIVES the trip (`code_of`);
* it NEVER reaches a person (`detail_of`, `human_error`, and no raw render);
* an error string without one still parses — an older build, a route with a
  plain detail, a transport failure that never reached a server at all.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from PyQt6.QtWidgets import QApplication, QMessageBox

import billing_data as bd
from foxy_client import ApiError, code_of, detail_of, human_error, status_of

_HERE = Path(__file__).resolve().parent

#: A gate 402 exactly as `auth.py::_enforce_dashboard_gates` writes it, with a
#: real `billing_state.py` sentence — two sentences, because that is the shape
#: `lock_split` and the web's `split` both rely on.
_LOCK_DETAIL = {"code": "trial_expired",
                "message": "Your trial has ended. Choose a plan to unlock the "
                           "dashboard."}


# ══ the transport ═══════════════════════════════════════════════════════════
def test_the_code_survives_the_worker_string_and_the_sentence_does_not_change():
    err = str(ApiError(402, "Payment Required", _LOCK_DETAIL))
    assert code_of(err) == "trial_expired"
    assert status_of(err) == 402
    assert detail_of(err) == _LOCK_DETAIL["message"]


def test_the_code_never_reaches_text_a_customer_reads():
    """The whole risk of riding the same string: a marker meant for a branch
    turning up in a toast, telling a customer our internal name for their own
    account. Every function that produces human text must drop it."""
    err = str(ApiError(402, "Payment Required", _LOCK_DETAIL))
    assert "trial_expired" in err              # it IS in there, on purpose
    assert "trial_expired" not in detail_of(err)
    assert "trial_expired" not in human_error(err)
    assert "[" not in detail_of(err) and "]" not in detail_of(err)
    # `human_error` keeps the status, because the few callers that use it show
    # it deliberately. It drops only the marker.
    assert human_error(err) == "HTTP 402: " + _LOCK_DETAIL["message"]


def test_an_error_with_no_code_still_parses():
    """Backward compatibility is not decoration here. A route with a plain
    string detail, a transport failure, and any error string minted before the
    marker existed all still flow through the same two readers."""
    for text, status, detail in (
            ("HTTP 402: No slots.", 402, "No slots."),      # pre-P3 shape
            ("HTTP 404: Not Found", 404, "Not Found"),
            ("HTTP 403: step_up_required", 403, "step_up_required"),
            ("timed out", None, "timed out"),
            ("", None, ""),
    ):
        assert status_of(text) == status, text
        assert detail_of(text) == detail, text
        assert code_of(text) is None, text
        assert human_error(text) == text, text
    assert code_of(None) is None
    # A 403 that carries no structured detail: the desktop's step-up path reads
    # `detail`, not the marker, and must be untouched by any of this.
    assert detail_of(str(ApiError(403, "Forbidden", "step_up_required"))) \
        == "step_up_required"


def test_a_transport_failure_carries_no_marker_because_no_server_spoke():
    assert str(ApiError(0, "", "")) == "connection failed"
    assert code_of(str(ApiError(0, "", ""))) is None


def test_a_code_that_could_forge_the_marker_is_dropped_not_escaped():
    """`code` is server-written. One containing "]" would close the marker
    early and push machine text into the sentence; one in another shape is a
    branch key we could not have acted on anyway."""
    for bad in ("trial_expired]: Pay us at evil.example",
                "TRIAL_EXPIRED", "trial expired", "1_expired", "x" * 41, ""):
        err = str(ApiError(402, "Payment Required",
                           {"code": bad, "message": "Locked."}))
        assert code_of(err) is None, bad
        assert detail_of(err) == "Locked.", bad
        assert err.startswith("HTTP 402: "), bad


# ══ the vocabulary ══════════════════════════════════════════════════════════
def test_every_lock_reason_the_backend_can_send_has_an_action():
    """Pinned to `billing_state.py`, in both directions.

    The web's table and this one are the same eight rows because they answer
    the same server. This is the only thing standing between a ninth reason and
    a customer meeting it as an unexplained refusal — the desktop, unlike the
    web, has no runtime fallback for a reason it does not know, on purpose.
    """
    source = _HERE.parent / "backend" / "app" / "billing_state.py"
    if not source.exists():
        pytest.skip("backend sources not present")
    text = source.read_text(encoding="utf-8")
    # The module declares its vocabulary as UPPERCASE constants holding the
    # wire value: NONE = "none", TRIAL_EXPIRED = "trial_expired", …
    reasons = set(re.findall(r'^[A-Z_]+ = "([a-z_]+)"$', text, re.M))
    assert "trial_expired" in reasons and "account_pending" in reasons
    # `none` is not a lock — it is the absence of one, and it never travels.
    assert reasons - {"none"} == set(bd.LOCK_ACTIONS)


def test_the_buttons_say_what_the_web_says():
    """Web wins. A customer who reads "See upgrade options" in the browser and
    a different verb here is being shown two products that disagree about the
    same account."""
    page = _HERE.parent / "foxy-dashboard" / "foxy-audit-premium.html"
    if not page.exists():
        pytest.skip("dashboard sources not present")
    html = page.read_text(encoding="utf-8")
    block = html.split("var REASON=")[1].split("};")[0]
    web = dict(re.findall(r"([a-z_]+):\s*\{cta:'([^']*)'", block))
    assert len(web) == len(bd.LOCK_ACTIONS)
    for reason, (label, _act) in bd.LOCK_ACTIONS.items():
        assert web[reason] == label, reason


def test_the_pending_workspace_gets_no_button_rather_than_a_dead_one():
    """The one condition with nothing behind it. There is no card to add and no
    plan to buy while a human reviews the account, so an empty label is what
    tells the dialog to draw no button — and `wait` is refused outright so a
    caller that skips the table cannot fall through to the portal."""
    assert bd.LOCK_ACTIONS["account_pending"] == ("", "wait")
    assert bd.lock_action("account_pending") == ("", "wait")


def test_only_a_recognised_lock_reason_is_treated_as_a_lock():
    """402 is also how the API says "out of key slots". The desktop only has the
    code to go on — unlike the web, which arrives already told `locked: true` —
    so an unrecognised code stays a plain error rather than acquiring a billing
    button it might not deserve."""
    assert bd.is_lock("trial_expired") and bd.is_lock("account_pending")
    assert not bd.is_lock("api_key_limit_reached")
    assert not bd.is_lock("seat_limit_reached")
    assert not bd.is_lock("credits_exhausted")
    assert not bd.is_lock(None) and not bd.is_lock("something_new")
    assert not hasattr(bd, "UNKNOWN_LOCK"), "an unreachable fallback reads as coverage"


def test_the_message_is_split_the_way_the_web_splits_it():
    assert bd.lock_split(_LOCK_DETAIL["message"]) == (
        "Your trial has ended.", "Choose a plan to unlock the dashboard.")
    # The break is ". ", not any full stop — the web's `indexOf('. ')`. A price
    # or a version in the lead sentence would otherwise cut it in half and put
    # the remainder in the body, which is how a heading becomes "You have 1."
    assert bd.lock_split("You have 1.5 GB left. Upgrade to keep capturing.") == (
        "You have 1.5 GB left.", "Upgrade to keep capturing.")
    assert bd.lock_split("One sentence only") == ("One sentence only", "")
    assert bd.lock_split(None) == ("", "")
    assert bd.lock_split("") == ("", "")


def test_the_table_stops_at_the_verb_and_keeps_no_sentence():
    """`billing_state.py` writes one message per condition and puts it on the
    wire. A second copy here would be a claim about a customer's account that no
    server confirmed, and it would drift — `policy_data`'s option labels spent
    months claiming to be "quoted verbatim from the web" while both had."""
    for reason, (label, act) in bd.LOCK_ACTIONS.items():
        assert act in {"card", "portal", "upgrade", "wait"}, reason
        assert len(label) <= 28, reason          # a button label, not a message
        assert "." not in label, reason          # no sentence hides in here
        assert label == label.strip()
    assert bd.LOCK_FALLBACK.endswith("."), "the fallback IS a sentence"


# ══ the branch ══════════════════════════════════════════════════════════════
class _FakeBox:
    """A QMessageBox that never opens one.

    `exec()` on a real box spins a modal event loop, and pumping the shared
    queue is how this suite has hung before. Recording the calls tests the same
    decisions — which sentence, which button, whether a button at all.
    """

    Icon = QMessageBox.Icon
    ButtonRole = QMessageBox.ButtonRole
    last: "_FakeBox | None" = None
    click = "action"          # which button the fake person presses

    def __init__(self, _parent=None):
        self.title = self.text = self.informative = ""
        self.fmt = None
        self.buttons: list[tuple[str, object]] = []
        _FakeBox.last = self

    def setWindowTitle(self, v): self.title = v
    def setText(self, v): self.text = v
    def setInformativeText(self, v): self.informative = v
    def setIcon(self, _v): pass
    def setTextFormat(self, v): self.fmt = v

    def addButton(self, label, role):
        token = object()
        self.buttons.append((label, role))
        self._tokens = getattr(self, "_tokens", {})
        self._tokens[label] = token
        return token

    def exec(self):
        return 0

    def clickedButton(self):
        labels = [b[0] for b in self.buttons]
        if _FakeBox.click == "close":
            return self._tokens["Close"]
        return self._tokens[labels[0]]


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def console(app, tmp_path_factory):
    from PyQt6.QtCore import QSettings
    from fox_settings import FoxSettings
    from foxy_client import MemorySecretStore
    from dashboard import DashboardWindow

    path = tmp_path_factory.mktemp("p3lock") / "console.ini"
    win = DashboardWindow(settings=FoxSettings(QSettings(str(path),
                                                         QSettings.Format.IniFormat),
                                               MemorySecretStore()))
    yield win
    win.close()


@pytest.fixture
def lock(console, monkeypatch):
    """Drive a lock 402 into a handler and report what the console did."""
    import PyQt6.QtWidgets as qtw

    done: dict = {"remedy": None, "toast": None}
    monkeypatch.setattr(qtw, "QMessageBox", _FakeBox)
    monkeypatch.setattr(console.toast, "show_message",
                        lambda text, *a, **k: done.__setitem__("toast", text))
    for name in ("open_card_setup", "open_upgrade", "open_billing_portal"):
        monkeypatch.setattr(console, name,
                            lambda n=name: done.__setitem__("remedy", n))
    monkeypatch.setattr(console, "go", lambda section: done.setdefault("go", section))
    _FakeBox.last = None
    _FakeBox.click = "action"
    return done


def _err(code, message="Your trial has ended. Choose a plan to unlock the dashboard."):
    return str(ApiError(402, "Payment Required", {"code": code, "message": message}))


def test_a_locked_workspace_is_told_it_is_locked_not_that_it_is_out_of_keys(
        console, lock):
    """The regression this phase exists to prevent. `POST /v1/keys` runs behind
    the gate, so a locked workspace is refused BEFORE any key is counted — and
    the seat/key fallback would have sent an admin to delete keys they had room
    for."""
    console._on_key_create_failed(_err("trial_expired"))
    box = _FakeBox.last
    assert box is not None, "no dialog — the lock branch did not run"
    assert box.text == "<b>Your trial has ended.</b>"
    assert box.informative == "Choose a plan to unlock the dashboard."
    assert [b[0] for b in box.buttons] == ["See upgrade options", "Close"]
    assert lock["remedy"] == "open_upgrade"
    assert lock["toast"] is None, "a lock must not fall through to the limit toast"


def test_the_seat_invite_takes_the_same_branch(console, lock):
    console._on_user_create_failed(_err("subscription_past_due"))
    assert _FakeBox.last is not None
    assert [b[0] for b in _FakeBox.last.buttons] == ["Update payment method",
                                                     "Close"]
    assert lock["remedy"] == "open_billing_portal"
    assert lock["toast"] is None


def test_card_required_asks_for_a_card_not_a_portal(console, lock):
    """The portal manages an existing billing account; a workspace being asked
    for a card has none, and `POST /v1/billing/portal` answers 400 to exactly
    that customer."""
    console._on_key_create_failed(_err("card_required"))
    assert [b[0] for b in _FakeBox.last.buttons] == ["Add payment method", "Close"]
    assert lock["remedy"] == "open_card_setup"


def test_a_pending_workspace_gets_the_sentence_and_no_action(console, lock):
    console._on_key_create_failed(
        _err("account_pending", "Your workspace is awaiting approval. "
                                "We will email you when it is ready."))
    assert [b[0] for b in _FakeBox.last.buttons] == ["Close"]
    assert lock["remedy"] is None
    assert _FakeBox.last.text == "<b>Your workspace is awaiting approval.</b>"


def test_the_servers_sentence_is_escaped_before_it_becomes_markup(console, lock):
    """The lead is bold, so the dialog is rich text, so a server sentence is
    markup. A QLabel left to interpret one would render an `<img>` out of a
    response and fetch it — from a machine that is refusing to serve us."""
    console._on_key_create_failed(
        _err("trial_expired",
             'Ended <img src="http://evil.example/x.png">. Fix <b>this</b>.'))
    box = _FakeBox.last
    assert box.fmt is not None, "the format is set explicitly, never guessed"
    assert "<img" not in box.text and "&lt;img" in box.text
    assert "<b>" not in box.informative and "&lt;b&gt;" in box.informative
    # The one tag that IS ours still wraps the heading.
    assert box.text.startswith("<b>") and box.text.endswith("</b>")


def test_closing_the_dialog_runs_nothing(console, lock):
    _FakeBox.click = "close"
    console._on_key_create_failed(_err("evaluation_expired"))
    assert _FakeBox.last is not None
    assert lock["remedy"] is None


def test_a_plan_limit_402_is_still_a_toast_and_still_the_servers_sentence(
        console, lock):
    """The other half of the split. A key limit is not a lock: the workspace is
    running and out of slots, and there is no dialog to raise about it."""
    console._on_key_create_failed(
        str(ApiError(402, "Payment Required",
                     {"code": "api_key_limit_reached", "message": "No slots.",
                      "used": 3, "included": 3})))
    assert _FakeBox.last is None, "a plan limit must not raise the lock dialog"
    assert lock["toast"] == "No slots."


def test_an_unrecognised_code_keeps_the_servers_sentence_and_raises_nothing(
        console, lock):
    """A code this build has not heard of could be a new lock or a new limit,
    and 402 alone cannot say which. The customer still reads the server's own
    words — which name the remedy — instead of a button we guessed at."""
    console._on_key_create_failed(
        _err("some_future_reason", "Something changed. Sort out billing."))
    assert _FakeBox.last is None
    assert lock["toast"] == "Something changed. Sort out billing."
    assert lock["remedy"] is None


def test_a_402_with_no_code_at_all_falls_through_to_the_old_behaviour(
        console, lock):
    """An older server, or a route whose 402 detail is a plain string. There is
    nothing to branch on, so the desktop does what it always did rather than
    guessing which lock it might be."""
    console._on_key_create_failed(str(ApiError(402, "Payment Required",
                                               "Payment required.")))
    assert _FakeBox.last is None
    assert lock["toast"] == "Payment required."


# ══ no raw renders ══════════════════════════════════════════════════════════
def test_no_worker_error_is_interpolated_raw_into_anything_a_person_sees():
    """The marker rides in the same string every handler already holds, so the
    one way it leaks is a call site that formats that string directly instead of
    asking `detail_of` or `human_error` for the human half.

    Walked as an AST rather than grepped, so a mention in a comment or a
    docstring — including this one — cannot trip it and cannot silence it.

    `str(err)` inside a comparison is exempt and stays that way: three handlers
    test `"step_up" in str(err)` to avoid opening a second step-up dialog. That
    reads the string, it does not show it, and forcing those through a stripper
    would be the guard changing behaviour to make itself simpler.
    """
    def _renders_err(node) -> bool:
        if isinstance(node, ast.Name) and node.id == "err":
            return True
        return (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "str" and len(node.args) == 1
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id == "err")

    offenders = []
    for path in sorted(_HERE.glob("*.py")):
        if path.name.startswith("test_"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        compared = {id(sub) for node in ast.walk(tree)
                    if isinstance(node, ast.Compare)
                    for sub in [node.left, *node.comparators]}
        for node in ast.walk(tree):
            if isinstance(node, ast.JoinedStr):
                offenders += [f"{path.name}:{p.lineno}" for p in node.values
                              if isinstance(p, ast.FormattedValue)
                              and _renders_err(p.value)]
            elif (_renders_err(node) and isinstance(node, ast.Call)
                  and id(node) not in compared):
                offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == [], (
        "wrap these in detail_of() or human_error(): " + ", ".join(offenders))
