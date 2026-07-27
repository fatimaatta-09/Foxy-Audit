"""D7 — the Policy ruleset page: shaping, permission gating, and the key rule.

This page writes tenant security configuration, so the heavy tests here are the
ones about what leaves the app and who is allowed to send it:

* a provider key is on the wire only when the user just typed it, and the
  "leave it alone" case OMITS the field rather than sending anything;
* nothing that came back from the server can ever reach a key field;
* a member or a key-only session cannot reach the save path at all.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from PyQt6.QtWidgets import QApplication

import policy_data as pd

_HERE = Path(__file__).resolve().parent


# ══ shaping ═════════════════════════════════════════════════════════════════
def test_policy_view_falls_back_to_the_backends_own_defaults():
    """Blanks would read as "off" for settings that are actually on."""
    view = pd.policy_view(None)
    assert view["pii_detection"] is True and view["prompt_injection"] is True
    assert view["regulated_data_mode"] is False
    assert view["max_token_threshold"] == pd.DEFAULT_TOKENS
    assert view["enforcement_mode"] == "block"
    assert view["confidence_threshold"] == "balanced"
    assert view["notify_on_breach"] == "immediate"


def test_an_unknown_option_value_never_reaches_a_control():
    """A select can only hold one of its own options; a server value outside
    that set falls back rather than silently selecting index 0."""
    view = pd.policy_view({"enforcement_mode": "nuke", "judge_provider": "xai"})
    assert view["enforcement_mode"] == "block" and view["judge_provider"] == "gemini"


def test_token_threshold_is_clamped_to_what_the_server_accepts():
    assert pd.clamp_tokens(0) == pd.DEFAULT_TOKENS      # web: mt>0 ? mt : 50000
    assert pd.clamp_tokens(-5) == pd.DEFAULT_TOKENS
    assert pd.clamp_tokens("nope") == pd.DEFAULT_TOKENS
    assert pd.clamp_tokens(99_000_000) == pd.MAX_TOKENS
    assert pd.clamp_tokens(1) == 1


def test_search_matches_the_description_not_just_the_name():
    """"HIPAA" only appears in the regulated row's description, and the web
    matches the row's whole text."""
    hits = pd.search_hits("hipaa")
    assert hits == [False, False, True]
    assert pd.search_hits("") == [True, True, True]
    assert pd.no_match("zzz") and not pd.no_match("") and not pd.no_match("pii")


def test_platform_mode_is_not_shown_for_a_plan_that_cannot_use_it():
    """The server refuses `platform` on a non-premium tier (policies.py:167),
    so displaying it as the current mode would promise what the next save
    would reject."""
    locked = pd.judge_view({"judge_key_mode": "platform",
                            "platform_keys_allowed": False})
    assert locked["mode"] == "own" and locked["platform_allowed"] is False
    allowed = pd.judge_view({"judge_key_mode": "platform",
                             "platform_keys_allowed": True})
    assert allowed["mode"] == "platform"


def test_key_rows_follow_the_provider_and_the_key_source():
    own = dict(judge_provider="gemini", mode="own")
    assert pd.key_field("gemini", **own, key_set=False)["used"] is True
    assert pd.key_field("openai", **own, key_set=False)["used"] is False
    assert pd.key_field("openai", judge_provider="both", mode="own",
                        key_set=False)["used"] is True
    # Foxy's keys pay for it — the user's own key fields are not in play
    assert pd.key_field("gemini", judge_provider="gemini", mode="platform",
                        key_set=True)["used"] is False


def test_a_stored_key_is_advertised_but_never_shown():
    """The placeholder is the only hint a key exists, and it says "stored"."""
    field = pd.key_field("gemini", judge_provider="gemini", mode="own",
                         key_set=True)
    assert field["badge"] and field["remove"]
    assert field["placeholder"] == pd.STORED_PLACEHOLDER
    assert "stored" in field["placeholder"] and "AIza" not in field["placeholder"]
    # marked for removal: the chip and the remove link go, the hint comes back
    cleared = pd.key_field("gemini", judge_provider="gemini", mode="own",
                           key_set=True, cleared=True)
    assert not cleared["badge"] and not cleared["remove"]
    assert cleared["placeholder"] == pd.KEY_HINTS["gemini"]


