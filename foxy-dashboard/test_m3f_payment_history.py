"""The dashboard's payment list, once it holds more than Stripe (M3f · #94).

Two kinds of payment now reach it: a card payment from the Paddle webhook and a
payment invoiced directly, recorded when a staff member activates the plan. They
have to be legible in one list without being pretended into the same thing.

Both defects the new rows made reachable were in one expression — `money()`
divided by 100 for every currency and turned a null amount into $0.00 — so most
of these guards are about arithmetic rather than markup.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

HTML = (Path(__file__).resolve().parent / "foxy-audit-premium.html").read_text(
    encoding="utf-8")


def _money_src() -> str:
    """The INVOICE money formatter — the two-argument one. There is a second,
    one-argument `money` earlier in the file for plain counts; matching the wrong
    one would guard nothing."""
    m = re.search(r"const money=\(cents,cur\)=>\{.*?\n  \};", HTML, re.S)
    assert m, "the invoice money formatter is gone"
    return m.group(0)


def _no_comments(js: str) -> str:
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    return re.sub(r"(?m)(?<!:)//.*$", "", js)


# ── 1 · money ───────────────────────────────────────────────────────────────

def test_the_amount_is_not_divided_by_a_hundred_for_every_currency() -> None:
    """¥1000 IS ¥1000 and 1000 KWD is 1.000 KWD. A flat /100 understates yen 100x
    and overstates dinar 10x — the failure `desktop/billing_data.py`'s
    ZERO_DECIMAL and THREE_DECIMAL tables exist to prevent."""
    src = _no_comments(_money_src())
    assert "/100" not in src.replace(" ", ""), "a flat /100 divisor is back"
    assert "maximumFractionDigits" in src, (
        "the divisor no longer comes from the currency's own minor-unit count"
    )
    assert "Math.pow(10," in src


def test_an_unrecorded_amount_is_a_dash_not_zero() -> None:
    """A staff activation records WHICH payment was seen, not how much it was.
    `$0.00` would tell a customer they paid nothing."""
    src = _no_comments(_money_src())
    assert re.search(r"cents===null\|\|cents===undefined", src), (
        "a null amount is no longer handled before formatting"
    )
    assert "(cents||0)" not in src, "a null amount coerces to zero again"


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_the_formatter_agrees_with_the_desktops_currency_tables() -> None:
    """Run the shipped function rather than read it.

    The locale is pinned to en-US for the run: the shipped call passes
    `undefined` so a customer sees their own grouping, and this guard is about
    the DIVISOR, not about whose comma is where. The three ISO-4217 shapes plus
    the null case, matching what `desktop/billing_data.money` renders for the
    same inputs.
    """
    src = _money_src().replace("new Intl.NumberFormat(undefined,",
                               "new Intl.NumberFormat('en-US',")
    script = src + """
const cases = [[4900,'usd','$49.00'], [1000,'jpy','¥1,000'],
               [1000,'kwd','KWD 1.000'], [null,'usd','—'], [undefined,'usd','—']];
const bad = [];
for (const [c, cur, want] of cases) {
  /* Intl separates a currency CODE from its number with a NON-BREAKING space
     (KWD 1.000). Normalising it keeps this about the digits and the decimal
     placement, which is what the divisor decides, rather than about Unicode
     spacing — the first version of this guard asserted a plain space and was
     wrong about its own expectation, not about the formatter. */
  const got = money(c, cur).replace(/ /g, ' ');
  if (got !== want) bad.push(c + ' ' + cur + ': got ' + got + ' want ' + want);
}
console.log(bad.length ? 'FAIL ' + bad.join(' | ') : 'OK');
"""
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                     encoding="utf-8") as fh:
        fh.write(script)
        path = fh.name
    proc = subprocess.run(["node", path], capture_output=True, text=True,
                          encoding="utf-8")
    out = (proc.stdout or "") + (proc.stderr or "")
    assert "OK" in out, out


# ── 2 · the two kinds of payment stay distinguishable ───────────────────────

def test_the_list_says_how_each_payment_arrived() -> None:
    """Legible in one list without pretending a card payment and an invoice are
    the same thing."""
    assert "const payVia=" in HTML, "the how-it-arrived label is gone"
    m = re.search(r"const payVia=.*?;", HTML, re.S)
    for provider in ("paddle", "stripe", "manual"):
        assert provider in m.group(0), f"{provider} has no label"
    assert "payVia(i.provider)" in HTML, (
        "the label is never rendered — the row cannot tell the two kinds apart"
    )


def test_a_pdf_link_is_offered_only_where_one_exists() -> None:
    """`/v1/invoices/{id}/link` resolves through Stripe. A Paddle or manually
    recorded payment has no hosted invoice, and a link that always fails is worse
    than no link."""
    row = HTML[HTML.index("$('invBody').innerHTML"):]
    row = row[:row.index("</tr>')")]
    assert "i.provider==='stripe'" in row, (
        "the PDF link is rendered for rows Stripe cannot resolve"
    )
    assert "esc(i.reference)" in row, (
        "a non-Stripe row shows neither a link nor the reference it could be "
        "identified by"
    )


def test_the_chart_never_plots_an_unrecorded_amount_as_zero() -> None:
    """A bar of height zero claims the customer paid nothing."""
    m = re.search(r"const invArr=.*?;", HTML, re.S)
    assert m, "the invoice chart's data is gone"
    assert "amount_cents!=null" in m.group(0), (
        "rows with no recorded amount are still plotted, as zero"
    )


def test_the_empty_state_still_covers_both_kinds_of_paying_customer() -> None:
    """M3e's wording stays true: an org invoiced directly can be paid up with an
    empty list. That is now less common but still possible — a plan activated
    with no reference records no payment."""
    assert HTML.count("Plans invoiced directly are not listed") == 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
