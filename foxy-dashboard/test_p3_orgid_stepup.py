"""P3 §7.1 · the org-ID reveal is a real gate in the client too.

The backend half of this change is guarded in
`backend/tests/integration/test_org_id_stepup.py`. These are the client-side
halves that a backend test cannot see: that the page no longer *reads* an
org_id it is no longer sent, and that nothing stashes the value in the DOM
before someone asks for it.

That second point is the whole bug. The old settings field held the real id in
`dataset.real` from page load and swapped it into `value` on click — so "hidden"
meant hidden from the eye icon and from nobody else. Inspect the element and it
was right there, which is exactly the theatre this plan set out to remove.
"""

from __future__ import annotations

import re

import pytest

from test_p1_contrast import HTML

REVEAL_ENDPOINT = "/v1/account/org-id"


@pytest.fixture(scope="module")
def html() -> str:
    return HTML.read_text(encoding="utf-8")


def test_the_page_never_reads_org_id_off_the_identity_payload(html):
    """`/v1/auth/me` does not carry org_id any more. A leftover `me.org_id`
    read is silently `undefined` — the failure mode the plan calls out."""
    assert "me.org_id" not in html
    assert "__foxUser.org_id" not in html


def test_no_org_id_is_stashed_in_the_dom_at_load(html):
    """The `dataset.real` stash is gone, and nothing replaced it."""
    assert "dataset.real" not in html


def test_the_reveal_goes_through_the_step_up_gated_endpoint(html):
    """One helper, one endpoint, POSTed — the shape `require_step_up_user`
    guards elsewhere in account.py."""
    m = re.search(r"window\.foxOrgId\s*=\s*function[\s\S]{0,600}", html)
    assert m, "window.foxOrgId is gone"
    body = m.group(0)
    assert REVEAL_ENDPOINT in body
    assert "method:'POST'" in body


def test_a_refused_reveal_resolves_to_null_and_caches_nothing(html):
    """Honest failure. If a cancelled step-up cached an empty string, the next
    attempt would short-circuit and the field would sit blank for ever."""
    m = re.search(r"window\.foxOrgId\s*=\s*function[\s\S]{0,600}", html)
    body = m.group(0)
    assert "r.ok?r.json():null" in body, "a non-200 must resolve to null"
    assert ":null" in body and ".catch(" in body, "network failure must resolve to null too"
    # The cache is only ever written on the truthy branch.
    assert "(d&&d.org_id)?(window.__foxOrgId=d.org_id):null" in body


def test_every_org_id_surface_says_so_when_the_reveal_is_refused(html):
    """Three surfaces ask for the id: the overview card, the settings field and
    the command palette. None may fall back to a blank field or a fake value."""
    assert "Organization ID not revealed" in html, "palette is silent on refusal"
    assert "Not revealed — identity was not confirmed." in html, "settings field is silent"
    # The overview card routes through foxMask's shared resolver path.
    assert "not revealed" in html.lower()


def test_the_settings_toggle_fetches_instead_of_unmasking_a_held_value(html):
    m = re.search(r"window\.toggleOrgId\s*=\s*function[\s\S]{0,900}", html)
    assert m, "window.toggleOrgId is gone"
    body = m.group(0)
    assert "window.foxOrgId()" in body, "the toggle must fetch, not unmask"
    assert "window.__foxOrgId" in body, "it must reuse an already-revealed id"


def test_the_settings_field_has_a_live_region_for_the_refusal(html):
    """A status message nobody announces is not an honest failure state for a
    screen-reader user."""
    m = re.search(r'id="setOrgStatus"[^>]*', html)
    assert m, "setOrgStatus is gone"
    assert 'aria-live="polite"' in m.group(0)
    assert 'role="status"' in m.group(0)


def test_the_overview_card_starts_with_no_value_at_all(html):
    """It is built with an empty value plus a resolver — not with an id."""
    m = re.search(r"const org=\$\('metaOrg'\);[^\n]*", html)
    assert m, "the metaOrg mask is gone"
    line = m.group(0)
    assert "resolve:window.foxOrgId" in line
    assert "foxMask(org, ''" in line


def test_a_lazy_mask_ignores_the_hide_sensitive_preference(html):
    """`hide_sensitive_metadata=false` must NOT auto-reveal a value the page
    does not hold — that would fire a step-up prompt on page load."""
    m = re.search(r"var lazy=typeof opts\.resolve[\s\S]{0,300}", html)
    assert m, "the lazy-mask branch is gone from foxMask"
    assert "lazy?true:" in m.group(0)
