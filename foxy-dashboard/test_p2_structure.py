"""P2 structural guards for the dashboard SPA.

One rule here so far, and it exists because the same mistake landed three times.

**A field that something LISTENS to must be assigned through `setFieldValue`.**
Assigning `el.value = x` in JavaScript fires no `change` and no `input` event.
Every listener stays silent, so the field holds the right value while everything
watching it holds the old one. In this file that produced:

  · P1 §10  polConfidence — the server's value was applied on load and the
            segmented control kept showing the previous level
  · P2 §7.2 polMaxTokens  — same shape, caught while building the presets
  · P2 §8.2 setExportRange — the range buttons left the passport card's period
            stale

Each was found by hand, in review, after it shipped into the branch. Three is a
pattern rather than a run of bad luck, so this asserts it instead of watching
for it.

The analysis is deliberately shallow: it reads the inline scripts as text and
looks for `<id>.value =` where `<id>` is a field something listens to. It will
not catch an assignment through an alias it cannot see, and that is fine — it
needs to catch the FOURTH one, not to be a type system.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import pytest

HTML = Path(__file__).resolve().parent / "foxy-audit-premium.html"


@lru_cache(maxsize=1)
def _source() -> str:
    return HTML.read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def _raw_scripts() -> str:
    """Inline scripts with comments INTACT — the exemption markers live there."""
    return "\n".join(re.findall(
        r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", _source(), re.S))


@lru_cache(maxsize=1)
def _scripts() -> str:
    """Every inline <script>, comments stripped so a commented-out example or a
    `//` note about the bug does not read as the bug."""
    blocks = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>",
                        _source(), re.S)
    joined = "\n".join(blocks)
    joined = re.sub(r"/\*.*?\*/", "", joined, flags=re.S)
    joined = re.sub(r"(?m)//.*$", "", joined)
    return joined


@lru_cache(maxsize=1)
def watched_ids() -> frozenset[str]:
    """Element ids that something reacts to when they change.

    Two sources, both read from the shipped file:
      1. `onchange="..."` / `oninput="..."` attributes, taking the id off the
         same tag.
      2. id-list arrays wired in a loop, e.g.
         `['polMaxTokens','polEnforcement',...].forEach(... addEventListener('change'`
         which is how the policy page attaches its handlers.
    """
    src = _source()
    found: set[str] = set()

    for tag in re.findall(r"<[^>]*\bon(?:change|input)\s*=[^>]*>", src):
        hit = re.search(r'\bid\s*=\s*"([^"]+)"', tag)
        if hit:
            found.add(hit.group(1))

    for arr, tail in re.findall(r"\[((?:\s*'[A-Za-z0-9_]+'\s*,?\s*)+)\](.{0,400})",
                                _scripts(), re.S):
        if "addEventListener('change'" in tail or "addEventListener('input'" in tail:
            found.update(re.findall(r"'([A-Za-z0-9_]+)'", arr))

    # ids handled by a delegated document-level change listener
    for hit in re.findall(r"e\.target\.id==='([A-Za-z0-9_]+)'", _scripts()):
        found.add(hit)

    return frozenset(found)


def test_the_scanner_found_the_fields_it_is_supposed_to_watch():
    """If watched_ids() silently returned an empty set the real assertion below
    would pass while checking nothing, which is the failure mode that matters
    most for a guard like this."""
    ids = watched_ids()
    assert len(ids) >= 5, f"only found {len(ids)} watched fields: {sorted(ids)}"
    for expected in ("polMaxTokens", "polConfidence", "expFrom", "expTo"):
        assert expected in ids, (
            f"{expected} is wired to a change handler in the page but the "
            f"scanner did not find it — the guard has gone blind")


def test_the_helper_exists_and_notifies():
    """setFieldValue is the sanctioned path, so it has to actually dispatch."""
    js = _scripts()
    assert "window.setFieldValue=" in js, "the assign-and-notify helper is gone"
    body = js[js.index("window.setFieldValue="):][:400]
    assert "dispatchEvent" in body and "change" in body, (
        "setFieldValue no longer fires a change event, which makes every call "
        "site silently wrong again")
    assert "bubbles:true" in body.replace(" ", ""), (
        "the event must bubble — the export page listens for it on document")


def test_no_watched_field_is_assigned_without_notifying():
    """The guard itself. A bare `.value =` on a watched field is the bug.

    An assignment may opt out by carrying `setFieldValue-exempt: <reason>` in a
    comment on the same line. That is deliberately noisy: an exception has to be
    argued for in the code where the next reader will meet it, rather than
    living in a plan or a reviewer's memory."""
    raw_lines = [ln for ln in _raw_scripts().splitlines()
                 if "setFieldValue-exempt:" in ln]
    exempt = set()
    for line in raw_lines:
        exempt.update(re.findall(r"'([A-Za-z0-9_]+)'", line))
    js = _scripts()
    ids = watched_ids()
    offenders: list[str] = []

    for target, ident in re.findall(
            r"(?:\$\(\s*'([A-Za-z0-9_]+)'\s*\)|getElementById\(\s*'([A-Za-z0-9_]+)'\s*\))"
            r"\s*(?:\|\|\s*\{\}\s*)?\)?\s*\.value\s*=(?!=)", js):
        name = target or ident
        if name in ids and name not in exempt:
            offenders.append(name)

    assert not offenders, (
        "these fields are watched by a change/input handler but are assigned "
        f"directly: {sorted(set(offenders))}. Use setFieldValue(el, v) — a bare "
        f"`.value =` fires no event, so everything listening stays stale. This "
        f"has already shipped three times in this file.")


def test_native_confirm_and_prompt_are_gone_from_destructive_flows():
    """§9.3 replaced the browser's own dialogs with foxConfirm, which can say
    what the action does. window.prompt in particular cannot be styled, cannot
    explain itself, and is blocked outright in some embedded contexts."""
    js = _scripts()
    assert "window.prompt(" not in js, (
        "window.prompt is back — use foxConfirm({input:{...}}) so the dialog "
        "can explain what is about to happen")
    assert "window.foxConfirm=" in js, "foxConfirm is gone"


@pytest.mark.parametrize("page", [
    "dashboard", "analytics", "ledger", "verify", "policy",
    "export", "keys", "billing", "notifications", "settings",
])
def test_every_page_still_balances_its_divs(page):
    """Cheap structural canary. Several P2 sections move whole blocks between
    pages; an unbalanced page silently swallows the ones after it."""
    src = _source()
    start = src.index('id="page-%s"' % page)
    start = src.rindex('<div class="page', 0, start)
    nxt = src.find('<div class="page', start + 20)
    if nxt < 0:
        # the LAST page has no next page to bound it; without an end marker the
        # slice runs to EOF and swallows the dialogs, the toast and the scripts
        nxt = src.index('</main>', start)
    block = src[start:nxt]
    opened, closed = block.count("<div"), block.count("</div>")
    # the slice starts AT the page's own opening tag and ends before the next
    # page opens, so a balanced page is exactly even
    assert opened == closed, (
        f"page-{page} has {opened} <div> and {closed} </div> — a page that does "
        f"not close itself silently swallows the ones after it")