# ══ the wire body — the security-critical half ══════════════════════════════
FORM = {"pii_detection": True, "prompt_injection": False,
        "regulated_data_mode": True, "max_token_threshold": 1234,
        "enforcement_mode": "flag", "confidence_threshold": "low",
        "notify_on_breach": "digest", "notify_email": " ops@x.co ",
        "notify_webhook_url": "", "judge_provider": "both"}
JUDGE_OWN = {"mode": "own", "platform_allowed": False}


def test_an_untouched_key_field_is_omitted_entirely():
    """THE rule. Omitting is how the server is told to keep what it has
    (policies.py::_store_key); sending "" would wipe a working key, and
    sending anything else would mean this app was holding one."""
    body = pd.save_body(FORM, JUDGE_OWN)
    assert "gemini_api_key" not in body and "openai_api_key" not in body


def test_a_typed_key_is_sent_and_a_removed_key_is_sent_empty():
    body = pd.save_body(FORM, JUDGE_OWN, typed={"gemini": "  AIza-fake-fake-key  "},
                        cleared=("openai",))
    assert body["gemini_api_key"] == "AIza-fake-fake-key"     # stripped, not mangled
    assert body["openai_api_key"] == ""               # "" clears server-side


def test_a_blank_typed_field_does_not_clear_a_stored_key():
    """An empty box means "I did not touch this", not "delete it" — the remove
    affordance is the only way to clear."""
    body = pd.save_body(FORM, JUDGE_OWN, typed={"gemini": "   "})
    assert "gemini_api_key" not in body


def test_the_body_a_freshly_loaded_form_would_send_carries_no_key():
    """The realistic path: GET says a key is stored, the user changes a toggle
    and saves. Nothing about `gemini_key_set` may turn into a key field."""
    payload = {"gemini_key_set": True, "openai_key_set": True,
               "judge_key_mode": "own", "platform_keys_allowed": False}
    body = pd.save_body(pd.policy_view(payload), pd.judge_view(payload))
    assert not [k for k in body if "api_key" in k]
    assert not any("key_set" in k for k in body)      # read-only fields too


def test_the_body_only_ever_contains_fields_the_server_writes():
    body = pd.save_body(FORM, JUDGE_OWN, typed={"gemini": "k"})
    assert set(body) <= {
        "pii_detection", "prompt_injection", "regulated_data_mode",
        "max_token_threshold", "enforcement_mode", "confidence_threshold",
        "notify_on_breach", "notify_email", "notify_webhook_url",
        "judge_provider", "judge_key_mode", "gemini_api_key", "openai_api_key"}
    assert body["notify_email"] == "ops@x.co"      # trimmed
    assert body["notify_webhook_url"] is None      # blank ⇒ null, not ""
    assert body["max_token_threshold"] == 1234


def test_validation_mirrors_the_servers_two_checks():
    assert pd.validate(FORM) is None
    assert pd.validate({**FORM, "notify_email": "nope"})[0] == "notify_email"
    assert pd.validate({**FORM, "notify_webhook_url": "yourco.com"})[0] == \
        "notify_webhook_url"
    assert pd.validate({**FORM, "notify_webhook_url": "https://y.co"}) is None


def test_a_503_says_nothing_was_saved():
    """The KEK is missing, `_store_key` raises before the commit, so the whole
    request is rolled back. "The key failed" would leave the user believing
    the rest of the ruleset went through."""
    message, tone = pd.save_result(503)
    assert tone == "bad"
    assert "not saved" in message.lower() and "nothing" in message.lower()
    assert "unencrypted" in message


def test_a_403_repeats_the_servers_own_reason():
    """403 is either "not an admin" or "this plan may not use Foxy's keys" and
    only the server knows which — so it speaks, rather than us guessing."""
    plan = "Foxy's managed provider keys require the premium plan"
    assert pd.save_result(403, plan)[0] == plan
    assert pd.save_result(403, "")[0] == pd.FORBIDDEN_FALLBACK
    assert "nothing was saved" in pd.save_result(0)[0].lower()


# ══ a rejected key must never come back onto the screen ═════════════════════
PLANTED = "AIza-planted-planted-planted"


def _pydantic_422(value: str) -> list:
    """What FastAPI actually returns with no custom handler: a list of
    Pydantic error dicts, each echoing the rejected value in `input`."""
    return [{"type": "string_too_long",
             "loc": ["body", "gemini_api_key"],
             "msg": "String should have at most 512 characters",
             "input": value,
             "ctx": {"max_length": 512}}]


