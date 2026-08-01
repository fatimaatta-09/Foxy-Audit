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

def _kpi_cards() -> list[str]:
    return re.findall(r'<div class="clay kpi k-[a-z]+">.*?</span></div>', SRC, re.S)


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


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
