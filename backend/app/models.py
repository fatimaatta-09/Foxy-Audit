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
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    chain_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    gemini_verdict: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
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

