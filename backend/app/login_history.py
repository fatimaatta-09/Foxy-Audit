"""Login-attempt recording (Phase 5 · 5K). Best-effort — never breaks the login
flow. Success is attributed to the resolved user's org; a failure on an unknown
email is recorded platform-level (org_id NULL)."""

from __future__ import annotations

from .ip_allow import client_ip
from .models import LoginEvent


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