def test_a_422_never_quotes_the_value_it_rejected():
    """Layer 2. `ApiError._text()` unwrapped a dict detail but let a LIST fall
    straight through, so `str(err)` was the raw Python repr of Pydantic's
    errors — `input` and all. That string is what reaches the status line."""
    from foxy_client import ApiError
    err = ApiError(422, "Unprocessable Entity", _pydantic_422(PLANTED))
    text = str(err)
    assert PLANTED not in text
    assert "input" not in text and "ctx" not in text
    # …and it still says something useful about what was wrong
    assert "gemini_api_key" in text and "512" in text
    assert text.startswith("HTTP 422: ")          # status_of still parses it
    from foxy_client import status_of
    assert status_of(err) == 422


def test_the_summary_survives_a_malformed_or_long_error_list():
    from foxy_client import ApiError
    many = [{"loc": ["body", f"f{i}"], "msg": "bad"} for i in range(6)]
    text = str(ApiError(422, "", many))
    assert "+3 more" in text
    assert str(ApiError(422, "", ["not-a-dict"])) == "HTTP 422: invalid value"
    assert str(ApiError(422, "", [])) == \
        "HTTP 422: the request was rejected as invalid"


def test_redaction_removes_known_and_key_shaped_values():
    """Layer 3, in the shape backend/app/anchor.py:61-67 already uses."""
    assert pd.redact(f"rejected {PLANTED} here", [PLANTED]) == \
        f"rejected {pd.REDACTED} here"
    # unknown to us, but shaped like a key
    assert "sk-aaaabbbb" not in pd.redact("saw sk-aaaabbbb", [])
    # a short "secret" must not blanket-replace ordinary words
    assert pd.redact("the mode is own", ["own"]) == "the mode is own"
    assert pd.redact(None, []) == ""


def test_an_over_length_key_never_becomes_a_request():
    """Layer 1. At the cap counts as too long: the field stops accepting text
    at KEY_MAX, so a paste that reached the limit was clipped — and a clipped
    key is a wrong key the server would accept."""
    assert pd.key_too_long({"gemini": "a" * (pd.KEY_MAX + 1)}) == "gemini"
    assert pd.key_too_long({"openai": "a" * pd.KEY_MAX}) == "openai"
    assert pd.key_too_long({"gemini": "a" * 40}) is None
    assert pd.key_too_long({}) is None and pd.key_too_long(None) is None


def test_the_module_cannot_persist_or_log_anything():
    """The D9 pattern: a module that handles key material must not be able to
    write one anywhere, and that is checked structurally rather than trusted."""
    tree = ast.parse((_HERE / "policy_data.py").read_text(encoding="utf-8"))
    banned_calls, banned_imports = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name in {"open", "write", "setValue", "print", "log", "debug",
                        "info", "warning", "error", "setText", "dump", "dumps"}:
                banned_calls.add(name)
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            module = getattr(node, "module", "") or ""
            names = [a.name for a in node.names] + [module]
            for candidate in names:
                if candidate.split(".")[0] in {"logging", "os", "io", "pathlib",
                                               "shutil", "tempfile", "keyring",
                                               "sqlite3", "PyQt6"}:
                    banned_imports.add(candidate)
    assert not banned_calls, f"policy_data can persist/log via {banned_calls}"
    assert not banned_imports, f"policy_data imports {banned_imports}"


# ══ the built page ══════════════════════════════════════════════════════════
@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def console(app, tmp_path_factory):
    from PyQt6.QtCore import QSettings
    from fox_settings import FoxSettings
    from foxy_client import MemorySecretStore
    from dashboard import DashboardWindow

    path = tmp_path_factory.mktemp("d7") / "console.ini"
    store = QSettings(str(path), QSettings.Format.IniFormat)
    win = DashboardWindow(settings=FoxSettings(store, MemorySecretStore()))
    yield win
    win.close()


