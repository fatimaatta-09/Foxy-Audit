"""D4 · the payment lock, UI half — consuming D1's gate rather than rebuilding it.

Four things here are one plausible-looking edit from telling a customer
something untrue, and none of them look wrong on screen:

* **`locked` and `reason` answer different questions.** A `past_due` org inside
  its grace window reports `reason=subscription_past_due` with `locked=false`,
  and it is the org that most needs telling. Driving the overlay off `reason` —
  the obvious simplification — shuts out the customer the grace window exists to
  protect, and the screenshot still looks fine.
* **`capture_blocked` does not follow `locked`.** past_due locks the dashboard
  and keeps recording; cancelled stops recording and leaves the dashboard open.
  Collapsing them into one state tells someone their audit trail stopped when it
  did not, or that it is still running when it is not.
* **402 is not 401.** `auth.py` chose 402 precisely so the SPA can tell "we need
  paying" from "sign in again"; a 402 that bounces to the sign-in card looks
  exactly like a session bug and hides the thing the customer has to fix.
* **The escape hatches.** `/v1/billing/` and `/v1/auth/` are gate-exempt so the
  locked state can act on itself. A lock whose own buttons are locked is a
  support ticket.

The reason vocabulary is asserted against `backend/app/billing_state.py` rather
than a copy of it, so adding a seventh condition server-side fails here until
the dashboard has copy for it.
"""

from __future__ import annotations

import re

import pytest

from test_p1_contrast import HTML

BACKEND = HTML.parent.parent / "backend" / "app" / "billing_state.py"


@pytest.fixture(scope="module")
def html() -> str:
    return HTML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def lockjs(html: str) -> str:
    """Just the D4 script block, so a match elsewhere cannot pass a test about
    this one."""
    i = html.index("D4 · the payment lock, UI half")
    return html[i:html.index("</script>", i)]


@pytest.fixture(scope="module")
def render(lockjs: str) -> str:
    """The body of render() — where the locked/reason split is either honoured
    or quietly lost."""
    i = lockjs.index("function render(st){")
    return lockjs[i:lockjs.index("\n  var inflight", i)]


# ── the split that the whole phase is about ─────────────────────────────────

def test_the_overlay_is_opened_from_locked_and_from_nothing_else(render):
    """`classList.add('on')` must sit inside `if(st.locked)`, and no comparison
    against a reason string may gate it. This is the regression that looks like
    a tidy-up: `if(st.reason!=='none')` renders identically for five of the six
    conditions and locks out the sixth."""
    opens = re.findall(r"ov\.classList\.add\('on'\)", render)
    assert len(opens) == 1, f"the overlay is opened in {len(opens)} places"

    branch = render[render.index("if(st.locked){"):render.index("ov.classList.remove('on')")]
    assert "ov.classList.add('on')" in branch, "the overlay no longer opens from `locked`"
    assert "st.reason" not in branch.split("copyFor")[0], (
        "a reason is being consulted before the overlay is opened")


def test_the_banner_is_chosen_from_reason_and_never_from_locked(render):
    """The other half of the same rule: the words come from `reason`."""
    tail = render[render.index("ov.classList.remove('on')"):]
    assert "st.reason&&st.reason!=='none'" in tail, "the banner no longer reads `reason`"
    assert "st.locked" not in tail, "the banner is being chosen from `locked`"


def test_the_reason_vocabulary_matches_the_backends(lockjs):
    """One vocabulary, shared with the 402 body's `code`. Read from
    billing_state.py so a new condition added server-side lands here."""
    src = BACKEND.read_text(encoding="utf-8")
    wire = {
        m.group(2)
        for m in re.finditer(r'^([A-Z_]+) = "([a-z_]+)"$', src, re.M)
        if m.group(1) not in {"SUBSCRIPTION_INACTIVE"}   # capture-side name only
    }
    assert "none" in wire, "the vocabulary parse found nothing — the regex has rotted"

    table = lockjs[lockjs.index("var REASON={"):lockjs.index("var UNKNOWN=")]
    have = set(re.findall(r"^\s{4}([a-z_]+):", table, re.M))
    assert have == wire - {"none"}, (
        f"dashboard copy covers {sorted(have)}, the wire says {sorted(wire - {'none'})}")
    assert "none:" not in table, "`none` needs no copy — there is nothing to do"


