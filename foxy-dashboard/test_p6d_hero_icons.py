"""P6d · the hero icons on the Overview KPI tiles.

**A missing icon has to fail a test, not just look wrong.** That is the whole
point of this file. The failure modes here are quiet ones: a data URI truncated
by an editor still parses as HTML and renders as a broken-image box or as
nothing at all; a `src` that drifted to an https URL works on the developer's
machine and dies behind the CSP; an icon deleted from one tile leaves a card that
still looks plausible at a glance. None of those raise anything.

So every icon is decoded here, not just pattern-matched — base64 that does not
round-trip, or bytes that are not actually a WebP, fail on this file rather than
in a screenshot nobody is looking at closely.
"""

from __future__ import annotations

import base64
import binascii
import re

import pytest

from test_p1_contrast import HTML  # the one source of the file path

# Left to right, and the order is part of the contract: these four labels are
# in the markup in this sequence and the icons have to match them.
TILES = ("Breaches stopped", "Open alerts", "Clean rate", "Time to verdict")


@pytest.fixture(scope="module")
def html() -> str:
    return HTML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def kpi_block(html) -> str:
    start = html.index('id="homeKpis"')
    return html[start:html.index("</div>\n\n", start)]


@pytest.fixture(scope="module")
def icons(kpi_block) -> list[str]:
    """Every heroicon src on the Overview row, in document order."""
    return re.findall(r'<img class="heroicon"[^>]*\ssrc="([^"]+)"', kpi_block)


# ══ the silent failures ════════════════════════════════════════════════════

def test_all_four_tiles_carry_an_icon(kpi_block, icons):
    """A tile that lost its icon still renders a perfectly reasonable card."""
    assert len(icons) == 4, (
        f"expected 4 hero icons on #homeKpis, found {len(icons)}"
    )
    labels = re.findall(r'<div class="l">([^<]+)</div>', kpi_block)
    assert labels == list(TILES), f"the KPI tiles or their order changed: {labels}"


def test_every_icon_is_a_self_contained_data_uri(icons):
    """No external fetch, ever. An https src passes review, works locally, and
    then fails behind the CSP — the exact class of bug this file's no-CDN rule
    exists to prevent."""
    for i, src in enumerate(icons):
        assert src.startswith("data:image/webp;base64,"), (
            f"icon {i} is not an embedded WebP data URI: {src[:60]!r}"
        )
        assert "http://" not in src and "https://" not in src, (
            f"icon {i} points at a remote URL"
        )


def test_every_icon_decodes_to_a_real_webp(icons):
    """Decoded, not pattern-matched. A truncated or re-wrapped data URI still
    looks like a data URI; it just does not paint."""
    for i, src in enumerate(icons):
        payload = src.split(",", 1)[1]
        try:
            blob = base64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError) as exc:
            pytest.fail(f"icon {i} is not valid base64: {exc}")
        assert len(blob) > 1024, (
            f"icon {i} decodes to only {len(blob)} bytes — truncated"
        )
        # RIFF container with a WEBP fourcc
        assert blob[:4] == b"RIFF" and blob[8:12] == b"WEBP", (
            f"icon {i} decodes to something that is not a WebP: {blob[:12]!r}"
        )


def test_the_icons_are_all_different(icons):
    """A copy-paste that pointed two tiles at the same picture would look almost
    right — two of these are round and two are upright."""
    assert len(set(icons)) == 4, "two hero tiles share the same icon"


def test_no_tile_fetches_anything_from_the_network(kpi_block):
    """Belt and braces on the whole row, not just the img src."""
    assert "http://" not in kpi_block and "https://" not in kpi_block, (
        "the KPI row references a remote resource"
    )


# ══ the rules the row has to keep ══════════════════════════════════════════

def test_the_icons_are_decorative_to_assistive_tech(kpi_block):
    """Every tile states its number and its label in text. An icon with alt text
    would make each card announce itself twice, and any alt we wrote would be an
    invention — nobody chose these pictures for their words."""
    for m in re.finditer(r'<img class="heroicon"[^>]*>', kpi_block):
        tag = m.group(0)
        assert 'alt=""' in tag, f"hero icon has non-empty alt text: {tag[:90]}"
        assert 'aria-hidden="true"' in tag, f"hero icon is not hidden: {tag[:90]}"


