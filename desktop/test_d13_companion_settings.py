"""D13 — companion settings + autostart.

Four things carry the weight:

* **autostart never touches the real machine from a test.** The module's whole
  job is modifying the user's login items; the seam is the constructor, and a
  test here asserts the dialog uses it.
* **the state lives in the OS**, so the toggle reads it fresh and applies
  immediately rather than on Save — Cancel cannot undo an OS write.
* **quiet hours wrap past midnight**, which is the case everyone gets wrong,
  and they silence the noise WITHOUT dropping the event.
* **every new key defaults to what the app already did**, so shipping the
  catalogue changes nobody's fox until they move something.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import autostart as asm
import companion_prefs as cp

_HERE = Path(__file__).resolve().parent


# ══ autostart ═══════════════════════════════════════════════════════════════
def test_the_module_can_be_driven_without_touching_this_machine():
    """The D3 round found tests writing the developer's real QSettings. This
    module writes LOGIN ITEMS — the seam is not optional."""
    store = asm.MemoryBackend()
    store.supported = True
    auto = asm.Autostart(store)
    assert auto.is_enabled() is False
    assert auto.set_enabled(True) is True
    assert auto.is_enabled() is True
    assert auto.set_enabled(False) is True
    assert auto.is_enabled() is False


def test_no_test_in_this_tree_talks_to_the_real_login_items():
    """The seam only helps if it is used. A bare `Autostart()` or a
    `SettingsDialog` without the argument reads — and could write — the login
    items of whoever runs the suite."""
    # Parsed, not grepped: the first version matched the backticked name in
    # its own docstring and failed on itself.
    offenders = []
    for path in sorted(_HERE.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "attr", None) or getattr(node.func,
                                                               "id", None)
            if name == "Autostart" and not node.args and not node.keywords:
                offenders.append(f"{path.name}: bare Autostart()")
            if name == "SettingsDialog" and not any(
                    k.arg == "autostart" for k in node.keywords):
                offenders.append(f"{path.name}: SettingsDialog without a seam")
    assert offenders == [], offenders


def test_a_refused_write_is_reported_not_swallowed():
    """A locked-down machine or a read-only home must produce a False the UI
    can show, never an exception on the way into a settings dialog."""
    store = asm.MemoryBackend(writable=False)
    store.supported = True
    auto = asm.Autostart(store)
    assert auto.set_enabled(True) is False
    assert auto.is_enabled() is False


def test_an_unsupported_platform_says_so_rather_than_pretending():
    auto = asm.Autostart(asm.MemoryBackend())      # supported = False
    assert auto.supported is False
    assert auto.set_enabled(True) is False
    assert auto.is_enabled() is False


def test_a_backend_that_raises_reads_as_off_not_as_a_crash():
    class Exploding:
        supported = True
        location = "boom"

        def read(self):
            raise OSError("nope")

        def write(self, command):
            raise OSError("nope")

        def remove(self):
            raise OSError("nope")

    auto = asm.Autostart(Exploding())
    assert auto.is_enabled() is False
    assert auto.set_enabled(True) is False


def test_the_launch_command_carries_the_script_when_not_frozen():
    """From source `sys.executable` is a bare interpreter — on its own it
    would open a REPL at login instead of the fox."""
    command = asm.launch_command()
    assert len(command) == 2 and command[1].endswith("omni_fox.py")


def test_each_platform_gets_its_own_documented_store(tmp_path):
    mac = asm.backend_for("darwin", home=tmp_path)
    assert mac.path.name == f"{asm.BUNDLE_ID}.plist"
    assert "LaunchAgents" in str(mac.path)
    linux = asm.backend_for("linux", home=tmp_path)
    assert linux.path.name == asm.LINUX_DESKTOP_FILE
    assert "autostart" in str(linux.path)


def test_an_unknown_platform_gets_the_unsupported_store():
    """Writing a .desktop file on an OS that never reads one would make the
    toggle claim to work while doing nothing."""
    assert asm.backend_for("sunos5").supported is False


def test_the_written_files_are_what_each_platform_actually_reads(tmp_path):
    mac = asm.backend_for("darwin", home=tmp_path)
    assert mac.write("/Applications/FoxyAudit.app/Contents/MacOS/FoxyAudit")
    text = mac.path.read_text(encoding="utf-8")
    assert "<key>RunAtLoad</key>" in text and "<true/>" in text
    assert asm.BUNDLE_ID in text
    # KeepAlive is deliberately absent — a companion you cannot dismiss.
    assert "KeepAlive" not in text

    linux = asm.backend_for("linux", home=tmp_path)
    assert linux.write("/usr/bin/foxy")
    entry = linux.path.read_text(encoding="utf-8")
    assert entry.startswith("[Desktop Entry]")
    assert "Exec=/usr/bin/foxy" in entry
    assert "Type=Application" in entry


def test_removing_an_absent_entry_is_success_not_failure(tmp_path):
    """The state the caller asked for is the state they got."""
    linux = asm.backend_for("linux", home=tmp_path)
    assert linux.read() is False
    assert linux.remove() is True


def test_a_windows_path_with_spaces_is_quoted_for_cmd(monkeypatch):
    monkeypatch.setattr(asm.sys, "platform", "win32")
    assert asm._quote([r"C:\Program Files\Foxy\FoxyAudit.exe"]) \
        == '"C:\\Program Files\\Foxy\\FoxyAudit.exe"'


def test_the_failure_message_names_the_platform_that_refused():
    assert "Windows" in asm.failure_message(True, "win32")
    assert "startup" in asm.failure_message(False, "linux")


# ══ frequencies ═════════════════════════════════════════════════════════════
def test_off_is_a_real_option_and_not_merely_very_rare():
    assert cp.frequency_range("off") is None
    assert cp.next_countdown("off", lambda a, b: 999) is None
    assert cp.next_countdown("normal", lambda a, b: 999) == 999


def test_normal_keeps_the_cadence_the_fox_already_had():
    """The shipped behaviour was `randint(600, 1200)` ticks. Adding a control
    must not silently retune the default."""
    assert cp.frequency_range("normal") == (600, 1200)


def test_an_unknown_frequency_falls_back_to_the_default_not_to_off():
    """A settings file from a newer build must not switch idle poses off."""
    assert cp.frequency_range("blistering") == cp.frequency_range("normal")
    assert cp.frequency_range(None) == cp.frequency_range("normal")


def test_the_frequencies_are_ordered_from_off_to_frequent():
    ranges = [r for _k, _l, r in cp.FREQUENCIES if r is not None]
    assert ranges == sorted(ranges, reverse=True)


# ══ fox appearance ══════════════════════════════════════════════════════════
def test_the_fox_can_never_be_scaled_into_something_unclickable():
    assert cp.scaled_size(192, 208, 100) == (192, 208)
    assert cp.scaled_size(192, 208, 50) == (96, 104)
    small, _ = cp.scaled_size(192, 208, 1)      # a hand-edited settings file
    assert small >= 48
    assert cp.scaled_size(192, 208, 400)[0] == cp.scaled_size(192, 208, 150)[0]
    # Zero is a VALUE, not a missing one. `int(percent or DEFAULT)` read it as
    # unset and returned full size — the opposite of what was asked for.
    assert cp.scaled_size(192, 208, 0) == cp.scaled_size(192, 208, cp.SCALE_MIN)
    assert cp.scaled_size(192, 208, None) == (192, 208)     # genuinely unset


def test_the_fox_can_never_be_made_invisible():
    """Then it could not be found in order to turn it back up."""
    assert cp.opacity_fraction(100) == 1.0
    assert cp.opacity_fraction(0) == cp.OPACITY_MIN / 100
    assert cp.opacity_fraction(80) == 0.8


def test_the_poll_interval_is_clamped_to_the_specced_range():
    assert cp.poll_interval(10) == 10
    assert cp.poll_interval(1) == cp.POLL_MIN
    assert cp.poll_interval(600) == cp.POLL_MAX
    assert cp.poll_interval("nonsense") == cp.POLL_DEFAULT


# ══ quiet hours ═════════════════════════════════════════════════════════════
def test_quiet_hours_wrap_past_midnight():
    """22:00-07:00 is the union of two spans. `start <= now <= end` silences
    nothing at all, which is the bug this exists to prevent."""
    for hour in (22, 23, 0, 3, 6):
        assert cp.in_quiet_hours((hour, 0), "22:00", "07:00"), hour
    for hour in (7, 12, 18, 21):
        assert not cp.in_quiet_hours((hour, 0), "22:00", "07:00"), hour


def test_a_same_day_window_still_works():
    assert cp.in_quiet_hours((13, 30), "09:00", "17:00")
    assert not cp.in_quiet_hours((18, 0), "09:00", "17:00")


def test_the_boundaries_are_half_open():
    """Inclusive at the start, exclusive at the end — so a 07:00 alert lands
    the moment quiet hours are over, not a minute later."""
    assert cp.in_quiet_hours((22, 0), "22:00", "07:00")
    assert not cp.in_quiet_hours((7, 0), "22:00", "07:00")


def test_equal_endpoints_mean_no_quiet_hours_not_permanent_silence():
    """Someone who set both to the same time asked for nothing, and silencing
    a compliance companion forever is not a reasonable reading of that."""
    for hour in (0, 9, 23):
        assert not cp.in_quiet_hours((hour, 0), "08:00", "08:00")


def test_unparseable_times_disable_the_window_rather_than_guess():
    assert not cp.in_quiet_hours((3, 0), "", "07:00")
    assert not cp.in_quiet_hours((3, 0), "25:00", "07:00")
    assert not cp.in_quiet_hours((3, 0), "22:00", "not-a-time")
    assert cp.parse_time("7:5") == (7, 5)
    assert cp.parse_time("24:00") is None


def test_the_toggle_beats_the_window():
    assert not cp.in_quiet_hours((3, 0), "22:00", "07:00", enabled=False)


def test_quiet_hours_take_the_noise_and_leave_the_evidence():
    """A breach at 3am must still show on screen and still be recorded — the
    user asked for silence, not for less evidence."""
    react = {"state": "ALERTING", "row": "ROW_ALERT", "duration": 5.0,
             "overlay": "red", "bubble": "⚠ Breach — risk 90",
             "toast": ("t", "b", "critical"), "sound": True, "route": "threats"}
    quiet = cp.silence(react, True)
    assert quiet["sound"] is False and quiet["toast"] is None
    assert quiet["state"] == "ALERTING" and quiet["overlay"] == "red"
    assert quiet["bubble"] == react["bubble"] and quiet["route"] == "threats"
    assert cp.silence(react, False) is react       # untouched outside the window
    assert cp.silence(None, True) is None


def test_silence_does_not_mutate_the_reaction_it_was_given():
    react = {"sound": True, "toast": ("t", "b", "info")}
    cp.silence(react, True)
    assert react["sound"] is True and react["toast"] is not None


# ══ click action ════════════════════════════════════════════════════════════
def test_the_click_default_is_what_a_click_already_did():
    assert cp.click_action(None) == "chat"
    assert cp.click_action("nonsense") == "chat"
    assert cp.click_action("panel") == "panel"


def test_every_click_action_is_one_the_fox_can_perform():
    source = (_HERE / "omni_fox.py").read_text(encoding="utf-8")
    body = source.split("def _do_click_action(")[1].split("\n    def ")[0]
    for key, _label in cp.CLICK_ACTIONS:
        assert f'"{key}"' in body or key == cp.DEFAULT_CLICK_ACTION, key


# ══ the settings keys ═══════════════════════════════════════════════════════
@pytest.fixture(scope="module")
def app():
    """Module-scoped and RETURNED, which every other test file here does too.

    That is load-bearing, not style: a bare `QApplication.instance() or
    QApplication([])` drops the only Python reference, PyQt garbage-collects
    the application while widgets are still alive, and the interpreter dies at
    shutdown with exit 127 and no traceback — after every test has passed.
    Holding the reference in a fixture is what keeps it alive to the end of
    the session. (Found here; written up on TASK 020 item 3.)
    """
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def settings(app, tmp_path):
    from PyQt6.QtCore import QSettings
    from fox_settings import FoxSettings
    from foxy_client import MemorySecretStore
    store = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    return FoxSettings(store, MemorySecretStore())


def test_every_new_default_is_what_the_app_already_did(settings):
    """Shipping the catalogue must not change anybody's fox until they move
    something. Each of these mirrors the pre-D13 hard-coded behaviour."""
    assert settings.start_hidden() is False
    assert settings.open_console_on_launch() is False
    assert settings.close_to_tray() is True          # tray app, always was
    assert settings.fox_scale() == 100
    assert settings.fox_opacity() == 100
    assert settings.always_on_top() is True          # WindowStaysOnTopHint
    assert settings.roam_speed() == 2                # the hard-coded speed
    assert settings.idle_break_frequency() == "normal"
    assert settings.tip_frequency() == "normal"
    assert settings.input_reactions_enabled() is True
    assert settings.hardware_reactions_enabled() is True
    assert settings.click_action() == "chat"
    assert settings.remember_position() is True
    assert settings.monitor_index() == -1            # follow primary
    assert settings.breach_poll_seconds() == 10      # BreachPollWorker's default
    assert settings.weekly_summary_enabled() is True
    assert settings.quiet_hours_enabled() is False   # opt-in, never assumed


def test_the_stored_values_survive_a_round_trip(settings):
    settings.set_fox_scale(125)
    settings.set_fox_opacity(75)
    settings.set_click_action("panel")
    settings.set_quiet_hours("23:30", "06:15")
    settings.set_breach_poll_seconds(45)
    assert settings.fox_scale() == 125
    assert settings.fox_opacity() == 75
    assert settings.click_action() == "panel"
    assert settings.quiet_hours() == ("23:30", "06:15")
    assert settings.breach_poll_seconds() == 45


def test_a_hand_edited_settings_file_cannot_produce_a_nonsense_fox(settings):
    settings.set_fox_scale(9000)
    settings.set_fox_opacity(0)
    settings.set_breach_poll_seconds(0)
    settings.set_roam_speed(999)
    assert settings.fox_scale() == cp.SCALE_MAX
    assert settings.fox_opacity() == cp.OPACITY_MIN
    assert settings.breach_poll_seconds() == cp.POLL_MIN
    assert settings.roam_speed() <= 6


def test_startup_state_is_not_mirrored_into_our_settings():
    """It lives in the OS. A copy here would go stale the moment the user
    revoked it from Task Manager, and we would then show a stale claim."""
    source = (_HERE / "fox_settings.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {n.name for n in ast.walk(tree)
             if isinstance(n, ast.FunctionDef)}
    assert not {n for n in names if "autostart" in n or "start_at_login" in n}


# ══ the dialog ══════════════════════════════════════════════════════════════
@pytest.fixture
def dialog(settings):
    from settings_dialog import SettingsDialog
    store = asm.MemoryBackend()
    store.supported = True
    dlg = SettingsDialog(settings, autostart=asm.Autostart(store))
    yield dlg, store
    dlg.reject()


def test_the_dialog_takes_an_injected_autostart(dialog):
    """Without this seam, ticking the box in a test would add a real login
    item to whoever ran the suite."""
    dlg, store = dialog
    assert dlg._autostart.backend is store


def test_ticking_the_box_writes_to_the_os_immediately(dialog):
    """Not on Save. Save/Cancel governs OUR settings; Cancel cannot undo an
    OS write, so a deferred toggle would leave the two disagreeing."""
    dlg, store = dialog
    dlg._autostart_check.setChecked(True)
    assert store.read() is True
    assert store.command, "no launch command was written"
    dlg._autostart_check.setChecked(False)
    assert store.read() is False


def test_a_refused_write_puts_the_switch_back(dialog, settings):
    """A switch showing a state the machine does not have is worse than no
    switch at all."""
    from settings_dialog import SettingsDialog
    store = asm.MemoryBackend(writable=False)
    store.supported = True
    dlg = SettingsDialog(settings, autostart=asm.Autostart(store))
    try:
        dlg._autostart_check.setChecked(True)
        assert dlg._autostart_check.isChecked() is False
        assert "startup" in dlg._autostart_note.text().lower()
    finally:
        dlg.reject()


def test_an_unsupported_platform_disables_the_box(settings):
    from settings_dialog import SettingsDialog
    dlg = SettingsDialog(settings, autostart=asm.Autostart(asm.MemoryBackend()))
    try:
        assert dlg._autostart_check.isEnabled() is False
        assert dlg._autostart_note.text() == asm.UNSUPPORTED
    finally:
        dlg.reject()


def test_the_box_reflects_the_os_on_every_open(settings):
    """"State drift": the user can revoke autostart from Task Manager while
    the app is closed, so the dialog must ask rather than remember."""
    from settings_dialog import SettingsDialog
    store = asm.MemoryBackend(enabled=True)
    store.supported = True
    first = SettingsDialog(settings, autostart=asm.Autostart(store))
    assert first._autostart_check.isChecked() is True
    first.reject()
    store._enabled = False                      # revoked outside the app
    second = SettingsDialog(settings, autostart=asm.Autostart(store))
    try:
        assert second._autostart_check.isChecked() is False
    finally:
        second.reject()


def test_saving_persists_every_control_on_both_new_tabs(dialog, settings):
    dlg, _store = dialog
    dlg._scale_slider.setValue(125)
    dlg._opacity_slider.setValue(70)
    dlg._on_top_check.setChecked(False)
    dlg._start_hidden_check.setChecked(True)
    dlg._close_tray_check.setChecked(False)
    dlg._roam_speed_slider.setValue(5)
    dlg._input_react_check.setChecked(False)
    dlg._hw_react_check.setChecked(False)
    dlg._remember_pos_check.setChecked(False)
    dlg._risk_slider.setValue(70)
    dlg._poll_slider.setValue(30)
    dlg._quiet_check.setChecked(True)
    dlg._weekly_check.setChecked(False)
    dlg._idle_combo.setCurrentIndex(dlg._idle_combo.findData("off"))
    dlg._click_combo.setCurrentIndex(dlg._click_combo.findData("console"))
    dlg._save()

    assert settings.fox_scale() == 125
    assert settings.fox_opacity() == 70
    assert settings.always_on_top() is False
    assert settings.start_hidden() is True
    assert settings.close_to_tray() is False
    assert settings.roam_speed() == 5
    assert settings.input_reactions_enabled() is False
    assert settings.hardware_reactions_enabled() is False
    assert settings.remember_position() is False
    assert settings.alert_min_risk() == 70
    assert settings.breach_poll_seconds() == 30
    assert settings.quiet_hours_enabled() is True
    assert settings.weekly_summary_enabled() is False
    assert settings.idle_break_frequency() == "off"
    assert settings.click_action() == "console"


def test_saving_does_not_touch_autostart(dialog, settings):
    """It already applied. Re-writing it on Save would resurrect an entry the
    user turned off, and Cancel would still not undo it."""
    dlg, store = dialog
    dlg._save()
    assert store.read() is False


def test_there_is_exactly_one_control_per_setting(dialog):
    """D13 folded the Behaviour tab in; had it not, roaming and glance would
    each have had two switches on two tabs, guaranteed to disagree."""
    from settings_dialog import ThemedCheckBox
    dlg, _store = dialog
    labels = [c.text() for c in dlg.findChildren(ThemedCheckBox)]
    assert len(labels) == len(set(labels)), f"duplicated control: {labels}"


def test_the_secret_error_still_surfaces_the_right_tab(dialog):
    """Inserting two tabs moved Foxy Audit from index 2 to 3; the handler used
    to hard-code 2, which would now open Alerts and hide the message."""
    dlg, _store = dialog
    dlg._show_secret_error(["org API key"])
    assert dlg._stack.currentIndex() == dlg._foxy_tab_index
    assert "keychain" in dlg._foxy_test_status.text()


def test_the_quiet_window_is_inert_until_quiet_hours_are_on(dialog):
    """Found by looking at it: the fields were live with the toggle off, so
    you could set 22:00-07:00, close the dialog, and wonder why the fox still
    beeped — the times were saved and ignored."""
    dlg, _store = dialog
    assert dlg._quiet_check.isChecked() is False
    assert dlg._quiet_row.isEnabled() is False
    dlg._quiet_check.setChecked(True)
    assert dlg._quiet_row.isEnabled() is True
    dlg._quiet_check.setChecked(False)
    assert dlg._quiet_row.isEnabled() is False


def test_the_dialog_opens_tall_enough_to_show_a_tab(dialog):
    """At the 560px minimum most of Companion sat below the fold on open."""
    dlg, _store = dialog
    assert dlg.height() >= 660


def test_both_new_tabs_scroll_so_no_control_is_unreachable(dialog):
    """The dialog is capped at 680px and these tabs carry ~13 controls each."""
    from PyQt6.QtWidgets import QScrollArea
    dlg, _store = dialog
    for index in (1, 2):                       # Companion, Alerts
        page = dlg._stack.widget(index)
        assert page.findChildren(QScrollArea), f"tab {index} cannot scroll"
