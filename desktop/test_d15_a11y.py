"""D15a — the accessibility, reduced-motion and HiDPI guards.

The other 698 desktop tests each ask "does this one page behave?". None of
them asks whether the app can be *operated* — whether a screen reader can name
the controls, whether Tab moves through a form in the order it reads, whether
someone who has asked their OS for less motion gets less motion, or whether
anything silently assumes a 1× display.

Those are regression guards. Where they found a real gap, the gap is recorded
here in an explicit, named ledger rather than fixed: this branch is the QA
sweep, and the fixes it points at are visible changes to the shipped app that
belong to the owner. A ledger entry is not a suppression — each one asserts
the exact shape of the gap, so the entry fails both when the gap gets worse
and when somebody quietly fixes it without deleting the entry.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PyQt6.QtCore import QAbstractAnimation, QSettings, Qt
from PyQt6.QtGui import QPainter, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView, QAbstractScrollArea, QPlainTextEdit, QScrollArea,
    QTextEdit, QWidget,
)

_HERE = Path(__file__).resolve().parent


@pytest.fixture(scope="module")
def app():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def console(app, tmp_path_factory):
    """Module-scoped, matching test_d5_pages: a full console is heavy, and one
    per test leaves enough windows and startup timers alive to fault the
    event queue."""
    from dashboard import DashboardWindow
    from fox_settings import FoxSettings
    from foxy_client import MemorySecretStore

    path = tmp_path_factory.mktemp("d15a") / "console.ini"
    store = QSettings(str(path), QSettings.Format.IniFormat)
    win = DashboardWindow(settings=FoxSettings(store, MemorySecretStore()))
    yield win
    win.close()


# ══ walking the widget tree ═════════════════════════════════════════════════
def _is_container(widget: QWidget) -> bool:
    """Scroll areas and item views are focusable, but they are not *controls*.

    A QScrollArea takes focus so the arrow keys can scroll it, and a
    QComboBox's popup QListView takes focus so the list can be driven — in
    both cases the thing a screen reader announces is the content, or the
    combo that owns the popup, not the scrolling machinery. Naming them would
    be noise, so they are out of scope for the name guard.

    QTextEdit is the trap here: it inherits QAbstractScrollArea, so the
    obvious `isinstance(w, QAbstractScrollArea)` filter silently drops every
    multi-line text input in the app — including the two spot-check fields
    this file reports as unnamed. Text edits are controls and stay in."""
    if isinstance(widget, (QTextEdit, QPlainTextEdit)):
        return False
    return isinstance(widget, (QAbstractScrollArea, QAbstractItemView))


def _controls(root: QWidget) -> list[QWidget]:
    return [w for w in root.findChildren(QWidget)
            if w.focusPolicy() != Qt.FocusPolicy.NoFocus and not _is_container(w)]


def _label_of(widget: QWidget) -> str:
    """What a screen reader would announce: the explicit accessible name, or
    failing that the visible text on the control itself."""
    name = (widget.accessibleName() or "").strip()
    if name:
        return name
    if hasattr(widget, "text"):
        try:
            return (widget.text() or "").strip()
        except (TypeError, RuntimeError):
            return ""
    return ""


def _attr_name(root: QWidget, widget: QWidget) -> str:
    """The attribute the console holds this widget under, so a failure names
    the control the way the source does instead of printing 'QPushButton'."""
    for attr in dir(root):
        if attr.startswith("__"):
            continue
        try:
            value = getattr(root, attr)
        except Exception:
            continue
        if value is widget:
            return attr
        if isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                if item is widget:
                    return f"{attr}[{index}]"
    return f"<unnamed {type(widget).__name__}#{widget.objectName()}>"


# ══ accessible names ════════════════════════════════════════════════════════
# The controls that reach a built console with neither visible text nor an
# accessible name. Every entry is a finding reported to MAIN, not a decision
# to leave it broken — see the two tests below for what each one is and why it
# is not fixed on this branch.
UNNAMED = {
    # (min_btn / close_btn used to sit here — named in the same change that
    #  merged this ledger, so the entries are gone rather than annotated)
    # (sb_prompt / sb_response / sb_seq sat here too: labelled by an adjacent
    #  QLabel and a placeholder, but with nothing tying the two together for a
    #  reader. They now carry setAccessibleName — see the test below.)
    # empty until data arrives; both are asserted to name themselves once
    # populated, by test_controls_that_fill_in_later_name_themselves
    "v_anchor_link",
}

#: The announcement banner's action button is created inside AnnouncementBanner
#: and is not held under a console attribute, so it cannot be listed above. It
#: is blank only until a message arrives — see
#: test_controls_that_fill_in_later_name_themselves.
ANONYMOUS_UNNAMED = 1


def test_every_interactive_control_can_be_named(console):
    """The regression guard. ~79 setAccessibleName calls across 16 files put
    the console in this state; this is what keeps the eightieth control from
    shipping mute.

    A new unnamed control fails here. Closing a known gap also fails here —
    deliberately — because a stale entry in UNNAMED is how a ledger rots into
    a suppression list."""
    unnamed, named = set(), 0
    for widget in _controls(console):
        if _label_of(widget):
            named += 1
            continue
        # an announcement can also come from a tooltip-only control; the app
        # sets tooltips alongside names, never instead of them, so a tooltip
        # is accepted as a name if one is ever used that way
        if (widget.toolTip() or "").strip():
            named += 1
            continue
        unnamed.add(_attr_name(console, widget))

    assert named > 100, (
        f"only {named} named controls — the console did not build far enough "
        f"for this guard to mean anything")

    # Controls the console does not hold under an attribute cannot be named in
    # UNNAMED, so they are counted instead of listed. Letting them through
    # unchecked would be the hole in this guard: a new unnamed button created
    # inline would never appear anywhere.
    anonymous = {u for u in unnamed if u.startswith("<unnamed")}
    named_in_ledger = unnamed - anonymous

    new = named_in_ledger - UNNAMED
    assert not new, (
        f"interactive controls with no accessible name and no visible text: "
        f"{sorted(new)}. A screen reader announces these as unlabelled. Give "
        f"each a setAccessibleName, or add it to UNNAMED with the reason.")

    assert len(anonymous) <= ANONYMOUS_UNNAMED, (
        f"{len(anonymous)} unnamed controls are not reachable as console "
        f"attributes (was {ANONYMOUS_UNNAMED}): {sorted(anonymous)}")

    fixed = UNNAMED - named_in_ledger
    assert not fixed, (
        f"{sorted(fixed)} now has an accessible name — delete it from UNNAMED "
        f"so the guard covers it properly.")


def test_the_window_controls_are_named_like_every_other_top_bar_button(console):
    """`min_btn` and `close_btn` are icon-only QPushButtons with no text, so an
    accessible name is the only thing a screen reader can announce. They were
    the sole controls on the bar without one — notifications, threats, account
    and refresh all had one, refresh being set on the line directly after the
    two that missed out — which made it an omission rather than a convention.

    Now closed. The assertion is inverted rather than deleted: an icon-only
    control silently losing its name is exactly the regression that produced
    the finding, and it reads as "button" to a reader either way."""
    for attr in ("min_btn", "close_btn"):
        button = getattr(console, attr)
        assert _label_of(button), (
            f"{attr} lost its accessible name — it has no visible text, so a "
            f"reader now announces a bare 'button'")
        assert button.isEnabled(), f"{attr} is a live, clickable control"

    # the neighbours that show the convention this pair missed
    for attr in ("notif_btn", "top_breach_btn", "user_btn", "refresh_btn"):
        assert _label_of(getattr(console, attr)), \
            f"{attr} lost its accessible name — the convention is eroding"


def test_the_spot_check_fields_are_labelled_for_a_reader_as_well_as_the_eye(console):
    """The verify page's spot-check inputs each sit under a real always-visible
    QLabel (dashboard.py:1194-1212) and carry a placeholder, so a sighted user
    was always fine. What was missing was the machine-readable tie — no
    `setAccessibleName`, no `QLabel.setBuddy` — so a reader reached three
    unlabelled fields on the page that carries the product's core proof.

    Now closed, with the house convention that already existed one file over:
    `_Field` in auth_windows.py does `self.input.setAccessibleName(label)`.
    Inverted rather than deleted, because the placeholder is the thing that
    makes this look fine in a screenshot while being broken for a reader —
    exactly the regression that produced the finding.

    The name is title-cased, not the label's shouted display text: an
    announcement of "ORIGINAL PROMPT" is read as an acronym by some readers."""
    for attr, expect, placeholder in (("sb_prompt", "Original Prompt", "prompt"),
                                      ("sb_response", "Original Response", "response"),
                                      ("sb_seq", "Ledger Seq #", "")):
        field = getattr(console, attr)
        assert field.accessibleName() == expect, (
            f"{attr} announces {field.accessibleName()!r}, not {expect!r} — a "
            f"reader has nothing but the placeholder to go on again")
        assert field.placeholderText(), \
            f"{attr} lost its placeholder — the sighted hint went with it"
        if placeholder:
            assert placeholder in field.placeholderText().lower()

    # the pattern they now follow, asserted so it cannot be lost at the source
    from auth_windows import _Field
    assert _Field("Email", placeholder="you@company.com").input.accessibleName() \
        == "Email"


def test_controls_that_fill_in_later_name_themselves(console):
    """Two controls are legitimately blank on a freshly built console because
    they carry data that has not arrived: the announcement banner's action
    button, and the verify page's anchor receipt link. Blank-and-hidden is not
    a naming gap — shipping them blank once populated would be. So this drives
    them and checks they announce themselves."""
    banner = console.ann_banner if hasattr(console, "ann_banner") else None
    if banner is None:                      # find it by type, not by guessing
        from chrome_widgets import AnnouncementBanner
        banner = console.findChild(AnnouncementBanner)
    assert banner is not None

    banner.show_message({"id": "m1", "text": "Chain head anchored",
                         "tone": "warn", "icon": "warn", "action": "View",
                         "target": "verify"})
    assert _label_of(banner), "a shown announcement must announce itself"
    assert "anchored" in banner.accessibleName()

    console.v_anchor_link.setText("Sepolia receipt 0xabc")
    assert _label_of(console.v_anchor_link), \
        "a populated anchor receipt link must carry its text"


# ══ focus order ═════════════════════════════════════════════════════════════
def _layout_order(root: QWidget) -> list[QWidget]:
    """Focusable widgets in the order the layout tree lays them out — which,
    for the console's vertical cards and forms, is reading order."""
    found: list[QWidget] = []

    def walk_widget(widget: QWidget):
        if isinstance(widget, QAbstractItemView):
            return                     # rows are model data, not laid-out widgets
        if isinstance(widget, QScrollArea):
            # a scroll area's content hangs off .widget(), not .layout()
            inner = widget.widget()
            if inner is not None:
                walk_widget(inner)
            return
        layout = widget.layout()
        if layout is not None:
            walk_layout(layout)

    def walk_layout(layout):
        for index in range(layout.count()):
            item = layout.itemAt(index)
            child = item.widget()
            if child is not None:
                if child.focusPolicy() != Qt.FocusPolicy.NoFocus:
                    found.append(child)
                walk_widget(child)
            elif item.layout() is not None:
                walk_layout(item.layout())

    walk_widget(root)
    return found


