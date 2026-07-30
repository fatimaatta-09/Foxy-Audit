"""P3 §5 · the first-run walkthrough.

Two things these guard that a reading of the diff would not.

The tour is an OVERLAY over controls that already exist, so its worst failure is
silent: it keeps working while pointing at nothing, or at the wrong thing. Every
selector it aims at is asserted to exist in this same file — if someone renames a
dock item's `data-page`, a step stops having a target and this suite says so
rather than a user meeting an empty spotlight.

And §5.2's escape hatches are load-bearing. We deliberately did NOT build the
blocking tutorial that was originally asked for, so "Esc exits" and "Skip is
visible" are the whole justification for that decision — they are asserted to be
unconditional, not merely present.
"""

from __future__ import annotations

import re

import pytest

from test_p1_contrast import HTML


@pytest.fixture(scope="module")
def html() -> str:
    return HTML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def tour_js(html) -> str:
    """The tour's own script block, so a match cannot come from elsewhere.

    Anchored on the assignment, not on `window.foxTourStart` — the name appears
    earlier in the command-palette entry, and matching that returned the wrong
    block entirely."""
    i = html.index("window.foxTourStart=open;")
    j = html.rindex("<script>", 0, i)
    return html[j:html.index("</script>", i)]


# ── §5.1 · it points at real controls ──────────────────────────────────────

def test_every_step_points_at_an_element_that_exists(html, tour_js):
    """The failure this prevents: a renamed page id leaves a step aiming at
    nothing, and the tour quietly shrinks or skips it."""
    sels = re.findall(r"'(\.(?:dock-item|mnbtn)\[data-page=\"(\w+)\"\])'", tour_js)
    assert sels, "no step selectors found — did the STEPS table move?"
    for _sel, page in sels:
        assert f'data-page="{page}"' in html, f"no element for step target {page!r}"
    # …and the one non-nav target.
    assert '.topbar-actions .topbtn[aria-label^="Search"]' in tour_js
    assert 'aria-label="Search and jump (command palette)"' in html


def test_the_step_count_matches_the_steps_actually_defined(tour_js):
    """Counted inside the STEPS table only — begin() also builds `{sel:` objects
    when it filters for on-screen targets."""
    table = tour_js[tour_js.index("var STEPS=["):tour_js.index("var steps=[]")]
    assert len(re.findall(r"\{sel:\[", table)) == 6


def test_no_step_invents_a_target(tour_js):
    """A step with nothing real to point at must be dropped, not faked."""
    assert "if(el)steps.push(" in tour_js
    assert "if(!steps.length){ close(); return; }" in tour_js


# ── §5.2 · Esc always exits, Skip is always visible ────────────────────────

def test_escape_exits_and_is_not_conditional_on_the_step(tour_js):
    """Guard 1. `if(!live)return;` is the ONLY gate allowed in front of Escape —
    anything keyed off `idx` would mean Esc works on some steps and not others."""
    m = re.search(r"if\(e\.key==='Escape'\)\{([^}]*)\}", tour_js)
    assert m, "the Escape branch is gone"
    body = m.group(1)
    assert "finish(false)" in body
    assert "idx" not in body, "Esc must not depend on which step is showing"


def test_the_skip_control_is_static_markup_not_per_step(html, tour_js):
    """Guard 2. Skip lives in the overlay's markup, so it is in the DOM on every
    step by construction. render() must never remove or hide it."""
    assert 'id="tourSkip"' in html
    assert not re.search(r"tourSkip'\)\.(style|remove|hidden)", tour_js), \
        "render() must not hide or drop the skip control"
    # Back is allowed to disable on step 1; Skip is not allowed to disable at all.
    assert "$('tourBack').disabled" in tour_js
    assert "$('tourSkip').disabled" not in tour_js


def test_skip_and_esc_record_a_skip_not_a_completion(tour_js):
    assert "$('tourSkip').onclick=function(){ finish(false); };" in tour_js
    assert "save(completed?{tutorial_completed:true}:{tutorial_skipped:true})" in tour_js


# ── §5.4 · keyboard + screen reader ────────────────────────────────────────

def test_focus_moves_to_the_highlighted_step(tour_js):
    assert "$('tourBubble').focus()" in tour_js


def test_the_step_number_is_announced(html, tour_js):
    """role=dialog + a per-step aria-label is what a screen reader reads on
    focus. The visible counter is aria-hidden so it is not said twice."""
    assert 'role="dialog"' in html and 'id="tourBubble"' in html
    assert "setAttribute('aria-label','Step '+n+' of '+m" in tour_js
    assert re.search(r'id="tourCount" aria-hidden="true"', html)


def test_tab_is_trapped_inside_the_tour(tour_js):
    m = re.search(r"if\(e\.key==='Tab'\)\{[\s\S]{0,400}?\n    \}", tour_js)
    assert m, "the Tab branch is gone"
    assert "e.preventDefault()" in m.group(0)
    assert "shiftKey" in m.group(0), "Shift+Tab must cycle backwards too"


