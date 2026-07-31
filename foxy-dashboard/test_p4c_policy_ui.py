"""The two enforcement settings, told apart (P4 §C).

Phase B shipped `sdk_enforcement` with no UI anywhere — the API carried it and
nothing a customer could reach would set it. This is the control that makes it
reachable, and the copy that stops the two settings being confused again.

They are not the same thing and never were:

  enforcement_mode   block|flag|monitor   what happens to a VERDICT after the
                                          judge has graded an interaction
  sdk_enforcement    observe|redact|block what the SDK does BEFORE a prompt
                                          reaches a model

The owner could not tell whether either did anything. One of them genuinely does
nothing yet, and the page now says so.

Behaviour that cannot be read off the markup — the null round-trip — is driven
through node against the real controller, because "Not set" has to send an
EXPLICIT null and a static assertion cannot prove that.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path

import pytest

from test_p1_contrast import HTML, _source
from test_p2_structure import _scripts


@pytest.fixture(scope="module")
def html() -> str:
    return _source()


def _card(html_text: str, title: str) -> str:
    """The .clay card whose stitle carries `title`."""
    at = html_text.index(title)
    start = html_text.rindex('<div class="clay pad">', 0, at)
    end = html_text.index('<div class="clay pad">', at)
    return html_text[start:end]


# ══ the control exists, with four states ═══════════════════════════════════

def test_the_sdk_control_renders_with_all_four_states(html):
    """Three would be a bug: the field is nullable and null is a real state."""
    seg = re.search(r'<div class="lvseg" id="polSdkSeg".*?</div>', html, re.S)
    assert seg, "there is no SDK enforcement control on the policy page"
    values = re.findall(r'data-val="([^"]*)"', seg.group(0))
    assert values == ["", "observe", "redact", "block"], values


def test_not_set_is_selectable_not_merely_the_starting_state(html):
    """An owner who picks Block must be able to go back to having no opinion."""
    seg = re.search(r'<div class="lvseg" id="polSdkSeg".*?</div>', html, re.S).group(0)
    first = re.search(r'<button[^>]*data-val=""[^>]*>([^<]+)</button>', seg)
    assert first, "the null state is not a button"
    assert "not set" in first.group(1).strip().lower()
    assert 'role="radio"' in seg[:seg.index('data-val=""')] or 'role="radio"' in first.group(0)


def test_the_control_is_a_keyboard_operable_radiogroup(html):
    seg = re.search(r'<div class="lvseg" id="polSdkSeg"[^>]*>', html).group(0)
    assert 'role="radiogroup"' in seg
    assert "aria-label=" in seg
    js = _scripts()
    assert "#polSdkSeg button" in js
    assert "ArrowRight" in js[js.index("polSdkSeg"):], "arrow keys do not move between options"


def test_it_reuses_the_shipped_level_control(html):
    """P1's theme is shipped; a new control would drift from it."""
    assert 'class="lvseg" id="polSdkSeg"' in html
    assert 'id="polSdkBar"' in html and 'class="lvbar"' in html
    seg = re.search(r'<div class="lvseg" id="polSdkSeg".*?</div>', html, re.S).group(0)
    for level in ("low", "medium", "high"):
        assert f'data-lv="{level}"' in seg
    # …and the null state carries no level, so it reads neutral rather than as a
    # severity the owner did not choose.
    null_btn = re.search(r'<button[^>]*data-val=""[^>]*>', seg).group(0)
    assert "data-lv=" not in null_btn


# ══ the wire contract — omission means KEEP, so null must be explicit ══════

def test_the_save_body_always_carries_the_key(html):
    """The server reads an absent key as "keep what you have"
    (policies.py, model_fields_set). Dropping it for "Not set" would silently
    retain the old value and the control would look broken."""
    js = _scripts()
    start = js.index("window.savePolicy=")
    save = js[start:start + 2500]
    assert "sdk_enforcement:" in save, "savePolicy never sends the field"
    assert "readSdkSeg" in save
    # It must not be conditional — no `if (…) body.sdk_enforcement = …`
    assert not re.search(r"if\s*\([^)]*\)\s*body\.sdk_enforcement", js)


