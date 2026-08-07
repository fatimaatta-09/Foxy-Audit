"""Static guards for the page a new customer sees first (M4d · welcome.html).

This is the whole first impression: it runs before the product, before the
dashboard, and it is the one and only place the plaintext API key is ever shown.

M4a made a demo signup provision a PENDING organisation — one that cannot
capture and cannot read its dashboard until a human approves it, with the 7-day
clock starting at approval. This page did not know that. It rendered four
confident steps ending in "Open my dashboard →", and that dashboard answered 402
with "we're reviewing your request": a promise the backend refuses.

Most of what follows is about the pending path specifically, because
DEMO_APPROVAL_REQUIRED ships OFF — so it is the path nobody sees by accident and
the one a render pass will miss unless it is produced deliberately.

    python -m pytest foxy-sale-page -q
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
WELCOME = (HERE / "welcome.html").read_text(encoding="utf-8")
INDEX = (HERE / "index.html").read_text(encoding="utf-8")
#: The dashboard's own locked-state copy, which this page has to agree with.
DASH = (HERE.parent / "foxy-dashboard" / "foxy-audit-premium.html").read_text(encoding="utf-8")


def _script() -> str:
    """welcome.html's single inline script."""
    blocks = [
        m.group(1)
        for m in re.finditer(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", WELCOME, re.S)
        if m.group(1).strip()
    ]
    assert len(blocks) == 1, f"expected one inline script, found {len(blocks)}"
    return blocks[0]


def _code() -> str:
    """The script minus its comments.

    Both kinds. This file explains its own guards in prose and so does the page,
    and a guard that greps a body greps the sentence describing it — which has
    caught three phases running on these surfaces, once through an apostrophe
    desynchronising a quote-stripping regex. Reading only the executable part
    removes the whole class.
    """
    src = re.sub(r"/\*.*?\*/", "", _script(), flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", src)


def _pending_branch() -> str:
    """The body of `if (PENDING) { … }`, by brace balance."""
    code = _code()
    i = code.index("if (PENDING)")
    j = code.index("{", i)
    depth = 0
    for k in range(j, len(code)):
        if code[k] == "{":
            depth += 1
        elif code[k] == "}":
            depth -= 1
            if depth == 0:
                return code[j: k + 1]
    raise AssertionError("the pending branch never closes")


# ── 1 · the promise the page must stop making ────────────────────────────────

def test_the_pending_path_cannot_send_anyone_to_the_dashboard() -> None:
    """THE guard this phase had to earn.

    A workspace waiting for approval answers 402 on every dashboard read. Sending
    somebody there is the defect M4d exists to remove, and it has to be
    impossible rather than merely unlikely — so the branch may not name the
    dashboard constant or its URL at all.
    """
    branch = _pending_branch()
    assert "DASH" not in branch, (
        "the pending branch still reaches for the dashboard destination")
    assert "app.foxyaudit.tech" not in branch, (
        "the pending branch hardcodes a dashboard URL")
    assert "/dashboard" not in branch, "the pending branch routes to a dashboard"
    m = re.search(r"DEST\s*=\s*'([^']+)'", branch)
    assert m, "the pending branch never redirects the final control anywhere"
    assert m.group(1).endswith(".html"), (
        f"the pending destination {m.group(1)!r} is not a page on this site")


def test_the_final_control_still_exists_and_still_records_consent() -> None:
    """It is not removed, and that is a deliberate difference from the
    dashboard's locked card. That control was the last thing on a screen the
    customer was already inside; this one ends a setup flow — and it is the ONLY
    thing that POSTs the terms consent, so deleting it would silently drop the
    audit row the checkbox exists to produce.
    """
    code = _code()
    assert "toDash.addEventListener('click'" in code, "the final control lost its handler"
    assert "/v1/consent" in code, "the consent POST is gone"
    branch = _pending_branch()
    assert "toDash.remove()" not in branch and "display='none'" not in branch.replace(" ", ""), (
        "the pending branch removes the control that records consent")
    assert "disabled = true" not in branch and "disabled=true" not in branch.replace(" ", ""), (
        "the pending branch disables the control rather than re-aiming it — a "
        "disabled button still says there is something here for you")


def test_the_terms_interlock_survives() -> None:
    """Step 1 gates the final button until the terms are accepted. Restructuring
    around that interlock is exactly how it gets lost."""
    code = _code()
    assert "toDash.disabled = !agree.checked" in code, "the terms gate is gone"
    assert "if (!agree.checked) return;" in code, (
        "the click handler no longer refuses an unaccepted state")
    assert 'id="agree"' in WELCOME, "the terms checkbox is gone"


# ── 2 · the key, which is the reason this page is security-sensitive ─────────

def test_a_pending_signup_is_still_shown_its_key() -> None:
    """Signup issues the key either way and the plaintext cannot be recovered, so
    hiding it behind the wait would turn a delay into a lost credential. It
    simply does not work yet."""
    branch = _pending_branch()
    for hiding in ("keyEl.style", "apiKey').style", "keybox"):
        assert hiding not in branch, f"the pending branch hides the key ({hiding})"
    assert "copyKey" not in branch, "the pending branch removes the copy control"
    assert "shown <b>once</b>" in WELCOME, "the shown-once warning is gone"


def test_the_key_wording_matches_the_dashboards_locked_state_exactly() -> None:
    """Two surfaces describing one wait in two wordings is how they drift. The
    dashboard's locked card already says this; agreeing with it word for word is
    cheaper than keeping them in step later.
    """
    sentence = ("Capture begins when your workspace is approved. "
                "Your API key is already issued and works from that moment.")
    assert sentence in DASH.replace("' \n        +'", "").replace("'\n        +'", ""), (
        "the sentence this guard pins is no longer the dashboard's — re-read the "
        "locked state before changing the page")
    branch = _pending_branch()
    joined = re.sub(r"'\s*\+\s*'", "", branch)
    assert sentence in joined, (
        "welcome.html no longer says what the dashboard says about when the key "
        "starts working")


def test_the_key_never_reaches_a_url_or_storage_it_should_not() -> None:
    """The page's standing rules, re-asserted because this phase touched the
    script that handles it."""
    code = _code()
    assert "localStorage" not in code, "the key path gained localStorage"
    assert "location.search" not in code and "history.pushState" not in code, (
        "the key path gained a URL surface")
    assert "sessionStorage.removeItem('foxy_welcome')" in code, (
        "the shown-once stash is no longer cleared after reading")


# ── 3 · what the sender has to forward ───────────────────────────────────────

def test_signup_forwards_the_fields_this_page_reads() -> None:
    """welcome.html cannot read what index.html does not hand it. The stash
    carried only {email, api_key}, so the pending state was unreachable here no
    matter what this page did with it."""
    assert INDEX.count("foxy_welcome") >= 2, "the signup stash is gone"
    for stash in re.findall(r"sessionStorage\.setItem\('foxy_welcome',(.*?)\);", INDEX, re.S):
        assert "approval_status" in stash, (
            "a signup path stashes no approval_status, so welcome.html cannot "
            "know the workspace is waiting")
        assert "api_key" in stash, "a signup path stopped forwarding the key"


# ── 4 · the desktop lock, and the reason it gives ────────────────────────────

def test_the_desktop_download_is_locked_and_not_hidden() -> None:
    """A customer should be able to see what buying adds. Locked, with the
    console's own disabled treatment, rather than absent."""
    branch = _pending_branch()
    assert "dlrow" in branch, "the pending branch leaves the download row alone"
    assert branch.count("disabled") >= 2, (
        "the two downloads are not both rendered as unavailable controls")
    assert "<button" in branch, (
        "the locked downloads are still anchors — a dead link is not a disabled "
        "control, and nothing announces it as unavailable")
    assert ".btn[disabled]" in WELCOME, "the disabled treatment this relies on is gone"


def test_the_desktop_lock_blames_the_plan_and_not_the_wait() -> None:
    """They are different waits. Telling a demo user the app arrives on approval
    is a promise nothing will keep — approval starts the 7 days, it does not add
    the desktop app."""
    branch = _pending_branch()
    note = branch[branch.index("dlNote"):]
    assert "paid plan" in note, "the lock does not say what actually unlocks it"
    assert "approval does not unlock it" in note, (
        "the lock does not rule out the reading a customer will otherwise make")
    for wrong in ("once approved", "after approval", "when approved"):
        assert wrong not in note, (
            f"the desktop lock says {wrong!r}, which ties it to the approval wait")


# ── 5 · what the page must not claim while waiting ───────────────────────────

def test_the_subtitle_stops_promising_a_trial_that_has_not_started() -> None:
    """The default subtitle sells a 7-day trial and 500 credits. For a pending
    workspace neither has begun — the clock starts at approval."""
    branch = _pending_branch()
    assert "sub" in branch and "textContent" in branch, (
        "the pending branch leaves the subtitle promising a running trial")
    joined = re.sub(r"'\s*\+\s*'", "", branch)
    seg = joined[joined.index("sub.textContent"):]
    seg = seg[: seg.index(";")]
    for claim in ("7-day trial", "500 audit-event credits", "7 days of"):
        assert claim not in seg, f"the pending subtitle still claims {claim!r}"


def test_the_pending_notice_uses_the_servers_own_sentence() -> None:
    """The same discipline the dashboard's locked card follows: the wording that
    names the condition is written once, by the code that decided it. A copy kept
    here is a copy that can drift — the fallback exists only for a stash written
    by an older build."""
    branch = _pending_branch()
    assert "data.message" in branch, (
        "the notice does not render the server's own message")
    assert "esc(" in branch, "the server's message is interpolated unescaped"


def test_the_page_adds_no_emoji() -> None:
    """14 of 23 pages already break this site's own written rule. This phase does
    not make it 15."""
    emoji = re.findall(
        r"[\U0001F300-\U0001FAFF←-⇿☀-➿]", _pending_branch())
    allowed = {"→"}          # → , already the page's own CTA convention
    stray = sorted(set(emoji) - allowed)
    assert not stray, f"the pending branch introduces {stray}"


def test_the_page_still_loads_its_typeface() -> None:
    """This surface fetches Poppins from Google Fonts rather than embedding it,
    and six pages omit the tags and silently fall back to system-ui. welcome.html
    is not one of them; a phase that touches the head must keep it that way."""
    assert "fonts.googleapis.com/css2?family=Poppins" in WELCOME, (
        "welcome.html stopped loading Poppins and now renders in system-ui")
    assert 'rel="preconnect"' in WELCOME, "the font preconnect is gone"


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