def _focus_ring(start: QWidget, limit: int = 20_000) -> dict[int, int]:
    """Tab positions along the focus chain containing `start`, counted from it.

    Qt's focus chain is not one ring per window — a QScrollArea's content
    widget forms its own sub-chain, so walking from a console page reaches
    three widgets while walking from the scroll area's inner widget reaches a
    hundred and fifty. Walking from a widget that is actually in the chain
    under test is therefore the only reliable way to read it, and starting at
    the first control in reading order means a correct chain numbers the rest
    in ascending order with no rotation to undo."""
    order, widget, steps = {id(start): 0}, start, 0
    while steps < limit:
        widget = widget.nextInFocusChain()
        steps += 1
        if widget is None or id(widget) in order:
            break
        order[id(widget)] = len(order)
    return order


def _tab_positions(laid_out: list[QWidget]) -> list[list[int]]:
    """Group widgets by the focus sub-chain they belong to, keeping each
    group's tab positions in the order the layout laid them out."""
    groups: list[list[int]] = []
    rings: list[dict[int, int]] = []
    for widget in laid_out:
        for ring, group in zip(rings, groups):
            if id(widget) in ring:
                group.append(ring[id(widget)])
                break
        else:
            rings.append(_focus_ring(widget))
            groups.append([0])
    return groups


