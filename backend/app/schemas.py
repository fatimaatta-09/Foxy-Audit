"""Pydantic request/response models.

Note: org_id is intentionally NOT in LogIngest — it is derived server-side from
the Bearer API key, so a client can never spoof another tenant's id.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


class LogIngest(BaseModel):
    prompt_hash: str = Field(min_length=64, max_length=64)
    response_hash: str = Field(min_length=64, max_length=64)
    token_count: int = Field(ge=0, le=10_000_000)
    policy_tag: str = Field(pattern=r"^[a-z0-9_]{1,32}$")
    pii_signals: list[str] | None = None
    # which model produced it (6B); charset-locked (like policy_tag) so it's safe
    # to render raw and can't smuggle HTML/delimiters into the chain blob.
    agent: str | None = Field(default=None, max_length=128,
                              pattern=r"^[A-Za-z0-9_.\-: /]{1,128}$")
    event_id: uuid.UUID | None = None
    client_id: str | None = Field(default=None, max_length=128,
                                  pattern=r"^[A-Za-z0-9_.:-]{1,128}$")
    client_seq: int | None = Field(default=None, ge=1)
    event_type: str = Field(default="interaction", pattern=r"^[a-z0-9_]{1,32}$")
    commitment_alg: str = Field(default="sha256-legacy", pattern=r"^[a-z0-9-]{1,32}$")
    event_metadata: dict[str, Any] | None = None
    occurred_at: datetime | None = None

    @field_validator("event_metadata")
    @classmethod
    def _metadata_is_content_blind(cls, value):
        if value is None:
            return value
        allowed = {"request_id", "trace_id", "session_id", "provider", "model",
                   "id", "usage", "choice_count", "tool_names", "retrieval_refs",
                   "client_seq_gap"}
        unknown = set(value) - allowed
        if unknown:
            raise ValueError("event_metadata contains unsupported fields")
        for key, item in value.items():
            values = item if isinstance(item, list) else [item]
            if len(values) > 64 or any(len(str(v)) > 256 for v in values):
                raise ValueError(f"event_metadata.{key} is too large")
        return value

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
    decision: str = Field(default="unknown", pattern=r"^(clean|breach|unknown)$")
    rules: list[str] = Field(default_factory=list)


class LogResponse(BaseModel):
    log_id: uuid.UUID
    seq: int
    chain_hash: str
    status: str = "pending"          # "pending" | "graded"
    verdict: Verdict | None = None


class LastAnchor(BaseModel):
    """The org's most recent public-chain anchor (Phase 3 A1), if any."""
    chain: str
    status: str
    root_hash: str
    last_seq: int
    tx_hash: str | None = None
    block_number: int | None = None
    anchored_at: str | None = None
    # True when the anchored root still matches a fresh recompute of the chain up
    # to last_seq — i.e. nothing before the anchor point was tampered with.
    matches_current_chain: bool | None = None


class VerifyResponse(BaseModel):
    ok: bool
    count: int
    first_broken_seq: int | None = None
    detail: str | None = None
    last_anchor: LastAnchor | None = None


class LogListItem(BaseModel):
    """One row returned by GET /v1/logs."""
    id: uuid.UUID
    seq: int
    event_id: uuid.UUID | None = None
    client_id: str | None = None
    client_seq: int | None = None
    event_type: str | None = None
    commitment_alg: str | None = None
    event_metadata: dict[str, Any] | None = None
    occurred_at: datetime | None = None
    chain_version: int = 1
    prompt_hash: str
    response_hash: str
    token_count: int
    policy_tag: str
    agent: str | None = None
    pii_signals: list[str] | None = None
    chain_hash: str
    gemini_verdict: dict[str, Any] | None = None
    grading_status: str = "pending"
    created_at: datetime

    class Config:
        from_attributes = True


class LogListResponse(BaseModel):
    items: list[LogListItem]
    total: int
    page: int
    limit: int


# ── dashboard stats (GET /v1/stats) ──
class GradingCounts(BaseModel):
    pending: int = 0
    in_progress: int = 0
    graded: int = 0
    failed: int = 0


class ActivityDay(BaseModel):
    date: str            # YYYY-MM-DD
    count: int
    breaches: int


class StatsResponse(BaseModel):
    total_logged: int
    breaches: int
    clean_rate: float | None     # percent, 0-100; None when no determinate grades exist
    avg_token_count: float
    judge_model: str             # the real configured Gemini judge model
    avg_seconds_to_verdict: float | None   # avg graded_at - created_at; None if none graded
    grading: GradingCounts
    activity_7d: list[ActivityDay]

