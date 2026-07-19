"""Audit-log viewer (P4 · §L) — a purpose-built filter/export over admin_actions,
beyond the raw Data browser. Viewer-gated; filter by actor / action / org / date,
paginate, or export the matching rows as CSV.
"""
from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import require_platform_role
from ..db import get_db
from ..models import AdminAction, Organization, StaffUser

router = APIRouter()


def _parse_dt(value: str | None):
    value = (value or "").strip()
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=422, detail=f"invalid date: {value}")
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _base_filters(action, org_id, since, until):
    where = []
    if action:
        where.append(AdminAction.action.ilike(action.strip() + "%"))
    if org_id:
        try:
            where.append(AdminAction.target_org_id == uuid.UUID(org_id))
        except ValueError:
            raise HTTPException(status_code=422, detail="invalid org_id")
    d0, d1 = _parse_dt(since), _parse_dt(until)
    if d0:
        where.append(AdminAction.created_at >= d0)
    if d1:
        where.append(AdminAction.created_at <= d1)
    return where


@router.get("/v1/audit")
def audit(
    staff: StaffUser = Depends(require_platform_role("viewer")),
    db: Session = Depends(get_db),
    actor: str | None = None,
    action: str | None = None,
    org_id: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    format: str | None = None,
):
    where = _base_filters(action, org_id, since, until)
    base = (
        select(AdminAction, StaffUser.email, Organization.name)
        .outerjoin(StaffUser, StaffUser.id == AdminAction.staff_user_id)
        .outerjoin(Organization, Organization.id == AdminAction.target_org_id)
        .where(*where)
    )
    cnt = (
        select(func.count()).select_from(AdminAction)
        .outerjoin(StaffUser, StaffUser.id == AdminAction.staff_user_id)
        .where(*where)
    )
    if actor:
        like = "%" + actor.strip() + "%"
        base = base.where(StaffUser.email.ilike(like))
        cnt = cnt.where(StaffUser.email.ilike(like))

    total = db.execute(cnt).scalar_one()
    export = (format or "").lower() == "csv"
    rows = db.execute(
        base.order_by(AdminAction.created_at.desc())
        .limit(10000 if export else limit).offset(0 if export else offset)
    ).all()
    items = [
        {"id": str(a.id), "actor": email, "action": a.action, "target_type": a.target_type,
         "target_id": a.target_id,
         "target_org_id": str(a.target_org_id) if a.target_org_id else None,
         "org_name": org_name, "detail": a.detail, "ip": a.ip,
         "at": a.created_at.isoformat() if a.created_at else None}
        for a, email, org_name in rows
    ]

    if export:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["at", "actor", "action", "target_type", "target_id",
                         "org_name", "ip", "detail"])
        for it in items:
            writer.writerow([
                it["at"], it["actor"] or "", it["action"], it["target_type"] or "",
                it["target_id"] or "", it["org_name"] or "", it["ip"] or "",
                json.dumps(it["detail"]) if it["detail"] else "",
            ])
        return PlainTextResponse(
            buf.getvalue(), media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=admin_audit.csv"},
        )
    return {"total": total, "limit": limit, "offset": offset, "items": items}
