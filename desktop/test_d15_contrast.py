"""D15a — the WCAG 2.1 contrast audit the 698 per-page tests cannot do.

Every other desktop test asks "does this one page behave?". None of them asks
the cross-cutting question: is the text actually readable? A token could drift
two shades darker and every existing test would stay green.

So this file measures. It computes WCAG 2.1 relative luminance and contrast
ratio from `foxy_tokens` itself — never from a hard-coded copy of the hex —
and holds every foreground/surface pair the app paints for BODY TEXT to the
4.5:1 AA bar, plus each status pill's own text-on-its-own-background pair.

Two things to know before editing this file.

**The maths is self-tested.** `test_the_maths_is_right` pins black-on-white at
exactly 21.0 and white-on-white at exactly 1.0. A broken luminance formula
tends to pass *everything*, which is the worst way for an audit to fail, so
the formula is checked before it is trusted.

**`WEB` is a byte-for-byte port of the site's `:root` and the web wins.** When
a pair here fails, the fix does NOT go in `foxy_tokens.WEB` — hand-tuning the
desktop copy forks the two looks and breaks the "no theme changes" decision.
The site's `:root` changes first and gets re-ported. That is why the pairs
below that sit under the bar are recorded as EXEMPT with a reason rather than
quietly dropped from the list: a hidden failure and a passing audit are the
same thing.
"""

from __future__ import annotations

import re

import pytest

from foxy_tokens import (
    BAD_RED, DARK_TX, INFO_BLUE, OK_GREEN, WARN_AMBER, WEB, clay_tokens,
    console_shell_qss, is_dark,
)


@pytest.fixture(scope="module")
def app():
    """Most of this file is pure arithmetic over the token dict and needs no
    screen. The two tests that build something Qt-side ask for this."""
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


# ══ WCAG 2.1 relative luminance and contrast ratio ══════════════════════════
def _channel(value: int) -> float:
    """One sRGB channel, 0-255, linearised per WCAG 2.1 §relative luminance."""
    srgb = value / 255
    return srgb / 12.92 if srgb <= 0.04045 else ((srgb + 0.055) / 1.055) ** 2.4


