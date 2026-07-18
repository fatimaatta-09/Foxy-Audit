"""FoxyClient — the developer-facing decorator.

`@client.audit(policy)` wraps any function (sync or async) that calls an LLM.
After the wrapped function returns, the SDK:

  1. SHA-256-hashes the prompt and response locally, then discards the raw text.
  2. Fires an instant best-effort `hash_ok` UDP ping to the desktop fox.
  3. Enqueues the metadata for background HTTP delivery to the backend (only
     when an API key is configured).

The wrapped function's own return value is always passed through unchanged, and
the SDK's own bookkeeping can never raise into the host application.
"""

from __future__ import annotations

import functools
import inspect
import logging
import re
import uuid

from . import dispatch, hashing, pii, udp
from .config import FoxyConfig

log = logging.getLogger("foxy_audit")

_POLICY_RE = re.compile(r"^[a-z0-9_]{1,32}$")
_PROMPT_KWARGS = ("prompt", "user_prompt", "message", "text", "input", "query")


class AuditRequiredError(RuntimeError):
    """Raised only when audit_required is enabled and durable delivery fails."""


def _extract_prompt(args: tuple, kwargs: dict) -> str:
    """Best-effort: the first known prompt kwarg, else the first string arg."""
    for key in _PROMPT_KWARGS:
        val = kwargs.get(key)
        if isinstance(val, str):
            return val
    for arg in args:
        if isinstance(arg, str):
            return arg
    return ""


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
        )

    @property
    def enabled(self) -> bool:
        return self.cfg.enabled

    def audit(self, policy: str = "default", agent: str | None = None):
        """Return a decorator that audits the wrapped LLM-calling function.

        `agent` records which model/agent produced the interaction (e.g.
        "gpt-4o", "claude-3-opus"); the backend folds it into the tamper-evident
        hash chain so it can't be altered after the fact (6B)."""
        if not _POLICY_RE.match(policy):
            log.warning("foxy-audit: invalid policy tag %r; falling back to 'default'", policy)
            policy = "default"

        def decorator(fn):
            if inspect.iscoroutinefunction(fn):
                @functools.wraps(fn)
                async def awrapper(*args, **kwargs):
                    try:
                        response = await fn(*args, **kwargs)
                    except BaseException as exc:
                        self.log_interaction(_extract_prompt(args, kwargs), exc, policy, agent,
                                             event_type="exception")
                        raise
                    self.log_interaction(_extract_prompt(args, kwargs), response, policy, agent,
                                         metadata=_metadata(kwargs))
                    return response
                return awrapper

            if inspect.isasyncgenfunction(fn):
                @functools.wraps(fn)
                async def agen_wrapper(*args, **kwargs):
                    chunks = []
                    try:
                        async for chunk in fn(*args, **kwargs):
                            chunks.append(chunk)
                            yield chunk
                    except BaseException as exc:
                        self.log_interaction(_extract_prompt(args, kwargs), exc, policy, agent,
                                             event_type="exception", metadata=_metadata(kwargs))
                        raise
                    self.log_interaction(_extract_prompt(args, kwargs), chunks, policy, agent,
                                         metadata=_metadata(kwargs), event_type="stream")
                return agen_wrapper

            @functools.wraps(fn)
            def wrapper(*args, **kwargs):
                try:
                    response = fn(*args, **kwargs)
                except BaseException as exc:
                    self.log_interaction(_extract_prompt(args, kwargs), exc, policy, agent,
                                         event_type="exception", metadata=_metadata(kwargs))
                    raise
                if inspect.isgenerator(response):
                    def generator():
                        chunks = []
                        try:
                            for chunk in response:
                                chunks.append(chunk)
                                yield chunk
                        except BaseException as exc:
                            self.log_interaction(_extract_prompt(args, kwargs), exc, policy, agent,
                                                 event_type="exception", metadata=_metadata(kwargs))
                            raise
                        self.log_interaction(_extract_prompt(args, kwargs), chunks, policy, agent,
                                             metadata=_metadata(kwargs), event_type="stream")
                    return generator()
                self.log_interaction(_extract_prompt(args, kwargs), response, policy, agent,
                                     metadata=_metadata(kwargs))
                return response
            return wrapper

        return decorator

    # ── internal ──────────────────────────────────────────────────────────
    def log_interaction(self, prompt, response, policy: str, agent: str | None = None,
                        metadata: dict | None = None, event_type: str = "interaction"):
        """Perform cryptographic hashing synchronously and push to AsyncDispatcher."""
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
                "pii_signals": pii.detect_pii(prompt_s, response_s),
            }
            if agent:
                payload["agent"] = agent
            if metadata:
                payload["event_metadata"] = metadata
            # raw text goes out of scope here — never stored or transmitted

            if self.cfg.desktop_ping:
                udp.send_ping(
                    {"event": "evaluating", "policy": policy, "tokens": payload["token_count"]},
                    self.cfg.udp_host,
                    self.cfg.udp_port,
                )
            if self.cfg.enabled:
                if self.cfg.audit_required:
                    return dispatch.submit(self.cfg, payload, wait=True)
                return dispatch.submit(self.cfg, payload)
        except Exception as exc:  # telemetry must never break the host app
            log.debug("foxy-audit observe error: %s", exc)
            if self.cfg.audit_required:
                raise AuditRequiredError("Foxy Audit could not durably deliver the event") from exc


def _metadata(kwargs: dict) -> dict:
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
    return result
