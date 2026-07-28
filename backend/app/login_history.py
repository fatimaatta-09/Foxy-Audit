"""Login-attempt recording (Phase 5 · 5K). Best-effort — never breaks the login
flow. Success is attributed to the resolved user's org; a failure on an unknown
email is recorded platform-level (org_id NULL)."""

from __future__ import annotations

from sqlalchemy import select

from .ip_allow import client_ip
from .models import LoginEvent


def is_new_device(db, user, user_agent: str | None) -> bool:
    """True when this user has signed in successfully before, but never from this
    user-agent — the question behind the new-device alert (P3 §3).

    Keyed on the user-agent alone, deliberately. IP churns on every mobile network
    hop, so keying on it would email somebody for walking between two cell towers,
    and an alert people learn to ignore protects nobody.

    The first-ever successful sign-in is NOT a new device: the person is sitting
    right there having just created the account, and telling them a stranger
    signed in would be a lie. Call BEFORE `record()` for the current login, or it
    matches itself and no device is ever new.
    """
    ua = (user_agent or "")[:256]
    seen_any = db.execute(
        select(LoginEvent.id).where(
            LoginEvent.user_id == user.id, LoginEvent.success.is_(True)).limit(1)
    ).first()
    if seen_any is None:
        return False
    seen_this = db.execute(
        select(LoginEvent.id).where(
            LoginEvent.user_id == user.id, LoginEvent.success.is_(True),
            LoginEvent.user_agent == ua).limit(1)
    ).first()
    return seen_this is None


def record(db, request, email: str, success: bool, user=None) -> None:
    try:
        db.add(LoginEvent(
            email=email,
            org_id=(user.org_id if user is not None else None),
            user_id=(user.id if user is not None else None),
            ip=client_ip(request)[:64],
            user_agent=(request.headers.get("user-agent") or "")[:256],
            success=success,
        ))
        db.commit()
    except Exception:
        db.rollback()