def _as_admin(console, *, admin: bool = True, session: bool = True):
    """Pretend a human session with a role, without a backend.

    The cookie has to carry the host the console's settings point at, because
    `has_session` deliberately refuses a cookie belonging to another backend.
    """
    from urllib.parse import urlsplit
    from test_foxy_client import _inject_session
    console.client.http._jar.clear()
    if session:
        host = urlsplit(console.settings.backend_url()).hostname or "127.0.0.1"
        _inject_session(console.client.http, domain=host)
    console._role = "admin" if admin else "member"
    console._apply_policy_permission()


def test_the_policy_page_builds(console):
    for attr in ("pol_search", "pol_rows", "pol_tokens", "pol_enforcement",
                 "pol_confidence", "pol_notify", "pol_email", "pol_webhook",
                 "pol_provider", "pol_mode_btns", "pol_key_rows", "pol_save",
                 "pol_status", "policy_scroll"):
        assert hasattr(console, attr), attr
    assert set(console.pol_rows) == {s["key"] for s in pd.SAFEGUARDS}


def test_loading_the_form_is_not_the_user_changing_it(console):
    """Every setter emits its change signal, so without the guard the page
    would announce "Unsaved changes" the moment it opened."""
    console._on_policy({"pii_detection": False, "max_token_threshold": 900,
                        "enforcement_mode": "monitor"})
    assert console.pol_status.text() == ""
    assert console._pol_dirty is False
    assert console.pol_rows["pii_detection"].toggle.isChecked() is False
    assert console.pol_tokens.value() == 900
    assert console.pol_enforcement.currentData() == "monitor"

    console.pol_rows["pii_detection"].toggle.setChecked(True)
    assert console._pol_dirty and console.pol_status.text() == pd.DIRTY


def test_the_search_hides_rows_and_says_when_nothing_matches(console):
    # isHidden(), not isVisible(): a child of an unshown window is never
    # "visible", so isVisible() here would assert nothing at all.
    console.pol_search.setText("injection")
    assert not console.pol_rows["prompt_injection"].isHidden()
    assert console.pol_rows["pii_detection"].isHidden()
    assert console.pol_no_match.isHidden()
    console.pol_search.setText("zzzz")
    assert not console.pol_no_match.isHidden()
    console.pol_search.clear()
    assert not console.pol_rows["pii_detection"].isHidden()


def test_a_member_cannot_reach_the_save_path(console):
    """PUT is admin-only. The controls are disabled BEFORE the attempt, so a
    member is not invited to fill in a form that will 403."""
    _as_admin(console, admin=False)
    assert not console.pol_save.isEnabled()
    assert not console.pol_tokens.isEnabled()
    assert not console.pol_rows["pii_detection"].toggle.isEnabled()
    assert not console.pol_notice_card.isHidden()
    assert console.pol_notice.text() == pd.MEMBER_NOTICE
    # and the search still works — reading what is enforced is not privileged
    assert console.pol_search.isEnabled()

    before = list(console._page_workers)
    console.save_policy()
    assert list(console._page_workers) == before      # nothing was sent


def test_key_only_mode_is_read_only_and_says_why(console):
    """An org API key authenticates the SDK, not a person; PUT needs an admin
    human session however the workspace is configured."""
    _as_admin(console, admin=True, session=False)
    console._role = "admin"          # a stale role must not be enough
    console._apply_policy_permission()
    assert not console._can_edit_policy()
    assert not console.pol_save.isEnabled()
    assert console.pol_notice.text() == pd.KEY_ONLY_NOTICE


def test_an_admin_gets_the_controls_back(console):
    _as_admin(console)
    assert console._can_edit_policy()
    assert console.pol_save.isEnabled() and console.pol_tokens.isEnabled()
    assert console.pol_notice_card.isHidden()


def test_a_failed_load_shows_no_ruleset_at_all(console):
    """A greyed-out "Block PII: ON" still reads as a fact about the workspace,
    and a failed GET taught us nothing — so the form is HIDDEN, not merely
    disabled, and saving is off so nobody writes defaults over a real ruleset
    we never managed to read."""
    from panel_state import PanelState
    _as_admin(console)
    console._on_policy({"pii_detection": True})
    assert not console.pol_form.isHidden()

    console._on_policy_failed("HTTP 500: boom")
    assert not console.pol_save.isEnabled()
    assert console.pol_form.isHidden()
    assert console.pol_state.state() is PanelState.ERROR
    assert not console.pol_state.isHidden()
    assert "nothing on this page is claimed" in console.pol_state.body.text().lower()
    assert console.pol_state.toolTip() == "HTTP 500: boom"   # raw kept, not shown
    assert not console.pol_state.retry_btn.isHidden()        # a way forward

    # A successful retry must restore Save on its own — this used to need the
    # caller to re-apply permission, so a page that failed once stayed
    # unsaveable for the session, and this test was hiding it by doing that
    # call itself.
    console._on_policy({})
    assert console.pol_save.isEnabled() and not console.pol_form.isHidden()
    assert console.pol_state.isHidden()


