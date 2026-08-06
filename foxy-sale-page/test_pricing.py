"""Static guards for the sale page's commercial surface (M4b).

`pricing.html` is what Paddle's domain reviewer reads to decide whether this
business may take real money, and what a regulated-industry buyer reads to decide
whether it is a real company. It has no build step and no framework, so these are
the checks a merge gate can run: what is offered, for how much, and that no
control on the page promises something the backend cannot do.

The first suite this surface has had. It does NOT run in CI — see the report.

    python -m pytest foxy-sale-page -q
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
PRICING = (HERE / "pricing.html").read_text(encoding="utf-8")
INDEX = (HERE / "index.html").read_text(encoding="utf-8")
DEMO = (HERE / "book-a-demo.html").read_text(encoding="utf-8")
SITE_CSS = (HERE / "site.css").read_text(encoding="utf-8")

#: The two prices this deployment actually sells, and the tier that has none.
PRO_PRICE = "$49"
MAX_PRICE = "$199"


def _no_comments(src: str) -> str:
    """Source with HTML and JS comments stripped.

    Load-bearing, not tidiness: these files explain their own rules, and the
    comment recording *why* the companion and guardian plans were removed
    contains both words. A guard that scans raw text is satisfied — or in that
    case defeated — by the prose describing it. This project has been bitten by
    that four times.
    """
    src = re.sub(r"<!--.*?-->", "", src, flags=re.S)
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"(?m)(?<!:)//.*$", "", src)


def _cards() -> list[str]:
    return re.findall(r'<div class="price-card">(.*?)</div>\s*(?=<div class="price-card">|</div>)',
                      _no_comments(PRICING), re.S)


def _card(name: str) -> str:
    """One price card's markup, by tier name.

    Split rather than matched with a lookahead: the first version anchored the
    end of the last card on an HTML comment, which `_no_comments` had already
    removed — so Premium, the last card, could never be found. A helper that
    depends on the thing another helper deletes is a helper that only works on
    the cards you happened to test.
    """
    segments = _no_comments(PRICING).split('<div class="price-card">')[1:]
    for seg in segments:
        if f'<p class="price-name">{name}</p>' in seg:
            return seg
    raise AssertionError(f"no price card named {name}")


# ── 1 · what is on sale, and for how much ───────────────────────────────────

def test_exactly_three_tiers_are_offered() -> None:
    """The 2026-08-06 commercial model is Pro, Max and Premium. A fourth card is
    either a tier nobody decided to sell or one nobody can buy."""
    names = re.findall(r'<p class="price-name">([^<]+)</p>', _no_comments(PRICING))
    assert names == ["Pro", "Max", "Premium"], f"tiers on the page are {names}"


def test_pro_and_max_show_their_real_monthly_price() -> None:
    """A pricing page without an amount is the question Paddle's reviewer has to
    stop and ask, and weeks of review is the cost of them asking."""
    assert f'{PRO_PRICE}<span>/mo</span>' in _card("Pro"), "Pro shows no monthly price"
    assert f'{MAX_PRICE}<span>/mo</span>' in _card("Max"), "Max shows no monthly price"


def test_premium_quotes_no_number() -> None:
    """Premium is negotiated end to end — credits, terms and price. Any figure
    printed here would be one this company cannot honour for every buyer, which
    is the fake-data rule applied to a price rather than to a metric."""
    card = _card("Premium")
    assert "$" not in card, "Premium quotes an amount it cannot honour for everyone"
    assert "Let's talk" in card


def test_the_free_tier_is_no_longer_sold_as_a_tier() -> None:
    """Free is gone from the commercial model for new signups. It must not appear
    as a priced card beside the three that are sold."""
    assert "$0" not in _no_comments(PRICING)
    assert "Free trial" not in _no_comments(PRICING)


def test_the_free_account_route_is_still_reachable_and_not_called_a_demo() -> None:
    """The transitional truth. Free signup is still the ONLY way a new person can
    open an account — the approved 7-day demo does not exist yet — so a pricing
    page that hid it would contradict the homepage button that does exactly that.

    It is not a card and it is not called a trial or a demo: a manually-approved
    demo is not what that button does today, and saying so would be the fake-state
    version of fake data.
    """
    band = re.search(r'<div class="enterprise-card">(.*?)</div>\s*</div>',
                     _no_comments(PRICING), re.S)
    assert band, "the free-account band is gone; the only signup route is now unmentioned"
    body = band.group(1)
    assert "/?plan=free" in body, "the band does not link to the signup that works"
    assert not re.search(r"\bdemo\b", body, re.I), (
        "the free account is presented as a demo, which is a state that does not exist"
    )
    assert not re.search(r"\btrial\b", body, re.I), (
        "the free account is presented as a trial tier again"
    )


# ── 2 · nothing on the page promises what the processor cannot do ───────────

def test_no_plan_without_a_price_can_be_selected_anywhere() -> None:
    """`companion` and `guardian` have no price at the processor. `guardian` was
    also a one-time lifetime purchase, so it was never a matter of adding one."""
    for name, src in (("pricing.html", PRICING), ("index.html", INDEX),
                      ("book-a-demo.html", DEMO)):
        code = _no_comments(src)
        for dead in ("companion", "guardian"):
            assert f"signup-{dead}" not in code, f"{name} still wires signup-{dead}"
            assert f"'{dead}'" not in code, f"{name} can still ask to buy {dead}"


def test_checkout_is_offered_for_exactly_the_sellable_plans() -> None:
    code = _no_comments(INDEX)
    branch = re.search(r"if \(src === 'signup-pro'[^)]*\) \{", code)
    assert branch, "the checkout branch is gone"
    assert "signup-max" in branch.group(0)
    assert branch.group(0).count("signup-") == 2, (
        f"the checkout branch handles more than pro and max: {branch.group(0)}"
    )


def test_a_failed_checkout_does_not_promise_an_email() -> None:
    """That answer was honest while no processor was configured. Paddle is
    configured now, so this path only fires on a REAL fault — and telling
    somebody you will email them to finish a purchase that just failed is a
    promise nobody is going to keep."""
    code = _no_comments(INDEX)
    assert "email you to finish checkout" not in code
    branch = code[code.index("if (src === 'signup-pro'"):]
    branch = branch[:branch.index("if (src === 'signup-free'")]
    assert "nothing was charged" in branch, "the failure does not say what happened"
    assert "postLead" not in branch, (
        "a failed purchase still quietly files the buyer as a lead"
    )


def test_premium_routes_to_the_conversation_pipeline() -> None:
    """Premium is sold by conversation, then invoiced. It must not reach a card
    checkout, and it reuses the existing qualified-lead form rather than a second
    one built beside it."""
    card = _card("Premium")
    assert "book-a-demo.html?plan=premium" in card
    assert "?plan=premium" not in _card("Pro") and "?plan=premium" not in _card("Max")
    demo = _no_comments(DEMO)
    assert "'plan') === 'premium'" in demo, (
        "book-a-demo does not read the tag, so a Premium enquiry is "
        "indistinguishable from a generic demo request in the inbox"
    )
    assert "Premium plan enquiry" in demo
    assert "source:'demo'" in demo, "the existing lead pipeline was rerouted"


# ── 3 · the surface's own rules ─────────────────────────────────────────────

def test_the_ghost_button_has_a_visible_boundary() -> None:
    """It drops the clay shadow, so its border is the ONLY thing separating it
    from the card. `--line` measures 1.29:1 against that card — under 1.4.11's
    3:1 — and this rule had never rendered anywhere before this page used it."""
    m = re.search(r"\.price-btn\.ghost\{([^}]*)\}", SITE_CSS)
    assert m, ".price-btn.ghost is gone"
    assert "border:1px solid var(--muted)" in m.group(1), (
        "the ghost button's only boundary is back under 3:1"
    )


def test_the_two_copies_of_the_ghost_rule_agree() -> None:
    """index.html carries its own copy of the pricing CSS. Two definitions of one
    rule is how a contrast fix lands on the page nobody was looking at."""
    def rule(src: str) -> str:
        m = re.search(r"\.price-btn\.ghost\{([^}]*)\}", src)
        assert m, "the ghost rule is missing from one of the two files"
        return m.group(1).strip()
    assert rule(SITE_CSS) == rule(INDEX), "the two ghost rules have drifted"


def test_the_brand_font_is_requested_the_way_this_surface_does_it() -> None:
    """This surface FETCHES Poppins from Google Fonts; the two consoles embed it.
    They are opposite strategies and pricing.html follows its own site — six pages
    here omit the tags entirely and fall back to system-ui, which is a real
    inconsistency but not this phase's to fix."""
    assert "fonts.googleapis.com" in PRICING
    assert 'rel="preconnect"' in PRICING


