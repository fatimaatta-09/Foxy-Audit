"""Cancellation, reachable in-product (P3 §4.5).

`POST /v1/billing/cancel` has existed and been correct since the payment-gate
work — step-up gated, admin-only, rate limited, cancelling at period end so the
customer keeps what they have already paid for. Nothing in the dashboard called
it, which meant §4.5's actual requirement — "reachable without contacting
support" — was not met by a working endpoint nobody could reach.

The two things worth guarding here are the ones that make a cancel button
trustworthy rather than frightening: it asks before firing, and afterwards it
shows the date the SERVER returned rather than one the browser computed.
"""

from __future__ import annotations

import re

import pytest

from test_p1_contrast import HTML, _source
from test_p2_structure import _scripts


@pytest.fixture(scope="module")
def html() -> str:
    return _source()


def _fn(js: str, name: str) -> str:
    start = js.index(f"window.{name}=")
    return js[start:start + 2200]


# ══ it exists and is reachable ═════════════════════════════════════════════

def test_the_cancel_button_is_on_the_billing_page(html):
    assert 'id="cancelPlanBtn"' in html
    btn = re.search(r'<button[^>]*id="cancelPlanBtn"[^>]*>([^<]*)</button>', html)
    assert btn, "the cancel control is not a button"
    assert "cancel" in btn.group(1).strip().lower()
    assert 'onclick="cancelSubscription(this)"' in btn.group(0)


def test_it_calls_the_endpoint_that_already_exists(html):
    js = _scripts()
    fn = _fn(js, "cancelSubscription")
    assert "'/v1/billing/cancel'" in fn
    assert "method:'POST'" in fn.replace(" ", "")


def test_it_is_offered_but_not_encouraged(html):
    """A ghost button, not the primary one. Cancelling should be findable without
    being the loudest thing on the card."""
    btn = re.search(r'<button[^>]*id="cancelPlanBtn"[^>]*>', html).group(0)
    assert 'class="btn ghost"' in btn
    manage = re.search(r'<button[^>]*id="managePlanBtn"[^>]*>', html).group(0)
    assert 'class="btn pri"' in manage


def test_it_appears_under_the_same_condition_as_manage_billing(html):
    js = _scripts()
    fn = _fn(js, "loadPlan")
    assert "cancelPlanBtn" in fn
    assert "has_billing_account" in fn


# ══ it asks first ══════════════════════════════════════════════════════════

def test_it_confirms_before_firing(html):
    """Irreversible-feeling actions ask. Without this a mis-click cancels a
    paying customer's subscription."""
    fn = _fn(_scripts(), "cancelSubscription")
    assert "confirm(" in fn
    guard = fn[:fn.index("/v1/billing/cancel")]
    assert "confirm(" in guard, "the request fires before the confirmation"
    assert "return" in guard, "declining the confirmation does not stop the request"


def test_the_confirmation_says_what_actually_happens(html):
    """"Cancel?" implies instant loss. It is cancel-at-period-end, and saying so
    is the difference between an informed choice and a scare."""
    fn = _fn(_scripts(), "cancelSubscription")
    lowered = fn.lower()
    assert "end of the period" in lowered
    assert "not deleted" in lowered, "it does not say the evidence survives"


# ══ every status code is handled distinctly ════════════════════════════════

@pytest.mark.parametrize("code,phrase", [
    ("400", "no active subscription"),
    ("403", "admin"),
    ("503", "isn"),
])
def test_each_status_code_says_something_specific(html, code, phrase):
    """A bare "failed (400)" tells a customer nothing they can act on."""
    fn = _fn(_scripts(), "cancelSubscription").lower()
    assert f"r.status==={code}" in fn.replace(" ", "")
    assert phrase.lower() in fn


def test_there_is_a_fallback_for_everything_else(html):
    fn = _fn(_scripts(), "cancelSubscription")
    assert "+r.status+" in fn.replace(" ", ""), "an unexpected status is swallowed"
    assert "Could not reach the server" in fn, "a network failure is unhandled"


def test_it_mirrors_the_billing_portal_shape(html):
    """Same api() helper, same disabled/label dance, same toast vocabulary — the
    two buttons sit beside each other and should not behave differently."""
    js = _scripts()
    cancel, portal = _fn(js, "cancelSubscription"), _fn(js, "openBillingPortal")
    for shape in ("btn.disabled=true", "btn.disabled=false", "showToast("):
        assert shape.replace(" ", "") in cancel.replace(" ", ""), shape
        assert shape.replace(" ", "") in portal.replace(" ", ""), shape
    for code in ("400", "503"):
        assert f"r.status==={code}" in cancel.replace(" ", "")
        assert f"r.status==={code}" in portal.replace(" ", "")


# ══ the date is the server's ═══════════════════════════════════════════════

def test_it_shows_the_access_until_the_endpoint_returned(html):
    """The endpoint returns the real period end from Stripe. Anything the browser
    computes instead is a guess presented as a fact."""
    fn = _fn(_scripts(), "cancelSubscription")
    assert "access_until" in fn
    assert 'id="planCancelUntil"' in _source()
    assert 'id="planCancelRow"' in _source()


def test_no_date_is_invented_when_the_server_sends_none(html):
    """access_until is nullable — the endpoint returns None when Stripe gives no
    period end. An honest empty state, not a fabricated date."""
    fn = _fn(_scripts(), "cancelSubscription")
    flat = fn.replace(" ", "")
    assert "if(until&&row&&val)" in flat, "the null case is not handled"
    assert "elseif(row){row.style.display='none'}" in flat.replace("；", ";") or \
        "row.style.display='none'" in flat
    for invented in ("Date.now()", "new Date()", "+30", "setDate("):
        assert invented.replace(" ", "") not in flat, f"a date is computed locally: {invented}"


def test_the_row_starts_hidden(html):
    """Nothing claims an end date before there is one."""
    row = re.search(r'<div class="erow" id="planCancelRow"[^>]*>', _source()).group(0)
    assert "display:none" in row


# ══ nothing leaks ══════════════════════════════════════════════════════════

def test_the_cancel_path_carries_no_secret_names(html):
    fn = _fn(_scripts(), "cancelSubscription")
    for forbidden in ("password_hash", "key_hash", "token_hash", "_key_enc",
                      "stripe_subscription_id", "sk_live"):
        assert forbidden not in fn
