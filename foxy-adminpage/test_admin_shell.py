"""Static guards for the staff ops console (foxy-adminpage/index.html).

The console is one 3.4k-line file with two inline <script> blocks and a single
<style> block, so nothing here is caught by a compiler. These are the checks
that a merge gate can actually run: shape, not behaviour.

Modelled on foxy-dashboard/test_p1_contrast.py. P1 added the first four groups;
P2 adds the shell. Each later phase of docs/plans/admin-console-punchlist.md
extends this file.

    python -m pytest foxy-adminpage/test_admin_shell.py -q
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HTML = Path(__file__).with_name("index.html")
SRC = HTML.read_text(encoding="utf-8")


# ── helpers ──────────────────────────────────────────────────────────────────

def _style_block() -> str:
    start = SRC.index("<style>", SRC.index("</style>"))  # the 2nd <style> — the 1st is @font-face
    return SRC[start + len("<style>") : SRC.index("</style>", start)]


def _script_blocks() -> list[str]:
    """Every inline <script> with a body (the boot shim + the app)."""
    return [
        m.group(1)
        for m in re.finditer(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", SRC, re.S)
        if m.group(1).strip()
    ]


def _defined_tokens() -> set[str]:
    """Custom properties declared anywhere: palette blocks, inline style="", JS."""
    return set(re.findall(r"(--[a-z0-9-]+)\s*:", SRC)) | set(
        re.findall(r"setProperty\(['\"](--[a-z0-9-]+)", SRC)
    )


def _markup() -> str:
    """SRC with <style>, <script> and HTML comments removed.

    Guards that look for an ELEMENT must look here, not in SRC. This file
    explains its own markup in prose, so a comment reading "no <h1> anywhere"
    is itself an `<h1>` as far as a substring search is concerned — which has
    now tripped a guard twice.
    """
    out = re.sub(r"<style[^>]*>.*?</style>", "", SRC, flags=re.S)
    out = re.sub(r"<script[^>]*>.*?</script>", "", out, flags=re.S)
    return re.sub(r"<!--.*?-->", "", out, flags=re.S)


def _blocks(open_tag: str) -> list[str]:
    """Every `open_tag` … matching `</div>` span, sliced by div balance."""
    out, start = [], SRC.find(open_tag)
    while start != -1:
        depth, i = 0, start
        while i < len(SRC):
            if SRC.startswith("<div", i):
                depth += 1
                i += 4
            elif SRC.startswith("</div>", i):
                depth -= 1
                i += 6
                if depth == 0:
                    break
            else:
                i += 1
        out.append(SRC[start:i])
        start = SRC.find(open_tag, i)
    return out


# ── 1. the skin axis is gone (P1) ────────────────────────────────────────────
# P1 collapsed original/glass/clay to one soft-UI skin. A survivor is not
# cosmetic: an html[data-skin=…] rule that never matches silently drops
# whichever tokens it was overriding.

SKIN_STRINGS = ["data-skin", "dataset.skin", "foxy_admin_skin", "setSkin", "toggleSkin"]


@pytest.mark.parametrize("needle", SKIN_STRINGS)
def test_no_skin_axis_survives(needle: str) -> None:
    assert needle not in SRC, f"{needle!r} still in index.html — the skin axis must be fully gone"


def test_no_glass_or_grain_tokens() -> None:
    """The refraction lens, the frost tiers and the film grain went with the skins."""
    for token in ("--refract", "--glass", "--glass-sm", "--glass-data", "--glass-tint",
                  "--spec", "--spec-soft", "--grain"):
        assert f"var({token})" not in SRC, f"var({token}) is still read but no longer defined"
    assert "foxGlassRefract" not in SRC, "the SVG refraction filter is still in the document"


# ── 2. every token read at runtime is defined (P1) ───────────────────────────
# The trap this file exists for. _cssvar()/_chartPalette() and ~40 inline
# style="--k:var(--teal)" attributes resolve tokens BY NAME at paint time. A
# rename compiles perfectly and blanks every chart, so the names are a contract.

RUNTIME_TOKENS = [
    # read by _chartPalette() / _cssvar(), in its CVD-validated order
    "--fox", "--blue", "--ok", "--violet", "--teal", "--warnc",
    # per-KPI accent + status colours passed through inline style=""
    "--blue2", "--breach-bg", "--danger", "--fox2", "--info-bg", "--ink",
    "--line", "--mono", "--muted", "--muted2", "--safe-bg", "--surf2",
    "--warn-bg", "--warn-soft", "--gate-overlay",
    # the console's focus/accent trio
    "--accent", "--accent-soft", "--accent-ink",
]


@pytest.mark.parametrize("token", RUNTIME_TOKENS)
def test_runtime_token_is_defined(token: str) -> None:
    assert token in _defined_tokens(), f"{token} is read at runtime but never declared"


def test_every_var_reference_resolves() -> None:
    """No var(--x) anywhere in the file without a matching declaration."""
    used = set(re.findall(r"var\((--[a-z0-9-]+)", SRC))
    missing = sorted(used - _defined_tokens())
    assert not missing, f"var() references with no declaration: {missing}"


def test_both_themes_declare_the_full_chart_palette() -> None:
    """Light must re-point the chart hues; the decorative fills are too pale on paper."""
    light = SRC[SRC.index('html[data-theme="light"]{') :]
    light = light[: light.index("}")]
    for token in ("--blue", "--blue2", "--violet", "--teal", "--ok", "--warnc", "--danger"):
        assert f"{token}:" in light, f"{token} is not re-pointed for the light theme"


# ── 3. the <style> block is structurally intact (P1) ─────────────────────────

def test_style_braces_balance() -> None:
    css = _style_block()
    opens, closes = css.count("{"), css.count("}")
    assert opens == closes, f"<style> braces unbalanced: {opens} {{ vs {closes} }}"


def test_single_theme_axis() -> None:
    """Two palettes, one axis: :root is dark, html[data-theme="light"] overrides it.

    A palette block is one that redefines --bg. Component rules keyed on a theme
    (color-scheme, the dark badge ring) are not palettes and do not count.
    """
    css = _style_block()
    palettes = [
        m.group(1).strip().splitlines()[-1].strip()   # drop any leading comment
        for m in re.finditer(r"(^[^{}\n][^{}]*)\{[^{}]*--bg\s*:", css, re.M)
    ]
    assert palettes == [":root", 'html[data-theme="light"]'], (
        f"expected exactly the two palette blocks, got: {palettes}"
    )


# ── 4. both inline <script> blocks parse (P1) ────────────────────────────────

def test_two_inline_scripts() -> None:
    assert len(_script_blocks()) == 2, "expected the boot shim + the app script"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
@pytest.mark.parametrize("index", [0, 1])
def test_inline_script_parses(index: int, tmp_path: Path) -> None:
    js = tmp_path / f"block{index}.js"
    js.write_text(_script_blocks()[index], encoding="utf-8")
    proc = subprocess.run(
        [shutil.which("node"), "--check", str(js)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr


# ── 5. the shell (P2) ────────────────────────────────────────────────────────
# Page identity moved out of the page and into the top bar. The two things that
# can now break silently are a head that grew a title back, and a page id the
# crumb has no name for — which leaves the title of whichever page you left.

def _pagehead_blocks() -> list[str]:
    return _blocks('<div class="pagehead">')


def test_seventeen_pageheads_survive() -> None:
    """The element stays even when empty — P3 hangs the wordmark and bloom on it."""
    assert len(_pagehead_blocks()) == 17


def test_no_title_block_inside_any_pagehead() -> None:
    for block in _pagehead_blocks():
        for banned in ("<h1", 'class="eyebrow"', 'class="sub"'):
            assert banned not in block, f"{banned} is back inside a .pagehead: {block[:120]}"


def test_no_visible_h1_and_no_page_without_an_outline() -> None:
    """P2 removed the last VISIBLE h1 — it repeated the crumb 40px above it — and
    the now-dead rule went with it. That is still the rule. What P2 did not
    intend, and what A1 fixes, is a document with no <h1> at all: no outline, no
    rotor entry, no way past seven bento panels except linear Tab.

    So: an h1 may exist, but only as .sr-only, and it must never come back as
    something with a font rule of its own.
    """
    assert "h1{font-family" not in _style_block(), "the h1 rule outlived its last element"
    for m in re.finditer(r"<h1([^>]*)>", _markup()):
        assert 'class="sr-only"' in m.group(1), (
            "a VISIBLE <h1> is back — the page name is already in the crumb and "
            f"the watermark: <h1{m.group(1)}>"
        )
    assert ".sr-only{position:absolute" in _style_block(), (
        "the sr-only utility went away and took every page outline with it"
    )


def test_pagehead_controls_survive() -> None:
    """The heads that carried working controls still carry them."""
    joined = " ".join(_pagehead_blocks())
    # A4 widened the last needle from `loadCampaigns()` to the call prefix. The
    # guard's claim is that the head still carries a working Refresh control,
    # not that the handler takes no arguments — and it now takes `this`, so
    # busy() can disable it while the reload is in flight. Pinning the arity
    # made this guard fail on a change that strengthened the thing it guards.
    for control in ('id="showDeleted"', 'onclick="orgVerify()"', 'onclick="auditExport()"',
                    'onclick="loadCampaigns('):
        assert control in joined, f"{control} was dropped with its page head"


def test_org360_backlink_survives_outside_the_head() -> None:
    """org360's eyebrow was navigation, not a label."""
    assert 'class="backline"' in SRC
    assert "&larr; organizations" in SRC
    assert "&larr; organizations" not in " ".join(_pagehead_blocks())


def test_org360_id_is_still_shown() -> None:
    """The org uuid is a value operators copy — it moved, it did not go."""
    assert 'id="o3Sub"' in SRC
    assert "$('o3Sub').textContent=d.id;" in SRC


def test_panel_eyebrows_are_untouched() -> None:
    """Only the page heads lost theirs; the in-panel labels are content."""
    assert SRC.count('class="eyebrow"') >= 30


def test_crumb_is_present_and_wired() -> None:
    for part in ('class="crumb"', 'class="crumb-logo"', 'class="crumb-brand"',
                 'id="topbarTitle"', 'class="crumb-dot"'):
        assert part in SRC, f"the crumb is missing {part}"
    # the mark is copied from the dock rather than carrying a second base64 copy
    assert "paintCrumbLogo" in SRC
    assert '<img class="crumb-logo" alt="" aria-hidden="true">' in SRC, (
        "crumb-logo must not carry its own src"
    )


def _ctx_keys() -> set[str]:
    body = SRC[SRC.index("const CTX={") :]
    body = body[: body.index("};")]
    return set(re.findall("([a-z0-9]+):", body))


def _page_ids() -> set[str]:
    return set(re.findall(r'id="page-([a-z0-9]+)"', SRC))


def test_ctx_covers_every_page() -> None:
    missing = sorted(_page_ids() - _ctx_keys())
    assert not missing, f"pages with no crumb title: {missing}"


def test_ctx_has_no_orphans() -> None:
    orphans = sorted(_ctx_keys() - _page_ids())
    assert not orphans, f"CTX names pages that do not exist: {orphans}"


def test_topbar_context_is_called_from_every_entry_point() -> None:
    """go() is not the only way in: sign-in and the org drill-down bypass it."""
    assert SRC.count("setTopbarContext(") >= 6
    for fn in ("function go(", "function enter(", "async function openOrg(",
               "function renderO3Header("):
        i = SRC.index(fn)
        assert "setTopbarContext(" in SRC[i : i + 1400], f"{fn} never sets the crumb title"


def test_static_topbar_brand_is_gone() -> None:
    for dead in ("topbar-brand", "topbar-word", "topbar-sub"):
        assert dead not in SRC, f"{dead} survived the crumb port"


def test_dock_active_state_is_a_press_not_a_bar() -> None:
    css = _style_block()
    block = css[css.index(".dock-item.active{") :]
    block = block[: block.index("}")]
    assert "var(--sink-sm)" in block, "the active rail item must read as pressed in"
    assert "inset 3px 0 0" not in block, "the hard orange accent bar is back"


def test_rail_icons_share_one_language() -> None:
    """13 rail icons, one grid, one weight, round caps — and no per-child override."""
    dock = SRC[SRC.index('<nav class="dock">') : SRC.index("</nav>")]
    svgs = re.findall("<svg [^>]*>", dock)
    assert len(svgs) == 13, f"expected 13 rail icons, found {len(svgs)}"
    for tag in svgs:
        assert 'stroke-width="1.9"' in tag, tag
        assert 'stroke-linecap="round"' in tag, tag
        assert 'stroke-linejoin="round"' in tag, tag
        assert 'viewBox="0 0 24 24"' in tag, tag
    assert ".dock-item svg *{stroke-width" not in _style_block(), (
        "the per-child stroke override outranks each icon's own width"
    )


def test_settings_cards_stack() -> None:
    i = SRC.index('id="page-settings"')
    grid = SRC[i : i + 1600]
    assert "grid-template-columns:minmax(0,1fr)" in grid, "Settings cards are still side by side"


# ── 6. page identity (P3) ────────────────────────────────────────────────────
# The wordmark and the bloom are decoration, but two things about them are not
# cosmetic: the head's overflow:hidden is the only thing stopping a word wider
# than its column from scrolling the whole document sideways (measured: 63px of
# body scroll at 1440 with the clip removed), and the word comes from the same
# CTX map as the crumb, so the two can never name the page differently.

def test_every_pagehead_carries_a_wordmark() -> None:
    blocks = _pagehead_blocks()
    assert len(blocks) == 17
    for block in blocks:
        assert 'class="pg-wm"' in block, f"a page head has no wordmark: {block[:120]}"


def test_wordmark_is_decoration_not_content() -> None:
    """Empty in markup, filled from CTX, and never announced."""
    assert SRC.count('<span class="pg-wm" aria-hidden="true"></span>') == 17


def test_pagehead_clips_and_isolates() -> None:
    css = _style_block()
    block = css[css.index(".pagehead{") :]
    block = block[: block.index("}")]
    for prop in ("position:relative", "overflow:hidden", "isolation:isolate"):
        assert prop in block, f".pagehead lost {prop} — the wordmark can now scroll the page"


def test_wordmark_rule_follows_the_child_rule() -> None:
    """.pagehead>* and .pg-wm have equal specificity, so only source order
    stops the child rule from overriding the wordmark's position and z-index."""
    css = _style_block()
    assert css.index(".pagehead>*{") < css.index(".pg-wm{")


def test_wordmark_is_hidden_on_narrow_screens() -> None:
    """Decoration, not information — the one intentional exception to P6."""
    css = _style_block().replace(" ", "")
    assert "@media(max-width:620px){.pg-wm{display:none}}" in css


def _bloom_alpha(block: str) -> str:
    i = block.index("--bloom:")
    return block[i : block.index(";", i)]


def test_bloom_is_retuned_per_theme_not_ported() -> None:
    """One alpha for both themes is the failure the token exists to prevent:
    what reads as light on near-black reads as a printing smudge on paper."""
    css = _style_block()
    root = css[css.index(":root{") : css.index('html[data-theme="light"]{')]
    light = css[css.index('html[data-theme="light"]{') :]
    light = light[: light.index("}")]   # the light block nests no braces
    dark_decl, light_decl = _bloom_alpha(root), _bloom_alpha(light)
    assert dark_decl != light_decl, "both themes are using the same --bloom"
    # geometry identical, strength different
    assert "60% 80% at 100% 0%" in dark_decl and "60% 80% at 100% 0%" in light_decl


def test_bloom_is_painted_on_the_head() -> None:
    css = _style_block()
    block = css[css.index(".pagehead::before{") :]
    block = block[: block.index("}")]
    assert "background:var(--bloom)" in block
    assert "pointer-events:none" in block


