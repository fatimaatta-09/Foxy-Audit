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


def test_no_h1_anywhere() -> None:
    """P2 removed the last one, and the now-dead rule went with it."""
    assert "<h1" not in SRC
    assert "h1{font-family" not in _style_block(), "the h1 rule outlived its last element"


def test_pagehead_controls_survive() -> None:
    """The heads that carried working controls still carry them."""
    joined = " ".join(_pagehead_blocks())
    for control in ('id="showDeleted"', 'onclick="orgVerify()"', 'onclick="auditExport()"',
                    'onclick="loadCampaigns()"'):
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
    css = _style_block()
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
    for row in _blocks('<div class="grid kpis"'):
        faces = re.findall('class="clay kpi (k-[a-z]+)"', row)
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
    assert len(ids) == 18, f"expected 18 glyphs, found {len(ids)}: {sorted(ids)}"
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


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
