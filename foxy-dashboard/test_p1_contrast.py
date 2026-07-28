"""P1 §14 — the WCAG 2.1 contrast audit for the dashboard's theme layer.

Soft UI leans on shadow instead of borders, so control-vs-background contrast
is the known weakness of the style, and it matters more on a compliance product
than on a dribbble shot. This file is what stops the weakness from becoming a
defect.

Three things to know before editing it.

**It parses the shipped CSS.** Nothing here is a copy of a hex. The token maps
are read out of `foxy-audit-premium.html` — `:root` for dark,
`html[data-theme="light"]` layered on top for light — and `var()` chains are
resolved the way a browser resolves them. A token can drift two shades and this
notices; a test written against literals never could.

**The maths is self-tested.** `test_the_maths_is_right` pins black-on-white at
21.0 and white-on-white at 1.0 before any pair is judged. A broken luminance
formula tends to pass *everything*, which is the worst way for an audit to
fail.

**Failures under the bar are recorded, never dropped.** `--muted2` is below 4.5
on purpose — it is the disabled-only colour and WCAG 1.4.3 exempts inactive
controls — so it is asserted to STAY below, from both sides. The day someone
lightens it until it looks readable, or points live text at it, this breaks.
That convention is shared with `desktop/test_d15_contrast.py`; keep the two
consistent or the desktop audit starts lying.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import pytest

HTML = Path(__file__).resolve().parent / "foxy-audit-premium.html"
AA_BODY = 4.5           # WCAG 1.4.3 AA, normal-size text
AA_LARGE = 3.0          # WCAG 1.4.3 AA, >=18.66px bold / >=24px; also 1.4.11


# ══ WCAG 2.1 relative luminance and contrast ratio ══════════════════════════
def _channel(value: int) -> float:
    """One sRGB channel, 0-255, linearised per WCAG 2.1 relative luminance."""
    srgb = value / 255
    return srgb / 12.92 if srgb <= 0.04045 else ((srgb + 0.055) / 1.055) ** 2.4


def luminance(colour: str) -> float:
    """Relative luminance of an #RGB or #RRGGBB colour, 0.0 (black) - 1.0."""
    hexpart = colour.strip().lstrip("#")
    if len(hexpart) == 3:
        hexpart = "".join(c * 2 for c in hexpart)
    if len(hexpart) != 6 or not re.fullmatch(r"[0-9a-fA-F]{6}", hexpart):
        raise ValueError(f"not an opaque hex colour: {colour!r}")
    r, g, b = (int(hexpart[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def ratio(fg: str, bg: str) -> float:
    """WCAG 2.1 contrast ratio, 1.0 (identical) - 21.0 (black on white)."""
    a, b = luminance(fg), luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


# ══ reading the shipped stylesheet ══════════════════════════════════════════
@lru_cache(maxsize=1)
def _source() -> str:
    return HTML.read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def _stylesheet() -> str:
    """Every <style> block, with comments stripped.

    The comments have to go before anything is parsed: a declaration block that
    opens with a prose comment containing a colon makes a naive `name: value`
    reader hand back the comment as the property name, and every pair judged
    against it then passes vacuously."""
    blocks = re.findall(r"<style>(.*?)</style>", _source(), re.S)
    assert blocks, "no <style> block in the dashboard"
    return re.sub(r"/\*.*?\*/", "", "\n".join(blocks), flags=re.S)


def _block(css: str, selector: str) -> str:
    """The declaration body of the first rule matching `selector` exactly."""
    pattern = re.compile(
        r"(?:^|[\n}])\s*" + re.escape(selector) + r"\s*\{([^{}]*)\}", re.S)
    found = pattern.search(css)
    if not found:
        raise AssertionError(f"no rule for {selector!r} in the shipped CSS")
    return found.group(1)


def _declarations(body: str) -> dict[str, str]:
    out = {}
    for line in body.split(";"):
        if ":" not in line:
            continue
        name, _, value = line.partition(":")
        out[name.strip()] = value.split("/*")[0].strip()
    return out


@pytest.fixture(scope="module")
def css() -> str:
    return _stylesheet()


@pytest.fixture(scope="module")
def themes(css) -> dict[str, dict[str, str]]:
    """{'dark': {...}, 'light': {...}} — every custom property, var() resolved.

    Light is `:root` with the `html[data-theme="light"]` block layered on top,
    which is exactly the cascade the browser applies."""
    root = _declarations(_block(css, ":root"))
    light_over = _declarations(_block(css, 'html[data-theme="light"]'))

    def resolve(raw: dict[str, str]) -> dict[str, str]:
        tokens = {k: v for k, v in raw.items() if k.startswith("--")}
        for _ in range(12):                       # var() chains are shallow
            changed = False
            for key, value in list(tokens.items()):
                hit = re.fullmatch(r"var\((--[\w-]+)\)", value.strip())
                if hit and hit.group(1) in tokens:
                    tokens[key] = tokens[hit.group(1)]
                    changed = True
            if not changed:
                break
        return {k[2:]: v for k, v in tokens.items()}

    dark = resolve(root)
    light = resolve({**root, **light_over})
    return {"dark": dark, "light": light}


# ══ the maths, before it is trusted ═════════════════════════════════════════
def test_the_maths_is_right():
    """#767676 on white is the standard worked example of a pair that only
    just clears 4.5:1."""
    assert ratio("#000000", "#ffffff") == pytest.approx(21.0, abs=1e-9)
    assert ratio("#ffffff", "#ffffff") == pytest.approx(1.0, abs=1e-9)
    assert ratio("#000", "#fff") == pytest.approx(21.0, abs=1e-9)
    assert ratio("#767676", "#ffffff") == pytest.approx(4.54, abs=0.01)
    for junk in ("transparent", "", "#12345", "rgba(255,255,255,.4)"):
        with pytest.raises(ValueError):
            luminance(junk)


def test_the_parser_reads_the_real_stylesheet(themes):
    """If the token extraction silently returned {} every pair below would
    vacuously pass, so the shape of what was parsed is pinned first."""
    for theme in ("dark", "light"):
        tokens = themes[theme]
        for required in ("bg", "surf", "ink", "muted", "muted2", "fox2",
                         "lv-low", "lv-med", "lv-high", "lv-ultra",
                         "safe-ink", "warn-ink", "breach-ink"):
            assert required in tokens, f"{theme} lost --{required}"
            assert re.fullmatch(r"#[0-9a-fA-F]{3,6}", tokens[required]), (
                f"{theme} --{required} is {tokens[required]!r}, not a hex")
    assert themes["dark"]["bg"] != themes["light"]["bg"], \
        "both themes resolved to the same background — the cascade is not being applied"


# ══ body text ═══════════════════════════════════════════════════════════════
# Every foreground/surface pair the dashboard paints for readable, resting body
# text. Anything under the bar is recorded below with a reason, never dropped.
BODY_TEXT = [
    ("ink",    "bg",    ".h1 / body copy on the page"),
    ("ink",    "bg2",   "text on the second background"),
    ("ink",    "surf",  ".clay card body text"),
    ("ink",    "surf2", "raised card body text"),
    ("ink",    "surf3", "row hover, .btn.ghost label"),
    ("ink2",   "bg",    "secondary copy on the page"),
    ("ink2",   "surf",  ".empty-t, .seg-chip"),
    ("ink2",   "surf2", "secondary text on a raised card"),
    ("ink2",   "surf3", ".mask-btn resting"),
    ("muted",  "bg",    ".plbl, .flabel on the page"),
    ("muted",  "bg2",   ".topuser-role"),
    ("muted",  "surf",  ".sub, .tool-desc, .lhash, .empty-d"),
    ("muted",  "surf2", ".dtbl .mono on a hovered row"),
    ("fox2",   "bg",    ".eyebrow"),
    ("fox2",   "surf",  ".gauge-row b, .codebox, .notif-head a"),
    ("safe-ink",   "surf", ".eval.ok, .fb-res.ok"),
    ("warn-ink",   "surf", ".eval.unknown"),
    ("breach-ink", "surf", ".fb-res.bad, .usermenu-item.danger, .stat .v.danger"),
    ("lv-low",   "surf", "judge sensitivity — Low"),
    ("lv-med",   "surf", "judge sensitivity — Medium"),
    ("lv-high",  "surf", "judge sensitivity — High"),
    ("lv-ultra", "surf", "judge sensitivity — Ultra"),
]


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("fg,bg,where", BODY_TEXT,
                         ids=[f"{f}-on-{b}" for f, b, _ in BODY_TEXT])
def test_body_text_clears_aa(themes, theme, fg, bg, where):
    tokens = themes[theme]
    measured = ratio(tokens[fg], tokens[bg])
    assert measured >= AA_BODY, (
        f"[{theme}] --{fg} on --{bg} is {measured:.2f}:1, under the {AA_BODY} "
        f"AA bar ({where}). Do not hand-tune one neutral to fix this — "
        f"regenerate the whole ramp at one saturation.")


# ══ status pills — judged on their own pair, not on what they sit on ════════
PILLS = [("safe-tx", "safe-bg", "clean / verified"),
         ("breach-tx", "breach-bg", "breach"),
         ("warn-tx", "warn-bg", "warning")]


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("tx,bg,name", PILLS, ids=[p[2] for p in PILLS])
def test_each_status_pill_is_readable(themes, theme, tx, bg, name):
    measured = ratio(themes[theme][tx], themes[theme][bg])
    assert measured >= AA_BODY, \
        f"[{theme}] the {name} pill is {measured:.2f}:1"


def test_a_fill_colour_is_not_a_text_colour(themes):
    """The reason --safe-ink exists at all. #16A34A is a perfectly good pill
    fill and a 2.90:1 word on the light surface; the two jobs need two tokens.
    This pins the distinction so nobody "simplifies" them back together."""
    light = themes["light"]
    assert ratio(light["safe-bg"], light["surf"]) < AA_BODY, (
        "--safe-bg now clears AA as text on the light surface. If that is "
        "real, --safe-ink has lost its reason to exist; if it is not, a fill "
        "token has been quietly repurposed.")
    assert ratio(light["safe-ink"], light["surf"]) >= AA_BODY


# ══ the muted2 convention, pinned from both sides ═══════════════════════════
@pytest.mark.parametrize("theme", ["dark", "light"])
def test_muted2_stays_a_disabled_only_colour(themes, theme):
    """`muted2` is under AA on purpose — it paints `:disabled` states, which
    WCAG 1.4.3 exempts as inactive controls. It no longer paints
    `.cin::placeholder`: P3 §2.1 moved that to `muted` because the owner could
    not read the sign-in fields, and an input awaiting your typing is not an
    inactive control. See test_login_card.py, which measures it.

    This pins the convention from both sides: muted2 must stay UNDER the bar and
    `muted`, its readable neighbour, must stay over it. The day someone reaches
    for muted2 for live text believing it is readable, or lightens it until the
    two roles are indistinguishable, this is what breaks.

    Shared with desktop/test_d15_contrast.py — keep both consistent."""
    tokens = themes[theme]
    disabled = ratio(tokens["muted2"], tokens["surf"])
    live = ratio(tokens["muted"], tokens["surf"])
    assert disabled < AA_BODY, (
        f"[{theme}] muted2 now measures {disabled:.2f}:1 on surf. If it has "
        f"become readable the disabled-state exemption no longer explains it.")
    assert live >= AA_BODY, (
        f"[{theme}] muted is {live:.2f}:1 on surf — the readable neighbour "
        f"stopped being readable, so there is nothing to point live text at.")
    assert live > disabled * 1.5, (
        f"[{theme}] muted and muted2 have converged; the two roles are no "
        f"longer visually distinguishable.")


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_line_is_a_separator_not_text(themes, theme):
    """`--line` is a rule between rows. WCAG 1.4.3 governs text and 1.4.11
    governs controls and meaningful graphics — a decorative divider is neither.
    Recorded so the next audit does not re-open it, and asserted as non-text so
    that the day someone paints a label in it, this fails."""
    assert ratio(themes[theme]["line"], themes[theme]["surf"]) < AA_LARGE


# ══ what the CSS actually paints, not just what the tokens are ══════════════
def test_the_decorative_faces_carry_ink_that_clears_aa(css, themes):
    """§9's decorative KPI faces are gradients, so the LIGHT stop is the pair
    that decides. White on the light end of these measures 2.35-2.74:1, which
    is why the faces carry dark ink instead. The stops are read out of the
    stylesheet so a re-tune cannot quietly break the label.
    Every decorative gradient in the sheet is judged, however many there are.
    Pinning a count made this fail the moment P2 deleted the two hero tiles —
    a true change to the subject, not a regression — so the rule is now "all of
    them, and there is at least one", which cannot be satisfied by deleting the
    thing under test either."""
    ink = themes["dark"]["foxink"]
    faces = re.findall(
        r"\.(?:k-(?:fox|blue|violet|pink)\s+\.face|htile\.t-(?:blue|pink))"
        r"\{background:linear-gradient\(145deg,(#[0-9a-fA-F]{6}),"
        r"(#[0-9a-fA-F]{6})\);?\}", css)
    assert len(faces) >= 4, (
        f"found only {len(faces)} decorative gradients — the four KPI faces "
        f"are the floor")
    for light_stop, dark_stop in faces:
        for label, stop in (("light stop", light_stop), ("dark stop", dark_stop)):
            measured = ratio(ink, stop)
            assert measured >= AA_BODY, (
                f"a decorative {label} {stop} carries {ink} at {measured:.2f}:1. "
                f"The .l label on that face is 9.5px body text.")


def test_red_amber_and_green_never_become_decorative(css):
    """§9 is a constraint, not a palette. Decorative colour is blue, violet and
    pink ONLY — the moment a decorative card can be red, a breach warning stops
    reading instantly beside four coloured ones, which is the entire reason the
    owner's "colour on the KPI cards" instruction was safe to follow."""
    root = _declarations(_block(css, ":root"))

    def channels(value):
        return [int(value.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]

    # Blue, violet and pink all sit on the blue side of the wheel; red, amber
    # and green all sit off it. That one property separates the two sets
    # cleanly, which is why it is the thing asserted rather than a list of
    # approved hexes — a list would pass any hex someone added to it.
    for token in ("--dec1", "--dec2", "--dec3"):
        r, g, b = channels(root[token])
        assert b > r * 0.6 and b > g * 0.6, (
            f"{token} is {root[token]}: it has left the blue side of the "
            f"wheel, which is where the decorative set lives. Red, amber and "
            f"green are reserved for status.")
    for token in ("--breach-bg", "--warn-bg", "--safe-bg"):
        r, g, b = channels(root[token])
        assert not (b > r * 0.6 and b > g * 0.6), (
            f"{token} now satisfies the decorative test, so the test no "
            f"longer separates the two sets and proves nothing.")


def test_the_primary_button_ink_clears_aa_in_both_stops(css, themes):
    """`.btn.pri` is a gradient, so it has to clear the bar at BOTH ends — a
    fix at one stop that fails at the other is not a fix. Reads the declaration
    rather than asserting two literals.

    The stops come from the `background` declaration alone, not from the whole
    rule. Scanning the rule matched `--fox\\d?` against nothing (so the second
    stop went unchecked entirely), and widening the pattern to `--fox[\\w-]*`
    then swept up `--foxink` from the `color` line and measured the ink against
    itself at 1.00:1. Naming the declaration is what makes it right."""
    decls = _declarations(_block(css, ".btn.pri"))
    stops = re.findall(r"var\((--[\w-]+)\)", decls.get("background", ""))
    assert stops, ".btn.pri no longer paints a token-driven gradient"
    ink_token = re.fullmatch(r"var\((--[\w-]+)\)", decls.get("color", "").strip())
    assert ink_token, ".btn.pri no longer takes its ink from a token"
    for theme in ("dark", "light"):
        tokens = themes[theme]
        ink = tokens[ink_token.group(1)[2:]]
        for stop in stops:
            measured = ratio(ink, tokens[stop[2:]])
            assert measured >= AA_BODY, (
                f"[{theme}] .btn.pri paints {ink} on {tokens[stop[2:]]} "
                f"({stop}) — {measured:.2f}:1.")


def test_every_accent_chip_that_carries_ink_clears_aa(css, themes):
    """The small filled chips — the section-title action, the step marker, the
    segmented control's selected tab — set their own ink on their own fill.
    Each is read from the stylesheet and each is judged on its own pair."""
    chips = [(".stitle-act", "section-title action"),
             (".vstep-n", "verify step marker"),
             ('.seg button[aria-pressed="true"]', "selected segment")]
    for selector, name in chips:
        decls = _declarations(_block(css, selector))
        ink, fill = decls.get("color"), decls.get("background")
        assert ink and fill, f"{name} no longer declares both colour and fill"
        for theme in ("dark", "light"):
            tokens = themes[theme]

            def resolve(value):
                hit = re.fullmatch(r"var\((--[\w-]+)\)", value.strip())
                return tokens[hit.group(1)[2:]] if hit else value

            measured = ratio(resolve(ink), resolve(fill))
            assert measured >= AA_BODY, (
                f"[{theme}] {name} ({selector}) paints {resolve(ink)} on "
                f"{resolve(fill)} — {measured:.2f}:1.")


# ══ the structural promises P1 made ═════════════════════════════════════════
def test_the_skin_axis_is_gone(css):
    """§2 collapsed 2 themes x 3 skins into 2 themes. `data-skin` surviving
    anywhere but the comment explaining its removal means a combination came
    back."""
    live = [line for line in css.splitlines()
            if "data-skin" in line and not line.strip().startswith(("/*", "*", "Was:"))]
    assert not live, f"data-skin is still live in {len(live)} place(s): {live[:3]}"
    assert "foxy_dash_skin" not in css, "the skin persistence key survived"
    assert "backdrop-filter" not in css, "the glass skin's blur survived"


def test_depth_is_never_the_only_signal_of_focus(css):
    """Soft UI's known weakness: it carries state in shadow, and a shadow
    change is not a focus indicator. Every interactive class P1 touched has to
    declare a real outline on :focus-visible."""
    for selector in (".btn:focus-visible", ".topbtn:focus-visible",
                     ".stat.kpi:focus-visible", ".lvseg button:focus-visible",
                     ".seg button:focus-visible"):
        decls = _declarations(_block(css, selector))
        assert "outline" in decls and decls["outline"] != "none", (
            f"{selector} has no visible outline — under soft UI a box-shadow "
            f"change alone cannot be the focus indicator.")


def test_every_animation_stops_under_reduced_motion(css):
    """§13: nothing loops decoratively, and everything that does move is behind
    prefers-reduced-motion. Collect every @keyframes the file defines and every
    name the reduced-motion block neutralises."""
    defined = set(re.findall(r"@keyframes\s+([\w-]+)", css))
    block = re.search(
        r"@media\(prefers-reduced-motion:reduce\)\{(.*?)\n\}", css, re.S)
    assert block, "the reduced-motion block is gone"
    body = block.group(1)
    # every infinite animation is the dangerous kind: it never stops on its own
    looping = set()
    for name in defined:
        if re.search(r"animation:[^;}]*\b" + re.escape(name) + r"\b[^;}]*infinite", css):
            looping.add(name)
    for name in sorted(looping):
        selectors = re.findall(
            r"([^{}\n]+)\{[^{}]*animation:[^;}]*\b" + re.escape(name)
            + r"\b[^;}]*infinite", css)
        assert any(sel.strip().split()[-1].lstrip(".#") in body
                   or sel.strip() in body for sel in selectors), (
            f"@keyframes {name} loops forever and nothing in the "
            f"reduced-motion block stops it (painted by {selectors}).")


def test_charts_never_scale_x_and_y_independently(css):
    """§12. preserveAspectRatio="none" distorts stroke widths and squashes the
    curve — it is what made the first mockup's chart look wrong.

    Matching the markup form alone was not enough: every chart in this file is
    generated by `S()`, which builds attributes from an object literal, so
    `preserveAspectRatio:'none'` in the engine sailed past a check that only
    looked for `preserveAspectRatio="none"`. This matches the attribute and the
    object key, quoted either way."""
    hits = re.findall(r"preserveAspectRatio\s*[:=]\s*['\"]?\s*none",
                      _source(), re.I)
    assert not hits, (
        f"{len(hits)} chart(s) scale x and y independently: {hits[:2]}")


def test_no_placeholder_data_reached_the_markup():
    """The standing rule: honest empty states, in code and in UI. The KPI
    faces P1 added ship with a dash or a zero, never an invented number."""
    source = _source()
    kpis = re.search(r'<div class="stats" id="homeKpis">(.*?)</div>\s*\n\s*\n',
                     source, re.S)
    assert kpis, "the home KPI row is gone"
    for value in re.findall(r'<div class="v"[^>]*>([^<]*)</div>', kpis.group(1)):
        assert value.strip() in ("0", "—", ""), (
            f"the home KPI row ships {value!r} as a starting value — that is "
            f"placeholder data, not an honest empty state.")