def test_focus_order_follows_visual_order_on_every_page(console):
    """Tab must walk a form the way the eye reads it.

    Geometry cannot answer this on an unshown window — half the widgets sit at
    their card's origin until the layout is resolved on screen — so the check
    is made against the layout tree instead, which *is* the visual order for
    these pages: every one is a stack of full-width cards in a QVBoxLayout,
    built top to bottom. Comparing tab order to layout order catches the two
    ways this actually breaks: a `setTabOrder` that fights the layout, and a
    control created out of sequence and inserted above where it was made.

    This covers the forms explicitly — Policy (rulesets), Export (date range +
    type), Settings (profile, password, 2FA), Access (keys), and the verify
    page's spot-check — since every one of them is a console page."""
    pages = [console.stack.widget(i) for i in range(console.stack.count())]
    assert len(pages) >= 9, "the console lost pages; this guard covers all of them"

    total, biggest = 0, 0
    for index, page in enumerate(pages):
        laid_out = _layout_order(page)
        total += len(laid_out)

        for group in _tab_positions(laid_out):
            biggest = max(biggest, len(group))
            assert group == sorted(group), (
                f"page {index}: Tab order does not follow layout order — the "
                f"chain visits {len(group)} controls at {group[:10]}. Reading "
                f"order is {[type(w).__name__ for w in laid_out[:8]]}.")

    assert total > 120, (
        f"only {total} focusable controls across {len(pages)} pages — the "
        f"console did not build far enough for this to mean anything")
    assert biggest > 20, (
        f"the largest chain checked held only {biggest} controls — the "
        f"grouping fell apart into singletons and this guard proves nothing")


