"""D11b — Settings, admin half: team, activity, webhooks, SSO.

Four things carry the weight here:

* an action is only offered when the server would actually accept it — the
  last active admin cannot be demoted, you cannot disable yourself, a disabled
  user cannot be re-invited;
* a member is TOLD, not refused: every card renders the reason and disables
  its controls rather than 403-ing after a click;
* nothing claims a state it did not measure — the SSO chip says "unknown"
  when nobody answered, never "not set up";
* a secret (temp password, `whsec_`) reaches the shown-once dialog and
  nothing else.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from PyQt6.QtWidgets import QApplication

import settings_admin as sa

_HERE = Path(__file__).resolve().parent


# ══ team ════════════════════════════════════════════════════════════════════
USERS = [
    {"id": "u1", "email": "Ada@Acme.co", "role": "admin", "disabled": False},
    {"id": "u2", "email": "grace@acme.co", "role": "member", "disabled": False},
    {"id": "u3", "email": "alan@acme.co", "role": "member", "disabled": True},
]


def _by_email(rows, email):
    return next(r for r in rows if r["email"].lower() == email)


def test_you_are_marked_regardless_of_case():
    """`/v1/auth/me` lower-cases on create but the list echoes what is stored,
    so a case mismatch would mark nobody and offer you a self-disable."""
    rows = sa.team_rows(USERS, "ada@acme.co")
    assert _by_email(rows, "ada@acme.co")["is_me"]
    assert not _by_email(rows, "grace@acme.co")["is_me"]


def test_you_are_never_offered_a_self_disable():
    """auth_human.py:537 — the server 400s on it."""
    rows = sa.team_rows(USERS, "ada@acme.co")
    assert not _by_email(rows, "ada@acme.co")["can_disable"]
    assert _by_email(rows, "grace@acme.co")["can_disable"]


def test_the_last_active_admin_cannot_be_demoted():
    """auth_human.py:574-578 refuses it, so offering the button is offering a
    dead end — and the org would be locked out of admin if it worked."""
    rows = sa.team_rows(USERS, "someone.else@acme.co")
    ada = _by_email(rows, "ada@acme.co")
    assert ada["last_admin"]
    assert not ada["can_change_role"]


def test_the_last_admin_rule_stops_where_the_servers_rule_stops():
    """`disable_user` has NO last-admin guard (auth_human.py:530-545) — only a
    self-disable one. Hiding the button anyway would be a rule we invented,
    and the same mistake as a client inventing password policy."""
    rows = sa.team_rows(USERS, "someone.else@acme.co")
    assert _by_email(rows, "ada@acme.co")["can_disable"]


def test_a_second_active_admin_unlocks_the_first():
    users = USERS + [{"id": "u4", "email": "kat@acme.co", "role": "admin",
                      "disabled": False}]
    rows = sa.team_rows(users, "someone.else@acme.co")
    ada = _by_email(rows, "ada@acme.co")
    assert not ada["last_admin"] and ada["can_change_role"]


def test_a_disabled_admin_does_not_count_as_the_active_one():
    users = [{"id": "u1", "email": "a@x.co", "role": "admin", "disabled": False},
             {"id": "u2", "email": "b@x.co", "role": "admin", "disabled": True}]
    rows = sa.team_rows(users, "")
    assert _by_email(rows, "a@x.co")["last_admin"]


def test_a_disabled_user_is_not_offered_a_re_invite():
    """auth_human.py:631 — "enable the user before re-inviting"."""
    rows = sa.team_rows(USERS, "")
    alan = _by_email(rows, "alan@acme.co")
    assert not alan["can_reinvite"] and alan["can_enable"]
    assert not alan["can_disable"]


def test_the_invite_check_is_shallow_but_catches_the_certain_cases():
    assert sa.invite_problem("", "member")
    assert sa.invite_problem("not-an-email", "member")
    assert sa.invite_problem("a@b.co", "owner")          # not a server role
    assert sa.invite_problem("a@b.co", "member") is None


def test_the_invite_body_omits_the_password_key():
    """Omitting it is exactly what makes the server email a set-password link
    instead of minting a temp password (auth_human.py:494)."""
    body = sa.create_user_body("  Ada@Acme.CO ", "Admin")
    assert body == {"email": "ada@acme.co", "role": "admin"}
    assert "password" not in body


def test_the_invite_result_carries_a_secret_only_when_one_was_sent():
    message, secret = sa.invite_result({"email": "a@b.co", "invited": True})
    assert secret == "" and "a@b.co" in message
    message, secret = sa.invite_result(
        {"email": "a@b.co", "temp_password": "hunter2"})
    assert secret == "hunter2"
    assert "hunter2" not in message        # never in anything user-visible


def test_each_team_warning_names_the_consequence():
    assert "signed out" in sa.disable_user_warning("a@b.co")
    assert "a@b.co" in sa.disable_user_warning("a@b.co")
    assert "manage" in sa.role_change_warning("a@b.co", "admin")
    assert "lose access" in sa.role_change_warning("a@b.co", "member")


# ══ account activity ════════════════════════════════════════════════════════
def test_every_label_maps_a_real_server_action():
    """A label for an action the backend never records is dead weight that
    reads as coverage. Checked against the source, not a memory of it."""
    root = _HERE.parent / "backend" / "app"
    if not root.exists():
        pytest.skip("backend sources not present")
    text = "".join(p.read_text(encoding="utf-8") for p in root.rglob("*.py"))
    missing = [a for a in sa.AUDIT_LABELS if f'action="{a}"' not in text]
    assert missing == [], f"{missing} are labelled but never recorded"


def test_every_recorded_action_has_a_label():
    """The web maps eight of them; the rest rendered as `webhook.create`."""
    root = _HERE.parent / "backend" / "app"
    if not root.exists():
        pytest.skip("backend sources not present")
    import re
    recorded = set()
    for path in root.rglob("*.py"):
        # `app/bundled/` is a VERBATIM copy of verifier/foxy_verify.py, shipped
        # inside the export bundle (E2, `56840d6`). It is a standalone CLI, not
        # application code: it records no audit action, and a byte-identity guard
        # forbids editing it. Its argparse calls read `action="store_true"`,
        # which this regex cannot tell apart from an audit action — so the
        # directory is skipped rather than the pattern loosened.
        #
        # Do NOT "fix" a future failure here by adding the offending string to
        # AUDIT_LABELS. That labels a thing which is not an action, and the next
        # genuinely unlabelled action then passes unnoticed — which is the one
        # job this test has.
        if "bundled" in path.parts:
            continue
        recorded |= set(re.findall(r'action="([a-z_.]+)"',
                                   path.read_text(encoding="utf-8")))
    assert recorded - set(sa.AUDIT_LABELS) == set()


def test_an_unknown_action_falls_through_to_its_raw_value():
    """A new server action must show up, not disappear."""
    rows = sa.audit_rows([{"action": "brand.new", "actor_email": "a@b.co"}])
    assert rows[0]["label"] == "brand.new"


def test_audit_rows_survive_a_row_that_is_not_a_dict():
    assert sa.audit_rows(["nope", {"action": "key.create"}])[0]["label"] \
        == "Created API key"


# ══ webhooks ════════════════════════════════════════════════════════════════
HOOKS = [{"id": "w1", "url": "https://a.co/h", "events": "breach",
          "secret_prefix": "whsec_1a2b3…", "last_status": "200"},
         {"id": "w2", "url": "https://b.co/h", "events": "breach,graded",
          "secret_prefix": "whsec_9f8e7…", "last_status": "timeout"},
         {"id": "w3", "url": "https://c.co/h", "events": "graded",
          "secret_prefix": "whsec_0000…", "last_status": None}]


def test_a_webhook_row_never_carries_the_secret():
    """`GET /v1/webhooks` only ever sends an 11-char prefix (webhooks.py:47);
    if a future response leaked the full one this must not surface it."""
    rows = sa.webhook_rows([dict(HOOKS[0], secret="whsec_" + "f" * 48)])
    assert not any("whsec_ffff" in str(v) for v in rows[0].values())


def test_delivery_status_is_toned_by_what_the_endpoint_answered():
    ok, bad, never = sa.webhook_rows(HOOKS)
    assert ok["tone"] == "ok" and "200" in ok["status_text"]
    assert bad["tone"] == "bad" and "timeout" in bad["status_text"]
    assert never["tone"] == "mute" and never["status_text"] == "never delivered"
    assert sa.webhook_rows([dict(HOOKS[0], last_status="503")])[0]["tone"] == "bad"


def test_only_the_outcome_is_toned_not_the_whole_meta_line():
    """Colouring the events and the secret prefix by delivery status said
    nothing — a working endpoint's prefix was green, a failing one's red."""
    row = sa.webhook_rows(HOOKS)[1]
    assert "timeout" not in row["meta"]
    assert "breach,graded" in row["meta"] and "whsec_9f8e7…" in row["meta"]


def test_the_add_form_checks_what_the_server_checks():
    assert sa.webhook_problem("", ["breach"])
    assert sa.webhook_problem("ftp://a.co", ["breach"])       # webhooks.py:73
    assert sa.webhook_problem("https://a.co", [])             # webhooks.py:76
    assert sa.webhook_problem("https://a.co", ["breach"]) is None


def test_every_offered_event_is_one_the_server_accepts():
    path = _HERE.parent / "backend" / "app" / "webhook_delivery.py"
    if not path.exists():
        pytest.skip("backend sources not present")
    text = path.read_text(encoding="utf-8")
    for value, _label, _default in sa.WEBHOOK_EVENTS:
        assert f'"{value}"' in text, f"{value} is not in VALID_EVENTS"


def test_a_test_that_the_endpoint_rejected_is_reported_as_a_failure():
    """`POST /{id}/test` returns 200 carrying the DELIVERY's status, so "test
    sent" would call a 500 from the customer's endpoint a success."""
    assert "delivered" in sa.webhook_test_result(None, {"status": "200"})
    assert "not delivered" in sa.webhook_test_result(None, {"status": "500"})
    assert "not delivered" in sa.webhook_test_result(None, {"status": "error"})
    assert "failed" in sa.webhook_test_result(404, None, "webhook not found")
    assert "did not report" in sa.webhook_test_result(None, {})