def test_the_row_is_back_on_the_neutral_surface(kpi_block):
    """The icons are multi-colour now — a gold bell on a blue card fights
    itself. Open alerts keeps .k-status, which is its own original class and
    carries the is-raised behaviour with it."""
    tones = re.findall(r'class="stat kpi (k-[a-z]+)"', kpi_block)
    assert tones == ["k-plain", "k-status", "k-plain", "k-plain"], (
        f"the Overview row is not on the neutral face: {tones}"
    )


def test_the_neutral_face_rules_cover_both_classes(html):
    """.k-plain shares .k-status's surface. If it stopped being listed on these
    declarations the base .stat.kpi .face would hand it --foxink, which is
    near-black on a near-black card."""
    for rule in (".stat.kpi.k-plain .face,\n.stat.kpi.k-status .face{background:var(--surf2);color:var(--ink);}",
                 ".stat.kpi.k-plain .v,\n.stat.kpi.k-status .v{color:var(--ink);}",
                 ".stat.kpi.k-plain .l,\n.stat.kpi.k-status .l{color:var(--muted);opacity:1;}"):
        assert rule in html, f"the neutral face lost a declaration:\n{rule}"


def test_the_status_tile_keeps_its_state_rules(html):
    """Open alerts is .k-status again, so the raised behaviour that shipped
    before P6d is back exactly as it was."""
    assert ".stat.kpi.k-status.is-raised .v{color:var(--breach-ink);}" in html
    assert ".stat.kpi.k-status.is-raised .kpi-trend{color:var(--breach-ink);}" in html


def test_the_hand_drawn_set_is_gone(html):
    """P6d went through two hand-drawn attempts. Their symbols, gradients and
    .hglyph class are dead now and must not linger as a second icon system."""
    for corpse in ("hglyph", "hi-sheen", "hi-shield-g1", '<symbol id="hi-'):
        assert corpse not in html, f"leftover from the hand-drawn attempt: {corpse}"


def test_the_duotone_set_is_untouched(html):
    """#dg-* is a different system with a different job, and eleven other cards
    still draw it."""
    symbols = set(re.findall(r'<symbol\s+id="(dg-[^"]+)"', html))
    for needed in ("dg-shield", "dg-threats", "dg-chart", "dg-verify", "dg-key",
                   "dg-access", "dg-quota", "dg-agent", "dg-billing", "dg-lock",
                   "dg-passport"):
        assert needed in symbols, f"#{needed} was removed; other pages still use it"
    assert 'class="dglyph"' in html, "the watermark class itself is gone"


def test_every_use_still_resolves_to_a_symbol(html):
    """Kept from the earlier guards: a broken <use> renders as NOTHING. It no
    longer covers the hero row, but it still covers the eleven cards that do use
    the sprite."""
    symbols = set(re.findall(r'<symbol\s+id="([^"]+)"', html))
    used = re.findall(r'<use\s+href="#([^"]+)"', html)
    missing = sorted({u for u in used if u not in symbols})
    assert not missing, f"<use href> points at symbols that do not exist: {missing}"


# ══ placement ══════════════════════════════════════════════════════════════

def test_the_icon_is_sized_to_be_seen(html):
    """Not the 28px corner badge and not the 98px .dglyph watermark. .face has a
    134px min-height; the source is 128px, so ~52px runs at 2.5x."""
    m = re.search(r"\.heroicon\{([^}]*)\}", html)
    assert m, ".heroicon is gone from the stylesheet"
    decl = m.group(1)
    size = re.search(r"width:(\d+)px", decl)
    assert size and 44 <= int(size.group(1)) <= 64, (
        f"hero icon is {size.group(1) if size else '?'}px — outside the 48-56 band"
    )
    assert "z-index:2" in decl, "the icon must sit in front of the face"
    assert "opacity" not in decl, "the icon is foreground art, not a watermark"


def test_the_mobile_rule_matches_the_watermark_breakpoint(html):
    """.dglyph shrinks at 620px. A different breakpoint here would mean the two
    systems disagree about what a small card is."""
    assert "@media(max-width:620px){ .heroicon{width:42px;height:42px;} }" in html
    assert "@media(max-width:620px){ .dglyph{" in html
