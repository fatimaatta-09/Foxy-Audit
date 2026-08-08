"""GET /v1/health — the endpoint the desktop app probes on startup.

Returns 200 only for a valid Bearer key (invalid → 401, which the desktop treats
as 'unreachable'). Body is informational; the desktop only checks the status code.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ..auth import require_org
from ..models import AuditLog, Organization
from ..db import get_db
from ..rls import cross_org_visibility
from ..config import get_settings

router = APIRouter()
START_TIME = time.time()


def _evaluator_status(settings) -> dict:
    """Expose configuration state without pretending it is provider health."""
    configured = bool(settings.gemini_api_key.strip())
    return {
        "provider": "gemini",
        "model": settings.gemini_model,
        "status": "configured" if configured else "unavailable",
        "configured": configured,
        "verdicts_advisory": True,
    }

@router.get("/v1/health")
def health(
    org: Organization = Depends(require_org),
    db: Session = Depends(get_db)
):
    chain_height = db.execute(
        select(func.count()).select_from(AuditLog).where(AuditLog.org_id == org.id)
    ).scalar_one()
    uptime_seconds = int(time.time() - START_TIME)
    
    settings = get_settings()
    return {
        "status": "ok",
        "org": org.name,
        "gemini_model": settings.gemini_model if getattr(settings, "gemini_api_key", "") else None,
        "openai_model": (settings.openai_model
                         if getattr(settings, "openai_api_key", "") else None),
        "evaluator": _evaluator_status(settings),
        "chain_height": chain_height,
        "uptime_seconds": uptime_seconds
    }


@router.get("/health/ready")
def ready(db: Session = Depends(get_db)):
    """Unauthenticated readiness probe for orchestrators (compose/k8s): DB
    reachable + the grading worker is alive + the queue backlog. Returns 503 when
    not ready so the platform can restart/alert instead of routing traffic to a
    box whose grading queue is silently stuck."""
    checks: dict = {}
    try:
        db.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as e:  # pragma: no cover
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "checks": {"db": f"error: {e}"}},
        )

    s = get_settings()
    hb = db.execute(
        text("SELECT beat_at FROM worker_heartbeat WHERE id = 1")
    ).scalar_one_or_none()
    # Stale if no beat within a few poll intervals + slack for one slow Gemini call.
    stale_after = s.grading_poll_interval * 5 + max(s.gemini_timeout, s.openai_timeout) + 10
    if hb is None:
        worker_ok = False
        checks["worker"] = "no heartbeat yet"
    else:
        age = (datetime.now(timezone.utc) - hb).total_seconds()
        worker_ok = age < stale_after
        checks["worker"] = f"last beat {int(age)}s ago" + ("" if worker_ok else " (STALE)")

    # #122 · ASKED BEFORE THE TWO COUNTS BELOW, WHICH ARE THE DEFECT IN
    # MINIATURE. audit_logs carries FORCE RLS and this probe sets no org GUC, so
    # under a role that is neither superuser nor BYPASSRLS they come back 0 and
    # this endpoint reports a perfectly healthy deployment. Inferring it FROM
    # those zeros is impossible — zero pending is the healthy answer most of the
    # time — so the capability is asked directly instead.
    #
    # And the counts are not published when the answer is no. A number that is
    # knowably wrong is worse than a visible failure on this product, and here it
    # may not even be computable: once any transaction on a pooled connection has
    # run set_config('app.current_org', …, true), the GUC resets to '' rather
    # than to unset, and ''::uuid RAISES instead of matching nothing. So the
    # confined failure is silent zeros on a fresh connection and a 500 on a
    # recycled one — measured, both.
    #
    # NOT READY rather than a warning: this is the smoke test the deploy gates
    # on, and a rollback is the right outcome for a change that would make every
    # staff figure silently understated. It is also the recoverable end of the
    # scale — a boot-time assertion would take the API down hard, and a role can
    # be altered under a process that has already started, which a check that
    # only ran at startup would never see.
    rls = cross_org_visibility(db)
    if rls["status"] != "ok":
        checks["rls"] = rls["detail"]
        return JSONResponse(status_code=503,
                            content={"status": "not_ready", "checks": checks})
    checks["rls"] = "ok"

    checks["pending"] = db.execute(
        text("SELECT count(*) FROM audit_logs WHERE grading_status = 'pending'")
    ).scalar_one()
    checks["failed"] = db.execute(
        text("SELECT count(*) FROM audit_logs WHERE grading_status = 'failed'")
    ).scalar_one()

    ok = worker_ok
    return JSONResponse(
        status_code=200 if ok else 503,
        content={"status": "ready" if ok else "not_ready", "checks": checks},
    )
