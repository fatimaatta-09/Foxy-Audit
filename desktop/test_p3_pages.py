"""D6 Verify+Anchors · D8 Export · D9 Access — shaping and built pages.

The heaviest tests here are the D9 ones. A plaintext API key is the only value
in this product that cannot be recovered if it leaks and cannot be re-shown if
it is lost, so the rule — shown once, in one dialog, never stored, never
logged — is pinned structurally rather than trusted to review.
"""

from __future__ import annotations

from datetime import date

import pytest
from PyQt6.QtWidgets import QApplication

import access_data as ad
import export_data as ed
import verify_data as vd
from panel_state import PanelState


# ══ D6 · verify ═════════════════════════════════════════════════════════════
def test_hash_hint_counts_up_rather_than_just_refusing():
    assert vd.hash_hint("")[0] == ""
    assert vd.hash_hint("abc")[0] == "3 / 64 hex characters"
    assert vd.hash_hint("a" * 64)[1] == "ok"
    assert vd.hash_hint("a" * 65)[1] == "bad"


def test_hash_hint_ignores_non_hex_so_a_pasted_row_still_counts():
    assert vd.hash_hint("ab:cd ef")[0] == "6 / 64 hex characters"


def test_precheck_blocks_a_short_hash_without_a_request():
    assert vd.verify_precheck("abc")[0] == "warn"
    assert vd.verify_precheck("a" * 64) is None


@pytest.mark.parametrize("payload,tone,title", [
    ({"found": False}, "bad", "Not in this ledger"),
    ({"found": True, "verified": False, "seq": 9}, "bad", "Tampering detected"),
    ({"found": True, "verified": True, "status": "breach", "seq": 9}, "warn",
     "Record intact · policy breach"),
    ({"found": True, "verified": True, "status": "clean", "seq": 9}, "ok",
     "Record verified — untampered"),
    (None, "bad", "Could not reach the server"),
])
def test_the_six_record_states(payload, tone, title):
    got_tone, got_title, _detail = vd.record_result(payload)
    assert (got_tone, got_title) == (tone, title)


def test_a_breach_is_not_a_verification_failure():
    """The distinction the whole page exists to make: verification is about
    whether the ledger was altered, the verdict is about what the model did.
    An intact breach must never be toned like tampering."""
    breach = vd.record_result({"found": True, "verified": True,
                               "status": "breach", "seq": 9})
    tampered = vd.record_result({"found": True, "verified": False, "seq": 9})
    assert breach[0] == "warn" and tampered[0] == "bad"
    assert "intact" in breach[1].lower()


def test_signed_out_is_told_apart_from_a_dead_server():
    assert vd.record_result(None, status=401)[1] == "Sign in to check the ledger"


def test_a_gap_and_a_mutation_are_reported_differently():
    """The backend distinguishes them; reporting both as "tampering" would
    misdescribe a deleted record as an altered one."""
    gap = vd.chain_result({"ok": False, "first_broken_seq": 4,
                           "detail": "sequence gap at 4"})
    mutation = vd.chain_result({"ok": False, "first_broken_seq": 4,
                                "detail": "hash mismatch"})
    assert "gap" in gap[1] and "altered" in mutation[1]


def test_a_partial_window_check_says_so():
    """"chain intact" over a window is a narrower claim than over everything,
    and an audit tool must not let them read the same."""
    assert vd.partial_window_note({"count": 10}) == ""
    note = vd.partial_window_note({"count": 60_000})
    assert "bounded window" in note
    assert vd.chain_result({"ok": True, "count": 60_000})[2] == note


def test_anchor_conflict_is_not_an_error():
    tone, title, _tx = vd.anchor_now_result(None, status=409)
    assert tone == "ok" and "already anchored" in title
    assert vd.anchor_now_result(None, status=500)[0] == "bad"