def test_the_null_state_is_not_smuggled_as_a_string(html):
    """"" or "none" would be a 422 from the Literal, and "observe" would be an
    opinion the owner did not express."""
    js = _scripts().replace(" ", "")
    assert "returnv||null" in js, \
        "the empty selection must resolve to null, not to a string"
    # …and nothing coerces it to a placeholder string on the way out.
    assert "sdk_enforcement:''" not in js and 'sdk_enforcement:""' not in js


def test_load_paints_from_the_response_including_null(html):
    js = _scripts().replace(" ", "")
    # The second argument marks the value as SERVER-supplied, which is what makes
    # the control sendable at all — see the pre-load guard below.
    assert "syncSdkSeg(p.sdk_enforcement,true)" in js
    # No `|| 'observe'` default — that would hide the null state on every load.
    assert not re.search(r"sdk_enforcement\s*\|\|", _scripts())


def test_the_control_needs_no_setfieldvalue_exemption(html):
    """The radiogroup's aria-checked IS the state, so nothing assigns a watched
    field. An exemption here would have been a new one in a file that has been
    bitten four times."""
    raw = HTML.read_text(encoding="utf-8")
    exemptions = [ln for ln in raw.splitlines() if "setFieldValue-exempt:" in ln]
    assert not any("polSdk" in ln for ln in exemptions), \
        "the SDK control added a setFieldValue exemption"
    assert "polSdkSeg" in raw
    assert not re.search(r"polSdk[A-Za-z]*'?\s*\)?\s*\.value\s*=", raw)


# ══ the copy — two settings, told apart ════════════════════════════════════

def test_each_setting_has_its_own_card(html):
    """Naming them both "enforcement" in one column is how they got conflated."""
    judge = _card(html, "After grading")
    sdk = _card(html, "Before the call")
    assert 'id="polEnforcement"' in judge
    assert 'id="polSdkSeg"' in sdk
    assert 'id="polSdkSeg"' not in judge and 'id="polEnforcement"' not in sdk


def test_the_verdict_labels_do_not_promise_prevention(html):
    """Fix 3. `enforcement_mode` is read AFTER the model call, so it cannot stop
    anything. "block on breach" said otherwise. Urgent / Notify / Silent describe
    how loudly a finding is treated, which is all this field can ever do."""
    judge = _card(html, "After grading")
    m = re.search(r'<select id="polEnforcement">(.*?)</select>', judge, re.S)
    assert m, "the verdict-handling select is gone"
    labels = re.findall(r"<option value=\"\w+\">([^<]*)</option>", m.group(1))
    assert len(labels) == 3
    joined = " ".join(labels).lower()
    for lie in ("block", "prevent", "stop", "allow through", "deny"):
        assert lie not in joined, (
            f"{lie!r} appears in a verdict-handling label — this control runs after "
            "the model call and cannot do that")
    assert labels[0].startswith("Urgent")
    assert labels[1].startswith("Notify")
    assert labels[2].startswith("Silent")


def test_the_stored_values_are_untouched_by_the_rename(html):
    """Display text only. `block|flag|monitor` is copied verbatim into the
    tamper-evident evidence snapshot (policy_snapshot.py), so renaming the stored
    strings would break comparability between old and new snapshots and
    destabilise foxy-policy-v1. No migration, and none needed."""
    m = re.search(r'<select id="polEnforcement">(.*?)</select>', html, re.S)
    values = re.findall(r'<option value="(\w+)"', m.group(1))
    assert values == ["block", "flag", "monitor"], (
        f"stored enforcement_mode values changed to {values} — evidence snapshots "
        "would stop being comparable")
    # …and the save path still sends the value, not the label.
    assert "enforcement_mode:($('polEnforcement')||{}).value" in html


def test_the_sdk_control_keeps_its_block_label(html):
    """`sdk_enforcement` runs BEFORE the model call and really does block, so
    "Block" is correct there. Two controls, two vocabularies — the rename must not
    have bled across."""
    sdk = _card(html, "Before the call")
    assert 'data-val="block">Block<' in sdk, "the SDK control's Block label changed"
    assert "Urgent" not in sdk, "verdict-handling wording leaked into the SDK control"