def test_the_sign_in_form_tabs_label_then_field_then_submit(app):
    """The one form a user meets before anything else, and the only one built
    outside the console. `_Field` is a QLabel above a QLineEdit; the label is
    not focusable, so a correct chain is field, field, button — never the
    submit button before the last field."""
    from auth_windows import _Field
    from PyQt6.QtWidgets import QPushButton, QVBoxLayout, QWidget as W

    host = W()
    box = QVBoxLayout(host)
    email = _Field("Email", placeholder="you@company.com")
    password = _Field("Password", password=True)
    submit = QPushButton("Sign in")
    for widget in (email, password, submit):
        box.addWidget(widget)

    ring = _focus_ring(host)
    assert ring[id(email.input)] < ring[id(password.input)] < ring[id(submit)]
    assert email.label.focusPolicy() == Qt.FocusPolicy.NoFocus, \
        "the field label is not a tab stop; it labels the field beside it"
    host.deleteLater()


# ══ reduced motion ══════════════════════════════════════════════════════════
def test_charts_do_not_animate_when_the_os_asks_for_less_motion(app, monkeypatch):
    """`FoxChart._replay` is the app's busiest animation — every chart on a
    page draws in at once. Under reduced motion the data must still be there;
    only the movement goes."""
    import charts
    from charts import FoxChart

    chart = FoxChart("bar")
    chart.show()                      # _replay ignores charts nobody is looking at
    try:
        monkeypatch.setattr(charts, "reduced_motion", lambda: True)
        chart.set_data([{"label": "a", "value": 1}, {"label": "b", "value": 2}])
        assert chart._progress == 1.0, \
            "reduced motion must land on the finished frame, not a blank one"
        # PyQt enum members are truthy even at value 0 — compare, never `not`
        assert chart._anim.state() == QAbstractAnimation.State.Stopped, \
            "the animation timer is still running"
        assert not chart._animated_once, "the draw-in ran anyway"
        assert chart._has_data(), "the data itself must survive"
    finally:
        chart.stop_animation()
        chart.close()
        chart.deleteLater()


def test_charts_do_animate_when_motion_is_fine(app, monkeypatch):
    """The other half of the pin: without it, a `reduced_motion` stuck at True
    would make the test above pass for the wrong reason."""
    import charts
    from charts import FoxChart

    chart = FoxChart("bar")
    chart.show()
    try:
        monkeypatch.setattr(charts, "reduced_motion", lambda: False)
        chart.set_data([{"label": "a", "value": 1}, {"label": "b", "value": 2}])
        assert chart._animated_once, "the draw-in did not run at all"
    finally:
        chart.stop_animation()
        chart.close()
        chart.deleteLater()


