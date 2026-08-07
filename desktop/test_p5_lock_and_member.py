"""P5 — the two ways this console knew something was wrong and did not say so.

#106 was *locked and silent*: every gated route answers 402, so each panel
resolved to `PanelState.ERROR` ("couldn't load") and nothing on screen said the
word locked until the customer tried to create something.

#107 was *restricted and silent*: E3 hid the upgrade button from members on a
sound rule — a control that cannot work is worse than no control — and left
nothing in its place.

The two claims worth guarding pull against each other:

* a locked console explains itself with **nobody clicking anything**;
* a member is offered **no route to a checkout** by any of it.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication

import billing_data as bd

_HERE = Path(__file__).resolve().parent
_WEB = _HERE.parent / "foxy-dashboard" / "foxy-audit-premium.html"

_LOCKED = {"locked": True, "reason": "trial_expired", "capture_blocked": True,
           "message": "Your 7-day trial has ended. Upgrade to continue "
                      "capturing events."}


# ══ the shaping — no Qt needed ══════════════════════════════════════════════
def test_the_overlay_is_drawn_from_locked_and_from_nothing_else():
    """THE rule this phase rests on, and the one plausible tidy-up that breaks
    it: `reason` is set on orgs that are NOT locked — a `past_due` org inside
    its grace window reports one, and that warning is the entire purpose of the
    window. Driving the overlay off `reason` shuts out exactly that customer."""
    assert bd.lock_view(_LOCKED) is not None
    assert bd.lock_view({**_LOCKED, "locked": False}) is None
    assert bd.lock_view({"locked": False, "reason": "subscription_past_due",
                         "message": "Your last payment failed. Update it."}) is None
    assert bd.lock_view(None) is None and bd.lock_view("nope") is None


def test_the_words_are_the_servers_and_none_are_kept_here():
    view = bd.lock_view(_LOCKED)
    assert view["lead"] == "Your 7-day trial has ended."
    assert view["rest"] == "Upgrade to continue capturing events."
    source = (_HERE / "billing_data.py").read_text(encoding="utf-8")
    for sentence in ("Your 7-day trial has ended", "Your last payment failed",
                     "Your subscription is not active"):
        assert sentence not in source, f"a server sentence is copied here: {sentence}"


def test_a_pending_workspace_is_not_labelled_billing():
    """"BILLING" over "we are reviewing your request" is the first thing a new
    demo user would read as "something is wrong with my payment"."""
    view = bd.lock_view({"locked": True, "reason": "account_pending",
                         "message": "Your workspace is waiting for approval. "
                                    "We will email you.",
                         "capture_blocked": True})
    assert view["eyebrow"] == "access"
    assert view["cta"] == "" and view["action"] == "wait"
    assert bd.lock_view(_LOCKED)["eyebrow"] == "billing"


def test_the_evidence_strip_is_read_from_capture_blocked_never_from_locked():
    """They do not track each other: `past_due` locks the dashboard and keeps
    recording, `cancelled` stops recording and leaves it open. One state for
    both tells a customer their audit trail stopped when it did not."""
    assert bd.evidence_note(True)[0] == "New evidence is not being recorded"
    assert bd.evidence_note(False)[0] == "Your evidence is still being recorded"
    # Unknown draws NOTHING rather than asserting something about a ledger
    # nobody managed to ask about.
    assert bd.evidence_note(None) is None
    # The case where the two fields DISAGREE, which is the only one that can
    # tell them apart — and the real one: past_due locks the dashboard and keeps
    # recording. A fixture where both are True passes whichever field is read.
    still_recording = bd.lock_view({"locked": True, "reason": "subscription_past_due",
                                    "capture_blocked": False,
                                    "message": "Your last payment failed. Update it."})
    assert still_recording["evidence"][0] == "Your evidence is still being recorded"
    assert still_recording["evidence"][2] == 5


def test_a_workspace_that_never_started_is_not_told_its_evidence_is_intact():
    """Checked first, and it has to be: a pending workspace is refused capture
    exactly like a cancelled one and arrives with the same `True`, so the
    stopped variant would reassure it about an empty ledger."""
    assert bd.evidence_note(True, True)[0] == "Nothing is being recorded yet"
    assert bd.evidence_note(True, True)[2] == 0        # no solid links
    assert bd.evidence_note(True, False)[2] == 4
    assert bd.evidence_note(False, False)[2] == 5


def test_every_evidence_sentence_is_the_webs_word_for_word():
    if not _WEB.exists():
        pytest.skip("dashboard sources not present")
    # The web concatenates its long sentences across source lines, so the
    # wrapping is collapsed before comparing. Searching the raw file instead
    # would pass on the short strings and silently skip the long ones — which
    # are the ones carrying the claim about somebody's ledger.
    html = re.sub(r"'\s*\+\s*'", "", _WEB.read_text(encoding="utf-8"))
    for note in (bd.evidence_note(True, True), bd.evidence_note(True),
                 bd.evidence_note(False)):
        assert note[0] in html, note[0]
        assert note[1] in html, note[1]


# ══ the member path ════════════════════════════════════════════════════════
def test_the_member_copy_is_the_webs_word_for_word():
    """Standing rule: the web wins. A customer who reads one wording in the
    browser and another here is being shown two products that disagree about
    their own account."""
    if not _WEB.exists():
        pytest.skip("dashboard sources not present")
    whole = _WEB.read_text(encoding="utf-8")
    # The web's OWN ask block — its DEFINITION, not the first mention of the
    # name, which is a call site three hundred lines earlier. And not the whole
    # file: "sending…" appears elsewhere on that page, so a file-wide search let
    # a drifted busy label through the first time this ran.
    start = whole.index("window.foxAskAdmin={")
    html = re.sub(r"'\s*\+\s*'", "", whole[start:whole.index("function banner(", start)])
    for line in (bd.ASK_TITLE, bd.ASK_CTA, bd.ASKED_TITLE, bd.ASK_BUSY,
                 bd.ASK_FAILED, bd.ASK_OFFLINE):
        assert line in html, f"not in the dashboard's ask block: {line!r}"
    # The two long ones are wrapped across source lines in the HTML, so they are
    # compared with the wrapping collapsed rather than by a substring search
    # that would silently pass on any drift.
    assert bd.ASK_BODY in html
    assert bd.ASKED_BODY.format(when="X") in html.replace("'+ago(st.requested_at)+'", "X")


def test_an_admin_is_offered_no_ask_block_at_all():
    assert bd.ask_view({"can_purchase": True, "requested_at": None}) is None
    # A lookup that never arrived is NOT "you are a member": drawing the block
    # from a failure puts "only an admin can buy a plan" in front of an admin.
    assert bd.ask_view(None) is None
    assert bd.ask_view({}) is None


def test_the_second_press_is_answered_and_says_when():
    """The de-dup IS the feature. The server writes one notification per org per
    24 hours and answers a repeat with the SAME timestamp and `created:false`,
    so this must say the admins were already told — not send again, and not
    appear to do nothing."""
    fresh = bd.ask_view({"can_purchase": False, "requested_at": None})
    assert fresh["asked"] is False and fresh["cta"] == bd.ASK_CTA
    asked = bd.ask_view({"can_purchase": False,
                         "requested_at": "2026-01-01T00:00:00Z"})
    assert asked["asked"] is True
    assert asked["cta"] == "", "a second press is still on offer"
    assert asked["title"] == bd.ASKED_TITLE
    assert "ago" in asked["body"]


def test_the_confirmation_reads_true_for_a_colleague_who_did_not_send_it():
    """The notification is org-wide, so the next member to open the page finds
    it already asked. "You asked" would be false for exactly that person."""
    body = bd.ASKED_BODY.format(when="3 hours ago")
    assert "you asked" not in body.lower() and "your request" not in body.lower()
    assert "Asked 3 hours ago" in body


def test_when_comes_from_the_servers_stamp_and_never_from_a_local_clock():
    source = (_HERE / "billing_data.py").read_text(encoding="utf-8")
    ask = source[source.index("def ask_view"):source.index("def ago(")]
    assert "requested_at" in ask
    # `ago` needs a clock to measure elapsed time; `ask_view` must not reach for
    # one, because that is where a missing `requested_at` would quietly become
    # "just now" and claim an ask that never happened.
    assert "now()" not in ask and "time.time()" not in ask, (
        "the confirmation stamps its own moment instead of the server's")
    assert bd.ask_view({"can_purchase": False, "requested_at": None})["asked"] is False
    assert bd.ago(None) == "just now" and bd.ago("nonsense") == "just now"


# ══ a member cannot reach a checkout ═══════════════════════════════════════
def test_the_ask_block_touches_one_endpoint_and_it_buys_nothing():
    """The whole point of #107's answer: it notifies, it does not purchase."""
    source = (_HERE / "dashboard.py").read_text(encoding="utf-8")
    body = source[source.index("def ask_admin_to_upgrade"):]
    body = body[:body.index("\n    # --")] if "\n    # --" in body else body[:2000]
    paths = set(re.findall(r'"(/v1/[a-z0-9/_{}-]+)"', body))
    assert paths == {"/v1/billing/upgrade-request"}, paths
    for money in ("upgrade-session", "checkout-session", "card-setup-session",
                  "portal"):
        assert money not in body, f"the ask can reach {money}"