def test_removing_a_webhook_says_the_secret_cannot_come_back():
    warning = sa.webhook_remove_warning("https://a.co/h")
    assert "https://a.co/h" in warning and "NEW signing secret" in warning


# ══ SSO ═════════════════════════════════════════════════════════════════════
CONNECTION = {"configured": True, "email_domain": "acme.co",
              "issuer": "https://acme.okta.com", "client_id": "0oa1",
              "active": True, "has_secret": True}


def test_the_sso_view_has_no_field_that_could_hold_the_secret():
    view = sa.sso_view(CONNECTION)
    assert "client_secret" not in view
    assert view["has_secret"] is True
    assert view["status"] == "active" and view["tone"] == "ok"


def test_an_inactive_connection_reads_as_disabled_not_absent():
    view = sa.sso_view(dict(CONNECTION, active=False))
    assert view["configured"] and view["status"] == "disabled"


def test_no_connection_is_not_the_same_as_no_answer():
    assert sa.sso_view({"configured": False})["status"] == sa.SSO_NOT_SET_UP
    assert sa.SSO_UNKNOWN != sa.SSO_NOT_SET_UP


def test_the_sso_check_mirrors_the_servers_own_rules():
    assert sa.sso_problem("", "https://i", "c", "s", False)
    assert sa.sso_problem("acme", "https://i", "c", "s", False)     # not bare
    assert sa.sso_problem("a@acme.co", "https://i", "c", "s", False)
    assert sa.sso_problem("acme.co", "http://i", "c", "s", False)   # not https
    assert sa.sso_problem("acme.co", "https://i", "", "s", False)
    assert sa.sso_problem("acme.co", "https://i", "c", "  ", False) is not None
    # …and blank IS allowed once one is stored (sso.py:86-90).
    assert sa.sso_problem("acme.co", "https://i", "c", "", True) is None