def test_the_plan_lock_is_shown_and_platform_mode_refused(console):
    _as_admin(console)
    console._on_policy({"platform_keys_allowed": False})
    platform = console.pol_mode_btns["platform"]
    assert not platform.isEnabled()
    assert "premium" in platform.text().lower()
    console.set_judge_key_mode("platform")
    assert console._pol_judge["mode"] == "own"        # refused, not applied

    console._on_policy({"platform_keys_allowed": True, "judge_key_mode": "own"})
    assert console.pol_mode_btns["platform"].isEnabled()
    console.set_judge_key_mode("platform")
    assert console._pol_judge["mode"] == "platform"
    # Foxy's keys pay for grading, so the BYOK fields step aside
    assert console.pol_key_rows["gemini"].isHidden()


def test_no_response_can_put_a_key_into_a_key_field(console):
    """The GET only returns booleans, but the point is that even a server that
    wrongly sent one could not get it onto the page."""
    _as_admin(console)
    console._on_policy({"gemini_key_set": True, "openai_key_set": True,
                        "judge_provider": "both",
                        "gemini_api_key": "AIza-leaked-leaked",
                        "openai_api_key": "sk-leaked-leaked"})
    for row in console.pol_key_rows.values():
        assert row.field.text() == ""
        assert "leaked" not in row.field.placeholderText()
        assert not row.badge.isHidden()       # driven by the boolean
    rendered = " ".join(
        w.text() for w in console.pol_key_rows["gemini"].findChildren(
            type(console.pol_status)))
    assert "leaked" not in rendered


def test_a_typed_key_leaves_the_app_and_does_not_stay_behind(console, monkeypatch):
    """It goes onto the wire once and the field is cleared — on success AND on
    failure, because a secret sitting in a box across a failed save is one we
    are holding for no reason."""
    _as_admin(console)
    console._on_policy({"judge_provider": "gemini", "gemini_key_set": False})
    sent = {}

    def fake_spawn(client, method, path, **kw):
        sent["method"], sent["path"], sent["body"] = method, path, kw.get("body")
        return None

    monkeypatch.setattr("dashboard.spawn_worker", fake_spawn)
    console.pol_key_rows["gemini"].field.setText("AIza-typed-typed-key")
    console.save_policy()

    assert sent["method"] == "PUT" and sent["path"] == "/v1/policies"
    assert sent["body"]["gemini_api_key"] == "AIza-typed-typed-key"
    assert "openai_api_key" not in sent["body"]

    console._on_policy_saved({"gemini_key_set": True})
    assert console.pol_key_rows["gemini"].field.text() == ""
    assert console.pol_status.text() == pd.SAVED

    console.pol_key_rows["gemini"].field.setText("AIza-again-again")
    console._on_policy_save_failed("HTTP 503: nope")
    assert console.pol_key_rows["gemini"].field.text() == ""
    assert console.pol_status.text() == pd.KEK_UNAVAILABLE


def test_the_desktop_never_writes_a_judge_key_to_settings(console):
    """The keychain and QSettings are both searched — a key must be in neither.
    D9's rule, applied to the other secret this app can hold."""
    _as_admin(console)
    console.pol_key_rows["openai"].field.setText("sk-neverstored-neverstored")
    console.pol_key_rows["openai"].field.clear()
    store = console.settings
    store._s.sync()
    dumped = " ".join(f"{k}={store._s.value(k)}" for k in store._s.allKeys())
    assert "neverstored" not in dumped, dumped
    # …and the keychain seam, which is where the org key legitimately lives
    secrets = store._secrets
    blob = " ".join(str(v) for v in vars(secrets).values())
    assert "neverstored" not in blob


