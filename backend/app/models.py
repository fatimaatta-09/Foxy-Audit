"""ORM models — organizations, the append-only audit_logs hash chain, and per-org policy config."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger, Boolean, Date, DateTime, ForeignKey, Integer, SmallInteger, String,
    Text, UniqueConstraint, func,
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
    # ── Platform/admin metadata (Phase 4 #1, migration 0011). All nullable/defaulted
    #    so ADD COLUMN is backward-compatible and the running app never breaks. ──
    contact_email: Mapped[str | None] = mapped_column(
        String(320), nullable=True, default=None)            # owner/billing contact
    trial_ends_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None)
    deleted_at: Mapped[datetime | None] = mapped_column(     # soft delete — never hard-delete an org
        DateTime(timezone=True), nullable=True, default=None)
    # Per-org DASHBOARD IP allow-list (5K) — comma-separated IPs/CIDRs; empty/NULL
    # = no restriction. Enforced on session auth only, never on the SDK key.
    ip_allowlist: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    suspended: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false")     # staff access hold (independent of Stripe)
    suspended_reason: Mapped[str | None] = mapped_column(
        String(255), nullable=True, default=None)
    monthly_log_quota: Mapped[int | None] = mapped_column(   # NULL = unlimited
        Integer, nullable=True, default=None)
    # Opt-in embeddable public trust badge (6C). NULL = no badge; minted on demand,
    # revocable. Resolves the org for the PUBLIC GET /v1/badge/{token}.svg — never
    # exposes org_id/name, only aggregate status. Unique so a token maps to one org.
    public_badge_token: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True, default=None)
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
    # Client identity makes retries idempotent and lets the verifier detect
    # missing events before they disappear into a batch boundary.
    event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    client_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    client_seq: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    event_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    commitment_alg: Mapped[str | None] = mapped_column(String(32), nullable=True)
    event_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    occurred_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    chain_version: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="1")
    prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_tag: Mapped[str] = mapped_column(String(32), nullable=False)
    # Which agent/model produced this interaction (6B). Folded into the chain hash
    # when present (tamper-evident); nullable — pre-6B rows have none.
    agent: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
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


class OrganizationSequence(Base):
    """Locked per-org allocator used when the ledger is empty or concurrent."""

    __tablename__ = "org_sequences"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True)
    next_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="1")


class AuditEvent(Base):
    """Append-only facts associated with a ledger interaction.

    The original audit_logs.gemini_verdict column is retained as a compatibility
    projection for old clients. New verdicts are also written here so grading
    does not rewrite the evidence event itself.
    """

    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    audit_log_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("audit_logs.id", ondelete="CASCADE"), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    event_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())


class AccountAction(Base):
    """Customer-visible audit trail of an org's OWN account actions — key/policy/
    member/MFA/settings changes (P2 · §D). Org-scoped by RLS, mirrors the staff
    admin_actions table but read by the customer, never exposing other tenants."""

    __tablename__ = "account_actions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False, index=True)
    actor_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target: Mapped[str | None] = mapped_column(String(255), nullable=True)
    detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())


class SsoConnection(Base):
    """Per-org enterprise SSO (OIDC) connection (P3 · §H). Users whose email
    domain matches are routed to this IdP at login. One per org / per domain."""

    __tablename__ = "sso_connections"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False, unique=True)
    email_domain: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    issuer: Mapped[str] = mapped_column(String(512), nullable=False)
    client_id: Mapped[str] = mapped_column(String(512), nullable=False)
    client_secret: Mapped[str] = mapped_column(String(512), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())


class WebhookSubscription(Base):
    """A customer's outbound webhook subscription (P3 · §F) — the worker POSTs a
    signed JSON payload to `url` for each subscribed event. Org-scoped by RLS."""

    __tablename__ = "webhook_subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False, index=True)
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    secret: Mapped[str] = mapped_column(String(80), nullable=False)   # HMAC-SHA256 key
    events: Mapped[str] = mapped_column(String(255), nullable=False, server_default="breach")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    last_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_delivery_at: Mapped[datetime | None] = mapped_column(
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
    # Judge sensitivity (Phase 5) — persisted policy preferences surfaced on the dashboard.
    enforcement_mode: Mapped[str] = mapped_column(String(16), nullable=False, server_default="block")
    confidence_threshold: Mapped[str] = mapped_column(String(16), nullable=False, server_default="balanced")
    notify_on_breach: Mapped[str] = mapped_column(String(16), nullable=False, server_default="immediate")
    # Optional destinations for the breach notifier (P2 · §F). NULL email falls
    # back to the org's contact_email; NULL webhook = no webhook.
    notify_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    notify_webhook_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
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
    # Google SSO link (nullable, globally unique): set on first Google sign-in so
    # the identity resolves unambiguously thereafter. Google users have an unusable
    # placeholder password_hash (they authenticate via Google, not a password).
    google_sub: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    # Email-OTP MFA (opt-in, Phase 5 · 5B.5) — dashboard login, mirrors staff.
    mfa_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false")
    mfa_code_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    mfa_code_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    # Password-reset link token (Phase 5 · 5D) — SHA-256 hash only, single-use, 1h TTL.
    reset_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reset_token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    # Dashboard onboarding checklist state (P4) — a small JSONB bag, e.g. {"dismissed": true}. The
    # checklist COMPLETION is computed live from real data; only dismissal is persisted here.
    onboarding_state: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


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
    expires_at: Mapped[datetime | None] = mapped_column(          # NULL = never expires
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
    chain: Mapped[str] = mapped_column(String(32), nullable=False)       # sepolia | stub
    tx_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    block_number: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="pending")           # pending|confirmed|failed
    detail: Mapped[str | None] = mapped_column(String(500), nullable=True)
    anchored_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)


# ─────────────────────── Platform / admin ("site 3") tables ───────────────────
# These power the internal admin site. Staff read cross-org with app.current_org
# UNSET, so tables read ONLY on a staff path (or with a NULLABLE org_id) are
# deliberately WITHOUT RLS — a nullable org_id can never match the RLS predicate,
# so RLS there would hide the very rows staff need. Tables a CUSTOMER also reads
# (invoices, usage_daily) keep org-scoped RLS exactly like audit_logs.


class StaffUser(Base):
    """A foxy.audit *platform* employee — NOT tenant-scoped (no org_id, no RLS).

    Staff log in over a SEPARATE session cookie (foxy_staff_session) and are
    resolved by auth.require_staff, which never sets app.current_org — so a staff
    query reads across ALL orgs (the docker 'foxy' superuser bypasses FORCE RLS).
    A customer `users` row can never satisfy require_staff and vice-versa: the two
    channels use disjoint session keys, cookie names, and signing secrets.
    """
    __tablename__ = "staff_users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)   # bcrypt
    platform_role: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="viewer")            # viewer|operator|superadmin
    disabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false")
    # Profile + preferences (Phase B · dashboard parity). preferences is a small
    # JSONB bag of booleans (hide_sensitive_metadata + notify_*), NULL = defaults.
    full_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    preferences: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Last successful sign-in (Phase E) — stamped by _establish_staff_session.
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    # Email-OTP MFA (opt-in, Phase 5 · 5B.5)
    mfa_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false")
    mfa_code_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    mfa_code_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    # Password-reset link token (Phase 5 · 5D) — SHA-256 hash only, single-use, 1h TTL.
    reset_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reset_token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("staff_users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())


class AdminAction(Base):
    """Append-only audit trail of every state-changing staff action.

    Written in the SAME transaction as the mutation it records, so a committed
    suspend without a logged action (or vice-versa) cannot happen. No RLS
    (platform-only); staff must never be able to DELETE from it.
    """
    __tablename__ = "admin_actions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    staff_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("staff_users.id"), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_org_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=True)
    target_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())


class StaffNotification(Base):
    """A notification for a platform-staff member, generated from a REAL event (a broadcast, a
    staff action targeting them, or a system/org event) — never fabricated. Platform-only (no RLS
    policy). One row per recipient (broadcasts fan out) so read state is per-staff. (Phase D)
    """
    __tablename__ = "staff_notifications"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # nullable = a broadcast-to-all row (unused while broadcasts fan out per-staff; kept for flex)
    staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("staff_users.id"), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    level: Mapped[str] = mapped_column(String(16), nullable=False, server_default="info")
    target_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True)


class StaffSession(Base):
    """A device session for a platform-staff member (Phase E). The signed staff cookie carries an
    opaque token whose SHA-256 is stored here; require_staff validates it (not revoked, <=2h) and
    refreshes last_seen_at — enabling an active-devices list + revoke + log-out-everywhere. NO
    remember-me: the 2h cookie max-age is kept. Platform-only (no RLS policy). token_hash never leaves
    the server.
    """
    __tablename__ = "staff_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    staff_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("staff_users.id"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(400), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)


class UserSession(Base):
    """A customer device session (dashboard P3). The signed customer cookie carries an opaque token
    whose SHA-256 is stored here; require_user validates it (not revoked, not past expires_at) and
    refreshes last_seen_at — enabling an active-devices list + revoke + log-out-everywhere. remember_me
    on login picks expires_at (30d) vs the 12h default. Per-org (org_isolation RLS). token_hash never
    leaves the server.
    """
    __tablename__ = "user_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(400), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)


class TrafficEvent(Base):
    """One request across any of the 3 sites — the traffic feed for the admin
    site. Written OFF the request hot path by
    middleware/traffic.py. Platform-only, NO RLS: org_id is nullable (anonymous
    marketing traffic has none), which structurally rules RLS out, and it is read
    only by staff. Range-partitioned by created_at (see migration 0012), so the
    effective PK is (id, created_at). Privacy: ip/ua are HMAC-hashed, never raw;
    path/referrer carry NO query string; no bodies/headers/secrets are stored.
    """
    __tablename__ = "traffic_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    site: Mapped[str] = mapped_column(String(16), nullable=False)        # marketing|app|admin
    path: Mapped[str] = mapped_column(String(512), nullable=False)       # route only, no query string
    method: Mapped[str] = mapped_column(String(8), nullable=False)
    status_code: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    org_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    staff_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("staff_users.id"), nullable=True)
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)   # HMAC(pepper, ip)
    ua_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)   # HMAC(pepper, user-agent)
    referrer: Mapped[str | None] = mapped_column(String(512), nullable=True)
    visitor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True, server_default=func.now())   # partition key


class ConsentEvent(Base):
    """One cookie-consent decision from the marketing banner — an auditable trail
    for GDPR/CCPA. IP/UA are HMAC-hashed (never raw), like traffic_events. Platform-
    only, no RLS (anonymous marketing visitors, read by staff)."""
    __tablename__ = "consent_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())
    region: Mapped[str | None] = mapped_column(String(8), nullable=True)      # eu|us|other
    regime: Mapped[str | None] = mapped_column(String(8), nullable=True)      # gdpr|ccpa
    analytics: Mapped[bool] = mapped_column(Boolean, nullable=False)
    functional: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    policy_version: Mapped[str | None] = mapped_column(String(16), nullable=True)
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ua_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


class MarketingLead(Base):
    """A marketing-site conversion before it becomes a paying org. Platform-only,
    NO RLS (no org_id until conversion; pre-sales data never on a customer path)."""
    __tablename__ = "marketing_leads"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    company: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    subject: Mapped[str | None] = mapped_column(String(128), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    utm_campaign: Mapped[str | None] = mapped_column(String(128), nullable=True)
    utm_source: Mapped[str | None] = mapped_column(String(128), nullable=True)
    utm_medium: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="new")               # new|trial|converted|churned
    converted_org_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=True)
    detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    # Admin support inbox: read/claim/reply tracking (migration 0033)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claimed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("staff_users.id"), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reply: Mapped[str | None] = mapped_column(Text, nullable=True)
    replied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class StripeEvent(Base):
    """Durable, idempotent log of every verified Stripe webhook event. Makes the
    formerly fire-and-forget webhook auditable + replay-safe (UNIQUE stripe_event_id
    → ON CONFLICT DO NOTHING). Platform-only, NO RLS (org_id may be null / precede
    an org; billing audit is staff-only)."""
    __tablename__ = "stripe_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    stripe_event_id: Mapped[str] = mapped_column(
        String(255), unique=True, index=True, nullable=False)           # evt_… idempotency key
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    org_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="received")          # received|processed|ignored|failed
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)


class Invoice(Base):
    """Billing history for an org. RLS-scoped by org_id like audit_logs — a
    customer reads its OWN invoices; staff (superuser, GUC unset) read all."""
    __tablename__ = "invoices"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, index=True)
    stripe_invoice_id: Mapped[str] = mapped_column(
        String(255), unique=True, index=True, nullable=False)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="usd")
    status: Mapped[str] = mapped_column(String(16), nullable=False)      # draft|open|paid|void|uncollectible
    period_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    period_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())


class UsageDaily(Base):
    """Per-org, per-day rollup of audit_logs so admin dashboards never scan the
    raw ledger. Populated incrementally by the worker (app/usage.py). RLS-scoped
    by org_id like audit_logs (a customer reads its own quota widget; staff read
    all) — keeping the aggregate no broader than its RLS-protected source."""
    __tablename__ = "usage_daily"
    __table_args__ = (UniqueConstraint("org_id", "day", name="uq_usage_org_day"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, index=True)
    day: Mapped[datetime] = mapped_column(Date, nullable=False)
    logs_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    tokens_sum: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    breach_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    graded_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    pending_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())


class WorkerHeartbeat(Base):
    """Read-only mapping of the single-row liveness table created by migration
    0007 (previously managed via raw SQL only in worker.py/health.py). Added
    for the admin data browser's benefit — never written through the ORM;
    the worker keeps updating it directly."""
    __tablename__ = "worker_heartbeat"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    beat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LoginEvent(Base):
    """Login-attempt history (Phase 5 · 5K) — successes AND failures, for security
    visibility. Platform/audit table, NO RLS (org_id is nullable for a failed
    login on an unknown email, which structurally rules RLS out); the history
    endpoint filters by org_id in app code."""
    __tablename__ = "login_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=True, index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(256), nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True)


class AuthHandoffToken(Base):
    """Single-use, short-TTL token for the desktop pet -> dashboard auto-login handoff.
    The pet (holding the org API key) mints one via POST /v1/auth/handoff; the dashboard
    redeems ?handoff=<token> to establish a session. Only the SHA-256 hash is stored. No
    RLS — it's auth plumbing, resolved by a hash lookup and gated on unused + unexpired."""
    __tablename__ = "auth_handoff_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())



class PlatformConfig(Base):
    """Platform-global config overrides (P3 · §F) — feature flags, default quotas,
    alert thresholds. Effective value = this override OR the Settings default. No
    org_id → platform-only (FORCE RLS with no policy; superuser staff bypass)."""
    __tablename__ = "platform_config"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict | None] = mapped_column(JSONB, nullable=True)   # scalar stored as JSON
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("staff_users.id"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class PlatformAnnouncement(Base):
    """Broadcast banner shown on the ops console (P3 · §G). Platform-only."""
    __tablename__ = "platform_announcements"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    level: Mapped[str] = mapped_column(String(16), nullable=False, server_default="info")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("staff_users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
