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
    elif not isinstance(value, (str, int, float, bool, list, dict, tuple)) and value is not None:
        value = str(value)
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)


def commitment_hex(value: Any, key: str) -> str:
    """Return a customer-keyed commitment; unlike SHA-256 it is not public."""
    return hmac.new(key.encode("utf-8"), canonical_json(value).encode("utf-8"), hashlib.sha256).hexdigest()


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