def luminance(colour: str) -> float:
    """Relative luminance of an #RGB or #RRGGBB colour, 0.0 (black) - 1.0."""
    hexpart = colour.strip().lstrip("#")
    if len(hexpart) == 3:
        hexpart = "".join(c * 2 for c in hexpart)
    if len(hexpart) != 6:
        raise ValueError(f"not a hex colour: {colour!r}")
    r, g, b = (int(hexpart[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def ratio(fg: str, bg: str) -> float:
    """WCAG 2.1 contrast ratio, 1.0 (identical) - 21.0 (black on white)."""
    a, b = luminance(fg), luminance(bg)
    lighter, darker = max(a, b), min(a, b)
    return (lighter + 0.05) / (darker + 0.05)


AA_BODY = 4.5          # WCAG 1.4.3 AA, normal-size text
AA_LARGE = 3.0         # WCAG 1.4.3 AA, >=18.66px bold / >=24px


def test_the_maths_is_right():
    """Pinned first, because a broken formula passes everything.

    Both extremes plus the canonical AA boundary colour: #767676 on white is
    the standard worked example of a pair that only just clears 4.5:1."""
    assert ratio("#000000", "#ffffff") == pytest.approx(21.0, abs=1e-9)
    assert ratio("#ffffff", "#ffffff") == pytest.approx(1.0, abs=1e-9)
    assert ratio("#000", "#fff") == pytest.approx(21.0, abs=1e-9)   # short form
    assert ratio("#767676", "#ffffff") == pytest.approx(4.54, abs=0.01)
    assert luminance("#000000") == 0.0
    assert luminance("#ffffff") == pytest.approx(1.0, abs=1e-9)


def test_the_ratio_is_symmetric():
    """Order must not matter — light-on-dark is the same pair as dark-on-light,
    and the whole desktop palette is light-on-dark."""
    assert ratio(WEB["ink"], WEB["surf"]) == ratio(WEB["surf"], WEB["ink"])


def test_a_non_colour_is_an_error_not_a_silent_zero():
    """A typo'd token must break the audit, not score 1.0 and pass as
    'identical colours'."""
    for junk in ("transparent", "", "#12345", "rgba(255,255,255,34)"):
        with pytest.raises(ValueError):
            luminance(junk)


# ══ body text ═══════════════════════════════════════════════════════════════
# Every foreground/surface pair the app paints for readable, resting body text,
# with the call site that paints it. Anything under the 4.5 bar lives in
# EXEMPT_TEXT below with a reason — never dropped from the audit.
BODY_TEXT = [
    # (fg token, surface token, where)
    ("ink",   "bg",    "console shell body text"),
    ("ink",   "bg2",   "page background body text"),
    ("ink",   "surf",  "card body text (#pageTitle, #kpiValue)"),
    ("ink",   "surf2", "raised card body text"),
    ("ink",   "surf3", "#toggleBtn:hover, row hover"),
    ("ink2",  "bg",    "secondary body text on the shell"),
    ("ink2",  "surf",  "#chainMeta"),
    ("ink2",  "surf2", "secondary text on a raised card"),
    ("ink2",  "surf3", "#toggleBtn resting label"),
    ("muted", "bg",    "#flabel / #hint on the auth card"),
    ("muted", "bg2",   "#sectionCap, #navMeta"),
    ("muted", "surf",  "#emptyState, #tableCount, #chainHash"),
    ("fox",   "bg",    "accent text on the shell"),
    ("fox",   "surf",  "accent text on a card"),
    ("fox2",  "bg",    "#eyebrow"),
    ("fox2",  "surf",  "verify-page link labels (_link)"),
]

# Status colours also carry text directly on a dark surface (a verdict word,
# an inline pass/fail note) — not only inside a filled pill.
STATUS_TEXT_ON_SURFACE = [
    (OK_GREEN,   "surf", "#chainState, #fbOk"),
    (BAD_RED,    "bg",   "#fbBad on the auth card"),
    (BAD_RED,    "surf", "breach verdict text"),
    (WARN_AMBER, "surf", "warning verdict text"),
]


@pytest.mark.parametrize("fg,bg,where", BODY_TEXT,
                         ids=[f"{f}-on-{b}" for f, b, _ in BODY_TEXT])
def test_body_text_clears_aa(fg, bg, where):
    measured = ratio(WEB[fg], WEB[bg])
    assert measured >= AA_BODY, (
        f"{fg} on {bg} is {measured:.2f}:1, under the {AA_BODY} AA bar "
        f"({where}). The fix goes in the web dashboard's :root first and is "
        f"re-ported here — do not hand-tune foxy_tokens.WEB.")


@pytest.mark.parametrize("fg,bg,where", STATUS_TEXT_ON_SURFACE,
                         ids=[w for _, _, w in STATUS_TEXT_ON_SURFACE])
def test_status_colours_are_readable_as_text(fg, bg, where):
    measured = ratio(fg, WEB[bg])
    assert measured >= AA_BODY, f"{where} is {measured:.2f}:1"


# ══ status pills ════════════════════════════════════════════════════════════
# The web's rule (foxy_tokens.py:65) is that a status background is coloured
# and its text is always pure-black-ish or white. Each pill is judged on its
# OWN pair — the surface it sits on is irrelevant once it is filled.
PILLS = [
    ("safe_tx",   "safe_bg",   "clean / verified"),
    ("breach_tx", "breach_bg", "breach"),
    ("warn_tx",   "warn_bg",   "warning"),
]


@pytest.mark.parametrize("tx,bg,name", PILLS, ids=[p[2] for p in PILLS])
def test_each_status_pill_is_readable(tx, bg, name):
    measured = ratio(WEB[tx], WEB[bg])
    assert measured >= AA_BODY, f"the {name} pill is {measured:.2f}:1"


def test_the_pill_ink_picker_never_lands_on_an_unreadable_pair():
    """dashboard.py:156 picks the pill ink by luminance: `#FFFFFF if is_dark(c)
    else DARK_TX`. White on INFO_BLUE would be 3.16:1 — under the bar — so the
    picker is what keeps that from happening, and the picker is what is tested
    rather than the raw pair. Every tone the console can hand it must come back
    readable."""
    for tone in (OK_GREEN, WARN_AMBER, BAD_RED, INFO_BLUE, WEB["fox"],
                 WEB["fox2"], WEB["foxdeep"]):
        ink = "#FFFFFF" if is_dark(tone) else DARK_TX
        measured = ratio(ink, tone)
        assert measured >= AA_BODY, (
            f"the pill ink picker chose {ink} for {tone}: {measured:.2f}:1")


# ══ the muted2 convention ═══════════════════════════════════════════════════
def test_muted2_stays_a_disabled_only_colour():
    """`muted2` is ~2.45:1 on `surf` — far under AA — and that is legitimate
    *only* because it paints `:disabled` states, which WCAG 1.4.3 exempts as
    inactive controls (foxy_tokens.py:483/506/525, policy_page.py:560/577,
    settings_page.py:508, export_page.py:231, settings_admin_page.py:495).

    This pins the convention from both sides. `muted2` must stay UNDER the bar
    and `muted` — its readable neighbour, the one live text is supposed to use
    — must stay over it. The day someone reaches for `muted2` for live text
    believing it is readable, or lightens it until the distinction between the
    two is gone, this is what breaks."""
    disabled = ratio(WEB["muted2"], WEB["surf"])
    live = ratio(WEB["muted"], WEB["surf"])
    assert disabled < AA_BODY, (
        f"muted2 now measures {disabled:.2f}:1 on surf. If it has become a "
        f"readable colour the disabled-state exemption no longer explains it "
        f"— either the token moved or the convention did.")
    assert live >= AA_BODY, (
        f"muted is {live:.2f}:1 on surf — the readable neighbour stopped "
        f"being readable, so there is nothing left to point live text at.")
    assert live > disabled * 1.5, (
        "muted and muted2 have converged; the two roles are no longer "
        "visually distinguishable.")


def test_muted2_is_never_the_only_thing_carrying_a_meaning():
    """Two live (non-disabled) `muted2` marks exist and both are non-text:
    the threats risk-legend DOT (threats_page.py:135) and the chart "mute"
    tone (charts.py:66). Neither is exempt as a disabled control, so what
    makes them acceptable is that neither is text and neither is alone — the
    legend dot's own label is drawn in `muted`, which passes, and it carries
    the same meaning. This pins that pairing: if the label ever drops to
    `muted2` too, the legend becomes an unreadable colour explaining an
    unreadable colour."""
    from threats_page import WEB as tp_web           # same dict, one source
    assert tp_web is WEB
    label = ratio(WEB["muted"], WEB["surf"])
    assert label >= AA_BODY, (
        f"the risk-legend label is {label:.2f}:1 — the dot beside it is "
        f"muted2 at {ratio(WEB['muted2'], WEB['surf']):.2f}:1 and was only "
        f"acceptable while the label carried the meaning readably.")


# ══ non-text tokens, recorded so they are not re-litigated ══════════════════
def test_line_is_a_separator_not_text():
    """`line` measures 1.26:1 on `surf`. That is correct and out of scope:
    WCAG 1.4.3 governs text, and 1.4.11 (non-text contrast, 3:1) governs
    controls and meaningful graphics — a decorative rule between rows is
    neither. Recorded so the next audit does not re-open it, and asserted as
    non-text so that the day someone paints a label in it, this fails."""
    assert ratio(WEB["line"], WEB["surf"]) < AA_LARGE
    from pathlib import Path
    source = (Path(__file__).resolve().parent / "foxy_tokens.py").read_text(
        encoding="utf-8")
    assert "color: {WEB['line']}" not in source, \
        "`line` is the separator colour and is now being used as text"


def test_fox3_is_dead_and_is_the_reason_it_is_not_a_finding(app):
    """`fox3` is 4.48:1 on `surf` — three hundredths under the bar. It is not
    reported as a contrast failure because nothing paints with it: the only
    hop out of the token dict is `clay_tokens()["accent3"]`, which no widget
    reads. If a consumer ever appears, the 4.48 stops being academic — so this
    asserts the deadness rather than the ratio."""
    from foxy_tokens import clay_tokens
    assert ratio(WEB["fox3"], WEB["surf"]) < AA_BODY      # the latent problem
    assert clay_tokens()["accent3"] == WEB["fox3"]        # the only hop

    from pathlib import Path
    desktop = Path(__file__).resolve().parent
    readers = []
    for path in sorted(desktop.glob("*.py")):
        if path.name.startswith("test_") or path.name == "foxy_tokens.py":
            continue
        text = path.read_text(encoding="utf-8")
        if "accent3" in text or "fox3" in text:
            readers.append(path.name)
    assert not readers, (
        f"fox3 is no longer dead ({', '.join(readers)} reads it) and it "
        f"measures {ratio(WEB['fox3'], WEB['surf']):.2f}:1 — under AA. It now "
        f"needs the same web-first fix as any other failing pair.")


# ══ recorded failures — under the bar, NOT fixed here ══════════════════════
# Three live text pairs measure under 4.5:1. None is fixed in this file, for
# two different reasons, and each is pinned at its measured value so that it
# can neither drift further nor be quietly repaired without this audit
# noticing. When one is genuinely fixed the pin below fails — that is the
# signal to delete it from this section, not to widen the tolerance.
def test_the_seg_switcher_label_is_a_faithful_port_of_a_web_miss():
    """The trend-metric switcher paints its resting label in `muted` on the
    track's `surf2`: 4.38:1, three per cent under AA.

    Not fixed, because it is not the desktop's miss. The site does exactly the
    same thing — `.seg button{color:var(--muted)}` over `.seg{background:
    var(--surf2)}` (html:186-187) — so the desktop is a correct port of a web
    bug. Hand-darkening `surf2` or lightening `muted` here would fork the two
    looks for a 0.12 gain. The fix belongs in the site's `:root` and comes
    back by re-port.

    Mitigating, and why it is minor rather than urgent: the *checked* label
    (the one that carries the state) is #1a0900 on `fox` at 7.46:1, hovering
    lifts the resting label to `ink`, and the control is a redundant view
    switcher, not the only route to the data."""
    resting = ratio(WEB["muted"], WEB["surf2"])
    assert 4.2 <= resting < AA_BODY, (
        f"#segBtn's resting label now measures {resting:.2f}:1 — it was 4.38. "
        f"If it now clears {AA_BODY} the web was fixed and re-ported: delete "
        f"this pin. If it dropped further, the port drifted.")
    checked = ratio("#1a0900", WEB["fox"])
    assert checked >= AA_BODY, (
        f"the checked label is {checked:.2f}:1 — it was what made the resting "
        f"miss tolerable, and it no longer holds.")


def test_the_primary_cta_ink_clears_aa_in_every_state():
    """`#ctaBtn` and `#verifyBtn` — "Export passport", "Check ledger", "Verify"
    — used to paint #ffffff on #c96a2f: 3.76:1 resting, 3.27:1 hovered. A
    genuine AA failure on the most-clicked controls in the console.

    Not a web port, which is why it could not be deferred to the site: #c96a2f
    appears nowhere in the dashboard's HTML — it is this app's own matte
    accent. The web's `.btn.pri` is `background:var(--fox); color:#1a0900` at
    7.46:1, so taking that ink moved the desktop TOWARD the web, not away from
    it, and the "no theme changes" rule argued for the fix rather than against.

    Pressed needed moving too: #a8551f is 3.68:1 under dark ink, so the fix
    would have traded a resting failure for a pressed one. #c06529 is the
    deepest shade that still clears AA while reading as darker than resting.

    Reads the QSS rather than two literals. The version this replaces asserted
    `ratio("#ffffff", "#c96a2f") < 4.5`, which is arithmetic about two
    constants — true forever, regardless of what the app paints. It claimed to
    notice the fix and could not have."""
    qss = console_shell_qss(clay_tokens())

    def declared(selector: str, prop: str) -> str:
        block = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", qss)
        assert block, f"{selector} is gone from the console stylesheet"
        found = re.search(prop + r"\s*:\s*(#[0-9a-fA-F]{3,6})", block.group(1))
        assert found, f"{selector} no longer declares {prop}"
        return found.group(1)

    for selector in ("QPushButton#ctaBtn", "QPushButton#verifyBtn"):
        ink = declared(selector, "color")
        for state, bg_selector in ((" resting", selector),
                                   (" hovered", selector + ":hover")):
            bg = declared(bg_selector, "background")
            measured = ratio(ink, bg)
            assert measured >= AA_BODY, (
                f"{selector}{state} paints {ink} on {bg} — {measured:.2f}:1, "
                f"under AA ({AA_BODY}). The web's answer is #1a0900 on an "
                f"orange of this weight.")

    pressed_bg = declared("QPushButton#verifyBtn:pressed", "background")
    pressed = ratio(declared("QPushButton#verifyBtn", "color"), pressed_bg)
    assert pressed >= AA_BODY, (
        f"verifyBtn pressed paints on {pressed_bg} at {pressed:.2f}:1. A "
        f"darker pressed state fails under dark ink just as the light one "
        f"failed under white — it has to clear AA in both directions.")


def test_the_kpi_sub_rule_paints_nothing_and_that_is_why_it_is_not_a_finding():
    """`QLabel#kpiSub` (dashboard.py:204) is styled in `text_muted2` — 2.45:1,
    and a sub-label is not a disabled control, so on its face it is a fourth
    failure.

    It is not, because it renders no text: `KpiTile` is constructed at exactly
    one site with `sub=""` (dashboard.py:1008) and `set_value()` is never
    called with a `sub` argument, so the rule styles an empty label. Recorded
    as a dead rule rather than a contrast bug — but asserted, because the day
    someone starts populating it, a 2.45:1 sub-label ships."""
    from pathlib import Path
    source = Path(__file__).resolve().parent / "dashboard.py"
    text = source.read_text(encoding="utf-8")
    assert 'KpiTile(t, label, value, "", _STAT_ACCENT.get(tone))' in text, (
        "KpiTile's `sub` is no longer hard-empty. #kpiSub is styled in muted2 "
        f"at {ratio(WEB['muted2'], WEB['surf']):.2f}:1 — it is now live text "
        "under AA and needs a real colour.")


# ══ the one sanctioned deviation from the web ══════════════════════════════
def test_the_auth_placeholder_keeps_its_deliberate_upgrade(app):
    """auth_windows.py:95 is the ONE place the desktop deliberately departs
    from the web's colour choice, and it is ungreppable: Qt has no
    `::placeholder` selector, so the colour is a QPalette role set in Python
    rather than a line of QSS anyone would find.

    The web paints `.cin::placeholder` in `--muted2` (html:591), which is
    ~2.7:1 on the field background. A placeholder in a sign-in form is not a
    disabled control — it is the hint telling someone what to type — so the
    desktop paints it in `--muted` instead, which clears AA.

    Without this test the deviation is invisible: delete the two lines and
    every other test still passes while the sign-in hints quietly become
    unreadable. So it is asserted on the built widget, not on the source."""
    from PyQt6.QtGui import QPalette
    from auth_windows import _Field

    field = _Field("Email", placeholder="you@company.com")
    placeholder = field.input.palette().color(QPalette.ColorRole.PlaceholderText)

    assert placeholder.name().lower() == WEB["muted"].lower(), (
        f"the auth placeholder is {placeholder.name()}, not WEB['muted']. If "
        f"it has gone back to the web's muted2 the sign-in hints are ~2.7:1.")

    # #cin paints on WEB['bg'] (auth_windows.py:194) — the pair that matters.
    measured = ratio(placeholder.name(), WEB["bg"])
    assert measured >= AA_BODY, f"auth placeholder is {measured:.2f}:1"
    assert ratio(WEB["muted2"], WEB["bg"]) < AA_BODY, \
        "the deviation exists because the web's choice fails; it no longer does"
    field.deleteLater()