def test_a_blank_secret_is_sent_blank_so_the_server_keeps_the_stored_one():
    body = sa.sso_body(" ACME.co ", " https://i ", " c ", "", True)
    assert body["client_secret"] == ""
    assert body["email_domain"] == "acme.co" and body["active"] is True


def test_the_secret_helper_describes_the_save_that_will_actually_happen():
    assert "Leave this blank" in sa.sso_secret_help(True)
    assert "Required" in sa.sso_secret_help(False)


def test_the_callback_url_comes_from_the_backend_and_is_not_invented():
    assert sa.sso_callback_url("") == ""
    assert sa.sso_callback_url("https://api.foxyaudit.tech/") \
        == "https://api.foxyaudit.tech/v1/auth/sso/callback"


# ══ the module must not be able to keep a secret ════════════════════════════
def test_settings_admin_cannot_persist_or_log_anything():
    """Same structural check as D11a's settings_data and D9's access_data."""
    tree = ast.parse((_HERE / "settings_admin.py").read_text(encoding="utf-8"))
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
    assert not banned_calls, f"settings_admin can persist/log via {banned_calls}"
    assert not banned_imports, f"settings_admin imports {banned_imports}"


# ══ the built cards ═════════════════════════════════════════════════════════
@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def console(app, tmp_path_factory):
    from PyQt6.QtCore import QSettings
    from fox_settings import FoxSettings
    from foxy_client import MemorySecretStore
    from dashboard import DashboardWindow

    path = tmp_path_factory.mktemp("d11b") / "console.ini"
    store = QSettings(str(path), QSettings.Format.IniFormat)
    settings = FoxSettings(store, MemorySecretStore())
    settings.set_backend_url("https://api.example.com")
    win = DashboardWindow(settings=settings)
    yield win
    win.close()


