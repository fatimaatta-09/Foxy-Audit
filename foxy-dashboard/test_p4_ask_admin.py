"""P4 · #56 — the member path: an honest empty state, and a door.

Buying a plan is admin-only, and this page's own rule is that a control which
cannot work is worse than no control. Together those left a member with nothing:
the two screens that offer plans could only answer a 403 with the sentence "ask
an admin on your team", which is an instruction rather than a way to do it.

Two things here are one plausible edit from being wrong on screen and right in
the diff:

* **the member must not be handed a purchase button.** The check is the
  server's `can_purchase`, not a role read locally — a local read is a second
  copy of `require_role("admin")` and copies drift.
* **"sent" must come off the row, not out of a variable.** A flag in this file
  would survive until the tab closed and then forget, on the one control whose
  whole job is telling somebody where their request stands.
"""

from __future__ import annotations

import re

import pytest

from test_p1_contrast import HTML

BACKEND = HTML.parent.parent / "backend" / "app" / "routers" / "billing.py"


@pytest.fixture(scope="module")
def html() -> str:
    return HTML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def askjs(html: str) -> str:
    """Just `window.foxAskAdmin`, so a match elsewhere in a 7,000-line file
    cannot pass a test about this helper."""
    i = html.index("window.foxAskAdmin={")
    return html[i:html.index("\n  function banner(", i)]


@pytest.fixture(scope="module")
def chooser(html: str) -> str:
    i = html.index("function chooser(btn,say){")
    return html[i:html.index("\n  function banner(", i)]


@pytest.fixture(scope="module")
def upgradejs(html: str) -> str:
    i = html.index("window.loadUpgrade=async function(){")
    return html[i:html.index("\n  document.addEventListener", i)]


# ── the member is never handed a purchase ───────────────────────────────────
def test_both_screens_decide_from_the_servers_answer(chooser, upgradejs):
    """`can_purchase` is `require_role("admin")` asked over the wire. A role
    read in this file would be a second copy of the rule the route enforces, and
    the two would drift the first time either changed."""
    for name, js in (("chooser", chooser), ("loadUpgrade", upgradejs)):
        assert "can_purchase===false" in js, f"{name} does not consult can_purchase"
        assert "foxAskAdmin.load()" in js, f"{name} never asks who is reading"
        assert "_isAdmin" not in js, f"{name} re-derives the role locally"
        assert "me.role" not in js and "role==='admin'" not in js, name


def test_the_member_branch_draws_no_control_that_starts_a_checkout(
        chooser, upgradejs):
    """The regression that looks like a tidy-up: reuse the admin renderer and
    just hide the button with CSS. `data-plan` is what both click handlers key
    off, so a member's card carrying one is a 403 waiting to be pressed."""
    for name, js, end in (("chooser", chooser, "if(!list.length){"),
                          ("loadUpgrade", upgradejs, "if(!plans.length){")):
        branch = js[js.index("can_purchase===false"):js.index(end)]
        assert "data-plan" not in branch, f"{name} gives a member a buy button"
        assert "upgrade-session" not in branch, f"{name} offers the money route"
        assert "foxAskAdmin.render" in branch, f"{name} offers nothing instead"


def test_the_only_endpoint_this_helper_touches_is_the_ask(askjs):
    paths = set(re.findall(r"'(/v1/[a-z0-9/_-]+)'", askjs))
    assert paths == {"/v1/billing/upgrade-request"}, paths


def test_the_ask_route_is_the_one_the_backend_serves(askjs):
    """Pinned to the router. A rename on either side turns this red instead of
    turning the button into a silent 404."""
    backend = BACKEND.read_text(encoding="utf-8")
    assert '@router.post("/v1/billing/upgrade-request")' in backend
    assert '@router.get("/v1/billing/upgrade-request")' in backend
    assert "/v1/billing/upgrade-request" in askjs


def test_the_new_kind_has_an_explainer_and_it_is_keyed_on_the_backends_string(
        html: str):
    """`KIND_HELP` is what a customer reads to find out what an alert means, and
    an unknown kind falls through to `||null` — no explainer, silently. So the
    key is pinned to the constant the writer uses rather than typed twice."""
    un = (HTML.parent.parent / "backend" / "app" / "user_notifications.py"
          ).read_text(encoding="utf-8")
    kind = re.search(r'UPGRADE_REQUEST_KIND = "([a-z_]+)"', un).group(1)
    block = html[html.index("var KIND_HELP={"):html.index("var LEVEL_WORD=")]
    assert kind + ":{" in block, f"no KIND_HELP entry for {kind!r}"
    entry = block[block.index(kind + ":{"):]
    entry = entry[:entry.index("}")]
    assert "go:'billing'" in entry, "the explainer sends nobody anywhere"
    assert "Nothing has been bought" in entry, (
        "an admin must be told this is a message, not a charge")


# ── "sent" is a fact on the server ──────────────────────────────────────────
def test_the_sent_state_is_read_from_requested_at(askjs):
    """Not a local flag. A new tab, a refresh, or a second colleague must all
    see the same answer, and only the row can give them that."""
    assert "st.requested_at" in askjs and "requested_at" in askjs
    assert re.search(r"var asked=!!\(st&&st\.requested_at\)", askjs), (
        "the sent state is no longer derived from the server's field")
    # A flag set on click and read on render would be the failure mode.
    assert not re.search(r"\b(sent|asked|done)\s*=\s*(true|1)\s*;", askjs)


