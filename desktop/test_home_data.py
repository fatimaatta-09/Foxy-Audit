"""Pure-logic tests for the Home page's data shaping (D4).

No Qt: everything the page *decides* before it paints. The cases here are the
ones that actually went wrong while building it — the greeting fallback chain,
coverage arithmetic when nothing has been reported, the trend metric map, the
activity merge, and above all the difference between "nothing yet" (a
measurement) and "couldn't load" (the absence of one).
"""

from __future__ import annotations

import home_data as hd


# ── greeting (web html:2253-2255) ───────────────────────────────────────────
def test_greeting_prefers_the_first_name():
    assert hd.greeting_name({"full_name": "ada lovelace"}) == "Ada"


def test_greeting_falls_back_to_the_email_local_part():
    assert hd.greeting_name({"email": "zoe@example.com"}) == "Zoe"


def test_greeting_has_a_neutral_last_resort():
    assert hd.greeting_name({}) == "there"
    assert hd.greeting_name(None) == "there"


def test_greeting_ignores_a_whitespace_only_name():
    assert hd.greeting_name({"full_name": "   ", "email": "kim@x.io"}) == "Kim"


# ── head subtitle ───────────────────────────────────────────────────────────
def test_head_subtitle_reports_real_counts():
    assert hd.head_subtitle({"total_logged": 24815, "grading": {"pending": 41}}) == \
        "24,815 events logged · 41 pending grading"


def test_head_subtitle_never_claims_zero_it_did_not_measure():
    # An empty payload means we do not know the count. "0 events logged" would
    # be a claim; the em dash is the honest answer.
    assert hd.head_subtitle({}) == "—"
    assert hd.head_subtitle(None) == "—"


# ── capture coverage ────────────────────────────────────────────────────────
def _coverage(**over):
    base = {"status": "verified", "message": "Observed everything.",
            "chain_verification": "verified", "total_events": 90,
            "missing_events": 10, "instrumented_clients": 2,
            "events_without_client_identity": 0, "clients": []}
    base.update(over)
    return base


def test_coverage_percent_is_observed_over_reported():
    view = hd.coverage_view(_coverage())
    assert view["pct"] == 90 and view["pct_text"] == "90%"
    assert view["gauge_tone"] == "warn"          # under 95%


def test_coverage_with_nothing_reported_shows_no_percentage():
    view = hd.coverage_view(_coverage(total_events=0, missing_events=0))
    assert view["pct"] is None and view["pct_text"] == "—"
    assert view["note"] == "No client-reported events yet."


def test_coverage_failure_is_stated_not_guessed():
    view = hd.coverage_view(None)
    assert view["ok"] is False
    assert view["pct"] is None and view["pct_text"] == "—"
    # Crucially the chips come back empty so the card cannot leave the last
    # cycle's numbers sitting under a "could not be loaded" message.
    assert view["chips"] == []
    assert view["clients"] == []


def test_coverage_chip_order_matches_the_web():
    names = [c[0] for c in hd.coverage_view(_coverage())["chips"]]
    assert names == ["Events observed", "SDK clients", "Missing events",
                     "No client identity"]


def test_coverage_client_row_flags_a_gap():
    row = hd.coverage_client_row({
        "client_id": "triage", "events": 941, "first_client_seq": 1,
        "last_client_seq": 953, "missing_ranges": [{"start": 402, "end": 413}]})
    assert row["status"] == "gap" and row["tone"] == "bad"
    assert row["gap_text"] == "402–413"


def test_coverage_client_row_is_continuous_without_anomalies():
    row = hd.coverage_client_row({"client_id": "b", "events": 900,
                                  "first_client_seq": 1, "last_client_seq": 900})
    assert (row["status"], row["tone"]) == ("continuous", "ok")


# ── usage trend switcher ────────────────────────────────────────────────────
DAYS = [{"day": "2026-07-01", "logs_count": 10, "tokens_sum": 500, "breach_count": 1},
        {"day": "2026-07-02", "logs_count": 20, "tokens_sum": 900, "breach_count": 0}]


