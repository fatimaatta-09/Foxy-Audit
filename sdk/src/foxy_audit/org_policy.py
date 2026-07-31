"""Org-side enforcement policy, fetched in the background and cached (P4 §B).

The workspace can tighten what the SDK enforces without a redeploy. Three
properties make that safe, and each one is a test:

* **It never blocks the caller.** The fetch happens on the dispatcher's existing
  thread, never on a call path. No cache and no network means the SDK behaves
  exactly as it does today. A fetch that fails keeps serving the last known
  policy rather than reverting.
* **It can only ever tighten to BLOCK.** An org can raise an `observe` service to
  `block`, and can fill in a mode where the code specified none. It can never
  turn an explicit local mode into `redact`, and it can never weaken anything.
  See :func:`resolve` for why redact is singled out.
* **It can be switched off.** ``FOXY_ORG_POLICY=off`` stops the fetch entirely,
  for air-gapped deployments or teams who want code to be the only source of
  truth. That can only make enforcement weaker-or-equal to what the code already
  says, so it is not a way to bypass local blocking.

Content-blindness holds: ``GET /v1/policies`` carries configuration, never any
prompt or response text, and nothing here touches interaction content.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

from .config import FoxyConfig

log = logging.getLogger("foxy_audit.org_policy")

# The SDK's preflight vocabulary. The org's vocabulary is DIFFERENT — see below.
SDK_MODES = ("observe", "redact", "block")
DEFAULT_MODE = "observe"

# The SDK reads `sdk_enforcement`, NOT `enforcement_mode`. They are different
# settings that happen to share a word:
#
#   enforcement_mode   block|flag|monitor   what to do with a verdict AFTER the
#                                           judge grades an interaction
#   sdk_enforcement    observe|redact|block what to do BEFORE the model is called
#
# Conflating them would have been an upgrade incident rather than a feature.
# enforcement_mode USED TO default to "block", and a default policy row is
# written on the org's first read, so every workspace stored "block" whether or
# not a human chose it. Honouring that field would have started blocking prompts
# for every customer running the default `observe` the moment they upgraded.
#
# Backend migration 0059 has since moved that default to "flag" and flipped the
# rows that only ever inherited it — for the same reason read the other way
# round: enforcement_mode now decides whether a graded breach emails a human, so
# "block" has to be something an owner chose rather than something a column
# default handed them. Neither default has ever meant anything here. This is not
# the field the SDK reads, before the change or after it, and the only safe way
# to keep it that way is to keep reading it by name.
#
# sdk_enforcement is nullable with no default. NULL — which is what every
# existing workspace reads — means "no opinion", and the SDK ignores org policy
# entirely. Nothing changes for anyone until an owner deliberately chooses.
SDK_ENFORCEMENT_FIELD = "sdk_enforcement"

_lock = threading.Lock()
_cache: dict[tuple[str, str], dict] = {}      # (endpoint, api_key) -> {mode, fetched_at}
_registry: dict[tuple[str, str], FoxyConfig] = {}


def _key(cfg: FoxyConfig) -> tuple[str, str]:
    return (cfg.endpoint, cfg.api_key)


def _cache_file(cfg: FoxyConfig) -> Path:
    """Beside the spool, for the same durability reason the spool has one: a
    restarted process honours org policy immediately instead of reverting to
    local defaults for a whole TTL."""
    from .spool import default_path
    return Path(cfg.spool_path or default_path()).parent / "org_policy.json"


# ── the resolution table (P4 §B, owner decision 2026-07-29) ─────────────────

def resolve(cfg: FoxyConfig, decorator_mode: str | None) -> tuple[str, bool]:
    """Return ``(effective_mode, org_tightened)`` for ONE call.

    Called per invocation rather than at decoration time, so a tightening
    reaches a long-running process without a restart. The cost is a dict read.

    The rule is NOT "strictest wins", and the difference is the whole point:

    ======================  ==========  ==================================
    local                   org         effective
    ======================  ==========  ==================================
    anything                block       block      (the org may always tighten)
    unset                   anything    the org's  (filling a gap, not overriding)
    anything explicit       not block   the local  (code wins)
    ======================  ==========  ==================================

    So ``local=observe`` + ``org=redact`` stays **observe**. Block raises
    :class:`FoxyPolicyBlocked` — loud and impossible to miss. Redact rewrites the
    prompt before the model sees it, changing what that model receives and
    degrading its output while raising nothing at all. An org must not be able to
    do that to a service whose code explicitly asked to observe.

    ``local=block`` + ``org=monitor`` stays **block**: nothing here ever weakens
    what the code asked for.
    """
    local = decorator_mode if decorator_mode is not None else (
        cfg.mode if getattr(cfg, "mode_is_explicit", False) else None)
    if local is not None:
        local = str(local).strip().lower()
        if local not in SDK_MODES:
            local = DEFAULT_MODE

    org = org_mode(cfg)

    if org == "block" and local != "block":
        return "block", True
    if local is None:
        if org is None:
            return DEFAULT_MODE, False
        return org, org != DEFAULT_MODE
    return local, False


def org_mode(cfg: FoxyConfig) -> str | None:
    """The cached org mode in SDK vocabulary, or None when nothing is known.

    None is the honest answer for "no cache and no successful fetch", and it is
    what makes the cold-start path byte-identical to today's behaviour."""
    if not cfg.org_policy_enabled:
        return None
    with _lock:
        entry = _cache.get(_key(cfg))
    if not entry:
        return None
    mode = entry.get("mode")
    # A cached None means the workspace expressed no opinion; that is the same
    # answer as "nothing known" for every caller, and both mean the SDK behaves
    # exactly as it did before org policy existed.
    return mode if mode in SDK_MODES else None


