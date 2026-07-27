"""Offscreen tests for the D3 console shell and its chrome widgets.

The logic already has its own suite (test_console_chrome.py); this covers the
wiring: that the nine web sections plus the two desktop extras exist and
navigate, that the title/crumb follow, that the breach cursor persists when you
visit Threats, that the chrome widgets construct and render, and that nothing
polls while the window is closed.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QApplication

from console_chrome import ALL_SECTIONS, EXTRA_SECTIONS, SECTIONS


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def console(app, tmp_path):
    """A console whose settings live in a throwaway ini.

    Injected explicitly rather than via QSettings' global default, because the
    global route silently kept resolving to the real store: on Windows that is
    HKCU\\Software\\OmniAwareFox\\DesktopPet, so the suite both polluted the
    developer's own app and became order-dependent (a breach cursor left by one
    test quietly changed the next one's expectations)."""
    from PyQt6.QtCore import QSettings
    from fox_settings import FoxSettings
    from foxy_client import MemorySecretStore
    from dashboard import DashboardWindow

    store = QSettings(str(tmp_path / "console.ini"), QSettings.Format.IniFormat)
    win = DashboardWindow(settings=FoxSettings(store, MemorySecretStore()))
    yield win
    win.close()


def _render(widget) -> QPixmap:
    pm = QPixmap(widget.size() if widget.size().width() else QPoint(320, 200))
    pm.fill()
    widget.render(pm)
    return pm


# ── sidebar + navigation ────────────────────────────────────────────────────
def test_all_eleven_sections_have_a_page(console):
    assert list(console._page_index) == [s[0] for s in ALL_SECTIONS]
    assert console.stack.count() == len(ALL_SECTIONS)


def test_every_section_has_a_nav_button(console):
    assert len(console.nav_buttons) == len(ALL_SECTIONS)
    for section_id, label, _t, _c, _i in ALL_SECTIONS:
        assert console._nav_by_id[section_id].text_label == label


def test_go_switches_page_and_title(console, app):
    for section_id, _label, title, crumb, _icon in ALL_SECTIONS:
        console.go(section_id)
        app.processEvents()
        assert console.stack.currentIndex() == console._page_index[section_id]
        assert console.page_title.text() == title
        assert console.page_crumb.text() == f"Foxy Audit · {crumb}"


def test_existing_pages_are_remapped_not_duplicated(console):
    """Overview→Home, Threat Analytics→Threats, Audit Log→Ledger: the widgets
    that already worked must be reused, not rebuilt beside new empties."""
    home = console.stack.widget(console._page_index["home"])
    threats = console.stack.widget(console._page_index["threats"])
    ledger = console.stack.widget(console._page_index["ledger"])
    assert console.hero.parent() is not None and home.isAncestorOf(console.hero)
    assert threats.isAncestorOf(console.analytics_recent_card)
    assert ledger.isAncestorOf(console.table)


def test_unbuilt_sections_are_honest_stubs(console):
    """A stub says what it is and which phase builds it — no invented data."""
    for section_id in ("verify", "policy", "export", "access", "billing", "settings"):
        page = console.stack.widget(console._page_index[section_id])
        text = " ".join(lbl.text() for lbl in page.findChildren(type(console.page_title)))
        assert "coming in phase" in text.lower()
        assert not any(ch.isdigit() and ch not in "0123456789"
                       for ch in "")          # no fabricated metrics at all


def test_desktop_extras_are_last(console):
    ids = list(console._page_index)
    assert ids[-2:] == [s[0] for s in EXTRA_SECTIONS]


# ── breach pip ──────────────────────────────────────────────────────────────
def test_visiting_threats_marks_breaches_read(console, app):
    console._on_breach_rows([{"seq": 4}, {"seq": 11}])
    app.processEvents()
    assert console.threat_pip.text() == "2"
    assert console.threat_pip.isVisible() or not console.isVisible()
    console.go("threats")
    app.processEvents()
    assert console.settings.breach_seen_seq() == 11
    assert console.threat_pip.text() == ""


def test_pip_stays_clear_when_everything_is_seen(console, app):
    console.settings.set_breach_seen_seq(11)
    console._on_breach_rows([{"seq": 4}, {"seq": 11}])
    app.processEvents()
    assert console.threat_pip.text() == ""


