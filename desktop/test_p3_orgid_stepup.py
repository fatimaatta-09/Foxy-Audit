"""P3 §7.1 · the desktop's org-ID surfaces go through the step-up gate.

The desktop is a shipped client of the same API, so removing `org_id` from
`/v1/auth/me` without touching it would have left three surfaces quietly
broken — the command palette copying `None`, the export card showing a dash for
ever, and the Settings identity row claiming the id was missing.

The palette entry was kept rather than deleted: FoxyClient already raises
`StepUpRequired` on the 403, omni_fox already runs the D1 dialog, and the
request is already replayed. Wiring one endpoint into that was strictly less
work than removing a feature people use.
"""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QApplication

import settings_data as sd
from test_d11_settings import ME, app, console  # noqa: F401 — fixtures

REVEAL_PATH = "/v1/account/org-id"


class _Spy:
    """Capture what reveal_org_id would have sent, without a network."""

    def __init__(self):
        self.calls = []

    def __call__(self, client, method, path, **kw):
        self.calls.append((method, path, kw))
        return None


@pytest.fixture(autouse=True)
def fresh(console):
    """`console` is module-scoped (building a DashboardWindow per test is slow),
    so the revealed-id cache has to be cleared explicitly — otherwise one test's
    successful reveal makes the next one's "must still be masked" pass for the
    wrong reason."""
    console._org_id = ""
    console._export_org = ""
    console.cmd_palette.set_org_id(None)
    if hasattr(console, "set_org_reveal"):
        console.set_org_reveal.setEnabled(True)
    return console


@pytest.fixture
def spy(console, monkeypatch):
    s = _Spy()
    import dashboard as dashboard_mod
    monkeypatch.setattr(dashboard_mod, "spawn_worker", s)
    return s


# ── the request itself ─────────────────────────────────────────────────────

def test_the_reveal_posts_the_step_up_gated_endpoint(console, spy):
    console.reveal_org_id(lambda _oid: None)
    assert [(m, p) for m, p, _ in spy.calls] == [("POST", REVEAL_PATH)]


def test_a_known_org_id_is_not_re_fetched(console, spy):
    """One confirmation serves every surface inside the grant window."""
    console._org_id = "org-abc"
    seen = []
    console.reveal_org_id(seen.append)
    assert seen == ["org-abc"]
    assert spy.calls == [], "a cached id must not cost a second round trip"


# ── guard 4: copy-org must never copy None ─────────────────────────────────

def test_copy_org_copies_the_revealed_id(console, spy):
    console._on_org_id({"org_id": "org-real-1234"}, console._copy_org_id)
    assert QApplication.clipboard().text() == "org-real-1234"


def test_copy_org_copies_nothing_when_the_reveal_is_refused(console, spy):
    """The regression this task exists to prevent: `me.get("org_id")` returning
    None and the palette cheerfully putting it on the clipboard."""
    QApplication.clipboard().setText("untouched")
    console._on_org_id({}, console._copy_org_id)
    assert QApplication.clipboard().text() == "untouched"
    console._on_org_id(None, console._copy_org_id)
    assert QApplication.clipboard().text() == "untouched"


def test_a_step_up_challenge_copies_nothing_and_says_why(console, spy):
    QApplication.clipboard().setText("untouched")
    console._on_org_id_error("HTTP 403: step_up_required", "copy the organization ID")
    assert QApplication.clipboard().text() == "untouched"


def test_the_palette_is_never_handed_an_id_the_app_does_not_have(console):
    """`set_org_id` is fed from the cache, not from the `me` payload — so the
    entry's arg is None until a reveal really happened."""
    console._apply_identity(ME)
    assert console.cmd_palette._org_id is None


# ── the error path never leaks the server's message ────────────────────────

def test_a_failure_reports_the_status_code_not_the_server_text(console, spy):
    shown = []
    console.show_toast = shown.append
    console._on_org_id_error("HTTP 500: postgres said something private", "reveal it")
    assert shown, "a failure must be surfaced"
    assert "postgres" not in shown[0], "the server's message must not reach the UI"
    assert "500" in shown[0]


# ── Settings identity row ──────────────────────────────────────────────────

def test_settings_shows_the_mask_until_someone_asks(console):
    console._on_settings_me(ME)
    assert console.set_readonly["org_id"].text() == sd.ORG_ID_MASK
    assert console.set_org_reveal.isEnabled()


def test_settings_reveal_fills_the_field_and_retires_the_button(console):
    console._on_settings_me(ME)
    console._on_settings_org_id("org-xyz-999")
    assert console.set_readonly["org_id"].text() == "org-xyz-999"
    assert not console.set_org_reveal.isEnabled()


def test_a_refused_settings_reveal_keeps_the_mask_and_explains(console):
    console._on_settings_me(ME)
    console._on_settings_org_id_refused("step_up")
    assert console.set_readonly["org_id"].text() == sd.ORG_ID_MASK
    assert "not revealed" in console.set_org_status.text()
    # Still offered — a refusal is not a dead end.
    assert console.set_org_reveal.isEnabled()


# ── export card ────────────────────────────────────────────────────────────

def test_the_export_card_holds_no_org_id_at_load(console):
    """Even if `me` still carried one, the card must not take it from there."""
    console._on_export_org(dict(ME, preferences={}))
    assert console._export_org == ""


def test_revealing_the_export_card_fetches_the_id(console, spy):
    console._on_export_org({"preferences": {}})
    console.on_reveal_metadata(True)
    assert ("POST", REVEAL_PATH) in [(m, p) for m, p, _ in spy.calls]


def test_hiding_the_export_card_fetches_nothing(console, spy):
    console._on_export_org({"preferences": {}})
    console.on_reveal_metadata(False)
    assert spy.calls == []


# ── the audit label the backend action needs ───────────────────────────────

def test_the_reveal_action_reads_as_a_sentence_in_the_account_trail(console):
    import settings_admin as sa
    assert sa.AUDIT_LABELS["account.org_id_reveal"] == "Revealed the organization id"
