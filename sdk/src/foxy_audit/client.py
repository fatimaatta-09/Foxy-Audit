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

from . import dispatch, hashing, pii, udp
from .config import FoxyConfig

log = logging.getLogger("foxy_audit")

_POLICY_RE = re.compile(r"^[a-z0-9_]{1,32}$")
_PROMPT_KWARGS = ("prompt", "user_prompt", "message", "text", "input", "query")


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
    ) -> None:
        self.cfg = FoxyConfig.resolve(
            api_key=api_key,
            endpoint=endpoint,
            udp_host=udp_host,
            udp_port=udp_port,
            desktop_ping=desktop_ping,
            timeout=timeout,
        )
        self.last_hash = "GENESIS_HASH"

    @property
    def enabled(self) -> bool:
        return self.cfg.enabled

    def audit(self, policy: str = "default"):
        """Return a decorator that audits the wrapped LLM-calling function."""
        if not _POLICY_RE.match(policy):
            log.warning("foxy-audit: invalid policy tag %r; falling back to 'default'", policy)
            policy = "default"

        def decorator(fn):
            if inspect.iscoroutinefunction(fn):
                @functools.wraps(fn)
                async def awrapper(*args, **kwargs):
                    response = await fn(*args, **kwargs)
                    self.log_interaction(_extract_prompt(args, kwargs), response, policy)
                    return response
                return awrapper

            @functools.wraps(fn)
            def wrapper(*args, **kwargs):
                response = fn(*args, **kwargs)
                self.log_interaction(_extract_prompt(args, kwargs), response, policy)
                return response
            return wrapper

        return decorator

    # ── internal ──────────────────────────────────────────────────────────
    def log_interaction(self, prompt, response, policy: str) -> None:
        """Perform cryptographic hashing synchronously and push to AsyncDispatcher."""
        import time
        try:
            prompt_s = "" if prompt is None else str(prompt)
            response_s = "" if response is None else str(response)
            timestamp = time.time()
            
            # Using self.last_hash, prompt, response, and timestamp synchronously
            combined = f"{self.last_hash}{prompt_s}{response_s}{timestamp}"
            self.last_hash = hashing.sha256_hex(combined)
            
            payload = {
                "prompt_hash": hashing.sha256_hex(prompt_s),
                "response_hash": hashing.sha256_hex(response_s),
                "token_count": hashing.estimate_tokens(prompt_s, response_s),
                "policy_tag": policy,
                "chain_hash": self.last_hash,
                "timestamp": timestamp,
                "pii_signals": pii.detect_pii(prompt_s, response_s),
            }
            # raw text goes out of scope here — never stored or transmitted

            if self.cfg.desktop_ping:
                udp.send_ping(
                    {"event": "hash_ok", "policy": policy, "tokens": payload["token_count"]},
                    self.cfg.udp_host,
                    self.cfg.udp_port,
                )
            if self.cfg.enabled:
                dispatch.submit(self.cfg, payload)
        except Exception as exc:  # telemetry must never break the host app
            log.debug("foxy-audit observe error: %s", exc)
