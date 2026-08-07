"""P6 — three things this page was telling somebody that were not true.

* **#108** — every billing-lock remedy is admin-only, and P4 gave members a door
  for one of the three. On the other two the CTA still ran, still 403'd, and
  still ended at a sentence telling them to go and do a thing the product gave
  them no way to do.
* **the filter chips** — `Quota` and `Account` matched an exact `kind` the
  backend has never written, so both could only ever answer "Nothing here".
* **#101, the UI half** — a Pro workspace carrying a pre-M3b `trial_ends_at` was
  told its trial ends in six days, on the banner and on the plan card.

The claim under all three is the same one: a control that cannot do what it says
is worse than no control, and a sentence nobody can act on is worse than silence.
"""

from __future__ import annotations

import re

import pytest

from test_p1_contrast import HTML

BACKEND = HTML.parent.parent / "backend" / "app"


@pytest.fixture(scope="module")
def html() -> str:
    return HTML.read_text(encoding="utf-8")


def _no_comments(js: str) -> str:
    """JS with its comments removed.

    Every scan below looks for a route name or a call, and this file explains
    its own rules in prose that names both. Four phases running have tripped on
    a guard reading its own explanation — once via an apostrophe desynchronising
    a quote-stripping regex, which is why only COMMENTS come out here and the
    string literals are left exactly as they are.
    """
    js = re.sub(r"/\*.*?\*/", " ", js, flags=re.S)
    return re.sub(r"(?<![:'\"])//[^\n]*", " ", js)


@pytest.fixture(scope="module")
def lockjs(html: str) -> str:
    """The D4 lock block, so a match elsewhere in 7,000 lines cannot pass a test
    about this one — P5's copy guard was green while measuring nothing for
    exactly that reason."""
    i = html.index("D4 · the payment lock, UI half")
    return html[i:html.index("</script>", i)]


# ══ #108 · a member is never handed an admin-only remedy ═══════════════════
def test_every_remedy_goes_through_the_same_who_check(lockjs: str):
    """One gate in front of all three, not a branch per action. `card` and
    `portal` were reachable because they shared the path BELOW the `upgrade`
    branch and nothing above it asked who was pressing."""
    run = lockjs[lockjs.index("function run(kind,btn,say){"):
                 lockjs.index("function act(kind,btn,say){")]
    assert "who().then(" in run, "run() dispatches without asking who is pressing"
    assert "can_purchase===false" in run
    assert "offerAsk(btn,say)" in run

    # THE regression #108 was, and the one a mutation walked straight through:
    # narrowing the gate to `kind==='upgrade'` leaves both assertions above true
    # and puts card and portal back where they started. So the member branch is
    # checked for being blind to `kind` — it may only pass it on, never test it.
    gate = _no_comments(run[run.index("who().then("):])
    assert "kind===" not in gate and "kind ===" not in gate, (
        "the member check is conditional on the action again — every remedy is "
        "admin-only, so every one of them must reach the ask")
    assert gate.count("kind") == 1 and "act(kind,btn,say)" in gate
    # Every remedy lives in act(), and act() is reachable only through run().
    act = lockjs[lockjs.index("function act(kind,btn,say){"):]
    act = act[:act.index("\n  var num=")]
    for path in ("card-setup-session", "billing/portal", "upgrade"):
        assert path in act, f"{path} left act(), so the gate no longer covers it"
    assert "who(" not in act, "act() re-asks instead of being gated once"


def test_act_is_called_from_nowhere_but_the_gate(lockjs: str):
    """Assert the CALL SITE. A perfect gate nobody routes through is exactly as
    open as no gate — and `run` is the name every button is wired to."""
    calls = re.findall(r"\bact\(", lockjs)
    assert len(calls) == 2, f"act() is called {len(calls)-1} times outside run()"
    assert "return act(kind,btn,say);" in lockjs


def test_a_member_cannot_reach_a_money_route_by_any_route(lockjs: str):
    """The three admin-only endpoints may only be fetched from inside `act`."""
    money = ("/v1/billing/card-setup-session", "/v1/billing/portal",
             "/v1/billing/upgrade-session")
    # Everything from `act` to `banner` is behind the gate: act() is entered
    # only from run(), and buy() + chooser() are entered only from act()'s
    # `upgrade` branch. Anything OUTSIDE that span reaching a money route would
    # be a second door with no check on it.
    inside = lockjs[lockjs.index("function act(kind,btn,say){"):
                    lockjs.index("function banner(")]
    outside = _no_comments(lockjs.replace(inside, ""))
    for path in money:
        assert path not in outside, f"{path} is reachable outside the gate"
    # And the chain that puts buy()/chooser() inside it, asserted rather than
    # assumed: chooser is reached from act, and buy only from chooser's rows.
    assert "return chooser(btn,say);" in inside
    chooser = inside[inside.index("function chooser(btn,say){"):]
    assert "buy(b.getAttribute('data-plan'),b,say)" in chooser
    assert _no_comments(lockjs).count("buy(") == 2


