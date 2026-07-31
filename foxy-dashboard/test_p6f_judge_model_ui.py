"""P6f — the judge MODEL selects on the Policy page.

These guard the failures that still look like a working page:

  * a select that is never populated, or populated from a hardcoded list that
    outlives the server's,
  * a save body that omits the field, so un-pinning appears to do nothing,
  * `.value =` instead of setFieldValue, which skips the change event,
  * the loader marking the form dirty, so "✓ Saved" flips back instantly,
  * the superseded read-only readout left behind next to the new control.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

HTML = Path(__file__).with_name("foxy-audit-premium.html")


@pytest.fixture(scope="module")
def html() -> str:
    return HTML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def markup(html) -> str:
    """The file with HTML comments stripped.

    Structural scans must not match the prose that explains them — P6b and P6d
    both shipped a test that passed on its own comment.
    """
    return re.sub(r"<!--.*?-->", "", html, flags=re.S)


# ══ the controls exist ═════════════════════════════════════════════════════

@pytest.mark.parametrize("select_id", ["polGeminiModel", "polOpenaiModel"])
def test_each_provider_has_a_model_select(markup, select_id):
    assert f'<select id="{select_id}"></select>' in markup, (
        f"{select_id} must be an EMPTY select — its options come from the "
        f"server's judge_models_available, never from markup"
    )


@pytest.mark.parametrize("select_id", ["polGeminiModel", "polOpenaiModel"])
def test_each_select_has_a_visible_label_bound_to_it(markup, select_id):
    assert re.search(rf'<label class="flabel"[^>]*for="{select_id}"', markup), (
        f"{select_id} needs a real <label for=…> — a bare .flabel div names the "
        f"control on screen but not to a screen reader"
    )


@pytest.mark.parametrize("row_id", ["polGeminiModelRow", "polOpenaiModelRow"])
def test_each_select_sits_in_a_row_that_can_be_hidden(markup, row_id):
    assert f'id="{row_id}"' in markup, (
        f"{row_id} is what renderJudgeKeyFields shows and hides; without it the "
        f"OpenAI picker would sit on the page for a Gemini-only org"
    )


def test_the_superseded_readout_is_gone(markup):
    # §7.6's read-only line said which model WOULD grade you. The select now says
    # it and lets you change it; leaving both would show the same fact twice and
    # let them disagree.
    assert "polJudgeModels" not in markup, (
        "the old #polJudgeModels readout is still in the page"
    )


# ══ options come from the server ═══════════════════════════════════════════

def test_the_options_are_read_from_judge_models_available(markup):
    assert "judge_models_available" in markup, (
        "the page must populate the selects from the server's list"
    )


def test_no_model_id_is_hardcoded_in_the_markup(markup):
    # A model id baked into an <option> outlives the deployment that could serve
    # it, and the page would offer a model the worker refuses to call.
    body = markup[markup.index('id="polGeminiModelRow"'):]
    body = body[:body.index("</select>", body.index("polOpenaiModel"))]
    assert not re.search(r"gemini-\d|gpt-\d", body), (
        "a model id is hardcoded near the selects; the list is the server's"
    )


def test_the_pin_is_set_through_setFieldValue(markup):
    assert re.search(r"window\.setFieldValue\(sel,\s*pinned\)", markup), (
        "the model selects must be populated via setFieldValue — a bare "
        "`.value =` skips the change event the rest of the page listens for"
    )


# ══ the save body ══════════════════════════════════════════════════════════

@pytest.mark.parametrize("key", ["judge_gemini_model", "judge_openai_model"])
def test_the_save_body_always_carries_the_model(markup, key):
    assert re.search(rf"{key}:\(\(\$\('pol\w+Model'\)\|\|\{{\}}\)\.value\|\|''\)\|\|null",
                     markup), (
        f"{key} must be sent on every save, as an explicit null when unset — "
        f"omitting it makes un-pinning a model look like a no-op"
    )


# ══ loading must not look like editing ═════════════════════════════════════

def test_loading_the_form_does_not_mark_it_dirty(markup):
    assert "JUDGE.loading=true" in markup and "JUDGE.loading=false" in markup, (
        "setFieldValue fires a change event; without a loading guard the page "
        "would read '● Unsaved changes' the moment it opened"
    )
    assert re.search(r"if\(!JUDGE\.loading\)window\.markPolicyDirty\(\)", markup), (
        "the model selects' change handler must skip the loader's own event"
    )


def test_the_guard_is_released_even_if_population_throws(markup):
    # A leaked `loading=true` would silently stop the ruleset ever looking dirty
    # again — the user would edit, see nothing, and lose the change on reload.
    block = markup[markup.index("JUDGE.loading=true"):]
    block = block[:block.index("JUDGE.loading=false") + 40]
    assert "try{" in block and "finally" in block, (
        "JUDGE.loading must be cleared in a finally, not on the happy path only"
    )


# ══ visibility follows the provider ════════════════════════════════════════

def test_the_selects_are_shown_by_provider_not_by_key_mode(markup):
    block = markup[markup.index("window.renderJudgeKeyFields="):]
    block = block[:block.index("polKeyModeHint")]
    assert "polGeminiModelRow" in block and "polOpenaiModelRow" in block, (
        "renderJudgeKeyFields must show/hide the model rows"
    )
    assert re.search(r"provider===f\[0\]\|\|provider==='both'\)&&!!\(sel&&sel\.options\.length\)",
                     block), (
        "a model row shows when its provider is in play AND the select actually "
        "has options — an older server sends none, and an empty select is worse "
        "than no select"
    )