def test_the_reduced_motion_probe_fails_closed(monkeypatch):
    """`reduced_motion()` reads a Win32 system parameter. If that call ever
    raises or the platform has no answer, it must return False — claiming
    "the user wants reduced motion" on every non-Windows machine would silently
    kill the app's motion design for everyone."""
    import foxy_tokens
    monkeypatch.setattr(foxy_tokens.sys, "platform", "linux")
    assert foxy_tokens.reduced_motion() is False


def test_the_shortcuts_overlay_skips_its_fade_under_reduced_motion(app, monkeypatch):
    """The `?` cheat sheet fades in over 160 ms (chrome_widgets.py:536). Under
    reduced motion it must appear at full opacity immediately — not appear
    invisible and stay that way, which is what a half-applied skip looks
    like, and which no existing test would notice."""
    import chrome_widgets
    monkeypatch.setattr(chrome_widgets, "reduced_motion", lambda: True)
    from chrome_widgets import ShortcutsOverlay

    overlay = ShortcutsOverlay()
    try:
        overlay.show_centered()
        assert overlay.windowOpacity() == 1.0, \
            "reduced motion skipped the fade but left the overlay transparent"
        assert overlay.isVisible()
    finally:
        overlay.close()
        overlay.deleteLater()


def test_the_companion_stops_roaming_when_the_os_asks_for_less_motion(app, tmp_path):
    """The fox used to be the one animation that consulted nothing, on the
    argument that a mascot which holds still is not the product. That argument
    is right about the sprite and wrong about the walk, and the two are
    separable: an idle blink on a 100 ms tick is not what a motion-sensitivity
    preference is about, and a sprite crossing the width of the desktop
    unprompted is precisely what it is about.

    So roaming — the one large-amplitude, unbidden movement the app makes —
    now stops under reduced motion, and the sprite keeps ticking in place.
    Roaming being opt-in and off by default was the old mitigation; that
    default is still pinned below, because it is what protects anyone whose OS
    cannot be asked (`reduced_motion()` has no answer off Windows).

    Driven, not grepped. The version this replaces asserted `"reduced_motion"
    not in omni_fox.py` — a source scan that a stray mention in a comment would
    have satisfied."""
    from fox_settings import FoxSettings
    from foxy_client import MemorySecretStore

    store = QSettings(str(tmp_path / "fox.ini"), QSettings.Format.IniFormat)
    settings = FoxSettings(store, MemorySecretStore())
    assert settings.roaming_enabled() is False, \
        "the fox now roams out of the box — the off-Windows mitigation is gone"

    import omni_fox

    class _Fox:
        """Only what `_roaming_tick` touches — building the real companion
        starts sensor threads, a tray icon and a breach poller."""
        state = "WALKING"
        current_row = 99
        current_frame = 7
        roam_target_x = 400
        is_dragging = _chat_open = _user_placed = _roam_paused = False
        _roam_pause_until = 0.0

        def __init__(self, roaming):
            self.settings = type("S", (), {"roaming_enabled": lambda _: roaming,
                                           "roam_speed": lambda _: 2})()
            self.moved = []

        def x(self):
            return 100

        def move(self, x, y):
            self.moved.append((x, y))

    for reduced, should_move in ((True, False), (False, True)):
        fox = _Fox(roaming=True)
        original = omni_fox.reduced_motion
        omni_fox.reduced_motion = lambda: reduced
        try:
            fox.screen_geom = app.primaryScreen().availableGeometry()
            fox._cell_w = 192
            fox._bottom_y = 900
            omni_fox.OmniAwareFox._roaming_tick(fox)
        finally:
            omni_fox.reduced_motion = original

        assert bool(fox.moved) is should_move, (
            f"with reduced_motion={reduced} the fox "
            f"{'walked' if fox.moved else 'stayed put'} — it should "
            f"{'walk' if should_move else 'stay put'}")

    # and it settles rather than freezing mid-stride: a fox left in WALKING
    # keeps playing the walk cycle on the spot, which is a worse animation
    # than the one that was removed
    frozen = _Fox(roaming=True)
    frozen.screen_geom = app.primaryScreen().availableGeometry()
    frozen._cell_w, frozen._bottom_y = 192, 900
    original = omni_fox.reduced_motion
    omni_fox.reduced_motion = lambda: True
    try:
        omni_fox.OmniAwareFox._roaming_tick(frozen)
    finally:
        omni_fox.reduced_motion = original
    assert frozen.state == "IDLE" and frozen.roam_target_x is None, \
        "the fox stopped walking but stayed in the WALKING state"