ME_ADMIN = {"email": "ada@acme.co", "role": "admin", "org_id": "o1",
            "full_name": "Ada", "mfa_enabled": False}
ME_MEMBER = dict(ME_ADMIN, role="member")


def test_the_admin_cards_build(console):
    for attr in ("team_rows", "team_strip", "team_add_btn", "acct_audit_rows",
                 "acct_audit_strip", "wh_rows", "wh_strip", "wh_url",
                 "wh_events", "wh_add_btn", "wh_form", "sso_fields",
                 "sso_secret", "sso_active", "sso_callback", "sso_strip",
                 "sso_form", "sso_status_chip", "sso_remove_btn"):
        assert hasattr(console, attr), attr
    assert set(console.wh_events) == {"breach", "graded"}
    assert set(console.sso_fields) == {"domain", "issuer", "client_id"}


def test_the_callback_url_is_shown_not_left_as_a_placeholder(console):
    console._apply_sso(sa.sso_view(CONNECTION))
    assert console.sso_callback.text() \
        == "https://api.example.com/v1/auth/sso/callback"


def test_a_member_is_told_rather_than_refused(console):
    """Every one of these endpoints is require_role("admin"). The web hides
    the cards; hiding leaves a member wondering where team management went."""
    console._on_team(None, status=403)
    console._on_account_audit(None, status=403)
    console._on_webhooks(None, status=403)
    console._on_sso(None, status=403)
    assert not console.team_add_btn.isEnabled()
    assert not console.wh_form.isEnabled()
    assert not console.sso_form.isEnabled()
    for strip, notice in ((console.team_strip, sa.TEAM_MEMBER_NOTICE),
                          (console.acct_audit_strip, sa.AUDIT_MEMBER_NOTICE),
                          (console.wh_strip, sa.WEBHOOK_MEMBER_NOTICE),
                          (console.sso_strip, sa.SSO_MEMBER_NOTICE)):
        from panel_state import PanelState
        assert strip.state() is PanelState.EMPTY     # a measurement, not a failure
        assert strip.body.text() == notice


def test_a_member_is_not_told_the_workspace_has_no_sso(console):
    """We did not ask, so we do not know — "not set up" would be a claim about
    a workspace that may well have SSO configured."""
    console._on_sso(CONNECTION)
    assert console.sso_status_chip.text() == "ACTIVE"
    console._on_sso(None, status=403)
    assert console.sso_status_chip.text() == sa.SSO_UNKNOWN.upper()
    assert all(f.text() == "" for f in console.sso_fields.values())
    assert not console.sso_remove_btn.isVisible()


def test_an_unreachable_backend_leaves_four_strips_placed_and_visible(console):
    """The first version set the strips by hand after `clear_rows` had taken
    them out of their layout, so three of them floated at a stale geometry and
    were clipped by their own card — invisible to every assertion, obvious the
    moment it was rendered."""
    from panel_state import PanelState
    console._on_team(USERS)                    # a populated start
    console._on_settings_me(None)              # /v1/auth/me failed
    for strip, layout in ((console.team_strip, console.team_rows),
                          (console.acct_audit_strip, console.acct_audit_rows),
                          (console.wh_strip, console.wh_rows)):
        assert strip.state() is PanelState.ERROR
        assert not strip.isHidden()
        placed = [layout.itemAt(i).widget() for i in range(layout.count())]
        assert strip in placed, "the strip is parented but in no layout"
        # isHidden, not isVisible: this fixture never shows the console, so
        # every descendant reports invisible regardless — the same reason
        # test_row_lists checks hidden-ness.
        assert not strip.retry_btn.isHidden(), "an error with no way forward"
    assert console.sso_strip.state() is PanelState.ERROR
    assert console.sso_status_chip.text() == sa.SSO_UNKNOWN.upper()


def _signed_in(console, monkeypatch):
    """`_can_fetch` wants a visible window and a credential; `_is_admin` wants
    a human session. Neither exists in a headless fixture."""
    monkeypatch.setattr(console, "_can_fetch", lambda: True)
    monkeypatch.setattr(console.client, "has_session", lambda: True)