# ── registration + the disk cache ──────────────────────────────────────────

def register(cfg: FoxyConfig) -> None:
    """Make this client's policy eligible for background refresh, and seed the
    in-memory cache from disk so a restart honours the org immediately."""
    if not cfg.org_policy_enabled or not cfg.api_key:
        return
    key = _key(cfg)
    with _lock:
        if key in _registry:
            return
        _registry[key] = cfg
    _load_from_disk(cfg)


def _load_from_disk(cfg: FoxyConfig) -> None:
    try:
        raw = json.loads(_cache_file(cfg).read_text(encoding="utf-8"))
        entry = raw.get(_disk_key(cfg))
        # None is a legitimate cached value — "this workspace has no opinion" —
        # and is as worth restoring as a mode, so a restart does not re-ask.
        if isinstance(entry, dict) and (entry.get("mode") in SDK_MODES
                                        or entry.get("mode") is None):
            with _lock:
                _cache.setdefault(_key(cfg), {"mode": entry.get("mode"),
                                              "fetched_at": float(entry.get("fetched_at") or 0.0)})
    except Exception as exc:                 # noqa: BLE001 — a bad cache is not fatal
        log.debug("foxy-audit: org policy cache unreadable (%s)", type(exc).__name__)


def _disk_key(cfg: FoxyConfig) -> str:
    """Endpoint plus a short fingerprint of the key — never the key itself."""
    import hashlib
    digest = hashlib.sha256(cfg.api_key.encode("utf-8")).hexdigest()[:16]
    return f"{cfg.endpoint}|{digest}"


def _save_to_disk(cfg: FoxyConfig, mode: str | None, fetched_at: float) -> None:
    path = _cache_file(cfg)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:                    # noqa: BLE001 — start fresh
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        raw[_disk_key(cfg)] = {"mode": mode, "fetched_at": fetched_at}
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(raw), encoding="utf-8")
        tmp.replace(path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except Exception as exc:                 # noqa: BLE001 — caching is best-effort
        log.debug("foxy-audit: could not write org policy cache (%s)", type(exc).__name__)


# ── the refresh, on the dispatcher's thread ────────────────────────────────

def tick(now: float | None = None) -> None:
    """Refresh any registered policy whose TTL has expired.

    Called from the dispatcher's existing loop — deliberately NOT its own thread.
    Everything here is best-effort: an exception must never escape into the
    dispatcher and stop event delivery."""
    now = time.time() if now is None else now
    with _lock:
        pending = list(_registry.items())
    for key, cfg in pending:
        with _lock:
            entry = _cache.get(key)
        age = now - float(entry["fetched_at"]) if entry else None
        if age is not None and age < cfg.org_policy_ttl:
            continue
        _refresh(cfg, now)


def _refresh(cfg: FoxyConfig, now: float) -> None:
    """Fetch once. On any failure the previous entry is left untouched — a stale
    policy is strictly better than silently reverting to the local default."""
    try:
        import requests
        resp = requests.get(
            f"{cfg.endpoint}/v1/policies",
            headers={"Authorization": f"Bearer {cfg.api_key}"},
            timeout=cfg.timeout,
        )
        if resp.status_code != 200:
            log.debug("foxy-audit: org policy fetch returned %s", resp.status_code)
            return
        payload = resp.json() or {}
        value = payload.get(SDK_ENFORCEMENT_FIELD)
        if value is None:
            # An explicit "no opinion" is a RESULT, not a failure to get one.
            # Cache it, or the TTL check has nothing to compare against and the
            # dispatcher refetches on every tick — once a second, forever, for
            # the NULL state that every workspace starts in.
            with _lock:
                _cache[_key(cfg)] = {"mode": None, "fetched_at": now}
            _save_to_disk(cfg, None, now)
            return
        raw = str(value).strip().lower()
    except Exception as exc:                 # noqa: BLE001
        # Type name only. The key is a Bearer header here rather than a URL
        # param, but the habit holds — an exception message can carry the
        # request, and this one is authenticated.
        log.debug("foxy-audit: org policy fetch failed (%s)", type(exc).__name__)
        return
    mode = raw if raw in SDK_MODES else None
    if mode is None:
        log.debug("foxy-audit: unrecognised sdk_enforcement value; ignoring")
        return
    with _lock:
        _cache[_key(cfg)] = {"mode": mode, "fetched_at": now}
    _save_to_disk(cfg, mode, now)


def _reset_for_tests() -> None:
    with _lock:
        _cache.clear()
        _registry.clear()
