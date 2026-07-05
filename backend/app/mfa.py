"""Shared email-OTP MFA helpers for BOTH logins — the staff/admin console
(auth_staff) and the customer dashboard (auth_human). The marketing site has no
MFA (account creation there can lean on Google/OAuth).

Opt-in per account via an `mfa_enabled` flag. The emailed 6-digit code is
single-use with a 5-minute TTL and is stored only as a SHA-256 hash. (Phase 5 · 5B.5)
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from . import email

TTL = timedelta(minutes=5)


def new_otp() -> str:
    """A 6-digit numeric code. Patched in tests for determinism."""
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_code(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def issue_code(db, subject, to_email: str) -> None:
    """Generate a code, store its hash + expiry on `subject` (which must expose
    mfa_code_hash / mfa_code_expires_at), commit, and email it via Brevo."""
    code = new_otp()
    subject.mfa_code_hash = hash_code(code)
    subject.mfa_code_expires_at = datetime.now(timezone.utc) + TTL
    db.commit()
    email.send_email(
        to=to_email,
        subject="Your Foxy Audit sign-in code",
        html=(f"<p>Your sign-in code is <b>{code}</b>. It expires in 5 minutes. "
              f"If you didn't request it, ignore this email.</p>"),
        text=f"Your Foxy Audit sign-in code is {code} (expires in 5 minutes).",
    )


def code_valid(subject, code: str) -> bool:
    """Constant-time check that `code` matches the unexpired stored hash."""
    now = datetime.now(timezone.utc)
    return bool(
        subject.mfa_code_hash and subject.mfa_code_expires_at
        and subject.mfa_code_expires_at >= now
        and hmac.compare_digest(subject.mfa_code_hash, hash_code(code))
    )


def clear_code(subject) -> None:
    subject.mfa_code_hash = None
    subject.mfa_code_expires_at = None
