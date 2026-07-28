"""The sign-in card (P3 §2).

The owner's `cramped.png` showed three problems on one card: a placeholder you
cannot read, no way to see what you are typing, and "Forgot password?" set
smaller than everything around it.

Contrast is MEASURED here, not asserted by token name, so the test still bites if
someone changes what `--muted` or `--bg` resolve to. The rest is a markup
contract: an eye toggle is only an accessibility win if it keeps its label, its
state, its focus ring and its hit area, and every one of those is easy to lose in
a restyle.

Shares its colour maths with test_p1_contrast.py — deliberately, so the two
cannot drift.
"""

from __future__ import annotations

import re

import pytest

from test_p1_contrast import AA_BODY, css, ratio, themes  # noqa: F401 — fixtures

# Anything below this is not a real touch target (WCAG 2.5.5 / the 44px rule).
MIN_TARGET_PX = 44


@pytest.fixture(scope="module")
def html() -> str:
    from test_p1_contrast import HTML
    return HTML.read_text(encoding="utf-8")


def _block(css_text: str, selector: str) -> str:
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css_text)
    assert m, f"{selector} is gone from the stylesheet"
    return m.group(1)


def _gate(html_text: str) -> str:
    """Just the auth overlay — assertions must not accidentally pass because of
    markup somewhere else on the page."""
    start = html_text.index('id="authGate"')
    return html_text[start:html_text.index("<script", start)]


# ══ §2.1 · the placeholder you could not read ══════════════════════════════

@pytest.mark.parametrize("theme", ["dark", "light"])
def test_placeholder_clears_aa_against_the_field(themes, css, theme):
    """Measured, in both themes. The shipped value was 3.09:1 dark / 2.61:1
    light — that is decoration, not a hint."""
    decl = _block(css, ".cin::placeholder")
    m = re.search(r"color:\s*var\(--([a-z0-9-]+)\)", decl)
    assert m, ".cin::placeholder no longer sets a token colour"
    tokens = themes[theme]
    got = ratio(tokens[m.group(1)], tokens["bg"])
    assert got >= AA_BODY, (
        f"[{theme}] the sign-in placeholder measures {got:.2f}:1 on the field "
        f"background. The owner reported this exact failure.")


def test_placeholder_matches_the_call_desktop_already_made(css):
    """auth_windows.py paints PlaceholderText with --muted deliberately. Web wins
    on conflicts, but here there is no conflict — the two must simply agree."""
    assert "var(--muted)" in _block(css, ".cin::placeholder")


# ══ §2.2 · the eye ═════════════════════════════════════════════════════════

def test_both_password_fields_in_the_gate_have_a_toggle(html):
    gate = _gate(html)
    for field in ("authPass", "resetPass"):
        assert f'data-pw-toggle="{field}"' in gate, f"{field} has no reveal control"


def test_the_toggle_is_a_real_button_that_will_not_submit(html):
    """An untyped <button> inside a form submits it — clicking "show password"
    would attempt a sign-in."""
    for m in re.finditer(r'<button[^>]*class="pw-eye"[^>]*>', _gate(html)):
        assert 'type="button"' in m.group(0)


def test_the_toggle_carries_a_name_and_a_state(html):
    """An icon-only button with no accessible name is unusable with a screen
    reader, and aria-pressed alone is announced inconsistently — an auth control
    gets both."""
    for m in re.finditer(r'<button[^>]*class="pw-eye"[^>]*>', _gate(html)):
        tag = m.group(0)
        assert 'aria-pressed="false"' in tag
        assert 'aria-label="Show password"' in tag
        assert "aria-controls=" in tag


def test_the_script_updates_both_the_state_and_the_name(html):
    assert "btn.setAttribute('aria-pressed'" in html
    assert "btn.setAttribute('aria-label',label(show))" in html
    assert "return on?'Hide password':'Show password';" in html


def test_the_hit_area_is_a_real_target(css):
    decl = _block(css, ".pw-eye")
    for axis in ("width", "height"):
        m = re.search(axis + r":\s*(\d+)px", decl)
        assert m and int(m.group(1)) >= MIN_TARGET_PX, (
            f".pw-eye {axis} is under {MIN_TARGET_PX}px — the drawing may be "
            f"20px, but the control is what you have to hit.")


