"""Org-side enforcement policy (P4 §B).

The workspace can tighten what the SDK enforces without a redeploy. That is a
remote control over customer code, so the tests that matter are the ones proving
what it CANNOT do:

* it can never weaken enforcement — `local=block` survives any org setting;
* it can never turn an explicit local mode into `redact`, because redact rewrites
  the prompt before the model sees it and raises nothing, so nobody finds out;
* it can never delay or fail the wrapped function, however badly the network
  behaves.

Everything else — TTL, disk cache, the kill switch — exists to keep those three
true in the real world rather than only on the happy path.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest

from foxy_audit import org_policy
from foxy_audit.client import FoxyClient, FoxyPolicyBlocked
from foxy_audit.config import FoxyConfig

PHI = "Patient SSN 123-45-6789 needs review"


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    """Every test starts with no cache, no registry and no ambient env."""
    for var in ("FOXY_MODE", "FOXY_ORG_POLICY", "FOXY_ORG_POLICY_TTL",
                "FOXY_API_KEY", "FOXY_SPOOL_PATH"):
        monkeypatch.delenv(var, raising=False)
    org_policy._reset_for_tests()
    monkeypatch.setenv("FOXY_SPOOL_PATH", str(tmp_path / "spool.sqlite3"))
    yield
    org_policy._reset_for_tests()


def _cfg(tmp_path, **kw):
    kw.setdefault("api_key", "foxy_sk_test")
    kw.setdefault("spool_path", str(tmp_path / "spool.sqlite3"))
    return FoxyConfig.resolve(**kw)


def _seed_org(cfg, sdk_mode, fetched_at=None):
    """Put a policy in the cache as though a fetch had succeeded."""
    with org_policy._lock:
        org_policy._cache[org_policy._key(cfg)] = {
            "mode": sdk_mode,
            "fetched_at": time.time() if fetched_at is None else fetched_at}


# ══ 1 · the kill-switch test — org may never weaken ════════════════════════

def test_org_observe_cannot_unblock_local_block(tmp_path):
    """THE most important assertion in this plan. A workspace must never be able
    to switch off enforcement that the code itself asked for."""
    cfg = _cfg(tmp_path, mode="block")
    _seed_org(cfg, "observe")
    assert org_policy.resolve(cfg, None) == ("block", False)
    assert org_policy.resolve(cfg, "block") == ("block", False)


@pytest.mark.parametrize("org", ["observe", "redact", None])
def test_local_block_survives_every_org_value(tmp_path, org):
    cfg = _cfg(tmp_path, mode="block")
    if org:
        _seed_org(cfg, org)
    assert org_policy.resolve(cfg, None)[0] == "block"


# ══ 2 · the silent-rewrite hole — org may tighten to block, never to redact ═

def test_org_redact_does_not_override_an_explicit_observe(tmp_path):
    """Block raises — loud, impossible to miss. Redact REWRITES the prompt before
    the model sees it, changes what the model receives, degrades the output and
    raises nothing at all. A workspace must not be able to do that to a service
    whose code explicitly said observe."""
    cfg = _cfg(tmp_path, mode="observe")
    _seed_org(cfg, "redact")
    assert org_policy.resolve(cfg, None) == ("observe", False)


def test_org_redact_does_not_override_an_explicit_decorator_mode(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_org(cfg, "redact")
    assert org_policy.resolve(cfg, "observe") == ("observe", False)


def test_org_block_does_tighten_an_observe_service(tmp_path):
    cfg = _cfg(tmp_path, mode="observe")
    _seed_org(cfg, "block")
    assert org_policy.resolve(cfg, None) == ("block", True)


def test_the_sdk_reads_its_own_field_not_the_judge_one():
    """enforcement_mode (block|flag|monitor) is what to do with a VERDICT after
    grading. sdk_enforcement (observe|redact|block) is what to do BEFORE the model
    is called. They are different settings that share a word, and reading the
    wrong one is an upgrade incident, not a bug — see
    test_an_existing_org_upgrading_sees_no_change_at_all."""
    assert org_policy.SDK_ENFORCEMENT_FIELD == "sdk_enforcement"
    assert not hasattr(org_policy, "ORG_TO_SDK"), (
        "the judge-vocabulary mapping is gone; the SDK reads its own field")


# ══ 3 · gap-filling still works ════════════════════════════════════════════

def test_org_fills_a_gap_when_nothing_is_specified_locally(tmp_path):
    """No decorator mode and no FOXY_MODE means the code expressed no opinion, so
    the org is filling a gap rather than overriding anybody."""
    cfg = _cfg(tmp_path)
    assert cfg.mode_is_explicit is False
    _seed_org(cfg, "redact")
    assert org_policy.resolve(cfg, None) == ("redact", True)


def test_an_explicit_observe_is_not_a_gap(tmp_path, monkeypatch):
    """The distinction the whole rule rests on. cfg.mode is "observe" in both
    cases; only mode_is_explicit tells them apart."""
    implicit = _cfg(tmp_path)
    monkeypatch.setenv("FOXY_MODE", "observe")
    explicit = _cfg(tmp_path)
    assert implicit.mode == explicit.mode == "observe"
    assert implicit.mode_is_explicit is False and explicit.mode_is_explicit is True
    _seed_org(implicit, "redact")
    _seed_org(explicit, "redact")
    assert org_policy.resolve(implicit, None)[0] == "redact"
    assert org_policy.resolve(explicit, None)[0] == "observe"


# ══ 4 · never blocks the caller ════════════════════════════════════════════

def test_a_hanging_policy_endpoint_never_delays_a_call(tmp_path, monkeypatch):
    """The fetch lives on the dispatcher thread. Even if it hangs forever, a
    decorated call must return in its normal budget."""
    started = threading.Event()

    def _hang(*a, **kw):
        started.set()
        time.sleep(30)

    monkeypatch.setattr(org_policy, "_refresh", _hang)
    client = FoxyClient(api_key="", spool_path=str(tmp_path / "s.sqlite3"))

    @client.audit(policy="hipaa")
    def call(prompt):
        return "ok"

    hang = threading.Thread(target=org_policy.tick, daemon=True)
    hang.start()
    started.wait(timeout=5)
    began = time.time()
    assert call(prompt="hello") == "ok"
    assert time.time() - began < 2.0, "a policy fetch delayed the wrapped function"


def test_a_failing_fetch_never_raises(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    org_policy.register(cfg)

    class _Boom:
        @staticmethod
        def get(*a, **kw):
            raise OSError("network down")

    monkeypatch.setitem(__import__("sys").modules, "requests", _Boom)
    org_policy.tick()                       # must not raise
    assert org_policy.org_mode(cfg) is None


# ══ 5 · cold start is byte-identical to today ══════════════════════════════

def test_cold_start_with_no_cache_behaves_exactly_as_before(tmp_path):
    cfg = _cfg(tmp_path)
    assert org_policy.org_mode(cfg) is None
    assert org_policy.resolve(cfg, None) == ("observe", False)
    assert org_policy.resolve(cfg, "block") == ("block", False)
    assert org_policy.resolve(cfg, "redact") == ("redact", False)


def test_a_client_with_no_key_never_registers(tmp_path):
    """No key means no backend, so there is nothing to fetch and nothing to leak."""
    cfg = _cfg(tmp_path, api_key="")
    org_policy.register(cfg)
    with org_policy._lock:
        assert org_policy._registry == {}


# ══ 6 · the cache survives a restart ═══════════════════════════════════════

def test_policy_survives_a_process_restart(tmp_path, monkeypatch):
    """Without the disk cache a restarted process reverts to local defaults for a
    whole TTL — the exact window an org tightened to close."""
    cfg = _cfg(tmp_path)
    org_policy.register(cfg)
    org_policy._save_to_disk(cfg, "block", time.time())

    org_policy._reset_for_tests()           # a brand-new process
    org_policy.register(cfg)
    assert org_policy.org_mode(cfg) == "block"
    assert org_policy.resolve(cfg, None) == ("block", True)


def test_the_disk_cache_never_contains_the_api_key(tmp_path):
    cfg = _cfg(tmp_path, api_key="foxy_sk_SUPERSECRET")
    org_policy._save_to_disk(cfg, "block", time.time())
    raw = org_policy._cache_file(cfg).read_text(encoding="utf-8")
    assert "SUPERSECRET" not in raw
    assert json.loads(raw)                  # still valid JSON


def test_a_corrupt_cache_is_ignored_not_fatal(tmp_path):
    cfg = _cfg(tmp_path)
    path = org_policy._cache_file(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    org_policy.register(cfg)                # must not raise
    assert org_policy.org_mode(cfg) is None


# ══ 7 · stale beats absent ═════════════════════════════════════════════════

def test_a_failed_refresh_keeps_the_last_known_policy(tmp_path, monkeypatch):
    """Reverting to the local default on a blip would silently un-tighten every
    deployment the moment the network wobbled."""
    cfg = _cfg(tmp_path, org_policy_ttl=0.01)
    org_policy.register(cfg)
    _seed_org(cfg, "block", fetched_at=0.0)     # stale enough to trigger a refresh

    class _Boom:
        @staticmethod
        def get(*a, **kw):
            raise OSError("network down")

    monkeypatch.setitem(__import__("sys").modules, "requests", _Boom)
    org_policy.tick()
    assert org_policy.org_mode(cfg) == "block", "a failed fetch discarded a good policy"


# ══ 8 · per-call resolution — proves B1 left the closure ═══════════════════

def test_changing_the_cache_changes_the_next_call_without_reimport(tmp_path):
    """If resolution were still captured at decoration time this would keep
    returning "ok" forever, because the decorator ran before the org tightened."""
    client = FoxyClient(api_key="foxy_sk_test", spool_path=str(tmp_path / "s.sqlite3"),
                        desktop_ping=False)

    @client.audit(policy="hipaa")
    def call(prompt):
        return "ok"

    assert call(prompt=PHI) == "ok"          # observe: runs, records after

    _seed_org(client.cfg, "block")           # the workspace tightens, mid-process
    with pytest.raises(FoxyPolicyBlocked):
        call(prompt=PHI)

    _seed_org(client.cfg, "observe")         # and relaxes again
    assert call(prompt=PHI) == "ok"


def test_per_call_resolution_also_applies_to_async(tmp_path):
    """The async wrapper is a separate code path from the sync one, and there are
    four of them in total — missing one would leave a frozen mode in production
    for exactly the callers using that shape."""
    client = FoxyClient(api_key="foxy_sk_test", spool_path=str(tmp_path / "s.sqlite3"),
                        desktop_ping=False)

    @client.audit(policy="hipaa")
    async def acall(prompt):
        return "ok"

    assert asyncio.run(acall(prompt=PHI)) == "ok"
    _seed_org(client.cfg, "block")
    with pytest.raises(FoxyPolicyBlocked):
        asyncio.run(acall(prompt=PHI))


def test_per_call_resolution_also_applies_to_async_generators(tmp_path):
    client = FoxyClient(api_key="foxy_sk_test", spool_path=str(tmp_path / "s.sqlite3"),
                        desktop_ping=False)

    @client.audit(policy="hipaa")
    async def stream(prompt):
        yield "ok"

    async def drain():
        return [chunk async for chunk in stream(prompt=PHI)]

    assert asyncio.run(drain()) == ["ok"]
    _seed_org(client.cfg, "block")
    with pytest.raises(FoxyPolicyBlocked):
        asyncio.run(drain())


# ══ 9 · the kill switch ════════════════════════════════════════════════════

def test_org_policy_off_attempts_no_fetch(tmp_path, monkeypatch):
    monkeypatch.setenv("FOXY_ORG_POLICY", "off")
    cfg = _cfg(tmp_path)
    assert cfg.org_policy_enabled is False

    called = []
    monkeypatch.setattr(org_policy, "_refresh", lambda *a, **kw: called.append(1))
    org_policy.register(cfg)
    org_policy.tick()
    assert called == [], "the kill switch did not stop the fetch"
    with org_policy._lock:
        assert org_policy._registry == {}


def test_org_policy_off_ignores_even_a_cached_policy(tmp_path, monkeypatch):
    """Off means off — a cache written while it was on must not keep applying."""
    cfg_on = _cfg(tmp_path)
    _seed_org(cfg_on, "block")
    monkeypatch.setenv("FOXY_ORG_POLICY", "off")
    cfg_off = _cfg(tmp_path)
    assert org_policy.org_mode(cfg_off) is None
    assert org_policy.resolve(cfg_off, None) == ("observe", False)


def test_the_kill_switch_cannot_weaken_local_enforcement(tmp_path, monkeypatch):
    """Switching org policy off is not a bypass: it can only ever land on what
    the code already said, never below it."""
    monkeypatch.setenv("FOXY_ORG_POLICY", "off")
    cfg = _cfg(tmp_path, mode="block")
    assert org_policy.resolve(cfg, None)[0] == "block"


@pytest.mark.parametrize("value,expected", [
    ("off", False), ("0", False), ("false", False), ("no", False), ("OFF", False),
    ("on", True), ("1", True), ("true", True), ("anything-else", True),
])
def test_kill_switch_parsing(tmp_path, monkeypatch, value, expected):
    monkeypatch.setenv("FOXY_ORG_POLICY", value)
    assert _cfg(tmp_path).org_policy_enabled is expected


# ══ TTL ════════════════════════════════════════════════════════════════════

def test_the_default_ttl_is_five_minutes(tmp_path):
    assert _cfg(tmp_path).org_policy_ttl == 300.0


def test_a_fresh_entry_is_not_refetched(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    org_policy.register(cfg)
    _seed_org(cfg, "block")
    calls = []
    monkeypatch.setattr(org_policy, "_refresh", lambda *a, **kw: calls.append(1))
    org_policy.tick()
    assert calls == [], "a fresh policy was refetched inside its TTL"


def test_an_expired_entry_is_refetched(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, org_policy_ttl=1.0)
    org_policy.register(cfg)
    _seed_org(cfg, "block", fetched_at=0.0)
    calls = []
    monkeypatch.setattr(org_policy, "_refresh", lambda *a, **kw: calls.append(1))
    org_policy.tick()
    assert calls == [1]


# ══ B6 · a developer can find out why ══════════════════════════════════════

def test_an_org_block_explains_itself_in_the_exception(tmp_path):
    """Seeing FoxyPolicyBlocked on code that says observe, with no explanation,
    sends a developer looking in entirely the wrong place."""
    client = FoxyClient(api_key="foxy_sk_test", spool_path=str(tmp_path / "s.sqlite3"),
                        desktop_ping=False)

    @client.audit(policy="hipaa")
    def call(prompt):
        return "ok"

    _seed_org(client.cfg, "block")
    with pytest.raises(FoxyPolicyBlocked) as caught:
        call(prompt=PHI)
    message = str(caught.value)
    assert "workspace policy" in message
    assert "FOXY_ORG_POLICY=off" in message
    assert PHI not in message, "the exception leaked prompt content"
    # …and it must name the field that actually produced the block. This said
    # `enforcement_mode=block` for two releases, which is the judge-response
    # setting the SDK has never read — an explanation that sends the developer
    # to the wrong control is the same defect as no explanation at all.
    assert "sdk_enforcement" in message
    assert "enforcement_mode" not in message


def test_a_local_block_does_not_blame_the_org(tmp_path):
    client = FoxyClient(api_key="foxy_sk_test", spool_path=str(tmp_path / "s.sqlite3"),
                        desktop_ping=False, mode="block")

    @client.audit(policy="hipaa")
    def call(prompt):
        return "ok"

    with pytest.raises(FoxyPolicyBlocked) as caught:
        call(prompt=PHI)
    assert "workspace policy" not in str(caught.value)


def test_the_event_records_where_the_decision_came_from(tmp_path, monkeypatch):
    client = FoxyClient(api_key="foxy_sk_test", spool_path=str(tmp_path / "s.sqlite3"),
                        desktop_ping=False)
    recorded = {}
    monkeypatch.setattr(client, "log_interaction",
                        lambda *a, **kw: recorded.update(kw))

    @client.audit(policy="hipaa")
    def call(prompt):
        return "ok"

    _seed_org(client.cfg, "block")
    with pytest.raises(FoxyPolicyBlocked):
        call(prompt=PHI)
    assert recorded.get("decision") == "blocked_by_org_policy"


# ══ the fetch itself ═══════════════════════════════════════════════════════

class _Resp:
    def __init__(self, payload, status=200):
        self._p, self.status_code = payload, status

    def json(self):
        return self._p


@pytest.mark.parametrize("value,expected", [
    ("block", "block"), ("redact", "redact"), ("observe", "observe"),
    ("BLOCK", "block"), ("  redact  ", "redact"),
])
def test_the_fetch_reads_sdk_enforcement(tmp_path, monkeypatch, value, expected):
    """Parametrised rather than looped: a loop shares one tmp_path, so the disk
    cache written by the first value seeds the next iteration and the fetch is
    correctly skipped as fresh — which looks like a bug and is not."""
    cfg = _cfg(tmp_path)
    org_policy.register(cfg)
    captured = {}

    class _R:
        @staticmethod
        def get(url, headers=None, timeout=None):
            captured["url"], captured["headers"] = url, headers
            return _Resp({"enforcement_mode": "block", "sdk_enforcement": value})

    monkeypatch.setitem(__import__("sys").modules, "requests", _R)
    org_policy.tick()
    assert org_policy.org_mode(cfg) == expected
    assert captured["url"].endswith("/v1/policies")
    assert captured["headers"]["Authorization"].startswith("Bearer ")


# ══ the upgrade incident this field exists to prevent ══════════════════════

def test_a_null_sdk_enforcement_is_the_same_as_no_org_policy(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    org_policy.register(cfg)

    class _R:
        @staticmethod
        def get(*a, **kw):
            return _Resp({"enforcement_mode": "block", "sdk_enforcement": None})

    monkeypatch.setitem(__import__("sys").modules, "requests", _R)
    org_policy.tick()
    assert org_policy.org_mode(cfg) is None
    assert org_policy.resolve(cfg, None) == ("observe", False)
    assert org_policy.resolve(cfg, "block") == ("block", False)


def test_a_null_answer_is_cached_rather_than_refetched_every_tick(tmp_path, monkeypatch):
    """"No opinion" is an ANSWER, not a failure to get one.

    If it is not cached, the org falls through to the unrecognised-value path,
    the cache stays empty, and the TTL check has nothing to compare against — so
    the dispatcher refetches /v1/policies on every tick, once a second, forever.
    For the NULL state that every workspace starts in, that is every customer."""
    cfg = _cfg(tmp_path)
    org_policy.register(cfg)
    calls = []

    class _R:
        @staticmethod
        def get(*a, **kw):
            calls.append(1)
            return _Resp({"enforcement_mode": "block", "sdk_enforcement": None})

    monkeypatch.setitem(__import__("sys").modules, "requests", _R)
    org_policy.tick()
    assert calls == [1]
    org_policy.tick()
    org_policy.tick()
    assert calls == [1], (
        "a null policy was refetched inside its TTL — every tick hits the API")
    assert org_policy.org_mode(cfg) is None


def test_a_null_answer_also_survives_a_restart(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    org_policy.register(cfg)

    class _R:
        @staticmethod
        def get(*a, **kw):
            return _Resp({"sdk_enforcement": None})

    monkeypatch.setitem(__import__("sys").modules, "requests", _R)
    org_policy.tick()
    org_policy._reset_for_tests()
    org_policy.register(cfg)
    with org_policy._lock:
        assert org_policy._key(cfg) in org_policy._cache,             "the cached no-opinion answer was lost on restart"


def test_an_existing_org_upgrading_sees_no_change_at_all(tmp_path, monkeypatch):
    """THE INCIDENT TEST, and the most important one on this branch.

    enforcement_mode used to default to "block" and a default policy row is
    written on first read, so EVERY existing workspace stored "block" whether or
    not a human ever chose it. Had the SDK honoured that field, every customer
    running the default `observe` would have started raising FoxyPolicyBlocked
    the moment they installed this version — a production incident on upgrade,
    for everyone at once, caused by a setting nobody touched.

    Backend migration 0059 has since moved that default to "flag", which changes
    nothing here and is exactly the point: a workspace can serve ANY
    enforcement_mode and this SDK must still ignore it. The org below is the
    original worst case — enforcement_mode=block, sdk_enforcement NULL."""
    client = FoxyClient(api_key="foxy_sk_test", spool_path=str(tmp_path / "s.sqlite3"),
                        desktop_ping=False)

    class _R:
        @staticmethod
        def get(*a, **kw):
            return _Resp({"enforcement_mode": "block", "confidence_threshold": "balanced",
                          "sdk_enforcement": None})

    monkeypatch.setitem(__import__("sys").modules, "requests", _R)
    org_policy.tick()

    @client.audit(policy="hipaa")
    def call(prompt):
        return "ok"

    assert call(prompt=PHI) == "ok", (
        "an org that never chose an SDK mode started blocking on upgrade")
    assert org_policy.resolve(client.cfg, None) == ("observe", False)


def test_a_deliberate_choice_does_take_effect(tmp_path, monkeypatch):
    """The other half: once an owner actually picks one, it works."""
    client = FoxyClient(api_key="foxy_sk_test", spool_path=str(tmp_path / "s.sqlite3"),
                        desktop_ping=False)

    class _R:
        @staticmethod
        def get(*a, **kw):
            return _Resp({"enforcement_mode": "monitor", "sdk_enforcement": "block"})

    monkeypatch.setitem(__import__("sys").modules, "requests", _R)
    org_policy.tick()

    @client.audit(policy="hipaa")
    def call(prompt):
        return "ok"

    with pytest.raises(FoxyPolicyBlocked):
        call(prompt=PHI)


def test_the_two_fields_are_independent(tmp_path, monkeypatch):
    """enforcement_mode moving must not move sdk_enforcement, in either
    direction — they answer different questions at different moments."""
    cfg = _cfg(tmp_path, org_policy_ttl=0.0001)
    org_policy.register(cfg)
    for judge_value in ("block", "flag", "monitor"):
        class _R:
            @staticmethod
            def get(*a, _j=judge_value, **kw):
                return _Resp({"enforcement_mode": _j, "sdk_enforcement": None})

        monkeypatch.setitem(__import__("sys").modules, "requests", _R)
        with org_policy._lock:
            org_policy._cache.pop(org_policy._key(cfg), None)
        org_policy.tick()
        assert org_policy.org_mode(cfg) is None, (
            f"enforcement_mode={judge_value} leaked into SDK enforcement")


def test_an_unknown_value_is_ignored_rather_than_guessed(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    org_policy.register(cfg)

    class _R:
        @staticmethod
        def get(*a, **kw):
            return _Resp({"sdk_enforcement": "something_new"})

    monkeypatch.setitem(__import__("sys").modules, "requests", _R)
    org_policy.tick()
    assert org_policy.org_mode(cfg) is None


def test_a_non_200_leaves_the_cache_alone(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, org_policy_ttl=0.01)
    org_policy.register(cfg)
    _seed_org(cfg, "block", fetched_at=0.0)

    class _R:
        @staticmethod
        def get(*a, **kw):
            return _Resp({}, status=503)

    monkeypatch.setitem(__import__("sys").modules, "requests", _R)
    org_policy.tick()
    assert org_policy.org_mode(cfg) == "block"
