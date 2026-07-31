"""P6c — the avatar endpoint, which is this backend's FIRST file upload.

Everything before this took JSON a schema shaped. This takes bytes a user chose,
which is a different threat model, so most of what is asserted here is refusal:

  · a file that is not an image, whatever it is called
  · an image over the cap
  · a PNG wearing a lying Content-Type, and a script wearing an image one — the
    filename and the declared type are both attacker-controlled and neither is
    consulted
  · one user reaching another user's photo

The last one is worth stating plainly: the endpoint takes no id at all, so the
test is not that a cross-user id is rejected — it is that there is no way to ask
the question. That is the strongest form the check can take, and it is why the
test asserts on what each session gets back rather than on a 403.
"""

from __future__ import annotations

import io

import pytest

PIL = pytest.importorskip("PIL", reason="Pillow is required for the avatar endpoint")
from PIL import Image  # noqa: E402


def _img_bytes(fmt: str = "PNG", size=(400, 260), colour=(200, 40, 40)) -> bytes:
    """A real, decodable image. Deliberately NOT square, so the server's
    centre-crop is exercised rather than assumed."""
    buf = io.BytesIO()
    Image.new("RGB", size, colour).save(buf, format=fmt)
    return buf.getvalue()


def _upload(client, data: bytes, filename="a.png", content_type="image/png"):
    return client.post("/v1/account/avatar",
                       files={"file": (filename, data, content_type)})


# ── auth ───────────────────────────────────────────────────────────────────

def test_every_avatar_route_requires_a_session(client):
    assert client.get("/v1/account/avatar").status_code in (401, 403)
    assert client.delete("/v1/account/avatar").status_code in (401, 403)
    assert _upload(client, _img_bytes()).status_code in (401, 403)


# ── the happy path ─────────────────────────────────────────────────────────

def test_upload_then_fetch_returns_a_square_png(make_org, login):
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])

    assert c.get("/v1/auth/me").json()["has_avatar"] is False
    assert c.get("/v1/account/avatar").status_code == 404      # nothing set yet

    r = _upload(c, _img_bytes(size=(400, 260)))
    assert r.status_code == 200, r.text
    assert r.json()["has_avatar"] is True

    got = c.get("/v1/account/avatar")
    assert got.status_code == 200
    assert got.headers["content-type"].startswith("image/png")
    # private, not public: this is one person's face, not a static asset, and it
    # must not sit in a shared proxy.
    assert "private" in got.headers.get("cache-control", "")

    out = Image.open(io.BytesIO(got.content))
    assert out.format == "PNG"
    assert out.size == (256, 256), "a non-square upload should be centre-cropped"


def test_the_re_encode_strips_metadata(make_org, login):
    """The re-encode is the security control, not a resize step. Anything riding
    along in the container — EXIF here, but equally an ICC profile, a PNG text
    chunk or bytes appended after the image data — does not survive being
    redrawn onto a canvas the server created."""
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])

    buf = io.BytesIO()
    im = Image.new("RGB", (300, 300), (10, 120, 200))
    exif = im.getexif()
    exif[0x010E] = "SECRET-MARKER-DO-NOT-PERSIST"        # ImageDescription
    im.save(buf, format="JPEG", exif=exif)
    payload = buf.getvalue()
    assert b"SECRET-MARKER-DO-NOT-PERSIST" in payload   # it really is in there

    assert _upload(c, payload, "photo.jpg", "image/jpeg").status_code == 200
    stored = c.get("/v1/account/avatar").content
    assert b"SECRET-MARKER-DO-NOT-PERSIST" not in stored
    assert not Image.open(io.BytesIO(stored)).getexif()


@pytest.mark.parametrize("fmt,ctype", [("PNG", "image/png"),
                                       ("JPEG", "image/jpeg"),
                                       ("WEBP", "image/webp")])
def test_the_three_accepted_formats_all_land_as_png(make_org, login, fmt, ctype):
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    assert _upload(c, _img_bytes(fmt), f"a.{fmt.lower()}", ctype).status_code == 200
    assert Image.open(io.BytesIO(c.get("/v1/account/avatar").content)).format == "PNG"


# ── the refusals ───────────────────────────────────────────────────────────

def test_a_non_image_is_rejected(make_org, login):
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    r = _upload(c, b"#!/bin/sh\nrm -rf /\n", "payload.png", "image/png")
    assert r.status_code == 400, r.text
    assert c.get("/v1/auth/me").json()["has_avatar"] is False   # nothing was stored


def test_a_lying_content_type_does_not_help(make_org, login):
    """Both directions. A real PNG announced as a PDF is still accepted, because
    the bytes are what matter; a script announced as a PNG is still refused, for
    the same reason. Neither the filename nor the declared type is consulted."""
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])

    ok = _upload(c, _img_bytes(), "not-an-image.pdf", "application/pdf")
    assert ok.status_code == 200, "the decoded bytes are what decide, not the label"

    bad = _upload(c, b"<?php system($_GET['c']); ?>", "shell.php", "image/png")
    assert bad.status_code == 400