def test_ctx_carries_a_word_for_every_page() -> None:
    """CTX values are [crumb title, wordmark]; both must be present and non-empty."""
    body = SRC[SRC.index("const CTX={") :]
    body = body[: body.index("};")]
    pairs = re.findall("([a-z0-9]+):.'([^']*)','([^']*)'", body)
    assert len(pairs) == 17, f"expected 17 CTX pairs, parsed {len(pairs)}"
    for key, title, word in pairs:
        assert title and word, f"{key} has an empty name"
    words = dict((k, w) for k, _t, w in pairs)
    assert words["overview"] == "Foxy Audit", "home must read Foxy Audit"


def test_wordmarks_are_painted_at_sign_in() -> None:
    assert "function paintWordmarks(" in SRC
    i = SRC.index("function enter(")
    assert "paintWordmarks()" in SRC[i : i + 900], "enter() never fills the wordmarks"


# ── 7. hero cards (P4) ───────────────────────────────────────────────────────
# The trap this group exists for: .kpi::before IS the accent bar, and an element
# has exactly one ::before. The file used to defend that by scoping every
# decorative pseudo .clay:not(.kpi); P1 deleted those with the skins, removing
# the guard and leaving the thing it guarded. A decorative ::before added to
# .clay, .kpi or any shared ancestor blanks all 21 bars and nothing errors.
# Rendered count at the time of writing (headless, computed ::before height over
# the four static rows): kpis=16 bars=16 faces=16 glyphs=16 blank=[].

def _kpi_cards() -> list[str]:
    return _blocks('<div class="clay kpi ')


FACE_CLASSES = ["k-azure", "k-blue", "k-indigo", "k-violet", "k-magenta",
                "k-rose", "k-coral", "k-orange", "k-teal"]


def test_every_kpi_has_a_face_a_beam_and_a_glyph() -> None:
    cards = _kpi_cards()
    assert len(cards) == 21, f"expected 21 KPI cards, found {len(cards)}"
    for c in cards:
        assert c.count('class="face"') == 1, c[:140]
        assert c.count('class="beam"') == 1, c[:140]
        assert c.count('class="gly"') == 1, c[:140]


def test_the_accent_bar_rule_survives() -> None:
    """It is the one ::before on .kpi, it paints, and it is not scaled away."""
    css = _style_block()
    assert ".kpi::before{" in css
    rule = css[css.index(".kpi::before{") :]
    rule = rule[: rule.index("}")]
    assert "content:''" in rule, "the accent bar has no content — it paints nothing"
    assert "height:3px" in rule
    assert "scaleX(1)" in rule, "the bar is scaled to zero and never revealed"


def test_no_other_pseudo_claims_the_kpi_before() -> None:
    """Any decorative ::before on .clay/.kpi or a shared ancestor blanks the bars."""
    # Comments first. This file explains its own selectors in prose, so a comment
    # that says ".kpi::before is the accent bar" was being read AS a rule
    # claiming .kpi::before — the guard failing on its own documentation.
    css = re.sub(r"/\*.*?\*/", "", _style_block(), flags=re.S)
    for sel in re.findall("([^{}]*)::before[^{}]*{", css):
        sel = sel.strip().splitlines()[-1].strip()
        if sel in (".kpi", "*,*", ".eyebrow", ".pagehead", ".tbl.cardify td"):
            continue
        assert ".clay" not in sel and ".kpi" not in sel, (
            f"{sel}::before competes with the KPI accent bar"
        )
    assert css.count(".kpi::before{") == 1


def test_face_and_beam_are_elements_not_pseudos() -> None:
    """The reason the bars survive at all."""
    css = _style_block()
    assert ".kpi .face{" in css
    assert ".kpi .beam{" in css
    for banned in (".kpi::after{", ".kpi .face::before{", ".kpi .face::after{"):
        assert banned not in css, f"{banned} is a pseudo where an element was required"


def test_nine_faces_are_defined_with_both_stops() -> None:
    css = _style_block()
    for k in FACE_CLASSES:
        i = css.index("." + k)
        rule = css[i : css.index("}", i)]
        assert "--k:" in rule and "--k2:" in rule, f"{k} is missing a stop: {rule}"


def test_status_hues_stay_out_of_the_face_palette() -> None:
    """Red, amber and green are reserved for status — the only reason a breach
    pill still reads instantly sitting on top of any of these faces."""
    css = _style_block()
    for k in FACE_CLASSES:
        i = css.index("." + k)
        rule = css[i : css.index("}", i)]
        for banned in ("--safe", "--warn", "--breach", "--danger", "--ok"):
            assert banned not in rule, f"{k} reaches for a status token: {rule}"


def _rows() -> list[list[str]]:
    """The face class of every card, grouped by .grid.kpis row."""
    rows = []
    for row in _blocks('<div class="grid kpis'):
        # the hue, whatever modifier classes follow it (e.g. "k-azure quiet")
        faces = re.findall(r'class="clay kpi (k-[a-z]+)[^"]*"', row)
        if faces:
            rows.append(faces)
    return rows


def test_no_two_faces_alike_in_a_row() -> None:
    """The owner rejected three identical blues on sight. Uniqueness within the
    row outranks the category bias."""
    rows = _rows()
    assert len(rows) >= 4, f"expected the static KPI rows, found {len(rows)}"
    for faces in rows:
        assert len(faces) == len(set(faces)), f"a row repeats a face: {faces}"


def test_every_row_has_a_warm_card() -> None:
    """The warm card is the number that can be bad — the only part of the
    assignment that carries meaning."""
    warm = {"k-orange", "k-coral", "k-rose", "k-magenta"}
    for faces in _rows():
        assert [f for f in faces if f in warm], f"a row has nothing warm in it: {faces}"


def test_the_glyph_sprite_is_complete_and_currentcolor_only() -> None:
    sprite = SRC[SRC.index('<svg width="0" height="0"') :]
    sprite = sprite[: sprite.index("</svg>") + 6]
    ids = set(re.findall('id="(g-[a-z]+)"', sprite))
    # 18 card glyphs + g-info, which round-2 P3 added for the hero (i) button.
    assert len(ids) == 19, f"expected 19 glyphs, found {len(ids)}: {sorted(ids)}"
    used = set(re.findall('href="#(g-[a-z]+)"', SRC))
    assert used <= ids, f"a card points at a glyph that does not exist: {sorted(used - ids)}"
    # currentColor is the point: the glyph inherits the card's ink, so it can
    # never wash out against the face it sits on and it re-themes for free.
    for bad in ("#", "rgb(", "hsl("):
        for attr in ('fill="', 'stroke="'):
            assert attr + bad not in sprite, f"a glyph carries its own colour ({attr}{bad})"


def test_glyphs_inherit_the_card_ink() -> None:
    css = _style_block()
    rule = css[css.index(".kpi .gly{") :]
    rule = rule[: rule.index("}")]
    assert "color:var(--foxink)" in rule


def test_the_label_is_not_dimmed_on_a_face() -> None:
    """The mock ran its label at opacity .82, which drops seven of the nine faces
    under 4.5:1 (indigo 3.89). At full strength the worst face is 4.64:1."""
    css = _style_block()
    i = css.index(".kpi .face .klabel")
    rule = css[i : css.index("}", i)]
    assert "opacity" not in rule, "the label is dimmed again"


def test_chart_series_is_split_from_chip_ink() -> None:
    """Worth Noting #34: --warnc drove both .chip.warn ink and slot 6 of the
    chart palette. Measured with the dataviz validator, the split lifts the light
    palette's worst adjacent pair from delta-E 15.5 to 22.0 (normal vision) and
    clears one of its two chroma failures."""
    assert "--warn-series" in _defined_tokens()
    assert "_cssvar('--warn-series'" in SRC, "the chart palette still reads the chip ink"
    assert "--warnc" in _defined_tokens(), "--warnc is still the chip/annbar ink"
    for block_start in (":root{", 'html[data-theme="light"]{'):
        b = SRC[SRC.index(block_start) :]
        b = b[: b.index("}")]
        assert "--warn-series:" in b, f"{block_start} does not set --warn-series"


def test_chart_palette_keeps_its_cvd_order() -> None:
    """P1 changed only the fallback hexes; the order is still load-bearing."""
    i = SRC.index("function _chartPalette()")
    body = SRC[i : SRC.index("}", i)]
    order = re.findall("_cssvar[(]'(--[a-z-]+)'", body)
    assert order == ["--fox", "--blue", "--ok", "--violet", "--teal", "--warn-series"], order


def test_beam_respects_reduced_motion() -> None:
    css = _style_block()
    i = css.rindex("@media(prefers-reduced-motion:reduce)", 0, css.index(".k-azure"))
    block = css[i : css.index("}", css.index("{", i) + 1) + 1]
    assert ".kpi .beam" in block and "display:none" in block, block


def test_no_invented_numbers_in_a_kpi_card() -> None:
    """Every value comes from a loader. Static markup ships an em dash; the
    org drill-down row is a JS template, so its slots are interpolations."""
    for c in _kpi_cards():
        for m in re.findall('class="kval"[^>]*>([^<]*)<', c):
            m = m.strip()
            interpolated = m.startswith("'+") and m.endswith("+'")
            assert m in ("", "—") or interpolated, (
                f"a KPI card ships a literal value: {m!r}"
            )


# ── 8. pagination (P5) ───────────────────────────────────────────────────────
# The one behaviour worth running rather than reading: foxPager must render
# NOTHING when everything fits on one page. A pager over a single page is
# furniture, and an empty table with an empty pager under it is worse — it
# implies there are other pages to look at.

PAGER_SHIM = """
var _made=[];
function El(tag){
  return {tag:tag, children:[], attrs:{}, textContent:'', className:'', type:'',
    disabled:false, listeners:0,
    appendChild:function(c){ this.children.push(c); return c; },
    setAttribute:function(k,v){ this.attrs[k]=v; },
    addEventListener:function(){ this.listeners++; }};
}
var HOST=El('div');
HOST.textContent='';
global.document={
  createElement:function(t){ var e=El(t); _made.push(e); return e; },
  getElementById:function(){ return HOST; }
};
global.window={};
"""


def _pager_source() -> str:
    """The pager IIFE as it ships, lifted out of the app script."""
    app = _script_blocks()[1]
    start = app.index("(function(){\n  /* which page numbers to show around the current one")
    end = app.index("})();", start) + len("})();")
    return app[start:end]


