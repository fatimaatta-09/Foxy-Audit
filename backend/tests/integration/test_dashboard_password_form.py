"""The client half of the P3 §1 lockout fix, pinned in markup.

The backend was never wrong (see test_password_lockout.py — the API walk is green
in the browser's own request order). What locked the owner out was that the
Settings "change password" controls were not a form and the change was fired by
an onclick+fetch. Browser password managers only offer to UPDATE a stored
credential on a form submission or an explicit navigator.credentials.store();
neither happened, so the manager kept the old password and re-filled it at the
login screen.

These assertions are deliberately structural. They fail if someone unwraps the
form, drops the username field the manager needs to match the credential, or
removes the confirmation box that stops a one-shot typo from being unrecoverable.
"""

from __future__ import annotations

import os
import re

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
_DASH = os.path.join(_REPO, "foxy-dashboard", "foxy-audit-premium.html")


@pytest.fixture(scope="module")
def html() -> str:
    with open(_DASH, encoding="utf-8") as fh:
        return fh.read()


def _inside_form(html: str, needle: str) -> bool:
    pre = html[:html.index(needle)]
    return len(re.findall(r"<form\b", pre)) > len(re.findall(r"</form>", pre))


def test_change_password_controls_live_in_a_real_form(html):
    """No form, no 'update your saved password?' prompt. This is the lockout."""
    assert '<form id="pwForm"' in html
    for field in ('id="setCurPw"', 'id="setNewPw"', 'id="setNewPw2"'):
        assert _inside_form(html, field), f"{field} escaped the form — manager will not update"


def test_form_submits_rather_than_relying_on_a_click(html):
    """A submit button + a submit listener is what the manager watches for."""
    assert re.search(r'<form id="pwForm".*?<button[^>]+type="submit"', html, re.S)
    assert "_pwForm.addEventListener('submit'" in html
    assert 'onclick="changePassword()"' not in html, "the old click path is still wired"


def test_a_username_field_accompanies_the_password_fields(html):
    """Managers need a username in the same form to know WHICH credential to update.
    display:none is skipped by several managers, so it must be positioned off-screen."""
    assert _inside_form(html, 'id="pwUser"')
    m = re.search(r'<input id="pwUser"[^>]*>', html)
    assert m and 'autocomplete="username"' in m.group(0)
    assert "display:none" not in m.group(0)
    assert "left:-9999px" in m.group(0)
    assert "$('pwUser').value=me.email" in html, "the username field is never populated"


def test_credential_manager_is_told_explicitly(html):
    """Belt and braces for Chromium, where the API exists and is reliable."""
    assert "navigator.credentials.store" in html
    assert "window.PasswordCredential" in html, "must be feature-detected, not assumed"


def test_new_password_is_confirmed_before_submit(html):
    """A password you type once and mistype is a lockout with no recovery path."""
    assert 'id="setNewPw2"' in html
    assert "nw.value!==nw2.value" in html


def test_password_fields_keep_their_autocomplete_contract(html):
    for field, token in (("setCurPw", "current-password"),
                         ("setNewPw", "new-password"),
                         ("setNewPw2", "new-password")):
        m = re.search(r'<input class="cin" id="%s"[^>]*>' % field, html)
        assert m, f"{field} missing"
        assert f'autocomplete="{token}"' in m.group(0)
