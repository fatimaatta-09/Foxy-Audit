"""P2 §2 · the responsive contract, guarded at the point it actually broke.

The §2 sweep (375 / 768 / 1024 / 1440 × every page) found exactly one real
defect: the billing page scrolled the BODY 66px at 375px. The cause was not the
wide table everyone would suspect — that table already sat in a card with
`overflow-x:auto`. It was the grid around the card. A grid item's implicit
`min-width` is `auto`, so it refuses to narrow past its content's min-content
width; the table's `min-width:380px` propagated straight out through the item
and past the viewport, and the card's overflow never got anything to scroll.

So there are two guards, not one. The first pins the fix. The second pins the
*shape* — a wide table must live in a card that can scroll — because that is the
invariant a future page can break again in a new place, and the sweep that found
it was manual.
"""

from __future__ import annotations

import re

import pytest

from test_p1_contrast import HTML


@pytest.fixture(scope="module")
def html() -> str:
    return HTML.read_text(encoding="utf-8")


def test_grid_items_can_shrink_below_their_content(html):
    """The fix. Revert this line and billing overflows the body at 375px again."""
    assert ".g2>*,.g3>*{min-width:0;}" in html, (
        "grid items must be allowed to shrink — without min-width:0 a wide child "
        "pushes the whole grid past the viewport and the body scrolls sideways")


def test_every_wide_table_sits_in_a_card_that_can_scroll(html):
    """§2: wide content scrolls INSIDE its own container. A table with a pinned
    min-width and no scrollable ancestor is a horizontal body scroll waiting to
    happen — which is precisely what billing was."""
    wide = [m for m in re.finditer(r"<table[^>]*min-width:(\d+)px[^>]*>", html)]
    assert wide, "no min-width tables found — did the markup change shape?"
    for m in wide:
        # Walk back to the nearest opening <div ...> before this table and make
        # sure the enclosing card opted into horizontal scrolling.
        before = html[max(0, m.start() - 400):m.start()]
        assert "overflow-x:auto" in before, (
            f"a table with min-width:{m.group(1)}px at offset {m.start()} has no "
            "overflow-x:auto card around it — it will push the page sideways")


def test_the_stats_grid_never_disappears_a_tile(html):
    """The owner's complaint was information CHANGING between breakpoints. Every
    .stats rule may re-flow the columns; none may hide a tile."""
    for m in re.finditer(r"\.stats\{([^}]*)\}", html):
        body = m.group(1)
        assert "display:none" not in body, "a KPI tile must reflow, never vanish"


def test_no_media_query_hides_a_content_card(html):
    """Reflow, never remove — asserted across every @media block in the file."""
    for m in re.finditer(r"@media([^{]*)\{(.*?)\n\}", html, re.S):
        cond, body = m.group(1), m.group(2)
        if "prefers-reduced-motion" in cond:
            continue          # motion blocks legitimately switch things off
        for rule in re.finditer(r"(\.clay|\.card|\.stat)\b[^{]*\{([^}]*)\}", body):
            assert "display:none" not in rule.group(2), (
                f"@media{cond.strip()} hides {rule.group(1)} — §2 forbids losing "
                "information at a breakpoint")


#: Where the rail gives way to the drawer + bottom bar. 820px per plan §2. It was
#: 1180, which put every laptop at or below 1280 into the mobile layout.
RAIL_BREAKPOINT = 820


def test_the_rail_survives_on_a_small_laptop(html):
    """Fix 1. A 96px sidebar beside ~725px of content is comfortable, so a 1024 or
    1280 screen has no business being shown the phone layout. Revert this to 1180
    and every small laptop loses its sidebar again."""
    assert f"@media(max-width:{RAIL_BREAKPOINT}px){{" in html, (
        f"the rail must collapse at {RAIL_BREAKPOINT}px, not wider")
    assert "@media(max-width:1180px){" not in html, (
        "1180px is back — laptops at 1280 and below get the mobile drawer again")


def test_no_comment_asserts_the_old_breakpoint_as_current(html):
    """A comment stating a number the code no longer uses is the same defect as a
    stale docstring. Naming 1180 as HISTORY is fine — claiming it is the live
    behaviour is not, so only the present-tense forms are banned."""
    for stale in ("below 1180", "max-width:1180", "under 1180", "at 1180px"):
        assert stale not in html, f"a comment still claims {stale!r} is current"
    # The tour decides by measuring, so its comments must quote no width at all.
    for m in re.finditer(r"function onScreen|function open\(\)", html):
        block = html[m.start():m.start() + 700]
        assert "1180" not in block and "820" not in block, (
            "the tour measures real rects — a hardcoded width in its comments will "
            "go stale the next time the breakpoint moves")


def test_the_main_responsive_cascade_stays_widest_first(html):
    """Two matching blocks at equal specificity resolve by SOURCE ORDER, not by
    which query is narrower. The 720 block used to precede the 980 one, so
    anything both declared was silently dropped below 720px. Nothing collided at
    the time, which is exactly why it would have gone unnoticed.

    Scoped to the RESPONSIVE cascade — the one-off blocks elsewhere in the file
    (.p5row, .crumb-brand, .pager) are single-rule and independent of each other."""
    start = html.index("════ RESPONSIVE ════")
    section = html[start:start + 1800]
    widths = [int(w) for w in re.findall(r"@media\(max-width:(\d+)px\)\{", section)]
    assert len(widths) >= 5, f"expected the whole cascade, saw {widths}"
    assert widths == sorted(widths, reverse=True), (
        f"responsive blocks are out of order: {widths} — a narrower block declared "
        "before a wider one will be overridden by it")


def test_every_destination_is_reachable_when_the_rail_collapses(html):
    """Below the breakpoint the bottom bar only carries five entries, so the
    drawer is the only route to the other five — it must exist and open."""
    rail_hidden = re.search(
        rf"@media\(max-width:{RAIL_BREAKPOINT}px\)\{{(.*?)\n\}}", html, re.S)
    assert rail_hidden, "the rail's collapse breakpoint moved"
    assert ".menu-fab{display:inline-flex;}" in rail_hidden.group(1)
    assert "function toggleDock()" in html
    dock_pages = set(re.findall(r'class="dock-item[^"]*" data-page="(\w+)"', html))
    mob_pages = set(re.findall(r'class="mnbtn[^"]*" data-page="(\w+)"', html))
    # Anything not in the bottom bar has to be behind the drawer, not nowhere.
    assert mob_pages <= dock_pages, (
        f"bottom-bar destinations missing from the drawer: {mob_pages - dock_pages}")
