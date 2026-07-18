"""Customer-facing account audit trail (P2 · §D).

record_account_action() STAGES an account_actions row in the caller's session so
it commits atomically with the mutation it records — a change without a logged
action (or vice-versa) can't happen. Org-scoped; never records another tenant.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from .models import AccountAction


def record_account_action(db: Session, *, org_id, actor_email: str | None,
                          action: str, target: str | None = None,
                          detail: dict | None = None) -> None:
    db.add(AccountAction(
        org_id=org_id, actor_email=actor_email, action=action,
        target=(target[:255] if target else None), detail=detail,
    ))
