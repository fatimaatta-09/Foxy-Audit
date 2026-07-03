"""Helper for the append-only staff audit trail (admin_actions).

Every state-changing staff action records exactly one AdminAction row IN THE SAME
transaction as the mutation — so a committed change without a logged action (or a
logged action without the change) cannot happen. Callers add the row and commit
together; this helper never commits on its own.
"""

from __future__ import annotations

from fastapi import Request
from sqlalchemy.orm import Session

from .models import AdminAction, StaffUser


def client_ip(request: Request) -> str | None:
    """Best-effort client IP for the audit row. Trusts the first X-Forwarded-For
    hop when present (set by our own proxy), else the direct peer."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()[:64]
    return request.client.host[:64] if request.client else None


def record_admin_action(
    db: Session,
    staff: StaffUser,
    action: str,
    *,
    target_org_id=None,
    target_type: str | None = None,
    target_id: str | None = None,
    detail: dict | None = None,
    ip: str | None = None,
) -> None:
    """Stage an AdminAction row. Does NOT commit — the caller commits it together
    with the mutation it describes."""
    db.add(AdminAction(
        staff_user_id=staff.id, action=action, target_org_id=target_org_id,
        target_type=target_type, target_id=target_id, detail=detail, ip=ip,
    ))