def test_pricing_stays_free_of_emoji() -> None:
    """`pricing.html` has never carried one and must not start.

    Deliberately scoped to this page. `index.html` already contains a waving-hand
    emoji and several dingbat ticks that predate this phase — the surface has 14
    such pages — and a guard that failed on them would be reporting somebody
    else's debt as this phase's regression. What this phase must not do is ADD
    one, which the copy guard below covers for the strings it introduced.
    """
    found = re.findall(r"[\U0001F300-\U0001FAFF\U00002600-\U000027BF]", PRICING)
    assert not found, f"pricing.html contains emoji: {found[:5]}"


def test_the_copy_this_phase_added_carries_no_emoji() -> None:
    """The two strings written into `index.html` and `book-a-demo.html`."""
    for src in (INDEX, DEMO):
        for line in _no_comments(src).splitlines():
            if "nothing was charged" in line or "Premium plan enquiry" in line:
                found = re.findall(r"[\U0001F300-\U0001FAFF\U00002600-\U000027BF]", line)
                assert not found, f"new copy contains emoji: {found}"


def test_pricing_still_needs_no_javascript_of_its_own() -> None:
    """141 lines and zero script blocks is why this page is robust. The FAQ's
    inline onclick is the whole of its behaviour."""
    blocks = [b for b in re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>",
                                    PRICING, re.S) if b.strip()]
    assert blocks == [], f"{len(blocks)} script blocks appeared on pricing.html"


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_every_inline_script_on_the_changed_pages_parses() -> None:
    import tempfile
    for name, src in (("index.html", INDEX), ("book-a-demo.html", DEMO)):
        for i, block in enumerate(re.findall(
                r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", src, re.S)):
            if not block.strip():
                continue
            with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                             encoding="utf-8") as fh:
                fh.write(block)
                path = fh.name
            proc = subprocess.run(["node", "--check", path],
                                  capture_output=True, text=True)
            assert proc.returncode == 0, f"{name} block {i}: {proc.stderr}"


def test_the_stylesheet_braces_balance() -> None:
    assert SITE_CSS.count("{") == SITE_CSS.count("}"), (
        f"site.css: {SITE_CSS.count('{')} open, {SITE_CSS.count('}')} close"
    )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