def test_no_money_route_is_reachable_without_the_admin_check():
    """Every desktop call to a route that spends this workspace's money must sit
    behind `_is_billing_admin`. Walked as an AST so a mention in a comment — in
    this file or that one — cannot pass or fail it."""
    source = (_HERE / "dashboard.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    money = ("/v1/billing/upgrade-session", "/v1/billing/portal",
             "/v1/billing/card-setup-session", "/v1/billing/plans")
    callers = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Constant) and sub.value in money:
                callers.add(node.name)
    assert callers == {"open_upgrade", "_start_checkout", "open_billing_portal",
                       "open_card_setup"}, callers
    # And each of those is only ever reached from a control the role gates, or
    # from the overlay's remedy — which is cleared to "" whenever an ask block
    # is on screen. `_on_lock_remedy` is that gate.
    # Read as a TREE, not as text: the function's own docstring explains why it
    # has no `else`, and a grep for the word finds the explanation. That is the
    # comment-grep trap this vault has now logged five times, and it caught this
    # guard on its first run.
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_on_lock_remedy")
    branch = next(n for n in fn.body if isinstance(n, ast.If))
    while branch.orelse:
        assert len(branch.orelse) == 1 and isinstance(branch.orelse[0], ast.If), (
            "an action this build does not know falls through to a purchase")
        branch = branch.orelse[0]