def test_every_condition_carries_its_own_action(lockjs):
    """"Add a card" and "your last payment failed" need different things from
    the customer. A table entry with no `act` renders a lock with no way out."""
    table = lockjs[lockjs.index("var REASON={"):lockjs.index("var UNKNOWN=")]
    rows = re.findall(r"^\s{4}[a-z_]+:\s*\{(.*)\}", table, re.M)
    assert len(rows) == 5, f"expected five conditions, found {len(rows)}"
    for row in rows:
        for field in ("cta:", "act:"):
            assert field in row, f"a condition is missing {field}: {row}"
    assert "var UNKNOWN=" in lockjs, "an unrecognised reason has nothing to render"


def test_the_wording_of_a_condition_is_never_kept_here_as_well(lockjs):
    """The sentence naming the condition is on the wire, written once per reason
    by the code that decided it. Keeping a title here too put it on screen twice
    — "Your last payment failed / Your last payment failed and the grace period
    has ended." — and gave the two copies somewhere to drift apart. The heading
    is the message's own lead sentence; this table stops at the verb."""
    table = lockjs[lockjs.index("var REASON={"):lockjs.index("var FALLBACK_MSG")]
    assert not re.search(r"[{,]\s*t:", table), (
        "a per-reason title is back alongside the server's message")
    assert "function split(msg){" in lockjs, "the lead-sentence split is gone"

    body = lockjs[lockjs.index("function render(st){"):]
    assert "$('billLockT').textContent=p[0]" in body, (
        "the locked heading is no longer the server's own sentence")


# ── the audit trail, which is the product ───────────────────────────────────

def test_capture_state_is_read_from_its_own_field(lockjs):
    """Never from `locked`, and never inferred. The strip's three states are the
    three values `capture_blocked` can hold."""
    fn = lockjs[lockjs.index("function evidence(blocked){"):lockjs.index("/* Both actions POST")]
    assert "blocked===true" in fn and "blocked===false" in fn, (
        "the evidence strip is no longer switching on capture_blocked exactly")
    # String literals first — the markup names the .lock-ev classes. What is left
    # is code, and the only lock-ish identifier code may name is the parameter.
    # A plain "locked" not in fn misses window.__locked and st.locked alike,
    # because "blocked" contains both "locked" and "lock".
    code = re.sub(r"'[^']*'", "''", fn)
    names = set(re.findall(r"\w*lock\w*", code))
    assert names <= {"blocked"}, (
        f"the evidence strip is reading the dashboard lock: {sorted(names - {'blocked'})}")
    assert fn.rstrip().endswith("return '';\n  }"), (
        "an unknown capture state must draw nothing, not a default claim")


def test_an_unverified_capture_state_is_never_claimed(lockjs):
    """A 402 body carries `code` and `message` and nothing about capture. The
    fallback state must say so rather than pick the reassuring answer."""
    fn = lockjs[lockjs.index("window.foxLock402="):]
    assert "capture_blocked:null" in fn, (
        "the 402 fallback is asserting a capture state it was never told")


def test_the_banner_reports_capture_separately_from_the_reason(lockjs):
    fn = lockjs[lockjs.index("function banner("):lockjs.index("function clearBanner()")]
    assert "st.capture_blocked===true" in fn and "st.capture_blocked===false" in fn, (
        "the banner's capture clause is no longer read from capture_blocked")


# ── the 402 path ────────────────────────────────────────────────────────────

def test_the_dashboard_still_patches_fetch_exactly_twice(html):
    """D4 was told to extend the two existing patches, not add a third. Each
    patch is another wrapper every request pays for, and the ordering between
    them is load-bearing (CSRF inside, response handling outside)."""
    assert len(re.findall(r"window\.fetch\s*=\s*function", html)) == 2


def test_the_402_is_read_in_the_response_patch(html):
    i = html.index("interceptor: a 403 {detail:\"step_up_required\"}")
    patch = html[i:html.index("})();", i)]
    assert "res.status===402" in patch, "the 402 catch is not in the existing patch"
    assert "window.foxLock402" in patch


