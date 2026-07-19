"""Derived operational alerts with durable staff acknowledgements."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..admin_audit import client_ip, record_admin_action
from ..auth import require_platform_role
from ..config import get_settings
from ..db import get_db
from ..platform_config import get_int
from ..models import AdminAction, ChainAnchor, Organization, StaffUser
from .admin_health import build_health

router = APIRouter()


def _utc(value):
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _iso(value):
    value = _utc(value)
    return value.isoformat() if value else None


def _alert(alert_id: str, level: str, title: str, detail: str,
           org_id: str | None = None, org_name: str | None = None) -> dict:
    return {
        "id": alert_id,
        "level": level,
        "title": title,
        "detail": detail,
        "org_id": org_id,
        "org_name": org_name,
        "acknowledged": False,
        "acknowledged_at": None,
        "acknowledged_by": None,
    }


def _derive_alerts(db: Session) -> list[dict]:
    settings = get_settings()
    health = build_health(db, settings)
    alerts = []
    stale_thresh = get_int(db, "anchor_stale_alert_seconds", settings.anchor_stale_alert_seconds)

    worker = health["worker"]
    if worker["status"] != "ok":
        detail = (
            "no heartbeat has been recorded"
            if worker["status"] == "missing"
            else f"last heartbeat is {worker['age_seconds']} seconds old"
        )
        alerts.append(_alert(
            "health.worker", "critical", "Worker heartbeat is stale", detail,
        ))

    failed = int(health.get("grading", {}).get("failed", 0))
    if failed:
        alerts.append(_alert(
            "grading.deadletter", "critical", "Grading dead-letter rows",
            f"{failed} grading row(s) are parked in the failed queue.",
        ))

    orgs = db.execute(select(Organization.id, Organization.name)).all()
    anchors = db.execute(
        select(ChainAnchor).order_by(
            ChainAnchor.org_id, ChainAnchor.anchored_at.desc(), ChainAnchor.id.desc())
    ).scalars().all()
    by_org: dict = {}
    for anchor in anchors:
        by_org.setdefault(anchor.org_id, []).append(anchor)
    now = datetime.now(timezone.utc)
    for oid, name in orgs:
        rows = by_org.get(oid, [])
        latest = rows[0] if rows else None
        if latest and latest.status == "failed":
            alerts.append(_alert(
                f"anchor.failed:{oid}", "critical", "Latest anchor failed",
                "The latest public-chain anchor receipt is failed and needs review.",
                str(oid), name,
            ))
        confirmed = [
            _utc(row.confirmed_at) for row in rows
            if row.status == "confirmed" and row.confirmed_at is not None
        ]
        last_confirmed = max(confirmed) if confirmed else None
        if (
            last_confirmed is not None
            and stale_thresh > 0
            and (now - last_confirmed).total_seconds() > stale_thresh
        ):
            age = int((now - last_confirmed).total_seconds())
            alerts.append(_alert(
                f"anchor.stale:{oid}", "warning", "Anchor is stale",
                f"The last confirmed anchor is {age} seconds old.",
                str(oid), name,
            ))

    ids = [item["id"] for item in alerts]
    if not ids:
        return alerts
    acknowledged = db.execute(
        select(AdminAction.target_id, AdminAction.created_at, StaffUser.email)
        .join(StaffUser, StaffUser.id == AdminAction.staff_user_id)
        .where(AdminAction.action == "alert.ack",
               AdminAction.target_type == "alert",
               AdminAction.target_id.in_(ids))
        .order_by(AdminAction.created_at.desc())
    ).all()
    for target_id, created_at, email in acknowledged:
        item = next((row for row in alerts if row["id"] == target_id), None)
        if item is not None and not item["acknowledged"]:
            item["acknowledged"] = True
            item["acknowledged_at"] = _iso(created_at)
            item["acknowledged_by"] = email
    return sorted(alerts, key=lambda item: (item["acknowledged"], item["level"], item["id"]))


@router.get("/v1/alerts")
def list_alerts(
    staff: StaffUser = Depends(require_platform_role("viewer")),
    db: Session = Depends(get_db),
):
    alerts = _derive_alerts(db)
    return {
        "items": alerts,
        "total": len(alerts),
        "unacknowledged": sum(1 for item in alerts if not item["acknowledged"]),
    }


@router.post("/v1/alerts/{alert_id}/ack")
def acknowledge_alert(
    alert_id: str,
    request: Request,
    staff: StaffUser = Depends(require_platform_role("operator")),
    db: Session = Depends(get_db),
):
    item = next((row for row in _derive_alerts(db) if row["id"] == alert_id), None)
    if item is None:
        raise HTTPException(status_code=404, detail="alert is no longer active")
    if not item["acknowledged"]:
        record_admin_action(
            db, staff, "alert.ack", target_org_id=item["org_id"],
            target_type="alert", target_id=alert_id,
            detail={"title": item["title"]}, ip=client_ip(request),
        )
        db.commit()
    return {"status": "acknowledged", "alert_id": alert_id}