def test_the_console_window_lands_opaque_when_motion_is_reduced(console, monkeypatch):
    """The console fades in over 190 ms from `windowOpacity = 0`
    (dashboard.py:4657). Skipping the animation without landing the opacity is
    the failure mode that matters here — it does not look like less motion, it
    looks like the console failed to open."""
    import dashboard
    monkeypatch.setattr(dashboard, "_reduced_motion", lambda: True)
    console.show_animated()
    try:
        assert console.windowOpacity() == 1.0, \
            "reduced motion skipped the fade and left the console invisible"
    finally:
        console.hide()


def test_the_chat_popup_arrives_in_place_when_motion_is_reduced(app, tmp_path,
                                                                monkeypatch):
    """The popup fades AND slides up 18 px (clay_chat_popup.py:824). Both have
    to go together — a slide with no fade, or a fade that leaves the window 18
    px low, is worse than either animation."""
    import clay_chat_popup
    monkeypatch.setattr(clay_chat_popup, "_reduced_motion", lambda: True)
    from clay_chat_popup import ChatPopup
    from fox_settings import FoxSettings
    from foxy_client import MemorySecretStore

    store = QSettings(str(tmp_path / "chat.ini"), QSettings.Format.IniFormat)
    host = QWidget()
    popup = ChatPopup(host, FoxSettings(store, MemorySecretStore()))
    try:
        popup.show_animated()
        assert popup.windowOpacity() == 1.0, \
            "reduced motion skipped the fade but left the popup transparent"
        assert popup.isVisible(), "the popup did not open at all"
        assert not hasattr(popup, "_anim_pos"), "the popup slid anyway"
        assert not hasattr(popup, "_anim_group"), \
            "the entrance animation group was built and started regardless"
    finally:
        popup.close()
        popup.deleteLater()
        host.deleteLater()


def test_the_security_glow_still_shows_itself_when_motion_is_reduced(app,
                                                                    monkeypatch):
    """The overlay's glow is feedback — it is how a hash confirmation and a
    breach announce themselves on the sprite — so reduced motion must take the
    ramp and leave the signal. Silencing it would trade an accessibility
    preference for a security one, which is not a trade this app gets to make.
    """
    import security_overlay
    monkeypatch.setattr(security_overlay, "reduced_motion", lambda: True)
    from security_overlay import SecurityOverlay

    host = QWidget()
    overlay = SecurityOverlay(host)
    try:
        overlay.flash_green()
        assert overlay.opacity_prop == 1.0, \
            "the glow was skipped entirely — the confirmation is now invisible"
        assert overlay._fade_anim.state() == QAbstractAnimation.State.Stopped, \
            "the fade animation ran anyway"

        monkeypatch.setattr(security_overlay, "reduced_motion", lambda: False)
        overlay.flash_green()
        assert overlay._fade_anim.state() == QAbstractAnimation.State.Running, \
            "the other half of the pin: the ramp must still run normally"
        overlay._fade_anim.stop()
    finally:
        overlay.deleteLater()
        host.deleteLater()