def test_the_ask_is_p4s_and_there_is_not_a_second_one(lockjs: str, html: str):
    """One copy of those sentences. They are matched verbatim by the desktop and
    by welcome.html, so a second ask here would be a third wording to keep in
    step with two surfaces that read this file."""
    assert lockjs.count("foxAskAdmin.render(") == 2, (
        "the ask is rendered from more than the chooser and offerAsk")
    offer = lockjs[lockjs.index("function offerAsk(btn,say){"):
                   lockjs.index("function run(kind,btn,say){")]
    assert "window.foxAskAdmin.render(box,w,'lock')" in offer
    # No sentence of the ask is retyped here — it all comes from the one block.
    for line in ("Only an admin can buy a plan", "Notify the admins",
                 "The admins have been told"):
        assert offer.count(line) == 0, f"the ask copy is duplicated: {line!r}"


def test_an_unlocked_member_is_sent_where_the_ask_already_lives(lockjs: str):
    """Not locked means the upgrade page is reachable and already carries the
    block — which is exactly what the `upgrade` branch does for an admin."""
    offer = lockjs[lockjs.index("function offerAsk(btn,say){"):
                   lockjs.index("function run(kind,btn,say){")]
    assert "window.go('upgrade',null)" in offer
    assert "billLock').classList.contains('on')" in offer


def test_an_unknown_answer_falls_through_to_the_route_that_guards(lockjs: str):
    """`can_purchase===false` is strict on purpose. A lookup that failed is not
    "you are a member" — falling through to the admin path leaves the SERVER
    deciding, which is the only safe direction to fail."""
    who = lockjs[lockjs.index("function who(){"):lockjs.index("function offerAsk(")]
    assert "return w||{}" in who and "function(){ return {}; }" in who
    assert "can_purchase==false" not in lockjs, "the check went loose"


def test_the_403_sentences_stay_as_the_backstop(lockjs: str):
    """The gate is a courtesy; the route is the rule. A role that changes
    mid-session must still meet a refusal that explains itself."""
    assert "Only a workspace admin can change billing." in lockjs
    assert "Only a workspace admin can buy a plan." in lockjs


# ══ the filter chips ═══════════════════════════════════════════════════════
def _written_kinds() -> set[str]:
    """Every `kind` the backend actually writes to `notifications`, read out of
    the source rather than from a list kept here — a list kept here is the thing
    that drifted into two chips matching nothing."""
    kinds = set()
    for path in (BACKEND / "user_notifications.py",
                 BACKEND / "routers" / "account.py"):
        text = path.read_text(encoding="utf-8")
        kinds |= set(re.findall(r'kind=["\']([a-z_]+)["\']', text))
        kinds |= set(re.findall(r'^UPGRADE_REQUEST_KIND = "([a-z_]+)"',
                                text, re.M))
    return {k for k in kinds if k}


def test_every_chip_can_match_a_kind_the_backend_writes(html: str):
    """The whole of the second fix. `loadNotifPage` filters on an EXACT match,
    so a chip naming a kind nothing writes is a control guaranteed to find
    nothing."""
    if not (BACKEND / "user_notifications.py").exists():
        pytest.skip("backend sources not present")
    written = _written_kinds()
    assert "breach" in written and "upgrade_request" in written, written
    chips = set(re.findall(r'data-nfilter="([a-z_]*)"', html))
    # "" is All and "unread" is the endpoint's own flag — neither is a kind.
    kind_chips = chips - {"", "unread"}
    assert kind_chips, "the kind chips vanished entirely"
    dead = kind_chips - written
    assert not dead, (
        f"chips that can never match anything the backend writes: {sorted(dead)}")


def test_the_filter_is_still_an_exact_match(html: str):
    """The guard above is only meaningful while this is true. If the filter ever
    groups or aliases, a chip could legitimately name something that is not a
    kind, and that test would be asserting the wrong rule."""
    i = html.index("window.loadNotifPage=async function(){")
    fn = html[i:html.index("};", i)]
    assert "items.filter(function(x){ return x.kind===_nFilter; })" in fn


def test_the_empty_state_stops_promising_events_that_never_arrive(html: str):
    """Three copies of one sentence named quota and account events. Neither is
    ever written, so the promise was as false as the chips."""
    assert "quota and account events" not in html
    assert html.count(
        "Breaches, weekly summaries and account events will show up here.") == 3