def test_the_admin_cards_load_only_after_the_role_is_known(console, monkeypatch):
    """Firing them beside `/v1/auth/me` would gate them on a role that is
    stale on the first visit and after any mid-session change."""
    import dashboard as dashboard_mod
    calls = []
    _signed_in(console, monkeypatch)
    monkeypatch.setattr(dashboard_mod, "spawn_worker",
                        lambda *a, **k: calls.append(a[2]))
    console._on_settings_me(ME_ADMIN)
    assert "/v1/auth/users" in calls
    assert "/v1/account/audit?limit=50" in calls
    assert "/v1/webhooks" in calls
    assert "/v1/auth/sso/connection" in calls


def test_a_member_never_fires_an_admin_request(console, monkeypatch):
    import dashboard as dashboard_mod
    calls = []
    _signed_in(console, monkeypatch)
    monkeypatch.setattr(dashboard_mod, "spawn_worker",
                        lambda *a, **k: calls.append(a[2]))
    console._on_settings_me(ME_MEMBER)
    assert "/v1/auth/users" not in calls
    assert "/v1/webhooks" not in calls
    assert not console.team_add_btn.isEnabled()


def test_a_role_change_does_not_open_a_second_step_up_dialog(console,
                                                             monkeypatch):
    """The client emits `step_up_required` and D1's central handler runs the
    dialog and replays. A second one here would stack two on each other."""
    import dashboard as dashboard_mod
    seen = {}
    monkeypatch.setattr(dashboard_mod, "spawn_worker",
                        lambda *a, **k: seen.update(k) or None)
    monkeypatch.setattr(console.toast, "show_message",
                        lambda m: seen.setdefault("toasts", []).append(m))
    row = sa.team_rows(USERS + [{"id": "u4", "email": "kat@acme.co",
                                 "role": "admin", "disabled": False}],
                       "kat@acme.co")
    target = _by_email(row, "grace@acme.co")
    monkeypatch.setattr(console, "_confirm_danger", lambda *a: True)
    console.change_team_role(target)
    seen["on_err"]("HTTP 403: step_up_required")
    assert any("Confirm your identity" in m for m in seen["toasts"])


def test_the_webhook_secret_is_shown_once_and_kept_nowhere(console,
                                                           monkeypatch):
    import dashboard as dashboard_mod
    import settings_admin_page
    shown = {}

    class _Dialog:
        def __init__(self, title, secret, parent=None):
            shown["title"], shown["secret"] = title, secret

        def exec(self):
            return 1

    monkeypatch.setattr(dashboard_mod, "ShownOnceDialog", _Dialog)
    monkeypatch.setattr(dashboard_mod, "spawn_worker", lambda *a, **k: None)
    monkeypatch.setattr(console.toast, "show_message", lambda m: None)
    console._on_webhook_created({"id": "w9", "url": "https://a.co",
                                 "secret": "whsec_" + "a" * 48})
    assert shown["secret"] == "whsec_" + "a" * 48
    leaked = [k for k, v in vars(console).items()
              if isinstance(v, str) and "whsec_" in v]
    assert leaked == [], f"the secret was stored on the window as {leaked}"
    assert settings_admin_page is not None


def test_a_created_webhook_with_no_secret_says_so_rather_than_pretending(
        console, monkeypatch):
    import dashboard as dashboard_mod
    messages = []
    monkeypatch.setattr(dashboard_mod, "spawn_worker", lambda *a, **k: None)
    monkeypatch.setattr(console.toast, "show_message", messages.append)
    console._on_webhook_created({"id": "w9", "url": "https://a.co"})
    assert any("did not return a signing secret" in m for m in messages)


def test_the_add_form_refuses_before_the_round_trip(console, monkeypatch):
    import dashboard as dashboard_mod
    calls = []
    monkeypatch.setattr(dashboard_mod, "spawn_worker",
                        lambda *a, **k: calls.append(a[2]))
    console.wh_form.setEnabled(True)
    console.wh_url.setText("not-a-url")
    console.add_webhook()
    assert calls == []
    assert not console.wh_status.isHidden()
    console.wh_url.setText("https://a.co/hook")
    console.wh_events["breach"].setChecked(False)
    console.wh_events["graded"].setChecked(False)
    console.add_webhook()
    assert calls == []
    console.wh_events["breach"].setChecked(True)
    console.add_webhook()
    assert calls == ["/v1/webhooks"]