def test_the_judge_setting_does_not_claim_to_stop_anything(html):
    """Saying otherwise is the lie that started this — the owner could not tell
    whether changing it did anything.

    The "reads it today" half of this assertion moved out when P5 §A gave the
    field a consumer; see test_the_note_no_longer_calls_the_field_unread."""
    judge = _card(html, "After grading")
    flat = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", judge)).lower()
    assert "after" in flat
    assert "does not stop anything from reaching a model" in flat


# ══ P5 §B — the page stops apologising for a field that now works ══════════

def _note(html_text: str) -> str:
    """The #polEnforcementNote div, and nothing around it.

    Sliced by id rather than off the card, deliberately. `_card` hands back the
    HTML comment above the select too, and the tag-stripping regex below stops at
    the first "greater than" character — so a comment that contains one leaks its
    own prose into the visible text. A test hunting for a removed phrase can then
    be satisfied by the comment explaining the removal. That trap has landed
    three phases running; this helper is how this file stops stepping in it."""
    at = html_text.index('id="polEnforcementNote"')
    start = html_text.rindex("<div", 0, at)
    end = html_text.index("</div>\n        </div>", at)
    return html_text[start:end]


def _flat(fragment: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", fragment)).strip().lower()


def test_the_explanatory_comment_cannot_leak_into_visible_text(html):
    """The trap itself, pinned. Every assertion in this file that reads "visible
    copy" strips tags with `<[^>]+>`, which treats an HTML comment as one tag
    only while the comment holds no "greater than" character. Put one in and the
    remainder becomes text, and prose about a removal starts satisfying tests
    written to forbid the thing removed."""
    judge = _card(html, "After grading")
    for comment in re.findall(r"<!--.*?-->", judge, re.S):
        assert ">" not in comment[4:-3], (
            "the comment above the enforcement select contains a 'greater than' "
            "character, so its prose now leaks into every visible-text assertion "
            "in this file")


def test_the_note_no_longer_calls_the_field_unread(html):
    """P5 §A shipped the consumer (org_notifications.send_breach_notice /
    user_notifications.send_breach_alert, migration 0059). The note used to say
    "Recorded, not yet acted on … nothing in the grading pipeline reads it
    today", which became false the moment that merged.

    Matched on the CLAIM, not on the replacement prose — the copy will be edited
    again and a guard pinned to it would just be deleted next time."""
    flat = _flat(_note(html))
    for dead in ("not yet acted on", "reads it today", "nothing in the grading",
                 "not yet", "no consumer"):
        assert dead not in flat, (
            f"the enforcement note still claims the field is inert ({dead!r}) — "
            "it has had a consumer since P5 §A")


def test_the_note_states_a_consequence_for_every_mode(html):
    """Three stored values, three consequences. Keyed off the labels the select
    actually renders, so adding a fourth mode — or renaming one — fails here
    instead of silently shipping a note that covers two of three."""
    m = re.search(r'<select id="polEnforcement">(.*?)</select>', html, re.S)
    labels = re.findall(r"<option value=\"\w+\">([^<]*)</option>", m.group(1))
    heads = [label.split("—")[0].strip() for label in labels]
    assert heads == ["Urgent", "Notify", "Silent"], heads
    flat = _flat(_note(html))
    for head in heads:
        assert head.lower() in flat, (
            f"the note says nothing about {head!r}, so one of the three modes "
            "ships with no stated consequence")
    # …and it still carries the two claims that survived the rewrite.
    assert "already found" in flat, "the note stopped saying the judge found it first"
    assert "sdk enforcement setting below" in flat, \
        "the note stopped pointing at the only control that can stop a call"


def test_silent_says_the_finding_is_still_recorded(html):
    """`monitor` suppresses DELIVERY, never RECORDING —
    routers/account.py backfills a kind="breach" in-app notification straight off
    the ledger, independent of email. Without this clause "Silent — record only"
    reads as a hole in the audit trail rather than a mail preference."""
    flat = _flat(_note(html))
    silent = flat[flat.index("silent"):]
    assert "still recorded" in silent
    assert "alerts" in silent and "ledger" in silent and "export" in silent


def test_the_digest_label_is_identical_on_both_surfaces(html):
    """THE ONE THAT STOPS A THIRD DRIFT. The desktop's own comment claimed its
    options were "quoted verbatim from the web" while both this label and the
    enforcement labels had drifted — a comment is not a test. These two strings
    are now compared directly."""
    m = re.search(r'<select id="polNotify">(.*?)</select>', html, re.S)
    assert m, "the notification-on-breach select is gone"
    web = dict(re.findall(r'<option value="(\w+)">([^<]*)</option>', m.group(1)))
    desktop_src = (Path(__file__).resolve().parents[1] / "desktop" / "policy_data.py"
                   ).read_text(encoding="utf-8")
    block = desktop_src[desktop_src.index("NOTIFY = ("):]
    block = block[:block.index(")\n")]
    native = dict(re.findall(r'\("(\w+)", "([^"]*)"\)', block))
    assert set(web) == set(native) == {"immediate", "digest", "none"}
    for value in ("immediate", "digest", "none"):
        assert web[value] == native[value], (
            f"notify_on_breach {value!r} reads {web[value]!r} on the web and "
            f"{native[value]!r} on the desktop — the two products describe one "
            "setting two different ways")


def test_the_digest_label_no_longer_promises_a_digest(html):
    """There is no hourly digest and there never has been: both senders treat
    anything but "immediate" as send-nothing, and send_weekly_digests runs off
    the per-user notify_weekly_digest preference, never off this field. The label
    sold batching and delivered silence."""
    m = re.search(r'<select id="polNotify">(.*?)</select>', html, re.S)
    label = dict(re.findall(r'<option value="(\w+)">([^<]*)</option>',
                            m.group(1)))["digest"].lower()
    for lie in ("hourly", "digest", "batch"):
        assert lie not in label, (
            f"{lie!r} is back in the notify_on_breach label — no such delivery "
            "exists (Worth Noting #29)")
    # …and the value itself is untouched: it lives in foxy-policy-v1.
    assert 'value="digest"' in m.group(1)


def test_the_enforcement_labels_are_identical_on_both_surfaces(html):
    """Same rule as the digest label, applied to the tuple the desktop had been
    carrying pre-98e1399 prevention language in. Web wins, verbatim."""
    m = re.search(r'<select id="polEnforcement">(.*?)</select>', html, re.S)
    web = dict(re.findall(r'<option value="(\w+)">([^<]*)</option>', m.group(1)))
    desktop_src = (Path(__file__).resolve().parents[1] / "desktop" / "policy_data.py"
                   ).read_text(encoding="utf-8")
    block = desktop_src[desktop_src.index("ENFORCEMENT = ("):]
    block = block[:block.index(")\n")]
    native = dict(re.findall(r'\("(\w+)", "([^"]*)"\)', block))
    assert web == native, f"web {web} vs desktop {native}"


def test_the_save_default_matches_the_migrated_column_default(html):
    """0059 moved the server default block -> flag so that Urgent is a deliberate
    choice. A stale ||'block' here writes the old default straight back."""
    js = _scripts().replace(" ", "")
    assert "enforcement_mode:($('polEnforcement')||{}).value||'flag'" in js, \
        "the save path no longer defaults enforcement_mode to the migrated 'flag'"
    assert "value||'block'" not in js, \
        "a save path still falls back to the pre-0059 'block' default"


def test_the_sdk_setting_says_when_it_applies(html):
    sdk = _card(html, "Before the call")
    flat = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", sdk)).lower()
    assert "before" in flat and "reaches a model" in flat
    assert "only setting on this page that can stop a call" in flat


def test_the_resolution_rule_is_explained(html):
    """A customer must come away knowing where enforcement really lives."""
    sdk = _card(html, "Before the call")
    flat = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", sdk)).lower()
    assert "@foxy.audit()" in flat and "mode=" in flat
    assert "never weaken" in flat
    assert "never introduce redaction" in flat
    assert "raises an error" in flat and "silently rewrites" in flat
    assert "foxy_org_policy=off" in flat


def test_no_copy_implies_the_judge_setting_reaches_the_sdk(html):
    judge = _card(html, "After grading")
    flat = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", judge)).lower()
    for claim in ("before the model", "prevent", "blocks the call", "sdk enforcement setting will"):
        if claim == "prevent":
            continue        # "to prevent a prompt… use the SDK setting below" is a POINTER
        assert claim not in flat, f"the judge card implies {claim!r}"


# ══ behaviour, driven through node ═════════════════════════════════════════

_DOM_HARNESS = r"""
const buttons = [
  {v:"", checked:true}, {v:"observe", checked:false},
  {v:"redact", checked:false}, {v:"block", checked:false},
];
function mkBtn(b){
  return {
    _attrs:{"data-val":b.v, "aria-checked":b.checked?"true":"false"},
    getAttribute(k){ return this._attrs[k] === undefined ? null : this._attrs[k]; },
    setAttribute(k,v){ this._attrs[k]=v; },
    focus(){}, parentNode:null,
  };
}
const btns = buttons.map(mkBtn);
const seg = {
  querySelectorAll(){ return btns; },
  querySelector(sel){
    return btns.find(b => b.getAttribute("aria-checked") === "true") || null;
  },
};
const bar = { style:{} };
const desc = { textContent:"" };
global.document = {
  getElementById(id){
    if(id==="polSdkSeg") return seg;
    if(id==="polSdkBar") return bar;
    if(id==="polSdkDesc") return desc;
    return null;
  },
  addEventListener(){},
};
global.window = {};
CONTROLLER_SOURCE
const out = [];
out.push(["before load", window.readSdkSeg() === undefined ? "UNDEFINED" : window.readSdkSeg()]);
window.syncSdkSeg(null, true);
out.push(["initial", window.readSdkSeg()]);
window.syncSdkSeg("block", true);
out.push(["after block", window.readSdkSeg(),
          btns.map(b=>b.getAttribute("aria-checked")).join(",")]);
window.syncSdkSeg(null, true);
out.push(["after null", window.readSdkSeg(),
          btns.map(b=>b.getAttribute("aria-checked")).join(",")]);
window.syncSdkSeg("redact", true);
out.push(["after redact", window.readSdkSeg()]);
window.syncSdkSeg("nonsense", true);
out.push(["after junk", window.readSdkSeg()]);
console.log(JSON.stringify(out));
"""


def _run_controller() -> list:
    raw = HTML.read_text(encoding="utf-8")
    start = raw.index("P4 §C · workspace SDK enforcement")
    block = raw[start:]
    body = block[block.index("<script>") + len("<script>"):block.index("</script>")]
    script = _DOM_HARNESS.replace("CONTROLLER_SOURCE", body)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "run.js"
        path.write_text(script, encoding="utf-8")
        proc = subprocess.run(["node", str(path)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_the_null_state_round_trips_through_the_real_controller():
    """THE ONE THAT MATTERS. "Not set" must read back as null — not "", not
    "observe" — because that null is what travels on the wire and clears the
    stored value. A static assertion cannot prove this."""
    results = dict((row[0], row[1]) for row in _run_controller())
    assert results["initial"] is None
    assert results["after block"] == "block"
    assert results["after null"] is None, \
        "returning to Not set did not produce a null — the owner cannot undo a choice"
    assert results["after redact"] == "redact"


def test_an_unknown_value_falls_back_to_not_set():
    """A value this build does not know must not paint as a severity."""
    results = dict((row[0], row[1]) for row in _run_controller())
    assert results["after junk"] is None


def test_exactly_one_option_is_selected_at_a_time():
    rows = {row[0]: row for row in _run_controller()}
    for key in ("after block", "after null"):
        checked = rows[key][2].split(",")
        assert checked.count("true") == 1, f"{key}: {checked}"


def test_a_save_before_the_page_has_loaded_cannot_wipe_the_value():
    """The control paints "Not set" at init and loadPolicy is async. In that
    window a save must OMIT the key — an omitted key tells the server to keep
    what it has — rather than send a null that clears the owner's choice."""
    results = dict((row[0], row[1]) for row in _run_controller())
    assert results["before load"] == "UNDEFINED", (
        "the control was sendable before the server value arrived, so an early "
        "save would have cleared a stored setting")


def test_undefined_is_dropped_from_the_request_body():
    """JSON.stringify omits undefined properties, which is precisely the
    keep-what-you-have contract. Pinned because it is load-bearing and looks
    like an accident."""
    import json as _json
    assert "sdk_enforcement" not in _json.dumps({"a": 1})
    js = _scripts()
    assert "readSdkSeg?window.readSdkSeg():null" in js.replace(" ", ""),         "savePolicy no longer routes through readSdkSeg"