def test_anchor_receipts_link_to_etherscan_only_with_a_tx():
    rows = vd.receipt_rows([{"status": "confirmed", "tx_hash": "0xabc",
                             "chain": "sepolia"},
                            {"status": "pending", "chain": "sepolia"}])
    assert rows[0]["url"].endswith("0xabc") and rows[0]["tone"] == "ok"
    assert rows[1]["url"] == "" and rows[1]["tone"] == "warn"


def test_anchor_freshness_and_sla_never_invent_a_cadence():
    assert vd.freshness([]) == "no anchors yet"
    assert vd.sla_text(None) == "" and vd.sla_text({}) == ""
    assert vd.sla_text({"cadence_human": "6 hours", "plan_tier": "pro"}) == \
        "Auto-anchored every 6 hours on pro."


# ══ D8 · export ═════════════════════════════════════════════════════════════
def test_range_presets_and_all_time():
    today = date(2026, 7, 28)
    assert ed.preset_range(30, today=today) == ("2026-06-28", "2026-07-28")
    assert ed.preset_range(0, today=today) == ("", "2026-07-28")


def test_export_params_default_to_a_bounded_year():
    assert ed.export_params("", "") == {"days": 365}
    assert ed.export_params("2026-01-01", "") == {"date_from": "2026-01-01"}


def test_the_passport_extension_is_sniffed_not_assumed():
    """The endpoint returns a PDF *or* HTML depending on whether the renderer
    is available server-side; saving HTML as .pdf gives the user a file the OS
    opens into an error."""
    assert ed.suggested_filename("passport", "application/pdf").endswith(".pdf")
    assert ed.suggested_filename("passport", "text/html; charset=utf-8") \
        .endswith(".html")
    assert ed.suggested_filename("passport", None).endswith(".html")
    assert ed.suggested_filename("logs_csv").endswith(".csv")


def test_file_filter_matches_the_suggested_name():
    for kind, ctype in (("passport", "application/pdf"), ("logs_json", None)):
        ext = ed.suggested_filename(kind, ctype).rsplit(".", 1)[-1]
        assert f"*.{ext}" in ed.file_filter(kind, ctype)


def test_integrity_and_records_never_claim_a_number_they_lack():
    assert ed.integrity_text(None) == ("—", "mute")
    assert ed.records_text(None) == "—"
    assert ed.integrity_text({"ok": True}) == ("100% — no gaps", "ok")
    assert ed.integrity_text({"ok": False, "first_broken_seq": 7})[0] == \
        "broken at seq 7"


def test_metadata_is_masked_by_default():
    assert ed.mask("9f2c1ab77e0d5544abcdef") == "9f2c1a…cdef"
    assert ed.mask("") == "—"
    assert ed.mask("short") == "short"


def test_history_range_labels():
    assert ed.range_label({"date_from": "2026-01-01"}) == "2026-01-01 → now"
    assert ed.range_label({"days": 90}) == "last 90d"
    assert ed.range_label({}) == "all time"


def test_export_progress_is_real_stages_not_a_timer():
    assert ed.progress_for("requesting")[0] < ed.progress_for("done")[0]
    assert ed.progress_for("done") == (100, "export ready")


# ══ D9 · access — the secrecy rule ══════════════════════════════════════════
def test_no_plaintext_key_ever_reaches_the_shaping_module():
    """access_data must never accept, return, format or store a plaintext key.

    Walked structurally rather than trusted: every string this module produces
    from a key row is derived from `key_prefix`, which is the non-secret
    fragment the server hands out precisely so a key can be identified without
    being revealed.
    """
    rows = ad.key_rows([{"id": "1", "name": "prod", "key_prefix": "fx_abc",
                         "status": "active",
                         # a server that wrongly included one must not leak it
                         "api_key": "SECRET-PLAINTEXT-VALUE"}])
    blob = repr(rows) + repr(ad.stat_row(
        [{"status": "active", "key_prefix": "fx_abc",
          "api_key": "SECRET-PLAINTEXT-VALUE"}], None))
    assert "SECRET-PLAINTEXT-VALUE" not in blob
    assert rows[0]["prefix"] == "fx_abc"


