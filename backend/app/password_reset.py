"""Password-reset tokens for BOTH logins — the customer dashboard (auth_human)
and the staff console (auth_staff). Phase 5 · 5D.

A reset request emails a link carrying a high-entropy token; only its SHA-256
hash is stored, so a DB leak alone can't reset a password. Tokens are single-use
with a 1-hour TTL. Enumeration safety (never revealing whether an email exists)
is the caller's responsibility — this module only issues/validates/clears tokens.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from . import email, email_templates as et

TTL = timedelta(hours=1)


def new_token() -> str:
    """A high-entropy, URL-safe reset token (~256 bits)."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


INVITE_TTL = timedelta(days=7)


def issue_reset(db, subject, to_email: str, base_url: str, *,
                invite: bool = False, ttl: timedelta | None = None,
                surface: str = "customer") -> None:
    """Generate a token, store its hash + expiry on `subject` (which must expose
    reset_token_hash / reset_token_expires_at), commit, and email a link.

    invite=True → new-account "set your password" copy + a 7-day TTL; otherwise a
    password reset (1h TTL). Both land on the same reset view / reset endpoint."""
    token = new_token()
    subject.reset_token_hash = hash_token(token)
    subject.reset_token_expires_at = (
        datetime.now(timezone.utc) + (ttl or (INVITE_TTL if invite else TTL)))
    db.commit()
    sep = "&" if "?" in base_url else "?"
    link = f"{base_url}{sep}reset_token={token}"
    where = "the Foxy Audit staff console" if surface == "staff" else "Foxy Audit"
    if invite:
        subject_line = "You're invited to Foxy Audit — set your password"
        html, plain = et.layout(
            title="Set your password",
            preheader=f"You've been invited to {where} — set your password to activate your account.",
            blocks=[
                et.paragraph(f"You've been invited to {where}. Set your password to activate your "
                             "account."),
                et.button("Set your password", link),
                et.muted("This invitation link expires in 7 days. If you weren't expecting it, you "
                         "can ignore this email."),
            ],
            surface=surface,
        )
    else:
        subject_line = "Reset your Foxy Audit password"
        html, plain = et.layout(
            title="Reset your password",
            preheader="Choose a new password for your Foxy Audit account — this link expires in 1 hour.",
            blocks=[
                et.paragraph("We received a request to reset your Foxy Audit password. Use the "
                             "button below to choose a new one."),
                et.button("Choose a new password", link),
                et.muted("This link expires in 1 hour. If you didn't request it, you can safely "
                         "ignore this email — your password stays the same."),
            ],
            surface=surface,
        )
    email.send_email(to=to_email, subject=subject_line, html=html, text=plain)


def token_valid(subject, token: str) -> bool:
    """Constant-time check that `token` matches the unexpired stored hash."""
    now = datetime.now(timezone.utc)
    return bool(
        subject.reset_token_hash and subject.reset_token_expires_at
        and subject.reset_token_expires_at >= now
        and hmac.compare_digest(subject.reset_token_hash, hash_token(token)))


def clear_reset(subject) -> None:
    subject.reset_token_hash = None
    subject.reset_token_expires_at = None
