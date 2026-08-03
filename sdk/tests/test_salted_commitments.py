"""H2 — the per-event salt, on top of the HMAC (never instead of it).

The load-bearing test in this file is the FIRST one. Every commitment ever
written was computed unsalted; if `salt=None` moves by one byte, every existing
row stops matching its own sidecar and the product's known-content proof breaks
retroactively for every customer at once.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re

import pytest

from foxy_audit import hashing, sidecar
from foxy_audit.client import FoxyClient


# ── the regression that cannot be allowed to move ────────────────────────────

# Pinned by hand, not by calling the code under test: a vector computed from the
# implementation would move WITH the implementation and prove nothing.
PINNED_KEY = "customer-secret"
PINNED_VALUE = "patient prompt"
PINNED_UNSALTED = hmac.new(
    PINNED_KEY.encode("utf-8"),
    json.dumps(PINNED_VALUE, ensure_ascii=True, sort_keys=True,
               separators=(",", ":"), default=str).encode("utf-8"),
    hashlib.sha256,
).hexdigest()


def test_no_salt_reproduces_todays_digest_byte_for_byte():
    assert hashing.commitment_hex(PINNED_VALUE, PINNED_KEY) == PINNED_UNSALTED
    assert hashing.commitment_hex(PINNED_VALUE, PINNED_KEY, None) == PINNED_UNSALTED
    # The same vector frozen as a literal, so a change to the canonicalization
    # recomputed ABOVE cannot quietly move the expectation along with it.
    assert PINNED_UNSALTED == (
        "a05ed61c721ad645f5b1b1a60e9fca7e4a7f222eeeb3694eeed7a8a03260b441")


def test_a_salt_changes_the_digest_and_two_salts_disagree():
    a = hashing.commitment_hex(PINNED_VALUE, PINNED_KEY, "aa" * 16)
    b = hashing.commitment_hex(PINNED_VALUE, PINNED_KEY, "bb" * 16)
    assert a != b
    assert a != PINNED_UNSALTED and b != PINNED_UNSALTED
    assert len(a) == 64


def test_the_salt_is_mixed_canonically_not_concatenated():
    """SHA-256(value + salt) is what the brief asked for and what we refused:
    naive concatenation is length-extension vulnerable. Prove the digest is the
    HMAC of a canonical JSON envelope, and NOT of any concatenation."""
    salt = "ab" * 16
    canonical_value = json.dumps(PINNED_VALUE, ensure_ascii=True, sort_keys=True,
                                 separators=(",", ":"), default=str)
    expected = hmac.new(
        PINNED_KEY.encode("utf-8"),
        json.dumps({"s": salt, "v": canonical_value}, ensure_ascii=True,
                   sort_keys=True, separators=(",", ":")).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    assert hashing.commitment_hex(PINNED_VALUE, PINNED_KEY, salt) == expected

    for concat in (PINNED_VALUE + salt, salt + PINNED_VALUE,
                   canonical_value + salt, salt + canonical_value):
        assert hashing.commitment_hex(PINNED_VALUE, PINNED_KEY, salt) != hashlib.sha256(
            concat.encode("utf-8")).hexdigest()


def test_salting_cannot_be_forged_by_moving_the_value_into_the_envelope():
    """The envelope must not be confusable with a plain value: committing the
    literal dict {"s": …, "v": …} unsalted must not equal a salted commitment of
    something else. Canonicalizing the value FIRST (into a JSON string) is what
    keeps the two apart."""
    salt = "cd" * 16
    forged = {"s": salt, "v": PINNED_VALUE}
    assert hashing.commitment_hex(forged, PINNED_KEY) != hashing.commitment_hex(
        PINNED_VALUE, PINNED_KEY, salt)


# ── generation ───────────────────────────────────────────────────────────────

def test_salts_come_from_the_csprng_and_are_128_bit_hex():
    salts = {sidecar.new_salt() for _ in range(200)}
    assert len(salts) == 200                       # no collisions at 128 bits
    assert all(re.fullmatch(r"[0-9a-f]{32}", s) for s in salts)


def test_new_salt_reads_the_os_csprng(monkeypatch):
    """`random` is seeded and reconstructible from its own output, and uuid4 leaks
    version/variant bits. A security primitive reads `secrets`, so prove the salt
    comes from there and asks for a full 16 bytes."""
    calls = []
    monkeypatch.setattr(sidecar.secrets, "token_hex",
                        lambda n: calls.append(n) or ("11" * n))
    assert sidecar.new_salt() == "11" * 16
    assert calls == [16]


# ── the sidecar ──────────────────────────────────────────────────────────────

def test_record_salt_appends_one_jsonl_line_per_event(tmp_path):
    path = tmp_path / "salts.jsonl"
    first = sidecar.record_salt(str(path), "evt-1")
    second = sidecar.record_salt(str(path), "evt-2")
    lines = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]
    assert lines == [{"event_id": "evt-1", "salt": first},
                     {"event_id": "evt-2", "salt": second}]
    assert first != second


def test_an_unwritable_sidecar_degrades_to_unsalted_rather_than_unprovable(tmp_path):
    """A salt that exists nowhere makes the commitment permanently unprovable.
    Falling back to yesterday's guarantee is strictly better."""
    unwritable = tmp_path / "no-such-dir" / "salts.jsonl"
    assert sidecar.record_salt(str(unwritable), "evt-1") is None


