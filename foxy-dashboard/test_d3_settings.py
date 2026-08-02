"""D3 · Settings reorganised into seven disclosure sections, plus the meter.

Three things here are one careless edit from regressing invisibly:

* a card dropped during a future reorganise takes a real control with it, and
  nothing on screen says so — hence the per-card placement map below;
* the `<details>` traps (Safari's own marker, the summary focus ring) fail
  silently in exactly one browser or for exactly one input method;
* the strength meter is allowed to advise and forbidden to block, and "add a
  disabled attribute" is the most natural-looking change anyone could make to it.
"""

from __future__ import annotations

import re

import pytest

from test_p1_contrast import HTML


@pytest.fixture(scope="module")
def html() -> str:
    return HTML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def settings(html: str) -> str:
    """Just #page-settings, so a match elsewhere on the page cannot pass a test
    about this one."""
    # Settings is the LAST .page in the file, so there is no next one to stop at.
    i = html.index('<div class="page" id="page-settings"')
    return html[i:html.index("</main>", i)]


# ── item 2 · the seven sections, and every card still in one of them ─────────

SECTIONS = [
    ("sgrp-account", "Your account"),
    ("sgrp-team", "Team &amp; access"),
    ("sgrp-policy", "Policy &amp; grading"),
    ("sgrp-connect", "Connect"),
    ("sgrp-share", "Share &amp; verify"),
    ("sgrp-data", "Data &amp; privacy"),
    ("sgrp-help", "Help &amp; support"),
]


def test_there_are_seven_sections_in_the_settled_order(settings):
    found = re.findall(r'<details class="sgrp" id="([\w-]+)"', settings)
    assert found == [i for i, _ in SECTIONS], f"section order changed: {found}"


def test_each_section_says_what_it_is(settings):
    for n, (ident, title) in enumerate(SECTIONS, 1):
        block = settings[settings.index(f'id="{ident}"'):]
        head = block[:block.index("</summary>")]
        assert f'<span class="sgrp-n" aria-hidden="true">{n}</span>' in head, (
            f"{ident} lost its position number")
        # §1's heading also carries the id test_p6c_avatar_ui anchors on.
        assert re.search(r"<h2(?: id=\"[\w-]+\")?>" + re.escape(title) + r"</h2>",
                         head), f"{ident} lost its title"
        assert 'class="sgrp-sub"' in head, f"{ident} lost its subtitle"


# Where each card has to end up. The failure this prevents is a reorganise that
# loses one: the count on the row still renders, the page still looks whole, and
# a control the user needs is simply gone.
PLACEMENT = {
    "sgrp-account": ['id="setName"', 'id="mfaState"', 'id="devicesList"',
                     'id="loginHistOrgBtn"', 'id="auditCard"', 'id="prefNotifyBreach"'],
    "sgrp-team": ['id="teamCard"', 'id="ipAllowInput"', 'id="ssoCard"'],
    "sgrp-policy": ['id="polMaxTokens"', 'id="polSdkSeg"', 'id="polEnforcement"',
                    'id="polNotify"', 'id="setPolicyStatus"'],
    "sgrp-connect": ['id="sdkCheckOut"', "Desktop app", 'id="webhookCard"'],
    "sgrp-share": ['id="badgeOut"'],
    "sgrp-data": ['id="prefHideSensitive"', "deleteWorkspace"],
    "sgrp-help": ["Help &amp; support"],
}


@pytest.mark.parametrize("ident,needles",
                         [(k, v) for k, v in PLACEMENT.items()])
def test_every_card_is_in_its_section(settings, ident, needles):
    start = settings.index(f'id="{ident}"')
    nxt = [settings.index(f'id="{i}"') for i, _ in SECTIONS
           if settings.index(f'id="{i}"') > start]
    block = settings[start:min(nxt) if nxt else len(settings)]
    for needle in needles:
        assert needle in block, f"{needle} is no longer inside {ident}"


def test_all_twenty_one_cards_survived(settings):
    """A reorganise, not a cull. 21 is what the page had after D2 removed the
    two explainer cards — the plan's table said 18 and was three short."""
    assert settings.count('<div class="sgrp-b">') == 7
    # Top-level cards only: every one of them is indented to the same column by
    # the reorganise, while the sub-cards nested inside them are deeper.
    n = len(re.findall(r'\n          <div class="clay pad', settings))
    assert n == 21, f"the page now carries {n} top-level cards, not 21"


def test_the_judge_save_bar_is_not_counted_as_a_setting(settings):
    """It is the section's submit row. Counting it claims five settings where
    four exist."""
    assert '<div class="clay pad" data-nocount' in settings
    # Bounded by the NEXT section, not by </details> — the judge cards contain
    # nested <details class="polgroup">, so the first close tag is not the
    # section's own.
    start = settings.index('id="sgrp-policy"')
    end = settings.index('id="sgrp-connect"')
    assert start < settings.index("data-nocount") < end


def test_the_count_is_measured_not_written(html):
    """Two cards are hidden until the admin check reveals them. A number typed
    into the markup tells a member the section holds three settings when they
    can reach one."""
    assert "window.foxCountSettings=function()" in html
    assert "' setting':' settings'" in html.replace('"', "'")
    assert not re.search(r'class="sgrp-c"[^>]*>\s*\d', html), (
        "a settings count was hardcoded into the summary row")


# ── item 2c · the disclosure behaviours ─────────────────────────────────────

def test_the_sections_are_native_disclosures(settings):
    """Not a JS accordion. Keyboard operation, the open state exposed to
    assistive tech, and find-in-page reaching inside a closed section are all
    free here and none of them survives being hand-rolled."""
    assert settings.count("<details class=\"sgrp\"") == 7
    assert settings.count('<summary class="sgrp-h">') == 7