def test_trend_switches_the_series_it_reads():
    rows, spec = hd.trend_rows(DAYS, "tokens")
    assert [r["value"] for r in rows] == [500, 900]
    assert spec["label"] == "Tokens"


def test_trend_falls_back_to_logs_for_an_unknown_metric():
    _rows, spec = hd.trend_rows(DAYS, "nonsense")
    assert spec["label"] == "Logs"


def test_trend_survives_a_missing_days_list():
    rows, _spec = hd.trend_rows(None, "logs")
    assert rows == []


# ── grading donut ───────────────────────────────────────────────────────────
def test_grading_slices_total_every_state():
    slices, total = hd.grading_slices(
        {"grading": {"graded": 24766, "pending": 41, "in_progress": 6, "failed": 2}})
    assert total == 24815
    assert [s["label"] for s in slices] == ["Graded", "Pending", "In progress",
                                            "Failed"]


def test_grading_total_zero_means_the_donut_shows_its_empty_state():
    _slices, total = hd.grading_slices({})
    assert total == 0


# ── onboarding stepper ──────────────────────────────────────────────────────
STEPS = [{"key": "api_key", "done": True, "title": "Create an API key",
          "desc": "d", "page": "keys", "label": "Create key"},
         {"key": "first_log", "done": True, "title": "Log a call", "desc": "d",
          "page": "keys", "label": "Go"},
         {"key": "invite_team", "done": False, "title": "Invite", "desc": "d",
          "page": "settings", "label": "Invite"}]


def test_onboarding_reports_the_server_progress():
    view = hd.onboarding_view({"done": 2, "total": 3, "steps": STEPS})
    assert view["progress_text"] == "2 / 3 done"


def test_onboarding_counts_the_steps_when_the_server_omits_done():
    # Without this the card printed "0 / 3 done" above two green checkmarks.
    view = hd.onboarding_view({"total": 3, "steps": STEPS})
    assert view["progress_text"] == "2 / 3 done"


def test_onboarding_is_hidden_once_dismissed():
    assert hd.onboarding_view({"dismissed": True, "steps": STEPS}) is None
    assert hd.onboarding_view(None) is None


def test_onboarding_titles_change_with_progress():
    all_done = [dict(s, done=True) for s in STEPS]
    view = hd.onboarding_view({"done": 3, "total": 3, "steps": all_done})
    assert view["title"] == "You're all set."


def test_onboarding_maps_web_pages_to_desktop_sections():
    view = hd.onboarding_view({"done": 0, "total": 3, "steps": STEPS})
    assert {s["page"] for s in view["steps"]} <= {"keys", "settings", "access",
                                                  "home", "ledger", "policy",
                                                  "threats", "export",
                                                  "analytics", "billing",
                                                  "system"}


# ── activity feed ───────────────────────────────────────────────────────────
def test_activity_merges_both_sources_newest_first():
    rows = hd.merge_activity(
        [{"action": "key.create", "created_at": "2026-07-27T07:00:00Z"}],
        [{"created_at": "2026-07-27T08:00:00Z", "success": True, "email": "a@x"}])
    assert len(rows) == 2
    assert rows[0]["kind"] == "login"          # the newer of the two


def test_activity_labels_a_known_action():
    rows = hd.merge_activity(
        [{"action": "key.create", "created_at": "2026-07-27T07:00:00Z"}], [])
    assert rows[0]["title"] == hd.ACTION_LABELS["key.create"]


def test_activity_keeps_an_unknown_action_visible():
    rows = hd.merge_activity(
        [{"action": "some.new.thing", "created_at": "2026-07-27T07:00:00Z"}], [])
    assert rows and rows[0]["title"]           # never silently dropped


def test_activity_marks_a_failed_sign_in():
    rows = hd.merge_activity(
        [], [{"created_at": "2026-07-27T07:00:00Z", "success": False, "email": "a@x"}])
    assert rows[0]["tone"] == "danger"


