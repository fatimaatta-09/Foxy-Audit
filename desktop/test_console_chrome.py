"""Logic tests for the console chrome (D3).

`console_chrome` deliberately imports no Qt, so everything the shell *decides*
— which palette rows to show, how many breaches are unseen, which announcement
wins, where a notification routes — is testable without a screen. Each case
below is pinned against the live web behaviour it mirrors.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from console_chrome import (
    ALL_SECTIONS, EXTRA_SECTIONS, QUICK_NAV, SECTIONS, announcement,
    avatar_initial, badge_text, breach_pip, notification_target, palette_entries,
    relative_time,
)

NOW = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)


# ── sections ────────────────────────────────────────────────────────────────
def test_the_nine_web_sections_in_the_webs_order():
    assert [s[0] for s in SECTIONS] == [
        "home", "threats", "ledger", "verify", "policy",
        "export", "access", "billing", "settings"]


def test_desktop_extras_come_after_the_web_nine():
    assert [s[0] for s in EXTRA_SECTIONS] == ["system", "sandbox"]
    assert [s[0] for s in ALL_SECTIONS[:9]] == [s[0] for s in SECTIONS]


def test_quick_nav_covers_every_web_section():
    """g-then-letter must reach all nine; the web's 'd' alias points at home."""
    targets = set(QUICK_NAV.values())
    assert targets == {s[0] for s in SECTIONS}
    assert QUICK_NAV["d"] == "home" == QUICK_NAV["h"]


# ── command palette (web build(), html:1730-1743) ───────────────────────────
def test_empty_query_lists_every_section():
    rows = palette_entries("")
    pages = [r for r in rows if r["kind"] == "page"]
    assert len(pages) == len(ALL_SECTIONS)
    assert pages[0]["label"] == "Go to Overview"


def test_query_filters_by_title_and_id():
    assert [r["arg"] for r in palette_entries("billing") if r["kind"] == "page"] \
        == ["billing"]
    assert [r["arg"] for r in palette_entries("ledger") if r["kind"] == "page"] \
        == ["ledger"]
    # the web matches the TITLE too, so "passport" finds Export
    assert [r["arg"] for r in palette_entries("passport") if r["kind"] == "page"] \
        == ["export"]


def test_pasting_a_hash_offers_verification():
    h = "a3f9c2b8d14e5f60"
    rows = palette_entries(h)
    verify = [r for r in rows if r["kind"] == "verify-hash"]
    assert len(verify) == 1
    assert verify[0]["arg"] == h
    assert h[:16] in verify[0]["label"]


def test_short_hex_is_not_a_hash():
    """The web's threshold is 8 hex chars — below that it's just a query."""
    assert not [r for r in palette_entries("a3f9") if r["kind"] == "verify-hash"]
    assert [r for r in palette_entries("a3f9c2b8") if r["kind"] == "verify-hash"]


def test_hash_matching_ignores_separators_like_the_web():
    rows = palette_entries("a3f9-c2b8:d14e")
    assert [r["arg"] for r in rows if r["kind"] == "verify-hash"] == ["a3f9c2b8d14e"]


def test_seq_query_offers_the_ledger():
    for query in ("#42", "42"):
        rows = [r for r in palette_entries(query) if r["kind"] == "ledger-seq"]
        assert rows and rows[0]["arg"] == "42"
        assert "#42" in rows[0]["label"]


def test_actions_are_keyword_searchable():
    assert [r["kind"] for r in palette_entries("organization")
            if r["kind"] == "copy-org"] == ["copy-org"]
    assert [r["kind"] for r in palette_entries("hotkeys")
            if r["kind"] == "shortcuts"] == ["shortcuts"]


def test_copy_org_carries_the_org_id_when_known():
    rows = palette_entries("copy", org_id="org-123")
    assert [r["arg"] for r in rows if r["kind"] == "copy-org"] == ["org-123"]
    rows = palette_entries("copy")
    assert [r["arg"] for r in rows if r["kind"] == "copy-org"] == [None]


def test_nonsense_query_returns_nothing_rather_than_everything():
    assert palette_entries("zzzzqqq") == []


# ── breach pip (web loadBreachBadge, html:3392-3403) ────────────────────────
def test_breach_pip_counts_only_unseen():
    rows = [{"seq": 3}, {"seq": 7}, {"seq": 9}]
    assert breach_pip(rows, 0) == (3, 9)
    assert breach_pip(rows, 7) == (1, 9)
    assert breach_pip(rows, 9) == (0, 9)


def test_breach_pip_handles_empty_and_junk():
    assert breach_pip([], 0) == (0, 0)
    assert breach_pip(None, 0) == (0, 0)
    assert breach_pip("not a list", 0) == (0, 0)
    # A row with no seq reads as seq 0, which is never greater than the cursor,
    # so it can't invent an unread badge.
    assert breach_pip([{"no_seq": 1}], 0) == (0, 0)
    assert breach_pip([{}], 0) == (0, 0)