def test_a_successful_send_redraws_from_the_response(askjs):
    """The POST answers with the same shape as the GET, so the confirmation is
    the server's timestamp — never `Date.now()` stamped here, which would drift
    from what a refresh then shows."""
    click = askjs[askjs.index("btn.onclick"):]
    assert "draw(d,true)" in click, "the response is not what redraws the block"
    assert "Date.now()" not in click, "the confirmation invents its own moment"


def test_the_change_is_announced_only_when_it_changes(askjs):
    """`role=status` on first paint reads the whole block out on every load."""
    assert "if(announce)box.setAttribute('role','status')" in askjs
    assert "draw(state,false)" in askjs, "the first paint announces itself"
    assert "draw(d,true)" in askjs, "the change after a send is silent"


# ── the copy ────────────────────────────────────────────────────────────────
def test_the_empty_state_names_the_reason_and_offers_the_action(askjs):
    """An empty screen is an invitation to act — the title says why they cannot
    do it, the body says what it costs the workspace, and the button says
    exactly what pressing it does."""
    assert "Only an admin can buy a plan" in askjs
    assert "spends money for the whole workspace" in askjs
    assert "Notify the admins" in askjs


def test_the_copy_promises_no_timeframe_and_apologises_for_nothing(askjs):
    """Nobody here knows when an admin next opens the dashboard, and a member
    asking for a bigger plan has done nothing that needs an apology.

    Scanned over the CODE with its comments stripped, not over string literals
    pulled out by a regex: pairing quotes across JavaScript with `'([^']+)'`
    silently desynchronises — after the first literal it starts capturing the
    code between literals instead, so the copy it thinks it is reading is not
    the copy at all. This test passed against both those defects before the
    mutation run showed it was measuring nothing.
    """
    code = re.sub(r"/\*.*?\*/", " ", askjs, flags=re.S)      # not our own prose
    code = re.sub(r"^\s*//.*$", " ", code, flags=re.M).lower()
    assert "spends money for the whole workspace" in code, "the copy moved"
    for word in ("sorry", "apolog", "unfortunately", "shortly", " soon",
                 "within 24", "get back to you", "as soon as possible"):
        assert word not in code, f"the copy says {word!r}"
    for word in ("not allowed", "denied", "forbidden", "you cannot",
                 "no permission", "insufficient", "you are not"):
        assert word not in code, f"the copy tells a colleague off: {word!r}"


def test_the_sent_state_reads_true_for_a_colleague_who_did_not_send_it(askjs):
    """It is org-wide: a second member opening the page finds it already asked.
    Copy written as "you asked" would be false for exactly that person."""
    assert "The admins have been told" in askjs
    sent = askjs[askjs.index("var title=asked?"):askjs.index("var act=asked")]
    assert "You asked" not in sent and "you asked" not in sent
    assert "Asked '+ago(" in askjs, "the confirmation does not say when"


def test_the_action_keeps_its_name_through_the_flow(askjs):
    """The button that says "Notify the admins" must not resolve into a state
    that calls it something else."""
    assert "'notifying…'" in askjs, "the in-flight label drifts from the action"
    assert "admins" in askjs[askjs.index("var title=asked?"):
                             askjs.index("var act=asked")].lower()


# ── failure has somewhere to go ─────────────────────────────────────────────
def test_a_failed_send_restores_the_button_and_says_why(askjs):
    click = askjs[askjs.index("btn.onclick"):]
    assert click.count("btn.disabled=false") == 2, (
        "a failure path leaves the control inert forever")
    assert click.count("btn.textContent=label") == 2
    assert "Could not reach the server" in click


def test_the_error_line_is_text_and_never_markup(askjs):
    """The only thing that reaches it is our own copy today, but this is the
    one place in the helper that builds a node at runtime — `textContent` keeps
    it that way if a server string is ever put through it."""
    err = askjs[askjs.index("function errLine("):askjs.index("function draw(")]
    assert "textContent" in err and "innerHTML" not in err


def test_every_server_value_that_becomes_markup_is_escaped(askjs, chooser):
    """`esc()` on both, because the tier list is the server's and so is the
    timestamp that feeds the sentence."""
    drawn = askjs[askjs.index("box.innerHTML=lock"):askjs.index("if(announce)")]
    assert drawn.count("esc(") == 4, "a value reaches innerHTML unescaped"
    member = chooser[chooser.index("can_purchase===false"):chooser.index("if(!list.length){")]
    assert "esc(p.tier)" in member and "esc(q)" in member


# ── it degrades ─────────────────────────────────────────────────────────────
def test_an_unreachable_ask_endpoint_does_not_break_either_screen(
        askjs, chooser, upgradejs):
    """`load()` answers null rather than throwing, and `can_purchase===false`
    is a strict test — so a failed lookup falls through to the admin path the
    route still guards, rather than blanking the page."""
    assert "return null" in askjs
    for js in (chooser, upgradejs):
        assert "can_purchase===false" in js and "can_purchase==false" not in js