def test_reduced_motion_is_respected(html):
    block = html[html.index("#tourOverlay{"):html.index("</style>", html.index("#tourOverlay{"))]
    assert "@media(prefers-reduced-motion:no-preference)" in block, \
        "tour motion must be opt-in, matching the rest of this file"
    # The hole/bubble motion must sit INSIDE that guard, not outside it.
    guarded = block[block.index("@media(prefers-reduced-motion:no-preference)"):]
    assert "transition:top" in guarded and "animation:afade" in guarded


def test_the_buttons_clear_the_44px_target(html):
    block = html[html.index("#tourOverlay{"):html.index("</style>", html.index("#tourOverlay{"))]
    assert "#tourBack,#tourNext{min-height:44px;}" in block
    assert "#tourSkip{min-height:44px;}" in block


# ── the two bugs rendering found ───────────────────────────────────────────

def test_the_first_placement_does_not_fly_in_from_the_corner(html, tour_js):
    """Found by rendering: the hole's untouched position is 0,0, so step 1
    transitioned a lit rectangle diagonally across the whole viewport."""
    assert "#tourHole.tour-jump{transition:none;}" in html
    assert "hole.classList.add('tour-jump')" in tour_js
    assert "hole.classList.remove('tour-jump')" in tour_js


def test_the_dock_is_not_measured_mid_slide(html, tour_js):
    """Found by rendering at 420px: the tour opened the dock and measured during
    its .25s slide, landing the spotlight on a stat tile in the page body and
    silently dropping two of six steps."""
    assert "#app.tour-dock .dock{transition:none;}" in html
    assert "app.classList.add('tour-dock')" in tour_js
    assert "void app.offsetWidth" in tour_js, "layout must be forced before measuring"


def test_an_off_canvas_target_is_not_treated_as_visible(tour_js):
    """A size check alone calls the off-canvas dock visible, because it is
    translated rather than hidden."""
    assert "r.right>0&&r.bottom>0&&r.left<window.innerWidth&&r.top<window.innerHeight" in tour_js


# ── §5.3 / §5.5 · reachable again, and killable ────────────────────────────

def test_the_tour_is_reachable_after_it_is_dismissed(html):
    """No help menu exists in this product; the palette and the account menu are
    where a user would look."""
    assert "Take the product tour" in html
    assert 'id="userMenuTour"' in html
    assert "window.foxTourStart" in html


def test_the_feature_flag_is_read_from_the_server(tour_js):
    """§5.5 · this file is static and cannot see Settings, so the kill switch has
    to arrive on a response the page already fetches."""
    assert "t.enabled&&!t.completed" in tour_js
    assert "'/v1/onboarding'" in tour_js


def test_a_skipped_tour_is_not_offered_again(tour_js):
    """P6a, reversing §5.3. The condition used to key off `completed` only, so a
    skip did not suppress the next offer. Because this gate runs on page load and
    not on a session boundary, "the next offer" meant the next REFRESH — the owner
    reported it, and it is the first item on the punch-list.

    The server has persisted `skipped` since the tour shipped; only the client
    ignored it. Both flags are the user's answer and either one is a no."""
    gate = tour_js.split("t.enabled")[1][:200]
    assert "!t.completed" in gate
    assert "!t.skipped" in gate, "a skipped tour must not be offered again"


def test_a_vanished_target_records_neither_flag(tour_js):
    """render() bails when a step's target has gone — the page changed under the
    tour. That is not the user answering, and under the new gate writing `skipped`
    there would retire the tour for somebody who never saw a step of it. It has to
    close through a path that saves nothing.

    Skip and Esc are unaffected: those ARE the user answering, and they still
    record a skip (see test_skip_and_esc_record_a_skip_not_a_completion)."""
    assert "if(!el){ abandon(); return; }" in tour_js, \
        "the abort path no longer routes through abandon()"
    m = re.search(r"function abandon\(\)\{(.*?)\n  \}", tour_js, re.S)
    assert m, "abandon() is gone"
    assert "save(" not in m.group(1), \
        "abandon() must not write tutorial state — that is the whole point of it"


def test_a_signed_out_page_gets_no_tour(tour_js):
    assert "if(!r.ok)return;" in tour_js


# ── it must not disturb the checklist it sits beside ───────────────────────

def test_the_firstrun_checklist_is_untouched(html):
    """§5 is a different feature from the P4 onboarding card. Both ship."""
    assert 'id="firstRun"' in html
    assert "window.loadFirstRun" in html
    assert 'id="firstRunDismiss"' in html
    # The tour writes its own keys and must never send the checklist's.
    i = html.index("window.foxTourStart=open;")
    tour = html[html.rindex("<script>", 0, i):]
    assert "dismissed:true" not in tour, "the tour must not dismiss the checklist"


def test_the_tour_sits_below_the_auth_gate(html):
    """If the session drops mid-tour, the sign-in gate must cover the tour."""
    assert "#tourOverlay{display:none;position:fixed;inset:0;z-index:9998;}" in html
    assert 'id="authGate"' in html and "z-index:9999" in html