def test_activity_is_capped():
    many = [{"action": "key.create", "created_at": f"2026-07-27T07:{i:02d}:00Z"}
            for i in range(60)]
    assert len(hd.merge_activity(many, [])) == hd.ACTIVITY_LIMIT


def test_activity_tolerates_unparseable_timestamps():
    rows = hd.merge_activity([{"action": "x", "created_at": "not-a-date"}], [])
    assert len(rows) == 1


# ── quick ledger check ──────────────────────────────────────────────────────
def test_quick_check_normalizes_to_hex():
    assert hd.normalize_hash("  AB-cd ef  ") == "abcdef"


def test_quick_check_rejects_a_wrong_length_hash_without_a_request():
    action, message, tone = hd.quick_verify_request("abc")
    assert action == "reject" and tone == "bad"
    assert "64-character" in message


def test_quick_check_clears_on_empty_input():
    assert hd.quick_verify_request("")[0] == "clear"


def test_quick_check_accepts_a_full_hash():
    assert hd.quick_verify_request("a" * 64)[0] == "check"


def test_quick_check_reports_tampering_with_the_sequence():
    message, tone = hd.quick_verify_result({"found": True, "verified": False,
                                            "seq": 91})
    assert tone == "bad" and "91" in message


def test_quick_check_separates_intact_from_in_policy():
    # A record can be untampered and still be a policy breach; conflating the
    # two would tell an auditor the ledger failed when it did not.
    message, tone = hd.quick_verify_result({"found": True, "verified": True,
                                            "status": "breach", "seq": 7})
    assert tone == "bad" and "record intact" in message
    ok_message, ok_tone = hd.quick_verify_result({"found": True, "verified": True,
                                                  "status": "clean", "seq": 7})
    assert ok_tone == "ok" and "record intact" in ok_message


def test_quick_check_handles_a_missing_record_and_a_dead_server():
    assert hd.quick_verify_result({"found": False})[1] == "bad"
    assert hd.quick_verify_result(None)[0] == "could not reach the server"


# ── recent ledger + alerts ──────────────────────────────────────────────────
def test_recent_ledger_takes_only_the_newest_few():
    items = [{"seq": i, "policy_tag": "p", "chain_hash": "a" * 64, "status": "clean"}
             for i in range(10)]
    assert len(hd.recent_ledger_rows({"items": items})) == hd.RECENT_LEDGER_LIMIT


def test_recent_ledger_shortens_the_hash_without_inventing_one():
    row = hd.recent_ledger_rows({"items": [{"seq": 1, "chain_hash": ""}]})[0]
    assert row["hash"] == "—"


def test_recent_ledger_of_nothing_is_empty():
    assert hd.recent_ledger_rows(None) == []


def test_verdict_tone_bands():
    assert hd.verdict_of({"status": "breach"})[1] == "bad"
    assert hd.verdict_of({"status": "pending"})[1] == "warn"
    assert hd.verdict_of({"status": "clean"})[1] == "ok"


def test_alert_rows_band_the_risk_score():
    rows = hd.alert_rows({"recent_high_risk": [
        {"seq": 1, "risk_score": 88}, {"seq": 2, "risk_score": 52},
        {"seq": 3, "risk_score": 5}]})
    assert [r["tone"] for r in rows] == ["bad", "warn", "ok"]


def test_alert_reason_is_truncated_not_dropped():
    rows = hd.alert_rows({"recent_high_risk": [
        {"seq": 1, "risk_score": 90, "reason": "x" * 400}]})
    assert len(rows[0]["reason"]) == 120


def test_open_alert_count_prefers_the_total():
    assert hd.open_alert_count({"total_threats": 12, "recent_high_risk": []}) == 12


def test_open_alert_count_is_unknown_when_the_call_failed():
    # None, not 0: "no open alerts" is a very different statement from "we
    # could not ask", and only one of them is true here.
    assert hd.open_alert_count(None) is None
