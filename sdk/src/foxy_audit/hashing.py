"""Local hashing — the heart of the zero-knowledge ("data blindness") design.

Prompt and response are hashed here, in-process, and the raw strings are never
returned, stored, or transmitted. Only the resulting digests + a token estimate
leave this module.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any


def sha256_hex(text: str) -> str:
    """Lowercase 64-char hex SHA-256 of the UTF-8 encoding of `text`."""
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def canonical_json(value: Any) -> str:
    """Serialize structured SDK values deterministically without sending them."""
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    elif hasattr(value, "dict") and callable(value.dict):
        value = value.dict()
    elif hasattr(value, "to_dict") and callable(value.to_dict):
        value = value.to_dict()
    elif not isinstance(value, (str, int, float, bool, list, dict, tuple)) and value is not None:
        value = str(value)
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)


def commitment_hex(value: Any, key: str, salt: str | None = None) -> str:
    """Return a customer-keyed commitment; unlike SHA-256 it is not public.

    ``salt`` is an optional per-event secret mixed into the HMAC **canonically** —
    ``HMAC(key, canonical_json({"s": salt, "v": canonical_json(value)}))`` — and
    never by concatenation. ``SHA-256(value + salt)`` would be a downgrade, not an
    upgrade: naive concatenation is length-extension vulnerable, which is the
    exact problem HMAC exists to solve. The salt is belt and braces on top of the
    key, so a commitment stays unguessable even against an attacker who has
    somehow learned the customer key.

    ``salt=None`` reproduces the pre-salt digest byte for byte, and has to: every
    commitment already written was computed that way, and a row must keep
    matching its own sidecar forever.

    **The trade, stated plainly.** The salt lives only in the customer's local
    sidecar (see ``sidecar.py``) and never reaches Foxy — not the wire, not the
    database, not a response, not a log line. A customer who loses that sidecar
    loses known-content proof for those events: they can no longer demonstrate
    *which* text a commitment covers. They do not lose chain verification — the
    tamper-evidence claim is recomputed from stored field values and never needs
    the salt — so the guarantee the product sells is unaffected.
    """
    payload = canonical_json(value)
    if salt:
        # Wrap the ALREADY-canonical form of `value`, so salting adds the salt and
        # changes nothing else about how the value itself is serialized.
        payload = json.dumps({"s": str(salt), "v": payload},
                             ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hmac.new(key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def estimate_tokens(*parts: str) -> int:
    """Cheap, dependency-free token estimate.

    Approximates a tokenizer with max(chars/4, whitespace-word-count) per part —
    good enough for anomaly detection on the backend without pulling in a heavy
    tokenizer dependency. Swap for tiktoken later if exactness is needed.
    """
    total = 0
    for part in parts:
        s = str(part)
        if not s:
            continue
        total += max(len(s) // 4, len(s.split()))
    return total