def test_a_gif_is_refused_even_though_pillow_reads_it(make_org, login):
    """Pillow opens far more formats than this endpoint accepts. The allow-list
    is checked AFTER decoding, so "Pillow could read it" is not the bar."""
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    r = _upload(c, _img_bytes("GIF"), "a.gif", "image/gif")
    assert r.status_code == 400
    assert "png" in r.json()["detail"].lower()


def test_an_oversized_file_is_rejected(make_org, login):
    """6 MB against a 5 MB cap. Incompressible noise, because a 6 MB flat colour
    PNG compresses to a few KB and would prove nothing about the cap."""
    import os
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    payload = os.urandom(6 * 1024 * 1024)
    r = _upload(c, payload, "big.png", "image/png")
    assert r.status_code == 413, r.text
    assert c.get("/v1/auth/me").json()["has_avatar"] is False


def test_an_empty_upload_is_rejected(make_org, login):
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    assert _upload(c, b"").status_code == 400


# ── isolation ──────────────────────────────────────────────────────────────

def test_a_user_cannot_reach_another_users_avatar(make_org, add_user, login):
    """There is no id parameter on GET — the path is derived from the session —
    so the assertion is that each session sees its OWN state and nothing else.
    A colleague with no photo gets 404 while the admin's file exists on disk."""
    org = make_org()
    add_user(org["org_id"], "colleague@test.dev", "colleaguepass12345", role="member")

    admin = login(org["admin_email"], org["admin_password"])
    assert _upload(admin, _img_bytes(colour=(255, 0, 0))).status_code == 200
    admin_bytes = admin.get("/v1/account/avatar").content

    mate = login("colleague@test.dev", "colleaguepass12345")
    assert mate.get("/v1/auth/me").json()["has_avatar"] is False
    assert mate.get("/v1/account/avatar").status_code == 404

    # and once the colleague uploads their own, the two are different files
    assert _upload(mate, _img_bytes(colour=(0, 0, 255))).status_code == 200
    assert mate.get("/v1/account/avatar").content != admin_bytes
    assert admin.get("/v1/account/avatar").content == admin_bytes   # untouched


def test_delete_clears_the_flag_and_the_file(make_org, login):
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    assert _upload(c, _img_bytes()).status_code == 200
    assert c.get("/v1/auth/me").json()["has_avatar"] is True

    assert c.delete("/v1/account/avatar").status_code == 200
    assert c.get("/v1/auth/me").json()["has_avatar"] is False
    assert c.get("/v1/account/avatar").status_code == 404
    # idempotent: removing a photo that is already gone is not an error
    assert c.delete("/v1/account/avatar").status_code == 200


def test_a_second_upload_replaces_the_first(make_org, login):
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    _upload(c, _img_bytes(colour=(255, 0, 0)))
    first = c.get("/v1/account/avatar").content
    _upload(c, _img_bytes(colour=(0, 255, 0)))
    assert c.get("/v1/account/avatar").content != first


# ── the /me contract ───────────────────────────────────────────────────────

def test_me_reports_has_avatar_and_still_no_org_id(make_org, login):
    """has_avatar is a BOOLEAN — the bytes are not on this response, and neither
    is a path. And org_id stays absent: its removal was deliberate (it is behind
    an emailed step-up now), so a phase that touches this model has to leave it
    out. See test_p3_orgid_stepup.py."""
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])

    body = c.get("/v1/auth/me").json()
    assert body["has_avatar"] is False
    assert body["avatar_updated_at"] is None
    assert "org_id" not in body
    assert "avatar_path" not in body, "the server's filesystem layout is not the client's business"

    assert _upload(c, _img_bytes()).status_code == 200
    body = c.get("/v1/auth/me").json()
    assert body["has_avatar"] is True
    assert isinstance(body["avatar_updated_at"], str) and body["avatar_updated_at"]
    assert "org_id" not in body


@pytest.mark.parametrize("field", ["password_hash", "key_hash", "token_hash"])
def test_no_avatar_response_serialises_anything_sensitive(make_org, login, field):
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    assert field not in _upload(c, _img_bytes()).text
    assert field not in c.get("/v1/auth/me").text
    assert field not in c.delete("/v1/account/avatar").text


# ── audit ──────────────────────────────────────────────────────────────────

def test_both_writes_are_audited(make_org, login):
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    _upload(c, _img_bytes())
    c.delete("/v1/account/avatar")
    actions = {a["action"] for a in c.get("/v1/account/audit").json()}
    assert "account.avatar_set" in actions
    assert "account.avatar_clear" in actions
