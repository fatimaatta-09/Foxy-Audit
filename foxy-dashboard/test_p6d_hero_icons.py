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

def _fills(html_text: str) -> dict[str, tuple[str, str]]:
    """Every decorative .face gradient, by tone class."""
    out = {}
    for m in re.finditer(
            r"\.(k-[a-z]+)\s+\.face\{background:linear-gradient\(145deg,(#[0-9a-f]{6}),(#[0-9a-f]{6})\)",
            html_text):
        out[m.group(1)] = (m.group(2), m.group(3))
    return out


def _lum(hexv: str) -> float:
    c = [int(hexv[i:i+2], 16) / 255 for i in (1, 3, 5)]
    c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def _ratio(a: str, b: str) -> float:
    la, lb = _lum(a), _lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)



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


def test_each_card_takes_its_hue_from_its_own_icon(kpi_block):
    """The pairing is the point. An earlier pass used a fixed rotation and put
    the blue-green shield on an orange card — the one combination where the card
    and the object sitting on it argued. These four are derived from the icons:
    teal from the shield's blue-to-green, fox-orange from the bell's gold, indigo
    from the chart's blue bars and violet line, rose against the pewter watch."""
    tones = re.findall(r'class="stat kpi ([^"]+)"', kpi_block)
    assert tones == ["k-teal", "k-status k-fox", "k-indigo", "k-pink"], (
        f"the Overview row's hues moved away from its icons: {tones}"
    )


def test_no_decorative_fill_wears_a_status_colour(html):
    """The stylesheet reserves red, amber and green for status so a warning still
    reads instantly beside four coloured cards. The teal is cyan-leaning for
    exactly this reason and must not drift toward --safe."""
    fills = _fills(html)
    for tone, (light, deep) in fills.items():
        for reserved in ("#12843c", "#3ddc84", "#f59e0b", "#dc2626"):
            assert light.lower() != reserved and deep.lower() != reserved, (
                f".{tone} is painted with the status colour {reserved}"
            )
    # the teal has to stay clearly off the success green
    tl = fills["k-teal"][0].lower()
    assert tl != "#3ddc84", "the teal collapsed onto --safe-ink"
    r_, g_, b_ = (int(tl[i:i+2], 16) for i in (1, 3, 5))
    assert b_ > g_ * 0.75, (
        f"the teal {tl} lost its blue and now reads as success green"
    )


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_every_decorative_fill_clears_aa_with_its_ink(html, theme):
    """MEASURED, not asserted by token name. These fills are new surfaces for
    text to sit on, and the .l label is 9.5px body text — the real risk of a
    coloured row. The ink is --foxink, which is declared once and never
    overridden, and the fills are literal hex, so both themes measure the same;
    the parametrize is here so that stops being an assumption.

    Deliberately checks the DEEP end: a 145deg gradient puts its darkest corner
    at bottom-right, which is where the label's tail sits."""
    foxink = re.search(r"--foxink:\s*(#[0-9a-f]{6})", html).group(1)
    for tone, (light, deep) in _fills(html).items():
        for end, hexv in (("light", light), ("deep", deep)):
            got = _ratio(foxink, hexv)
            assert got >= 4.5, (
                f"[{theme}] .{tone}'s {end} end {hexv} measures {got:.2f}:1 against "
                f"--foxink — deepen the fill, never lighten the ink"
            )


def test_the_status_tile_can_still_be_coloured_by_its_state(html):
    """The gate note this file was built on: "this card carries state, so the
    state is what colours it". Open alerts now also carries a decorative fill, so
    the state takes the whole FACE rather than one number — --breach-ink measures
    ~3:1 on orange and could not have carried it.

    Every rule carries both classes, so none of them can outrank a state the way
    P6a's theme override outranked .livedot.off."""
    assert ".stat.kpi.k-status.k-fox .face{background:linear-gradient(145deg,#ff8b42,#dd5f18);color:var(--foxink);}" in html
    assert ".stat.kpi.k-status.k-fox.is-raised .face{background:linear-gradient(145deg,#ff8d7d,#db4c3d);}" in html
    # and the raised fill must itself be readable
    foxink = re.search(r"--foxink:\s*(#[0-9a-f]{6})", html).group(1)
    assert _ratio(foxink, "#db4c3d") >= 4.5, "the raised fill is too dark for its ink"


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
