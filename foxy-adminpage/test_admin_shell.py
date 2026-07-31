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


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