def test_breach_pip_survives_missing_seq_values():
    assert breach_pip([{"seq": None}, {"seq": 5}], 0) == (1, 5)


def test_badge_caps_at_99_plus():
    assert badge_text(0) == ""
    assert badge_text(7) == "7"
    assert badge_text(99) == "99"
    assert badge_text(100) == "99+"


# ── notification routing (web onNotifClick, html:3774-3780) ─────────────────
def test_notification_routing():
    assert notification_target("breach") == "threats"
    assert notification_target("quota") == "billing"
    assert notification_target("digest") is None
    assert notification_target(None) is None


# ── announcement banner (web loadAnnBanner, html:3512-3525) ─────────────────
def test_breach_outranks_everything():
    msg = announcement(breaches=[{"seq": 12}],
                       plan={"trial_ends_at": "2026-07-29T00:00:00Z"},
                       usage={"quota": {"monthly_log_quota": 100,
                                        "used_this_month": 99}},
                       now=NOW)
    assert msg["id"] == "breach:12"
    assert msg["target"] == "threats"
    assert msg["tone"] == "bad"


def test_trial_beats_quota_when_no_breach():
    msg = announcement(breaches=[],
                       plan={"trial_ends_at": "2026-07-30T12:00:00Z"},
                       usage={"quota": {"monthly_log_quota": 100,
                                        "used_this_month": 95}},
                       now=NOW)
    assert msg["id"].startswith("trial:")
    assert "3 days" in msg["text"]


def test_trial_singular_day():
    msg = announcement(plan={"trial_ends_at": "2026-07-28T12:00:00Z"}, now=NOW)
    assert "1 day." in msg["text"]


def test_trial_outside_the_window_is_not_announced():
    assert announcement(plan={"trial_ends_at": "2026-09-01T00:00:00Z"},
                        now=NOW) is None


def test_quota_over_and_near():
    over = announcement(usage={"quota": {"monthly_log_quota": 100,
                                         "used_this_month": 140,
                                         "over_quota": True}}, now=NOW)
    assert over["id"] == "quota:over"
    assert over["tone"] == "bad"
    near = announcement(usage={"quota": {"monthly_log_quota": 100,
                                         "used_this_month": 92}}, now=NOW)
    assert near["id"] == "quota:90"
    assert "92%" in near["text"]


def test_quota_below_the_threshold_is_silent():
    assert announcement(usage={"quota": {"monthly_log_quota": 100,
                                         "used_this_month": 40}}, now=NOW) is None


def test_no_signals_means_no_banner():
    assert announcement() is None
    assert announcement(breaches=[], plan=None, usage=None) is None


def test_dismissed_ids_never_come_back():
    """A dismissal must survive — the whole point of persisting it."""
    breaches = [{"seq": 5}]
    assert announcement(breaches=breaches, now=NOW)["id"] == "breach:5"
    assert announcement(breaches=breaches, dismissed={"breach:5": 1}, now=NOW) is None


def test_dismissing_one_reveals_the_next_priority():
    msg = announcement(breaches=[{"seq": 5}],
                       usage={"quota": {"monthly_log_quota": 100,
                                        "used_this_month": 95}},
                       dismissed={"breach:5": 1}, now=NOW)
    assert msg["id"] == "quota:90"


def test_a_newer_breach_gets_its_own_id_after_dismissal():
    """Dismissing breach:5 must not silence breach:6."""
    msg = announcement(breaches=[{"seq": 6}], dismissed={"breach:5": 1}, now=NOW)
    assert msg["id"] == "breach:6"


def test_malformed_inputs_do_not_raise():
    assert announcement(breaches="nope", plan="nope", usage="nope", now=NOW) is None
    assert announcement(plan={"trial_ends_at": "not-a-date"}, now=NOW) is None


# ── misc ────────────────────────────────────────────────────────────────────
def test_relative_time_matches_the_webs_buckets():
    assert relative_time((NOW - timedelta(seconds=30)).isoformat(), now=NOW) == "just now"
    assert relative_time((NOW - timedelta(minutes=5)).isoformat(), now=NOW) == "5m ago"
    assert relative_time((NOW - timedelta(hours=3)).isoformat(), now=NOW) == "3h ago"
    assert relative_time((NOW - timedelta(days=2)).isoformat(), now=NOW) == "2d ago"
    assert relative_time(None) == ""
    assert relative_time("garbage") == ""


def test_avatar_initial_prefers_the_name():
    assert avatar_initial({"full_name": "Ada Lovelace", "email": "a@x.io"}) == "A"
    assert avatar_initial({"email": "zoe@x.io"}) == "Z"
    assert avatar_initial({}) == "?"
    assert avatar_initial(None) == "?"