def test_the_kinds_with_no_chip_still_have_an_explainer(html: str):
    """The chips are deliberately a SMALLER set than the kinds, so the page must
    still be able to say what an unfiltered row means. `KIND_HELP` is where that
    lives, and it is keyed defensively (`KIND_HELP[kind]||null`)."""
    if not (BACKEND / "user_notifications.py").exists():
        pytest.skip("backend sources not present")
    block = html[html.index("var KIND_HELP={"):html.index("var LEVEL_WORD=")]
    documented = set(re.findall(r"^\s{4}([a-z_]+):\{", block, re.M))
    missing = _written_kinds() - documented
    assert not missing, f"kinds the backend writes with no explainer: {sorted(missing)}"


def test_quota_keeps_its_explainer_even_with_no_chip(html: str):
    """`quota` is a kind somebody INTENDED and never wrote — `models.py`'s own
    docstring says notifications come from "policy breaches, quota crossings".
    The chip goes because it lies; the explainer stays because it costs nothing,
    is keyed defensively, and is already correct if quota crossings are ever
    written. Deleting the chip and deleting the intent are different bets."""
    block = html[html.index("var KIND_HELP={"):html.index("var LEVEL_WORD=")]
    assert "quota:{" in block
    assert 'data-nfilter="quota"' not in html


# ══ #101 · a paid workspace has no trial ═══════════════════════════════════
def test_the_paid_test_is_the_backends_own(html: str):
    """`billing_state.on_a_paid_plan` is `plan_tier` not in {"", "free"}. A
    dashboard that disagrees with the server about who is paying is how the next
    three entries get filed."""
    if not (BACKEND / "billing_state.py").exists():
        pytest.skip("backend sources not present")
    backend = (BACKEND / "billing_state.py").read_text(encoding="utf-8")
    fn = backend[backend.index("def on_a_paid_plan"):]
    fn = fn[:fn.index("\ndef ")]
    assert 'not in {"", "free"}' in fn, "the backend rule moved"
    i = html.index("window.foxPaidTier=function(tier){")
    js = html[i:html.index("};", i)]
    assert "['','free']" in js
    assert ".trim().toLowerCase()" in js, "the two rules normalise differently"


def test_both_trial_surfaces_ask_the_tier_and_not_just_the_field(html: str):
    """Two sites, and neither used to read `plan_tier` — which arrives in the
    SAME response object both of them already hold."""
    row = html[html.index("const tr=$('planTrialRow');"):]
    row = row[:row.index("const manage=")]
    assert "!window.foxPaidTier(p.plan_tier)" in row

    banner = html[html.index("window.loadAnnBanner=async function(){"):]
    banner = banner[:banner.index("if(!msg){ try{ var u=await api('/v1/usage")]
    assert "!window.foxPaidTier(plan.plan_tier)" in banner


def test_a_free_org_inside_its_trial_still_sees_both(html: str):
    """The condition is the TIER, never the field. Hiding the row whenever the
    value is set would take the warning away from the only people it is for."""
    row = html[html.index("const tr=$('planTrialRow');"):]
    row = row[:row.index("const manage=")]
    assert "p.trial_ends_at&&" in row, "the field is no longer required"
    banner = html[html.index("window.loadAnnBanner=async function(){"):]
    banner = banner[:banner.index("if(!msg){ try{ var u=await api('/v1/usage")]
    assert "plan.trial_ends_at&&" in banner
    # And the window is still the web's own: within seven days, not any date.
    assert "days>=0&&days<=7" in banner


def test_the_predicate_is_defined_once_for_both_blocks(html: str):
    """They live in different <script> blocks. Two copies of one predicate is
    the drift this file has logged against `policy_data` and `KIND_HELP`."""
    assert html.count("window.foxPaidTier=function") == 1
    assert html.count("window.foxPaidTier(") == 2


def test_the_usage_strip_was_already_right_and_is_untouched(html: str):
    """`/v1/usage`'s `trial_active` is computed server-side WITH the free-tier
    test in it (`account.py:202`), so the third place that says "trial ends"
    never had the defect. Pinned so a future tidy-up does not "make it
    consistent" by reading the raw field here too."""
    assert "q.trial_active&&q.trial_ends_at?('trial ends '" in html
    if (BACKEND / "routers" / "account.py").exists():
        backend = (BACKEND / "routers" / "account.py").read_text(encoding="utf-8")
        fn = backend[backend.index("trial_active = bool("):]
        assert '== "free"' in fn[:260], "the server stopped gating trial_active"