# ══ HiDPI ═══════════════════════════════════════════════════════════════════
def test_no_layout_maths_reads_the_display_scale(console):
    """Nothing may size or position itself from the display's scale factor.

    Qt lays out in logical pixels and scales at the backing store, so a layout
    is HiDPI-correct precisely by *not* consulting the ratio. The failure mode
    is a widget that multiplies a margin, a fixed size or a font by
    `devicePixelRatio()` and so doubles on a 200% display while everything
    around it stays put.

    Asserted at the source, because it cannot be asserted at runtime: Qt fixes
    a window's scale when it is created, and `QWidget.render()` into a 2×
    pixmap is not a substitute — it changes the paint device under the font
    metrics and relayouts wrapped text, so it fails for reasons that have
    nothing to do with HiDPI."""
    sizing = ("resize", "setFixedSize", "setFixedWidth", "setFixedHeight",
              "setMinimumWidth", "setMinimumHeight", "setMaximumWidth",
              "setMaximumHeight", "setContentsMargins", "setSpacing",
              "setPointSize", "setPointSizeF", "move")

    offenders = []
    for path in sorted(_HERE.glob("*.py")):
        if path.name.startswith("test_"):
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "devicePixelRatio" not in line:
                continue
            if any(f"{call}(" in line for call in sizing):
                offenders.append(f"{path.name}:{number}: {line.strip()}")

    assert not offenders, (
        "layout maths is reading the display scale — on a 200% display these "
        "will double while the logical layout around them does not:\n  "
        + "\n  ".join(offenders))

    # …and the scan is only meaningful if the ratio is read *somewhere*,
    # otherwise a rename would empty it out and it would pass forever. The
    # list grew from one file to three when the three remaining 1x rasters
    # were fixed; it is spelled out rather than counted so that a *fourth*
    # reader has to be justified here before it ships.
    reads = [path.name for path in sorted(_HERE.glob("*.py"))
             if not path.name.startswith("test_")
             and "devicePixelRatio" in path.read_text(encoding="utf-8")]
    assert reads == ["auth_windows.py", "chrome_widgets.py", "dashboard.py"], (
        f"the display scale is now read in {reads} — every reader must be a "
        f"rasteriser allocating device pixels, never layout maths")

    # the runtime half of the HiDPI guard is the two tests below: they check
    # that painting at 2x keeps the same logical geometry. It is deliberately
    # not checked by rendering a whole page into a 2x pixmap — that swaps the
    # paint device under the font metrics and re-wraps text, so it fails for
    # reasons that have nothing to do with the display scale.
    assert console.stack.count() >= 9


def test_the_annunciator_icon_rasterises_at_the_widget_scale(app):
    """The behavioural half of the HiDPI guard, on the one widget that gets it
    right: the announcement banner allocates `18 * devicePixelRatioF()` device
    pixels and stamps the ratio on the pixmap, so the icon stays 18×18 logical
    and stays sharp when the display is scaled."""
    from chrome_widgets import AnnouncementBanner

    banner = AnnouncementBanner()
    try:
        banner.show_message({"id": "m2", "text": "Anchor confirmed",
                             "tone": "warn", "icon": "warn", "action": "View",
                             "target": "verify"})
        pixmap = banner.icon.pixmap()
        ratio = banner.devicePixelRatioF() or 1.0

        assert pixmap.devicePixelRatio() == pytest.approx(ratio), \
            "the icon buffer does not carry the ratio it was painted at"
        assert pixmap.width() == int(18 * ratio), \
            "the icon buffer is not allocated in device pixels"
        assert pixmap.deviceIndependentSize().width() == pytest.approx(18, abs=1), \
            "the icon no longer occupies 18 logical pixels"
    finally:
        banner.close()
        banner.deleteLater()


def test_the_icon_painter_draws_at_the_same_logical_size_on_a_2x_buffer():
    """`paint_icon` takes a QRectF in logical coordinates. On a 2× surface it
    must fill the same logical rect — twice the device pixels, same size on
    screen — rather than drawing at half size in the corner."""
    from foxy_tokens import paint_icon, qcolor
    from PyQt6.QtCore import QRectF

    drawn = {}
    for ratio in (1.0, 2.0):
        pixmap = QPixmap(int(18 * ratio), int(18 * ratio))
        pixmap.setDevicePixelRatio(ratio)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        paint_icon(painter, QRectF(0, 0, 18, 18), "shield", qcolor("#ffffff"), 2.0)
        painter.end()

        image = pixmap.toImage()
        box = image.rect()
        xs = [x for x in range(image.width()) for y in range(image.height())
              if image.pixelColor(x, y).alpha() > 0]
        ys = [y for y in range(image.height()) for x in range(image.width())
              if image.pixelColor(x, y).alpha() > 0]
        assert xs and ys, f"nothing was drawn at {ratio}x"
        drawn[ratio] = ((min(xs) / ratio, min(ys) / ratio),
                        (max(xs) / ratio, max(ys) / ratio), box)

    (x1a, y1a), (x2a, y2a), _ = drawn[1.0]
    (x1b, y1b), (x2b, y2b), _ = drawn[2.0]
    assert abs(x1a - x1b) <= 1 and abs(y1a - y1b) <= 1, \
        "the icon starts somewhere else on a 2x surface"
    assert abs((x2a - x1a) - (x2b - x1b)) <= 1, \
        "the icon is a different logical width on a 2x surface"
    assert abs((y2a - y1a) - (y2b - y1b)) <= 1, \
        "the icon is a different logical height on a 2x surface"


