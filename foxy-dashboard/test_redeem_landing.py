"""Static guards for the evaluation-link landing (round 2 · item 7).

The link is the whole feature's attack surface: it carries the redemption code in
a query string, and the page it lands on changes billing state. These pin the
things that must not quietly regress.

    python -m pytest foxy-dashboard/test_redeem_landing.py -q
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

DASH = Path(__file__).with_name("foxy-audit-premium.html").read_text(encoding="utf-8")
SALE = (Path(__file__).parent.parent / "foxy-sale-page" / "index.html").read_text(encoding="utf-8")


def _landing() -> str:
    start = DASH.index("Round 2 · evaluation redemption landing")
    return DASH[start: DASH.index("</script>", start)]


# ── the landing itself ───────────────────────────────────────────────────────

def test_the_landing_only_runs_for_a_link():
    """It must not affect normal boot — no ?offer=, no overlay, no fetch."""
    body = _landing()
    assert "get('offer')" in body
    assert re.search(r"if\(!code\)\s*return", body), "the landing runs unconditionally"


def test_the_landing_sits_above_the_gate_but_below_the_step_up():
    """9999 is #authGate, 10001 is the step-up overlay it triggers. Landing at
    10000 covers the sign-in card and is still covered by the step-up prompt."""
    body = _landing()
    assert "z-index:10000" in body
    assert "z-index:9999" in DASH and "z-index:10001" in DASH


def test_the_landing_does_not_build_its_own_step_up():
    """The P15 interceptor already catches the 403, prompts and retries the same
    request. A second step-up implementation is a second set of bugs."""
    body = _landing()
    assert "step-up/request" not in body and "step-up/confirm" not in body
    assert "foxStepUp" not in body


def test_the_landing_states_the_trade_before_the_button():
    """Redeeming is not purely additive: it swaps the free monthly quota for a
    finite, expiring allowance, and capture STOPS when the window closes. A user
    has to be told that before they confirm, not after."""
    body = _landing()
    confirm = body[body.index("function offerConfirm"): body.index("function submit")]
    assert "capture stops" in confirm.lower(), "the landing does not disclose the trade"
    assert "free monthly quota" in confirm.lower()
    assert confirm.index("capture stops") < confirm.index("redeemGo"), \
        "the trade is disclosed after the button rather than before it"


def test_the_landing_shows_the_real_reason_a_redeem_was_refused():
    """Each guard answers with its own {code,message}; a generic failure would
    make a paid-plan refusal indistinguishable from a used-up campaign."""
    body = _landing()
    assert "m.message" in body, "the guard's own message is not surfaced"


def test_the_landing_does_not_leave_the_code_in_the_address_bar():
    body = _landing()
    assert "function cleanUrl" in body and "replaceState" in body
    # ...and it is actually called on both terminal paths.
    assert body.count("cleanUrl()") >= 2, "the code survives in the URL on some path"


def test_a_signed_out_visitor_is_routed_to_signup_with_the_code():
    body = _landing()
    out = body[body.index("function signedOut"):]
    assert "encodeURIComponent(code)" in out, "the code is not carried, or not encoded"
    assert "foxyaudit.tech/?offer=" in out


# ── the other half: the sale page receives it ────────────────────────────────

def test_the_sale_page_prefills_the_code_and_then_drops_it():
    assert "openLeadModal('judge-offer'" in SALE, "the link does not open the offer modal"
    block = SALE[SALE.index("The other half of the shareable evaluation link"):]
    block = block[: block.index("})();")]
    assert "searchParams.delete('offer')" in block, "the code stays in the shared URL"
    assert "replaceState" in block


def test_the_prefill_reaches_the_field_that_is_submitted():
    """openLeadModal used to hard-clear the code field on every open."""
    fn = SALE[SALE.index("function openLeadModal("):]
    fn = fn[: fn.index("\n}")]
    assert "prefillCode" in fn
    assert "leadOfferCode.value = ''" not in fn, "the prefill is cleared on open"


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
