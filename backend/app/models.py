"""ORM models — organizations, the append-only audit_logs hash chain, and per-org policy config."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger, Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String, nullable=False)
    api_key_hash: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False)
    # Billing / Stripe integration
    plan_tier: Mapped[str | None] = mapped_column(
        String(32), nullable=True, default=None)
    stripe_customer_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, unique=True, default=None)
    stripe_subscription_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, default=None)
    subscription_status: Mapped[str | None] = mapped_column(
        String(32), nullable=True, default=None)             # active | past_due | cancelled
    # Key rotation tracking
    key_rotated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (UniqueConstraint("org_id", "seq", name="uq_audit_org_seq"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, index=True)
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)          # per-org monotonic
    prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_tag: Mapped[str] = mapped_column(String(32), nullable=False)
    pii_signals: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    chain_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    gemini_verdict: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # ── durable grading queue (Postgres outbox) ──
    grading_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="pending")   # pending|in_progress|graded|failed
    grading_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0")
    grading_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    graded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())


class OrgPolicy(Base):
    """Per-org compliance policy configuration — Core Requirement #1.

    One row per organization. Created with safe defaults on first GET.
    Non-technical executives update this via PUT /v1/policies; the
    Gemini evaluator reads these flags to adjust its system prompt.
    """
    __tablename__ = "org_policies"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), primary_key=True)
    pii_detection: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    prompt_injection: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    regulated_data_mode: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    max_token_threshold: Mapped[int] = mapped_column(Integer, nullable=False, default=50000)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class User(Base):
    """A human dashboard account (email/password + role), scoped to one org.

    Separate from the org API key: the SDK authenticates with the machine Bearer
    key (require_org); humans log in here over a signed cookie session.
    """
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("org_id", "email", name="uq_user_org_email"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)   # bcrypt
    role: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="member")             # admin | member
    # Offboarding: a disabled user cannot log in and any live session is rejected
    # (see auth.require_org's human path / auth_human.login).
    disabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())


class ApiKey(Base):
    """A named SDK API key, one org can hold many (Phase 3 A2).

    The plaintext key is shown once at creation and never stored — only
    ``key_hash`` = HMAC-SHA256(server pepper, key) is kept, so a DB leak alone
    can't recover a usable key (unlike the legacy plain-SHA256 org key, which
    require_org still honours as a fallback). No RLS: require_org looks a key up
    *before* the tenant GUC is set, so isolation here is enforced in app code
    (every management query filters on org_id).
    """
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(64), nullable=False)   # display only e.g. foxy_sk_1a2…7e02
    key_hash: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False)             # HMAC-SHA256 hex
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="active")             # active | revoked
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)


class ChainAnchor(Base):
    """A public-chain anchor of an org's hash-chain head (Phase 3 A1).

    Each row records that the org's chain head (``root_hash`` = the latest
    audit_logs.chain_hash at ``last_seq``) was published to a public chain in
    transaction ``tx_hash``. Once confirmed, anyone holding this receipt can
    check the on-chain value and prove the ledger existed in that state at that
    block — so tampering is detectable EXTERNALLY, not just by our own recompute.
    RLS-scoped by org_id like audit_logs; also filtered in app code.
    """
    __tablename__ = "chain_anchors"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, index=True)
    root_hash: Mapped[str] = mapped_column(String(64), nullable=False)   # chain head at anchor time
    last_seq: Mapped[int] = mapped_column(BigInteger, nullable=False)    # seq of the head row
    chain: Mapped[str] = mapped_column(String(32), nullable=False)       # sepolia | stub | bitcoin
    tx_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    block_number: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="pending")           # pending|confirmed|failed
    detail: Mapped[str | None] = mapped_column(String(500), nullable=True)
    anchored_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)