def test_the_field_makes_room_so_the_icon_never_sits_on_the_text(css):
    assert re.search(r"padding-right:\s*\d+px", _block(css, ".pw-wrap .cin"))


def test_the_toggle_keeps_a_visible_focus_ring(css):
    """Keyboard users need to see where they are, and the ring must be the one
    the fields already use rather than a new invention. `.pw-eye:focus-visible`
    appears in more than one rule, so scan them all."""
    rings = [m.group(1) for m in
             re.finditer(r"\.pw-eye:focus-visible\s*\{([^}]*)\}", css)]
    assert rings, ".pw-eye has no :focus-visible rule at all"
    assert any("2px solid var(--fox)" in r for r in rings), (
        "the reveal button lost its focus ring — keyboard users cannot see it")


def test_the_icon_is_inline_svg_with_no_external_fetch(html):
    """CSP forbids an icon library or any remote asset."""
    assert "<svg viewBox=\"0 0 24 24\"" in html
    block = html[html.index("var SVG="):html.index("function label(")]
    assert "http" not in block and "url(" not in block


def test_the_slash_animation_respects_reduced_motion(css):
    m = re.search(r"@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{(.*?)\}\s*\}",
                  css, re.S)
    assert m and "pw-slash" in m.group(1), \
        "the reveal animation ignores prefers-reduced-motion"


def test_revealing_never_outlives_the_card(html):
    """A password left on screen after sign-in is a password shown to whoever
    walks past next."""
    assert "window.foxHidePasswords=function()" in html
    assert html.count("foxHidePasswords()") >= 5


# ══ §2.3 / §2.4 · the links ════════════════════════════════════════════════

def test_forgot_password_is_not_smaller_than_the_form(css):
    """It was 10px against a 11.5px form. Someone reading it has already had a
    bad minute."""
    m = re.search(r"font-size:\s*([\d.]+)px", _block(css, ".pw-link"))
    assert m and float(m.group(1)) >= 11.5


def test_forgot_password_comes_before_the_tertiary_links(html):
    """Hierarchy, not just size: it must not be buried between "Book a demo" and
    an SSO link of equal weight."""
    gate = _gate(html)
    assert gate.index('id="forgotLink"') < gate.index('id="ssoLink"')
    assert gate.index('id="forgotLink"') < gate.index("Book a demo")


def test_forgot_password_carries_more_ink_than_the_tertiary_links(themes, css):
    fg = re.search(r"color:\s*var\(--([a-z0-9-]+)\)", _block(css, ".pw-link")).group(1)
    sm = re.search(r"color:\s*var\(--([a-z0-9-]+)\)", _block(css, ".pw-link-sm")).group(1)
    for theme in ("dark", "light"):
        t = themes[theme]
        assert ratio(t[fg], t["surf"]) > ratio(t[sm], t["surf"])


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("cls", [".pw-link", ".pw-link-sm"])
def test_every_auth_link_clears_aa(themes, css, theme, cls):
    """§2.4 — the SSO and demo links measured too, not just eyeballed."""
    token = re.search(r"color:\s*var\(--([a-z0-9-]+)\)", _block(css, cls)).group(1)
    t = themes[theme]
    got = ratio(t[token], t["surf"])
    assert got >= AA_BODY, f"[{theme}] {cls} measures {got:.2f}:1 on the card"


def test_the_links_are_focusable_controls_not_clickable_divs(html):
    """They do something when clicked, so they must be reachable by keyboard."""
    gate = _gate(html)
    for el in ('id="forgotLink"', 'id="ssoLink"'):
        m = re.search(r"<(\w+)[^>]*" + re.escape(el), gate)
        assert m and m.group(1) == "button", f"{el} is a <{m.group(1) if m else '?'}>, not a button"


# ══ labels ═════════════════════════════════════════════════════════════════

def test_the_gate_fields_have_associated_labels(html):
    """A .flabel div sitting above an input looks like a label but is not one —
    nothing connects them, so a screen reader reads an unlabelled field."""
    gate = _gate(html)
    for field in ("authEmail", "authPass", "resetPass"):
        assert re.search(r'<label class="flabel"[^>]*for="%s"' % field, gate), \
            f"{field} has no <label for>"