def test_a_402_never_reaches_the_sign_in_card(html):
    """The distinction 402 exists to carry. A 402 that shows #authGate is
    indistinguishable from a session bug and hides what has to be fixed."""
    i = html.index("res.status===402")
    branch = html[i:html.index("if(res.status!==403", i)]
    for forbidden in ("authGate", "gate.style", "location.reload", "foxLogout"):
        assert forbidden not in branch, f"a 402 is doing {forbidden}"


def test_the_402_passes_the_response_through_untouched(html):
    """It observes; it must not swallow. Every existing caller keeps whatever
    error handling it had."""
    i = html.index("res.status===402")
    branch = html[i:html.index("if(res.status!==403", i)]
    assert "res.clone()" in branch, "the 402 handler is consuming the caller's body"
    assert "return" not in branch, "the 402 handler is returning instead of observing"


def test_billing_access_is_called_on_boot(html):
    i = html.index("async function loadAll(){")
    fn = html[i:html.index("\n  async function check()", i)]
    assert "window.foxBillingAccess" in fn, "the billing state is not read on boot"
    assert "await window.foxBillingAccess" not in fn, (
        "the billing call is holding the dashboard back")


# ── the escape hatches ──────────────────────────────────────────────────────

def test_the_locked_state_can_act_on_itself(html):
    """Billing, sign-out and support are the three things that must work while
    locked, because they are how the lock gets cleared. `/v1/billing/` and
    `/v1/auth/` are gate-exempt in auth.py for exactly this."""
    i = html.index('<div class="lock-ov" id="billLock"')
    card = html[i:html.index("</div>\n</div>", i)]
    assert 'id="billLockGo"' in card, "the locked state has no action"
    assert 'id="billLockOut"' in card, "the locked state cannot sign out"
    assert "mailto:" in card, "the locked state cannot reach support"

    i = html.index("function run(kind,btn,say){")
    fn = html[i:html.index("function banner(", i)]
    assert "/v1/billing/card-setup-session" in fn and "/v1/billing/portal" in fn, (
        "the lock's actions no longer point at the gate-exempt billing paths")


def test_the_lock_sits_below_the_sign_in_card(html):
    """A session that expires while locked must show the sign-in card, not a
    lock nobody can act on."""
    rule = re.search(r"\.lock-ov\{([^}]*)\}", html)
    assert rule, "the overlay rule vanished"
    z = int(re.search(r"z-index:(\d+)", rule.group(1)).group(1))
    gate = int(re.search(r'id="authGate"[^>]*z-index:(\d+)', html).group(1))
    assert z < gate, f"the lock ({z}) covers the sign-in card ({gate})"


# ── the two traps this file has already been bitten by ──────────────────────

def test_no_live_text_in_the_lock_uses_the_disabled_only_ink(html):
    """--muted2 measures 3.01:1 dark and 2.71:1 light — under AA, on purpose,
    because it paints :disabled. Every rule D4 adds is live text."""
    i = html.index("D4 · THE PAYMENT LOCK")
    # Comments stripped first: the block names the token in order to warn against
    # it, and a note about a mistake is not the mistake.
    block = re.sub(r"/\*.*?\*/", "", html[i:html.index("/* motion respect */", i)], flags=re.S)
    assert "--muted2" not in block, "D4 pointed live text at the disabled-only ink"


def test_d4_declares_no_earlier_reduced_motion_block(html):
    """test_p1_contrast reads the FIRST @media(prefers-reduced-motion:reduce) in
    the file as the register of everything that loops. Declaring an earlier one
    silently makes it the register and every real check stops running with
    nothing going red (issue #43). D4 animates nothing and must add none."""
    blocks = re.findall(r"@media\(prefers-reduced-motion:reduce\)\{(.*?)\n\}", html, re.S)
    assert blocks, "the canonical reduced-motion block is gone"
    assert ".skel{animation:none;}" in blocks[0], (
        "the first reduced-motion block is no longer the canonical register")

    i = html.index("D4 · THE PAYMENT LOCK")
    block = re.sub(r"/\*.*?\*/", "", html[i:html.index("/* motion respect */", i)], flags=re.S)
    assert "prefers-reduced-motion" not in block, "D4 declared its own reduced-motion block"
    assert "animation:" not in block, "D4 added an animation without registering it"
