"""P3 §5 · the first-run walkthrough's server-side half.

Three things are load-bearing here.

**§5.6 — completion is per user, on the server.** localStorage would lose the
fact on a new laptop and show a finished user the tour again. So the assertions
below deliberately throw the client away and sign in fresh: a state that only
survives because the same TestClient is still holding it has not been tested.

**§5.3, as revised by P6a — a skip is an answer, not a deferral.** A skipped tour
used to be re-offered on the next login. The owner reported it as a bug, because
the client gated on `completed` alone and so re-offered on every page load. The
client now reads `skipped` too. The endpoint is unchanged: it still keeps the two
facts apart, and it must, because they are not the same fact — the desktop
console reads this payload, and a skip reported as a completion would tell it a
user who never saw the tour had finished it.

**The desktop reads this payload.** `/v1/onboarding` grew rather than changed, and
one test here exists purely to fail if a key the console reads goes missing.
"""

from __future__ import annotations

import pytest

# Every key the desktop console's `_on_onboarding` → `home_data.onboarding_view`
# path reads today. Growing this response is fine; shrinking it is not.
DESKTOP_KEYS = {"steps", "done", "total", "complete", "dismissed"}


def _tut(client) -> dict:
    r = client.get("/v1/onboarding")
    assert r.status_code == 200, r.text
    return r.json()


# ── guard 6 · the shipped client keeps everything it reads ─────────────────

def test_onboarding_still_returns_every_key_the_desktop_reads(make_org, login):
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    body = _tut(c)
    missing = DESKTOP_KEYS - set(body)
    assert not missing, f"the desktop console reads these and they are gone: {missing}"
    assert isinstance(body["steps"], list)
    assert isinstance(body["done"], int) and isinstance(body["total"], int)


def test_the_tutorial_block_is_additive(make_org, login):
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    body = _tut(c)
    assert set(body["tutorial"]) == {"enabled", "completed", "skipped"}
    # A brand-new workspace has never seen it.
    assert body["tutorial"]["completed"] is False
    assert body["tutorial"]["skipped"] is False


def test_writing_tutorial_state_does_not_disturb_the_checklist(make_org, login):
    """The two features share one JSONB column. Finishing the tour must not
    dismiss the P4 checklist card, and vice versa."""
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    assert c.put("/v1/onboarding", json={"dismissed": True}).status_code == 200
    assert c.put("/v1/onboarding", json={"tutorial_completed": True}).status_code == 200
    body = _tut(c)
    assert body["dismissed"] is True, "the checklist dismissal was clobbered"
    assert body["tutorial"]["completed"] is True


# ── guard 3 · completion survives a new device ─────────────────────────────

def test_completion_survives_a_brand_new_session(make_org, login):
    """§5.6. The second client shares nothing with the first but the account —
    no cookie, no localStorage, no in-memory anything."""
    org = make_org()
    first = login(org["admin_email"], org["admin_password"])
    assert first.put("/v1/onboarding", json={"tutorial_completed": True}).status_code == 200

    fresh = login(org["admin_email"], org["admin_password"])     # a "new device"
    assert _tut(fresh)["tutorial"]["completed"] is True


def test_completion_is_per_user_not_per_workspace(make_org, add_user, login):
    """A colleague who has never seen the tour must still be offered it."""
    org = make_org()
    add_user(org["org_id"], "new@test.dev", "newpass12345", role="member")
    admin = login(org["admin_email"], org["admin_password"])
    assert admin.put("/v1/onboarding", json={"tutorial_completed": True}).status_code == 200

    colleague = login("new@test.dev", "newpass12345")
    assert _tut(colleague)["tutorial"]["completed"] is False


# ── guard 4 · a skip stays skipped ─────────────────────────────────────────

def test_a_skipped_tutorial_is_not_offered_again(make_org, login):
    """P6a, reversing §5.3. The owner reported the re-offer as a bug: the client
    gated the offer on `completed` alone, so a tour the user had explicitly
    skipped came back on the next login — and, because the gate runs on every
    page load rather than on a session boundary, on every REFRESH.

    `skipped` has been persisted since the tour shipped; only the client ignored
    it. So the fix is a read, not a write, and this endpoint's job is unchanged:
    keep the two facts apart and report both truthfully. What moved is the
    meaning of a skip — it is now an answer, not a deferral.

    Both flags stay separate on purpose. Collapsing a skip into `completed` would
    tell the desktop console, which reads this same payload, that a user who
    never saw the tour had finished it."""
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    assert c.put("/v1/onboarding", json={"tutorial_skipped": True}).status_code == 200

    fresh = login(org["admin_email"], org["admin_password"])     # a "new device"
    body = _tut(fresh)["tutorial"]
    assert body["skipped"] is True, "the skip did not survive a new session"
    assert body["completed"] is False, "a skip is not a completion"
    assert body["enabled"] is True, "the kill switch is unrelated to the skip"


def test_finishing_clears_a_previous_skip(make_org, login):
    """A user who skipped, then came back through the user menu and finished, has
    finished. Leaving both flags set would report that state as a skip to anything
    reading one field — the desktop console among them."""
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    c.put("/v1/onboarding", json={"tutorial_skipped": True})
    c.put("/v1/onboarding", json={"tutorial_completed": True})
    body = _tut(c)["tutorial"]
    assert body["completed"] is True and body["skipped"] is False


def test_a_partial_update_leaves_the_other_field_alone(make_org, login):
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    c.put("/v1/onboarding", json={"tutorial_skipped": True})
    c.put("/v1/onboarding", json={"dismissed": True})       # unrelated field
    assert _tut(c)["tutorial"]["skipped"] is True


# ── guard 5 · the kill switch ──────────────────────────────────────────────

def test_the_feature_flag_reaches_the_client(make_org, login, monkeypatch):
    """§5.5. The dashboard is a static file; this response is the only way the
    flag can reach it, so it has to be readable here."""
    import app.config as cfg
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    assert _tut(c)["tutorial"]["enabled"] is True

    s = cfg.get_settings()
    monkeypatch.setattr(s, "first_run_tutorial_enabled", False)
    assert _tut(c)["tutorial"]["enabled"] is False


def test_the_flag_does_not_erase_progress(make_org, login, monkeypatch):
    """Turning the tour off must not look like "nobody has done it" when it is
    turned back on."""
    import app.config as cfg
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    c.put("/v1/onboarding", json={"tutorial_completed": True})

    s = cfg.get_settings()
    monkeypatch.setattr(s, "first_run_tutorial_enabled", False)
    assert _tut(c)["tutorial"]["completed"] is True


# ── the coercion trap the plan warned about ────────────────────────────────

def test_tutorial_state_is_not_routed_through_the_preferences_path(make_org, login):
    """_ALLOWED_PREFS bool()-coerces every value it stores. These fields are
    genuinely booleans, but they must live in onboarding_state, not preferences,
    or a later non-boolean field would silently become True."""
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    c.put("/v1/onboarding", json={"tutorial_completed": True})
    prefs = c.get("/v1/auth/me").json().get("preferences") or {}
    assert "tutorial_completed" not in prefs
    assert "tutorial_skipped" not in prefs


@pytest.mark.parametrize("field", ["password_hash", "key_hash", "token_hash"])
def test_the_payload_serializes_nothing_sensitive(make_org, login, field):
    org = make_org()
    c = login(org["admin_email"], org["admin_password"])
    assert field not in c.get("/v1/onboarding").text


def test_onboarding_requires_a_session(client):
    assert client.get("/v1/onboarding").status_code == 401
    assert client.put("/v1/onboarding", json={"tutorial_completed": True}).status_code == 401