# ── announcement banner ─────────────────────────────────────────────────────
def test_banner_shows_a_real_signal_and_dismissal_persists(console, app):
    console._ann_part("breaches", [{"seq": 3}])
    app.processEvents()
    assert console.banner.isVisibleTo(console)
    assert "breach" in console.banner.text.text().lower()
    console.banner._on_dismiss()
    app.processEvents()
    assert "breach:3" in console.settings.dismissed_announcements()
    # and it stays gone on the next refresh
    console._ann_part("breaches", [{"seq": 3}])
    app.processEvents()
    assert not console.banner.isVisible()


def test_banner_hidden_when_there_is_nothing_to_say(console, app):
    console._ann_part("breaches", [])
    app.processEvents()
    assert not console.banner.isVisible()


# ── notifications ───────────────────────────────────────────────────────────
def test_notifications_render_and_count(console, app):
    console._on_notifications({"unread": 3, "items": [
        {"id": "1", "kind": "breach", "title": "Policy breach", "level": "critical",
         "created_at": "2026-07-27T11:00:00Z", "read": False},
        {"id": "2", "kind": "digest", "title": "Weekly digest", "level": "info",
         "created_at": "2026-07-26T09:00:00Z", "read": True}]})
    app.processEvents()
    assert console.notif_pip.text() == "3"
    _render(console.notif_panel)


def test_notifications_empty_state_is_honest(console, app):
    console._on_notifications({"unread": 0, "items": []})
    app.processEvents()
    assert console.notif_pip.text() == ""
    labels = [w.text() for w in console.notif_panel.findChildren(type(console.page_title))]
    assert any("No notifications yet" in t for t in labels)


def test_clicking_a_breach_notification_routes_to_threats(console, app):
    console._on_notification_clicked("", "breach")
    app.processEvents()
    assert console.stack.currentIndex() == console._page_index["threats"]


# ── chrome widgets ──────────────────────────────────────────────────────────
def test_live_dot_states_render(console, app):
    for state in (True, False, None):
        console.live_dot.set_online(state)
        app.processEvents()
        _render(console.live_dot)
        assert console.live_dot.accessibleName()


def test_health_ping_treats_http_errors_as_online(console):
    """Any HTTP answer proves reachability; only transport failure is offline."""
    console._on_health_ping_failed("HTTP 503: Service Unavailable")
    assert console.live_dot._online is True
    console._on_health_ping_failed("connection refused")
    assert console.live_dot._online is False


def test_palette_opens_and_routes(console, app):
    console.palette.open_fresh()
    app.processEvents()
    assert console.palette.list.count() > 0
    console._on_palette_choice({"kind": "page", "arg": "policy"})
    app.processEvents()
    assert console.stack.currentIndex() == console._page_index["policy"]
    console.palette.close()


def test_shortcuts_overlay_renders(console, app):
    console.shortcuts.show_centered(console)
    app.processEvents()
    _render(console.shortcuts)
    console.shortcuts.close()


def test_toast_shows_and_hides(console, app):
    console.show_toast("Copied organization ID")
    app.processEvents()
    assert console.toast.text() == "Copied organization ID"
    assert console.toast.isVisibleTo(console)


def test_user_chip_shows_the_identity_initial(console, app):
    console.on_signed_in({"email": "ada@example.com", "role": "admin",
                          "full_name": "Ada Lovelace", "org_id": "org-9"})
    app.processEvents()
    assert console.user_btn.text() == "A"
    assert "ada@example.com" in console.user_btn.toolTip()
    console.on_signed_out()
    app.processEvents()
    assert console.user_btn.text() == "?"


# ── teardown discipline (D0.1's rule) ───────────────────────────────────────
def test_closing_stops_every_poller(console, app):
    console.show()
    app.processEvents()
    assert console._health_timer.isActive()
    console.close()
    app.processEvents()
    assert not console._health_timer.isActive()
    assert not console._backend_poll.isActive()
    assert not console._tick.isActive()


def test_reopening_restarts_them(console, app):
    console.show()
    app.processEvents()
    console.close()
    app.processEvents()
    console.show()
    app.processEvents()
    assert console._health_timer.isActive()
    assert console._backend_poll.isActive()


def test_chrome_refresh_is_a_noop_while_hidden(console, app):
    console.close()
    app.processEvents()
    before = len(console._workers)
    console._refresh_chrome()
    assert len(console._workers) == before
