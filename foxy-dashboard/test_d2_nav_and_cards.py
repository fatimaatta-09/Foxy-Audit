"""D2 · the three fixes from `docs/plans/dashboard-newest-issues.md`.

Item 1 removed two Settings explainer cards and the home "Recent activity" card.
Item 3 gave in-dashboard navigation real history entries so Back stops leaving
for the sales page. Item 5 made the side rail scroll instead of squishing its
items on a short viewport.

Items 3 and 5 are both one-line reverts away from silently coming back — a
deleted `pushState` looks like tidying, and a `flex-shrink:0` looks redundant
next to a fixed height until you meet a 500px-tall window. Each guard below
names the failure it prevents.
"""

from __future__ import annotations

import re

import pytest

from test_p1_contrast import HTML


@pytest.fixture(scope="module")
def html() -> str:
    return HTML.read_text(encoding="utf-8")


# ── item 1 · the cards are gone, and nothing that survived them is orphaned ──

def test_the_home_recent_activity_card_is_gone(html):
    """It was injected, so it leaves no markup behind — only these ids and the
    feed that filled them. Matched as code, not as prose: the comments left in
    place name what was removed, and should be free to."""
    for dead in ('id="homeActivityCard"', "$('homeActivityFeed')",
                 "$('homeActivityStamp')", "function injectHomeCard(",
                 "window.loadGlobalActivity="):
        assert dead not in html, f"{dead} came back — the home card was removed in D2"


def test_the_settings_explainer_cards_are_gone(html):
    """By their content rather than their titles, for the same reason."""
    assert "prompt_commitment &nbsp;= HMAC-SHA256" not in html
    assert "Never stored or transmitted:" not in html


def test_the_content_blindness_claim_still_gets_made(html):
    """The reason removing "What Foxy stores" was safe. If a later edit strips
    the remaining statements this fails, and someone gets to decide deliberately
    rather than discover it in an audit."""
    claims = [
        "it never reads raw prompts or responses",
        "never your prompts or responses",
        "raw prompts and responses are never exported",
        "never the prompt or response text",
    ]
    missing = [c for c in claims if c not in html]
    assert not missing, f"the product's core claim lost these statements: {missing}"


def test_account_activity_kept_its_card_and_its_callers(html):
    """A different card, on Settings, reading the same endpoint. It stays — and
    /v1/account/audit therefore still has a consumer."""
    assert 'id="auditCard"' in html
    assert "window.loadAccountAudit=" in html
    assert html.count("loadAccountAudit()") >= 2, (
        "the Account activity card needs both its refresh control and its "
        "admin-boot call")


def test_no_endpoint_lost_its_last_consumer(html):
    """The two endpoints the deleted feed read are both still fetched elsewhere."""
    assert "/v1/account/audit" in html
    assert "/v1/auth/login-history" in html


# ── item 3 · Back stays inside the dashboard ──

def test_navigation_files_a_history_entry(html):
    """The root cause: the file called replaceState three times and pushState
    never, so Back left on the first press.

    Both halves matter, and the second is the one a revert reaches first —
    deleting the call in go() leaves entry() defined and still called from the
    home floor, so asserting only that pushState exists somewhere passes a
    dashboard whose navigation has stopped recording anything."""
    assert "history.pushState({foxPage:page},'')" in html
    assert "if(!popping&&page!==here)entry(page);" in html, (
        "go() must file an entry, or Back has nothing to walk back through")


def test_landing_on_home_puts_an_entry_back_underneath(html):
    """The floor. Without it, Back walks off the end of the stack and exits."""
    assert "if(page===HOME)entry(HOME);" in html


def test_the_token_strippers_are_still_replaceState(html):
    """cleanUrl() and the two handoff strippers must never become pushState: they
    exist to take a one-time token OUT of the URL, and pushState would file that
    token in the history instead."""
    calls = re.findall(r"history\.replaceState\(", html)
    assert len(calls) == 4, (
        "expected the three token strippers plus D2's one state label, "
        f"found {len(calls)}")
    assert "history.replaceState(null,'','/dashboard')" in html
    assert "history.replaceState(null,'',location.pathname)" in html


def test_pushState_never_carries_a_url(html):
    """Two arguments means "reuse the current URL". A third would put whatever is
    in the address bar — including a one-time token — into the history."""
    for m in re.finditer(r"history\.pushState\(([^)]*)\)", html):
        assert m.group(1).count(",") == 1, (
            f"pushState({m.group(1)}) passes a URL — it must not")


def test_the_caveat_is_written_where_the_loop_is(html):
    """This stops an accidental click and nothing more. A future reader who
    believes otherwise will build on a guarantee that does not exist."""
    assert "It is not a guarantee." in html
    assert "Holding the Back button" in html


# ── item 5 · the rail scrolls instead of squishing ──

def test_the_dock_is_a_scrollport(html):
    dock = re.search(r"\.dock\{([^}]*)\}", html)
    assert dock, "the .dock rule changed shape"
    assert "overflow-y:auto" in dock.group(1), (
        "a fixed top:0;bottom:0 flex column with no overflow compresses its "
        "items on a short viewport — this is the fix")


def test_the_rail_items_refuse_to_shrink(html):
    """overflow-y alone changes nothing: flex would still shrink the items to fit
    rather than let the column overflow and scroll."""
    assert ".dock-mark,.dock-item,.dock-foot{flex-shrink:0;}" in html


def test_the_rail_scrollbar_follows_the_theme(html):
    """A hardcoded thumb colour is invisible in one of the two themes."""
    assert "scrollbar-color:var(--muted2) transparent" in html
    assert ".dock::-webkit-scrollbar-thumb{background:var(--muted2)" in html


def test_the_drawer_breakpoint_is_untouched(html):
    """Below 820px the rail is a drawer, which is why this bug only lived between
    "wide enough for the rail" and "tall enough for its items"."""
    assert "@media(max-width:820px){" in html
    assert ".dock{display:flex;transform:translateX(-110%)" in html
