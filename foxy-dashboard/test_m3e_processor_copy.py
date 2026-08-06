"""The dashboard stopped naming the wrong processor to paying customers (M3e).

Paddle has taken a real purchase. A customer who paid through Paddle was still
told by their own dashboard that the amount is "confirmed by Stripe at checkout"
and that invoices arrive "after your first Stripe billing cycle" — invoices that
will never arrive, because they would come from a processor with no account.

WHY THESE GUARDS READ STRINGS AND NOT THE FILE. `foxy-audit-premium.html` still
mentions Stripe a dozen times in developer comments, correctly: the Stripe code
path is still wired in the backend and those comments describe it. A blanket
"this file must not say Stripe" guard would fail on them — and on the sentence
you are reading. So each guard names the customer-facing string it governs.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

HTML = (Path(__file__).resolve().parent / "foxy-audit-premium.html").read_text(
    encoding="utf-8")


def _visible_text() -> str:
    """Everything a customer can read: markup with comments, <script> and <style>
    removed. This is the half of the file the copy rules apply to."""
    out = re.sub(r"<!--.*?-->", "", HTML, flags=re.S)
    out = re.sub(r"<script[^>]*>.*?</script>", "", out, flags=re.S)
    return re.sub(r"<style[^>]*>.*?</style>", "", out, flags=re.S)


def _js_strings() -> str:
    """The quoted strings inside the inline scripts — where the two invoice
    empty states live, since they are built by the billing loader."""
    js = " ".join(re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", HTML, re.S))
    js = re.sub(r"(?m)(?<!:)//.*$", "", js)
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    return js


# ── 1 · no customer is told the wrong processor ─────────────────────────────

def test_no_visible_copy_names_the_processor_that_never_charged_anyone() -> None:
    """Scoped to what renders. The developer comments that still say it are
    correct — the Stripe path is live code — and are deliberately excluded."""
    needle = "Strip" + "e"
    assert needle not in _visible_text(), (
        "customer-visible markup still names a processor this workspace was "
        "not charged by"
    )


def test_the_invoice_empty_states_do_not_promise_a_stripe_cycle() -> None:
    needle = "Strip" + "e"
    for phrase in ("No invoices yet", "Invoice totals", "Card payments appear here"):
        for m in re.finditer(re.escape(phrase) + r"[^'\"]*", _js_strings()):
            assert needle not in m.group(0), m.group(0)[:120]


def test_the_invoice_empty_state_is_true_for_a_customer_who_paid_by_invoice() -> None:
    """An org invoiced directly through Payoneer is legitimately paid up and will
    never have a row here (register #94, not fixed in this phase). "Invoices
    appear after your first billing cycle" is a promise this list cannot keep for
    them, so the empty state has to be true for both kinds of paying customer."""
    js = _js_strings()
    assert "Plans invoiced directly are not listed" in js, (
        "the empty state still implies every paying customer gets invoices here"
    )
    assert js.count("Plans invoiced directly are not listed") == 2, (
        "only one of the two invoice empty states was corrected"
    )


# ── 2 · the merchant of record is stated where it helps ─────────────────────

def test_the_statement_descriptor_is_stated_at_the_point_of_purchase() -> None:
    """Paddle is the merchant of record, so the charge is Paddle's, not ours. A
    buyer who does not know that sees an unrecognised line, and an unrecognised
    line is how a chargeback starts — the most expensive thing that can happen to
    a brand-new merchant account."""
    text = _visible_text()
    m = re.search(r"<div id=\"upgradeList\">.*?</div>\s*</div>", HTML, re.S)
    assert m, "the upgrade panel is gone"
    panel = HTML[m.start():m.end() + 700]
    assert "PADDLE.NET" in panel, "the point of purchase does not say what the charge looks like"
    assert "merchant of record" in panel
    assert "PADDLE.NET" in text, "the note is not visible copy"


def test_the_note_names_only_the_part_of_the_descriptor_that_is_fixed() -> None:
    """`PADDLE.NET*` is the fixed prefix. What follows the asterisk is a 2–10
    character descriptor the seller configures in Paddle, defaulting to the first
    ten characters of the company name given at signup. Asserting a suffix we
    have not verified would be inventing the one string a worried customer checks
    against."""
    text = _visible_text()
    for invented in ("PADDLE.NET* FOXY", "PADDLE.NET*FOXY", "PADDLE.NET* FOXYAUDIT"):
        assert invented not in text, f"an unverified statement descriptor is claimed: {invented}"


def test_the_note_is_not_buried_in_a_tooltip() -> None:
    """It has to be read without hovering anything."""
    m = re.search(r"[^<>]*PADDLE\.NET[^<>]*", _visible_text())
    assert m, "PADDLE.NET is not in readable text at all"
    holder = HTML[max(0, HTML.index("PADDLE.NET") - 400):HTML.index("PADDLE.NET")]
    assert "data-tip" not in holder.split("<div")[-1], "the note sits inside a tooltip"
    assert "title=" not in holder.split("<div")[-1]


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