def test_the_annunciator_icon_keeps_the_raster_the_others_copied():
    """chrome_widgets.py:196 was the app's only DPR-aware raster and is now the
    pattern the other four follow: allocate `logical * devicePixelRatioF()`
    device pixels and stamp the ratio on the pixmap, so the icon keeps its
    logical size and gains its sharpness."""
    source = (_HERE / "chrome_widgets.py").read_text(encoding="utf-8")
    assert "devicePixelRatioF()" in source and "setDevicePixelRatio(ratio)" in source, \
        "the annunciator lost its HiDPI rasterisation"


def test_the_four_vector_icons_rasterise_for_the_display(app):
    """Four painted pixmaps used to allocate at logical size and never call
    `setDevicePixelRatio`, so on a 150% or 200% display the compositor
    upscaled a 16-to-34 px bitmap and they rendered visibly soft:

      auth_windows._glyph          (34×34, the sign-in card's icon chip)
      dashboard._monogram          (30×30, the account chip)
      dashboard._paint_chain_icon  (16×16, the ledger status icon)
      dashboard._fox_pixmap        (sprite scaled to a logical size)

    All four now do what the annunciator does. Inverted rather than deleted,
    because the regression is silent by construction: on the 1× display most
    development happens on, every one of these looks identical whether or not
    the ratio is applied, and no other test in the suite would notice.

    Both halves are asserted for each: the buffer must be allocated in DEVICE
    pixels *and* carry the ratio. Allocating without stamping gives a
    double-size icon; stamping without allocating gives a half-size one."""
    from auth_windows import _glyph

    # forced to 2x rather than read from the test machine's screen, which is
    # almost always 1x — where a broken implementation still passes
    for size, ratio in ((34, 1.0), (34, 2.0), (36, 1.5)):
        glyph = _glyph("key", size, ratio)
        assert glyph.devicePixelRatio() == pytest.approx(ratio), \
            f"_glyph({size}, {ratio}) does not carry the ratio it painted at"
        assert glyph.width() == int(size * ratio), \
            f"_glyph({size}, {ratio}) is not allocated in device pixels"
        assert glyph.deviceIndependentSize().width() == pytest.approx(size, abs=1), \
            f"_glyph({size}, {ratio}) no longer occupies {size} logical pixels"

    # _glyph with no ratio falls back to the screen, never to a constant 1.0
    from PyQt6.QtGui import QGuiApplication
    screen = QGuiApplication.primaryScreen().devicePixelRatio()
    assert _glyph("key", 34).devicePixelRatio() == pytest.approx(screen), \
        "_glyph stopped consulting the screen when no ratio is passed"

    # The three on the console are bound to it, so they are read there — one
    # method body at a time, cut at the next `def`. A fixed-size window instead
    # of the real boundary was the first version of this, and it did not bite:
    # _monogram passed on _paint_chain_icon's call three lines further down.
    source = (_HERE / "dashboard.py").read_text(encoding="utf-8")
    for what in ("_monogram", "_paint_chain_icon", "_fox_pixmap"):
        after = source.split(f"    def {what}(", 1)
        assert len(after) == 2, f"{what} is gone"
        body = after[1].split("\n    def ", 1)[0]
        assert "devicePixelRatioF()" in body, (
            f"{what} stopped asking the widget for the display scale — on a "
            f"200% display it is soft again, and nothing else here notices")
        assert "setDevicePixelRatio(ratio)" in body, (
            f"{what} allocates device pixels without stamping the ratio, so "
            f"the icon now draws at the display scale times its logical size")
