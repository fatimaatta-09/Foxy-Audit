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
# This group used to guard .kpi::before, the 3px accent bar. R1 deleted the bar:
# a gradient rule across the top of every rounded card is on the design skill's
# own list of generated patterns, and the owner read it that way. The guards
# below now defend its ABSENCE, which is the harder thing to keep — an accent
# bar is what a stylesheet grows back on its own.

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


def test_the_accent_bar_is_gone() -> None:
    """R1. The bar was a 3px --k2 -> --k gradient pinned across the top of all 21
    cards. Nothing about it encoded anything the face was not already saying —
    the whole card IS the card's hue — so it was decoration applied uniformly,
    which is the exact shape of the thing this pass was asked to remove."""
    # Comments stripped: the stylesheet explains the removal in prose and names
    # the selector while doing it, and a raw grep reads that documentation as
    # the defect. This file has failed on its own comments twice.
    css = re.sub(r"/\*.*?\*/", "", _style_block(), flags=re.S)
    assert ".kpi::before" not in css, "the accent bar is back on the KPI card"
    assert not re.search(r"\.kpi[^{}]*::(before|after)\s*{", css), (
        "a decorative pseudo has taken the slot the accent bar vacated"
    )


def test_no_pseudo_rebuilds_the_bar_on_a_shared_ancestor() -> None:
    """.clay is .kpi's other class. A 3px gradient strip added there paints the
    same bar on every KPI without ever naming .kpi, which is how this would come
    back — as a card-level flourish rather than a KPI decision."""
    css = re.sub(r"/\*.*?\*/", "", _style_block(), flags=re.S)
    for m in re.finditer(r"([^{}]*)::before[^{}]*\{([^}]*)\}", css):
        sel = m.group(1).strip().splitlines()[-1].strip()
        if ".clay" not in sel and ".kpi" not in sel:
            continue
        body = m.group(2)
        assert not ("gradient" in body and re.search(r"height:\s*[1-6]px", body)), (
            f"{sel}::before is the accent bar under another name: {body}"
        )


def test_face_and_beam_are_elements_not_pseudos() -> None:
    """Not for the bar's sake any more — the beam has to sit BEHIND the face and
    the face has to hold real children, and a pseudo can do neither."""
    css = _style_block()
    assert ".kpi .face{" in css
    assert ".kpi .beam{" in css
    for banned in (".kpi::before{", ".kpi::after{",
                   ".kpi .face::before{", ".kpi .face::after{"):
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
    # 18 card glyphs. g-info went with R1: the card's own glyph IS the (i) now,
    # so there is no second icon to draw and no symbol left to draw it from.
    assert len(ids) == 18, f"expected 18 glyphs, found {len(ids)}: {sorted(ids)}"
    assert "g-info" not in SRC, (
        "the (i) icon is back — which means something is drawing two icons in "
        "the one 38px slot again"
    )
    used = set(re.findall('href="#(g-[a-z]+)"', SRC))
    assert used <= ids, f"a card points at a glyph that does not exist: {sorted(used - ids)}"
    assert ids <= used, f"a symbol is drawn by nothing: {sorted(ids - used)}"
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
    # loader built the nineteenth. 19 since M3d added the Paddle events feed
    # beside the Stripe one on the revenue page. Back to 18 since S1 removed
    # the Stripe feed, leaving Paddle as the only webhook log. The count has to
    # move with a legitimate table; the real assertion — that every one of them
    # opts in to cardify — is the loop below and is unchanged.
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


def _base_status_rule() -> str:
    """The BASE status-mark rule, anchored so the on-face override cannot shadow
    it. A plain substring search matches the descendant selector too; this file
    has already shipped one guard that read an override and reported the base as
    broken."""
    m = re.search(r"(?:^|\})\.chip\{([^}]*)\}", _css(), re.M)
    assert m, "the base status rule is gone"
    return m.group(1)


def test_the_status_chip_is_a_margin_mark_not_a_pill() -> None:
    """Three passes argued about making the pill legible — tint, then solid fill,
    then an inverted plate — and none asked whether a pill was the right object.
    The defect that survived all three: a Status column is six rows of the
    expected state and one of the exception, every one a saturated filled pill,
    so the exception had nothing left to be louder with.

    R2 removes the container. Named by property rather than by literal here,
    because a guard that greps this file greps the explanation above it."""
    base = _base_status_rule()
    for prop, why in (("background:none", "a fill is being drawn again"),
                      ("box-shadow:none", "the pill's boundary shadow came back"),
                      ("border:0", "the pill outline came back")):
        assert prop in base, f"{why}: {base}"
    assert re.search(r"border-left:3px solid", base), "the mark lost its rule: " + base
    assert "letter-spacing:.12em" in base, "the mark lost its tracking"
    # the padding must be one-sided; a symmetric box is a pill by another name
    pad = re.search(r"padding:([^;}]+)", base)
    assert pad and pad.group(1).strip().startswith("0 0 0"), (
        "the mark padded itself back into a box: " + (pad.group(1) if pad else "none")
    )


def test_nothing_draws_a_container_around_a_status() -> None:
    """This replaces a guard that asserted a boundary shadow EXISTED, to stop the
    solid pill dissolving into a hero face. There is no pill to dissolve now, and
    that guard kept passing after the change for the wrong reason: the property
    it searched for is still present in the rule, set to none. Assert the
    rendered intent instead of the token."""
    # EVERY rule that names the component, not a hand-listed three. Rendering the
    # pre-change comparison showed why: the on-face override no longer restates
    # background, so a fill added to any MODIFIER is not reset by it, and a
    # hand-listed set of parent rules would never have looked there.
    rules = [(sel.strip(), body) for sel, body in re.findall(r"([^{}]+)\{([^}]*)\}", _css())
             if re.search(r"\.chip", sel)]
    assert len(rules) >= 6, f"only {len(rules)} status rules found — the sweep is not seeing them"
    for sel, body in rules:
        assert not re.search(r"background:\s*(?!none)\S", body), f"a status grew a fill: {sel}"
        assert not re.search(r"box-shadow:\s*(?!none)\S", body), f"a status grew a plate: {sel}"
        assert "border-radius:var(--r-" not in body, f"a status took the pill radius: {sel}"


def test_the_dim_chip_does_not_shout_in_a_status_colour() -> None:
    """`never` and `not_applicable` mark absence, not a state. A0's reasoning
    survives R2 unchanged — the mark carries no meaning to lose, the word does —
    only the word is a grey on the panel now rather than ink on a plate."""
    rule = _scope(".chip.dim{")
    for status in ("--safe-bg", "--breach-bg", "--warn-bg", "--info-bg",
                   "--ok", "--danger", "--warnc", "--infoc"):
        assert status not in rule, f"the absence mark borrowed {status}"
    assert "color:var(--muted2)" in rule and "--line2" in rule, rule
    # absence must stay quieter than the quietest real status
    for theme in ("dark", "light"):
        for panel in ("--surf", "--surf2"):
            r = _ratio(_token("--muted2", theme), _token(panel, theme))
            assert r >= 4.5, f"{theme} absence word is {r:.2f}:1 on {panel}"


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
    """It is never hidden now, so nothing has to keep it focusable — but
    display:none or visibility:hidden would still take it out of the tab order
    and make it pointer-only, which is not reachable."""
    rule = _css_rule(".kpi .kinfo")
    assert "display:none" not in rule and "visibility:hidden" not in rule, \
        "the (i) is hidden in a way that removes it from the tab order"
    assert 'tabindex="-1"' not in SRC[SRC.index('class="kinfo"'):][:400]
    assert not re.search(r"opacity:\s*0[;}]", rule), (
        "the control is hidden at rest again — the glyph IS the control, so "
        "hiding it hides the card's own mark"
    )


def test_two_icons_never_share_the_one_slot() -> None:
    """THE R1 DEFECT, and the only one here you cannot see in a static capture.

    `.kpi:hover .gly{opacity:0}` faded the glyph out while `.kpi:hover .kinfo
    {opacity:1}` faded an (i) in, both absolutely positioned at the same
    right/top. For the whole transition both drew at partial alpha on top of
    each other. The fix is structural: ONE icon, the card's own, inside the
    button. So the guard is structural too — the glyph must be a CHILD of the
    control, and nothing may animate it out from under itself."""
    css = _css()
    for card in _kpi_cards():
        m = re.search(r'<button[^>]*class="kinfo".*?</button>', card, re.S)
        assert m, "a card lost its (i) control"
        assert 'class="gly"' in m.group(0), (
            "the glyph is a sibling of the control again, not its child — that "
            "is the overlap, restored: " + card[:120]
        )
        assert card.count('class="gly"') == 1, "a card draws two glyphs"
    # ...and no rule may fade the one remaining mark away under any state.
    for bad in re.findall(r"[^{}]*\.gly[^{}]*\{[^}]*\}", css):
        assert not re.search(r"opacity:\s*0[;}]", bad), (
            "a rule still fades the glyph out: " + bad.strip()
        )
    assert ".gly{opacity:0}" not in css.replace(" ", "")


def test_the_control_says_it_is_a_control() -> None:
    """The glyph used to be decoration and the (i) carried the affordance. Now
    the glyph is the target, so it has to earn the pointer on its own."""
    rule = _css_rule(".kpi .kinfo")
    assert "cursor:help" in rule or "cursor:pointer" in rule, (
        "the (i) target gives no cursor affordance"
    )
    css = _css()
    hov = css[css.index(".kpi:hover .kinfo"):]
    hov = hov[: hov.index("}") + 1]
    assert ".kinfo:focus-visible" in hov, "keyboard focus does not light the control"
    assert "background:" in hov, (
        "hover changes nothing visible on the control — the card lifting is not "
        "an affordance for the thing inside it"
    )
    # Anchored on the STANDALONE rule. `.kpi .kinfo:focus-visible` also appears
    # as the third selector of the hover group two lines above, and _scope()
    # takes the first match — which measures the wrong block and would pass on
    # a card that has no ring at all.
    ring = re.search(r"^\.kpi \.kinfo:focus-visible\{([^}]*)\}", css, re.M)
    assert ring and "outline:2px solid" in ring.group(1), "the control has no focus ring"


def test_the_info_button_has_a_touch_path() -> None:
    """There is no hover on a phone. Nothing has to appear now — the glyph is
    always there — but the target must reach 44px, and it must be openable by
    tap: .datatip's focusin path is :focus-visible gated, which a tap does not
    satisfy."""
    css = _style_block()
    i = css.index("@media (hover:none)")
    block = css[i: css.index("\n}", i)]
    assert ".kpi .kinfo" in block, block
    assert "44px" in block, "the touch target is under the 44px minimum"
    assert "opacity:0" not in block, "something hides the card's mark on touch"
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

def test_a_status_on_a_hero_face_is_a_margin_mark_not_a_pill() -> None:
    """A0's problem was real: a solid status fill measured 1.005:1 against the
    face behind it. A0's answer was to invert the fill to a near-black --foxink
    plate, which fixed the number by putting a second card-shaped object on top
    of a card. R1 removes the object instead of re-colouring it — a fill you do
    not draw cannot dissolve into anything."""
    rule = _scope(".kpi .face .chip{")
    # R2 reconciliation: this rule used to restate six declarations purely to UNDO
    # a filled base. The base is a mark now, so those moved down to it and the
    # cover for "no container on a face" is test_nothing_draws_a_container_around
    # _a_status, which checks the base AND both overrides. What is asserted here
    # is only what a face genuinely needs differently from a panel.
    assert re.search(r"border-left:4px solid var\(--foxink\)", rule), (
        "the margin mark lost its rule: " + rule
    )
    assert not re.search(r"background|box-shadow", rule), (
        "the on-face rule is restating what the base already says — that is the "
        "half-definition R2 removed: " + rule
    )
    assert "color:var(--foxink)" in rule, "the word is not in the face ink"
    assert re.search(r"font-weight:8\d\d", rule) and "letter-spacing:.14em" in rule, (
        "the word lost the weight and tracking that let it stand without a "
        "container: " + rule
    )
    # ...and nothing may put the plate back under a narrower selector.
    css = _css()
    assert "background:var(--foxink)" not in _scope(".kpi .kval>.chip{"), \
        "the verdict re-plated itself"


def test_the_margin_mark_clears_text_contrast_against_every_face_stop() -> None:
    """The word is TEXT now, not ink on a plate, so it owes 4.5:1 rather than
    1.4.11's 3.0:1 — against all eighteen stops, in both themes, because the
    face palette is declared once and never re-themed. This is the measurement
    the whole change rests on; A0 added the plate to solve it and the word
    never needed one. Worst pair at the time of writing: 4.64:1."""
    ink = _token("--foxink")
    worst, stop = min((_ratio(ink, s), s) for s in _face_stops())
    assert worst >= 4.5, (
        f"the margin mark {ink} is only {worst:.2f}:1 against {stop} — the word "
        f"is under AA on a face and now has no plate to sit on"
    )


def test_the_face_ink_that_is_left_is_the_one_still_used() -> None:
    """--face-ink-safe/bad/warn/info existed to carry status hue on A0's plate.
    Measured against the faces themselves they are 1.03-1.23:1, so they could
    not follow the mark out of the plate, and they went with it. --face-ink-dim
    stays only because .kpi .face .tag still plates."""
    css = _css()
    for kind in ("safe", "bad", "warn", "info"):
        assert "--face-ink-" + kind not in css, (
            f"--face-ink-{kind} is back; if the mark is taking a status hue, "
            f"measure it against the FACE, not against a plate that is gone"
        )
    assert "var(--face-ink-dim)" in css, ".kpi .face .tag lost its ink"


