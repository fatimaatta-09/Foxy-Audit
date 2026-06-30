"""Local hashing — the heart of the zero-knowledge ("data blindness") design.

Prompt and response are hashed here, in-process, and the raw strings are never
returned, stored, or transmitted. Only the resulting digests + a token estimate
leave this module.
"""

from __future__ import annotations

import hashlib


def sha256_hex(text: str) -> str:
    """Lowercase 64-char hex SHA-256 of the UTF-8 encoding of `text`."""
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


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
