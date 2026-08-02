"""P6c · the Settings account card — avatar upload, password reveals, reset link.

The failures worth guarding here are the quiet ones. A `<form action=>` post
would work in a browser and silently skip both `window.fetch` patches, so CSRF
and the step-up retry would just stop applying to this one request. An eye toggle
that loses `data-pw-toggle` still renders — it just never does anything, because
the IIFE that wires it only ever sees attributes. And an `<img>` fallback that
stops being an overlay would leave an empty circle rather than an initial.

None of those raise. All of them look fine in a screenshot.
"""

from __future__ import annotations

import re

import pytest

from test_p1_contrast import HTML  # the one source of the file path

PW_FIELDS = ("setCurPw", "setNewPw", "setNewPw2")


@pytest.fixture(scope="module")
def html() -> str:
    return HTML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def account_card(html) -> str:
    """Just the Account & identity card — assertions must not pass because of
    the sign-in gate's own password fields somewhere else in the file."""
    # D3 turned the five <section>s into seven <details>. The heading id this
    # anchors on moved onto the summary row and kept its name deliberately, so
    # the slice is the same region bounded by the element that replaced it.
    start = html.index('id="sec-accountidentity"')
    return html[start:html.index("</details>", start)]


# ══ the avatar block ═══════════════════════════════════════════════════════

def test_the_card_offers_upload_and_remove(account_card):
    for el in ('id="setAvatarPick"', 'id="setAvatarClear"', 'id="setAvatarFile"',
               'id="setAvatarPrev"'):
        assert el in account_card, f"{el} is missing from the account card"


def test_the_file_input_accepts_only_the_formats_the_server_takes(account_card):
    """A picker that offers HEIC and a server that refuses it is a rejection the
    user could not have avoided. The list has to match app/routers/account.py's
    _AVATAR_FORMATS."""
    m = re.search(r'<input type="file" id="setAvatarFile"[^>]*accept="([^"]+)"', account_card)
    assert m, "the file input lost its accept list"
    assert set(m.group(1).split(",")) == {"image/png", "image/jpeg", "image/webp"}


def test_the_file_input_is_off_screen_not_display_none(account_card):
    """display:none removes an input from the tab order. This one is triggered by
    a real button, but it still has to stay focusable — the same reason the
    password form's hidden username field is positioned off-screen."""
    m = re.search(r'<input type="file" id="setAvatarFile"[^>]*>', account_card)
    assert m and "display:none" not in m.group(0)
    assert "left:-9999px" in m.group(0)


def test_the_upload_goes_through_fetch_not_a_form_post(html):
    """This SPA patches window.fetch twice — CSRF, and the step-up retry. A bare
    <form action=> post bypasses both, which is why FormData through fetch is the
    only correct shape here."""
    assert "new FormData()" in html
    assert "api('/v1/account/avatar',{method:'POST',body:fd})" in html
    # and it must NOT set a content-type: the browser has to supply the multipart
    # boundary, and an explicit header loses it
    m = re.search(r"api\('/v1/account/avatar',\{method:'POST'[^}]*\}", html)
    assert m and "content-type" not in m.group(0).lower()
    assert not re.search(r'<form[^>]*action="[^"]*avatar', html)


def test_the_client_resizes_before_upload(html):
    """A 12 MP phone photo is ~6 MB on the wire for a picture the server crops to
    256x256 and throws the rest away. The canvas step is about the user's data,
    not about security — the server re-decodes everything regardless."""
    assert "const AV_PX=256" in html
    assert "canvas" in html and "toBlob" in html


def test_the_upload_is_capped_client_side_too(html):
    """Checked here so the refusal is instant, and again on the server because
    this one is a courtesy that any client can skip."""
    assert "const AV_PX=256, AV_MAX=5*1024*1024" in html
    assert "file.size>AV_MAX" in html


