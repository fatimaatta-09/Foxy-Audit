"""Pydantic request/response models.

Note: org_id is intentionally NOT in LogIngest — it is derived server-side from
the Bearer API key, so a client can never spoof another tenant's id.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field, field_validator


class LogIngest(BaseModel):
    prompt_hash: str = Field(min_length=64, max_length=64)
    response_hash: str = Field(min_length=64, max_length=64)
    token_count: int = Field(ge=0, le=10_000_000)
    policy_tag: str = Field(pattern=r"^[a-z0-9_]{1,32}$")

    @field_validator("prompt_hash", "response_hash")
    @classmethod
    def _must_be_hex(cls, v: str) -> str:
        v = v.lower()
        int(v, 16)  # raises ValueError on non-hex → 422 (never a corrupt row)
        return v


class Verdict(BaseModel):
    policy_breach: bool = False
    reason: str = ""
    risk_score: int = Field(default=0, ge=0, le=100)


class LogResponse(BaseModel):
    log_id: uuid.UUID
    seq: int
    chain_hash: str
    status: str = "pending"          # "pending" | "graded"
    verdict: Verdict | None = None


class VerifyResponse(BaseModel):
    ok: bool
    count: int
    first_broken_seq: int | None = None
    detail: str | None = None