def test_a_rejected_key_reaches_neither_the_screen_nor_the_a11y_tree(console):
    """End to end through the layer that actually renders it: the 422 arrives
    as a worker error string, and BOTH the label text and the accessible name
    are what a person or a screen reader gets."""
    from foxy_client import ApiError
    _as_admin(console)
    console._on_policy({"judge_provider": "gemini", "gemini_key_set": False})
    console.pol_key_rows["gemini"].field.setText(PLANTED)

    console._on_policy_save_failed(str(ApiError(422, "Unprocessable Entity",
                                                _pydantic_422(PLANTED))))
    assert PLANTED not in console.pol_status.text()
    assert PLANTED not in console.pol_status.accessibleName()
    assert "AIza" not in console.pol_status.text()
    assert console.pol_key_rows["gemini"].field.text() == ""

    # the value-based pass catches a key the summariser could not know about
    console.pol_key_rows["gemini"].field.setText(PLANTED)
    console._on_policy_save_failed(f"HTTP 400: rejected {PLANTED}")
    assert PLANTED not in console.pol_status.text()
    assert pd.REDACTED in console.pol_status.text()


def test_an_over_length_paste_is_refused_before_it_is_sent(console, monkeypatch):
    _as_admin(console)
    console._on_policy({"judge_provider": "gemini", "gemini_key_set": False})
    calls = []
    monkeypatch.setattr("dashboard.spawn_worker",
                        lambda *a, **k: calls.append(a))
    field = console.pol_key_rows["gemini"].field
    # the widget itself refuses to hold more than the server accepts
    field.setText("a" * (pd.KEY_MAX + 50))
    assert len(field.text()) == pd.KEY_MAX

    console.save_policy()
    assert not calls                                   # never left the app
    assert field.property("invalid") == "true"
    # reported on the key row itself, not under the webhook field in the
    # other card — that is a message about something the user is not looking at
    row = console.pol_key_rows["gemini"]
    assert row.error.text() == pd.KEY_TOO_LONG and not row.error.isHidden()
    assert console.pol_field_error.isHidden()
    assert pd.KEY_TOO_LONG in field.accessibleDescription()

    field.setText("AIza-short-short")
    console.save_policy()
    assert calls and field.property("invalid") == "false"
    assert row.error.isHidden()
    field.clear()


def test_a_marked_field_is_red_while_focused(console, app):
    """The bug this replaces: a bare `QLineEdit { border-color: red }` loses
    to `QLineEdit:focus` whatever the order, and the handler focuses the field
    it just marked — so the user always saw the ordinary orange ring. Measured
    from the pixels, because the property-only version of this test passed."""
    from collections import Counter
    from foxy_tokens import BAD_RED, WEB

    _as_admin(console)
    console._on_policy({})
    console.show()
    console.go("policy")
    app.processEvents()

    console.pol_email.setText("not-an-email")
    console.save_policy()
    app.processEvents()
    try:
        assert console.pol_email.hasFocus()
        counts = Counter(
            console.pol_email.grab().toImage().pixelColor(x, y).name()
            for x in range(console.pol_email.width())
            for y in range(console.pol_email.height()))
        red = counts.get(BAD_RED.lower(), 0)
        orange = counts.get(WEB["fox"].lower(), 0)
        assert red > 50, f"invalid border painted {red} red pixels"
        assert red > orange, (f"focus ring wins: {orange} orange vs {red} red")
    finally:
        console.pol_email.setText("ops@x.co")
        console.save_policy()
        console.hide()


def test_bad_input_is_reported_next_to_the_field_not_after_a_round_trip(console,
                                                                       monkeypatch):
    _as_admin(console)
    console._on_policy({})
    calls = []
    monkeypatch.setattr("dashboard.spawn_worker",
                        lambda *a, **k: calls.append(a))
    console.pol_email.setText("not-an-email")
    console.save_policy()
    assert not calls                                   # never left the app
    assert not console.pol_field_error.isHidden()
    # "check the highlighted field" has to be true of something: the field
    # itself is marked, not just described.
    assert console.pol_email.property("invalid") == "true"
    assert console.pol_webhook.property("invalid") == "false"
    assert console.pol_field_error.text() in \
        console.pol_email.accessibleDescription()

    console.pol_email.setText("ops@x.co")
    console.save_policy()
    assert calls and console.pol_field_error.isHidden()
    assert console.pol_email.property("invalid") == "false"