# ══ the console, built ══════════════════════════════════════════════════════
@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def console(app, tmp_path_factory):
    from fox_settings import FoxSettings
    from foxy_client import MemorySecretStore
    from dashboard import DashboardWindow

    path = tmp_path_factory.mktemp("p5") / "console.ini"
    win = DashboardWindow(settings=FoxSettings(
        QSettings(str(path), QSettings.Format.IniFormat), MemorySecretStore()))
    yield win
    win.close()


def test_a_locked_console_explains_itself_without_anybody_clicking(console):
    """The regression #106 is. No button was pressed and no page was opened —
    the response to a routine background read is the whole trigger."""
    assert console.lock_ov.isHidden()
    console._apply_lock(_LOCKED, {"can_purchase": True})
    assert not console.lock_ov.isHidden(), "a locked workspace still says nothing"
    assert console.lock_ov.title.text() == "Your 7-day trial has ended."
    assert console.lock_ov.body.text() == "Upgrade to continue capturing events."
    assert console.lock_ov.ev.isVisibleTo(console.lock_ov)
    assert console.lock_ov.ev_title.text() == "New evidence is not being recorded"
    assert console.lock_ov.cta.text() == "See upgrade options"
    console._clear_lock()


def test_paying_clears_it(console):
    console._apply_lock(_LOCKED, {"can_purchase": True})
    assert not console.lock_ov.isHidden()
    console._apply_lock({**_LOCKED, "locked": False}, {"can_purchase": True})
    assert console.lock_ov.isHidden(), "the wall outlived the payment"


