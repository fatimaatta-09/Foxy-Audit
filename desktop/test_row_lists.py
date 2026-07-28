"""Every list in the console must be VISIBLE the moment it is filled.

`QLayout.addWidget` does not show its child — Qt leaves it hidden until the
layout next activates. Normally the next event-loop pass does that and nobody
notices; when a repaint lands first, the panel draws as an empty card with
every row present, hidden, at the default 640×480 geometry.

D10 fixed that in `_fill_rows` and claimed "every list in the console goes
through here". It did not: Home had five hand-rolled copies of the same loop,
and the notifications panel and the chat history had one each — seven in all.
So this file pins two things, neither of which is a comment:

* **behaviourally**, that rows are visible immediately after a refill, with no
  event loop pumped in between (`count()` is visibility-agnostic, which is
  exactly why the D10 test could not have caught this);
* **structurally**, that no module rebuilds a list by hand any more.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from PyQt6.QtWidgets import QApplication

import panel_state

_HERE = Path(__file__).resolve().parent
_SOURCES = sorted(p for p in _HERE.glob("*.py") if not p.name.startswith("test_"))


# ══ structural ══════════════════════════════════════════════════════════════
def test_only_panel_state_rebuilds_a_row_list():
    """A hand-rolled take/delete/add loop is how the bug got in, and how it
    would get back in. `clear_rows` is the only place `takeAt` may appear."""
    offenders = [p.name for p in _SOURCES
                 if ".takeAt(" in p.read_text(encoding="utf-8")
                 and p.name != "panel_state.py"]
    assert offenders == [], (
        f"{offenders} rebuild a list by hand — use panel_state.clear_rows / "
        f"fill_visible so the rows are actually shown")


def test_every_adder_shows_what_it_adds():
    """The whole point of the helpers. Checked in the source, because a helper
    that quietly stopped calling show() would put all seven lists back."""
    tree = ast.parse((_HERE / "panel_state.py").read_text(encoding="utf-8"))
    for name in ("add_visible", "insert_visible"):
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == name)
        calls = {getattr(c.func, "attr", None) for c in ast.walk(fn)
                 if isinstance(c, ast.Call)}
        assert "show" in calls, f"{name} does not show the widget it adds"
    fill = next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == "fill_visible")
    calls = {getattr(c.func, "attr", None) or getattr(c.func, "id", None)
             for c in ast.walk(fill) if isinstance(c, ast.Call)}
    assert {"add_visible", "activate"} <= calls


# ══ behavioural ═════════════════════════════════════════════════════════════
@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def console(app, tmp_path_factory):
    from PyQt6.QtCore import QSettings
    from fox_settings import FoxSettings
    from foxy_client import MemorySecretStore
    from dashboard import DashboardWindow

    path = tmp_path_factory.mktemp("rows") / "console.ini"
    store = QSettings(str(path), QSettings.Format.IniFormat)
    win = DashboardWindow(settings=FoxSettings(store, MemorySecretStore()))
    win.show()
    yield win
    win.close()


def _hidden(layout, *, start: int = 0) -> list:
    """Indices of rows Qt will not paint.

    The container has to be VISIBLE for this to mean anything: a child added
    to a hidden parent is not itself "hidden" — it inherits the parent's state
    and is shown with it. Every case below makes its container visible first,
    which is also the real-world condition (the bug appeared refilling a page
    the user was looking at). Getting this wrong is how a test reads as
    covering something it does not — I checked by reverting each fix.
    """
    return [i for i in range(start, layout.count())
            if layout.itemAt(i).widget() is not None
            and layout.itemAt(i).widget().isHidden()]


LEDGER = [{"seq": 1, "chain_hash": "a" * 64, "policy_tag": "phi",
           "gemini_verdict": {"policy_breach": False}, "agent": "gpt",
           "timestamp": "2026-07-28T10:00:00Z"} for _ in range(3)]
USAGE = {"quota": {"monthly_log_quota": 1000, "used_this_month": 10},
         "days": [{"day": "2026-07-27", "logs_count": 2, "tokens_sum": 9,
                   "breach_count": 0},
                  {"day": "2026-07-28", "logs_count": 4, "tokens_sum": 11,
                   "breach_count": 1}]}
INVOICES = [{"id": "a", "amount_cents": 100, "currency": "usd",
             "status": "paid", "created_at": "2026-07-01T00:00:00Z"}]
THREATS = {"recent_high_risk": [{"seq": 3, "policy_tag": "phi",
                                 "risk_score": 90, "agent": "gpt",
                                 "timestamp": "2026-07-28T10:00:00Z"}]}
COVERAGE = {"clients": [{"client_id": "c1", "events": 5, "last_seq": 5,
                         "status": "ok"}], "observed_events": 5}
ONBOARDING = {"steps": [{"key": "sdk", "title": "Install", "done": True},
                        {"key": "key", "title": "Create a key", "done": False}],
              "dismissed": False}
#: Shaped by the real loader, so the row builder gets what it expects.
import home_data as _hd
ACTIVITY = _hd.merge_activity(
    [{"created_at": "2026-07-28T10:00:00Z", "action": "auth.login",
      "actor_email": "a@b.co", "detail": {}}], [])
TEAM = [{"id": "u1", "email": "a@b.co", "role": "admin", "disabled": False},
        {"id": "u2", "email": "c@b.co", "role": "member", "disabled": False}]
ACCOUNT_AUDIT = [{"id": "a1", "action": "key.create", "target": "prod",
                  "actor_email": "a@b.co",
                  "created_at": "2026-07-28T10:00:00Z"}]
WEBHOOKS = [{"id": "w1", "url": "https://a.co/h", "events": "breach",
             "secret_prefix": "whsec_1a2b3…", "last_status": "200"}]

# (name, layout attribute, how to fill it, where the rows start)
CASES = [
    ("Home · onboarding", "onboarding_steps",
     lambda c: c._on_onboarding(ONBOARDING), 0),
    ("Home · coverage", "coverage_table",
     lambda c: c._on_coverage(COVERAGE), "coverage_rows_start"),
    ("Home · recent ledger", "recent_ledger",
     lambda c: c._on_recent_ledger({"items": LEDGER}), 0),
    ("Home · active alerts", "home_alerts",
     lambda c: c._on_threats(THREATS), 0),
    ("Home · activity feed", "activity_list",
     lambda c: c._render_activity(ACTIVITY), 0),
    ("Billing · daily usage", "bil_usage_rows",
     lambda c: c._on_billing_usage(USAGE), 0),
    ("Billing · invoices", "bil_inv_rows",
     lambda c: c._on_invoices(INVOICES), 0),
    ("Settings · team", "team_rows", lambda c: c._on_team(TEAM), 0),
    ("Settings · account activity", "acct_audit_rows",
     lambda c: c._on_account_audit(ACCOUNT_AUDIT), 0),
    ("Settings · webhooks", "wh_rows", lambda c: c._on_webhooks(WEBHOOKS), 0),
]

#: Which section each list lives on — `go()` has to put the page on screen or
#: the container is hidden and `_hidden` proves nothing (see its docstring).
_PAGE_OF = {"bil_": "billing", "team_": "settings", "acct_": "settings",
            "wh_": "settings"}


@pytest.mark.parametrize("name,attr,fill,start", CASES,
                         ids=[c[0] for c in CASES])
def test_rows_are_visible_the_instant_they_are_filled(console, name, attr,
                                                      fill, start):
    """No `processEvents` anywhere in here on purpose — that is the whole
    point. Pumping the loop is what used to hide the bug."""
    console.go(next((page for prefix, page in _PAGE_OF.items()
                     if attr.startswith(prefix)), "home"))
    layout = getattr(console, attr)
    container = layout.parentWidget()
    if container is not None:
        container.show()          # see _hidden: a hidden parent hides nothing
    fill(console)
    first = getattr(console, start) if isinstance(start, str) else start
    assert layout.count() > first, f"{name}: nothing was added"
    hidden = _hidden(layout, start=first)
    assert hidden == [], (
        f"{name}: rows {hidden} were added but never shown — the panel will "
        f"paint as an empty card if anything repaints before the layout "
        f"activates")


def test_a_refill_leaves_no_hidden_rows_either(console):
    """The failing case was the SECOND fill, not the first."""
    console.go("billing")
    console.bil_usage_rows.parentWidget().show()
    console.go("home")
    console.recent_ledger.parentWidget().show()
    for _ in range(3):
        console._on_billing_usage(USAGE)
        console._on_recent_ledger({"items": LEDGER})
    assert _hidden(console.bil_usage_rows) == []
    assert _hidden(console.recent_ledger) == []
    assert console.bil_usage_rows.count() == 2      # and not stacking up
    assert console.recent_ledger.count() == 3


def test_the_notification_panel_shows_its_rows_too(console):
    """The seventh site, outside dashboard.py entirely."""
    panel = console.notif_panel
    panel.show()                  # a closed dropdown hides nothing, see _hidden
    panel.set_items([{"id": "1", "title": "Breach", "body": "x",
                      "level": "critical", "read": False},
                     {"id": "2", "title": "Quota", "body": "y",
                      "level": "warn", "read": True}])
    assert _hidden(panel.rows) == []
    panel.set_items([])                       # the empty state must show as well
    assert _hidden(panel.rows) == []
    panel.hide()