# ── the client wiring ────────────────────────────────────────────────────────

def _capture(monkeypatch):
    seen = {}

    def fake_submit(cfg, payload, wait=False):
        seen.update(payload)
        return {"ok": True}

    monkeypatch.setattr("foxy_audit.client.dispatch.submit", fake_submit)
    monkeypatch.setattr("foxy_audit.client.dispatch.resume", lambda cfg: None)
    monkeypatch.setattr("foxy_audit.client.org_policy.register", lambda cfg: None)
    return seen


def _client(**kw):
    return FoxyClient(api_key="k-test", client_id="c-1", desktop_ping=False,
                      commitment_key="customer-secret", **kw)


def test_without_a_sidecar_path_nothing_changes(monkeypatch):
    seen = _capture(monkeypatch)
    foxy = _client()

    @foxy.audit("chat")
    def run(prompt):
        return "the answer"

    run("hello")
    assert seen["commitment_alg"] == "hmac-sha256"
    assert seen["prompt_hash"] == hashing.commitment_hex("hello", "customer-secret")


def test_with_a_sidecar_path_the_event_is_salted_and_declares_it(monkeypatch, tmp_path):
    seen = _capture(monkeypatch)
    path = tmp_path / "salts.jsonl"
    foxy = _client(salt_sidecar_path=str(path))

    @foxy.audit("chat")
    def run(prompt):
        return "the answer"

    run("hello")
    assert seen["commitment_alg"] == "hmac-sha256-salted"

    recorded = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]
    assert len(recorded) == 1
    # The salt was recorded against THIS event, and the shipped hash is the one
    # that salt produces — the round trip a customer's verification depends on.
    assert recorded[0]["event_id"] == seen["event_id"]
    salt = recorded[0]["salt"]
    assert seen["prompt_hash"] == hashing.commitment_hex("hello", "customer-secret", salt)
    assert seen["response_hash"] == hashing.commitment_hex("the answer", "customer-secret", salt)
    assert seen["prompt_hash"] != hashing.commitment_hex("hello", "customer-secret")


def test_every_event_gets_its_own_salt(monkeypatch, tmp_path):
    seen = _capture(monkeypatch)
    path = tmp_path / "salts.jsonl"
    foxy = _client(salt_sidecar_path=str(path))

    @foxy.audit("chat")
    def run(prompt):
        return "same response"

    run("same prompt")
    first_hash = seen["prompt_hash"]
    run("same prompt")
    second_hash = seen["prompt_hash"]

    recorded = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]
    assert len({r["salt"] for r in recorded}) == 2
    # Identical text, different commitments: the point of a per-event salt.
    assert first_hash != second_hash


def test_the_salt_never_appears_on_the_wire(monkeypatch, tmp_path):
    seen = _capture(monkeypatch)
    path = tmp_path / "salts.jsonl"
    foxy = _client(salt_sidecar_path=str(path))

    @foxy.audit("chat")
    def run(prompt):
        return "the answer"

    run("hello")
    salt = json.loads(path.read_text(encoding="utf-8").splitlines()[0])["salt"]
    assert salt not in json.dumps(seen)
    assert not any("salt" in k for k in seen)


def test_a_broken_sidecar_still_ships_the_event_unsalted(monkeypatch, tmp_path):
    seen = _capture(monkeypatch)
    foxy = _client(salt_sidecar_path=str(tmp_path / "no-such-dir" / "salts.jsonl"))

    @foxy.audit("chat")
    def run(prompt):
        return "the answer"

    assert run("hello") == "the answer"
    assert seen["commitment_alg"] == "hmac-sha256"
    assert seen["prompt_hash"] == hashing.commitment_hex("hello", "customer-secret")


def test_no_key_means_no_salt_even_with_a_sidecar(monkeypatch, tmp_path):
    """The salt rides on the HMAC. Without a key there is no HMAC to salt, and
    `sha256-legacy` must keep its exact historical meaning — so nothing is
    written to the sidecar either."""
    _capture(monkeypatch)
    path = tmp_path / "salts.jsonl"
    monkeypatch.setenv("FOXY_COMMITMENT_KEY", "")
    foxy = FoxyClient(api_key="", client_id="c-1", desktop_ping=False,
                      commitment_key="", salt_sidecar_path=str(path))
    foxy.log_interaction("hello", "the answer", "chat")
    assert not path.exists()


@pytest.mark.parametrize("alg", ["hmac-sha256", "sha256-legacy", "hmac-sha256-salted"])
def test_commitment_alg_stays_within_the_backend_pattern(alg):
    """schemas.py pins `^[a-z0-9-]{1,32}$`; a longer or odd name would be
    rejected at ingest and the events would never land."""
    assert re.fullmatch(r"^[a-z0-9-]{1,32}$", alg)