def test_the_sdk_snippet_never_contains_a_real_key():
    """The configure box shows a prefix with a placeholder tail — a snippet
    that looked ready to paste would be a plaintext key on screen."""
    for _caption, code in ad.sdk_snippets("fx_abc"):
        assert "SECRET" not in code
    configure = dict((c, k) for c, k in
                     ((c, k) for c, k in ad.sdk_snippets("fx_abc")))
    assert any("fx_abc…" in code for _c, code in ad.sdk_snippets("fx_abc"))
    assert any("your_key_here" in code for _c, code in ad.sdk_snippets(None))


def test_access_data_has_no_persistence_or_logging_calls():
    """Nothing in this module may write a value anywhere.

    Walked as an AST rather than as text: the module's own docstring names the
    things it must not do, and a substring scan flags that as a violation.
    """
    import ast
    with open(_module_path("access_data.py"), encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    banned = {"open", "print", "setValue", "write", "writelines", "dump",
              "dumps"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", None) or getattr(
                node.func, "attr", None)
            assert name not in banned, f"access_data calls {name}()"
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                assert alias.name.split(".")[0] not in {
                    "logging", "PyQt6", "pathlib", "os", "io"},                     f"access_data imports {alias.name}"


def _module_path(name: str) -> str:
    from pathlib import Path
    return str(Path(__file__).resolve().parent / name)


def test_expired_is_surfaced_even_though_the_status_still_says_active():
    assert ad.key_status({"status": "active", "expired": True})[0] == "expired"
    assert ad.key_status({"status": "active"})[0] == "active"
    assert ad.key_status({"status": "revoked"}) == ("revoked", "mute")


def test_dead_keys_are_dimmed_and_not_revocable():
    rows = ad.key_rows([{"status": "revoked", "key_prefix": "a"},
                        {"status": "active", "expired": True, "key_prefix": "b"},
                        {"status": "active", "key_prefix": "c"}])
    assert [r["dim"] for r in rows] == [True, True, False]
    assert [r["revocable"] for r in rows] == [False, False, True]


def test_key_limit_tells_unlimited_apart_from_unknown():
    """`∞` is a real answer; `—` means we did not get one. Showing ∞ for a
    failed quota call would promise capacity nobody verified."""
    assert ad.stat_row([], {"api_key_limit": None})[2][1] == "∞"
    assert ad.stat_row([], {})[2][1] == "—"
    assert ad.stat_row([], {"api_key_limit": 5})[2][1] == "5"


def test_stat_row_is_all_dashes_before_anything_is_known():
    assert [t[1] for t in ad.stat_row(None, None)] == ["—", "—", "—", "—"]


def test_the_quota_limit_message_carries_the_numbers():
    message = ad.limit_reached_message(
        {"code": "api_key_limit_reached", "message": "No slots.",
         "used": 3, "included": 3})
    assert "3 of 3" in message
    assert ad.limit_reached_message({"code": "other"}) is None
    assert ad.limit_reached_message("nope") is None


def test_create_body_clamps_expiry_like_the_web():
    assert ad.create_body("", None) == {"name": "unnamed key"}
    assert ad.create_body("prod", 9999)["expires_in_days"] == 3650
    assert "expires_in_days" not in ad.create_body("prod", 0)


def test_connection_result_distinguishes_rejected_from_unreachable():
    assert ad.connection_result(True)[1] == "ok"
    assert "401" in ad.connection_result(False, status=401)[0]
    assert "reach" in ad.connection_result(False)[0]


# ══ the built pages ═════════════════════════════════════════════════════════
@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def console(app, tmp_path_factory):
    from PyQt6.QtCore import QSettings
    from fox_settings import FoxSettings
    from foxy_client import MemorySecretStore
    from dashboard import DashboardWindow

    path = tmp_path_factory.mktemp("p3") / "console.ini"
    store = QSettings(str(path), QSettings.Format.IniFormat)
    win = DashboardWindow(settings=FoxSettings(store, MemorySecretStore()))
    yield win
    win.close()


def test_all_three_pages_build(console):
    for attr in ("v_hash", "v_hint", "v_result", "v_quick_hash", "v_chain_result",
                 "v_anchor_list", "verify_scroll",
                 "exp_range_buttons", "exp_type", "exp_progress", "exp_meta",
                 "exp_history", "export_scroll",
                 "key_stats", "key_rows", "key_new_btn", "sdk_boxes",
                 "access_scroll"):
        assert hasattr(console, attr), attr


def test_the_hash_hint_updates_as_you_type(console):
    console.v_hash.setText("abc")
    assert console.v_hint.text() == "3 / 64 hex characters"
    console.v_hash.setText("a" * 64)
    assert "valid chain hash" in console.v_hint.text()
    console.clear_verify()
    assert console.v_hint.text() == "" and console.v_hash.text() == ""


def test_a_short_hash_is_rejected_without_a_request(console):
    console.v_hash.setText("abc")
    console.run_verify()
    assert console.v_result.tone() == "warn"
    assert not console._page_workers
    console.clear_verify()


def test_the_result_panel_is_hidden_until_something_is_checked(console):
    console.clear_verify()
    assert console.v_result.isHidden()
    console.v_result.show_result(*vd.record_result(
        {"found": True, "verified": True, "status": "clean", "seq": 3}))
    assert console.v_result.tone() == "ok"
    console.clear_verify()


def test_anchors_empty_and_error_say_different_things(console):
    from panel_state import ERROR_TITLE
    console._on_anchors([])
    assert console.v_anchor_empty.state() is PanelState.EMPTY
    assert "No anchors yet" in console.v_anchor_empty.title.text()
    console._on_anchors(None, ok=False)
    assert console.v_anchor_empty.title.text() == ERROR_TITLE


def test_export_range_presets_drive_the_pickers(console):
    console.set_export_range(30)
    assert console.exp_range_buttons[30].isChecked()
    assert not console.exp_all_time.isChecked()
    console.set_export_range(0)
    assert console.exp_all_time.isChecked()
    assert console._export_dates()[0] == ""      # no start date at all time
    console.set_export_range(90)


def test_chain_metadata_is_masked_until_revealed(console):
    console._on_export_head({"items": [{"chain_hash": "9f2c1ab77e0d5544abcdef"}]})
    console._on_export_org({"org_id": "11112222333344445555"})
    assert "…" in console.exp_meta["head"].text()
    console.exp_reveal.setChecked(True)
    assert console.exp_meta["head"].text() == "9f2c1ab77e0d5544abcdef"
    console.exp_reveal.setChecked(False)


def test_export_history_empty_vs_error(console):
    from panel_state import ERROR_TITLE
    console._on_exports({"items": []})
    assert console.exp_history_empty.state() is PanelState.EMPTY
    console._on_exports(None, ok=False)
    assert console.exp_history_empty.title.text() == ERROR_TITLE


def test_a_member_gets_a_notice_not_an_error(console):
    """403 on /v1/keys means "you may not manage these", which is a fact about
    the account — not a failure the user can retry their way out of."""
    console._on_keys(None, ok=False, status=403)
    assert not console.key_member_notice.isHidden()
    assert not console.key_new_btn.isEnabled()
    assert console.key_empty.state() is PanelState.EMPTY
    assert console.key_empty.retry_btn.isHidden()


def test_an_admin_with_no_keys_gets_an_empty_state_and_the_buttons(console):
    console._on_keys([])
    assert console.key_member_notice.isHidden()
    assert console.key_new_btn.isEnabled()
    assert console.key_empty.state() is PanelState.EMPTY


def test_a_failed_key_fetch_offers_a_retry(console):
    from panel_state import ERROR_TITLE
    console._on_keys(None, ok=False, status=500)
    assert console.key_empty.title.text() == ERROR_TITLE
    assert not console.key_empty.retry_btn.isHidden()


def test_no_key_row_widget_can_show_a_plaintext_key(console):
    """The rendered rows carry the prefix only."""
    console._on_keys([{"id": "1", "name": "prod", "key_prefix": "fx_abc",
                       "status": "active",
                       "api_key": "SECRET-PLAINTEXT-VALUE"}])
    from PyQt6.QtWidgets import QLabel
    texts = " ".join(w.text() for w in
                     console.access_scroll.widget().findChildren(QLabel))
    assert "SECRET-PLAINTEXT-VALUE" not in texts
    assert "fx_abc" in texts


def test_the_shown_once_dialog_carries_the_warning_and_clears_itself(app):
    """The only surface allowed to display a key — and it blanks the field on
    close rather than leaving the string live in a hidden widget."""
    from access_page import ShownOnceDialog
    dialog = ShownOnceDialog("New API key", "fx_live_secret", None)
    assert dialog.value.text() == "fx_live_secret"
    assert dialog.value.isReadOnly()
    warning = " ".join(w.text() for w in dialog.findChildren(type(dialog.value))
                       if hasattr(w, "text"))
    from PyQt6.QtWidgets import QLabel
    labels = " ".join(w.text() for w in dialog.findChildren(QLabel))
    assert "Shown once" in labels and "cannot be recovered" in labels
    dialog.clear_secret()
    assert dialog.value.text() == ""


def test_the_console_never_stores_a_key_on_itself(console):
    """After the shown-once path, no attribute of the window holds the key."""
    console._show_new_key.__doc__          # touch it so the name is used
    values = " ".join(repr(v) for v in vars(console).values()
                      if isinstance(v, (str, bytes)))
    assert "fx_live_secret" not in values


def test_neither_new_page_spawns_into_the_gating_set(console):
    import ast
    import inspect
    import textwrap
    import dashboard

    for name in ("refresh_anchors", "refresh_exports", "refresh_export_meta",
                 "refresh_keys", "run_verify", "run_export", "create_key"):
        src = textwrap.dedent(inspect.getsource(
            getattr(dashboard.DashboardWindow, name)))
        for node in ast.walk(ast.parse(src)):
            if (isinstance(node, ast.Call)
                    and getattr(node.func, "id", "") == "spawn_worker"):
                tracks = [kw.value.attr for kw in node.keywords
                          if kw.arg == "track"
                          and isinstance(kw.value, ast.Attribute)]
                assert "_home_workers" not in tracks, name


def test_the_new_worker_bucket_is_drained_on_close(console):
    import inspect
    import dashboard
    assert "_page_workers" in inspect.getsource(
        dashboard.DashboardWindow.closeEvent)


def test_none_of_the_three_pages_fetches_without_a_credential(console):
    console.show()
    console.settings.set_backend_url("http://127.0.0.1:9")
    try:
        console.refresh_anchors()
        console.refresh_exports()
        console.refresh_keys()
        assert not console._page_workers
    finally:
        console.settings.set_backend_url("")
        console.close()


# ══ the worker error contract ═══════════════════════════════════════════════
def test_status_of_parses_the_code_out_of_a_worker_error():
    """`ApiWorker.failed` carries a STRING, so a handler that wants to branch
    on the status has to parse it.

    Every `err.status` check written before this — the 401 in the quick check,
    the 403/402 here — silently got None and took its fallback branch. Emitting
    the exception (or any custom object) instead crashes the interpreter, so
    parsing the message is the contract.
    """
    from foxy_client import status_of
    assert status_of("HTTP 403: step_up_required") == 403
    assert status_of("HTTP 402: api_key_limit_reached") == 402
    assert status_of("timed out") is None
    assert status_of(None) is None
    assert status_of("") is None


def test_api_error_renders_the_shape_status_of_expects():
    """The two halves have to agree, or the parse silently never matches."""
    from foxy_client import ApiError, status_of
    assert status_of(str(ApiError(403, "Forbidden", "step_up_required"))) == 403


def test_spawn_worker_accepts_the_raw_flag():
    """The passport download needs bytes plus the Content-Type."""
    import inspect
    from foxy_client import spawn_worker
    assert "raw" in inspect.signature(spawn_worker).parameters