def _run_pager(total: int, size: int) -> dict:
    """Run the shipped foxPager against a DOM stub and report what it built."""
    probe = (
        PAGER_SHIM
        + _pager_source()
        + f"""
window.foxPager('host',{{page:1,pageSize:{size},total:{total},onPage:function(){{}}}});
console.log(JSON.stringify({{
  children:HOST.children.length,
  nav:HOST.children.length?HOST.children[0].className:null,
  buttons:_made.filter(function(e){{return e.tag==='button';}}).length
}}));
"""
    )
    import tempfile, os
    fd, path = tempfile.mkstemp(suffix=".js")
    os.close(fd)
    try:
        Path(path).write_text(probe, encoding="utf-8")
        proc = subprocess.run([shutil.which("node"), path], capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
        import json
        return json.loads(proc.stdout.strip().splitlines()[-1])
    finally:
        os.unlink(path)


def test_the_pager_component_was_ported_not_rewritten() -> None:
    """foxPager/foxPageSlice come from the Dashboard; the console's own _pager —
    a prev/next pair built as an HTML string — is gone."""
    assert "window.foxPager=function" in SRC
    assert "window.foxPageSlice=function" in SRC
    assert "function _pager(" not in SRC, "the old string-built pager is back"
    assert "_pager(" not in SRC, "something still calls the old pager"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
@pytest.mark.parametrize("total,size", [(0, 25), (1, 25), (24, 25), (25, 25)])
def test_no_pager_when_everything_fits_on_one_page(total: int, size: int) -> None:
    """total <= pageSize renders nothing at all — not an empty nav, nothing."""
    out = _run_pager(total, size)
    assert out["children"] == 0, f"total={total} size={size} still drew {out}"
    assert out["buttons"] == 0


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
@pytest.mark.parametrize("total,size,pages", [(26, 25, 2), (100, 25, 4), (500, 25, 20)])
def test_pager_appears_once_there_is_a_second_page(total: int, size: int, pages: int) -> None:
    out = _run_pager(total, size)
    assert out["children"] == 1 and out["nav"] == "pager", out
    # prev + next + at most seven numbered buttons (the rest collapse to gaps)
    assert 3 <= out["buttons"] <= 9, out


def test_pager_css_is_present() -> None:
    css = _style_block()
    for sel in (".pager{", ".pager-count{", ".pager-nav{", ".pager button{",
                ".pager button[aria-current=\"page\"]{", ".pager-gap{"):
        assert sel in css, f"the pager is missing {sel}"
    # real <button>s, so keyboard and focus come from the platform
    assert ".pager button:focus-visible{" in css


def test_every_pager_host_is_wired_both_ways() -> None:
    """A host div with no foxPager call is dead markup; a call with no host is a
    pager nobody sees. Neither errors at runtime."""
    hosts = set(re.findall('id="([a-zA-Z0-9]+Pager)"', SRC))
    # pgState('orgs',25) has a comma in it, so match the host by name, not by
    # argument position.
    called = set(re.findall("'([a-zA-Z0-9]+Pager)'", SRC))
    assert hosts, "no pager hosts at all"
    assert hosts == called, (
        f"hosts with no call: {sorted(hosts - called)}; "
        f"calls with no host: {sorted(called - hosts)}"
    )


def test_client_side_slices_do_not_invent_query_params() -> None:
    """The seven capped endpoints keep their shapes: no limit/offset was added
    to /organizations, /staff, /anchors, /alerts, /evaluation-campaigns,
    /leads or /security/logins. Changing an /admin/v1/* response is out of
    scope for this phase and needs its own review."""
    for path in ("/admin/v1/organizations'", "/admin/v1/staff'", "/admin/v1/anchors'",
                 "/admin/v1/alerts'", "/admin/v1/evaluation-campaigns'"):
        i = SRC.find("api('" + path)
        assert i != -1, f"{path} call site moved"
        call = SRC[i : i + 160]
        assert "limit=" not in call and "offset=" not in call, (
            f"{path} grew a paging param: {call[:110]}"
        )


def test_the_data_page_warning_survived_the_blurb_sweep() -> None:
    """The one line that must not go: it names the four permanently read-only
    tables and why, which is a real warning about the tamper-evident chain."""
    assert "permanently read-only" in SRC
    assert "tamper-evident chain" in SRC


def test_the_named_blurb_is_gone() -> None:
    """Its card's eyebrow already reads 'local · this browser'."""
    assert "Saved to this browser only" not in SRC


def test_shown_once_secret_warning_is_not_a_blurb() -> None:
    """Kept: the reader cannot get this anywhere else, and getting it wrong
    loses the code."""
    assert "shown once" in SRC


# ── 9. responsive consistency (P6) ───────────────────────────────────────────
# The owner's complaint was that the console shows DIFFERENT CONTENT at
# different widths. The rule this group enforces: nothing that is information
# may be removed by a media query. Measured headless, no horizontal body scroll
# and nothing hidden at 1414 / 998 / 742 / 594 / 494 / 365 — the last inside a
# sized iframe, because Chrome clamps --window-size to ~490px and a naive "375px
# screenshot" is a lie.

def _width_media_blocks() -> list[tuple[str, str]]:
    """(condition, body) for every width-based media query in the stylesheet."""
    css = _style_block()
    out = []
    for m in re.finditer(r"@media\((max|min)-width:(\d+)px\)\s*\{", css):
        depth, i = 1, m.end()
        while i < len(css) and depth:
            if css[i] == "{":
                depth += 1
            elif css[i] == "}":
                depth -= 1
            i += 1
        out.append((f"{m.group(1)}-width:{m.group(2)}px", css[m.end(): i - 1]))
    return out


# Each entry is a deliberate exception with the reason it is allowed. Anything
# else that hides itself by width is the defect this phase exists to remove.
HIDE_ALLOWLIST = {
    ".pg-wm":                 "decoration — a 72px word behind a 44px band, the plan's one exception",
    ".crumb-sep":             "a box-drawing glyph, already aria-hidden; it carries nothing",
    ".tbl.cardify td:empty":  "a cell with no value, which the desktop table also renders blank",
    ".tbl.cardify thead":     "A1 — td::before already prefixes every cell with its column "
                              "name, so the header row is a duplicate of the labels, not a "
                              "lost control. It was visually-hidden by clip, which KEPT it "
                              "in the a11y tree: the columns were announced once as a loose "
                              "run and then again inside every card.",
}


def test_nothing_is_hidden_by_width_except_the_named_exceptions() -> None:
    offenders = []
    for cond, body in _width_media_blocks():
        for m in re.finditer(r"([^{};]+)\{[^{}]*display:none", body):
            sel = m.group(1).strip().splitlines()[-1].strip()
            for one in (x.strip() for x in sel.split(",")):
                if one not in HIDE_ALLOWLIST:
                    offenders.append(f"@media({cond}) {one}")
    assert not offenders, (
        "these disappear at some width — reinstate them in a width-appropriate "
        f"form instead: {offenders}"
    )


def test_the_controls_that_used_to_vanish_are_back() -> None:
    """Below 760px the rail used to drop .dock-mark, .dock-ops and .dock-foot,
    which took the avatar AND the sign-out button with it — a lost control, not
    a lost decoration. .topuser-meta took the signed-in identity."""
    css = _style_block()
    for sel in (".dock-mark,.dock-ops,.dock-foot{display:none}",
                ".topuser-meta{display:none}",
                ".crumb-brand,.crumb-sep,.crumb-logo{display:none}"):
        assert sel not in css.replace(" ", ""), f"{sel} is back"


def test_sign_out_stays_reachable_in_the_bottom_bar() -> None:
    """The bar scrolls horizontally, so .dock-foot is pinned to its trailing
    edge rather than sitting behind thirteen nav items."""
    css = _style_block()
    i = css.index(".dock-foot{", css.index("@media(max-width:760px)"))
    rule = css[i: css.index("}", i)]
    assert "position:sticky" in rule and "right:0" in rule, rule


def test_the_top_bar_wraps_rather_than_dropping_its_right_half() -> None:
    css = _style_block()
    i = css.index(".topbar{", css.index("@media(max-width:760px)"))
    rule = css[i: css.index("}", i)]
    assert "flex-wrap:wrap" in rule, rule


# ── every table stacks, and every stacked cell names its column ──────────────

def _tables() -> list[str]:
    out, cur = [], 0
    while True:
        m = re.search(r'<table class="tbl[^"]*"[^>]*>', SRC[cur:])
        if not m:
            return out
        start = cur + m.start()
        depth, i = 0, start
        while i < len(SRC):
            if SRC.startswith("<table", i):
                depth += 1; i += 6
            elif SRC.startswith("</table>", i):
                depth -= 1; i += 8
                if depth == 0:
                    break
            else:
                i += 1
        out.append(SRC[start:i])
        cur = i


def test_every_table_cardifies() -> None:
    """One table opting in and eighteen scrolling sideways is exactly the
    'different pages behave differently' that was reported."""
    tables = _tables()
    # 18 since round-2 item 9 removed the Settings self-activity card, whose
    # loader built the nineteenth.
    assert len(tables) == 18, f"expected 18 tables, found {len(tables)}"
    missing = [t[:70] for t in tables if "tbl cardify" not in t[:60]]
    assert not missing, f"tables that still scroll sideways instead of stacking: {missing}"


def test_every_labelled_column_labels_its_cells() -> None:
    """A stacked card shows attr(data-label) as the cell's name. A cell with no
    label in a table that has headers is a value with nothing to identify it.

    Cells legitimately without one: an empty-state row (colspan), and an action
    column whose own <th> is blank because the button says what it does.
    """
    bad = []
    for t in _tables():
        heads = [re.sub(r"<[^>]+>", "", h).strip()
                 for h in re.findall(r"<th\b[^>]*>(.*?)</th>", t, re.S)]
        if not heads:
            continue          # the activity feeds have no header row at all
        for row in re.findall(r"<tr\b[^>]*>.*?</tr>", t, re.S):
            if "colspan" in row:
                continue
            for idx, td in enumerate(re.findall(r"<td\b[^>]*>", row)):
                if idx < len(heads) and heads[idx] and "data-label" not in td:
                    bad.append(f"{heads[idx]}: {td[:70]}")
    assert not bad, f"cells with no card label: {bad}"


def test_the_pagers_survived_the_restructure() -> None:
    """P5's hosts sit outside the <table>, so stacking must not have eaten them."""
    hosts = set(re.findall('id="([a-zA-Z0-9]+Pager)"', SRC))
    assert len(hosts) >= 14, f"pager hosts lost in the restructure: {sorted(hosts)}"
    for t in _tables():
        assert "Pager" not in t, "a pager host ended up inside a <table>"


def test_the_wordmark_clip_is_still_load_bearing() -> None:
    """P3 measured it at 64/43/44/30px of horizontal body scroll without it."""
    css = _style_block()
    block = css[css.index(".pagehead{"):]
    block = block[: block.index("}")]
    assert "overflow:hidden" in block


# ── the change-password modal (round 2 · P2) ─────────────────────────────────
# The lockout guard. A password manager only learns a new credential from a form
# submission or an explicit navigator.credentials.store. The modal used to be two
# bare inputs and an onclick, which fires neither — so the manager kept re-filling
# the OLD password at the gate and locked staff out of the console with a password
# they had just changed. Everything below holds that shape in place.

def _js_func(name: str) -> str:
    """The source of `function name(…){…}`, sliced by brace balance."""
    m = re.search(r"(?:async\s+)?function\s+" + re.escape(name) + r"\s*\(", SRC)
    assert m, f"{name}() is gone"
    i = SRC.index("{", m.end() - 1)
    depth, j = 0, i
    while j < len(SRC):
        if SRC[j] == "{":
            depth += 1
        elif SRC[j] == "}":
            depth -= 1
            if depth == 0:
                return SRC[i: j + 1]
        j += 1
    raise AssertionError(f"{name}() never closes")


def _pw_modal_body() -> str:
    return _js_func("pwModal")


def test_the_change_password_modal_body_is_a_form() -> None:
    """Not a <div> with an onclick. This is the whole point of the phase."""
    body = _pw_modal_body()
    assert '<form id="pwForm"' in body, "the modal body is not a form"
    assert "</form>" in body
    assert "onsubmit=" in body, "the form has no submit handler"


def test_the_form_carries_a_username_for_the_password_manager() -> None:
    """A manager needs a username in the same form to know which credential to
    update, and skips display:none — hence off-screen positioning."""
    body = _pw_modal_body()
    m = re.search(r'<input id="pwUser"[^>]*>', body)
    assert m, "no username field for the manager"
    tag = m.group(0)
    assert 'autocomplete="username"' in tag
    assert "left:-9999px" in tag, "the field is not positioned off-screen"
    assert "display:none" not in tag, "an off-screen field became display:none — managers skip those"
    assert 'tabindex="-1"' in tag, "the manager's field is in the tab order"


def test_the_submit_button_is_a_real_submit_bound_to_the_form() -> None:
    """The footer lives outside #pwForm, so the button needs form= to submit it."""
    body = _pw_modal_body()
    assert 'type="submit" form="pwForm"' in body, "the footer button does not submit the form"


def test_the_modal_has_a_confirm_field() -> None:
    """There is no way back from a password typed only once."""
    body = _pw_modal_body()
    for field in ('id="pwCur"', 'id="pwNew"', 'id="pwNew2"'):
        assert field in body, f"{field} is missing"
    assert body.count('autocomplete="new-password"') == 2, "new and confirm must both be new-password"


def test_a_mismatched_confirm_blocks_the_request() -> None:
    """The comparison has to sit before the fetch and return, not warn after it."""
    fn = _js_func("_doChangePw")
    mismatch = fn.index("nw.value!==nw2.value")
    assert "return" in fn[mismatch: fn.index("\n", mismatch)], "mismatch does not return"
    assert mismatch < fn.index("api("), "the request is sent before the confirm is checked"
    assert "nw2.focus()" in fn, "the field that failed is not focused"


def test_the_meter_never_gates_the_submit() -> None:
    """It advises. The only hard rule is the 8-character minimum the server
    enforces — a meter that refuses to submit turns a heuristic into a policy."""
    fn = _js_func("_doChangePw")
    for name in ("_pwScore", "_pwMeter", "PW_STATES"):
        assert name not in fn, f"{name} is consulted before submitting"
    assert "disabled" not in _js_func("_pwMeter"), "the meter disables a control"
    assert "disabled" not in _pw_modal_body(), "the modal ships a disabled control"


def test_the_meter_has_four_labelled_states_and_no_dependency() -> None:
    assert "const PW_STATES=['weak','fair','good','strong']" in SRC
    assert SRC.count('class="pwm-seg"') == 4, "the track is not four segments"
    # Named in a comment as the thing NOT shipped; called or fetched would be a
    # 400 KB dependency on a surface that has none.
    assert "zxcvbn(" not in SRC and "zxcvbn." not in SRC, \
        "a strength library was added to a zero-dependency surface"
    assert not re.search(r"<script[^>]*\bsrc=", SRC), "the console loaded an external script"
    for cls in (".pwm.s0", ".pwm.s1", ".pwm.s2", ".pwm.s3"):
        assert cls in _style_block(), f"{cls} has no fill"


def test_success_tells_the_password_manager() -> None:
    """The store call is the belt to the form submit's braces."""
    fn = _js_func("_doChangePw")
    assert "_storeCredential(" in fn, "nothing tells the manager the credential changed"
    assert fn.index("_storeCredential(") > fn.index("if(!r.ok)"), "stored before the server agreed"
    assert "navigator.credentials.store" in _js_func("_storeCredential")


def test_both_password_fields_can_be_revealed() -> None:
    """Three fields, three toggles, each naming its input and its state."""
    body = _pw_modal_body()
    assert body.count("data-pw-toggle") >= 1 and "eye('pwCur')" in body
    for field in ("pwCur", "pwNew", "pwNew2"):
        assert f"eye('{field}')" in body, f"{field} has no reveal toggle"
    eye = _js_func("_pwEyes")
    assert "aria-pressed" in eye and "aria-label" in eye, "the toggle does not announce its state"
    assert ".pw-eye{" in _style_block()


def test_the_offscreen_username_does_not_steal_the_modal_focus() -> None:
    """openModal focuses the first field; the manager's username must not be it."""
    assert 'querySelector(\'input:not([tabindex="-1"])' in SRC, \
        "openModal would focus the off-screen username field"


# ── chips, the hero (i), and the removed activity card (round 2 · P3) ─────────

def _css_rule(selector: str) -> str:
    """The body of the first `selector{…}` rule in the real <style> block."""
    css = _style_block()
    i = css.index(selector + "{")
    return css[i + len(selector) + 1: css.index("}", i)]


def test_the_chip_is_a_solid_fill_with_no_border() -> None:
    """The old chip was a translucent tint plus a 30%-mix border. Over .k-teal's
    deep stop its own ink measured 1.55:1 — that is item 4. Only an opaque fill
    makes the face behind it stop mattering."""
    base = _css_rule(".chip")
    assert "border:0" in base, "the chip still draws a border"
    assert "border:1px" not in base
    for cls, fill, ink in (("safe", "--safe-bg", "--safe-tx"),
                           ("bad", "--breach-bg", "--breach-tx"),
                           ("warn", "--warn-bg", "--warn-tx"),
                           ("info", "--info-bg", "--info-tx")):
        rule = _css_rule(f".chip.{cls}")
        assert f"background:var({fill})" in rule, f".chip.{cls} is not a solid fill: {rule}"
        assert f"color:var({ink})" in rule, f".chip.{cls} does not use its paired ink: {rule}"
        assert "border-color" not in rule, f".chip.{cls} kept a border colour"
        assert "-soft" not in rule, f".chip.{cls} is still a translucent tint"


def test_the_chip_separates_from_the_face_without_a_border() -> None:
    """Solid fixes the text and breaks the edge: #3ddc84 on .k-teal's light stop
    is 1.01:1, so the word reads but the pill dissolves into the card. The owner
    ruled out a border, so the boundary has to come from the lift."""
    assert "box-shadow" in _css_rule(".chip"), "nothing separates the pill from the face"


def test_the_dim_chip_does_not_shout_in_a_status_colour() -> None:
    """`never` and `not_applicable` mark absence, not a state."""
    rule = _css_rule(".chip.dim")
    for status in ("--safe-bg", "--breach-bg", "--warn-bg", "--info-bg",
                   "--ok", "--danger", "--warnc", "--infoc"):
        assert status not in rule, f".chip.dim borrowed {status}"
    assert "background:var(--line2)" in rule and "color:var(--ink2)" in rule, rule


# ── the hero info button ──

# (_kpi_cards is defined once, up with the other block helpers. A second
#  definition used to live here and silently shadowed it: its regex required the
#  class attribute to END at the hue, so any modifier class made four separate
#  guards report a phantom card-count drop instead of the thing they test.)


def test_every_hero_card_has_an_info_button_with_real_copy() -> None:
    cards = _kpi_cards()
    assert len(cards) >= 16, f"only found {len(cards)} kpi cards"
    for card in cards:
        m = re.search(r'<button type="button" class="kinfo"[^>]*>', card)
        assert m, f"a card has no (i): {card[:90]}"
        tag = m.group(0)
        tip = re.search(r'data-tip="([^"]*)"', tag)
        assert tip and len(tip.group(1)) > 40, f"(i) copy is too thin to be worth a button: {tag}"
        assert 'aria-label="More about ' in tag, f"the (i) has no accessible name: {tag}"


def test_the_info_button_is_a_real_button_left_in_the_tab_order() -> None:
    """opacity:0 keeps it focusable. display:none or visibility:hidden would take
    it out of the tab order and make it pointer-only, which is not reachable."""
    rule = _css_rule(".kpi .kinfo")
    assert "opacity:0" in rule, "the (i) is not hidden by opacity"
    assert "display:none" not in rule and "visibility:hidden" not in rule, \
        "the (i) is hidden in a way that removes it from the tab order"
    assert 'tabindex="-1"' not in SRC[SRC.index('class="kinfo"'):][:400]


def test_focus_visible_reveals_the_info_button_exactly_as_hover_does() -> None:
    css = _style_block()
    reveal = css[css.index(".kpi:hover .kinfo"):]
    reveal = reveal[: reveal.index("}") + 1]
    assert ".kinfo:focus-visible" in reveal, "keyboard focus does not reveal the (i)"
    assert "opacity:1" in reveal
    # ...and the glyph steps aside for both, or the two overlap.
    swap = css[css.index(".kpi:hover .gly"):]
    swap = swap[: swap.index("}") + 1]
    assert ".kinfo:focus-visible + .gly" in swap, "the glyph does not move on keyboard focus"


def test_the_info_button_has_a_touch_path() -> None:
    """There is no hover on a phone: the (i) must be permanently visible there,
    and it must be openable by tap — .datatip's focusin path is :focus-visible
    gated, which a tap does not satisfy."""
    css = _style_block()
    i = css.index("@media (hover:none)")
    block = css[i: css.index("\n}", i)]
    assert ".kpi .kinfo" in block and "opacity:1" in block, block
    assert ".kpi .gly" in block and "opacity:0" in block, "the glyph does not step aside on touch"
    assert "44px" in block, "the touch target is under the 44px minimum"
    assert "onclick=\"kinfoTip(this)\"" in SRC, "the (i) cannot be opened by tap"


def test_the_info_button_reuses_the_shared_tooltip() -> None:
    """One .datatip node, 23 existing sites. A second tooltip is a second set of
    edge-flip, delay and reduced-motion bugs."""
    assert "data-tip=" in SRC[SRC.index('class="kinfo"'):][:400]
    assert SRC.count("class='datatip'") + SRC.count('class="datatip"') <= 1
    fn = _js_func("kinfoTip")
    assert "_tipForEl" in fn and "tipHide" in fn, "kinfoTip built its own tooltip"


# ── item 9: the Settings self-activity card ──

def test_the_settings_activity_card_is_gone() -> None:
    assert "setActivity" not in SRC, "#setActivity survived"
    assert "loadSelfActivity" not in SRC, "its loader survived"


def test_the_staff_drilldown_activity_list_survived() -> None:
    """#mActivity is a different feature — the staff-member audit list a
    superadmin opens from the drill-down modal. Only the self card went."""
    assert 'id="mActivity"' in SRC, "the staff drill-down activity list was removed too"
    assert "loadStaffActivity" in SRC, "its loader was removed too"


# ── the P2 error-focus routing, hardened ──

def test_the_change_password_error_focus_reads_no_server_copy() -> None:
    """It used to be `(/current/i.test(m) ? cur : nw).focus()`, which also matched
    the reuse message — a complaint about the NEW field — and so pointed at the
    wrong input as soon as the backend grew its reuse guard."""
    # Comments stripped: this function explains the old shape in prose, and the
    # guard is about the code, not about what the code says about itself.
    fn = re.sub(r"//[^\n]*", "", _js_func("_doChangePw"))
    tail = fn[fn.index("if(!r.ok)"):]
    assert "/current/" not in fn, "the substring match over server copy came back"
    assert not re.search(r"\.test\(\s*m\s*\)|m\.(includes|indexOf|match)\(", tail), \
        f"the error branch is parsing the server's wording again: {tail[:200]}"
    # The local reuse check is what makes that safe — it removes the only server
    # rejection that is about the new-password box.
    assert "nw.value===cur.value" in fn, "nothing catches reuse before the request"


# ── the shown-once campaign disclosure (round 2 · P4) ────────────────────────

def test_the_campaign_panel_shows_both_secrets_once() -> None:
    """Creation now returns two things you cannot get again, not one."""
    fn = _js_func("createCampaign")
    assert "_secretRow('redemption code'" in fn
    assert "_secretRow('shareable link'" in fn
    assert "shown once" in fn


def test_the_panel_says_the_link_is_as_secret_as_the_code() -> None:
    """A URL reads like something you paste into a channel; a code does not. The
    UI has to close that gap in words, because the two shapes do not."""
    assert "contains the code" in _js_func("createCampaign"), \
        "the link's secrecy is never stated"


def test_the_secret_row_shows_its_value_in_full() -> None:
    """A link you cannot read end to end is a link you cannot check before you
    send it, so it wraps rather than truncating or masking."""
    fn = _js_func("_secretRow")
    assert "word-break:break-all" in fn
    assert "text-overflow" not in fn and "masked" not in fn


def test_the_copy_button_is_wired() -> None:
    assert "data-copy=" in SRC
    handler = SRC[SRC.index("var b=e.target.closest?e.target.closest('[data-copy]')"):]
    assert "_copyText(" in handler[:400], "the copy button does nothing"

# ═══════════════════════════════════════════════════════════════════════════
# A0 — the shared component layer (docs/plans/admin-refinement.md)
#
# These measure. The one guard this file has shipped that asserted a constant
# against itself is the reason every ratio below is recomputed from the token
# values in the file rather than compared to a number written beside them.
# ═══════════════════════════════════════════════════════════════════════════

def _srgb(c: float) -> float:
    c /= 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _lum(hex_: str) -> float:
    h = hex_.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _srgb(r) + 0.7152 * _srgb(g) + 0.0722 * _srgb(b)


def _ratio(a: str, b: str) -> float:
    la, lb = _lum(a), _lum(b)
    if la < lb:
        la, lb = lb, la
    return (la + 0.05) / (lb + 0.05)


def _css() -> str:
    """The style block with comments stripped.

    This matters more than it looks. The comments in this file quote token names
    and selectors verbatim -- "Contrast against --surf: ink 15.79:1", ".btn:hover
    was declared twice" -- so any guard that greps the raw block reads prose as
    if it were code. Several of the guards below were written that way first and
    passed for the wrong reason until they were pointed at this.
    """
    return re.sub(r"/\*.*?\*/", "", _style_block(), flags=re.S)


def _scope(name: str) -> str:
    css = _css()
    at = css.index(name)
    return css[at:css.index("}", at)]


def _token(name: str, theme: str = "dark") -> str:
    """The literal hex a token resolves to in one theme, following var() aliases."""
    root, light = _scope(":root{"), _scope('html[data-theme="light"]{')
    scopes = [light, root] if theme == "light" else [root]
    seen, cur = set(), name
    while True:
        assert cur not in seen, f"var() cycle at {cur}"
        seen.add(cur)
        val = None
        for scope in scopes:
            m = re.search(re.escape(cur) + r"\s*:\s*([^;}]+)", scope)
            if m:
                val = m.group(1).strip()
                break
        assert val is not None, f"{cur} is not declared in the {theme} scope"
        m = re.fullmatch(r"var\((--[a-z0-9-]+)\)", val)
        if not m:
            assert val.startswith("#"), f"{name} -> {val}: not a hex in {theme}"
            return val
        cur = m.group(1)


def _face_stops() -> list[str]:
    css = _css()
    out = []
    for k in FACE_CLASSES:
        i = css.index("." + k)
        out += re.findall(r"--k2?:\s*(#[0-9a-fA-F]{3,6})", css[i:css.index("}", i)])
    assert len(out) == 18, f"expected 9 faces x 2 stops, got {len(out)}"
    return out


CHIP_KINDS = {"safe": "--safe-bg", "bad": "--breach-bg", "warn": "--warn-bg",
              "info": "--info-bg", "dim": "--line2"}


# ── 1 · the measured defect ──────────────────────────────────────────────────

def test_a_chip_on_a_hero_face_does_not_use_the_panel_fill() -> None:
    """The whole point of A0. Without this scope the pill reverts to a fill that
    measured 1.005:1 against the face it sits on."""
    css = _style_block()
    assert ".kpi:not(.quiet) .face .chip{background:var(--foxink)" in css, (
        "the on-face chip lost its plate — every status pill on a SATURATED hero "
        "card goes back to matching the gradient behind it"
    )
    for kind in CHIP_KINDS:
        assert ".kpi:not(.quiet) .face .chip.%s{color:var(--face-ink-%s)}" % (kind, kind) in css, (
            ".chip.%s has no on-face ink" % kind
        )


def test_the_on_face_plate_clears_the_component_bar_against_every_face_stop() -> None:
    """3.0:1 is WCAG 1.4.11 for a component boundary. Measured, not asserted."""
    plate = _token("--foxink")
    worst, stop = min((_ratio(plate, s), s) for s in _face_stops())
    assert worst >= 3.0, f"the on-face plate {plate} is only {worst:.2f}:1 against {stop}"


def test_every_on_face_ink_is_legible_on_the_plate() -> None:
    plate = _token("--foxink")
    for kind in CHIP_KINDS:
        ink = _token("--face-ink-" + kind)
        r = _ratio(ink, plate)
        assert r >= 4.5, f"--face-ink-{kind} {ink} is {r:.2f}:1 on {plate}"


def test_the_on_face_inks_are_theme_invariant() -> None:
    """The nine faces are declared once and never re-themed, so anything measured
    against them must be too. A light-block override would reopen the defect in
    one theme only."""
    light = _scope('html[data-theme="light"]{')
    for kind in CHIP_KINDS:
        assert "--face-ink-" + kind not in light, (
            f"--face-ink-{kind} is re-themed; the face it lands on is not"
        )


def test_the_health_hero_is_still_the_case_this_was_built_for() -> None:
    """#hStatus and #hWorker are .kval nodes inside a .kpi .face and opsChip()
    writes a .chip into both. The scoped rule above is the only thing protecting
    them, so if either leaves a face, say so."""
    assert "$('hStatus').innerHTML=opsChip(" in SRC
    assert "$('hWorker').innerHTML=opsChip(" in SRC
    for hid in ("hStatus", "hWorker"):
        card = next(c for c in _kpi_cards() if 'id="%s"' % hid in c)
        assert 'class="face"' in card, f"#{hid} left the hero face"


# ── 2 · chips on flat panels, the other context ──────────────────────────────

def test_every_meaning_bearing_chip_fill_separates_from_every_panel() -> None:
    """.dim is exempt and says so at its rule: its fill carries no meaning to
    lose (the word clears 6.79/5.32), and any fill that would reach 3.0 against
    near-white paper would be louder than a real status."""
    for theme in ("dark", "light"):
        for kind, tok in CHIP_KINDS.items():
            if kind == "dim":
                continue
            fill = _token(tok, theme)
            for p in ("--bg", "--surf", "--surf2", "--surf3"):
                r = _ratio(fill, _token(p, theme))
                assert r >= 3.0, (
                    f"{theme} .chip.{kind} ({fill}) is {r:.2f}:1 on {p} — no visible pill"
                )


def test_every_chip_ink_is_legible_on_its_own_fill() -> None:
    inks = {"safe": "--safe-tx", "bad": "--breach-tx", "warn": "--warn-tx",
            "info": "--info-tx", "dim": "--ink2"}
    for theme in ("dark", "light"):
        for kind, tok in CHIP_KINDS.items():
            r = _ratio(_token(inks[kind], theme), _token(tok, theme))
            assert r >= 4.5, f"{theme} .chip.{kind} ink is {r:.2f}:1 on its fill"


def test_a_category_is_not_dressed_as_a_status() -> None:
    """A role, a plan tier, a site and a threshold are labels. Wearing amber for
    'superadmin' beside a column where amber means a real HTTP warning is what
    teaches staff to stop reading amber."""
    css = _css()
    assert ".tag{" in css and "background:var(--neutral-soft)" in css
    for banned in ('chip info">\'+esc(p.enforcement_mode)',
                   'chip info">\'+esc(p.confidence_threshold)',
                   'chip info">\'+esc(d.plan_tier',
                   'chip dim">you</span>',
                   "s.platform_role==='superadmin'?'warn'"):
        assert banned not in SRC, f"a category is wearing a status hue: {banned}"


# ── 3 · the button family ────────────────────────────────────────────────────

def test_the_button_obeys_the_files_own_elevation_contract() -> None:
    """'--raise is the default for anything you would pick up; :active swaps
    raise -> sink', says the token block. .btn sat at --clay-press (= --sink-sm)
    at rest AND on :active, so pressed and unpressed were the same shadow."""
    assert "box-shadow:var(--raise-sm)" in _scope(".btn{"), ".btn no longer rests raised"
    assert "var(--sink-sm)" in _scope(".btn:active:not(:disabled){"), ".btn:active no longer sinks"


def test_no_interactive_button_rule_can_beat_disabled() -> None:
    """:hover still matches on a disabled button, and .btn.pri:hover outranks
    .btn:disabled on specificity — so a disabled Suspend / Replay / Re-anchor
    lifted off the page and brightened its glow under the cursor."""
    css = _css()
    for m in re.finditer(r"\.btn(?:\.[a-z]+)?:(?:hover|active)[^{,]*\{", css):
        assert ":not(:disabled)" in m.group(0), f"{m.group(0).strip()} is not disabled-guarded"


def test_the_button_declares_hover_and_active_exactly_once() -> None:
    """They were declared twice — in CONTROLS and again in the E3 block, which
    silently shadowed the first pair. An edit there did nothing."""
    css = _css()
    assert len(re.findall(r"(?<![.\w)])\.btn:hover", css)) == 1
    assert len(re.findall(r"(?<![.\w)])\.btn:active", css)) == 1


def test_disabled_is_a_real_treatment_not_an_opacity() -> None:
    """Group opacity fades fill and ink together over the page: all 16 variants
    landed under 4.5:1, worst light .btn.safe at 1.42:1, and the light .btn's own
    fill hit 1.02:1 against its panel — the button disappeared."""
    rule = _scope(".btn:disabled,")
    assert "opacity:" not in rule, "disabled is back to fading the whole button"
    assert "color:var(--muted)" in rule and "background:var(--bg)" in rule
    assert "box-shadow:var(--sink-sm)" in rule, "a disabled button with no shadow has no shape"
    for theme in ("dark", "light"):
        r = _ratio(_token("--muted", theme), _token("--bg", theme))
        assert r >= 4.5, f"{theme} disabled label is only {r:.2f}:1"


def test_the_button_family_is_complete() -> None:
    """operate.md: default, hover, focus, active, disabled, loading. Do not ship
    with half of them."""
    css = _css()
    for variant in (".btn.pri{", ".btn.ghost{", ".btn.danger{", ".btn.safe{"):
        assert variant in css, f"{variant} is missing from the family"
    assert '.btn[aria-busy="true"]' in css, "the button has no loading state"
    assert ".btn.ghost:hover:not(:disabled)" in css
    assert ".btn.ghost:active:not(:disabled)" in css


def test_a_finger_gets_a_finger_sized_button() -> None:
    css = _css()
    at = css.index("@media (hover:none){", css.index(".btn.sm{"))
    assert "min-height:44px" in css[at:at + 200], "no SC 2.5.5 target on a coarse pointer"


# ── 4 · the light theme's primary action ─────────────────────────────────────

def test_the_primary_action_carries_its_ink_in_both_themes() -> None:
    """--fox-hi is a FILL and was aliased onto --fox2, which the light block had
    already re-derived into an INK. That put --on-pri #1a0900 on #b14000 at
    3.33:1 — below AA, on .btn.pri, .fauxselect-opt.sel, .segbtn[aria-pressed]
    and the checkbox tick, in the shipped light theme."""
    for theme in ("dark", "light"):
        ink = _token("--on-pri", theme)
        for stop in ("--fox-hi", "--fox"):
            r = _ratio(ink, _token(stop, theme))
            assert r >= 4.5, f"{theme} --on-pri on {stop} is {r:.2f}:1"


def test_the_primary_fill_separates_from_the_page() -> None:
    for theme in ("dark", "light"):
        for stop in ("--fox-hi", "--fox"):
            r = _ratio(_token(stop, theme), _token("--surf", theme))
            assert r >= 3.0, f"{theme} {stop} is {r:.2f}:1 against --surf"


def test_fox_hi_is_not_an_alias_of_the_ink_in_light() -> None:
    assert _token("--fox-hi", "light") != _token("--fox2", "light"), (
        "--fox-hi is back to borrowing the ink token's value"
    )


# ── 5 · forms ────────────────────────────────────────────────────────────────

def test_every_field_label_is_a_real_label() -> None:
    """There were 47 .flabel and zero `for` attributes: every input in the
    console, including the sign-in password, announced as 'edit, blank'."""
    orphans = re.findall(
        r'<div class="flabel"[^>]*>[^<]*</div>\s*<(?:input|textarea)[^>]*\bclass="[^"]*\bcin\b', SRC)
    assert not orphans, f"{len(orphans)} field labels are still unassociated <div>s"
    assert len(re.findall(r'<label class="flabel"[^>]*\bfor="', SRC)) >= 30


def test_every_label_for_points_at_something() -> None:
    """A wrong `for` is worse than no `for`."""
    for target in re.findall(r'<label class="flabel"[^>]*\bfor="([^"]+)"', SRC):
        assert 'id="%s"' % target in SRC, f'<label for="{target}"> points at nothing'


def test_the_label_style_survives_becoming_an_element() -> None:
    assert "display:block" in _scope(".flabel{"), "<label> is inline; the block layout is gone"


def test_the_custom_dropdown_is_operable_without_a_mouse() -> None:
    """The swap from <select> lost arrow keys, Home/End, type-ahead, Escape and
    every AT role. A keyboard user could open the list and reach no option."""
    assert "root.addEventListener('keydown'" in SRC, ".fauxselect has no key handling"
    keys = SRC[SRC.index("function _fauxKeys("):]
    keys = keys[:keys.index("\nfunction ")]
    for k in ("ArrowDown", "ArrowUp", "Home", "End", "Escape", "Enter"):
        assert k in keys, f".fauxselect does not handle {k}"
    roles = SRC[SRC.index("function _fauxRoles("):]
    roles = roles[:roles.index("\nfunction ")]
    for attr in ("'role','combobox'", "'role','listbox'", "'role','option'",
                 "'aria-expanded'", "'aria-selected'"):
        assert attr in roles, f".fauxselect is missing {attr}"


def test_the_dropdowns_that_cannot_take_a_for_are_named_another_way() -> None:
    for rid in ("trafSite", "trafStatus", "nsRole", "dataTable"):
        assert 'aria-labelledby="%sLab"' % rid in SRC, f"#{rid} trigger is unnamed"
        assert 'id="%sLab"' % rid in SRC


def test_the_command_palette_input_replaces_the_ring_it_suppresses() -> None:
    assert ".cmdk-in:focus-visible{box-shadow:var(--focus)}" in _css()


# ── 6 · states ───────────────────────────────────────────────────────────────

def test_a_fault_is_not_an_empty() -> None:
    """'No ledger rows.' and 'Failed to load ledger.' rendered as the same grey
    centred sentence, and `Retry` appeared zero times in 4,125 lines."""
    css = _css()
    assert ".chart-fault{" in css
    assert "background:var(--danger-soft)" in _scope(".chart-fault{")
    assert "function fault(" in SRC and "function faultRow(" in SRC
    assert "Try again" in SRC, "a fault state with no recovery is just a nicer empty"
    for stale in ('chart-empty">Failed to load', 'chart-empty">unavailable',
                  'chart-empty">trend unavailable'):
        assert stale not in SRC, f"a fault is still rendering as an empty: {stale}"


def test_every_retry_calls_a_function_that_exists() -> None:
    calls = set(re.findall(r'fault\([^;]*?,\s*"(\w+)\(', SRC))
    assert calls, "no fault() call site offers a retry"
    for call in calls:
        assert "function %s(" % call in SRC, f"Try again calls {call}(), which is not defined"


def test_the_loading_state_loads_in_both_themes() -> None:
    """The shimmer was a hardcoded white at 14%: 1.55:1 on the dark surface and
    1.016:1 on the light one."""
    css = _css()
    after = css[css.index(".skel::after"):]
    assert "var(--skel-hi)" in after[:300]
    assert "rgba(255,255,255,.14)" not in after[:300]
    assert "--skel-hi" in _scope('html[data-theme="light"]{'), "--skel-hi is not re-themed"


def test_the_skeleton_matches_what_it_stands_in_for() -> None:
    minh = re.search(r"min-height:(\d+)px", _scope(".kpi .face{")).group(1)
    assert ".skel-kpi{height:%spx}" % minh in _css(), (
        "the KPI skeleton and the KPI card are different heights — a jump on load"
    )


def test_the_toast_announces_itself() -> None:
    """The only confirmation and error channel for suspend, disable, replay and
    re-anchor. The password meter had a live region; this did not."""
    i = SRC.index('<div id="toast"')
    tag = SRC[i:SRC.index(">", i)]
    assert 'role="status"' in tag and 'aria-live="polite"' in tag


# ── 7 · tables ───────────────────────────────────────────────────────────────

def test_only_a_clickable_row_claims_to_be_clickable() -> None:
    css = _css()
    assert ".tbl tbody tr:hover{background:var(--surf2)}" in css
    assert ".tbl tbody tr.rowbtn:hover{box-shadow:inset 3px 0 0 var(--fox)}" in css, (
        "the drill-down affordance is back on rows that do not drill down"
    )


def test_a_sticky_header_has_something_to_stick_inside() -> None:
    """position:sticky needs a scrollport, and .twrap is height:auto — so on 9 of
    the 10 wraps the rule was inert. .scrolly is the opt-in that makes it real."""
    assert "overflow:auto" in _scope(".twrap.scrolly{")
    assert 'class="twrap" style="max-height' not in SRC, (
        "a table is hand-rolling a scrollport instead of using .scrolly"
    )


# ── 8 · the scales ───────────────────────────────────────────────────────────

RADIUS_TOKENS = ("--r-xs", "--r-sm", "--r-md", "--r-lg", "--r-xl", "--r-pill")


def _root_value(token: str) -> str:
    return re.search(re.escape(token) + r"\s*:\s*([^;}]+)", _scope(":root{")).group(1)


def test_the_radius_ladder_has_no_off_scale_strays() -> None:
    """It was 12 distinct hardcoded radii against a 4-token scale, with 11px —
    exactly --r-sm — retyped as a literal three times. 50% is a circle and 2px is
    a hairline on a 3-4px bar; neither is a surface, so neither is on the ladder."""
    css = _css()
    strays = [v.strip() for v in re.findall(r"border-radius:\s*([^;}]+)", css)
              if not any(t in v for t in RADIUS_TOKENS) and v.strip() not in ("50%", "2px")]
    assert not strays, f"off-ladder radii: {sorted(set(strays))}"


def test_the_radius_ladder_ascends() -> None:
    px = {t: int(re.search(r"(\d+)px", _root_value(t)).group(1))
          for t in ("--r-xs", "--r-sm", "--r-md")}
    assert px["--r-xs"] < px["--r-sm"] < px["--r-md"], px


def test_the_two_fluid_radii_never_converge() -> None:
    """The overlap was reported as a defect; it is not one. Both clamps ride the
    same vw, so --r-xl leads --r-lg by 4-8px at every real viewport. Checked
    rather than assumed, because the next reader will wonder too."""
    lg = [float(x) for x in re.findall(r"[\d.]+", _root_value("--r-lg"))]
    xl = [float(x) for x in re.findall(r"[\d.]+", _root_value("--r-xl"))]
    for vw in (320, 480, 768, 1024, 1440, 1920):
        a = max(lg[0], min(lg[2], lg[1] / 100 * vw))
        b = max(xl[0], min(xl[2], xl[1] / 100 * vw))
        assert b - a >= 3.0, f"at {vw}px --r-lg {a} and --r-xl {b} are indistinguishable"


def test_no_token_is_declared_and_then_never_used() -> None:
    """A scale nothing references is a claim about a system, not a system —
    --s-1/-2/-6/-7/-8, --r-xl and --ease-spring were all at zero call sites while
    their literal values were retyped below."""
    for token in ["--s-%d" % n for n in range(1, 8)] + list(RADIUS_TOKENS) + \
                 ["--ease-spring", "--dur-slow", "--skel-hi"] + \
                 ["--face-ink-%s" % k for k in CHIP_KINDS]:
        uses = len(re.findall(r"var\(" + re.escape(token) + r"[,)]", SRC))
        assert uses >= 1, f"{token} is declared and never used"


def test_the_shared_components_speak_the_spacing_scale() -> None:
    """Page rules still hold raw px — A1-A6 convert their own. The components
    that everything downstream inherits do not."""
    css = _css()
    for sel in (".chip{", ".tag{", ".btn{", ".tbl td{", ".flabel{"):
        rule = css[css.index(sel):css.index("}", css.index(sel))]
        spacing = re.findall(r"(?:padding|margin|gap)\s*:\s*([^;}]+)", rule)
        assert spacing, f"{sel} declares no spacing at all"
        for v in spacing:
            assert "--s-" in v, f"{sel} still hardcodes spacing: {v}"


# ── 9 · depth ────────────────────────────────────────────────────────────────

def test_no_shadow_is_a_zero_offset_halo() -> None:
    """A shadow carries an offset and a blur; `0 0 12px` of a brand colour is
    decoration wearing depth's clothes."""
    css = _css()
    halos = re.findall(r"box-shadow:\s*0 0 [1-9]\d*px\s+(?:var\(|rgba?\(|#)", css)
    assert not halos, f"zero-offset colored halos: {halos}"


# ── 10 · A1 · the overview and health pages ──────────────────────────────────
# Register #65: the saturated face was on all 21 KPIs, so nothing was primary.
# A1 reserves it for the ONE card per page that answers "is something wrong
# right now" and lets the rest go quiet — without touching the beam, which is a
# settled decision (#64) and orbits on all 21 regardless.


def _css_without_media() -> str:
    """The stylesheet with every @media block removed, by brace balance.

    Splitting on the first "@media" is not the same thing: the first width query
    in this sheet sits ~2000 chars ABOVE the bento rules, so that split threw
    away most of the base cascade — and a guard written against it failed on
    correct source, which is the same defect as passing on broken source.
    """
    css = _css()
    out, i = [], 0
    while True:
        m = css.find("@media", i)
        if m == -1:
            out.append(css[i:])
            return "".join(out)
        out.append(css[i:m])
        j = css.index("{", m)
        depth = 1
        j += 1
        while j < len(css) and depth:
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
            j += 1
        i = j


def _page(page_id: str) -> str:
    """The markup of one <section class="page" id="page-…">."""
    start = SRC.index('id="page-%s"' % page_id)
    start = SRC.rindex("<section", 0, start)
    return SRC[start: SRC.index("</section>", start)]


A1_PAGES = ("overview", "health")


def test_exactly_one_emphatic_face_per_a1_page() -> None:
    """The whole point. Two emphatic cards on one page is the old problem at
    lower volume; zero means the page lost its answer."""
    for page in A1_PAGES:
        cards = re.findall(r'<div class="clay kpi ([^"]*)"', _page(page))
        assert cards, "%s has no KPI cards at all" % page
        loud = [c for c in cards if "quiet" not in c]
        assert len(loud) == 1, (
            "%s has %d emphatic faces (%s) — the face is the mark of the one card "
            "that answers 'is something wrong right now'" % (page, len(loud), loud)
        )


def test_the_emphatic_card_is_the_one_that_answers_the_question() -> None:
    """Which card keeps the face is the decision, not an accident of order."""
    overview = _page("overview")
    loud = re.search(r'<div class="clay kpi (k-[a-z]+)"(?![^>]*quiet)', overview)
    assert loud, "the overview lost its emphatic card"
    card = overview[overview.index(loud.group(0)):][:900]
    assert "Policy breaches" in card, (
        "the overview's emphatic face moved off Policy breaches — the only one of "
        "its five KPIs that is both volatile and consequential"
    )
    health = _page("health")
    hloud = re.search(r'<div class="clay kpi (k-[a-z]+)"(?![^>]*quiet)', health)
    assert hloud, "health lost its emphatic card"
    hcard = health[health.index(hloud.group(0)):][:900]
    assert 'id="hStatus"' in hcard, "health's emphatic face left the Overall card"


def test_a_quiet_card_is_quiet_not_dead() -> None:
    """'Quieter' must not collapse into 'grey'. A quiet card keeps its hue in the
    surface and keeps the accent bar at full saturation; if either goes, the
    proposal has become 'turn the colour off', which is a different thing."""
    css = _css()
    rule = css[css.index(".kpi.quiet .face{"):]
    rule = rule[: rule.index("}")]
    assert "color-mix" in rule and "var(--k" in rule, (
        "the quiet face stopped deriving its tint from the card's own hue: " + rule
    )
    assert ".kpi.quiet::before" not in css, (
        "something dimmed the accent bar on quiet cards; it is the one element "
        "that still states the card's hue at full strength"
    )


def test_the_beam_is_untouched_by_the_quiet_variant() -> None:
    """#64 is decided: the beam orbits on all 21 cards. Hierarchy here had to be
    built with motion held constant, so a quiet card must not buy its quietness
    by stopping."""
    css = _css()
    for bad in (".kpi.quiet .beam", ".quiet .beam"):
        assert bad not in css, "%s — the quiet variant is reaching for the beam" % bad
    for page in A1_PAGES:
        markup = _page(page)
        cards = re.findall(r'<div class="clay kpi [^"]*"', markup)
        beams = markup.count('class="beam"')
        assert beams == len(cards), "%s: %d cards but %d beams" % (page, len(cards), beams)


def test_secondary_ink_on_a_quiet_face_is_tinted_not_grey() -> None:
    """MEASURED. --muted / --muted2 are greys tuned against the flat panel; on
    the tinted quiet face they fell to 3.93-4.10:1 (label) and 3.54-3.69:1
    (foot) in the dark theme. Both are under AA. The replacement is mixed out of
    --ink and the card's own deep stop and clears 7.9:1 in both themes."""
    css = _css()
    for part in ("klabel", "kfoot"):
        m = re.search(r"\.kpi\.quiet \.face \.%s\{color:([^}]+)\}" % part, css)
        assert m, "the quiet face stopped styling .%s and inherited --foxink" % part
        value = m.group(1)
        assert "--muted" not in value, (
            ".%s is back on a flat-panel grey over a tinted surface: %s" % (part, value)
        )
        assert "color-mix" in value and "--ink" in value, (
            ".%s must tint from the surface's own hue: %s" % (part, value)
        )


def test_the_verdict_is_not_smaller_than_the_counts_beside_it() -> None:
    """#hStatus is the answer to the only question this page is asked, and it was
    rendering at the base .chip's 9.5px inside a slot sized for a 30px number."""
    css = _css()
    m = re.search(r"\.kpi \.kval>\.chip\{([^}]*)\}", css)
    assert m, "the on-face verdict lost its size rule and is a 9.5px pill again"
    size = re.search(r"font-size:([\d.]+)px", m.group(1))
    assert size and float(size.group(1)) >= 15, (
        "the verdict is %s — the raw counts next to it render at 22-30px"
        % (size.group(1) + "px" if size else "unsized")
    )


def test_the_status_of_the_health_hero_is_announced() -> None:
    """innerHTML swapping — to ok, or ok to degraded — is silent to a screen
    reader without a live region, on the console's headline card."""
    health = _page("health")
    for hid in ("hStatus", "hWorker"):
        m = re.search(r'<div class="kval" id="%s"([^>]*)>' % hid, health)
        assert m, "#%s is no longer a .kval on the health page" % hid
        assert 'aria-live="polite"' in m.group(1) and 'role="status"' in m.group(1), (
            "#%s changes silently: attrs were %r" % (hid, m.group(1))
        )


def test_no_dead_links_on_the_a1_pages() -> None:
    """`<a>` with no href is not focusable, not activatable and not announced.
    Six of these were the overview's only routes to the incident pages."""
    for page in A1_PAGES:
        dead = re.findall(r'<a class="linklike"[^>]*>', _page(page))
        assert not dead, "%s still has link-shaped divs: %s" % (page, dead)
    assert '<a class="linklike" onclick="goOps(' not in SRC, (
        "the JS-emitted 'view all N' alert link is still a dead <a>"
    )


def test_every_column_on_an_a1_page_names_itself() -> None:
    """Under 760px the header row is removed entirely, so scope= and data-label
    are the only things tying a value to its column."""
    for page in A1_PAGES:
        for th in re.findall(r"<th\b[^>]*>", _page(page)):
            assert 'scope="col"' in th, "%s: unscoped header %s" % (page, th)
    assert '<th scope="col">When</th>' in SRC, (
        "the alert-history table lost its column scopes"
    )


def test_the_mobile_card_table_does_not_announce_its_columns_twice() -> None:
    """td::before already prefixes every cell with its column name. Clipping the
    header row hides it visually but KEEPS it in the a11y tree, so the columns
    were read once as a loose run and then again inside each card."""
    body = dict(_width_media_blocks()).get("max-width:760px", "")
    m = re.search(r"\.tbl\.cardify thead\{([^}]*)\}", body)
    assert m, "the cardify header rule vanished from the 760px breakpoint"
    assert "display:none" in m.group(1), (
        "the cardify header row is hidden by clip again, which leaves it audible: "
        + m.group(1)
    )


def test_direction_is_never_carried_by_colour_alone_on_a_face() -> None:
    """loadKpiSparks set style.color to --ok / --danger on a .kfoot sitting on a
    decorative face. An inline style beats the face-ink rule, so the line
    measured 1.12:1 on .k-orange's deep stop — and painted a RISE in policy
    breaches green."""
    js = " ".join(_script_blocks())
    assert "f.style.color=" not in js, (
        "a status hue is being written onto a KPI foot from JS again"
    )
    assert "(up?'▲':'▼')" in js, (
        "the delta lost the glyph that carries its direction non-chromatically"
    )


def test_a_spark_inside_a_face_takes_that_faces_ink() -> None:
    """The sparkline colour was read from :root as --foxink — correct on a
    saturated face, invisible on a quiet one. The face publishes --spark now."""
    css = _css()
    assert "--spark:var(--foxink)" in css, "the saturated face stopped publishing --spark"
    assert "--spark:var(--k2)" in css, "the quiet face stopped overriding --spark"
    js = " ".join(_script_blocks())
    assert "getPropertyValue('--spark')" in js, (
        "loadKpiSparks is no longer reading the ink off the card it draws into"
    )
    assert "getComputedStyle(document.documentElement).getPropertyValue(c[3])" not in js, (
        "the spark colour is being read from :root again, which cannot know "
        "whether the card it lands on is quiet"
    )


def test_a_capability_that_is_never_measured_is_not_a_red_status() -> None:
    """build_health() only ever emits circuit_breaker.state "unavailable", so
    opsChip's else-branch painted a permanent red pill on the page whose job is
    "is anything wrong right now". A red chip that is always red gets skipped."""
    js = " ".join(_script_blocks())
    assert "opsCheck('circuit breaker',cb.state,cb.detail,true)" in js, (
        "the circuit-breaker row is a status chip again"
    )
    assert "neutral?" in js, "opsCheck lost its neutral (capability) rendering"


def test_the_overview_leads_with_the_two_panels_that_answer_the_question() -> None:
    """Open alerts used to be 7th of seven — bottom-right, below the fold — while
    the double-width slot went to a browsable org list. Order is read by the eye,
    the tab sequence and a screen reader alike."""
    bento = _page("overview")
    bento = bento[bento.index('<div class="bento">'):]
    titles = re.findall(r'<h2 class="panel-t">([^<]+)</h2>', bento)
    assert titles[:2] == ["System health", "Open alerts"], (
        "the bento no longer leads with the operational panels: %s" % titles[:2]
    )
    # the BASE rule, not a breakpoint override — the same declaration is
    # repeated inside the 1100px query, so a bare substring search over the whole
    # sheet stayed green when the base rule was deleted.
    base = _css_without_media()
    assert re.search(r"\.b-alerts[^{}]*\{[^{}]*grid-column:span 2", base), (
        "Open alerts lost the double slot its sentence-length rows need"
    )


def test_the_a1_pages_have_an_outline() -> None:
    """Zero headings meant no rotor entry and no way past seven panels except
    linear Tab. Every panel title is a real heading under one page-level h1."""
    for page in A1_PAGES:
        markup = _page(page)
        h1 = re.findall(r'<h1 class="sr-only">([^<]+)</h1>', markup)
        assert len(h1) == 1, "%s has %d page headings, expected 1" % (page, len(h1))
        assert '<h2 class="panel-t"' in markup, (
            "%s's panel titles are back to being unlabelled <div>s" % page
        )
        assert '<div class="panel-t">' not in markup, (
            "%s still has a <div> styled as a heading" % page
        )


def test_no_bento_class_names_something_that_does_not_exist() -> None:
    """b-traffic, b-leads and b-health were in the markup with zero CSS rules
    anywhere — a naming system that named nothing, which is the texture this
    phase exists to remove."""
    css = _css()
    for cls in re.findall(r'class="clay pad (b-[a-z]+)', _page("overview")):
        assert "." + cls in css, ".%s is in the markup but has no rule" % cls


# ── 11 · A2 · the orgs list, the org360 drill-down and the data browser ──────
# The two table-heavy surfaces. Most of what this section guards is behaviour
# that failed SILENTLY: a breach table that rendered zero rows while announcing
# a count, loaders that left "loading…" on screen forever, a table that kept the
# previous table's rows under the new table's name, and an IP allow-list whose
# failed read looked exactly like "no restrictions".


A2_PAGES = ("orgs", "org360", "data")

# every loader that paints one of the A2 surfaces, plus the two A1 loaders this
# phase inherited. Each must be able to say "I could not tell you".
A2_LOADERS = ("loadOrgs", "refreshData", "loadHealth", "loadOverview")


def _nocomment(text: str) -> str:
    """Strip HTML comments. This file argues with itself in prose — the A2
    sections carry comments containing `<a>`, `role="tab"` and `aria-selected`
    precisely because those are the things being removed. A guard that greps
    raw source finds the explanation and calls it the defect."""
    return re.sub(r"<!--.*?-->", "", text, flags=re.S)


def _a2_page(page_id: str) -> str:
    return _nocomment(_page(page_id))


def test_no_map_callback_is_decapitated_by_semicolon_insertion() -> None:
    """o3LoadBreaches ended a line with a bare `return`, with the row string
    starting on the next one. ASI closed the statement, every iteration returned
    undefined, and .join('') produced "" — so the Breaches tab rendered the
    header "N breaches" over an empty table, with a working pager. A confident,
    silent, wrong answer, and `node --check` accepts it happily.

    File-wide, because the shape is invisible to every other check here.
    """
    for block in _script_blocks():
        for n, line in enumerate(block.split("\n"), 1):
            code = re.sub(r"/\*.*?\*/", "", line).rstrip()
            assert not re.search(r"(^|[^.\w])return$", code), (
                "line %d ends with a bare `return`; ASI will discard whatever "
                "is on the next line: %r" % (n, line.strip()[:90])
            )


def test_every_a2_loader_can_say_that_it_failed() -> None:
    """A toast is a receipt for a thing you just did — it lasts 3.2s and leaves
    the placeholder behind. On these surfaces a dead endpoint was therefore
    indistinguishable from a slow one, on the console whose whole job is
    answering "is something wrong right now"."""
    for name in A2_LOADERS:
        body = _js_func(name)
        assert re.search(r"fault(Row)?\(", body), (
            "%s() has no fault() path — a failure leaves the surface on its "
            "loading placeholder with no way back" % name
        )
        assert not re.search(r"if\(!r(?:s)?(?:\|\|!r(?:s)?)?\.ok\)\s*\{\s*toast[^}]*return\s*\}", body), (
            "%s() still toasts-and-returns on failure" % name
        )
        assert "try{" in body or "try {" in body, (
            "%s() awaits without a catch: a network-layer throw (offline, DNS, "
            "TLS) rejects the whole function and produces no message at all" % name
        )


def test_every_a2_retry_calls_something_that_exists() -> None:
    """A Try-again button wired to a typo is worse than no button."""
    js = " ".join(_script_blocks())
    for name in A2_LOADERS:
        for retry in re.findall(r"fault(?:Row)?\([^)]*?['\"]([A-Za-z_$][\w$]*)\(",
                                _js_func(name)):
            assert re.search(r"(?:async\s+)?function\s+%s\s*\(" % re.escape(retry), js), (
                "%s()'s retry calls %s(), which is not defined" % (name, retry)
            )


def test_no_dead_link_survives_anywhere() -> None:
    """`<a>` with no href is not focusable, not activatable, not announced. A1
    converted six on the overview; the last two were org360's — one of them the
    ONLY route back out of the drill-down."""
    dead = re.findall(r"<a\s+class=\"linklike\"[^>]*>", _nocomment(SRC))
    assert not dead, "link-shaped non-links are back: %s" % dead
    assert "<button type=\"button\" class=\"linklike\" onclick=\"navTo('orgs')\"" in SRC, (
        "the org360 back-link is no longer a real button"
    )


def test_every_column_on_an_a2_surface_names_itself() -> None:
    """Under 760px .tbl.cardify removes the header row outright, so scope= and
    data-label are all that tie a value to its column. These two surfaces own
    34 of the file's 90 header cells."""
    for page in A2_PAGES:
        for th in re.findall(r"<th\b[^>]*>", _a2_page(page)):
            assert 'scope="col"' in th, "%s: unscoped header %s" % (page, th)
    # the JS-emitted tables: org360's four tabs and the data browser's emitter
    for fn in ("renderO3Users", "renderO3Keys", "o3LoadLedger", "o3LoadBreaches",
               "refreshData"):
        for th in re.findall(r"<th\b[^>]*>", _js_func(fn)):
            assert 'scope="col"' in th, "%s() emits an unscoped header %s" % (fn, th)


def test_the_actions_column_has_a_name() -> None:
    """`<th></th>` is a header cell a screen reader still lands on and cannot
    announce. The data browser emitted one for its edit/delete column."""
    body = _js_func("refreshData")
    assert "<th></th>" not in body, "the actions column is a nameless header again"
    assert 'class="sr-only">Row actions' in body, (
        "the actions column lost its accessible name"
    )


def test_the_org_list_can_be_opened_without_a_mouse() -> None:
    """The console's front door was a <tr> with an onclick: no tabindex, no key
    handler, invisible to AT. A1 met the same defect on the overview and
    correctly WITHDREW the affordance, because all six rows led to the one place
    the panel header already linked. Here every row leads somewhere different,
    so the promise is real and has to be kept for the keyboard too."""
    body = _js_func("renderOrgs")
    row = re.search(r"<tr class=\"rowbtn\"[^>]*>(.*?)</td>", body, re.S)
    assert row, "the org row stopped rendering"
    assert re.search(r"<button[^>]*class=\"rowlink\"[^>]*onclick=\"[^\"]*openOrg", row.group(1)), (
        "the org name is not a real control — the row is mouse-only again"
    )
    assert "event.stopPropagation()" in row.group(1), (
        "the name button does not stop the row's own handler, so opening an org "
        "fires openOrg twice"
    )
    css = _css()
    assert ".rowlink:focus-visible{" in css, "the row control has no visible focus"
    assert re.search(r"\.rowlink\{[^}]*color:inherit", css), (
        ".rowlink must inherit its ink — it replaced a <b>, and this was a "
        "semantics change, not a visual one"
    )


def test_exactly_one_emphatic_face_on_the_tenant_page() -> None:
    """A1's rule (#65) reaches the JS-emitted rows too: five saturated faces is
    the 'nothing is primary' state A1 was built to end."""
    body = _js_func("renderO3Header")
    cards = re.findall(r'<div class="clay kpi (k-[a-z]+[^"]*)"', body)
    assert len(cards) == 5, "org360 no longer renders five KPI cards: %s" % cards
    loud = [c for c in cards if "quiet" not in c]
    assert len(loud) == 1, (
        "org360 has %d emphatic faces (%s) — the face marks the ONE card that "
        "answers 'is something wrong right now'" % (len(loud), loud)
    )


def test_the_emphatic_tenant_card_is_the_volatile_consequential_one() -> None:
    """Which card keeps the face is the decision. A1's own test — the only KPI
    that is both volatile and consequential — picks Breaches over the window:
    ledger height only ever climbs, all-time breaches barely move, interactions
    and user counts are neither. It also lands on .k-orange, the hue the
    platform Overview already gives Policy breaches."""
    body = _js_func("renderO3Header")
    m = re.search(r'<div class="clay kpi (k-[a-z]+)"(?![^>]*quiet)', body)
    assert m, "the tenant page lost its emphatic card"
    assert m.group(1) == "k-orange", (
        "the emphatic face moved to %s; breaches are orange on the Overview and "
        "the two pages have to agree" % m.group(1)
    )
    card = body[body.index(m.group(0)):][:1200]
    assert "Breaches" in card and "all time" not in card, (
        "the emphatic face is no longer on the windowed Breaches card"
    )


def test_a_spark_inside_a_tenant_face_takes_that_faces_ink() -> None:
    """o3spBr drew --breach-bg — red — on the .k-orange face: 1.56:1 against the
    light stop and 1.01:1 against the deep one, i.e. gone across the bottom half
    of the very card that reports breach velocity. o3spInt hardcoded --foxink,
    which is right on a saturated face and invisible on a quiet one. The face
    publishes --spark for exactly this."""
    # the CALL SITES, not the whole body: the comment above them names the two
    # tokens it removed, and a body-wide grep finds the explanation and calls it
    # the defect. This file has shipped that mistake before.
    body = _js_func("renderO3Header")
    sparks = re.findall(r"chart\('(o3sp\w+)',\{[^}]*?color:([^}]+?)\}", body)
    assert len(sparks) == 2, "expected two tenant sparklines, found %s" % sparks
    for host, colour in sparks:
        assert colour.strip().startswith("_spark("), (
            "%s takes a hardcoded colour (%s) instead of its own face's --spark"
            % (host, colour.strip())
        )
    assert "getPropertyValue('--spark')" in _js_func("_spark")


def test_the_tenant_tab_bar_is_the_component_it_claims_to_be() -> None:
    """role="tablist" over six plain buttons was the only role="tab*" string in
    the file: no role="tab", no aria-selected, no aria-controls, no arrow keys.
    A screen reader heard "tab list", found generic buttons and was never told
    which was current — strictly worse than no role at all. Selection was also
    carried by .pri, the look of Suspend/Offboard directly above it."""
    markup = _nocomment(SRC)
    assert 'role="tablist"' not in markup, (
        "a tablist is back without tabs to own"
    )
    bar = re.search(r'<div class="segmented"[^>]*id="o3Tabs">(.*?)</div>',
                    _a2_page("org360"), re.S)
    assert bar, "the org360 tab bar is no longer the shared .segmented control"
    buttons = re.findall(r"<button[^>]*>", bar.group(1))
    assert len(buttons) == 6, "expected six sections, found %d" % len(buttons)
    for b in buttons:
        assert 'class="segbtn"' in b, "a section button left the component: %s" % b
        assert "aria-pressed=" in b, "a section button announces no state: %s" % b
    assert "setAttribute('aria-pressed'" in _js_func("o3Tab"), (
        "o3Tab no longer moves the pressed state — selection is back to a class"
    )
    assert 'id="o3Panel" aria-live="polite"' in markup, (
        "the panel swaps its whole contents silently again"
    )


def test_the_data_browser_has_exactly_one_modal_system() -> None:
    """A hand-rolled dialog sat 20 lines from the shared one, looking identical
    and sharing none of its behaviour: no role, no heading, no Esc, no focus
    move, no trap, no restore. Two modal systems on one surface, and the
    accessible one was the destructive one."""
    assert "dataEditModal" not in _nocomment(SRC), "the second modal is back"
    body = _js_func("openDataEdit")
    assert "openModal({" in body, "the edit dialog is hand-rolled again"
    assert "_dataRowLabel(" in body, "the edit dialog does not name its row"
    assert "_dataRowLabel(" in _js_func("deleteDataRow"), (
        "the delete confirmation does not name the row it destroys, against a "
        "table that truncates every cell at 60 characters"
    )


def test_a_failed_table_load_cannot_be_read_as_the_new_table() -> None:
    """Nothing here cleared the previous rows, so switching from a table that
    loaded to one that failed left table A's rows under table B's name, with the
    count still reading "N rows · table_a"."""
    body = _js_func("refreshData")
    fail = body[body.index("if(!r||!r.ok)"):]
    fail = fail[: fail.index("return")]
    assert "_dataClear()" in fail, (
        "the failure path no longer clears the previous table's rows"
    )
    clear = _js_func("_dataClear")
    for target in ("dataRows", "dataCount", "dataPager", "dataHead"):
        assert target in clear, "_dataClear leaves #%s behind" % target


def test_a_failed_allowlist_read_cannot_be_saved_as_an_empty_one() -> None:
    """The GET's failure path fell through to value='' — an empty textarea that
    reads exactly like "this tenant has no restrictions". Pressing Save then PUT
    an empty list and erased a live allow-list nobody ever saw."""
    body = _js_func("orgIp")
    assert "mIpSave" in body, "the allow-list Save button has no handle to hold"
    ok = body.index("if(r&&r.ok)")
    after = body[ok:]
    assert "disabled=true" in after, (
        "a failed read no longer disables saving — an empty box can still "
        "overwrite whatever is live"
    )


def test_a_name_bound_into_a_handler_is_escaped_for_javascript() -> None:
    """esc() is right for text between tags and wrong inside an inline handler:
    the attribute parser decodes &#39; back to an apostrophe BEFORE the JS
    parser sees it, so a tenant called "O'Brien Health" turned Offboard into a
    SyntaxError and the button simply stopped working. Verified in Chrome."""
    js = " ".join(_script_blocks())
    assert re.search(r"const jsq\s*=", js), "the JS-string escaper is gone"
    for bad in ("orgOffboard('${d.id}','${esc(d.name)}')",
                "keyRevoke('${O3_ID}','${k.id}','${esc(k.name)}')"):
        assert bad not in js, "a customer-supplied name is back on esc(): %s" % bad
    for good in ("${jsq(d.name)}", "${jsq(k.name)}"):
        assert good in js, "expected %s to reach the handler through jsq()" % good


def test_the_usage_heading_and_the_cards_agree_about_the_window() -> None:
    """The panel eyebrow said "usage · 30 days" while the cards beside it derived
    their window from usage.length. A tenant whose rollup holds nine days got a
    heading claiming thirty above two cards saying nine."""
    assert "usage · 30 days" not in _a2_page("org360"), (
        "the usage window is hardcoded in the markup again"
    )
    assert "o3UsageWindow" in _js_func("renderO3Header"), (
        "the heading no longer reads the window off the same array as the cards"
    )


def test_the_a2_surfaces_have_an_outline() -> None:
    """Overview and health each carry an <h1 class="sr-only">; these three had no
    heading at all, so the rotor returned nothing and linear Tab was the only
    way through the console's most-used pages."""
    for page in A2_PAGES:
        markup = _a2_page(page)
        assert re.search(r'<h1 class="sr-only">', markup), (
            "page-%s has no heading of any kind" % page
        )
        assert '<div class="panel-t">' not in markup, (
            "page-%s styles a <div> as a heading" % page
        )


def test_one_range_of_rows_is_stated_once() -> None:
    """The ledger and breach panels hand-rolled "N rows · 1–50" directly above
    foxPager's own "1–50 of N" — one fact, two formats, twelve pixels apart.
    The pager component was introduced to own exactly this."""
    for fn in ("o3LoadLedger", "o3LoadBreaches"):
        body = _js_func(fn)
        assert not re.search(r"\(d\.offset\+1\)\+'–'", body), (
            "%s() prints its own row range again, next to the pager's" % fn
        )


def test_an_empty_state_says_what_would_put_something_there() -> None:
    """.chart-empty .hint was styled and then used nowhere in the file — the
    slot for "what would fill this" existed and every one of the twelve empty
    states was a single bare sentence. On the tenant page that costs real
    information: "No usage recorded" cannot distinguish a new tenant from one
    whose SDK stopped shipping."""
    css = _css()
    assert ".chart-empty .hint{" in css
    for fn in ("renderO3Users", "renderO3Keys", "o3LoadLedger", "o3LoadBreaches",
               "renderO3Policy", "refreshData"):
        body = _js_func(fn)
        for empty in re.findall(r'<div class="chart-empty">(.*?)</div>', body, re.S):
            assert 'class="hint"' in empty, (
                "%s() has an empty state with no next step: %r"
                % (fn, empty[:70])
            )


def test_the_org_list_is_not_left_on_its_loading_placeholder() -> None:
    """The static markup ships `loading…` inside #orgRows. If loadOrgs cannot
    replace it, the sentence has to be replaced by something that admits it."""
    assert 'id="orgRows" aria-live="polite"' in _nocomment(SRC), (
        "the org table changes silently"
    )
    body = _js_func("loadOrgs")
    assert "faultRow(6," in body, (
        "the org table's failure path does not fill its own six columns"
    )


# ── A3 · the signal pages (traffic, security, audit + the ops group) ─────────
#
# _a2_page is reused rather than redefined: a second `def` in this file silently
# shadows the first, which has already happened once here (_kpi_cards, four
# guards reporting phantom results). One definition, whatever phase named it.

A3_PAGES = ("traffic", "security", "audit", "deadletter", "anchors", "alerts")

# The loader, the id of a body it must fill on failure, and the column count.
A3_LOADERS = {
    "loadTraffic": ("trafRows", 7),
    "loadSecurity": ("secWatch", 3),
    "loadDeadletter": ("deadRows", 7),
    "loadAnchors": ("anchorRows", 6),
    "loadAlerts": ("alertSummary", 0),
}


def test_a3_loaders_fail_visibly_instead_of_toasting_and_returning() -> None:
    """Nine loaders answered a dead endpoint with `toast(); return`, leaving
    "loading…" on screen forever — so a failure and a slow load were the same
    picture on a console whose entire job is telling those apart. A0 built
    fault()/faultRow() with a filter-preserving retry; A2 moved four loaders
    onto it. These five are the rest of the inherited set.

    loadAlerts was the worst: two bare returns and no toast at all, so the page
    that answers "what needs attention" reported a dead API as "nothing needs
    attention"."""
    for name in A3_LOADERS:
        body = _js_func(name)
        assert "fault(" in body or "faultRow(" in body, (
            "%s still fails without saying so" % name
        )
        assert not re.search(
            r"catch\s*\([^)]*\)\s*\{\s*toast\([^;]*\);?\s*return\s*\}", body
        ), "%s still toasts and returns into a permanent loading state" % name
        assert not re.search(r"catch\s*\([^)]*\)\s*\{\s*return\s*\}", body), (
            "%s still fails completely silently" % name
        )


def test_a3_loaders_never_await_the_api_without_a_catch() -> None:
    """loadTraffic had no try/catch at all, so a network error rejected out of
    the function as an unhandled rejection — four "—" KPIs and a permanent
    "loading…", without even the toast the others managed."""
    for name in A3_LOADERS:
        body = _js_func(name)
        for m in re.finditer(r"await\s+api\(", body):
            head = body[max(0, m.start() - 60):m.start()]
            assert re.search(r"try\s*\{\s*r\s*=\s*$", head), (
                "%s awaits api() outside a try — an outage becomes an unhandled "
                "rejection, not a visible failure" % name
            )


def test_a3_loaders_clear_the_pager_they_invalidate() -> None:
    """refreshData left the PREVIOUS table's rows under the NEW table's name.
    The pager is the same defect one level down: "1–25 of 240" printed over a
    fault row is a confident wrong answer, not a failure."""
    pagers = {
        "loadTraffic": ["trafPager"],
        "loadSecurity": ["secWatchPager", "secRecentPager"],
        "loadDeadletter": ["deadPager"],
        "loadAnchors": ["anchorPager"],
        "loadAlerts": ["alertPager"],
    }
    for name, ids in pagers.items():
        body = _js_func(name)
        assert re.search(r"innerHTML\s*=\s*''", body), (
            "%s never clears anything on failure" % name
        )
        for pid in ids:
            assert pid in body, (
                "%s leaves #%s stale over its fault row" % (name, pid)
            )


def test_the_ops_subnav_is_navigation_and_not_a_primary_action() -> None:
    """Register #68. This row is ONE component on four ops pages and it wore
    .btn.sm.pri — the exact plate `requeue` and `re-anchor` wear, real mutations
    one click away. On the alerts page it inverted: the "you are here" pill was
    .pri while `acknowledge`, the actual mutation, was a plain .btn."""
    navs = re.findall(
        r'<nav class="segmented opsnav"[^>]*>.*?</nav>', _nocomment(SRC), re.S
    )
    assert len(navs) == 4, "expected the ops sub-nav on 4 pages, found %d" % len(navs)
    for nav in navs:
        assert 'aria-label="Operations"' in nav, "the ops sub-nav is an unnamed landmark"
        buttons = re.findall(r"<button[^>]*>", nav)
        assert len(buttons) == 4, "expected 4 ops destinations, found %d" % len(buttons)
        assert nav.count('aria-current="page"') == 1, (
            "an ops sub-nav marks %d current pages" % nav.count('aria-current="page"')
        )
        for b in buttons:
            assert 'class="segbtn"' in b, "an ops link left the component: %s" % b
            assert 'type="button"' in b, "an ops link can submit a form: %s" % b
            assert "btn sm pri" not in b, "an ops link wears the mutation plate again"


def test_the_ops_subnav_current_state_is_not_the_mutation_plate() -> None:
    """The fix that looks sufficient and is not. Moving the row onto .segmented
    alone would have kept the collision: .segbtn[aria-pressed="true"] paints the
    same linear-gradient(150deg,--fox-hi,--fox) as .btn.pri. The current item is
    marked by a rail and a lift instead, so orange still says "you are here"
    without also saying "this writes"."""
    pri = _scope(".btn.pri{")
    cur = _scope('.segmented.opsnav .segbtn[aria-current="page"]{')
    assert "linear-gradient(150deg,var(--fox-hi),var(--fox))" in pri, (
        "the primary plate moved — re-measure what the nav must not look like"
    )
    assert "linear-gradient(150deg,var(--fox-hi),var(--fox))" not in cur, (
        "the ops sub-nav is wearing the primary-action fill again"
    )
    rail = _scope('.segmented.opsnav .segbtn[aria-current="page"]::after{')
    assert "var(--fox)" in rail, "the current ops page has no state indicator at all"


def test_traffic_reserves_its_face_for_the_card_that_answers_the_question() -> None:
    """#65, applied to the last KPI row that never adopted it. All four traffic
    cards were saturated — the pre-A1 state preserved, where nothing is primary
    and the eye gets no path. Errors is the card that answers "is something
    wrong right now", so Errors keeps the face and leads the row."""
    cards = _kpi_cards()
    traffic = [c for c in cards if any(k in c for k in ("tErr", "tMkt", "tApp", "tAdm"))]
    assert len(traffic) == 4, "expected 4 traffic KPI cards, found %d" % len(traffic)
    loud = [c for c in traffic if "quiet" not in c.split(">")[0]]
    assert len(loud) == 1, "traffic shows %d emphatic cards, not 1" % len(loud)
    assert 'id="tErr"' in loud[0], "the traffic face is not on Errors"
    order = _a2_page("traffic").split('<div class="clay kpi ')[1:]
    assert 'id="tErr"' in order[0], "the diagnosis no longer leads the row"


def test_every_a3_page_names_itself() -> None:
    """Five pages carried an <h1 class="sr-only">; these six carried none, so
    heading navigation landed nowhere and the page name existed only as a <b>
    in the crumb."""
    for p in A3_PAGES:
        assert '<h1 class="sr-only">' in _a2_page(p), "page-%s has no heading" % p


def test_a3_tables_bind_their_columns() -> None:
    """Under 760px .tbl.cardify removes the header row entirely, so scope +
    data-label are the only things left tying a value to its column. The empty
    action header is the other half: "" is a cell a screen reader lands on and
    cannot announce."""
    for p in A3_PAGES:
        for m in re.finditer(r"<th\b([^>]*)>(.*?)</th>", _a2_page(p), re.S):
            attrs = m.group(1)
            text = re.sub(r"<[^>]+>", "", m.group(2)).strip()
            assert "scope=" in attrs, "page-%s has an unbound column header" % p
            assert text, "page-%s has a nameless column header" % p


def test_a3_result_regions_announce_when_they_change() -> None:
    """Typing in #trafPath rewrote the whole table and said nothing; so did
    requeueing a row. Only the toast spoke, and only for mutations."""
    markup = _nocomment(SRC)
    for node in ("trafRows", "auRows", "deadRows", "anchorRows",
                 "alertRows", "secWatch", "secRecent"):
        assert re.search(r'id="%s"[^>]*aria-live="polite"' % node, markup), (
            "#%s changes silently" % node
        )


def test_the_ops_mutations_cannot_be_fired_twice() -> None:
    """.btn[aria-busy="true"] shipped in A0 with a spinner, cursor:progress AND
    pointer-events:none, and nothing in the file ever set it. re-anchor writes
    to a public chain: a double click published twice."""
    assert "pointer-events:none" in _scope('.btn[aria-busy="true"]{'), (
        "the busy state no longer blocks the second click"
    )
    guard = _js_func("busy")
    assert "setAttribute('aria-busy','true')" in guard, "busy() never marks the button"
    assert "finally" in guard and "removeAttribute('aria-busy')" in guard, (
        "busy() can leave a control permanently inert when the call throws"
    )
    for fn in ("requeueDeadletter", "_doReanchor", "ackAlert"):
        assert re.search(r"busy\(btn\s*,", _js_func(fn)), "%s is not guarded" % fn
    markup = _nocomment(SRC)
    assert "requeueDeadletter('${jsq(x.id)}',this)" in markup, "requeue passes no button"
    assert "ackAlert('${jsq(x.id)}',this)" in markup, "acknowledge passes no button"
    assert "_doReanchor('${jsq(id)}',this)" in markup, "re-anchor passes no button"


def test_a_busy_label_survives_the_plate_it_sits_on() -> None:
    """A0 shipped .btn[aria-busy] with color:var(--muted), which measures ~5:1
    on a plain .btn and 1.45:1 / 1.36:1 on .btn.pri — the plate `requeue` and
    `re-anchor` actually use. Nothing called the state until A3, so nothing had
    ever measured it against the fill it sits on. Mid-flight is precisely when
    the label matters, so each coloured intent keeps its own ink."""
    css = _css()
    assert "color:var(--muted)" in _scope('.btn[aria-busy="true"]{'), (
        "the plain busy label changed — re-measure the coloured overrides"
    )
    for sel, ink in (('.btn.pri[aria-busy="true"]{', "var(--on-pri)"),
                     ('.btn.danger[aria-busy="true"]{', "#fff"),
                     ('.btn.safe[aria-busy="true"]{', "var(--safe-tx)")):
        assert sel in css, "%s lost its busy ink and dissolves into its plate" % sel
        assert ink in _scope(sel), "%s no longer carries a legible label" % sel


def test_re_anchoring_confirms_before_it_writes_to_a_public_chain() -> None:
    """The only action here that writes to a system Foxy does not control, and
    it fired from an 11px pill, 25 to a page — while editing a row in the data
    browser opened a modal."""
    body = _js_func("reanchor")
    assert "openModal(" in body, "re-anchor still writes on a single click"
    assert "api(" not in body, "re-anchor still writes before the operator confirms"
    assert "cannot be undone" in body, "the confirm does not say what is permanent"
    assert "jsq(x.org_name)" in _nocomment(SRC), (
        "a tenant called O'Brien Health breaks the re-anchor handler"
    )


def test_the_audit_range_cannot_be_impossible() -> None:
    """since > until is an incoherent query and it answered "no matching
    actions" — telling the operator their filters found nothing when the truth
    is the question could not match. Export reused the same params, so the
    impossible range exported too, as a silent empty CSV in a new tab."""
    rng = _js_func("auditRange")
    assert "u.min=s.value" in rng and "s.max=u.value" in rng, (
        "the two dates no longer bound each other"
    )
    assert "auNote" in rng, "an inverted range gives no feedback"
    assert re.search(r"if\(!bad\)loadAudit\(\)", rng), (
        "an impossible range is still sent to the API"
    )
    exp = _js_func("auditExport")
    assert "s>u" in exp.replace(" ", ""), "Export CSV still exports the impossible range"


def test_a_finger_gets_a_finger_sized_ops_control() -> None:
    """The rule claimed SC 2.5.5 and delivered 40px to .btn.sm — requeue,
    re-anchor, acknowledge and every filter clear. The pager was worse: a hard
    30x30 with no coarse-pointer rule at all, on all six pages."""
    css = _css()
    # From .btn.sm, not from the top: the FIRST @media (hover:none) in the file
    # is .kpi .kinfo's, and a guard anchored there measures the wrong block and
    # passes for the wrong reason.
    at = css.index("@media (hover:none){", css.index(".btn.sm{"))
    block = css[at:css.index("\n}", at)]
    assert re.search(r"\.btn\.sm\{min-height:44px", block), ".btn.sm is under the target"
    assert re.search(r"\.pager button\{min-width:44px;height:44px", block), (
        "the pager is back under the target"
    )
    assert "min-height:44px" in _scope(".segmented.opsnav .segbtn{"), (
        "the ops sub-nav is under the target"
    )


def test_the_scrollable_region_is_reachable_by_keyboard() -> None:
    """.scrolly is the file's only scrollable region and had no tabindex, so a
    keyboard user could reach neither the scrollbar nor the rows below the fold
    (SC 2.1.1)."""
    m = re.search(r'<div class="twrap scrolly"([^>]*)>', _nocomment(SRC))
    assert m, "the scrollable region is gone"
    assert 'tabindex="0"' in m.group(1), "the scroll region cannot be focused"
    assert "aria-labelledby=" in m.group(1), "the focusable region has no name"


def test_traffic_states_the_window_it_actually_searched() -> None:
    """The filters run client-side over the last 200 requests and nothing said
    so: an operator greps for /v1/logs, sees 3 hits and reads that as 3 — on a
    page whose KPI says tens of thousands."""
    assert re.search(r'id="trafCount"[^>]*role="status"', _nocomment(SRC)), (
        "the traffic result count no longer announces"
    )
    body = _js_func("renderTraffic")
    assert "of the last" in body, "the searched window is unstated again"
    assert "trafClear()" in body, "the empty state no longer offers its own recovery"


def test_an_alert_level_does_not_default_to_red() -> None:
    """opsChip's else-branch is "unrecognised, assume the worst" — right for a
    status probe, wrong for an alert LEVEL. `info` rendered as a red pill, and
    the alerts table's own model default is level="info"."""
    body = _js_func("opsChip")
    assert "s==='info'?'info'" in body, "an info alert still paints red"
    assert "s==='critical'?'bad'" in body, "critical is back on the unnamed else-branch"
    # A4 added `revoked` to this same branch, which broke the literal match
    # while strengthening the claim. The assertion is the claim, not the
    # spelling: both original names must still land in the dim branch, and the
    # branch must still be the one that resolves to 'dim'.
    dim = re.search(r"((?:s==='[a-z_]+'\|\|)*s==='[a-z_]+')\?'dim'", body)
    assert dim, "the dim branch is gone — absence has no home in the mapping"
    names = set(re.findall(r"s==='([a-z_]+)'", dim.group(1)))
    assert {"never", "not_applicable"} <= names, (
        "absence is shouting in a status hue again — dim now covers %s" % sorted(names)
    )


# ── A4 · revenue and campaigns — the money pages ─────────────────────────────

A4_PAGES = ("revenue", "campaigns")


def _js_nocomment() -> str:
    """Every inline script with block comments removed.

    Not cosmetic. This file argues with itself in prose, and the A4 comments
    quote `confirm()`, `offset=NaN` and `toast(); return` precisely because
    those are the things being removed — a guard that greps raw script source
    finds the explanation and calls it the defect. Same trap _nocomment and
    _css were written for, on the third of the three languages in this file.
    """
    return re.sub(r"/\*.*?\*/", "", "\n".join(_script_blocks()), flags=re.S)


def test_a4_pages_name_themselves() -> None:
    """revenue and campaigns were the last two pages with no heading at all, so
    heading navigation landed nowhere and the page name existed only as a <b>
    in the crumb."""
    for p in A4_PAGES:
        assert '<h1 class="sr-only">' in _a2_page(p), "page-%s has no heading" % p


def test_revenue_reserves_its_face_for_the_card_that_answers_the_question() -> None:
    """#65, on the last KPI row that never adopted it. All three revenue cards
    were saturated — the pre-A1 state, where the page named "revenue" gave a
    subscription count and a trial count the same weight as the revenue."""
    cards = [c for c in _kpi_cards() if any(k in c for k in ("rvRev30", "rvActive", "rvTrial"))]
    assert len(cards) == 3, "expected 3 revenue KPI cards, found %d" % len(cards)
    loud = [c for c in cards if "quiet" not in c.split(">")[0]]
    assert len(loud) == 1, "revenue shows %d emphatic cards, not 1" % len(loud)
    assert 'id="rvRev30"' in loud[0], (
        "the revenue face is not on Revenue · 30 days — the one card the page is named after"
    )


def test_the_mask_control_is_legible_on_the_face_it_lands_on() -> None:
    """A style with a new caller has never been measured. sens() writes into the
    .kval of a saturated card, and .sens paints its dots in --muted2 and its eye
    in --muted — greys tuned against a flat --surf. On this page's own face they
    measured 1.07:1 and 1.04:1 at the deep stop: the headline number and the
    only control that uncovers it, both invisible on their own card."""
    css = _css()
    for sel in (
        r"\.kpi:not\(\.quiet\) \.face \.sens\.masked \.sens-val\{color:var\((--[a-z0-9-]+)\)\}",
        r"\.kpi:not\(\.quiet\) \.face \.sens-btn\{color:var\((--[a-z0-9-]+)\)\}",
    ):
        m = re.search(sel, css)
        assert m, "the on-face mask/reveal override is gone: %s" % sel
        ink = _token(m.group(1))
        worst, stop = min((_ratio(ink, s), s) for s in _face_stops())
        assert worst >= 4.5, (
            "the on-face mask control uses %s (%s), only %.2f:1 against %s"
            % (m.group(1), ink, worst, stop)
        )
    # If either override lost its :not(.quiet) scope it would repaint a
    # near-black control on a tinted --surf2 — A1's failure, pointed the other
    # way — so the count of scoped rules is part of the assertion.
    assert css.count(".kpi:not(.quiet) .face .sens") == 4, (
        "the on-face .sens overrides are no longer the four scoped rules"
    )


def test_the_revenue_page_says_when_it_cannot_answer() -> None:
    """The last toast()-and-return loader in any phase's scope, and the worst,
    because this page is never blank on a failed RELOAD: measured under Node,
    a 500 left the previous poll's money on screen with #sePager still reading
    "1-25 of 240" over it."""
    body = _js_func("loadRevenue")
    assert "toast(" not in body.split("const d=")[0], "revenue still fails by toast alone"
    for slot in ("rvMonthly", "rvByPlan"):
        assert re.search(r"\$\('%s'\)\.innerHTML=fault\(" % slot, body), (
            "%s does not report the failure" % slot
        )
    assert "loadRevenue()" in body, "the revenue fault offers no retry"
    assert re.search(r"\$\('sePager'\)\.innerHTML=''", body), (
        "a stale '1-25 of 240' can still sit above a fault row"
    )


def test_a_new_stripe_filter_is_a_new_question() -> None:
    """_fauxCommit invokes data-onchange as window[cb](root), so naming
    loadStripeEvents there put the select ELEMENT into its `page` parameter and
    the query string went out as offset=NaN. Driven through a real option
    click, picking "failed" answered "no stripe events with status failed"
    while three failed events existed."""
    m = re.search(r'id="seStatus"[^>]*data-onchange="([a-zA-Z]+)"', _nocomment(SRC))
    assert m, "the Stripe status select is gone"
    assert m.group(1) != "loadStripeEvents", (
        "the status select still hands the select element to a `page` parameter"
    )
    body = _js_func(m.group(1))
    assert "pgReset('stripe')" in body, "a new Stripe filter still keeps the old offset"
    assert "loadStripeEvents()" in body, "the filter no longer reloads the table"


def test_replaying_a_stripe_webhook_is_confirmed_first() -> None:
    """Replay re-runs a webhook inside Stripe — A3's re-anchor case: a write to
    a system Foxy does not control, and one that can move money. It had no
    confirmation and no busy guard, and three rapid clicks sent three POSTs."""
    gate = _js_func("replayStripe")
    assert "openModal(" in gate, "replay fires straight at Stripe with no confirmation"
    assert "api(" not in gate, "the confirmation step still calls the API itself"
    assert "_doReplayStripe(" in gate, "the modal does not lead anywhere"
    run = _js_func("_doReplayStripe")
    assert "await busy(btn," in run, (
        "replay is not wrapped in busy() — the aria-busy it sets IS the double-submit guard"
    )
    assert "try{" in run and "catch(e)" in run, (
        "a dropped connection still throws past the caller with nothing on screen"
    )


def test_a_staff_action_is_confirmed_in_the_product_not_the_browser() -> None:
    """A0 built the modal to replace prompt()/confirm(). revokeCampaign was the
    last native confirm() in the file, and it could not name which campaign it
    was about."""
    assert "confirm(" not in _js_nocomment(), "a native confirm() is back"
    gate = _js_func("revokeCampaign")
    assert "openModal(" in gate and "_doRevokeCampaign(" in gate, (
        "revoke no longer confirms through the product's own modal"
    )
    assert "esc(label" in gate, "the confirmation cannot say which campaign it is about"
    assert "await busy(btn," in _js_func("_doRevokeCampaign"), "revoke can double-submit"


def test_the_campaign_pager_is_not_inside_the_element_it_paginates() -> None:
    """#campaignPager was a CHILD of #campaignRows, and renderCampaigns sets
    campaignRows.innerHTML — so every render deleted the pager the line above
    had just drawn. With 23 campaigns the page showed 10 and offered no way to
    reach the other 13."""
    # Written first as a non-greedy slice from campaignRows to campaignPager,
    # which stayed GREEN when the pager was mutated back inside: `.*?` stops at
    # the first pager either way, so the pattern could not tell a sibling from
    # a child. Depth has to be walked, not matched.
    page = _a2_page("campaigns")
    i = page.index('<div id="campaignRows"')
    depth, j = 0, i
    while j < len(page):
        if page.startswith("<div", j):
            depth += 1
            j += 4
        elif page.startswith("</div>", j):
            depth -= 1
            j += 6
            if depth == 0:
                break
        else:
            j += 1
    assert depth == 0, "#campaignRows never closes"
    assert 'id="campaignPager"' not in page[i:j], (
        "the pager is inside #campaignRows again — renderCampaigns rewrites that "
        "element wholesale, so the pager it just drew is deleted on every render"
    )
    assert 'id="campaignPager"' in page[j:], "the pager is gone from the campaigns page"


def test_every_campaign_field_has_exactly_one_label() -> None:
    """Each field was `<label><label for=…>…</label><input></label>`. The outer
    bare label wraps the control, so every input reported TWO labels and a
    screen reader announced the field name twice with no authoritative one."""
    page = _a2_page("campaigns")
    assert "<label" in page, "the create form lost its labels entirely"
    assert not re.search(r"<label[^>]*>\s*<label", page), "a campaign field is double-labelled"
    for field in ("campaignOfferId", "campaignLabel", "campaignCode",
                  "campaignCredits", "campaignDays", "campaignMax"):
        assert page.count('for="%s"' % field) == 1, "%s is not labelled exactly once" % field


def test_the_stripe_table_binds_its_columns() -> None:
    """Under 760px .tbl.cardify removes the header row, so scope + data-label
    are the only things left tying a value to its column. All six headers here
    carried neither half."""
    head = re.search(r"<thead><tr>(.*?)</tr></thead>", _a2_page("revenue"), re.S).group(1)
    ths = re.findall(r"<th\b([^>]*)>", head)
    assert len(ths) == 6, "the Stripe events table no longer has six columns"
    assert all('scope="col"' in t for t in ths), "a Stripe events column does not name itself"


def test_the_campaign_list_says_what_to_do_next() -> None:
    """"No campaigns yet." named the absence and stopped, on a page where a
    superadmin can act on it from the card directly above."""
    body = _js_func("renderCampaigns")
    assert "can('superadmin')" in body, (
        "the empty state gives the same advice to someone who cannot act on it"
    )
    assert "Issue a redemption code" in body, "the empty state names no next step"


def test_the_shown_once_panel_is_not_inside_an_inline_element() -> None:
    """createCampaign injects a .clay panel carrying two unrecoverable secrets.
    Its host was a <span> in a flex row beside the button, so the one panel that
    must be read carefully was invalid markup squeezed into a column."""
    m = re.search(r'<(\w+) id="campaignOut"', _a2_page("campaigns"))
    assert m and m.group(1) == "div", "the shown-once panel is back inside an inline host"
    assert "clay pad" in _js_func("createCampaign"), "the shown-once panel is gone"


def test_campaign_writes_cannot_double_submit() -> None:
    """createCampaign mints a bearer credential: a double-click either burns the
    typed code against a 409 or issues two campaigns, with nothing on screen to
    say the first click was in flight."""
    for fn in ("createCampaign", "loadCampaigns"):
        assert "busy(btn," in _js_func(fn), "%s has no in-flight guard" % fn
    page = _a2_page("campaigns")
    assert "createCampaign(this)" in page and "loadCampaigns(this)" in page, (
        "the guard has no caller — the button never hands itself to busy()"
    )


def test_a_revealed_figure_is_not_truncated_on_the_card_it_leads() -> None:
    """The colour fix made this one visible. .kval is clamp(22px,2.2vw,30px) and
    the eye and copy take 52px beside it, so at 375px the revealed "$42,189"
    measured scrollWidth 89 into clientWidth 46 and drew "$42,…" — the operator
    taps the eye and is handed a truncated number. Measured again after the fix:
    89 into 89, clipped=false."""
    css = _css()
    assert ".kpi .face .sens{flex-wrap:wrap}" in css, (
        "the on-face value row cannot wrap, so the controls squeeze the figure again"
    )
    assert re.search(r"\.kpi \.face \.sens-val\{[^}]*text-overflow:clip", css), (
        "a revealed figure elides on the card it leads"
    )
    # Scoped to .face deliberately: in a table cell sens() is showing a
    # truncated id and the short form IS the design.
    #
    # Anchored to the start of a rule, not searched as a substring. `_scope(
    # ".sens-val{")` was written first and it matched `.kpi .face .sens-val{`
    # — the override — because that rule is declared earlier in the file. The
    # guard then read the override and reported the base rule as broken. Same
    # shadowing trap the comment-stripping helpers exist for, one level down.
    base = re.search(r"(?:^|\})\.sens-val\{([^}]*)\}", css, re.M)
    assert base, "the base .sens-val rule is gone"
    assert "text-overflow:ellipsis" in base.group(1), (
        "the base .sens-val stopped eliding — that is the table's design, not a defect"
    )


def test_a_live_campaign_does_not_render_as_an_alarm() -> None:
    """Only a render showed this. Neither `active` nor `revoked` was mapped, so
    both fell to opsChip's else — "unrecognised, assume the worst" — and the
    campaigns list painted every row the same red, ACTIVE included. The two
    names are the whole vocabulary: evaluation_campaigns.status is commented
    `active|revoked` in models.py and admin_campaigns.py writes nothing else."""
    body = _js_func("opsChip")
    assert re.search(r"s==='ok'\|\|s==='confirmed'\|\|s==='active'\?'safe'", body), (
        "an active campaign still paints red — active is not grouped with the healthy states"
    )
    assert re.search(r"s==='never'\|\|s==='not_applicable'\|\|s==='revoked'\?'dim'", body), (
        "a switched-off campaign is shouting in a status hue again"
    )
    # A branch with no caller has never been measured, and the vocabulary is two
    # words — a third would be invention, not coverage.
    assert "s==='expired'" not in body, "opsChip maps a campaign status the API never emits"


def test_no_mojibake_survives() -> None:
    """The campaigns list shipped `Â·` twice — a UTF-8 middot re-decoded as
    cp1252 — so every row read "judges-july Â· 250 credits". They were the only
    two in the file, which is why a whole-file guard is cheap."""
    assert not re.search(r"[ÂÃ]\S|â€.", SRC), "mojibake is back in the file"


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