def test_the_status_line_is_announced(account_card):
    """A rejected upload that is only coloured red is invisible to a screen
    reader — and this is the control most likely to be refused."""
    m = re.search(r'<div id="setAvatarStatus"[^>]*>', account_card)
    assert m, "the avatar status line is gone"
    assert 'role="status"' in m.group(0) and 'aria-live="polite"' in m.group(0)


def test_the_avatar_image_is_decorative(html):
    """Every mount already renders the user's name in text beside it. Alt text
    here would make the account announce itself twice, and any wording would be
    invented — nobody described this picture."""
    m = re.search(r"img\.alt='';\s*img\.setAttribute\('aria-hidden','true'\);", html)
    assert m, "the avatar <img> is not marked decorative"


def test_the_initial_survives_as_the_fallback(html):
    """The initial is written first and always: it is the resting state for a
    user with no photo, the in-flight state, and the it-404'd state. The <img>
    goes OVER it, so onerror only has to remove the image to be correct again."""
    assert "el.textContent=initial;" in html
    assert "img.onerror=function(){ if(img.parentNode)img.parentNode.removeChild(img); };" in html
    # the overlay, not a replacement
    assert ".av-img{position:absolute" in html


def test_the_cache_buster_is_wired(html):
    """Without ?v= the browser keeps serving the previous photo from cache — the
    URL is otherwise byte-identical after an upload."""
    assert "me.avatar_updated_at?encodeURIComponent(me.avatar_updated_at)" in html
    assert "'/v1/account/avatar'+(ver?('?v='+ver):'')" in html


def test_an_avatar_change_repaints_every_mount(html):
    """The dock, the top bar and the settings preview all read [data-avatar]. If
    only one were refreshed they would disagree about who is signed in."""
    assert "document.querySelectorAll('[data-avatar]')" in html
    for after in ("uploadAvatar", "removeAvatar"):
        block = html[html.index("window.%s=" % after):]
        assert "window.foxAvatar()" in block[:1400], f"{after} does not repaint the mounts"


def test_the_photo_is_opt_in_per_mount(html):
    """The initial goes on every mount; the picture only goes where it is asked
    for. Written as an opt-in rather than an exclusion list on purpose — an
    exclusion works until someone adds a fourth mount, which would then silently
    inherit a photo nobody chose for it. This pins the gate itself, because the
    whole point of the design is that the quiet behaviour is the default."""
    assert "if(!me.has_avatar||!el.hasAttribute('data-avatar-photo'))return;" in html, (
        "the <img> is no longer gated on [data-avatar-photo] — every mount is "
        "about to start showing a face again"
    )
    # the initial is still unconditional, before the gate
    loop = html[html.index("document.querySelectorAll('[data-avatar]')"):]
    assert loop.index("el.textContent=initial;") < loop.index("hasAttribute('data-avatar-photo')"), (
        "the initial must be written before the photo gate, so a mount that opts "
        "out still gets its letter"
    )


def test_only_the_top_bar_and_the_settings_preview_carry_a_photo(html):
    """The owner's call: the face belongs in the top-right chip, and in the
    Settings preview because that IS the preview of the photo you just uploaded.
    The dock stays a letter."""
    mounts = re.findall(r'<div[^>]*\bdata-avatar\b[^>]*>', html)
    assert len(mounts) == 3, f"the number of avatar mounts changed: {len(mounts)}"

    by_id = {}
    for tag in mounts:
        m = re.search(r'id="([^"]+)"', tag)
        cls = re.search(r'class="([^"]+)"', tag)
        by_id[m.group(1) if m else cls.group(1)] = "data-avatar-photo" in tag

    assert by_id.get("dockUser") is False, (
        "the dock avatar took a photo again — it is meant to stay the initial"
    )
    assert by_id.get("topuser-av") is True, "the top-bar chip lost its photo"
    assert by_id.get("setAvatarPrev") is True, (
        "the Settings preview lost its photo — it is the preview of the upload"
    )