def test_safaris_own_triangle_is_suppressed_both_ways(html):
    """Safari draws it from ::-webkit-details-marker and ignores list-style;
    Firefox uses ::marker. Miss one and that browser grows a second arrow."""
    assert ".sgrp-h::-webkit-details-marker{display:none;}" in html
    assert '.sgrp-h::marker{content:"";}' in html
    assert re.search(r"\.sgrp-h\{[^}]*list-style:none", html)


def test_the_summary_row_has_a_focus_ring(html):
    """The row IS the button, and it is the only control that opens a section."""
    assert ".sgrp-h:focus-visible{outline:2px solid var(--fox);outline-offset:-2px;}" in html


def test_the_last_card_does_not_leave_a_dead_gap(html):
    assert ".sgrp-b > :last-child{margin-bottom:0!important;}" in html


def test_only_the_first_section_greets_you_open(settings):
    opens = re.findall(r'<details class="sgrp" id="([\w-]+)" open>', settings)
    assert opens == ["sgrp-account"], f"sections open by default: {opens}"


def test_the_chevron_respects_reduced_motion(html):
    """In the file's ONE canonical reduced-motion block, not a second one of its
    own. test_p1_contrast reads the first such block in the file as the register
    of everything that moves — declaring an earlier one silently makes it the
    register and stops every looping animation from being checked. That is the
    exact regression this pair of rules caused before it was moved."""
    assert ".sgrp[open] .sgrp-v{transform:rotate(180deg);}" in html
    blocks = re.findall(r"@media\(prefers-reduced-motion:reduce\)\{(.*?)\n\}", html, re.S)
    assert blocks, "the canonical reduced-motion block is gone"
    assert ".sgrp-v{transition:none;}" in blocks[0]
    assert ".pwm-seg{transition:none;}" in blocks[0]


def test_the_count_never_uses_the_disabled_only_ink(html):
    """--muted2 is under AA by design (3.01:1 dark, 2.71:1 light on --bg2) and
    test_p1_contrast names pointing live text at it as the thing to prevent.
    The plan's reference CSS did exactly that."""
    rule = re.search(r"\.sgrp-c\{([^}]*)\}", html)
    assert rule, "the count rule vanished"
    assert "var(--muted2)" not in rule.group(1)
    assert "color:var(--muted)" in rule.group(1)


# ── item 2b · the relabel, all the way through the flow ─────────────────────

def test_the_button_adds_a_teammate(settings):
    assert "+ add teammate" in settings
    assert "add auditor" not in settings


def test_the_dialog_and_the_toast_agree_with_the_button(html):
    """A renamed button that opens a dialog still saying "auditor" is worse than
    not renaming it."""
    assert "auditor@firm.com" not in html
    assert "Auditor added" not in html
    assert "toast('Teammate added')" in html


def test_the_word_auditor_survives_where_it_is_correct(html):
    """It is still the product's word for the person reading your evidence. The
    relabel was about the person you invite, not about the role."""
    assert "Auditor sign-in" in html
    assert "your auditor can verify" in html


def test_the_team_list_has_an_empty_state(html):
    """It rendered an empty box for "no teammates", "not allowed" and "server
    down" alike — three different situations, one blank rectangle."""
    assert "No teammates yet — add one to share this workspace." in html
    assert "Could not load the team (" in html


# ── item 4 · the meter advises and never blocks ─────────────────────────────

def test_the_meter_sits_between_the_confirm_field_and_the_status_line(settings):
    a = settings.index('id="setNewPw2"')
    b = settings.index('id="setPwMeter"')
    c = settings.index('id="setPwStatus"')
    assert a < b < c, "the meter moved out of its place in the form"


def test_the_meter_is_wired_to_the_new_password_field(settings, html):
    assert 'oninput="window._setPwMeter&amp;&amp;window._setPwMeter()"' in settings
    assert "window._setPwMeter=function()" in html


def test_the_meter_never_blocks_the_submit(html):
    """It advises. The 8-character minimum is the server's and it is the only
    hard rule — no disabled submit, no refusal."""
    fn = html[html.index("window._setPwMeter=function()"):]
    fn = fn[:fn.index("window.changePassword")]
    for forbidden in ("disabled", "preventDefault", "return false"):
        assert forbidden not in fn, (
            f"the meter now does {forbidden!r} — it is only allowed to advise")


def test_the_meter_names_a_lever_rather_than_a_verdict(html):
    assert "length helps most — 12+ characters, or add a symbol" in html
    assert "at least 8 characters" in html


def test_the_live_region_only_speaks_when_the_bucket_changes(html):
    """A polite region that fires on every keystroke is unusable with a screen
    reader on."""
    assert "if(s!==_pwLastScore)" in html
    assert 'id="setPwMeterLab" role="status" aria-live="polite"' in html


def test_the_port_did_not_bring_the_consoles_duration_token(html):
    """foxy-adminpage writes `transition:background var(--dur)`. This file has
    no --dur, so a straight copy would have made the segments jump."""
    # Comments stripped: the CSS comment above .pwm names the trap by quoting it.
    code = re.sub(r"/\*.*?\*/|<!--.*?-->", "", html, flags=re.S)
    assert "var(--dur)" not in code, "a --dur reference this file cannot resolve"
    assert ".pwm-seg{height:4px;flex:1;border-radius:2px;background:var(--line);" in html


def test_the_confirm_field_and_the_reuse_check_were_not_rebuilt(html):
    """Both already existed here — the console copied them FROM this file."""
    assert "The two new passwords do not match" in html
    assert html.count('id="setNewPw2"') == 1                 # one field, not two
    assert html.count('data-pw-toggle="setNewPw2"') == 1     # with its own reveal
    assert "nw.value!==nw2.value" in html                    # and the check intact