def test_a_failed_lookup_never_invents_a_lock(console):
    """A dropped connection is not a payment wall. Rendering one from a failure
    is the same class of lie as a panel inventing a zero."""
    console._clear_lock()
    console._apply_lock(None, None)
    assert console.lock_ov.isHidden()


def test_a_locked_member_is_offered_the_ask_and_not_the_remedy(console):
    """Every lock remedy — card-setup, portal, upgrade-session — is admin-only,
    so a member pressing any of them gets a 403. The ask REPLACES the CTA."""
    console._apply_lock(_LOCKED, {"can_purchase": False, "requested_at": None})
    assert console.lock_ov.ask_title.text() == bd.ASK_TITLE
    assert console.lock_ov.cta.text() == bd.ASK_CTA
    assert console.lock_ov._action == "", "a member can still fire the remedy"
    console._clear_lock()


def test_a_locked_member_who_already_asked_gets_no_button(console):
    console._apply_lock(_LOCKED, {"can_purchase": False,
                                  "requested_at": "2026-01-01T00:00:00Z"})
    assert console.lock_ov.ask_title.text() == bd.ASKED_TITLE
    assert console.lock_ov.cta.text() == ""
    assert console.lock_ov.cta.isHidden()
    console._clear_lock()


def test_a_pending_workspace_gets_no_button_either(console):
    console._apply_lock({"locked": True, "reason": "account_pending",
                         "capture_blocked": True,
                         "message": "Your workspace is waiting for approval. "
                                    "We will email you when it is ready."},
                        {"can_purchase": True})
    assert console.lock_ov.cta.text() == ""
    assert console.lock_ov.eyebrow.text() == "ACCESS"
    assert console.lock_ov.ev_title.text() == "Nothing is being recorded yet"
    console._clear_lock()


def test_the_remedy_button_fires_the_reasons_own_action(console, monkeypatch):
    fired = []
    for name in ("open_card_setup", "open_upgrade", "open_billing_portal"):
        monkeypatch.setattr(console, name, lambda n=name: fired.append(n))
    console._apply_lock(_LOCKED, {"can_purchase": True})
    console.lock_ov.cta.click()
    assert fired == ["open_upgrade"]
    console._clear_lock()


def test_a_limit_402_never_raises_the_wall(console, monkeypatch):
    """402 is also how the API says "out of API-key slots" — a limit on a
    WORKING workspace. Treating every 402 as a lock would shut the console on
    an org that is simply out of key slots."""
    asked = []
    monkeypatch.setattr(console, "refresh_lock", lambda: asked.append(1))
    console._on_payment_required("api_key_limit_reached")
    assert asked == []
    console._on_payment_required("trial_expired")
    assert asked == [1]


def test_the_lock_check_is_throttled(console, monkeypatch):
    """One page load can 402 on every panel at once; the web takes 1.5s for the
    same reason."""
    asked = []
    monkeypatch.setattr(console, "refresh_lock", lambda: asked.append(1))
    console._lock_throttle.stop()
    for _ in range(5):
        console._on_payment_required("trial_expired")
    assert asked == [1]


def test_the_console_asks_for_the_lock_itself(console):
    """Connected on the console, not in `omni_fox` which owns the other three
    app-wide signals: this window is built with its own client whenever one is
    not passed, and a lock the companion never saw would leave it as silent as
    before."""
    source = (_HERE / "dashboard.py").read_text(encoding="utf-8")
    assert "self.client.payment_required.connect(self._on_payment_required)" in source
    assert hasattr(console, "lock_ov") and hasattr(console, "_on_payment_required")


