"""D11a — Settings, account half: secrets, defaults, and destructive controls.

The weight here is on three things:

* a secret typed into this page (password, 2FA code) reaches the request and
  nothing else — not QSettings, not a log, not a label;
* an unset preference is shown the way the SERVER behaves when the key is
  absent, not as False;
* every destructive control names what it destroys and is confirmed before it
  fires, and the workspace delete needs the name typed.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from PyQt6.QtWidgets import QApplication

import settings_data as sd

_HERE = Path(__file__).resolve().parent


# ══ preferences ═════════════════════════════════════════════════════════════
def test_an_unset_preference_shows_what_the_server_actually_does():
    """`_ALLOWED_PREFS` are absent until first written, and the notifier treats
    absent as deliver for breach alerts and the digest, opt-in for the rest
    (account.py:593-597). Rendering every absent key as "off" would tell a
    user they get no breach alerts while the emails keep arriving."""
    values = sd.preference_values(None)
    assert values["notify_breach_alerts"] is True
    assert values["notify_weekly_digest"] is True
    assert values["notify_key_rotation_reminders"] is False
    assert values["hide_sensitive_metadata"] is False
    assert values["notify_product_updates"] is False
    # …and this one follows the WEB, whose checkbox is unchecked when the key
    # is unset. Showing it ON here made one account read two ways.
    assert values["notify_security_alerts"] is False


def test_an_explicit_false_beats_the_default():
    values = sd.preference_values({"preferences": {"notify_breach_alerts": False}})
    assert values["notify_breach_alerts"] is False
    assert values["notify_weekly_digest"] is True      # still its default


def test_one_key_per_request_so_a_stale_toggle_cannot_overwrite():
    """The server merges (account.py:613). Sending the whole bag would let a
    toggle this window has not refreshed clobber a change made elsewhere."""
    body = sd.preference_body("hide_sensitive_metadata", True)
    assert body == {"preferences": {"hide_sensitive_metadata": True}}
    assert len(body["preferences"]) == 1


def test_every_offered_preference_is_one_the_server_accepts():
    """A toggle the backend ignores would silently do nothing."""
    allowed = (_HERE.parent / "backend" / "app" / "routers" / "account.py")
    if not allowed.exists():                       # backend not in this tree
        pytest.skip("backend sources not present")
    source = allowed.read_text(encoding="utf-8")
    for key in sd.PREF_KEYS:
        assert f'"{key}"' in source, f"{key} is not in _ALLOWED_PREFS"


# ══ passwords and 2FA ═══════════════════════════════════════════════════════
def test_the_local_password_check_is_deliberately_shallow():
    """The server owns the policy. A client inventing extra rules would refuse
    passwords the server would accept."""
    assert sd.password_problem("", "newpassword") is not None
    assert sd.password_problem("cur", "") is not None
    assert sd.password_problem("cur", "short") is not None
    assert sd.password_problem("same-one", "same-one") is not None
    assert sd.password_problem("current", "a-long-enough-one") is None


def test_the_2fa_code_must_be_six_digits():
    assert sd.mfa_code_ok("123456")
    assert sd.mfa_code_ok("  123456 ")
    assert not sd.mfa_code_ok("12345") and not sd.mfa_code_ok("abcdef")
    assert not sd.mfa_code_ok("") and not sd.mfa_code_ok(None)


def test_turning_2fa_off_says_what_it_costs():
    assert "password alone" in sd.MFA_DISABLE_WARNING


# ══ the allow-list, where a bad save locks you out ══════════════════════════
def test_a_malformed_allowlist_is_caught_before_it_locks_you_out():
    """A rejected save is recoverable. A save that succeeds with the wrong
    entry is not — you have locked yourself out of the page you would fix it
    on, which is why this one is checked locally as well as server-side."""
    assert sd.allowlist_problem("203.0.113.4, 10.0.0.0/24") is None
    assert sd.allowlist_problem("") is None            # blank = allow all
    assert sd.allowlist_problem("   ") is None
    assert "not an IPv4" in sd.allowlist_problem("999.1.1.1")
    assert "not an IPv4" in sd.allowlist_problem("hello")
    assert "prefix length" in sd.allowlist_problem("10.0.0.0/99")
    assert sd.allowlist_problem("10.0.0.0/8") is None
    assert "lock" in sd.IP_LOCKOUT.lower()


# ══ destructive controls ════════════════════════════════════════════════════
def test_delete_needs_the_name_and_ignores_case_and_spaces():
    assert sd.delete_confirmed("Acme Corp", "acme corp")
    assert sd.delete_confirmed("  acme  ", "Acme")
    assert not sd.delete_confirmed("", "Acme")
    assert not sd.delete_confirmed("acme", "")
    assert not sd.delete_confirmed("acme inc", "Acme")


def test_each_destructive_warning_names_the_consequence():
    """Not "are you sure" — what actually happens."""
    assert "signed out" in sd.revoke_device_warning("Chrome on macOS")
    assert "Chrome on macOS" in sd.revoke_device_warning("Chrome on macOS")
    assert "including this one" in sd.LOGOUT_ALL_WARNING
    assert "stops rendering" in sd.BADGE_REVOKE_WARNING
    assert "signed out" in sd.DELETE_WARNING and "locked" in sd.DELETE_WARNING


def test_the_badge_never_invents_a_url():
    assert sd.badge_view(None)["token"] == ""
    assert sd.badge_view({"token": "t"}, "")["svg_url"] == ""   # no backend set
    view = sd.badge_view({"token": "abc"}, "https://app.example.com/")
    assert view["svg_url"] == "https://app.example.com/v1/badge/abc.svg"
    assert view["verify_url"] == "https://app.example.com/verify/abc"
    assert view["svg_url"] in view["embed"] and view["verify_url"] in view["embed"]


def test_a_server_supplied_badge_url_is_escaped_into_the_link():
    html = sd.badge_link_html({"verify_url": 'x"><script>y'}, "#fff")
    assert "<script>" not in html and "&lt;script&gt;" in html


# ══ the module must not be able to keep a secret ════════════════════════════
def test_settings_data_cannot_persist_or_log_anything():
    """Same structural check as D9's access_data and D7's policy_data."""
    tree = ast.parse((_HERE / "settings_data.py").read_text(encoding="utf-8"))
    banned_calls, banned_imports = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name in {"open", "write", "setValue", "print", "log", "debug",
                        "info", "warning", "error"}:
                banned_calls.add(name)
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            module = getattr(node, "module", "") or ""
            for candidate in [a.name for a in node.names] + [module]:
                if candidate.split(".")[0] in {"logging", "os", "io", "pathlib",
                                               "keyring", "sqlite3", "shutil"}:
                    banned_imports.add(candidate)
    assert not banned_calls, f"settings_data can persist/log via {banned_calls}"
    assert not banned_imports, f"settings_data imports {banned_imports}"


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

    path = tmp_path_factory.mktemp("d11") / "console.ini"
    store = QSettings(str(path), QSettings.Format.IniFormat)
    win = DashboardWindow(settings=FoxSettings(store, MemorySecretStore()))
    yield win
    win.close()


ME = {"email": "ada@acme.co", "role": "admin", "org_id": "org_123",
      "full_name": "Ada Lovelace", "mfa_enabled": False}
SESSIONS = {"items": [
    {"id": "s1", "user_agent": "Foxy Desktop on Windows", "ip": "203.0.113.4",
     "current": True, "created_at": "2026-07-20T10:00:00Z",
     "last_seen_at": "2026-07-28T09:00:00Z"},
    {"id": "s2", "user_agent": "Chrome on macOS", "ip": "198.51.100.9",
     "current": False, "created_at": "2026-07-01T10:00:00Z",
     "last_seen_at": "2026-07-27T10:00:00Z"}]}


def test_the_settings_page_builds(console):
    for attr in ("set_name", "set_readonly", "set_pw_fields", "mfa_state",
                 "mfa_off_box", "mfa_on_box", "pref_rows", "ip_allow",
                 "device_rows", "badge_btn", "delete_workspace_btn",
                 "settings_scroll"):
        assert hasattr(console, attr), attr
    assert set(console.pref_rows) == set(sd.PREF_KEYS)


def test_identity_fills_and_stays_read_only(console):
    console._on_settings_me(ME)
    assert console.set_name.text() == "Ada Lovelace"
    assert console.set_readonly["email"].text() == "ada@acme.co"
    assert console.set_readonly["role"].text() == "admin"
    assert console.set_readonly["org_id"].text() == "org_123"
    for field in console.set_readonly.values():
        assert field.isReadOnly()


def test_a_failed_me_does_not_blank_what_we_know(console):
    console._on_settings_me(ME)
    console._on_settings_me(None)
    assert console.set_readonly["email"].text() == "ada@acme.co"


def test_loading_the_preferences_does_not_fire_a_save(console, monkeypatch):
    """Every setChecked emits `toggled`; without the guard, opening the page
    would PUT six preferences back at the server."""
    import dashboard as dashboard_mod
    calls = []
    monkeypatch.setattr(dashboard_mod, "spawn_worker",
                        lambda *a, **k: calls.append(k.get("body")))
    console._on_preferences({"preferences": {"notify_breach_alerts": False}})
    assert calls == []
    assert console.pref_rows["notify_breach_alerts"].toggle.isChecked() is False
    assert console.pref_rows["notify_weekly_digest"].toggle.isChecked() is True

    console.pref_rows["hide_sensitive_metadata"].toggle.setChecked(True)
    assert calls == [{"preferences": {"hide_sensitive_metadata": True}}]


def test_the_password_fields_are_cleared_on_success_and_on_failure(console):
    console.set_pw_fields["current"].setText("old-password")
    console.set_pw_fields["new"].setText("a-new-long-password")
    console._password_done(None, "")
    assert all(f.text() == "" for f in console.set_pw_fields.values())

    console.set_pw_fields["current"].setText("old-password")
    console.set_pw_fields["new"].setText("a-new-long-password")
    console._password_done(400, "wrong password")
    assert all(f.text() == "" for f in console.set_pw_fields.values())
    assert console.set_pw_btn.isEnabled()


def test_a_local_password_problem_never_leaves_the_app(console, monkeypatch):
    import dashboard as dashboard_mod
    calls = []
    monkeypatch.setattr(dashboard_mod, "spawn_worker",
                        lambda *a, **k: calls.append(a))
    console.set_pw_fields["current"].setText("cur")
    console.set_pw_fields["new"].setText("short")
    console.change_password()
    assert not calls
    assert not console.set_pw_status.isHidden()
    console.set_pw_fields["current"].clear()
    console.set_pw_fields["new"].clear()


def test_the_2fa_code_and_password_never_outlive_their_request(console):
    console.mfa_code.setText("123456")
    console.mfa_password.setText("hunter2")
    console._mfa_changed(True)
    assert console.mfa_code.text() == "" and console.mfa_password.text() == ""
    assert not console.mfa_on_box.isHidden() and console.mfa_off_box.isHidden()
    console._mfa_changed(False)
    assert console.mfa_off_box.isHidden() is False


def test_no_settings_secret_reaches_qsettings(console):
    store = console.settings
    console.set_pw_fields["current"].setText("SECRET-pw-never-stored")
    console.mfa_password.setText("SECRET-pw-never-stored")
    console._password_done(None, "")
    console._mfa_changed(False)
    store._s.sync()
    dumped = " ".join(f"{k}={store._s.value(k)}" for k in store._s.allKeys())
    assert "SECRET" not in dumped
    assert "SECRET" not in " ".join(str(v) for v in vars(store._secrets).values())


def test_devices_list_flags_this_one_and_offers_no_revoke_for_it(console):
    console._on_devices(SESSIONS)
    assert console.device_rows.count() == 2
    rows = sd.device_rows(SESSIONS)
    assert rows[0]["current"] and not rows[0]["revocable"]
    assert rows[1]["revocable"]


def test_devices_empty_and_error_say_different_things(console):
    from panel_state import ERROR_TITLE, PanelState
    console._on_devices({"items": []})
    assert console.device_empty.state() is PanelState.EMPTY
    console._on_devices(None, ok=False)
    assert console.device_empty.title.text() == ERROR_TITLE


def test_a_bad_allowlist_never_leaves_the_app(console, monkeypatch):
    import dashboard as dashboard_mod
    calls = []
    monkeypatch.setattr(dashboard_mod, "spawn_worker",
                        lambda *a, **k: calls.append(a))
    console.ip_allow.setText("999.1.1.1")
    console.save_ip_allowlist()
    assert not calls and not console.ip_status.isHidden()
    console.ip_allow.setText("10.0.0.0/24")
    console.save_ip_allowlist()
    assert calls


def test_the_delete_dialog_stays_disabled_until_the_name_matches(console, app):
    from settings_page import ConfirmNameDialog
    dialog = ConfirmNameDialog("Acme Corp", console)
    assert not dialog.confirm.isEnabled()
    dialog.field.setText("acme")
    assert not dialog.confirm.isEnabled()
    dialog.field.setText("Acme Corp")
    assert dialog.confirm.isEnabled()
    dialog.field.setText(" acme corp ")          # trimmed, case-insensitive
    assert dialog.confirm.isEnabled()
    dialog.deleteLater()


def test_the_desktop_card_replaces_the_download_prompt(console):
    """The web offers a download; we are the thing being downloaded.

    Asserted on the CONTROLS, not the prose — the card's own copy says "there
    is nothing to download", so a word search would fail on the very sentence
    that proves the point."""
    from PyQt6.QtWidgets import QPushButton
    page = console.stack.widget(console._page_index["settings"])
    text = " ".join(lbl.text() for lbl in page.findChildren(type(console.mfa_state)))
    assert sd.DESKTOP_CARD[0] in text

    labels = [b.text().lower() for b in page.findChildren(QPushButton)]
    assert not any("download" in b for b in labels)
    # …and neither of the two things the desktop deliberately does not port
    assert not any("theme" in b or "skin" in b for b in labels)
    assert "theme" not in text.lower() and "skin picker" not in text.lower()


# ══ TASK 016 · the three blockers, each previously untested ═════════════════
def test_logging_out_everywhere_actually_signs_out(console, monkeypatch):
    """It cleared the cookie and toasted, so the console kept rendering as
    signed in. The real path is `sign_out_requested` -> omni_fox.sign_out()."""
    import dashboard as dashboard_mod
    monkeypatch.setattr(dashboard_mod, "spawn_worker", lambda *a, **k: None)
    console._signed_in = True
    emitted = []
    console.sign_out_requested.connect(lambda: emitted.append(True))

    console._signed_out_everywhere()
    assert console._signed_in is False
    assert console.user_val.text() == "not signed in"
    assert emitted == [True], "nothing asked the app to show the login window"


def test_deleting_the_workspace_signs_out_too(console, monkeypatch):
    """After a delete the workspace no longer exists — continuing to render it
    is the worst version of this bug."""
    import dashboard as dashboard_mod
    sent = {}

    def fake(client, method, path, **kw):
        sent["path"] = path
        sent["body"] = kw.get("body")
        kw["on_ok"]({})                       # the server accepted it
        return None

    monkeypatch.setattr(dashboard_mod, "spawn_worker", fake)
    monkeypatch.setattr("settings_page.ConfirmNameDialog.exec", lambda self: 1)
    monkeypatch.setattr("settings_page.ConfirmNameDialog.typed",
                        lambda self: "Acme Corp")
    console._org_name = "Acme Corp"
    console._signed_in = True
    emitted = []
    console.sign_out_requested.connect(lambda: emitted.append(True))

    console.delete_workspace()
    assert sent["path"] == "/v1/account/delete"
    assert sent["body"] == {"confirm_name": "Acme Corp"}
    assert console._signed_in is False and emitted == [True]


def test_a_session_only_user_can_still_confirm_the_delete(console):
    """`_org_name` comes from /v1/health, which is Bearer-only — so an
    email-and-password session has no name, `delete_confirmed(typed, "")` was
    mathematically impossible, and the button could never enable. The server
    compares it anyway (account.py:266), so it decides."""
    from settings_page import ConfirmNameDialog
    unknown = ConfirmNameDialog(None, console)
    assert not unknown.confirm.isEnabled()
    unknown.field.setText("Acme Corp")
    assert unknown.confirm.isEnabled(), (
        "a session-only user could never confirm the delete")
    text = " ".join(lbl.text() for lbl in unknown.findChildren(type(console.mfa_state)))
    assert "checked by the server" in text, "the dialog must say why"
    unknown.deleteLater()

    known = ConfirmNameDialog("Acme Corp", console)
    known.field.setText("wrong")
    assert not known.confirm.isEnabled()      # still matched live when known
    known.deleteLater()


def test_recent_logins_is_built_and_admin_gated(console, monkeypatch):
    """The docstring claimed this card existed; it did not. Failures are
    named, not just coloured."""
    import dashboard as dashboard_mod
    from panel_state import PanelState
    monkeypatch.setattr(dashboard_mod, "spawn_worker", lambda *a, **k: None)
    assert hasattr(console, "login_rows") and hasattr(console, "login_rows_strip")

    console._on_login_history([
        {"email": "ada@acme.co", "ip": "203.0.113.4", "success": True,
         "created_at": "2026-07-28T09:00:00Z"},
        {"email": "mallory@evil.co", "ip": "198.51.100.9", "success": False,
         "created_at": "2026-07-28T08:00:00Z"}])
    assert console.login_rows.count() == 2
    rows = sd.login_rows([{"email": "x", "success": False}])
    assert rows[0]["outcome"] == "failed" and rows[0]["tone"] == "bad"

    console._on_login_history(None, ok=False)
    assert console.login_rows_strip.state() is PanelState.ERROR

    # a member is told, not shown an error they cannot act on
    console.client.http._jar.clear()
    console._role = "member"
    console.refresh_login_history()
    assert console.login_rows_strip.state() is PanelState.EMPTY
    assert "admins" in console.login_rows_strip.body.text()


def test_the_badge_button_does_not_offer_what_the_endpoint_cannot_do(console):
    """POST /v1/account/badge mints OR returns the existing token
    (account.py:298-306) — it never regenerates."""
    console._on_badge({"token": "abc123"})
    assert "regenerate" not in console.badge_btn.text().lower()
    assert not console.badge_btn.isEnabled()
    console._on_badge(None)
    assert console.badge_btn.isEnabled()


def test_the_badge_reads_the_key_the_server_actually_sends(console):
    """`url` is what the route returns; reading `svg_url` worked only because
    the fallback happened to build the same string."""
    view = sd.badge_view({"token": "t", "url": "https://cdn.example/b.svg"},
                         "https://app.example.com")
    assert view["svg_url"] == "https://cdn.example/b.svg"


def test_an_unconfirmed_toggle_says_so(console, monkeypatch):
    """Save failed AND the corrective re-read failed: the switch on screen is
    a value nobody confirmed, so the row says that rather than looking settled."""
    row = console.pref_rows["notify_product_updates"]
    console._preference_unknown("notify_product_updates")
    assert not row.note.isHidden()
    assert row.note.text() == sd.PREF_UNCONFIRMED
    console._on_preferences({"preferences": {}})
    assert row.note.isHidden(), "a confirmed read must clear the warning"


def test_the_version_line_never_invents_a_build_number():
    assert sd.desktop_version_line("1.1.4") == "Version 1.1.4"
    assert "unknown" in sd.desktop_version_line("")
    assert "unknown" in sd.desktop_version_line(None)
