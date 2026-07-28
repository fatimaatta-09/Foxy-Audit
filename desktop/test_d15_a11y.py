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
    # labelled by an adjacent QLabel and a placeholder, but with no
    # setAccessibleName / setBuddy tying the two together
    "sb_prompt",
    "sb_response",
    "sb_seq",
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


def test_the_spot_check_fields_are_labelled_visually_but_not_for_a_reader(console):
    """The verify page's spot-check inputs each sit under a real always-visible
    QLabel (dashboard.py:1194-1210) and carry a placeholder, so a sighted user
    is fine. What is missing is the machine-readable tie: no
    `setAccessibleName` and no `QLabel.setBuddy`, so a screen reader reaches
    three unlabelled fields.

    Minor, and reported rather than fixed for the same reason as above. The
    house convention already exists one file over — `_Field` in auth_windows.py
    does `self.input.setAccessibleName(label)` — so the fix is to apply it."""
    for attr, placeholder in (("sb_prompt", "prompt"),
                              ("sb_response", "response"),
                              ("sb_seq", "")):
        field = getattr(console, attr)
        assert not _label_of(field), (
            f"{attr} is named now — remove it from UNNAMED and drop this "
            f"expectation")
        assert field.placeholderText(), \
            f"{attr} lost its placeholder too — now it has nothing at all"
        if placeholder:
            assert placeholder in field.placeholderText().lower()

    # the pattern they should be following, asserted so it cannot be lost
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


def test_the_companion_does_not_roam_the_screen_unless_asked(app, tmp_path):
    """The fox is the one animation that cannot consult `reduced_motion()` —
    an animated mascot that holds still is not the product. What matters for
    motion sensitivity is the large-amplitude motion: roaming across the
    desktop. That is opt-in and off by default, which is the mitigation, and
    this pins the default.

    Recorded as a finding rather than a fix: the sprite still animates in
    place under reduced motion (omni_fox.py drives it on a 100 ms tick with no
    `reduced_motion()` check), as do the console's window fade
    (dashboard.py:4670), the chat popup (clay_chat_popup.py:824) and the
    security overlay (security_overlay.py:33). Whether the mascot should
    honour the OS preference is a product decision, not an executor's."""
    from fox_settings import FoxSettings
    from foxy_client import MemorySecretStore

    store = QSettings(str(tmp_path / "fox.ini"), QSettings.Format.IniFormat)
    settings = FoxSettings(store, MemorySecretStore())
    assert settings.roaming_enabled() is False, \
        "the fox now roams out of the box — the reduced-motion mitigation is gone"

    source = (_HERE / "omni_fox.py").read_text(encoding="utf-8")
    assert "reduced_motion" not in source, (
        "omni_fox now consults reduced_motion — the companion honours the OS "
        "preference and this finding is closed; delete this assertion")


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
    # otherwise a rename would empty it out and it would pass forever
    reads = [path.name for path in sorted(_HERE.glob("*.py"))
             if not path.name.startswith("test_")
             and "devicePixelRatio" in path.read_text(encoding="utf-8")]
    assert reads == ["chrome_widgets.py"], (
        f"the display scale is now read in {reads} — the scan above was "
        f"written when only the annunciator's rasteriser consulted it")

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


def test_the_annunciator_icon_is_the_one_site_that_rasterises_for_the_display():
    """chrome_widgets.py:196 is the app's only DPR-aware raster: it allocates
    `18 * devicePixelRatioF()` device pixels and stamps the ratio on the
    pixmap, so the icon stays 18×18 logical and stays sharp when scaled.

    Pinned as the reference implementation the four sites below should copy."""
    source = (_HERE / "chrome_widgets.py").read_text(encoding="utf-8")
    assert "devicePixelRatioF()" in source and "setDevicePixelRatio(ratio)" in source, \
        "the annunciator lost its HiDPI rasterisation"


def test_four_vector_icons_still_rasterise_at_1x(app):
    """The finding. Four painted pixmaps allocate at logical size and never
    call `setDevicePixelRatio`, so on a 150% or 200% display the compositor
    upscales an 18-to-34 px bitmap and they render visibly soft:

      auth_windows._glyph      (34×34, the sign-in card's icon chip)
      dashboard._monogram      (30×30, the account chip)
      dashboard._paint_chain_icon (16×16, the ledger status icon)
      dashboard._fox_pixmap    (sprite scaled to a logical size)

    Cosmetic, not functional — nothing is unreadable or unreachable, which is
    why it is reported and not fixed here; each needs the same three-line
    change chrome_widgets already makes. Asserted so the count cannot grow and
    so that fixing one is noticed.

    `_glyph` is the one that can be checked on the object rather than the
    source, so it is."""
    from auth_windows import _glyph

    glyph = _glyph("key", 34)
    assert glyph.devicePixelRatio() == 1.0, (
        "_glyph now rasterises for the display — good; drop it from this "
        "finding and from the source scan below")
    assert glyph.width() == 34, "the buffer is still allocated at logical size"

    source = (_HERE / "dashboard.py").read_text(encoding="utf-8")
    for call, what in (("QPixmap(30, 30)", "_monogram"),
                       ("QPixmap(16, 16)", "_paint_chain_icon")):
        assert call in source, (
            f"{what} no longer allocates a fixed 1x buffer — if it is now "
            f"DPR-aware, remove it from this finding")
    assert "setDevicePixelRatio" not in source, (
        "dashboard.py started rasterising for the display; update this "
        "finding to match what is actually left")