def test_a_hidden_console_spawns_no_workers(console):
    """Every test in this tree builds a console and never shows it, and the
    file's own rule is that the chrome pollers do not run while hidden. A lock
    check on a timer put two QThreads into a window nobody was looking at, and
    the suite died of an access violation at the first `processEvents`."""
    assert console.isHidden()
    before = len(console._page_workers)
    console.refresh_lock()
    console.refresh_ask_state()
    assert len(console._page_workers) == before


def test_the_billing_page_offers_the_member_something(console):
    """#107 itself: the button goes, and this stands in its place."""
    console.bil_ask.show_ask(bd.ask_view({"can_purchase": False,
                                          "requested_at": None}))
    assert console.bil_ask.title.text() == bd.ASK_TITLE
    assert console.bil_ask.cta.text() == bd.ASK_CTA
    console.bil_ask.show_ask(None)
    assert console.bil_ask.isHidden(), "an admin is shown a members-only block"


def test_the_ask_block_never_sits_beside_the_upgrade_button(console):
    """They are two answers to the same question and only one applies to a
    person. Both on screen would offer a member a button that 403s."""
    source = (_HERE / "dashboard.py").read_text(encoding="utf-8")
    body = source[source.index("def _apply_billing_buttons"):
                  source.index("def refresh_ask_state")]
    assert "if not admin" in body and "self.bil_ask.hide()" in body

def test_the_client_itself_raises_the_signal(app):
    """Assert the CALL SITE, not the connection.

    Two greps for `payment_required` — one on the client's declaration, one on
    the console's `connect` — both pass while the emit itself is a `pass`. Only
    driving a real 402 through `FoxyClient.request` proves the wire is joined,
    and this is the guard the mutation run added: deleting the emit left every
    other test in this file green.
    """
    from foxy_client import ApiError, FoxyClient

    class _Refuses:
        base_url = "http://x"
        bearer_key = None

        def session_value(self, *a, **k):
            return None

        def request(self, *a, **k):
            raise ApiError(402, "Payment Required",
                           {"code": "trial_expired", "message": "Ended. Upgrade."})

    client = FoxyClient(http=_Refuses())
    seen = []
    client.payment_required.connect(seen.append)
    with pytest.raises(ApiError):
        client.request("GET", "/v1/stats")
    assert seen == ["trial_expired"], "the 402 never reached the signal"


def test_a_limit_402_still_reaches_the_signal_and_is_filtered_later(app):
    """The client says what it knows — "the server refused for payment, and
    named this" — and the billing vocabulary decides what that means. Filtering
    inside the client would put the product's reason table in the one module
    every other module imports."""
    from foxy_client import ApiError, FoxyClient

    class _Limit:
        base_url = "http://x"
        bearer_key = None

        def session_value(self, *a, **k):
            return None

        def request(self, *a, **k):
            raise ApiError(402, "Payment Required",
                           {"code": "api_key_limit_reached", "message": "No slots."})

    client = FoxyClient(http=_Limit())
    seen = []
    client.payment_required.connect(seen.append)
    with pytest.raises(ApiError):
        client.request("POST", "/v1/keys")
    assert seen == ["api_key_limit_reached"]
    assert not bd.is_lock(seen[0]), "a plan limit is being treated as a lock"


def test_a_failed_role_lookup_hides_the_block_rather_than_drawing_it(console,
                                                                    monkeypatch):
    """Drawing "only an admin can buy a plan" from a lookup that never arrived
    puts it in front of an ADMIN whose connection dropped. The error callback is
    invoked directly, because the branch is the thing under test."""
    import dashboard as dash

    captured = {}

    def fake_spawn(*a, **kw):
        captured.update(kw)

    monkeypatch.setattr(dash, "spawn_worker", fake_spawn)
    monkeypatch.setattr(console, "isVisible", lambda: True)
    console.bil_ask.show_ask(bd.ask_view({"can_purchase": False,
                                          "requested_at": None}))
    assert not console.bil_ask.isHidden()
    console.refresh_ask_state()
    assert "on_err" in captured, "the role lookup was never spawned"
    captured["on_err"]("HTTP 500: Internal Server Error")
    assert console.bil_ask.isHidden(), "a failed lookup drew the members-only block"