def test_the_dock_keeps_its_plate_and_its_letter(html):
    """Only the <img> was taken away. The dock avatar must look exactly as it
    did: same plate, same letter, same fallback when there is no name."""
    m = re.search(r'<div class="dock-user" id="dockUser"[^>]*>([^<]*)</div>', html)
    assert m, "the dock avatar markup changed shape"
    assert m.group(1) == "F", "the dock avatar lost its no-name fallback letter"
    assert re.search(r"\.dock-user\{[^}]*background:var\(--bg\)", html), (
        "the dock avatar lost its plate"
    )
    # and it is no longer set up to host an overlay it will never receive
    m2 = re.search(r"^\.topuser-av,\.av-prev\{position:relative;overflow:hidden;\}",
                   html, re.M)
    assert m2, "the overlay anchors should now list only the two photo mounts"


# ══ the password fields ════════════════════════════════════════════════════

def test_all_three_password_fields_carry_a_toggle(account_card):
    for fid in PW_FIELDS:
        assert f'data-pw-toggle="{fid}"' in account_card, (
            f"#{fid} has no reveal button — the IIFE wires by attribute, so a "
            "field without one silently has no toggle at all"
        )
        assert f'<div class="pw-wrap">' in account_card


def test_the_toggles_match_the_aria_contract(account_card):
    """Same contract test_login_card.py asserts for the sign-in card. Two cards
    with the same control must not disagree about how it announces itself."""
    buttons = re.findall(r'<button[^>]*class="pw-eye"[^>]*>', account_card)
    assert len(buttons) == 3, f"expected 3 reveal buttons on this card, found {len(buttons)}"
    for tag in buttons:
        assert 'type="button"' in tag, "a submit button inside the form would submit it"
        assert 'aria-pressed="false"' in tag
        assert 'aria-label="Show password"' in tag
        assert "aria-controls=" in tag


def test_hiding_passwords_covers_the_new_fields(html):
    """foxHidePasswords() walks [data-pw-toggle], so the settings fields are
    covered by construction — this pins that it still walks the attribute rather
    than a hardcoded list that would have to be kept in sync."""
    block = html[html.index("window.foxHidePasswords="):]
    assert "querySelectorAll('.pw-eye[data-pw-toggle]')" in block[:400]


# ══ the reset link ═════════════════════════════════════════════════════════

def test_the_reset_link_is_present_and_placed_with_the_field_it_helps(account_card):
    """Under the current-password field, which is the one that failed the reader
    — not in a footer with the legal links."""
    assert 'id="setPwForgot"' in account_card
    assert account_card.index('id="setCurPw"') < account_card.index('id="setPwForgot"')
    assert account_card.index('id="setPwForgot"') < account_card.index('id="setNewPw"')
    assert "We'll email you a reset link." in account_card


def test_the_reset_link_calls_the_existing_endpoint(html):
    """The whole reset flow is already built front and back. This is a call site,
    not a second implementation."""
    assert "api('/v1/auth/forgot-password'" in html
    block = html[html.index("window.setPwForgot="):]
    assert "/v1/auth/forgot-password" in block[:900]


def test_the_reset_response_is_enumeration_safe(html):
    """Same wording the sign-in gate uses. The answer must not change based on
    whether the address exists — even though here it is the signed-in user's."""
    block = html[html.index("window.setPwForgot="):]
    assert "If that address has an account, a reset link is on its way." in block[:1400]


# ══ the standing rules ═════════════════════════════════════════════════════

def test_nothing_new_assigns_value_directly(html):
    """`.value = x` with no change event has bitten this file three times. The
    one exception here is `input.value=''`, which CLEARS the file picker so the
    same photo can be re-selected after a failed upload — there is nothing
    watching a file input for changes it could miss."""
    block = html[html.index("/* ── P6c · profile photo"):]
    block = block[:block.index("window.savePref=")]
    offenders = [m for m in re.findall(r"\w+\.value\s*=\s*[^=]", block)
                 if not m.startswith("input.value=")]
    assert not offenders, f"direct .value assignment in the P6c block: {offenders}"
    # …and the one exception really is only the picker reset
    assert "try{ input.value=''; }catch(e){}" in block
