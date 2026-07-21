"""FoxyClient — the developer-facing decorator.

`@client.audit(policy)` wraps any function (sync or async) that calls an LLM.
After the wrapped function returns, the SDK:

  1. Creates customer-keyed HMAC-SHA-256 commitments of the prompt and response
     locally, then discards the raw text.
  2. Fires best-effort `evaluating` then `hash_ok` UDP pings to the desktop fox.
     These confirm only local hashing and queueing — not a backend verdict.
  3. Enqueues the metadata for background HTTP delivery to the backend (only
     when an API key is configured).

The wrapped function's own return value is always passed through unchanged, and
the SDK's own bookkeeping can never raise into the host application.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import logging
import re
import uuid

from . import dispatch, hashing, pii, udp
from . import policy as policy_engine
from .adapters import response_metadata
from .config import FoxyConfig
from .spool import EventSpool

log = logging.getLogger("foxy_audit")

_POLICY_RE = re.compile(r"^[a-z0-9_]{1,32}$")
_MODES = ("observe", "block", "redact")
_PROMPT_KWARGS = ("prompt", "user_prompt", "message", "messages", "contents",
                  "text", "input", "query")


class AuditRequiredError(RuntimeError):
    """Raised only when audit_required is enabled and durable delivery fails."""


class FoxyPolicyBlocked(RuntimeError):
    """Raised when the preflight guard blocks a prompt BEFORE the wrapped
    function runs (mode="block"). The wrapped LLM call is never made; a
    ``blocked`` audit event is emitted first. Content-blind: the message carries
    only a policy tag and a short reason label, never the offending text."""


def _extract_prompt(args: tuple, kwargs: dict):
    """Best-effort extraction that preserves structured provider messages locally."""
    for key in _PROMPT_KWARGS:
        val = kwargs.get(key)
        if val is not None:
            return val
    for arg in args:
        if isinstance(arg, str):
            return arg
    return ""


def _replace_prompt(args: tuple, kwargs: dict, new_prompt: str):
    """Return (args, kwargs) with the string prompt swapped for ``new_prompt``.

    Mirrors :func:`_extract_prompt`'s search order but only substitutes into
    string-valued slots, so structured provider messages are never clobbered by
    a redacted string. Used only on the redact path."""
    for key in _PROMPT_KWARGS:
        if isinstance(kwargs.get(key), str):
            new_kwargs = dict(kwargs)
            new_kwargs[key] = new_prompt
            return args, new_kwargs
    new_args = list(args)
    for i, arg in enumerate(new_args):
        if isinstance(arg, str):
            new_args[i] = new_prompt
            return tuple(new_args), kwargs
    return args, kwargs


class FoxyClient:
    def __init__(
        self,
        api_key: str | None = None,
        endpoint: str | None = None,
        udp_host: str | None = None,
        udp_port: int | None = None,
        desktop_ping: bool = True,
        timeout: float = 5.0,
        commitment_key: str | None = None,
        spool_path: str | None = None,
        client_id: str | None = None,
        audit_required: bool | None = None,
        mode: str | None = None,
    ) -> None:
        self.cfg = FoxyConfig.resolve(
            api_key=api_key,
            endpoint=endpoint,
            udp_host=udp_host,
            udp_port=udp_port,
            desktop_ping=desktop_ping,
            timeout=timeout,
            commitment_key=commitment_key,
            spool_path=spool_path,
            client_id=client_id,
            audit_required=audit_required,
            mode=mode,
        )
        if self.cfg.enabled and not self.cfg.client_id:
            spool = EventSpool(self.cfg.spool_path or None)
            self.cfg = self.cfg.__class__(
                **{**self.cfg.__dict__,
                   "client_id": spool.get_or_create_client_id(
                       self.cfg.endpoint, self.cfg.api_key)}
            )
        if self.cfg.enabled:
            # Resume any events left in the local spool after a prior process exit.
            dispatch.resume(self.cfg)

    @property
    def enabled(self) -> bool:
        return self.cfg.enabled

    def _record_host_exception(self, prompt, exc, policy, agent, metadata=None):
        """Telemetry failure must never replace the application's exception."""
        try:
            self.log_interaction(prompt, exc, policy, agent,
                                 event_type="exception", metadata=metadata)
        except AuditRequiredError:
            log.debug("foxy-audit could not capture host exception", exc_info=True)

    async def _record_async(self, *args, **kwargs):
        """Keep synchronous hashing and delivery waits off the event loop."""
        return await asyncio.to_thread(self.log_interaction, *args, **kwargs)

    # ── preflight guard ───────────────────────────────────────────────────────
    def _evaluate_preflight(self, args, kwargs, policy: str, effective_mode: str):
        """Run policy BEFORE the wrapped fn. Pure (no side effects): returns a
        plan dict the wrappers act on, or ``None`` for the observe path.

        plan["kind"] is one of:
          "block"  — raise after emitting a blocked event (fn must not run)
          "redact" — call fn with plan["args"]/["kwargs"] (redacted prompt)
          "allow"  — clean prompt seen under block/redact mode; record decision
        """
        if effective_mode == "observe":
            return None
        prompt = _extract_prompt(args, kwargs)
        decision = policy_engine.evaluate(prompt, policy)
        if not decision.triggered:
            return {"kind": "allow", "hash_prompt": prompt, "args": args, "kwargs": kwargs,
                    "decision": "allowed", "rules": list(decision.rules),
                    "signals": None, "reason": None, "event_type": None}
        if effective_mode == "block":
            return {"kind": "block", "hash_prompt": prompt,
                    "rules": list(decision.rules), "signals": list(decision.signals),
                    "reason": decision.reason}
        redacted = policy_engine.redact(prompt, policy)
        new_args, new_kwargs = _replace_prompt(args, kwargs, redacted)
        return {"kind": "redact", "hash_prompt": prompt, "args": new_args, "kwargs": new_kwargs,
                "decision": "redacted", "rules": list(decision.rules),
                "signals": list(decision.signals), "reason": decision.reason,
                "event_type": "redacted"}

    def _emit_block(self, plan: dict, policy: str, agent: str | None) -> None:
        """Emit the blocked audit event and fire the desktop policy_breach ping.

        prompt_hash = commitment of the ORIGINAL prompt; response_hash =
        commitment of "" (the fn never ran, so there is no response)."""
        self.log_interaction(plan["hash_prompt"], "", policy, agent,
                             event_type="blocked", decision="blocked",
                             policy_rules=plan["rules"], signals=plan["signals"],
                             blocked_reason=plan["reason"])
        if self.cfg.desktop_ping:
            udp.send_ping(
                {"event": "policy_breach", "policy": policy,
                 "reason": plan["reason"], "rules": plan["rules"][:8],
                 "decision": "blocked"},
                self.cfg.udp_host, self.cfg.udp_port,
            )

    def audit(self, policy: str = "default", agent: str | None = None,
              mode: str | None = None):
        """Return a decorator that audits the wrapped LLM-calling function.

        `agent` records which model/agent produced the interaction (e.g.
        "gpt-4o", "claude-3-opus"); the backend folds it into the tamper-evident
        hash chain so it can't be altered after the fact (6B).

        `mode` overrides the client's configured mode for this decorator:
        "observe" (default — unchanged: run the fn, then hash), "block"
        (evaluate the prompt FIRST and raise ``FoxyPolicyBlocked`` without ever
        calling the fn on a violation) or "redact" (scrub the prompt locally,
        then call the fn with the redacted prompt)."""
        if not _POLICY_RE.match(policy):
            log.warning("foxy-audit: invalid policy tag %r; falling back to 'default'", policy)
            policy = "default"
        effective_mode = str(mode if mode is not None else self.cfg.mode or "observe").strip().lower()
        if effective_mode not in _MODES:
            log.warning("foxy-audit: invalid mode %r; falling back to 'observe'", effective_mode)
            effective_mode = "observe"

        def decorator(fn):
            if inspect.iscoroutinefunction(fn):
                @functools.wraps(fn)
                async def awrapper(*args, **kwargs):
                    plan = self._evaluate_preflight(args, kwargs, policy, effective_mode)
                    if plan and plan["kind"] == "block":
                        await asyncio.to_thread(self._emit_block, plan, policy, agent)
                        raise FoxyPolicyBlocked(_block_message(policy, plan))
                    call_args = plan["args"] if plan else args
                    call_kwargs = plan["kwargs"] if plan else kwargs
                    try:
                        response = await fn(*call_args, **call_kwargs)
                    except BaseException as exc:
                        try:
                            await self._record_async(_extract_prompt(args, kwargs), exc,
                                                     policy, agent, event_type="exception")
                        except AuditRequiredError:
                            log.debug("foxy-audit could not capture async host exception",
                                      exc_info=True)
                        raise
                    await self._record_async(
                        plan["hash_prompt"] if plan else _extract_prompt(args, kwargs),
                        response, policy, agent, metadata=_metadata(kwargs, response),
                        event_type=(plan and plan["event_type"]) or "interaction",
                        decision=plan["decision"] if plan else None,
                        policy_rules=plan["rules"] if plan else None,
                        signals=plan["signals"] if plan else None,
                        blocked_reason=plan["reason"] if plan else None)
                    return response
                return awrapper

            if inspect.isasyncgenfunction(fn):
                @functools.wraps(fn)
                async def agen_wrapper(*args, **kwargs):
                    plan = self._evaluate_preflight(args, kwargs, policy, effective_mode)
                    if plan and plan["kind"] == "block":
                        await asyncio.to_thread(self._emit_block, plan, policy, agent)
                        raise FoxyPolicyBlocked(_block_message(policy, plan))
                    call_args = plan["args"] if plan else args
                    call_kwargs = plan["kwargs"] if plan else kwargs
                    chunks = []
                    try:
                        async for chunk in fn(*call_args, **call_kwargs):
                            chunks.append(chunk)
                            yield chunk
                    except BaseException as exc:
                        try:
                            await self._record_async(_extract_prompt(args, kwargs), exc,
                                                     policy, agent, event_type="exception",
                                                     metadata=_metadata(kwargs))
                        except AuditRequiredError:
                            log.debug("foxy-audit could not capture async stream exception",
                                      exc_info=True)
                        raise
                    await self._record_async(
                        plan["hash_prompt"] if plan else _extract_prompt(args, kwargs),
                        chunks, policy, agent, metadata=_metadata(kwargs),
                        event_type=(plan and plan["event_type"]) or "stream",
                        decision=plan["decision"] if plan else None,
                        policy_rules=plan["rules"] if plan else None,
                        signals=plan["signals"] if plan else None,
                        blocked_reason=plan["reason"] if plan else None)
                return agen_wrapper

            @functools.wraps(fn)
            def wrapper(*args, **kwargs):
                plan = self._evaluate_preflight(args, kwargs, policy, effective_mode)
                if plan and plan["kind"] == "block":
                    self._emit_block(plan, policy, agent)
                    raise FoxyPolicyBlocked(_block_message(policy, plan))
                call_args = plan["args"] if plan else args
                call_kwargs = plan["kwargs"] if plan else kwargs
                try:
                    response = fn(*call_args, **call_kwargs)
                except BaseException as exc:
                    self._record_host_exception(_extract_prompt(args, kwargs), exc,
                                                policy, agent, _metadata(kwargs))
                    raise
                hash_prompt = plan["hash_prompt"] if plan else _extract_prompt(args, kwargs)
                decision = plan["decision"] if plan else None
                rules = plan["rules"] if plan else None
                signals = plan["signals"] if plan else None
                reason = plan["reason"] if plan else None
                event_override = (plan and plan["event_type"]) or None
                if inspect.isgenerator(response):
                    def generator():
                        chunks = []
                        try:
                            for chunk in response:
                                chunks.append(chunk)
                                yield chunk
                        except BaseException as exc:
                            self._record_host_exception(_extract_prompt(args, kwargs), exc,
                                                        policy, agent, _metadata(kwargs))
                            raise
                        self.log_interaction(hash_prompt, chunks, policy, agent,
                                             metadata=_metadata(kwargs),
                                             event_type=event_override or "stream",
                                             decision=decision, policy_rules=rules,
                                             signals=signals, blocked_reason=reason)
                    return generator()
                self.log_interaction(hash_prompt, response, policy, agent,
                                     metadata=_metadata(kwargs, response),
                                     event_type=event_override or "interaction",
                                     decision=decision, policy_rules=rules,
                                     signals=signals, blocked_reason=reason)
                return response
            return wrapper

        return decorator

    # ── internal ──────────────────────────────────────────────────────────
    def log_interaction(self, prompt, response, policy: str, agent: str | None = None,
                        metadata: dict | None = None, event_type: str = "interaction",
                        decision: str | None = None, policy_rules=None,
                        signals=None, blocked_reason: str | None = None):
        """Perform cryptographic hashing synchronously and push to AsyncDispatcher.

        The preflight guard passes ``decision`` ("allowed"|"blocked"|"redacted"),
        the fired ``signals`` (used verbatim as ``pii_signals``), the matched
        ``policy_rules`` and the dominant ``blocked_reason``. When ``decision`` is
        None the emitted payload is byte-for-byte identical to the observe path.
        """
        try:
            prompt_s = hashing.canonical_json(prompt)
            response_s = hashing.canonical_json(response)
            key = self.cfg.commitment_key or self.cfg.api_key
            if key:
                prompt_hash = hashing.commitment_hex(prompt, key)
                response_hash = hashing.commitment_hex(response, key)
                commitment_alg = "hmac-sha256"
            else:
                prompt_hash = hashing.sha256_hex(prompt_s)
                response_hash = hashing.sha256_hex(response_s)
                commitment_alg = "sha256-legacy"

            # The backend owns the tamper-evident hash chain (it re-derives each
            # link server-side), so the SDK only ships the per-interaction hashes;
            # a client-side chain_hash/timestamp would just be ignored.
            payload = {
                "event_id": str(uuid.uuid4()),
                "client_id": self.cfg.client_id,
                "event_type": event_type,
                "commitment_alg": commitment_alg,
                "prompt_hash": prompt_hash,
                "response_hash": response_hash,
                "token_count": hashing.estimate_tokens(prompt_s, response_s),
                "policy_tag": policy,
                # On the guard path pii_signals carries the exact signals that
                # fired; otherwise the historical prompt+response detection.
                "pii_signals": signals if signals is not None else pii.detect_pii(prompt_s, response_s),
            }
            if agent:
                payload["agent"] = agent
            if decision is not None:
                # Thread the preflight decision into event_metadata without
                # disturbing the observe path (decision is None there).
                meta = dict(metadata) if metadata else {}
                meta["decision"] = decision
                meta["policy_rules"] = list(policy_rules or [])
                if blocked_reason is not None:
                    meta["blocked_reason"] = blocked_reason
                payload["event_metadata"] = meta
            elif metadata:
                payload["event_metadata"] = metadata
            # raw text goes out of scope here — never stored or transmitted

            if self.cfg.desktop_ping:
                udp.send_ping(
                    {"event": "evaluating", "policy": policy, "tokens": payload["token_count"]},
                    self.cfg.udp_host,
                    self.cfg.udp_port,
                )
            result = None
            if self.cfg.enabled:
                result = (dispatch.submit(self.cfg, payload, wait=True)
                          if self.cfg.audit_required else dispatch.submit(self.cfg, payload))
            if self.cfg.desktop_ping:
                # This confirms only local hashing and queueing. It is not a
                # backend receipt or a policy verdict.
                udp.send_ping(
                    {
                        "event": "hash_ok",
                        "policy": policy,
                        "tokens": payload["token_count"],
                        "delivery": "queued" if self.cfg.enabled else "local_only",
                    },
                    self.cfg.udp_host,
                    self.cfg.udp_port,
                )
            if self.cfg.enabled:
                return result
        except Exception as exc:  # telemetry must never break the host app
            log.debug("foxy-audit observe error: %s", exc)
            if self.cfg.audit_required:
                raise AuditRequiredError("Foxy Audit could not durably deliver the event") from exc


def _block_message(policy: str, plan: dict) -> str:
    """Content-blind exception message: policy tag + short reason label only."""
    return (f"Foxy Audit blocked a prompt under policy '{policy}' "
            f"(reason: {plan['reason']}). The wrapped function was not called.")


def _metadata(kwargs: dict, response=None) -> dict:
    """Keep only non-content identifiers useful to an auditor."""
    allowed = ("request_id", "trace_id", "session_id", "provider", "model",
               "tool_names", "retrieval_refs")
    result = {}
    for key in allowed:
        value = kwargs.get(key)
        if value is None:
            continue
        if key in {"tool_names", "retrieval_refs"}:
            result[key] = [str(v)[:128] for v in value] if isinstance(value, (list, tuple)) else [str(value)[:128]]
        else:
            result[key] = str(value)[:256]
    result.update(response_metadata(response) if response is not None else {})
    return result