def test_the_on_face_ink_is_theme_invariant() -> None:
    """The nine faces are declared once and never re-themed, so anything measured
    against them must be too. A light-block override would reopen the defect in
    one theme only."""
    light = _scope('html[data-theme="light"]{')
    for tok in ("--face-ink-dim", "--foxink"):
        assert tok not in light, (
            f"{tok} is re-themed; the face it lands on is not"
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

def _srgb_mix(hex_a: str, hex_b: str, pct: float) -> str:
    """color-mix(in srgb, a pct%, b) — the same space the stylesheet asks for."""
    a = [int(hex_a.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
    b = [int(hex_b.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
    f = pct / 100.0
    return "#%02x%02x%02x" % tuple(round(a[i] * f + b[i] * (1 - f)) for i in range(3))


def _mark_colour(kind: str, theme: str) -> str:
    """What the margin rule actually resolves to, mix and all. Read out of the
    stylesheet rather than restated here — a guard that hardcodes the percentage
    is asserting a constant against itself."""
    # Every rule whose selector names this modifier, in source order, because the
    # two loud modifiers share one grouped selector. Matching the first
    # `.chip.<kind>{` finds that group and reports the standalone rule as missing
    # — the shadowing trap this file already carries two notes about.
    decls = [d for sel, d in re.findall(r"([^{}]+)\{([^}]*)\}", _css())
             if ".chip." + kind in sel]
    assert decls, f"no rule declares the {kind} mark"
    vals = [m.group(1).strip() for d in decls
            for m in [re.search(r"border-left-color:\s*([^;}]+)", d)] if m]
    assert vals, f"the {kind} mark has no colour: {decls}"
    val = vals[-1]
    mix = re.fullmatch(
        r"color-mix\(in srgb,\s*var\((--[a-z0-9-]+)\)\s*([\d.]+)%\s*,\s*var\((--[a-z0-9-]+)\)\)",
        val)
    if mix:
        return _srgb_mix(_token(mix.group(1), theme), _token(mix.group(3), theme),
                         float(mix.group(2)))
    plain = re.fullmatch(r"var\((--[a-z0-9-]+)\)", val)
    assert plain, f"the {kind} mark is neither a token nor a mix of two: {val}"
    return _token(plain.group(1), theme)


def test_every_status_mark_separates_from_every_panel() -> None:
    """The mark is the only coloured thing left, so it carries 1.4.11's 3.0:1
    alone, against both panels a status lands on, in both themes.

    Absence is exempt and says so at its own rule, on A0's reasoning: it carries
    no meaning to lose and the nearest grey that would clear 3.0 against
    near-white paper is louder than every real status."""
    for theme in ("dark", "light"):
        for kind in ("safe", "info", "warn", "bad"):
            mark = _mark_colour(kind, theme)
            for p in ("--surf", "--surf2"):
                r = _ratio(mark, _token(p, theme))
                assert r >= 3.0, (
                    f"{theme} {kind} mark ({mark}) is {r:.2f}:1 on {p} — invisible margin"
                )


def _word_colour(kind: str, theme: str) -> str:
    """The ink the word actually resolves to: the base declaration, then any
    modifier rule that overrides it, in source order.

    Written the lazy way first — a dict of the tokens each tier was SUPPOSED to
    use — and a mutation that changed the stylesheet to a near-invisible grey
    left it green, because it was comparing the file's tokens to a copy of the
    file's tokens rather than to what the rule says. That is the one failure mode
    this section's own header warns about, reproduced.
    """
    ink = None
    for sel, body in re.findall(r"([^{}]+)\{([^}]*)\}", _css()):
        sel = sel.strip()
        if not re.fullmatch(r"\.chip(?:\.\w+)?(?:,\s*\.chip(?:\.\w+)?)*", sel):
            continue
        names = {p.strip() for p in sel.split(",")}
        if not ({".chip", ".chip." + kind} & names):
            continue
        m = re.search(r"(?:^|;)\s*color:\s*var\((--[a-z0-9-]+)\)", body)
        if m:
            ink = m.group(1)
    assert ink, f"nothing sets the {kind} word's colour"
    return _token(ink, theme)


def test_every_status_word_is_legible_on_its_panel() -> None:
    """There is no fill to be legible on any more; the word sits on the panel,
    and both panels exist in this file — a status lands on either.

    The ink is read out of the stylesheet, not restated here."""
    for theme in ("dark", "light"):
        for kind in ("safe", "info", "warn", "bad", "dim"):
            ink = _word_colour(kind, theme)
            for p in ("--surf", "--surf2"):
                r = _ratio(ink, _token(p, theme))
                assert r >= 4.5, f"{theme} {kind} word ({ink}) is {r:.2f}:1 on {p}"


def test_the_expected_and_the_exception_do_not_read_alike() -> None:
    """The point of the whole change, and the one a naive "the mark exists" test
    would pass while defeating. If every row is a mark of the same weight, six
    rows of the expected state still shout as loudly as the one exception and
    nothing has been fixed — it is the same defect in a thinner costume.

    So: the two tiers must differ on the WORD, which is what the eye reads in a
    column, and not only on the rule beside it."""
    quiet = _scope(".chip.safe{") + _base_status_rule()
    loud = _scope(".chip.warn,.chip.bad{")
    assert "color:var(--ink)" in loud and "color:var(--muted)" in quiet, (
        "the expected state and the exception are inked alike"
    )
    qw = re.search(r"font-weight:(\d+)", quiet)
    lw = re.search(r"font-weight:(\d+)", loud)
    assert qw and lw and int(lw.group(1)) > int(qw.group(1)), (
        "the exception carries no extra weight"
    )
    assert re.search(r"border-left-width:4px", loud), "the exception's rule did not thicken"
    # and the loud word must actually out-contrast the quiet one, not just differ
    for theme in ("dark", "light"):
        for p in ("--surf", "--surf2"):
            hi = _ratio(_token("--ink", theme), _token(p, theme))
            lo = _ratio(_token("--muted", theme), _token(p, theme))
            assert hi > lo * 2, (
                f"{theme}: the exception word is only {hi / lo:.1f}x the expected "
                f"word on {p} — that is not a hierarchy, it is a rounding error"
            )


def test_the_quiet_mark_is_desaturated_not_dimmed() -> None:
    """Mixing the hue toward the PANEL was the first attempt and it spends
    CONTRAST, which is the one budget a status mark cannot pay from: at 45% the
    marks land at 2.35-2.94:1 against the panels, under 1.4.11. Clearing 3.0 that
    way needs 75-80%, by which point nothing is quiet.

    Mixing toward a neutral spends CHROMA instead. This asserts the axis, because
    the two mixes are one token apart in the source and look identical in review
    — and only one of them is accessible."""
    for kind in ("safe", "info"):
        rule = _scope(".chip.%s{" % kind)
        m = re.search(r"color-mix\(in srgb,\s*var\(--[a-z0-9-]+\)\s*[\d.]+%\s*,\s*"
                      r"var\((--[a-z0-9-]+)\)\)", rule)
        assert m, f"the {kind} mark is no longer a mix: {rule}"
        assert m.group(1) in ("--muted", "--muted2", "--ink2"), (
            f"the {kind} mark is being mixed toward {m.group(1)} — if that is a "
            f"panel token, it is buying quiet with contrast"
        )
    # and the mix must genuinely cost saturation
    for theme in ("dark", "light"):
        for kind, tok in (("safe", "--safe-bg"), ("info", "--info-bg")):
            def chroma(h):
                v = [int(h.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
                return max(v) - min(v)
            full, quiet = chroma(_token(tok, theme)), chroma(_mark_colour(kind, theme))
            assert quiet < full * 0.75, (
                f"{theme} {kind}: chroma {full} -> {quiet}, barely quieter"
            )


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
                 ["--ease-spring", "--dur-slow", "--skel-hi", "--face-ink-dim"]:
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


# ── 10 · R1 · the overview and health pages ──────────────────────────────────
# A1 reserved the saturated face for one card per page and gave the rest
# `.quiet`; A3 and A4 carried that to traffic, revenue and campaigns. The owner
# reviewed all of it live and reverted it: every card is saturated again, on
# every page. These guards used to assert the reserved face. They assert the
# reversal now — including that `.quiet` is gone from the stylesheet, because a
# half-live variant class is worse than either decision.


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


def test_every_kpi_card_is_fully_coloured() -> None:
    """Owner decision, taken after seeing A1 live. Not "most" and not "the
    important ones" — every card on every page, including the two rows A3 and A4
    converted afterwards and the five the tenant drill-down emits from JS."""
    cards = re.findall(r'<div class="clay kpi ([^"]*)"', SRC)
    assert len(cards) == 21, "expected 21 KPI cards, found %d" % len(cards)
    dim = [c for c in cards if "quiet" in c]
    assert not dim, (
        "%d cards are still carrying the reserved-face modifier: %s" % (len(dim), dim)
    )


def test_the_quiet_variant_is_gone_from_the_stylesheet() -> None:
    """The class, not just its call sites. Left in the sheet it would re-style
    any card that picked the modifier back up in a merge — and this file has one
    branch per phase all editing the same 486 KB, so that is not hypothetical."""
    css = _css()
    # The invariant is that the VARIANT is gone: its rule block, and any card
    # wearing it. NOT every textual mention. A4 scopes four `.sens` rules with
    # `.kpi:not(.quiet)`, and stripping that qualifier would drop their
    # specificity from (0,2,0) to (0,1,0) — a real cascade change made to satisfy
    # a string search. No card carries the class, so the qualifier is inert; it
    # is left alone and named here instead.
    assert ".kpi.quiet" not in css, (
        "the quiet variant still has rules: " +
        ", ".join(sorted(set(re.findall(r"[^{}\n]*\.kpi\.quiet[^{}]*", css))))[:300]
    )
    # ...and no card wears it. Inert today because the rule is gone, but a
    # stray modifier plus a re-added rule is exactly how the variant returns.
    # MARKUP, not the prose about it: the comment a few hundred lines up
    # quotes `class="kpi … quiet"` while explaining why the rule was
    # deleted, and a naive grep over SRC matches its own footnote.
    # Strip BOTH comment syntaxes: the footnote that quotes this exact
    # string lives in a CSS comment inside <style>, not an HTML comment,
    # so stripping only <!-- --> leaves the guard matching its own prose.
    markup = re.sub(r"<!--.*?-->|/\*.*?\*/", "", SRC, flags=re.S)
    assert not re.search(r'class="[^"]*\bkpi\b[^"]*\bquiet\b', markup), (
        "a KPI card still carries the quiet modifier in markup")

def test_the_face_still_carries_the_ink_it_publishes() -> None:
    """The saturated face is the only face now, so everything A1 measured for it
    has to still hold: dark ink, undimmed label, and one published --spark."""
    css = _css()
    rule = _scope(".kpi .face{")
    assert "linear-gradient(145deg,var(--k" in rule, (
        "the face stopped painting the card's own two stops: " + rule
    )
    assert "--spark:var(--foxink)" in css, "the face stopped publishing --spark"
    assert css.count("--spark:") == 1, (
        "there is more than one --spark declaration; there is only one face now"
    )


def test_the_health_hero_does_not_span_the_row() -> None:
    """A1 gave Overall `grid-column:1/-1` and re-laid its face as a flex ROW, so
    the page opened with a full-width band holding a chip at one end and text at
    the other. Owner decision: four equal cards.

    ⚠ And .leadcard went with it rather than being emptied. .kpis is
    repeat(auto-fit,minmax(150px,1fr)) and auto-fit only collapses tracks that
    are EMPTY — a card spanning 1/-1 filled all eight, so nothing collapsed and
    the three probes sat in tracks 1-3 of 8 against the left edge. With no
    spanner the four cards collapse to four equal tracks unaided, which is why
    there is no replacement column count to assert."""
    css = _css()
    assert "leadcard" not in css, "the lead-card rules are still in the sheet"
    assert "leadcard" not in _markup(), "a KPI row still asks for the lead card"
    assert "grid-column:1/-1" not in css, (
        "something spans a KPI row again; auto-fit will stop collapsing and the "
        "cards beneath will bunch to the left"
    )
    health = _page("health")
    row = re.search(r'<div class="grid kpis([^"]*)">', health)
    assert row and not row.group(1).strip(), (
        "the health KPI row carries a layout modifier again: %r"
        % (row.group(1) if row else None)
    )
    assert len(re.findall(r'<div class="clay kpi ', health)) == 4, (
        "health is no longer four cards, so 'four equal cards in one row' is "
        "not what auto-fit will produce"
    )
    # the face is a bottom-aligned STACK on every card, with no row exception.
    assert "flex-direction:column" in _scope(".kpi .face{")
    assert "flex-direction:row" not in css.split(".kpi .face{")[1][:1200], (
        "a KPI face lays itself out as a row again"
    )


def test_the_beam_is_untouched_by_the_reversal() -> None:
    """#64 is decided: the beam orbits on all 21 cards. It was held constant
    while A1 built hierarchy out of fill, and it stays constant now that R1 has
    taken the fill back — the two decisions are independent, and the beam is not
    a lever either of them gets to pull.

    The old form of this guard named the two selectors A1 might have written and
    passed once A1 was reverted, because it was asserting the absence of a class
    that no longer exists. It looks for anything that stops a beam now."""
    css = _css()
    for m in re.finditer(r"([^{}]*\.beam[^{}]*)\{([^}]*)\}", css):
        sel, body = m.group(1).strip().splitlines()[-1].strip(), m.group(2)
        if not re.search(r"display:\s*none|animation:\s*none|opacity:\s*0[;}]", body):
            continue
        # the one legitimate suppressor, and it is a whole-sheet accessibility
        # rule rather than a per-card one.
        assert sel == ".kpi .beam" and "prefers-reduced-motion" in css[:m.start()][-200:], (
            "%s stops the beam on a subset of cards: %s" % (sel, body)
        )
    for page in A1_PAGES:
        markup = _page(page)
        cards = re.findall(r'<div class="clay kpi [^"]*"', markup)
        beams = markup.count('class="beam"')
        assert beams == len(cards), "%s: %d cards but %d beams" % (page, len(cards), beams)


def test_every_line_on_a_face_takes_the_face_ink() -> None:
    """The quiet variant is what used to re-ink these three for a near-surface.
    With one face left there is one answer, and it must cover all three lines —
    .klabel and .kfoot default to --muted / --muted2, which are greys tuned
    against the flat panel and measure 1.1-1.3:1 on a saturated face."""
    css = _css()
    m = re.search(r"([^{}]*)\{color:var\(--foxink\)\}", css)
    rule = re.search(
        r"\.kpi \.face \.klabel,\.kpi \.face \.kval,\.kpi \.face \.kfoot"
        r"\{color:var\(--foxink\)\}", css)
    assert rule, (
        "the three lines on a face no longer take --foxink together; a face line "
        "left on --muted is a grey on a gradient (found instead: %s)"
        % (m.group(1).strip()[:80] if m else "nothing")
    )
    i = css.index(".kpi .face .klabel{")
    assert "opacity" not in css[i: css.index("}", i)], "the label is dimmed again"


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
    """The sparkline colour was read straight off :root, which cannot know what
    surface the line lands on — that is how --breach-bg red got drawn on the
    orange face at 1.01:1. The face publishes --spark and the JS reads it from
    the face it is drawing into. Keep the indirection even though there is one
    face again: it is what stopped a hue being hard-coded in JS."""
    css = _css()
    assert "--spark:var(--foxink)" in css, "the face stopped publishing --spark"
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


def test_the_js_emitted_tenant_cards_are_fully_coloured_too() -> None:
    """The reversal has to reach the rows that are built as strings, or the
    tenant drill-down is the one page still wearing the rejected variant."""
    body = _js_func("renderO3Header")
    cards = re.findall(r'<div class="clay kpi (k-[a-z]+[^"]*)"', body)
    assert len(cards) == 5, "org360 no longer renders five KPI cards: %s" % cards
    dim = [c for c in cards if "quiet" in c]
    assert not dim, "org360 still emits reserved-face modifiers: %s" % dim


def test_breaches_wear_the_same_hue_on_both_pages() -> None:
    """What survives A1 here is the HUE assignment, not the reserved face. The
    windowed Breaches card lands on .k-orange, which is what the platform
    Overview gives Policy breaches, so the two pages agree about what a breach
    looks like. That pairing is the decision; the rest of the row is free."""
    body = _js_func("renderO3Header")
    card = re.search(r'<div class="clay kpi (k-[a-z]+)"[^>]*>(?:(?!</div>).)*?'
                     r'Breaches\'\+win', body, re.S)
    assert card, "the windowed Breaches card is gone from the tenant header"
    assert card.group(1) == "k-orange", (
        "windowed Breaches moved to %s; breaches are orange on the Overview and "
        "the two pages have to agree" % card.group(1)
    )
    overview = _page("overview")
    m = re.search(r'<div class="clay kpi (k-[a-z]+)"(?:(?!</div>).)*?Policy breaches',
                  overview, re.S)
    assert m and m.group(1) == "k-orange", (
        "the Overview's Policy breaches card is %s, not k-orange"
        % (m.group(1) if m else "missing")
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
    # SEVEN since C3 added the quota meter beside Plan. A fault row spanning the
    # wrong count is invisible until the table is actually empty, which is why
    # this is pinned to a number rather than to "some colspan".
    assert "faultRow(7," in body, (
        "the org table's failure path does not fill its own seven columns"
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


def test_the_traffic_diagnosis_still_leads_the_row() -> None:
    """A3 did two things to this row: reserved the face for Errors, and put
    Errors FIRST. R1 reverted the first — every card is saturated again — and
    kept the second. Order is not fill: the card that answers "is something
    wrong right now" reads before the three volume counts, and the eye still
    gets its path from position. Do not sort these back into volume order."""
    cards = _kpi_cards()
    traffic = [c for c in cards if any(k in c for k in ("tErr", "tMkt", "tApp", "tAdm"))]
    assert len(traffic) == 4, "expected 4 traffic KPI cards, found %d" % len(traffic)
    assert not [c for c in traffic if "quiet" in c.split(">")[0]], (
        "a traffic card is still carrying the reserved-face modifier"
    )
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
    """REWRITTEN at MAIN's R1 gate, not deleted.

    A4 wrote this to assert #65's reserved face on revenue. The owner then
    reviewed the reserved face in context across the whole surface and reverted
    it (R1): every KPI card is fully coloured again. A4 could not update this
    guard because R1 branched before A4 merged. The assertion flips; the
    coverage stays."""
    cards = [c for c in _kpi_cards() if any(k in c for k in ("rvRev30", "rvActive", "rvTrial"))]
    assert len(cards) == 3, "expected 3 revenue KPI cards, found %d" % len(cards)
    loud = [c for c in cards if "quiet" not in c.split(">")[0]]
    assert len(loud) == 3, "revenue shows %d fully-coloured cards, not 3" % len(loud)
    assert any('id="rvRev30"' in c for c in cards), "the Revenue card is missing"


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
    a 500 left the previous poll's money on screen with the webhook pager still
    reading "1-25 of 240" over it.

    S1 re-pointed the feed half at Paddle. The contract was written against the
    Stripe feed and M3d never extended it when it added the Paddle one, so on a
    failed /revenue the Paddle table sat on "loading..." indefinitely — both
    feeds load only after the early return this guard covers. The Stripe feed is
    gone; the assertion follows the feed that is still here rather than leaving
    the page with no webhook fault state at all."""
    body = _js_func("loadRevenue")
    assert "toast(" not in body.split("const d=")[0], "revenue still fails by toast alone"
    for slot in ("rvMonthly", "rvByPlan"):
        assert re.search(r"\$\('%s'\)\.innerHTML=fault\(" % slot, body), (
            "%s does not report the failure" % slot
        )
    assert "loadRevenue()" in body, "the revenue fault offers no retry"
    assert re.search(r"\$\('peRows'\)\.innerHTML=faultRow\(", body), (
        "the webhook feed sits on 'loading...' forever when /revenue fails"
    )
    assert re.search(r"\$\('pePager'\)\.innerHTML=''", body), (
        "a stale '1-25 of 240' can still sit above a fault row"
    )


def test_a_new_webhook_filter_is_a_new_question() -> None:
    """_fauxCommit invokes data-onchange as window[cb](root), so naming
    loadStripeEvents there put the select ELEMENT into its `page` parameter and
    the query string went out as offset=NaN. Driven through a real option
    click, picking "failed" answered "no stripe events with status failed"
    while three failed events existed.

    RE-AIMED AT PADDLE BY S1, NOT DELETED. The Stripe feed is gone, but M3d
    built the Paddle select in the same shape and NOTHING guarded it — the
    measured gap: no test in this file mentioned #peStatus or
    paddleFilterChanged. Deleting this guard with its subject would have left
    the one webhook filter that still ships free to reproduce a false negative
    about failed payments, on the money page, with 306 guards green."""
    m = re.search(r'id="peStatus"[^>]*data-onchange="([a-zA-Z]+)"', _nocomment(SRC))
    assert m, "the Paddle status select is gone"
    assert m.group(1) != "loadPaddleEvents", (
        "the status select still hands the select element to a `page` parameter"
    )
    body = _js_func(m.group(1))
    assert "pgReset('paddle')" in body, "a new Paddle filter still keeps the old offset"
    assert "loadPaddleEvents()" in body, "the filter no longer reloads the table"


def test_replaying_a_webhook_cannot_double_submit() -> None:
    """Replay re-runs a webhook inside the processor — A3's re-anchor case: a
    write to a system Foxy does not control, and one that can move money. It had
    no confirmation and no busy guard, and three rapid clicks sent three POSTs.

    RE-AIMED AT PADDLE BY S1, NOT DELETED. `_doReplayPaddle` carries busy() and
    a try/catch, but no guard read its body — measured: no test in this file
    passed "_doReplayPaddle" to _js_func or _pe_code, so both remedies were
    unprotected on the only replay path that still ships. The confirmation
    STEP is covered by test_the_replay_button_goes_through_a_confirmation; what
    only lived here is the double-submit guard, the try/catch, and the rule that
    the confirmation itself must not call the API."""
    gate = _js_func("replayPaddle")
    assert "openModal(" in gate, "replay fires straight at the processor with no confirmation"
    assert "api(" not in gate, "the confirmation step still calls the API itself"
    run = _js_func("_doReplayPaddle")
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


def test_the_webhook_table_binds_its_columns() -> None:
    """Under 760px .tbl.cardify removes the header row, so scope + data-label
    are the only things left tying a value to its column. All six headers here
    carried neither half.

    RE-AIMED AT PADDLE BY S1, NOT DELETED. `revenue` is in A4_PAGES, NOT
    A2_PAGES, so test_every_column_on_an_a2_surface_names_itself never reached
    this page — checked, not assumed. This is the only scope= guard the revenue
    page has, and after S1 the table it lands on is the Paddle feed.

    It is anchored to #peRows rather than to "the first thead on the page",
    which is how the original found the Stripe table. That anchor is why the
    guard would have kept passing while silently changing subject: remove the
    panel above and the same regex quietly starts measuring the next table
    down. Naming the tbody makes the subject explicit."""
    page = _a2_page("revenue")
    end = page.index('id="peRows"')
    start = page.rindex("<table", 0, end)
    head = re.search(r"<thead><tr>(.*?)</tr></thead>", page[start:end], re.S)
    assert head, "the Paddle events table has no header row"
    ths = re.findall(r"<th\b([^>]*)>", head.group(1))
    assert len(ths) == 6, "the Paddle events table no longer has six columns"
    assert all('scope="col"' in t for t in ths), "a Paddle events column does not name itself"


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


# ═══════════════════════════════════════════════════════════════════════════
# A5 · leads + inbox — the pre-sales pipeline and the support inbox.
#
# These two pages are where staff act on people OUTSIDE the company: a prospect
# who gets an email, a lead who gets marked churned. Almost every guard below
# holds one shape in place — the page stating something that is not true of what
# it is showing, or of what it just did.
# ═══════════════════════════════════════════════════════════════════════════

A5_PAGES = ("leads", "inbox")


def test_the_reply_reports_whether_the_email_actually_went_out() -> None:
    """The endpoint records the reply, tries to send it, and returns which of
    those happened. sendReply read r.ok alone, so a mail provider that was down
    returned 200 and the console said it had emailed a prospect it had not —
    then painted a replied mark on the row so nobody would look again. The truth
    was already in the response body; we were not reading it."""
    body = _js_func("sendReply")
    assert "emailed" in body, "sendReply reports success from the status code alone again"
    assert re.search(r"if\s*\(\s*d\.emailed\s*\)", body), (
        "nothing branches on whether the email was actually sent"
    )
    assert body.count("toast(") >= 2, "the sent and not-sent outcomes collapsed into one message"


def test_no_outward_action_can_be_double_submitted() -> None:
    """busy() sets aria-busy, which .btn[aria-busy] already turns into
    pointer-events:none — so calling it IS the guard. It had shipped for four
    phases with no caller on either of these pages, and the uncovered action was
    the one that emails a person: two clicks sent two mails AND overwrote the
    first reply's stored text, because the server keeps a single reply column."""
    for fn in ("sendReply", "claimMsg", "releaseMsg", "leadMove"):
        assert re.search(r"busy\(\s*btn", _js_func(fn)), f"{fn} lost its double-submit guard"
    # the guard is worthless if the markup never hands the button over
    js = _js_nocomment()
    for call in ("sendReply('", "claimMsg('", "releaseMsg('", "leadMove('"):
        at = js.index(call)
        assert "this" in js[at:at + 90], f"{call}...) is called without its button"


def test_no_action_on_these_pages_is_an_anchor_wearing_a_button() -> None:
    """claim, release and force-release were anchors with an onclick and a
    `return false`. They navigate nowhere, announce as links, and take Enter but
    not Space. A2 fixed eight of these by sweeping the class they shared; these
    three carried no class, so the sweep could not see them."""
    js = _js_nocomment()
    detail = js[js.index("function renderInboxDetail"):]
    detail = detail[: detail.index("async function sendReply")]
    assert 'href="#"' not in detail, "an inbox action is an anchor again"
    for page in A5_PAGES:
        assert 'href="#"' not in _a2_page(page), f"{page} markup has an anchor acting as a button"


def test_the_reply_box_says_who_it_emails_while_you_type() -> None:
    """It had no label and no aria-label — no accessible name at all, on the
    highest-stakes control on either page. The recipient was named in the
    placeholder, which is the one place a fact disappears the moment you start
    writing the message it is a fact about."""
    body = _js_func("renderInboxDetail")
    assert 'for="replyBox"' in body, "the reply box lost its label"
    label = body[body.index('for="replyBox"'):]
    label = label[: label.index("</label>")]
    assert "emails" in label and "it.email" in label, (
        "the label no longer names the address the reply goes to"
    )


def test_every_mark_on_the_selected_row_is_legible_on_it() -> None:
    """The four marks were inline hues on bare spans, so the selected-row
    override could not reach them — it keys off two utility classes and these
    carried neither. The selected row is painted in the same value the lock mark
    was painted in, so on the open row the lock measured 1.00:1. That is the row
    it matters on: "someone else already owns this" is the fact that stops a
    second email going to a live prospect.

    Recomputed from the token values rather than compared to a number written
    beside them."""
    css = _css()
    for cls in (".ib-lock", ".ib-done", ".ib-new", ".ib-pri"):
        assert cls in css, f"{cls} is gone — the marks are inline hues again"
    assert "var(--foxink)" in _scope(".inbox-row.on .ib-lock"), (
        "the marks no longer restate themselves on the selected row"
    )
    # BOTH themes: --fox and --fox3 are re-themed, --foxink is not. Measuring only
    # the dark scope passed a light-mode figure this guard had never seen.
    # Measured today: dark 7.46 / 4.92, light 5.60 / 3.33 against the two stops.
    # The floor is 1.4.11's 3.0 rather than 4.5 because no ink clears 4.5 against
    # the light gradient's dark stop -- pure black is 3.60 -- so a 4.5 assertion
    # here would be a number that cannot be met rather than one being held. The
    # incumbent .inbox-row.on rule paints every other word in the row in this same
    # ink at these same ratios; the marks now match it instead of being invisible.
    ink = _token("--foxink")
    for theme in ("dark", "light"):
        for stop in ("--fox", "--fox3"):      # the two stops of the row's gradient
            r = _ratio(_token("--foxink", theme), _token(stop, theme))
            assert r >= 3.0, (
                f"the mark measures {r:.2f}:1 against {stop} in {theme} on the selected row"
            )
    assert _token("--foxink", "light") == ink, "--foxink is re-themed now; re-measure the marks"
    # And no fill under it: a dark wash cut from the same ink measured 1.64:1 as
    # a FILL against that gradient — the order of the wash it would have replaced.
    assert "background:none" in _scope(".inbox-row.on .ib-pri"), (
        "a plate is being drawn under the urgency mark on the selected row again"
    )
    # the row's own urgency rail had the same problem, one level up
    assert "var(--foxink)" in _scope(".inbox-row.pri.on"), (
        "the urgency rail is an orange rail down an orange gradient again"
    )


def test_a_failed_leads_load_does_not_leave_the_stage_counts_standing() -> None:
    """The failure path wrote a fault into the board and left the strip above it
    untouched, so a failed refresh rendered four confident stage totals from a
    previous fetch directly over the words "Leads did not load". loadOverview
    clears both of its funnels in this same idiom; this one was missed."""
    body = _js_func("loadLeads")
    fail = body[: body.index("const d=await r.json()")]
    assert "$('ldFunnel').innerHTML=''" in fail, "stale stage counts survive a failed load"
    assert "$('ldScope')" in fail, "the scope line survives a failed load"


def test_the_filter_counts_count_the_list_they_sit_above() -> None:
    """The counts were tallied over every loaded message while the list beneath
    them was ALSO filtered by the search box — so typing a domain left the
    buttons advertising the whole inbox. The number on the button was not the
    number of rows pressing it would give you."""
    assert "_inboxSearched()" in _js_func("_inboxCounts"), (
        "the filter counts ignore the search box again"
    )
    assert "_inboxSearched()" in _js_func("renderInboxList"), (
        "the list and the counts are reading two different sources again"
    )


def test_an_empty_stage_draws_no_bar() -> None:
    """funnelRow floored the width at Math.max(2, ...) and .funbar i adds a
    min-width on top of it, so a stage holding nothing rendered a short coloured
    stub — the numeral said 0 and the bar disagreed with it."""
    body = _js_func("funnelRow")
    assert re.search(r"val\s*>\s*0", body), "a zero bucket draws a bar again"
    assert "min-width:3px" in _css(), (
        "the min-width this guard exists to neutralise is gone; re-check funnelRow"
    )


def test_the_four_stages_have_one_source() -> None:
    """The strip and the board were two literal lists of the same four buckets
    carrying two colour vocabularies, and they did not agree: the first stage was
    one token in the strip and another in the board, which resolve to the same
    value in one theme and to two different blues in the other."""
    assert "LEAD_STAGES" in SRC, "the stage table is gone"
    body = _js_func("loadLeads")
    assert "LEAD_STAGES" in body, "loadLeads stopped reading the stage table"
    for legacy in ("['new','New'", "var(--blue)"):
        assert legacy not in body, f"a second stage vocabulary is back in loadLeads ({legacy})"
    stages = SRC[SRC.index("const LEAD_STAGES=["):]
    stages = stages[: stages.index("];")]
    for tok in ("--info-bg", "--warn-bg", "--safe-bg", "--breach-bg"):
        assert tok in stages, f"{tok} left the stage table"


def test_both_pipeline_pages_announce_themselves() -> None:
    """Thirteen of this file's pages carry an sr-only h1. These two did not, and
    the only other text naming them is the watermark, which is aria-hidden — so
    both presented zero headings to the accessibility tree."""
    for page in A5_PAGES:
        assert re.search(r'<h1 class="sr-only">[^<]+</h1>', _a2_page(page)), (
            f"{page} has no heading"
        )
    for field, page in (("inboxSearch", "inbox"), ("ldSearch", "leads")):
        assert 'for="%s"' % field in _a2_page(page), f"{field} is unlabelled again"


def test_the_stage_move_clears_the_pointer_target_minimum() -> None:
    """The only action the leads page has. It was inline padding with 9px type,
    measured 45x22 rendered, and 22 is under the 24 SC 2.5.8 asks — and being
    inline it also beat the coarse-pointer rescue rule, which is a media query
    and cannot outrank a style attribute."""
    m = re.search(r"min-height:(\d+)px", _scope(".lead-card .lc-moves .btn"))
    assert m and int(m.group(1)) >= 24, "the stage-move target is under the pointer minimum again"
    # Scoped to the button tag itself. The moves expression also holds the
    # read-only fallback, which is a label and not a target; a slice that wide
    # policed the wrong element and failed for the wrong reason.
    body = _js_func("_leadCard")
    moves = body[body.index("const moves="): body.index("const utm=")]
    tag = moves[moves.index("<button"): moves.index(">", moves.index("<button"))]
    assert "style=" not in tag, (
        "the move buttons are sized inline again, out of reach of the rescue rule"
    )


def test_the_inbox_can_have_a_breakpoint_at_all() -> None:
    """The two panes were an inline grid on a bare div, and an inline grid is
    unreachable from every media query in the file. Measured at a 375px viewport
    the detail pane ran to x=524 and the document scrolled sideways, taking the
    message body, the reply box and the send control off screen — which is the
    whole job of the page."""
    assert "mailgrid" in _css(), "the inbox grid is not a class"
    mk = _a2_page("inbox")
    assert 'class="mailgrid"' in mk, "the inbox page stopped using the class"
    assert "grid-template-columns" not in mk, "the inbox grid is inline again"
    stacked = [b for _w, b in _width_media_blocks() if "mailgrid" in b]
    assert stacked, "the inbox grid has no width breakpoint"
    assert any("1fr" in b for b in stacked), (
        "the breakpoint exists but does not collapse the panes to one column"
    )


def test_the_console_still_has_no_native_dialog() -> None:
    """One survived, on the inbox claim path, on a surface whose modal section is
    headed as the replacement for the native dialogs. It also blocked the unread
    poll for as long as it sat there. Named by pattern rather than spelled out,
    because a guard that greps this file greps its own explanation."""
    assert not re.search(r"(?<![\w.])" + "aler" + r"t\s*\(", _js_nocomment()), (
        "a native dialog is back"
    )


def test_the_scrollable_regions_on_these_pages_are_reachable() -> None:
    """A comment in this file calls .scrolly the only scrollable region it has.
    It was wrong by four, and all four are on these two pages: the message list,
    and one inner scroller per stage column. Same SC 2.1.1 defect .scrolly was
    fixed for, in markup that names no class."""
    assert re.search(r'class="clay inbox-pane"[^>]*tabindex="0"', _a2_page("inbox")), (
        "the message list is a focusless scroll region again"
    )
    cols = _js_func("loadLeads")
    at = cols.index("overflow:auto")
    assert 'tabindex="0"' in cols[max(0, at - 220):at], (
        "the stage columns scroll with no way to reach them from the keyboard"
    )


# ═══════════════════════════════════════════════════════════════════════════
# A6 · staff + settings — the accounts that hold the platform, and the switches
# that govern it. The last two pages of the refinement pass, and the two that
# every earlier phase's conventions had reached last.
# ═══════════════════════════════════════════════════════════════════════════

A6_PAGES = ("staff", "settings")


def _a6_js(name: str) -> str:
    return _js_func(name)


def test_mfa_off_is_an_exception_and_not_an_absence() -> None:
    """The redefined vocabulary made a latent mislabel expensive. The absence
    tier is documented at its own rule as "not a status at all", and its mark is
    deliberately left under 1.4.11 — measured 1.47-1.54:1 — because absence has
    no meaning for a mark to carry; the word does the work.

    An account with no second factor is not an absence. It is the single row a
    quarterly hygiene pass exists to find, and under the absence tier it rendered
    as the quietest thing in the table — quieter than the accounts that are fine.
    The measurement below is the argument: the absence tier would ship a
    meaning-bearing indicator at a ratio that is only defensible when the
    indicator means nothing."""
    for fn in ("renderStaff", "loadSettings"):
        body = _js_func(fn)
        assert "chip warn" in body and "MFA" in body.upper() or "chip warn" in body, (
            f"{fn} no longer marks a missing second factor as an exception"
        )
        assert not re.search(r'chip dim">(off|MFA off)<', body), (
            f"{fn} put the second factor back in the absence tier"
        )
    # the absence tier's mark is under 3:1 by design — so it must not carry meaning
    for theme in ("dark", "light"):
        for panel in ("--surf", "--surf2"):
            r = _ratio(_token("--line2", theme), _token(panel, theme))
            assert r < 3.0, (
                "the absence mark now clears 3:1, so the argument for keeping a "
                "security state out of that tier needs re-deriving, not copying"
            )
            w = _ratio(_token("--ink", theme), _token(panel, theme))
            assert w >= 4.5, f"{theme} the exception word is {w:.2f}:1 on {panel}"


def test_never_signed_in_takes_the_tier_that_was_built_for_it() -> None:
    """The absence tier's own rule names this exact word as its use case, and it
    was the one place not using it — plain text — while the tier was being spent
    on a live security state instead."""
    body = _js_func("_lastSeen")
    assert re.search(r'chip dim">never<', body), (
        "a staff account that has never signed in is no longer marked as absence"
    )


def test_a_dormant_account_cannot_read_as_a_recent_one() -> None:
    """The shared time formatter renders month, day and clock and NO YEAR —
    right for a session seen this week, wrong for the one column on this page
    whose whole job is surfacing accounts nobody has touched. A sign-in from two
    years ago rendered character-identical in form to one from three weeks ago,
    so the hygiene pass read a stale account as current. The day formatter, one
    line below it in the same file, has carried the year all along."""
    body = _js_func("_lastSeen")
    assert "fmtDay(" in body, "the last-login column dropped the year again"
    assert "fmtTime(" not in body, "the yearless formatter is back on this column"
    assert "STALE_DAYS" in body and "chip warn" in body, (
        "a dormant account no longer marks itself as the exception it is"
    )
    # the threshold has to be a real number of days, not a placeholder
    m = re.search(r"const STALE_DAYS=(\d+);", SRC)
    assert m and 30 <= int(m.group(1)) <= 365, "the dormancy threshold is not a plausible age"
    # ⚠ ADDED AT THE MERGE GATE. Everything above inspects the helper's BODY, so
    # it stays green while the column that needed it stops calling it: swapping
    # the cell back to `s.last_login?fmtTime(...)` reproduced the original defect
    # exactly and all 256 guards passed. Guarding a definition is not guarding
    # its use — and on this surface the use is the whole finding.
    cell = re.search(r'data-label="Last login"[^>]*>\$\{([^}]+)\}', _js_func("renderStaff"))
    assert cell, "the roster's last-login cell moved or stopped being a template hole"
    assert "_lastSeen(" in cell.group(1), (
        "the roster stopped calling the dormancy-aware formatter: " + cell.group(1)
    )
    assert "fmtTime(" not in cell.group(1), (
        "the yearless formatter is back at the call site, which is where it does "
        "the damage: " + cell.group(1)
    )


def test_every_column_on_an_a6_page_names_itself() -> None:
    """Under 760px these tables drop their header row entirely, so the binding
    and the per-cell label are the only things left tying a value to its column.
    These ten were the last unbound ones in the file.

    The sessions table also ended in an EMPTY header cell — worse than an unbound
    one, because a reader lands on a column that exists and hears nothing for
    it. Named the way the data browser already names its own action column."""
    heads = re.findall(r"<thead>.*?</thead>", _nocomment(_page("staff")), re.S)
    heads += re.findall(r"<thead>.*?</thead>", _js_func("loadSessions"), re.S)
    assert len(heads) == 2, f"expected the two A6 headers, found {len(heads)}"
    for h in heads:
        cells = re.findall(r"<th\b[^>]*>", h)
        assert cells, "a header row lost its cells"
        for c in cells:
            assert "scope=" in c, f"an A6 column does not bind its cells: {c}"
    # and no header cell may be empty of text
    for h in heads:
        for m in re.finditer(r"<th\b[^>]*>(.*?)</th>", h, re.S):
            assert m.group(1).strip(), "a column header announces nothing"


def test_the_last_two_pages_announce_themselves() -> None:
    """Fifteen sibling pages carry a screen-reader heading; these two were the
    only ones left without, so navigating by heading landed nowhere and the only
    text naming them is the watermark, which is hidden from the tree."""
    for page in A6_PAGES:
        assert re.search(r'<h1 class="sr-only">[^<]+</h1>', _nocomment(_page(page))), (
            f"{page} has no heading"
        )


def test_the_loading_row_spans_the_table_it_is_in() -> None:
    """It spanned four of six columns, so the first thing the page ever rendered
    sat under Last login with two empty cells beside it. The error and empty
    states in the same table already used the right number."""
    mk = _nocomment(_page("staff"))
    cols = len(re.findall(r"<th\b", mk))
    for m in re.finditer(r'colspan="(\d+)"', mk):
        assert int(m.group(1)) == cols, (
            f"a row spans {m.group(1)} of {cols} columns"
        )


def test_the_staff_list_says_which_failure_it_hit() -> None:
    """It printed a permission sentence for ANY non-ok status. A 500, a 502 and a
    504 all rendered it, with the real code sitting unused in the same template
    string. A viewer reads it and is right; a superadmin reads it during an
    outage and concludes their own account has been demoted, and this page has no
    other signal to correct them with."""
    body = _js_func("loadStaff")
    assert "403" in body, "the permission message is no longer conditional on a status"
    assert "faultRow(" in body, "the loader hand-rolls a dead end again"
    assert re.search(r"try\{\s*r\s*=\s*await api\(", body), "the staff loader can fail silently again"
    # the permission wording must sit on the permission branch only
    perm = body[body.index("perm"):]
    assert perm.index("superadmin-only") < perm.index("did not load"), (
        "the permission sentence escaped its branch"
    )


def test_no_call_on_these_two_pages_can_fail_silently() -> None:
    """api() resolves to a bare fetch, which THROWS on a dropped connection.
    Eleven call sites here were unguarded, so a network blip produced an
    unhandled rejection and the control did nothing at all — no message, no
    change. A superadmin clicks Disable account, nothing moves, and they walk
    away believing the account is disabled."""
    fns = ("loadStaff", "createStaff", "staffDisable", "staffEnable", "staffRole",
           "staffMfaReset", "loadStaffActivity", "saveProfile", "savePrefs",
           "loadSessions", "revokeSession", "logoutEverywhere", "mfaEnableFlow",
           "_doMfaConfirm", "_doMfaDisable", "loadConfig", "saveConfig",
           "sendBroadcast", "loadAnnouncements", "deactivateAnn")
    for fn in fns:
        body = _js_func(fn)
        calls = len(re.findall(r"\bapi\(", body))
        if not calls:
            continue
        assert re.search(r"try\{\s*r\s*=\s*await api\(", body), f"{fn} calls the API unguarded"
        # Two of these answer a null r through a ternary rather than an early
        # return. Both are real handling, so the assertion is that SOMETHING
        # tests r for falsiness -- not that it is spelled one particular way.
        assert re.search(r"if\(!r[\)|&]", body) or re.search(r"[^\w]r\?", body), (
            f"{fn} guards the throw but has no branch for the null it produces — "
            f"the click still does nothing"
        )


def test_the_irreversible_staff_actions_ask_first() -> None:
    """The same file gates revoking ONE tenant's API key behind a modal that says
    it cannot be undone, and offboarding a tenant behind typing its name. Ending
    a colleague's access, clearing a superadmin's second factor and promoting an
    account to full control had nothing — one click each. Staff who learn that
    dangerous things ask twice will click straight through the ones that do not."""
    for helper in ("staffConfirm", "staffRoleConfirm", "confirmLogoutEverywhere"):
        assert "openModal(" in _js_func(helper), f"{helper} no longer confirms anything"
    mgr = _js_func("staffManage")
    for direct in ("staffDisable('", "staffMfaReset('", "staffRole('"):
        assert direct not in mgr, f"the modal calls {direct}…) without confirming first"
    # and the page's own control must route through the confirmation too
    assert "confirmLogoutEverywhere()" in _nocomment(_page("settings")), (
        "log out everywhere fires straight from the button again"
    )


def test_the_one_time_password_is_copyable_and_announced() -> None:
    """The most sensitive string either page produces, shown once. It was 10.5px
    muted body text with no way to copy it and no live region, so a screen-reader
    user heard nothing and the credential was unrecoverable. The component that
    solves all three already ships and is used by the campaign panel."""
    assert "_secretRow('one-time password'" in _js_func("createStaff"), (
        "the temp password is hand-rendered again"
    )
    mk = _nocomment(_page("staff"))
    out = re.search(r'<div id="nsOut"[^>]*>', mk)
    assert out and "aria-live" in out.group(0), "the credential is written into a silent element"


# ═══════════════════════════════════════════════════════════════════════════
# A7 · two follow-ups on shipped pages: the platform-config fields stop
# manufacturing integers out of things nobody typed, and the two lists that
# lacked a search get one.
# ═══════════════════════════════════════════════════════════════════════════


def test_an_emptied_config_field_cannot_reach_the_server() -> None:
    """The severe half, and not the one the register describes. The old test was
    Number.isInteger(Number(v)), and Number is a coercion rather than a parse:
    an emptied box became 0, which passes the integer test, differs from the
    stored value, and was PUT. The page then reported a save, because it had
    saved — it saved zero. A quota or a retention window silently becoming 0 is
    a data defect, not a UX gap."""
    v = _js_func("_cfgValidate")
    assert "v===''" in v.replace('"', "'"), "an empty field is no longer refused"
    assert "would save 0" in v, "the message no longer names what emptying would do"
    # and the coercion that produced the zero must be gone from the value test
    save = _js_func("saveConfig")
    assert "Number.isInteger(Number(" not in save, "the coercion test is back"


def test_the_validator_is_actually_called_before_a_save() -> None:
    """A6 shipped a helper whose body was guarded and whose CALL SITE was not,
    and the defect came back with every guard green. So this asserts the wiring,
    not the rule: the save path must run the check, and the field must run it as
    the operator types."""
    save = _js_func("saveConfig")
    assert "cfgFieldCheck(" in save, "saveConfig no longer validates anything"
    assert "_cfgValidate(" in _js_func("cfgFieldCheck"), "the check stopped reading the rule"
    # rendered fields must carry the live check too
    load = _js_func("loadConfig")
    assert "cfgFieldCheck(this)" in load, "a config field is rendered without its check"


def test_a_value_nobody_typed_as_a_number_is_refused() -> None:
    """Every one of these passed the old integer test and was stored as the
    number on the right: hex as 16, binary as 3, exponent as 1000, a leading
    plus as 5. A negative passed the browser and was rejected by the API on a
    round trip, which is a worse way to learn it. The rule is a digit test now,
    stated in words at the field, and the sign is allowed only when the key's
    own minimum is negative — read from the schema rather than assumed."""
    v = _js_func("_cfgValidate")
    assert "d+$/" in v, "the digit rule is gone"
    flat = v.replace(" ", "")
    # Both patterns must exist AND the choice between them must be the key's
    # own minimum. Asserting only that the comparison appears somewhere passed
    # while the rule was hardcoded, because the same comparison also picks the
    # wording of the message below it.
    assert "(min<0?" in flat and flat.count("d+$/") >= 2, (
        "the sign rule is hardcoded instead of chosen by the key's own minimum"
    )
    assert "isSafeInteger" in v, "a number too large to store exactly is accepted again"


def test_save_is_blocked_while_any_field_is_invalid() -> None:
    """Editing two fields and getting one wrong used to PUT the good one and
    report success, leaving the operator believing both had landed. Nothing goes
    until everything is valid, and every field is checked rather than stopping
    at the first, so they are all shown at once."""
    save = _js_func("saveConfig")
    head = save[: save.index("const updates=")]
    assert "filter(" in head and "return;" in head, (
        "the save no longer refuses before it builds the payload"
    )
    assert head.index("cfgFieldCheck") < head.index("return;"), (
        "the refusal is not gated on the check"
    )
    assert "api(" not in head, "a request can still be sent while a field is invalid"
    # ⚠ ADDED AT THE MERGE GATE. Everything above asserts the refusal is WRITTEN
    # and correctly ordered — none of it asserts the branch can FIRE. Swapping
    # the condition for a constant false keeps every string above present, in
    # order, and passes: the save then PUTs a payload built from fields it has
    # just found invalid. Same family as A6's helper that nothing called.
    cond = re.search(r"if\s*\(([^)]*)\)\s*\{\s*bad\[0\]", head)
    assert cond, "the refusal is no longer a branch on the failed set"
    assert "bad" in cond.group(1), (
        "the refusal is gated on something other than the failed fields — a "
        "constant here reopens the exact defect: " + cond.group(1)
    )


def test_an_invalid_field_announces_itself() -> None:
    """A red border is not a message. The console had one aria-invalid in the
    whole file and no error class on any input, so this is the first shared
    version of both."""
    load = _js_func("loadConfig")
    assert 'aria-invalid="false"' in load, "fields render with no validity state"
    assert "aria-describedby" in load, "the message is not bound to the field"
    assert 'role="status"' in load and 'aria-live' in load, (
        "the message is written into an element nothing announces"
    )
    check = _js_func("cfgFieldCheck")
    assert "aria-invalid" in check, "the validity state is never updated"


def test_the_invalid_state_reads_as_an_exception() -> None:
    """A validation message is a status, so it answers to the same vocabulary as
    the rest: an invalid field is an EXCEPTION, not an absence. The sentence
    takes the exception ink and the mark takes full strength.

    Measured from the tokens in both themes, because the panel a card sits on
    and the theme both move underneath this."""
    rule = _scope(".fielderr{")
    assert "color:var(--ink)" in rule, "the message dropped to a quieter ink"
    assert "var(--breach-bg)" in rule, "the message lost its mark"
    for theme in ("dark", "light"):
        for panel in ("--surf", "--surf2"):
            word = _ratio(_token("--ink", theme), _token(panel, theme))
            assert word >= 4.5, f"{theme} the message is {word:.2f}:1 on {panel}"
            mark = _ratio(_token("--breach-bg", theme), _token(panel, theme))
            assert mark >= 3.0, f"{theme} the mark is {mark:.2f}:1 on {panel}"
    # the field itself has to change too — a message with no anchor is a footnote
    assert "border-color:var(--breach-bg)" in _scope(".cin.invalid{"), (
        "the invalid input no longer marks itself"
    )


def test_both_new_searches_reset_the_pager() -> None:
    """Both lists page locally. Filter to three results while sitting on page
    three and the table is empty under a pager insisting there are results."""
    for fn in ("orgSearchChanged", "staffSearchChanged"):
        body = _js_func(fn)
        assert "pgReset(" in body, f"{fn} leaves the reader on a page that no longer exists"


def test_both_pagers_count_the_list_the_table_contains() -> None:
    """foxPageSlice derives its total from the array it is handed, so the count
    is only honest if the FILTERED array is what gets handed over. Passing the
    unfiltered one states a number the table does not contain — the same defect
    class as a revenue KPI counting rows it is not showing."""
    for fn, filt, raw in (("renderOrgs", "_orgsFiltered()", "ORGS_ALL"),
                          ("renderStaff", "_staffFiltered()", "STAFF_CACHE")):
        body = _js_func(fn)
        assert filt in body, f"{fn} does not filter"
        slice_call = body[body.index("foxPageSlice("):]
        slice_call = slice_call[: slice_call.index(")")]
        assert raw not in slice_call, (
            f"{fn} pages the unfiltered list, so the pager counts rows the table "
            f"is not showing"
        )


def test_a_filtered_list_says_so() -> None:
    """An operator must never be able to read a filtered list as the whole list.
    The leads board already states its scope in words for this reason and is the
    precedent being copied."""
    for page, node in (("orgs", "orgScope"), ("staff", "staffScope")):
        mk = _nocomment(_page(page))
        el = re.search(r'<div[^>]*id="%s"[^>]*>' % node, mk)
        assert el, f"{page} has no scope line"
        assert 'role="status"' in el.group(0) and "aria-live" in el.group(0), (
            f"{page}'s scope line changes silently"
        )
    for fn, node in (("renderOrgs", "orgScope"), ("renderStaff", "staffScope")):
        body = _js_func(fn)
        assert node in body and "matching" in body, f"{fn} never states its scope"
        assert " of " in body, f"{fn} states a count without saying what it is out of"


def test_an_empty_result_is_not_an_empty_list() -> None:
    """Forty organizations and none matching `acme` is not the same page as no
    organizations at all, and saying the second when the first is true is a
    false statement about the data."""
    for fn in ("renderOrgs", "renderStaff"):
        body = _js_func(fn)
        assert body.count("innerHTML='<tr>") >= 2 or body.count('innerHTML=\'<tr>') >= 2, (
            f"{fn} has only one empty state"
        )
        assert "match" in body.lower(), f"{fn} does not distinguish no-results from no-rows"


def test_both_new_searches_are_labelled() -> None:
    """Placeholders vanish the moment you type, so a placeholder is not a name."""
    for page, node in (("orgs", "orgSearch"), ("staff", "staffSearch")):
        mk = _nocomment(_page(page))
        assert 'for="%s"' % node in mk, f"{node} is unlabelled"
        assert re.search(r'<input[^>]*id="%s"' % node, mk), f"{node} is gone"


# ── M0 · the payment reference on staff plan activation ─────────────────────
# The modal is built by a JS template literal, not static markup, so these read
# the function source rather than _page("orgs"). Line comments are stripped
# first: this file explains its own guards in prose, and a guard that greps a
# body greps the sentence describing it (bitten twice already).


def _js_code(name: str) -> str:
    """`_js_func`, minus `//` line comments — the executable part only."""
    return re.sub(r"(?m)^\s*//.*$", "", _js_func(name))


def test_the_plan_modal_offers_a_labelled_payment_reference() -> None:
    """Staff take money outside the payment processor now, so the plan modal has
    to be able to record what was paid against. Labelled, because a placeholder
    disappears the moment somebody types into it."""
    body = _js_code("orgPlan")
    assert re.search(r'<input[^>]*id="mPayRef"', body), "the reference input is gone"
    assert 'for="mPayRef"' in body, "mPayRef is unlabelled"
    assert 'maxlength="128"' in body, (
        "the input does not state the limit the API enforces, so an overlong "
        "reference is only discovered as a 422 after Apply"
    )


def test_the_plan_modal_posts_the_reference_it_collects() -> None:
    """A6's lesson, applied: a control that exists and is never read is exactly
    as useless as no control. Assert the CALL SITE — that `_doPlan` reads
    `mPayRef` and puts it in the body it actually POSTs."""
    body = _js_code("_doPlan")
    assert "mPayRef" in body, "_doPlan never reads the input"
    assert "body.payment_reference" in body, "the value never reaches the request"
    post = body[body.index("'/admin/v1/organizations/"):]
    assert "JSON.stringify(body)" in post, (
        "_doPlan posts something other than the object it built"
    )


def test_a_blank_reference_is_not_sent_as_an_empty_string() -> None:
    """No placeholder, no invented reference: nothing typed means the key is
    absent, so the audit row does not claim a payment nobody cited."""
    body = _js_code("_doPlan")
    m = re.search(r"body\.payment_reference\s*=", body)
    assert m, "the assignment is gone"
    before = body[:m.start()]
    assert ".trim()" in before, "the value is not trimmed before it is judged"
    assert re.search(r"if\s*\(\s*\w+\s*\)\s*\{[^}]*$", before), (
        "the assignment is unconditional, so an untouched field posts \"\""
    )


# ── M3c · the audit trail's Detail column (register #93) ────────────────────
# `payment_reference` was write-only: staff could record what a customer paid
# against and then had no way to read it back. It is stored on the AdminAction
# row, `/admin/v1/audit` has always returned `detail`, and `loadAudit` rendered
# six columns and never it. For a payment taken outside the processor, that row
# is the only record there is.


def _audit_code(name: str) -> str:
    """A function's source with `//` line comments stripped.

    These blocks document their own rules — the comment above `auditDetail`
    names `payment_reference`, `esc` and `JSON.stringify` — so a structural check
    over raw text can be satisfied by the prose describing the rule instead of
    the rule itself. This file has been bitten by that shape more than once.
    """
    return re.sub(r"(?m)(?<!:)//.*$", "", _js_func(name))


def test_the_audit_table_has_a_detail_column() -> None:
    mk = _nocomment(_page("audit"))
    head = re.search(r"<thead>.*?</thead>", mk, re.S)
    assert head, "the audit table has no header"
    cols = re.findall(r'<th scope="col">([^<]*)</th>', head.group(0))
    assert cols == ["When", "Actor", "Action", "Target", "Org", "IP", "Detail"], cols


def test_load_audit_actually_renders_the_detail() -> None:
    """A6's lesson: a guard that reads a helper is not guarding its use. The
    helper stayed perfect while `renderStaff` stopped calling it and all 256
    guards stayed green. Assert the CALL SITE."""
    body = _audit_code("loadAudit")
    assert "auditDetail(a.detail)" in body, (
        "loadAudit no longer renders the detail, so the reference is write-only again"
    )
    assert 'data-label="Detail"' in body, "the stacked layout would not label the cell"
    assert 'class="kvcell"' in body, (
        "the detail cell lost the class its phone layout is keyed on"
    )


def test_every_audit_colspan_counts_the_new_column() -> None:
    """Three of them — loading, empty and fault. A stale colspan leaves the
    empty state a column short and visibly wrong."""
    body = _audit_code("loadAudit")
    assert 'colspan="6"' not in body and "faultRow(6" not in body
    assert 'colspan="7"' in body and "faultRow(7" in body
    mk = _nocomment(_page("audit"))
    loading = re.search(r'<tbody id="auRows"[^>]*>\s*<tr><td colspan="(\d+)"', mk)
    assert loading and loading.group(1) == "7", "the loading row spans the wrong width"


def test_the_detail_renderer_escapes_both_halves() -> None:
    """`detail` is JSONB and untrusted at BOTH ends: the value is free text a
    staff member typed, and a hand-written row can carry any key at all. This
    console has an esc() helper; neither half may be interpolated raw."""
    body = _audit_code("auditDetail")
    assert "esc(k)" in body, "the detail KEY is interpolated unescaped"
    assert "esc(s)" in body, "the detail VALUE is interpolated unescaped"
    assert not re.search(r"\+\s*(k|s)\s*\+", body), (
        "a raw key or value is concatenated into the markup"
    )


def test_the_detail_value_is_reproduced_exactly() -> None:
    """This is the only record a payment made outside the processor has, so the
    reference must come back out in the case it went in: no truncation, no
    ellipsis, and NOT `.tag`'s uppercase — an invoice number is case-sensitive."""
    body = _audit_code("auditDetail")
    assert ".slice(" not in body and ".substring(" not in body, "the value is truncated"
    assert "toUpperCase" not in body and "toLowerCase" not in body
    kvv = re.search(r"\.kvv\{([^}]*)\}", _style_block())
    assert kvv, ".kvv is gone"
    assert "text-transform" not in kvv.group(1), (
        "the value is case-folded, so an invoice reference cannot be read back"
    )
    assert "overflow-wrap:anywhere" in kvv.group(1), (
        "a long unbroken reference will widen the table instead of wrapping"
    )


def test_an_absent_detail_says_so_rather_than_rendering_nothing() -> None:
    """Most admin actions carry no detail. An empty cell reads as a rendering
    failure; the em-dash is the absence marker the other five cells already use.

    Asserted on the two EARLY RETURNS, not on the character appearing somewhere
    in the function. The first version counted em-dashes anywhere in the body and
    survived a mutation that emptied both returns — because a third em-dash lives
    in the null-value expression further down and kept the count above zero.
    """
    body = _audit_code("auditDetail")
    returns = re.findall(r"return\s+('[^']*'|\"[^\"]*\");", body)
    assert len(returns) >= 2, "the absent-detail early returns are gone"
    for literal in returns:
        assert "—" in literal, (
            f"an absent detail returns {literal} — an empty cell reads as a "
            f"rendering failure, not as 'there was nothing here'"
        )


def test_the_detail_cell_stacks_on_a_phone() -> None:
    """The stacked layout is a `space-between` flex row whose label does not
    shrink, so the pairs were squeezed into what was left and wrapped one
    character per line, with long values pushed outside the card. Found by
    rendering 375px, not by reading it."""
    rule = re.search(r"\.tbl\.cardify td\.kvcell\{([^}]*)\}", _style_block())
    assert rule, "the phone layout for the detail cell is gone"
    assert "flex-direction:column" in rule.group(1)
    assert "align-items:flex-start" in rule.group(1)


# ── M3d · the Paddle events feed and its replay (#98 · #103) ────────────────


def _pe_code(name: str) -> str:
    """A function's source with `//` line comments stripped — these blocks
    explain their own rules, and a structural check over raw text is otherwise
    satisfied by the prose describing the rule rather than the rule."""
    return re.sub(r"(?m)(?<!:)//.*$", "", _js_func(name))


def test_the_revenue_page_shows_the_paddle_feed() -> None:
    """Paddle is the processor that actually takes money here. It had a feed
    beside Stripe's from M3d; S1 removed Stripe's, so this is now the only
    webhook log on the console — which is why the seRows assertion this guard
    used to carry ("the Stripe feed was displaced rather than joined") is gone
    rather than inverted. Absence is asserted by
    test_the_revenue_page_does_not_name_stripe_as_the_processor below."""
    mk = _nocomment(_page("revenue"))
    assert 'id="peRows"' in mk, "the Paddle events table is gone"
    assert 'id="pePager"' in mk
    assert "Paddle events" in mk


def test_the_revenue_page_does_not_name_stripe_as_the_processor() -> None:
    """S1 · Paddle takes the money. Two KPI tooltips on this page still read
    "Invoices Stripe marked paid" and "Orgs whose Stripe subscription status" —
    false sentences over correct numbers, because /admin/v1/revenue applies NO
    provider filter at all: it sums every Invoice whose status is 'paid',
    whoever recorded it, and groups Organization.subscription_status the same
    way. The defect class this console has now produced seven times is a true
    figure under a caption that describes something else.

    SCOPED TO THE REVENUE PAGE ON PURPOSE. A file-wide search would fight the
    data browser, which still lists `stripe_events` and the `stripe_*` columns —
    correctly, because the table and the columns still exist. Only the surface
    that tells an operator who took the money is covered.

    COMMENTS ARE STRIPPED IN BOTH SYNTAXES. This file argues with itself in
    prose, and S1's own comments say the word "Stripe" repeatedly precisely
    because Stripe is what they are about — including one INSIDE loadRevenue's
    body, which _js_func returns verbatim. A guard that greps raw source here
    finds its own explanation and calls it the defect.
    """
    parts = [_page("revenue")]
    # the loaders that paint this page — the tooltips are markup, but a caption
    # naming a vendor is just as false when a loader writes it into .kfoot.
    parts += [_js_func(fn) for fn in ("loadRevenue", "paddleFilterChanged",
                                      "loadPaddleEvents", "replayPaddle",
                                      "_doReplayPaddle")]
    prose = re.sub(r"<!--.*?-->|/\*.*?\*/", "", "\n".join(parts), flags=re.S)
    assert "stripe" not in prose.lower(), (
        "the revenue page still names Stripe as the processor: %s"
        % [ln.strip()[:90] for ln in prose.splitlines() if "stripe" in ln.lower()]
    )


def test_the_paddle_feed_is_actually_loaded() -> None:
    """A table nobody fills is worse than no table: it reads as 'no events'."""
    assert "loadPaddleEvents()" in _pe_code("loadRevenue"), (
        "the revenue page never calls loadPaddleEvents, so the feed stays on "
        "'loading…' forever"
    )


def test_the_paddle_feed_reads_the_purpose_built_route() -> None:
    """NOT the generic data browser: that returns every column minus a denylist,
    and a Paddle payload carries the customer's name, email and address."""
    body = _pe_code("loadPaddleEvents")
    assert "/admin/v1/billing/payment-events" in body
    assert "/admin/v1/data/" not in body


def test_replay_is_offered_only_where_it_can_help() -> None:
    """`failed` and `received` are the rows where the handler demonstrably did
    not finish. Re-running a processed one can only apply stale state."""
    body = _pe_code("loadPaddleEvents")
    m = re.search(r"const replayable=\(([^)]*)\)", body)
    assert m, "the replayable test is gone"
    assert "'failed'" in m.group(1) and "'received'" in m.group(1)
    assert "'processed'" not in m.group(1) and "'ignored'" not in m.group(1)
    assert "canReplay&&replayable" in body, (
        "the button is rendered without checking both the role and the status"
    )


def test_the_replay_button_goes_through_a_confirmation() -> None:
    """Replay is irreversible-shaped. It must not fire from the row."""
    body = _pe_code("loadPaddleEvents")
    assert "replayPaddle(" in body
    assert "_doReplayPaddle(" not in body, (
        "the row fires the replay directly, with no confirmation step"
    )
    assert "_doReplayPaddle(" in _pe_code("replayPaddle"), (
        "the confirmation never reaches the request"
    )


def test_the_confirmation_says_what_running_it_again_does() -> None:
    """Vague confirmations get clicked through. This one has to name the two
    things a staff member would want to know: that no money moves, and that a
    brand-new workspace gets emailed."""
    body = _pe_code("replayPaddle")
    low = body.lower()
    assert "charged" in low and "refunded" in low, (
        "the modal does not say whether money can move"
    )
    assert "set-password" in low or "email" in low, (
        "the modal does not warn that a new workspace is emailed"
    )


def test_the_paddle_row_escapes_everything_it_renders() -> None:
    """`type` and `error` come from a processor, and the id is bound into an
    inline handler — which crosses two parsers, which is what `jsq` is for."""
    body = _pe_code("loadPaddleEvents")
    for field in ("e.type", "e.status"):
        assert f"esc({field}" in body, f"{field} is rendered unescaped"
    assert "esc((e.error" in body, "the processor's error text is rendered unescaped"
    assert "jsq(e.id)" in body, "the id is interpolated into an onclick without jsq"


def test_the_paddle_feed_has_a_fault_state_that_retries_itself() -> None:
    body = _pe_code("loadPaddleEvents")
    m = re.search(r"faultRow\(\s*(\d+)\s*,[^,]*,\s*\"([A-Za-z_$][\w$]*)\(", body)
    assert m, "the Paddle feed has no fault row"
    assert m.group(1) == "6", "the fault row spans the wrong number of columns"
    assert m.group(2) == "loadPaddleEvents", "the retry calls something else"




# ── M4c · the approvals queue ────────────────────────────────────────────────
# M4a made demo signups land in a `pending` state that cannot capture and cannot
# read its dashboard, and nothing surfaced the queue, so nobody could clear it.
# The 7-day clock starts at APPROVAL, which makes every hour of an unseen queue
# an hour of somebody else's demo.
#
# These read function bodies via _js_code (comments stripped), because this file
# explains its own guards in prose and a guard that greps a body greps the
# sentence describing it — which has bitten this surface twice.


def test_approve_posts_to_the_approve_route_and_not_the_plan_route() -> None:
    """THE one that had to exist.

    `set_organization_plan` with plan="free" grants a trial and leaves
    `approval_status` untouched: the org stays locked, the applicant stays
    blocked, and the staff member walks away believing they helped. Approve is
    the only route that starts the clock, and M4a's own report flags the
    confusion as the likely mistake.
    """
    body = _js_code("_doApprove")
    assert "/approve'" in body or '/approve"' in body, (
        "_doApprove no longer posts to the approve route")
    assert "'/admin/v1/organizations/'+id+'/approve'" in body.replace('"', "'"), (
        "the approve call is not built from the org id and the approve path")
    assert "method:'POST'" in body.replace('"', "'"), "approve is not a POST"
    for wrong in ("/plan", "/suspend", "/trial", "/enable", "/offboard"):
        assert wrong not in body, f"_doApprove reaches for {wrong}"


def test_the_approve_button_is_wired_to_approve() -> None:
    """A6's lesson: guarding a helper is not guarding its use. A perfect
    `_doApprove` nobody calls is exactly as broken as no `_doApprove`."""
    header = _js_code("renderO3Header")
    assert "orgApprove(" in header, "the org header renders no approve control"
    modal = _js_code("orgApprove")
    assert "_doApprove(" in modal, (
        "the approve modal's button does not call _doApprove — it is wired to "
        "something else, or to nothing")


def test_approve_is_offered_only_to_a_workspace_that_is_waiting() -> None:
    """An approve button on an active tenant is a button that restarts a trial
    somebody is already using. The route 409s, but the console should not be
    offering it."""
    header = _js_code("renderO3Header")
    m = re.search(r"if\(_orgIsPending\(d\)\)\{(.*?)\}", header, re.S)
    assert m, "the approve control is not gated on the workspace being pending"
    assert "orgApprove(" in m.group(1), "approve is rendered outside the pending gate"


def test_pending_is_read_as_pending_and_never_as_not_approved() -> None:
    """NULL is not a state this feature owns — it means the organisation never
    came through the demo route, which is true of every row created before M4a
    and of every org a purchase makes. Asking the negative puts the whole tenant
    list in the queue."""
    body = _js_code("_orgIsPending")
    assert "==='pending'" in body.replace('"', "'").replace(" ", ""), (
        "the pending test is no longer an equality against 'pending'")
    assert "!==" not in body, "the pending test is phrased as a negation"
    assert "approved" not in body, (
        "the pending test reasons about 'approved', so NULL rows fall into the queue")


def test_the_queue_is_a_client_side_filter_over_the_list_already_loaded() -> None:
    """A7's pattern, reused rather than re-invented. `loadOrgs` already holds
    every row and pages locally, so the queue needs no endpoint, no query
    parameter and no index."""
    filt = _js_code("_orgsFiltered")
    assert "ORG_PENDING_ONLY" in filt, "the filter does not consult the queue toggle"
    assert "_orgIsPending" in filt, "the filter does not use the shared predicate"
    loader = _js_code("loadOrgs")
    for param in ("approval_status=", "pending=", "&status="):
        assert param not in loader, (
            f"loadOrgs sends {param} — the queue became a backend query")


def test_changing_the_queue_filter_resets_the_page() -> None:
    """Filter to two rows while sitting on page three and the table is empty
    under a pager insisting there are results. The search control already had to
    learn this."""
    body = _js_code("orgPendingChanged")
    assert "pgReset('orgs')" in body.replace('"', "'"), "the pager is not reset"
    assert "renderOrgs()" in body, "the table is not re-rendered"


def test_a_waiting_workspace_is_marked_as_the_exception_it_is() -> None:
    """The status vocabulary is EXPECTED / EXCEPTION / ABSENCE, and a workspace
    waiting on a human is the definition of the row somebody has to look at.

    NOT `.dim`: that tier is ABSENCE, its ~1.5:1 mark is exempt precisely
    because it carries no meaning, and the note is explicit that the exemption
    does not cover a meaning-bearing state. A6 found exactly this mislabel on
    MFA-off and moved it to `.warn`.
    """
    for fn in ("renderOrgs", "renderO3Header"):
        body = _js_code(fn)
        m = re.search(r"_orgIsPending\([od]\)\?'<span class=\"chip (\w+)\"", body)
        assert m, f"{fn} does not render a chip for a waiting workspace"
        assert m.group(1) == "warn", (
            f"{fn} marks a waiting workspace `.{m.group(1)}` — the queue's own "
            "status is in the wrong tier")


def test_approve_confirms_before_it_fires_and_says_what_it_starts() -> None:
    """Irreversible-shaped: the 7 days begin the moment it lands, and leaving a
    workspace in the queue costs the applicant nothing. Both facts belong in
    front of the button, not in a runbook."""
    body = _js_code("orgApprove")
    assert "openModal(" in body, "approve fires without a confirmation step"
    # A token being PRESENT is not the same as it being REACHED — the A6 lesson
    # in a new costume. `_doApprove(id); return; openModal(...)` satisfied the
    # line above with the confirmation left as dead code, and this file has a
    # whole section about that shape. With string literals stripped, the only
    # reference to _doApprove in this function lives inside footHTML, so seeing
    # it in the executable part means something calls it directly.
    code = re.sub(r"'[^']*'", "", body)
    assert "_doApprove(" not in code, (
        "approve is invoked directly, so the confirmation never gates it")
    assert "7 days start" in body, "the confirmation does not say what it starts"
    assert "no undo" in body, "the confirmation does not say it cannot be taken back"


def test_declining_reuses_suspend_rather_than_inventing_a_state() -> None:
    """M4a considered a `rejected` value and chose against it: suspend plus
    suspended_reason already refuses a workspace on every auth channel, and a
    second vocabulary would put a new reason string on three surfaces to say one
    word."""
    body = _js_code("orgDecline")
    assert "orgSuspend(" in body, "decline does not delegate to the suspend control"
    assert "/reject" not in SRC, "a reject endpoint was invented"
    assert "'rejected'" not in SRC and '"rejected"' not in SRC, (
        "a `rejected` approval state was invented")


def test_approve_reuses_the_consoles_own_step_up_path() -> None:
    """`api()` intercepts a 403 carrying step_up_required, opens the emailed-code
    modal and retries once. A second implementation is a second thing to keep in
    step — and the route is step-up gated, so a handler that bypassed `api()`
    would simply fail."""
    body = _js_code("_doApprove")
    assert re.search(r"\bapi\(", body), "approve does not go through api()"
    assert "_apiRaw(" not in body, "approve bypasses the step-up interceptor"
    assert "step-up/request" not in body, "approve rolls its own step-up"


def test_the_approve_failure_message_is_readable_by_a_human() -> None:
    """This route answers 409 with an OBJECT ({code,message}), unlike the string
    detail every older handler here expects. Reading `d.detail` straight into
    toast() renders "[object Object]" on the one screen that has to explain why
    nothing happened."""
    body = _js_code("_doApprove")
    assert "det.message" in body.replace(" ", ""), (
        "the structured detail's message is never read")
    assert "r.status" in body, "the status code is never surfaced as a fallback"


def test_the_empty_queue_states_what_it_observed_and_not_why() -> None:
    """DEMO_APPROVAL_REQUIRED ships OFF, so on most deployments this is the
    NORMAL state, not an edge case.

    "nobody is waiting" and "approval is switched off" are different facts and
    the console cannot tell them apart — the flag is not in CONFIG_SCHEMA, so
    /admin/v1/config does not carry it and no admin route returns it. So the
    sentence claims only the observation and points at where the rest of the
    answer lives. A confident wrong reason here is worse than a narrow one.
    """
    # BLOCK comments stripped too, not just `//`. `_js_code` removes line
    # comments only, and the prose explaining this very decision sits in a /* */
    # above the branch and contains the phrases below — so the first run of this
    # guard failed on its own explanation. Third time on this surface; the fix is
    # to read what the branch RENDERS, never what it says about itself.
    body = re.sub(r"/\*.*?\*/", "", _js_code("renderOrgs"), flags=re.S)
    # The BRANCH, not just the sentence. `if(false){…}` left the wording in the
    # file as dead code and the assertion below passed while a filtered-empty
    # queue fell through to "no organizations match" — blaming a search nobody
    # typed. Same shape as the approve survivor above.
    assert "if(!rows.length&&ORG_PENDING_ONLY&&!ORG_Q){" in body.replace(" ", ""), (
        "the queue-empty branch is gone or no longer reachable")
    assert "No organizations are awaiting approval." in body, (
        "the queue has no empty state of its own, so it falls through to the "
        "search wording and blames a query nobody typed")
    assert "DEMO_APPROVAL_REQUIRED" in body, (
        "the empty state does not say where the other half of the answer lives")
    assert "cannot read" in body, (
        "the empty state does not admit which half of it the console knows")
    for guess in ("approval is off", "approvals are disabled", "is switched off"):
        assert guess not in body, (
            f"the empty state asserts {guess!r} — a state this console cannot observe")


def test_the_overview_count_only_appears_when_somebody_is_waiting() -> None:
    """It rides on the Organizations KPI footer, which already enumerates access
    states. A standing "0 awaiting approval" would be noise on every deployment
    that has not turned the flag on — which is all of them today — and a bento
    panel of its own would be the unearned system this surface has been burned
    by before."""
    body = _js_code("loadOverviewExtras")
    assert "kOrgsFoot" in body, "the overview never reports the queue"
    assert re.search(r"if\(n&&foot\)", body.replace(" ", "")), (
        "the count is rendered unconditionally, so an empty queue writes a zero")
    assert "goApprovals()" in body, "the overview count is not a way into the queue"


def test_the_overview_count_does_not_use_link_ink_on_a_tinted_face() -> None:
    """`.linklike` paints `--fox2`, which is tuned against the flat panel. On the
    Organizations KPI — a saturated `k-azure` face — it measures 1.10:1 dark and
    1.67:1 light against the gradient stops. Measured on the real card, not
    guessed: `.kfoot`'s own ink, mixed by A1 for exactly these surfaces, holds
    8.54 / 5.56, and inheriting it means the two cannot drift apart.

    This is the trap this surface keeps re-learning — a token correct on one
    surface is not thereby correct on another — so it gets a guard rather than a
    comment.
    """
    body = _js_code("loadOverviewExtras")
    seg = body[body.index("kOrgsFoot"):]
    assert "color:inherit" in seg.replace(" ", ""), (
        "the queue link paints its own colour on a tinted KPI face")
    assert "text-decoration:underline" in seg.replace(" ", ""), (
        "with the colour inherited, nothing marks the link as actionable")

def test_the_overview_count_cannot_break_the_page_it_enriches() -> None:
    """Same best-effort contract as the health and alerts blocks beside it: the
    footer the stats endpoint already wrote must survive this failing."""
    body = _js_code("loadOverviewExtras")
    seg = body[: body.index("kOrgsFoot")]
    assert "try{" in seg, "the queue count is not wrapped"
    assert "insertAdjacentHTML" in body, (
        "the count replaces the footer instead of appending to it, so a failure "
        "or a re-render loses the stats line")


def test_the_jump_ticks_the_control_the_operator_can_see() -> None:
    """Setting the state behind the checkbox would leave the table filtered and
    the control saying otherwise."""
    body = _js_code("goApprovals")
    assert "orgPendingOnly" in body, "the jump does not touch the visible control"
    assert ".checked=true" in body.replace(" ", ""), "the checkbox is not ticked"
    assert "orgPendingChanged()" in body, "the jump does not re-render through the filter"


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))


# ── P2 · #96 · a second click must not become a second request ───────────────
# `busy()` was written in A0 with an `aria-busy` guard, a spinner and
# `pointer-events:none`, and the register recorded it as never called. That was
# TRUE when written and is not now — A3 and A4 wired eleven row-level actions to
# it. What stayed unguarded was the MODAL family, and that is where the money is:
# Apply on Set-plan writes an `org.plan.set` audit row citing a payment
# reference, so a double-click makes the trail double-count one payment.
#
# Bodies are read with comments stripped: this file explains its own guards in
# prose and so does the console, and a guard that greps a body greps the sentence
# describing it — three phases running on this surface.

#: Every handler a modal footer can fire that changes something server-side.
_MODAL_WRITERS = (
    "_doApprove", "_doDeleteRow", "_doIp", "_doMfaConfirm", "_doMfaDisable",
    "_doOffboard", "_doPlan", "_doSuspend", "_doTrial", "_doRevoke",
    "_stepUpSubmit", "saveDataEdit",
)


def test_every_modal_write_is_guarded_against_a_second_click() -> None:
    """The whole family, not one button. Asserted at the CALL SITE — `busy()`
    being correct proved nothing for four phases while nothing invoked it."""
    for fn in _MODAL_WRITERS:
        body = _js_code(fn)
        assert re.search(r"(?<![\w-])busy\s*\(\s*btn\s*,", body), (
            f"{fn} can be fired twice — it does not route through busy()")


def test_every_modal_write_is_handed_the_button_it_must_disable() -> None:
    """`busy(btn, …)` is inert when `btn` is undefined, so the footer has to pass
    `this`. A handler that takes the argument and is never given it reads as
    guarded and is not — the same shape as the helper nobody called."""
    for fn in _MODAL_WRITERS:
        sig = re.search(r"function\s+" + fn + r"\s*\(([^)]*)\)", SRC)
        assert sig and sig.group(1).strip().endswith("btn"), (
            f"{fn} does not take a button as its last argument")
        calls = re.findall(r'onclick="' + fn + r'\(([^"]*)\)"', SRC)
        assert calls, f"{fn} is not reachable from any control"
        for args in calls:
            assert args.strip().endswith("this"), (
                f"a control calls {fn}({args}) without passing itself, so the "
                "guard is handed nothing to disable")


def test_no_modal_footer_gained_a_writer_that_skipped_the_pattern() -> None:
    """The list above is a snapshot; this is what stops the NEXT modal being the
    unguarded one.

    Asserts the INVARIANT, not membership of that list. The first version
    demanded the name appear in `_MODAL_WRITERS` and immediately failed on
    `_doReanchor` — which A3 had already wired correctly, and which my own footer
    scan had missed because its regex stopped at the first backtick. A guard that
    fails on code doing the right thing teaches people to edit the guard.
    """
    # Cancel and close write nothing and must never go inert: a modal whose
    # dismiss button is disabled mid-request is a trap.
    benign = {"closeModal", "closeDataEdit", "_stepUpDone"}
    # Every `footHTML:` up to the `})` that closes its openModal call. The first
    # version stopped at the next backtick, which truncated every footer built
    # from a template literal — so it silently scanned almost nothing, and the
    # `checked >=` floor below is what would have caught that.
    foots = re.findall(r"footHTML\s*:\s*(.*?)\}\)\s*;", SRC, re.S)
    named = set()
    for foot in foots:
        named |= set(re.findall(r'onclick="([A-Za-z_$][\w$]*)\(', foot))
    checked = 0
    for fn in sorted(named - benign):
        body = _js_code(fn)
        if not re.search(r"method\s*:\s*['\"](POST|PUT|DELETE|PATCH)", body):
            continue
        checked += 1
        assert re.search(r"(?<![\w-])busy\s*\(\s*btn\s*,", body), (
            f"{fn} writes from a modal footer and can be fired twice")
    assert checked >= len(_MODAL_WRITERS), (
        f"only {checked} modal writers found; expected at least "
        f"{len(_MODAL_WRITERS)} — the scan has rotted, so this guard is passing "
        "by finding nothing")


def test_busy_refuses_a_second_call_while_the_first_is_in_flight() -> None:
    """The guarantee itself. Everything else about this state is presentation."""
    # BLOCK comments stripped as well: `_js_code` removes `//` only, and the
    # comments inside `busy()` name the very identifiers asserted here — the
    # first version of this passed on its own prose. Fourth time on this surface.
    body = re.sub(r"/\*.*?\*/", "", _js_code("busy"), flags=re.S)
    assert re.search(r"aria-busy'\)==='true'\)\s*return", body.replace('"', "'")), (
        "busy() no longer refuses a second call, so the attribute is decoration")
    assert re.search(r"setAttribute\(\s*'aria-busy'\s*,\s*'true'\s*\)",
                     body.replace('"', "'")), "the attribute is never set"
    assert re.search(r"finally\s*\{[^}]*removeAttribute", body), (
        "a throw leaves the control permanently inert")


def test_going_busy_does_not_move_the_layout() -> None:
    """Measured, not reasoned about, and it took three attempts.

    In flow the mark added 9px plus the flex gap and the button grew 86px -> 99px
    — and the modal footer is `justify-content:flex-end`, so its neighbour slides
    too. Pinning the width in `busy()` was worse: the label wrapped to two lines
    and the button grew TALLER. What holds is taking the mark out of flow
    entirely, so the fix belongs in CSS and `busy()` stays a behaviour helper.
    """
    css = _style_block().replace(" ", "").replace("\n", "")
    rule = re.search(r"\.btn\[aria-busy=\"true\"\]::after\{(.*?)\}", css)
    assert rule, "the busy mark is gone"
    assert "position:absolute" in rule.group(1), (
        "the busy mark is back in flow, so the button widens and its neighbours "
        "move the moment it is pressed")
    assert ".btn{position:relative}" in css or "position:relative" in (
        re.search(r"(?<![\w.\[-])\.btn\{(.*?)\}", css).group(1)), (
        "the mark is absolute against something other than its own button")
    body = re.sub(r"/\*.*?\*/", "", _js_code("busy"), flags=re.S)
    assert "offsetWidth" not in body and "style.width" not in body, (
        "busy() is measuring geometry again — that attempt wrapped the label")


def test_the_busy_state_keeps_its_label_readable_on_every_plate() -> None:
    """A3 measured this the first time the state got a caller: `--muted` on a
    coloured plate put the label at 1.45:1. P2 gives `.pri` and `.danger` many
    more callers, so the overrides that fixed it are pinned rather than trusted."""
    css = _style_block()
    for sel, ink in (("pri", "--on-pri"), ("danger", "#fff"), ("safe", "--safe-tx")):
        # `var(` is optional because two of the three are tokens and one is a
        # literal — asserting the token NAME without it silently matched nothing.
        pat = r"\.btn\." + sel + r"\[aria-busy=\"true\"\]\{color:(var\()?" + re.escape(ink)
        assert re.search(pat, css.replace(" ", "")), (
            f".btn.{sel} lost its busy-state ink and falls back to --muted")
    assert 'aria-busy="true"]{cursor:progress' in css.replace(" ", ""), (
        "the busy state no longer marks itself non-interactive")


# ── C0 · register #91 · the hairline between two touching chart segments ─────
# Every status hue in this file was tuned to clear 3:1 against the PANEL.
# Passing that pins them all into one luminance band, so measured against EACH
# OTHER: dark safe/warn 1.15:1, safe/bad 1.83:1, warn/bad 2.12:1; light
# safe/bad 1.01:1, warn/bad 1.04:1, safe/warn 1.05:1. 0 of 15 pairs clear 3:1,
# in BOTH themes. Line and area charts are fine -- the series are physically
# apart and the legend names them. The exposure is fills that TOUCH, where hue
# is the only thing dividing them: stacked bars, grouped bars, donut arcs.
#
# THESE GUARDS RUN THE REAL ENGINE AND READ THE SVG IT EMITS. Asserting that
# _segSep contains the word "stroke" would stay green the moment a draw call
# stopped calling it -- the A6 lesson, guard the USE. And asserting that a
# stroke attribute merely EXISTS is exactly what the dashboard already had: it
# shipped stroke:var(--bc) at 7% alpha, which measures 1.03:1 of separation
# from its own fill. Present in the markup, invisible on screen. So the
# assertion is on the emitted VALUE.

_CHART_SHIM = """
// PANEL is what the parent actually PAINTS. --surf and --line are stubbed to
// two OTHER values deliberately: a _panelBg that gave up and read a token off
// :root, or a _segSep that reached for --line, each comes back as a value this
// probe can name and reject. Making any of the three equal would let one of
// those mutations through green.
var PANEL='rgb(27, 26, 25)', LINE='rgb(255, 0, 255)', ROOTSURF='rgb(9, 9, 9)';
function El(bg,parent){ return {nodeType:1,parentElement:parent||null,__bg:bg,
  clientWidth:600, style:{}, innerHTML:'',
  classList:{add:function(){},remove:function(){}},
  querySelector:function(){return null;}, querySelectorAll:function(){return [];}}; }
global.getComputedStyle=function(n){ return {backgroundColor:n.__bg,
  getPropertyValue:function(p){ return p==='--line'?LINE:(p==='--surf'?ROOTSURF:'#888'); }}; };
global.document={documentElement:{},getElementById:function(){return null;}};
global.window={};
function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;')
  .replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
function _drawIn(){} function _bindChartTT(){}
var _RM={matches:true}; function reduced(){return true;}
var _DASH=['0','5 4','2 3','8 3 2 3','1 4'];
"""

_CHART_FNS = ("_cssvar", "_panelBg", "_segSep", "_niceMax", "_fmtNum",
              "_chartPalette", "chart", "_chartXY", "_chartDonut", "_arc")


def _js_decl(name: str) -> str:
    """`function name(...){...}` INCLUDING its header -- _js_func returns only
    the body, which cannot be re-declared inside a probe."""
    m = re.search(r"(?:async\s+)?function\s+" + re.escape(name) + r"\s*\(", SRC)
    assert m, "%s() is gone" % name
    i = SRC.index("{", m.end() - 1)
    depth, j = 0, i
    while j < len(SRC):
        if SRC[j] == "{":
            depth += 1
        elif SRC[j] == "}":
            depth -= 1
            if depth == 0:
                return SRC[m.start(): j + 1]
        j += 1
    raise AssertionError("%s() never closes" % name)


def _draw(opts: dict) -> list:
    """Run the SHIPPED chart() over a stubbed DOM and return one dict per
    emitted data mark: whether it carries a stroke, that stroke's colour, and
    the extents a stroke would have to fit inside.

    The host is deliberately TRANSPARENT and its PARENT paints, so a result
    carrying the panel colour also proves _panelBg walked up rather than
    reading the element it was handed. --line is stubbed to magenta and must
    appear nowhere in a correct result.
    """
    import json
    import os
    import tempfile
    probe = (_CHART_SHIM
             + "\n".join(_js_decl(f) for f in _CHART_FNS)
             + "\nvar parent=El(PANEL,null), host=El('rgba(0, 0, 0, 0)', parent);\n"
             + "chart(host, " + json.dumps(opts) + ");\n"
             + "console.log(JSON.stringify(host.innerHTML));\n")
    fd, path = tempfile.mkstemp(suffix=".js")
    os.close(fd)
    try:
        Path(path).write_text(probe, encoding="utf-8")
        proc = subprocess.run([shutil.which("node"), path],
                              capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
        html = json.loads(proc.stdout.strip().splitlines()[-1])
    finally:
        os.unlink(path)
    marks = []
    for tag in re.findall(r'<(?:rect|path) class="chart-dpt"[^>]*>', html):
        st = re.search(r'\sstroke="([^"]*)"', tag)
        h = re.search(r'\sheight="([\d.]+)"', tag)
        w = re.search(r'\swidth="([\d.]+)"', tag)
        marks.append({"tag": tag,
                      "stroke": st.group(1) if st else None,
                      "height": float(h.group(1)) if h else None,
                      "width": float(w.group(1)) if w else None})
    assert marks, "the engine emitted no data marks at all"
    return marks


_STACKED = {"type": "stackedbar", "height": 200, "labels": ["a", "b", "c"],
            "series": [{"name": "graded", "values": [50, 60, 55]},
                       {"name": "failed", "values": [10, 8, 12]},
                       {"name": "pending", "values": [5, 7, 6]}]}
_DONUT = {"type": "donut", "height": 200,
          "series": [{"name": "free", "value": 40},
                     {"name": "pro", "value": 30},
                     {"name": "max", "value": 30}]}


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_touching_chart_segments_are_drawn_with_a_boundary() -> None:
    """Grading throughput is graded/failed/pending in ONE bar and plan mix is
    one donut, so the only thing between two segments is hue -- and warn/bad
    measure 2.12:1 against each other in dark, 1.04:1 in light."""
    for label, opts in (("stacked", _STACKED), ("donut", _DONUT)):
        marks = _draw(opts)
        bare = [m["tag"][:80] for m in marks if not m["stroke"]]
        assert not bare, "%s: segments touch with no boundary: %s" % (label, bare)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_grouped_bars_are_separated_too() -> None:
    """NOT in this phase's brief, and it is the same defect. _chartXY gives each
    series w=bw/series.length at x=Xb(bi)-bw/2+si*w -- exactly adjacent, with no
    gap between them, so a grouped bar chart stands two status hues edge to edge
    just as a stacked one does."""
    marks = _draw({"type": "bar", "height": 200, "labels": ["a", "b"],
                   "series": [{"name": "x", "values": [5, 7]},
                              {"name": "y", "values": [6, 4]},
                              {"name": "z", "values": [3, 9]}]})
    assert all(m["stroke"] for m in marks), "grouped bars still touch bare"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_the_boundary_is_the_panel_behind_the_chart() -> None:
    """THE VALUE, not the presence. A boundary reads as a division only if it is
    the panel: --line is a third colour matching neither the panel nor the
    fills, and a constant cannot be right on both --surf and --surf2.

    The probe paints the PARENT and leaves the host transparent, so passing this
    also proves _panelBg walks up instead of reading the node it was handed.
    """
    for opts in (_STACKED, _DONUT):
        for m in _draw(opts):
            assert m["stroke"] == "rgb(27, 26, 25)", (
                "the boundary is %r, not the panel behind the chart" % m["stroke"])
            assert m["stroke"] != "rgb(255, 0, 255)", "the boundary is drawn in --line"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_a_segment_too_thin_for_a_stroke_keeps_its_whole_fill() -> None:
    """THE ONE THAT PROTECTS THE DATA. An SVG stroke is CENTRED on the edge, so
    a 1px stroke eats 0.5px of fill on each side and closes over any segment
    thinner than itself. One failed grading against a thousand graded ones is
    such a segment -- and it rendering as nothing would say "0 failed" on the
    page whose job is reporting failures. Below the threshold the stroke is
    dropped and the fill keeps its entire extent; the taller neighbours either
    side are stroked and still bound it.
    """
    marks = _draw({"type": "stackedbar", "height": 200, "labels": ["a", "b"],
                   "series": [{"name": "graded", "values": [1000, 1000]},
                              {"name": "failed", "values": [1, 1]},
                              {"name": "pending", "values": [400, 400]}]})
    thin = [m for m in marks if not m["stroke"]]
    assert thin, ("every segment was stroked, so the 1-in-1000 sliver carries a "
                  "stroke wider than itself and is no longer visible")
    for m in thin:
        assert m["height"] and m["height"] > 0, "a real value was drawn with no height"
    # and the thick ones still got theirs, or "nothing is stroked" passes this
    assert [m for m in marks if m["stroke"]], "the thin case switched the fix off entirely"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_a_segment_that_touches_nothing_gets_no_boundary() -> None:
    """A single-series bar chart leaves a gap between bars (bw is 62% of the
    pitch) and a one-slice donut has no neighbour. Stroking those would draw a
    panel-coloured outline around a lone mark for no reason."""
    for opts in ({"type": "bar", "height": 200, "labels": ["a", "b"],
                  "series": [{"name": "only", "values": [5, 7]}]},
                 {"type": "donut", "height": 200,
                  "series": [{"name": "all", "value": 9}]}):
        assert not any(m["stroke"] for m in _draw(opts)), (
            "a mark with no neighbour was given a boundary anyway")


# ── C1 · trends on four KPI cards (register: the plan's C1) ──────────────────
# The four cards that gained a spark are hFailed, hAnchors, rvRev30 and kOrgs.
# Six others deliberately did not, and one guard below pins that.
#
# MOST OF THESE RUN THE CODE. C0 paid for this: a deleted `d.forEach` left all
# 369 dashboard tests green because every one of them was a grep. Where a rule
# is about what a function PRODUCES -- a null that must not become a zero, a
# series that must not be the wrong one -- the guard executes it and reads the
# result, and only the DOM-shape rules are matched statically.

_KPI_SHIM = """
var EL={};
function mk(id){ return EL[id]={id:id,textContent:'',innerHTML:'',style:{},
  classList:{add:function(){},remove:function(){}}}; }
['hFailedFoot','hAnchorsFoot','kOrgsTrend','rvRev30Foot','kLogsFoot',
 'hFailedSpark','hAnchorsSpark','kOrgsSpark','rvRev30Spark'].forEach(mk);
function $(id){ return EL[id]||null; }
var DREW=[];
function chart(id,o){ DREW.push({id:id,
  vals:(o.series&&o.series[0]&&o.series[0].values)||[], aria:o.ariaLabel||''}); }
function _spark(){ return '#FF6A1A'; }
function num(n){ return String(n); }
"""


def _run_kpi(fns: tuple, body: str) -> dict:
    """Declare the shipped functions over a DOM stub, run `body`, return R."""
    import json
    import os
    import tempfile
    probe = (_KPI_SHIM + "\n".join(_js_decl(f) for f in fns)
             + "\nvar R={};\n" + body + "\nconsole.log(JSON.stringify(R));\n")
    fd, path = tempfile.mkstemp(suffix=".js")
    os.close(fd)
    try:
        Path(path).write_text(probe, encoding="utf-8")
        # encoding="utf-8" is NOT optional here. text=True alone decodes node's
        # stdout with the system locale -- cp1252 on this project's Windows
        # runners -- and the ▲ / ▼ that carry the delta's direction come back as
        # mojibake, so an assertion about them fails against correct output.
        proc = subprocess.run([shutil.which("node"), path],
                              capture_output=True, text=True, encoding="utf-8")
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout.strip().splitlines()[-1])
    finally:
        os.unlink(path)


pytestmark_c1 = pytest.mark.skipif(shutil.which("node") is None,
                                   reason="node not on PATH")


@pytestmark_c1
def test_a_delta_against_an_empty_prior_window_is_not_a_zero() -> None:
    """THE ONE THAT PROTECTS THE NUMBER. /stats/timeseries returns delta_pct as
    null when prev_total is 0 -- a percentage against nothing is unanswerable,
    not 0%. Rendering it as "0%" would state that nothing changed on a card
    where everything did.

    Run, not grepped: the null branch and the zero branch differ by one
    comparison, and a guard that only checked the string 'no prior data' was
    present in the file would pass with the branch unreachable (A7).
    """
    r = _run_kpi(("_kpiDelta",), """
_kpiDelta('hFailedFoot', null, '30d', '99 total'); R.nul=EL.hFailedFoot.textContent;
_kpiDelta('hFailedFoot', 0,    '30d', '99 total'); R.zero=EL.hFailedFoot.textContent;
_kpiDelta('hFailedFoot', 12.4, '30d', '99 total'); R.up=EL.hFailedFoot.textContent;
_kpiDelta('hFailedFoot', -8,   '30d', '99 total'); R.down=EL.hFailedFoot.textContent;
""")
    assert "no prior data" in r["nul"], "a null delta does not say so: %r" % r["nul"]
    assert "0%" not in r["nul"], "a null delta rendered as a percentage: %r" % r["nul"]
    assert "%" not in r["nul"], "a null delta rendered as a rate at all: %r" % r["nul"]
    # and a REAL zero still reads as a measured zero, or the guard above is
    # satisfied by a function that answers 'no prior data' to everything
    assert "0%" in r["zero"], "a measured 0%% no longer renders: %r" % r["zero"]
    assert "no prior data" not in r["zero"], "a measured zero is being called absent"
    assert "▲" in r["up"] and "▼" in r["down"], "direction lost its glyph"


@pytestmark_c1
def test_a_delta_on_a_face_writes_no_colour_at_all() -> None:
    """P4 removed style.color from this line: an inline colour beats
    `.kpi .face .kfoot{color:var(--foxink)}`, so it measured 1.12:1 on
    .k-orange and all eight theme/face/direction combinations failed AA. It
    also painted a RISE in policy breaches green.

    test_direction_is_never_carried_by_colour_alone_on_a_face greps for
    `f.style.color=`. This runs the writer and asserts nothing landed on
    `style` at all -- which also catches setProperty, cssText, or a renamed
    local, none of which that grep can see.
    """
    r = _run_kpi(("_kpiDelta",), """
_kpiDelta('hFailedFoot', 12.4, '30d', '99 total');
R.keys=Object.keys(EL.hFailedFoot.style);
_kpiDelta('hFailedFoot', null, '30d', '');
R.keys2=Object.keys(EL.hFailedFoot.style);
""")
    assert r["keys"] == [], "the delta writer set %s on a KPI face" % r["keys"]
    assert r["keys2"] == [], "the null branch sets %s on a KPI face" % r["keys2"]


@pytestmark_c1
def test_the_health_delta_computes_its_own_null() -> None:
    """/admin/v1/health/trends carries no delta_pct -- unlike /stats/timeseries
    it returns daily arrays and nothing else -- so _halfDelta stands in for it
    and has to publish the SAME contract, including the null.

    The flat case is the half of this that matters: a function that returned
    null whenever it was unsure would satisfy 'empty prior window is null' and
    silently stop reporting real changes.
    """
    r = _run_kpi(("_halfDelta",), """
R.empty_prior=_halfDelta([0,0,0,0, 5,5,5,5]);
R.flat       =_halfDelta([3,3,3,3, 3,3,3,3]);
R.doubled    =_halfDelta([1,1,1,1, 2,2,2,2]);
R.halved     =_halfDelta([4,4,4,4, 1,1,1,1]);
R.nothing    =_halfDelta([]);
""")
    assert r["empty_prior"] is None, "a rise from nothing was given a percentage"
    assert r["nothing"] is None, "an empty window was given a percentage"
    assert r["flat"] == 0, "an unchanged window is not reported as unchanged"
    assert r["doubled"] == 100, "a doubling reads as %s%%" % r["doubled"]
    assert r["halved"] == -75, "a fall reads as %s%%" % r["halved"]


@pytestmark_c1
def test_the_anchor_spark_climbs_when_the_card_climbs() -> None:
    """THE ONE THAT STOPS AN INVERTED ALARM.

    #hAnchors counts orgs whose NEWEST anchor is failed or stale: up is bad.
    The obvious series in the same payload, anchors[].anchored, is anchoring
    ACTIVITY per day: up is healthy. Drawing that under this number gives it a
    line that rises when things get better beneath a figure that rises when
    they break -- P4's inverted alarm rebuilt as a geometry instead of a hue.

    `anchored - confirmed` is the only series here that moves with the card.
    Asserted on the emitted values, because both candidates come off the same
    array and a grep for 'anchored' cannot tell which one was drawn.
    """
    r = _run_kpi(("_healthSparks", "_kpiDelta", "_halfDelta"), """
var g=[{failed:1},{failed:2},{failed:3},{failed:4}];
var a=[{anchored:10,confirmed:10},{anchored:8,confirmed:5},
       {anchored:9,confirmed:2},{anchored:7,confirmed:7}];
_healthSparks(g,a);
R.drew=DREW;
""")
    drew = {d["id"]: d for d in r["drew"]}
    assert "hAnchorsSpark" in drew, "the anchor card lost its spark"
    vals = drew["hAnchorsSpark"]["vals"]
    assert vals == [0, 3, 7, 0], (
        "the anchor spark plots %s; anchored-confirmed is [0, 3, 7, 0]" % vals)
    assert vals != [10, 8, 9, 7], (
        "the anchor spark plots raw `anchored`, which rises when anchoring is "
        "HEALTHY -- under a number that rises when anchoring is BROKEN")
    assert drew["hFailedSpark"]["vals"] == [1, 2, 3, 4], (
        "the failed-grading spark no longer plots the daily failure count")
    for d in r["drew"]:
        assert d["aria"], "%s draws with no accessible name" % d["id"]


@pytestmark_c1
def test_the_masked_revenue_card_does_not_print_its_own_figure() -> None:
    """#rvRev30 goes through sens({mask:true}) so revenue is not readable over a
    shoulder until staff press the eye. The pattern the other cards use ends in
    `· 1,240 total`, and doing that here would print the money in cleartext one
    line under the control that hides it.

    Driven through the real loader against a stubbed API, because the rule is
    about what reaches the DOM: a grep for `d.total` in the function would pass
    the moment someone wrote the same number a different way.
    """
    # loadRevSpark is async, so the harness's own trailing print fires first
    # with R still empty and the resolved one lands after it. _run_kpi reads the
    # LAST line, which is the settled result.
    r = _run_kpi(("loadRevSpark", "_kpiDelta"), """
var api=function(){ return Promise.resolve({ok:true, json:function(){
  return Promise.resolve({metric:'revenue',unit:'cents',delta_pct:12.4,
    total:987654, points:[{day:'a',value:100},{day:'b',value:200}]}); }}); };
loadRevSpark().then(function(){
  R.foot=EL.rvRev30Foot.textContent;
  R.drew=DREW.length;
  console.log(JSON.stringify(R));
});
""")
    foot = r["foot"]
    assert "987654" not in foot and "9,876" not in foot, (
        "the 30-day revenue is printed under the masked value: %r" % foot)
    assert "$" not in foot, "an amount reached the foot of a masked card: %r" % foot
    # the percentage IS allowed -- a rate of change is not the amount
    assert "12%" in foot and "▲" in foot, (
        "the revenue card lost its delta entirely: %r" % foot)


def test_exactly_the_intended_cards_carry_a_trend() -> None:
    """OWNER DECISION, and the reason this is a guard rather than a comment.

    Six cards count a STOCK -- how many things exist right now -- and no daily
    history exists for any of them. A trend on "how many staff we have" would be
    noise even if the data did exist. They must stay bare, so a later phase
    cannot quietly add one; the ones that gained a spark must keep it.

    C2 added the four traffic cards to the second list. This stays ONE guard on
    purpose: it is the register of which cards carry a trend, and a second copy
    elsewhere is how two lists drift apart.
    """
    mk = _nocomment(SRC)
    for card in ("kUsers", "kStaff", "rvActive", "rvTrial", "hStatus", "hWorker"):
        assert 'id="%sSpark"' % card not in mk, (
            "%s is a stock with no daily history and has been given a spark" % card)
    for card in ("hFailed", "hAnchors", "rvRev30", "kOrgs", "kLogs", "kBreaches",
                 "tErr", "tMkt", "tApp", "tAdm"):
        assert 'id="%sSpark"' % card in mk, "%s lost its spark host" % card


def test_every_sparked_card_has_somewhere_to_write_its_delta() -> None:
    """A spark with no delta line states a shape and refuses to say how much;
    a delta line with no host is dead markup. Anchored to the ids, not to
    position -- S1 found a guard that matched "the first thead on the page" and
    silently changed subject when the panel above it was deleted.
    """
    mk = _nocomment(SRC)
    js = " ".join(_script_blocks())
    for host, foot in (("hFailedSpark", "hFailedFoot"),
                       ("hAnchorsSpark", "hAnchorsFoot"),
                       ("rvRev30Spark", "rvRev30Foot"),
                       ("kOrgsSpark", "kOrgsTrend"),
                       ("kLogsSpark", "kLogsFoot"),
                       ("kBreachesSpark", "kBreachesFoot")):
        assert 'id="%s"' % host in mk, "%s has no host in the markup" % host
        assert 'id="%s"' % foot in mk, "%s has no delta slot in the markup" % foot
        assert "'%s'" % foot in js, "%s is markup nothing ever writes" % foot


def test_the_org_count_keeps_the_foot_its_own_tooltip_points_at() -> None:
    """#kOrgsFoot carries live data -- `12 active · 3 suspended`, plus the
    approvals link M4c appends to it -- and the card's tooltip tells the reader
    to look there: "The line below splits the same count into active and
    suspended." Writing the trend into it would delete a fact and turn the
    tooltip into a lie in one stroke, which is why kOrgs is the only one of
    these cards with a second line.
    """
    js = " ".join(_script_blocks())
    # Block comments stripped: this function's own comment EXPLAINS that it must
    # not write #kOrgsFoot, and names it to do so. A guard that greps the raw
    # body finds its own explanation and calls it the defect -- the trap
    # _nocomment and _js_nocomment were written for, one language over.
    body = re.sub(r"/\*.*?\*/", "", _js_func("loadKpiSparks"), flags=re.S)
    assert "'kOrgsTrend'" in body or "kOrgsTrend" in body, (
        "the org trend is not routed to its own slot")
    assert "kOrgsFoot" not in body, (
        "loadKpiSparks writes #kOrgsFoot, which already carries the active / "
        "suspended split the card's own tooltip points at")
    assert "active · '" in js, "the active/suspended split stopped being written"


def test_a_failed_reload_takes_the_trend_down_with_the_value() -> None:
    """Every one of these cards keeps its last good delta in the DOM. A reload
    that fails blanks the value to "—" and would leave "30d ▲12%" and a drawn
    sparkline sitting under it: yesterday's trend presented as today's. That is
    A4's shape, on four more cards.

    Asserted at the CALL SITES -- guarding _kpiTrendOff's body would say nothing
    about whether any fault path reaches it (A6).
    """
    for fn, pairs in (("loadHealth", ("hFailedFoot", "hAnchorsFoot")),
                      ("loadRevenue", ("rvRev30Foot",)),
                      ("loadOverview", ("kOrgsTrend", "kLogsFoot", "kBreachesFoot"))):
        body = _js_func(fn)
        for foot in pairs:
            assert re.search(r"_kpiTrendOff\('%s'" % foot, body), (
                "%s's fault path leaves %s showing the previous poll's trend"
                % (fn, foot))


# ── C2 · trends on the four traffic KPI cards ────────────────────────────────
# tErr / tMkt / tApp / tAdm, fed by ONE new aggregate rather than four calls.
# All four had NO .kfoot before this phase -- klabel + kval only -- so unlike C1
# there was nothing to displace and the delta slot is simply new markup.
#
# These run the loader. The one thing that cannot be checked by reading the file
# is the series-to-card mapping: `errors` and `marketing` are both arrays of
# numbers, and a swapped pair draws a perfectly plausible line on the wrong card.

_TRAFFIC_SHIM = """
var EL={};
function mk(id){ return EL[id]={id:id,textContent:'',innerHTML:'',style:{},
  classList:{add:function(){},remove:function(){}}}; }
['tErr','tMkt','tApp','tAdm'].forEach(function(c){mk(c+'Foot');mk(c+'Spark');});
function $(id){ return EL[id]||null; }
var DREW=[], ASKED=[];
function chart(id,o){ DREW.push({id:id, vals:(o.series[0].values)||[],
  aria:o.ariaLabel||''}); }
function _spark(){ return '#FF6A1A'; }
function num(n){ return String(n); }
"""

# Four series whose values cannot be confused for one another: whichever host
# receives [2,2] received `marketing`, and no other reading is possible.
_TRAFFIC_OK = """
var api=function(u){ ASKED.push(u); return Promise.resolve({ok:true,json:function(){
  return Promise.resolve({days:7, unit:'count', series:{
    errors   :{points:[{value:1},{value:1}], total:2, delta_pct: 11.0},
    marketing:{points:[{value:2},{value:2}], total:4, delta_pct: 22.0},
    app      :{points:[{value:3},{value:3}], total:6, delta_pct: null},
    admin    :{points:[{value:4},{value:4}], total:8, delta_pct:-44.0}}}); }}); };
"""


def _run_traffic(api_js: str) -> dict:
    """Run the shipped loadTrafficSparks over a DOM stub and report what it did."""
    import json
    import os
    import tempfile
    probe = (_TRAFFIC_SHIM + api_js
             + "\n".join(_js_decl(f) for f in
                         ("_kpiDelta", "_kpiTrendOff", "loadTrafficSparks"))
             + """
loadTrafficSparks().then(function(){
  var R={drew:DREW, asked:ASKED, feet:{}, styled:{}};
  ['tErr','tMkt','tApp','tAdm'].forEach(function(c){
    R.feet[c]=EL[c+'Foot'].textContent;
    R.styled[c]=Object.keys(EL[c+'Foot'].style).length;
    R['spark_'+c]=EL[c+'Spark'].innerHTML; });
  console.log(JSON.stringify(R));
});
""")
    fd, path = tempfile.mkstemp(suffix=".js")
    os.close(fd)
    try:
        Path(path).write_text(probe, encoding="utf-8")
        # encoding="utf-8": text=True alone decodes cp1252 here and the ▲ / ▼
        # that carry direction come back as mojibake, failing correct output.
        proc = subprocess.run([shutil.which("node"), path],
                              capture_output=True, text=True, encoding="utf-8")
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout.strip().splitlines()[-1])
    finally:
        os.unlink(path)


pytestmark_c2 = pytest.mark.skipif(shutil.which("node") is None,
                                   reason="node not on PATH")


@pytestmark_c2
def test_each_traffic_card_is_fed_by_its_own_series() -> None:
    """THE ONE NOTHING ELSE CAN CATCH. The series names and the card ids are
    paired in exactly one place -- loadTrafficSparks' cfg table -- and both
    sides are arrays of counts, so swapping `marketing` and `admin` draws a
    plausible line on the wrong card and moves a plausible percentage under it.
    Nothing about the rendered page would look wrong.

    The probe returns four series whose values cannot be mistaken for one
    another, so the host that received [2, 2] received marketing and there is no
    other reading.
    """
    r = _run_traffic(_TRAFFIC_OK)
    drew = {d["id"]: d for d in r["drew"]}
    for host, value, series in (("tErrSpark", 1, "errors"),
                                ("tMktSpark", 2, "marketing"),
                                ("tAppSpark", 3, "app"),
                                ("tAdmSpark", 4, "admin")):
        assert host in drew, "%s was never drawn" % host
        assert drew[host]["vals"] == [value, value], (
            "%s drew %s; the %s series is %s -- the cfg table is cross-wired"
            % (host, drew[host]["vals"], series, [value, value]))
        assert series in drew[host]["aria"], (
            "%s announces itself as %r" % (host, drew[host]["aria"]))
    # and the deltas landed on the matching feet, not just the sparks
    assert "11%" in r["feet"]["tErr"] and "22%" in r["feet"]["tMkt"], r["feet"]
    assert "44%" in r["feet"]["tAdm"], r["feet"]


@pytestmark_c2
def test_the_traffic_feet_carry_no_total_and_no_colour() -> None:
    """TWO RULES IN ONE PLACE because both are about what reaches the foot.

    NO TOTAL: the face's number comes from /v1/traffic, which counts a ROLLING
    `now() - interval '7 days'`. The trend endpoint buckets by UTC calendar day,
    so today is partial and the two totals differ by a few hours of traffic.
    "· 1,240 in 7d" under a card reading 1,192 is two answers to one question,
    so the foot says the percentage and nothing else.

    NO COLOUR: an inline colour beats `.kpi .face .kfoot{color:var(--foxink)}`
    and measured 1.12:1 on .k-orange -- P4's defect. Asserted on `style` having
    no keys at all, which also catches setProperty and cssText.
    """
    r = _run_traffic(_TRAFFIC_OK)
    for card, foot in r["feet"].items():
        assert r["styled"][card] == 0, "%s's foot was given an inline style" % card
        if "no prior data" in foot:
            continue
        assert re.fullmatch(r"7d [▲▼]\d+%", foot), (
            "%s's foot is %r; it must be the window, the glyph and the "
            "percentage -- no total, because it would not match the face" % (card, foot))


@pytestmark_c2
def test_a_traffic_card_with_no_prior_window_says_so() -> None:
    """delta_pct is null when the prior 7 days were empty -- a new site, or the
    first week after deploy. That is not 0%, and a traffic card reading "7d ▲0%"
    on a site that just started serving would be a flat line over a launch."""
    r = _run_traffic(_TRAFFIC_OK)
    assert r["feet"]["tApp"] == "no prior data", (
        "a null delta rendered as %r" % r["feet"]["tApp"])
    assert "%" not in r["feet"]["tApp"], "a null delta rendered as a rate"


@pytestmark_c2
def test_the_traffic_trend_asks_for_the_window_its_cards_promise() -> None:
    """Every one of these four cards is labelled "· 7d". If the request asked
    for a different window the label would be a lie about a real number, which
    is the defect class this console has produced most often.
    """
    r = _run_traffic(_TRAFFIC_OK)
    assert len(r["asked"]) == 1, (
        "four cards issued %d requests; they share one aggregate" % len(r["asked"]))
    url = r["asked"][0]
    assert "days=7" in url, "the trend asks for %r while the cards say 7d" % url
    assert "/admin/v1/stats/traffic-timeseries" in url
    mk = _nocomment(SRC)
    for card in ("Errors ≥400 · 7d", "Marketing · 7d",
                 "Customer app · 7d", "Admin · 7d"):
        assert card in mk, "the label %r changed; check the window with it" % card


@pytestmark_c2
def test_a_failed_trend_call_takes_all_four_cards_down() -> None:
    """The trend is a SECOND request: /v1/traffic can succeed and paint the four
    numbers while this one 500s. Leaving the previous poll's sparklines under
    fresh numbers is A4's shape, and here it needs no reload at all to happen --
    one call simply outlives the other."""
    r = _run_traffic(
        "var api=function(u){ ASKED.push(u); "
        "return Promise.resolve({ok:false,status:500}); };")
    for card, foot in r["feet"].items():
        assert foot == "trend unavailable", "%s kept %r after a failed call" % (card, foot)
        assert r["spark_" + card] == "", "%s kept its previous sparkline" % card


def test_the_traffic_cards_gained_a_foot_they_did_not_have() -> None:
    """All four were klabel + kval only -- no .kfoot anywhere on the traffic
    page -- so unlike C1's cards there was nothing to displace and no tooltip
    pointing at a line that had to survive. Recorded because the next phase
    reading register #116's two-grammar split should know these four are neither
    grammar: the delta slot IS the foot, because the foot is new.
    """
    mk = _nocomment(SRC)
    for card in ("tErr", "tMkt", "tApp", "tAdm"):
        assert 'id="%sFoot"' % card in mk, "%s has no delta slot" % card
        assert 'id="%sSpark"' % card in mk, "%s has no spark host" % card
    js = " ".join(_script_blocks())
    for card in ("tErr", "tMkt", "tApp", "tAdm"):
        assert "'%sFoot'" % card in js or '"%sFoot"' % card in js or \
            "c[1]+'Foot'" in js, "%s's foot is markup nothing writes" % card


# ── C3 · the quota meter ─────────────────────────────────────────────────────
# The only new component in this plan. It encodes a THRESHOLD, not a proportion:
# R2's ladder (recede / exception / breach) rather than a red-to-green ramp,
# because the operator's question is "who is near their limit", not "how full is
# each one" -- and a ramp makes every row a slightly different colour and none
# of them findable.
#
# Almost all of these RUN quotaMeter and read the markup it returns. The three
# tiers differ by one comparison each, and a guard that greps the file for the
# string "warn" would pass with every threshold rewired.

_QM_SHIM = """
function num(n){return Number(n).toLocaleString('en-US');}
function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;')
  .replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
"""

_QM_ROLLED = "'2026-08-08T00:00:00Z'"


def _meter(cases: dict) -> dict:
    """Run the shipped quotaMeter over each case; report what it emitted."""
    import json
    import os
    import tempfile
    probe = (_QM_SHIM + _js_decl("quotaMeter")
             + "\nvar CASES=" + json.dumps(cases) + ";\nvar R={};\n"
             + r"""
for(var k in CASES){ var h=quotaMeter(CASES[k]);
  R[k]={html:h,
        tier:(/class="qmeter ([a-z]*)"/.exec(h)||[,''])[1],
        width:(/width:([\d.]+)%/.exec(h)||[,null])[1],
        hasFill:/<i /.test(h),
        hasTrack:/qmeter-t/.test(h),
        word:(/qmeter-w">([a-z]+)</.exec(h)||[,''])[1],
        text:h.replace(/<[^>]*>/g,' ').replace(/\s+/g,' ').trim()}; }
console.log(JSON.stringify(R));
""")
    fd, path = tempfile.mkstemp(suffix=".js")
    os.close(fd)
    try:
        Path(path).write_text(probe, encoding="utf-8")
        # encoding="utf-8" is not optional: text=True alone decodes cp1252 here
        # and the · separator in the unmetered state comes back as mojibake.
        proc = subprocess.run([shutil.which("node"), path],
                              capture_output=True, text=True, encoding="utf-8")
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout.strip().splitlines()[-1])
    finally:
        os.unlink(path)


def _case(used, quota):
    return {"usage_this_month": used, "monthly_log_quota": quota}


pytestmark_c3 = pytest.mark.skipif(shutil.which("node") is None,
                                   reason="node not on PATH")


@pytestmark_c3
def test_the_meter_changes_tier_at_eighty_and_at_a_hundred() -> None:
    """THE THRESHOLDS ARE THE COMPONENT. Each tier is one comparison, and each
    boundary is asserted from BOTH sides -- a guard that only checked 50% and
    150% would pass with the thresholds at 40 and 90.

    19,999/25,000 is 79.996%, which ROUNDS to 80 for the bar's width. It must
    still be the quiet tier: the tier reads the true percentage, not the
    rounded one it draws with.
    """
    r = _meter({
        "half":    _case(12_500, 25_000),   # 50%
        "just_under": _case(19_999, 25_000),  # 79.996% -> rounds to 80
        "at_80":   _case(20_000, 25_000),   # 80%
        "at_99":   _case(24_999, 25_000),
        "at_100":  _case(25_000, 25_000),
        "over":    _case(27_900, 25_000),
    })
    assert r["half"]["tier"] == "", "a half-full quota is being flagged"
    assert r["just_under"]["tier"] == "", (
        "79.996%% took the exception tier: the tier is reading the rounded "
        "width (%s) instead of the true percentage" % r["just_under"]["width"])
    assert r["at_80"]["tier"] == "warn", "80%% is not the exception tier"
    assert r["at_99"]["tier"] == "warn", "99%% is not the exception tier"
    assert r["at_100"]["tier"] == "bad", "reaching the cap is not a breach"
    assert r["over"]["tier"] == "bad"
    # and the fill never runs past its own track
    assert float(r["over"]["width"]) == 100.0, (
        "an over-quota bar draws %s%% wide" % r["over"]["width"])


@pytestmark_c3
def test_no_tier_is_carried_by_colour_alone() -> None:
    """R2's rule, and the reason this component is not a ramp: hue says WHICH
    state, weight says WHETHER it is the one you expected, and a WORD says it in
    text. A colourblind operator, a greyscale screenshot in a ticket, and a
    forced-colors user all get the same answer.
    """
    r = _meter({"quiet": _case(1_000, 25_000),
                "near": _case(21_000, 25_000),
                "over": _case(30_000, 25_000)})
    assert r["quiet"]["word"] == "", "a normal row is being labelled"
    assert r["near"]["word"] == "near", "the exception tier has no word"
    assert r["over"]["word"] == "over", "the breach tier has no word"
    for key in ("near", "over"):
        assert r[key]["word"] in r[key]["text"], (
            "%s's word is not in the rendered text" % key)


@pytestmark_c3
def test_zero_usage_draws_an_empty_track_and_one_event_does_not() -> None:
    """A5 PAID FOR THIS ALREADY, one component over. .funbar i carries
    min-width:3px, so a bucket of zero rendered a short coloured stub that reads
    as "a few" while the numeral beside it said 0 -- the bar disagreeing with
    its own number.

    The other half is C0's thin-segment rule pointed the other way: 1 event in a
    25,000 allowance is 0.004%, which rounds to a 0.0% width, so the fill must
    still EXIST and let the CSS floor make it visible. Draw nothing there and
    the meter says "unused" about a live customer.
    """
    r = _meter({"none": _case(0, 25_000), "one": _case(1, 25_000)})
    assert not r["none"]["hasFill"], (
        "zero usage drew a fill: %s" % r["none"]["html"])
    assert r["none"]["hasTrack"], "zero usage drew no track either"
    assert "0 / 25,000" in r["none"]["text"], r["none"]["text"]
    assert r["one"]["hasFill"], (
        "one event drew no fill at all, so an active workspace reads as unused")
    assert float(r["one"]["width"]) == 0.0, (
        "the test's premise is gone: 1/25,000 no longer rounds to a 0%% width, "
        "so this is no longer exercising the floor")


def test_the_fill_floor_is_in_the_stylesheet() -> None:
    """The other half of the guard above: quotaMeter emits width:0.0% for one
    event and relies on the CSS floor to make it visible. Asserted here because
    the two halves live in different languages and either can be removed alone.
    """
    css = _css()
    m = re.search(r"\.qmeter-t i\{([^}]*)\}", css)
    assert m, "the meter's fill rule is gone"
    assert "min-width:3px" in m.group(1), (
        "the fill lost its floor, so a 0.004%% usage draws nothing")


@pytestmark_c3
def test_an_unmetered_workspace_gets_no_bar_at_all() -> None:
    """premium and guardian are unlimited by contract, and billing_state nulls
    the cap while an evaluation offer is live. There is no denominator, so there
    is no bar -- a full one would be a lie about a plan that cannot fill, and an
    empty one would be a lie about a workspace sending 98,000 events.

    Not even a track: an empty track is the "no measurement yet" state below,
    and drawing one here would say the two are the same thing.
    """
    r = _meter({"un": _case(98_000, None)})["un"]
    assert not r["hasFill"] and not r["hasTrack"], (
        "an unmetered workspace drew a meter: %s" % r["html"])
    assert "unmetered" in r["text"], r["text"]
    assert "98,000" in r["text"], "the usage stopped being reported"
    assert "%" not in r["text"], "a percentage of nothing was rendered"



# test_usage_with_no_rollup_behind_it_is_an_absence_not_a_zero lived here.
# C3.1 DELETED THE STATE IT GUARDED, on purpose. The meter counted usage_daily
# and needed usage_rolled_up_at to say "this zero is not a measurement yet"; it
# now counts audit_logs, where a count is always a real count. There is no
# moment when the number is not an answer, so there is nothing left to assert.
# Removed rather than weakened -- a guard kept alive against a branch that no
# longer exists is the A6 shape, asserting a definition nothing reaches.

@pytestmark_c3
@pytestmark_c3
def test_the_meter_has_no_awaiting_rollup_state_left() -> None:
    """The removal, asserted rather than assumed. C3's branch keyed on
    usage_rolled_up_at, a field the endpoint no longer sends -- so if the branch
    survived, `!undefined` is TRUE and EVERY row would render "awaiting rollup"
    instead of its number. A deletion whose leftover fails open like that is
    worth a guard even though it is a deletion."""
    body = re.sub(r"/\*.*?\*/", "", _js_func("quotaMeter"), flags=re.S)
    assert "usage_rolled_up_at" not in body, (
        "quotaMeter still reads a field the API stopped sending; every row "
        "would take that branch")
    assert "awaiting rollup" not in body
    r = _meter({"zero": _case(0, 500)})["zero"]
    assert "0 / 500" in r["text"], (
        "a zero no longer renders as a measured zero: %r" % r["text"])


@pytestmark_c3
def test_a_hand_set_zero_allowance_does_not_divide_by_zero() -> None:
    """PlanRequest validates monthly_log_quota >= 0, so staff can set 0 by hand.
    Every other path stores settings.quota_for(plan), which returns None rather
    than 0 -- so this is only reachable through the console itself, which is
    exactly why it is worth a guard rather than an assumption."""
    r = _meter({"zero_cap": _case(5, 0), "zero_both": _case(0, 0)})
    assert r["zero_cap"]["tier"] == "bad", "usage against no allowance is not over"
    assert r["zero_both"]["tier"] == "", "0 of 0 is not a breach"
    for k in r:
        assert "NaN" not in r[k]["html"] and "Infinity" not in r[k]["html"], (
            "%s produced %s" % (k, r[k]["text"]))


def test_the_meter_is_built_from_the_measured_tokens() -> None:
    """The track is --line because it is the ONLY candidate clearing 3:1 for all
    three fills in BOTH themes while leaving an empty track visible against the
    panel: --surf2 and --surf3 vanish on the panel (1.03 / 1.11), --line2 puts
    breach at 2.89 in light. Pinned so the next person who reaches for "a
    slightly lighter grey" has to re-measure rather than re-taste.

    The fills are R2's own tokens, which is what makes this the same ladder as
    the status mark rather than a second vocabulary for the same idea.
    """
    css = _css()
    track = re.search(r"\.qmeter-t\{([^}]*)\}", css)
    assert track, "the meter's track rule is gone"
    assert "background:var(--line)" in track.group(1), (
        "the track is no longer --line: %s" % track.group(1))
    assert "background:var(--muted)" in re.search(r"\.qmeter-t i\{([^}]*)\}", css).group(1)
    for sel, token in ((r"\.qmeter\.warn \.qmeter-t i", "--warn-bg"),
                       (r"\.qmeter\.bad \.qmeter-t i", "--breach-bg")):
        m = re.search(sel + r"\{([^}]*)\}", css)
        assert m, "the meter lost its %s rule" % token
        assert "background:var(%s)" % token in m.group(1), (
            "the %s tier is painted %s" % (token, m.group(1)))
    # a ramp would need a gradient; there is not one here, in either direction
    for rule in (track.group(1), css[css.index(".qmeter-t i{"):css.index(".qmeter-t i{") + 400]):
        assert "gradient" not in rule, (
            "the meter grew a gradient; it encodes a threshold, not a proportion")


def test_every_empty_state_on_the_org_table_spans_its_new_width() -> None:
    """The table went from six columns to seven, and a stale colspan is
    invisible until the table is actually empty -- which for three of these five
    is the state a staff member hits on their first visit.

    FIVE sites, not three: the loading row that ships in the markup and the
    faultRow() call are both easy to miss because neither looks like an empty
    state in the source.
    """
    mk = _nocomment(SRC)
    tbody = mk[mk.index('<tbody id="orgRows"'):]
    tbody = tbody[:tbody.index("</tbody>")]
    assert 'colspan="7"' in tbody, "the shipped loading row still spans six"
    body = _js_func("renderOrgs")
    assert body.count('colspan="7"') == 3, (
        "renderOrgs has %d seven-column empty states, expected 3"
        % body.count('colspan="7"'))
    assert 'colspan="6"' not in body, "an empty state still spans the old width"
    assert "faultRow(7," in _js_func("loadOrgs"), (
        "the fault row still spans six of seven columns")


# ── C4 · the active-filter pill row ──────────────────────────────────────────
# A7 gave orgs and staff a sentence saying HOW MANY rows their filters left.
# These pills say WHICH filters did it and remove one in a single action. The
# sentence keeps the count; the pills carry the cause.
#
# Most of these RUN the renderer. The states differ by one comparison each
# (`pills.length > 1` decides "clear all"), and a grep for the string "clear
# all" would pass with that threshold rewired to zero.

_PILL_SHIM = """
var EL={}; function mk(id){return EL[id]={id:id,innerHTML:''};}
mk('orgPills'); mk('staffPills');
function $(id){return EL[id]||null;}
function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;')
  .replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
var _FPILL_X='<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M18 6 6 18M6 6l12 12"/></svg>';
"""


def _pills(cases: dict) -> dict:
    """Run the shipped renderFilterPills over each case; report what it built."""
    import json
    import os
    import tempfile
    probe = (_PILL_SHIM + _js_decl("renderFilterPills")
             + "\nvar CASES=" + json.dumps(cases) + ", R={};\n"
             + r"""
for(var k in CASES){
  renderFilterPills('orgPills', CASES[k], 'demoClear()');
  var h=EL.orgPills.innerHTML;
  R[k]={html:h,
        pills:(h.match(/class="tag fpill"/g)||[]).length,
        clear:/class="fpill-clear"/.test(h),
        buttons:(h.match(/<button/g)||[]).length,
        anchors:(h.match(/<a[ >]/g)||[]).length,
        labels:(h.match(/aria-label="[^"]*"/g)||[]),
        statusClass:/fpill[^"]*\b(safe|warn|bad|dim|info)\b/.test(h)};
}
console.log(JSON.stringify(R));
""")
    fd, path = tempfile.mkstemp(suffix=".js")
    os.close(fd)
    try:
        Path(path).write_text(probe, encoding="utf-8")
        # encoding="utf-8": text=True alone decodes cp1252 here, and the curly
        # quotes the scope line uses come back as mojibake.
        proc = subprocess.run([shutil.which("node"), path],
                              capture_output=True, text=True, encoding="utf-8")
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout.strip().splitlines()[-1])
    finally:
        os.unlink(path)


_ONE = [{"key": "search", "val": "acme", "off": "orgPillClear('q')"}]
_TWO = _ONE + [{"key": "status", "val": "awaiting approval",
                "off": "orgPillClear('pending')"}]

pytestmark_c4 = pytest.mark.skipif(shutil.which("node") is None,
                                   reason="node not on PATH")


@pytestmark_c4
def test_no_filters_draws_no_row_at_all() -> None:
    """An empty pill row would be a permanent strip of nothing above every
    table, which is how "at a glance" turns into furniture. Both the empty list
    and a list that is only nulls -- the callers build theirs with inline
    conditionals, so an all-off page hands over [null, null]."""
    r = _pills({"empty": [], "nulls": [None, None]})
    for k in ("empty", "nulls"):
        assert r[k]["html"] == "", "%s drew %r" % (k, r[k]["html"])


@pytestmark_c4
def test_clear_all_appears_only_once_it_saves_an_action() -> None:
    """At one pill "clear all" is a second way to press the button beside it.
    Asserted from BOTH sides: a threshold rewired to zero would satisfy "two
    pills have it" and put a redundant control on every single-filter page."""
    r = _pills({"one": _ONE, "two": _TWO})
    assert r["one"]["pills"] == 1 and not r["one"]["clear"], (
        "a single filter was given a clear-all: %s" % r["one"]["html"])
    assert r["two"]["pills"] == 2 and r["two"]["clear"], (
        "two filters got no clear-all: %s" % r["two"]["html"])
    # 2 removals + 1 clear-all
    assert r["two"]["buttons"] == 3, r["two"]["buttons"]


@pytestmark_c4
def test_every_control_in_the_row_is_a_real_button() -> None:
    """A5 found three <a href="#"> wearing a control's clothes on this surface.
    A link that does not navigate is a keyboard and screen-reader lie, and
    "clear all" is the obvious place to add a fourth."""
    r = _pills({"two": _TWO})["two"]
    assert r["anchors"] == 0, "the pill row grew an anchor: %s" % r["html"]
    assert r["buttons"] == 3
    assert 'type="button"' in r["html"], (
        "a button in a row that may sit inside a form defaults to submit")


@pytestmark_c4
def test_the_remove_control_names_what_it_removes() -> None:
    """The x is icon-only, so its accessible name is the only thing a screen
    reader has. "Remove" alone would be three identical buttons in a row; the
    name has to carry the field AND the value."""
    labels = _pills({"two": _TWO})["two"]["labels"]
    assert len(labels) == 2, labels
    assert any("search" in l and "acme" in l for l in labels), labels
    assert any("status" in l and "awaiting approval" in l for l in labels), labels
    for l in labels:
        assert l.lower().startswith('aria-label="remove'), (
            "the name does not say what pressing it does: %s" % l)


@pytestmark_c4
def test_an_operators_own_typing_cannot_become_markup() -> None:
    """The value is whatever was typed into a search box, and it lands in TWO
    places -- the visible text and the aria-label. Escaping one and not the
    other is the easy half-fix."""
    r = _pills({"x": [{"key": "search", "val": '<img src=x onerror=alert(1)>',
                       "off": "orgPillClear('q')"}]})["x"]
    assert "<img" not in r["html"], "a search term reached the DOM as markup"
    assert "&lt;img" in r["html"]
    assert r["html"].count("&lt;img") == 2, (
        "the value is escaped in one place but not the other: %s" % r["html"])


@pytestmark_c4
def test_removing_a_pill_goes_through_the_page_that_owns_the_filter() -> None:
    """THE COUNTING TRAP, and why this is guarded on the CALL and not the body.

    A7's handlers each pgReset before they render. Narrow to two rows while
    sitting on page three and the table is empty under a pager insisting there
    are results -- so a removal path that set state directly would rebuild that
    defect, and only on a filter cleared from page two or later.

    Run, not grepped: orgPillClear is asserted to CALL both handlers, which is
    what carries the reset. Guarding that pgReset appears somewhere in the file
    would say nothing about whether this path reaches it (A6).
    """
    import json
    import os
    import tempfile
    probe = ("""
var CALLED=[], V={};
function $(id){ return V[id] || (V[id]={value:'x',checked:true}); }
function orgSearchChanged(){ CALLED.push('search'); }
function orgPendingChanged(){ CALLED.push('pending'); }
"""
             + _js_decl("orgPillClear")
             + """
var R={};
orgPillClear('q');   R.q={called:CALLED.slice(), search:V.orgSearch.value};
CALLED.length=0;
orgPillClear('pending'); R.pending={called:CALLED.slice(), checked:V.orgPendingOnly.checked};
CALLED.length=0; V.orgSearch.value='y'; V.orgPendingOnly.checked=true;
orgPillClear();      R.all={called:CALLED.slice(), search:V.orgSearch.value,
                            checked:V.orgPendingOnly.checked};
console.log(JSON.stringify(R));
""")
    fd, path = tempfile.mkstemp(suffix=".js")
    os.close(fd)
    try:
        Path(path).write_text(probe, encoding="utf-8")
        proc = subprocess.run([shutil.which("node"), path],
                              capture_output=True, text=True, encoding="utf-8")
        assert proc.returncode == 0, proc.stderr
        r = json.loads(proc.stdout.strip().splitlines()[-1])
    finally:
        os.unlink(path)

    for case in ("q", "pending", "all"):
        assert "search" in r[case]["called"] and "pending" in r[case]["called"], (
            "%s did not route through the handlers that pgReset: %s"
            % (case, r[case]["called"]))
    assert r["q"]["search"] == "", "removing the search pill left the box filled"
    assert r["pending"]["checked"] is False, "removing the status pill left it ticked"
    assert r["all"]["search"] == "" and r["all"]["checked"] is False, (
        "clear all left a control set: %s" % r["all"])


def test_the_pill_is_a_category_and_not_a_status() -> None:
    """R2's ladder answers "is this the state you expected" -- safe/warn/bad are
    margin marks for rows a human has to look at. "search: acme" is not a state
    at all, it is a category, and `.tag` is what this file already has for
    those. The detail-pair renderer reuses `.tag` for the same reason, so this
    is the second caller rather than a second vocabulary.
    """
    js = " ".join(_script_blocks())
    assert 'class="tag fpill"' in js, (
        "the pill stopped being built on .tag, so it is a lookalike of the "
        "category vocabulary instead of the thing itself")
    body = _js_func("renderFilterPills")
    for status in ("chip", "safe", "warn", " bad", "dim"):
        assert 'class="%s' % status not in body, (
            "the pill row is emitting a status class (%s); a filter is a "
            "category" % status)


def test_the_refetching_filter_is_deliberately_not_a_pill() -> None:
    """M4c split the org controls by what they DO: `showDeleted` re-requests the
    list with include_deleted, while the search and the approvals toggle narrow
    what has already arrived -- and it says so in the markup, "keeps 'this
    refetches' and 'this filters' from looking alike".

    A pill costing a round trip beside two that cost nothing is that same
    conflation one layer up, so showDeleted is absent BY DECISION. Its state is
    still named in the scope sentence and its checkbox is in the pagehead.
    Guarded because "add the missing one" is the obvious next edit.
    """
    body = re.sub(r"/\*.*?\*/", "", _js_func("renderOrgs"), flags=re.S)
    # sliced to the CALL, not the whole function: renderOrgs mentions
    # showDeleted legitimately elsewhere (it reads the checkbox for the scope
    # sentence), so a body-wide grep would fail on correct code.
    call = body[body.index("renderFilterPills('orgPills'"):]
    call = call[: call.index("');") + 3]
    assert "showDeleted" not in call, (
        "showDeleted was added to the pill row; it refetches, and M4c grouped "
        "these controls by that difference on purpose")
    # and the sentence still carries it, which is what makes the omission safe
    assert "offboarded included" in body, (
        "the scope line stopped naming the offboarded toggle, so nothing does")


def test_both_pill_rows_are_rendered_by_the_one_renderer() -> None:
    """One grammar. Register #116/#119 already record three KPI-footer grammars
    on this console; a second pill implementation would be the same mistake in
    a new place. Anchored to the ids, not to position."""
    mk = _nocomment(SRC)
    for host in ("orgPills", "staffPills"):
        assert 'id="%s"' % host in mk, "%s has no host in the markup" % host
    for fn, host in (("renderOrgs", "orgPills"), ("renderStaff", "staffPills")):
        body = _js_func(fn)
        assert "renderFilterPills('%s'" % host in body, (
            "%s does not render its pill row through the shared renderer" % fn)


def test_the_remove_target_meets_the_touch_minimum_where_touch_happens() -> None:
    """44x44 is a TOUCH rule. The pointer size is 20px, which is right for a
    9.5px pill on a desktop ops console; the minimum is applied at the same
    breakpoint the tables restack at, and measured there it is 44x44."""
    css = _css()
    mq = css[css.index("@media(max-width:760px){"):]
    mq = mq[: mq.index("\n}")]
    assert ".fpill-x{width:44px;height:44px}" in mq.replace(" ", ""), (
        "the touch target is not raised at the breakpoint where taps happen")
