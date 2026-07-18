"""Cross-org anchor monitoring and audited manual re-anchoring."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import anchor as anchor_engine
from ..admin_audit import client_ip, record_admin_action
from ..auth import require_platform_role, set_org_scope_for_staff
from ..config import Settings, get_settings
from ..db import get_db
from ..models import AuditLog, ChainAnchor, Organization, StaffUser

router = APIRouter()


def _uuid(value: str, label: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"invalid {label}") from exc


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _iso(value: datetime | None) -> str | None:
    value = _utc(value)
    return value.isoformat() if value else None


def _serialize(anchor: ChainAnchor) -> dict:
    return {
        "id": str(anchor.id),
        "root_hash": anchor.root_hash,
        "last_seq": anchor.last_seq,
        "chain": anchor.chain,
        "tx_hash": anchor.tx_hash,
        "block_number": anchor.block_number,
        "status": anchor.status,
        "detail": anchor.detail,
        "anchored_at": _iso(anchor.anchored_at),
        "confirmed_at": _iso(anchor.confirmed_at),
    }


def _wallet_status(settings: Settings) -> dict:
    if settings.anchor_provider.lower() != "evm":
        return {"state": "not_applicable", "provider": settings.anchor_provider}
    if not all((settings.anchor_evm_rpc_url, settings.anchor_evm_private_key,
                settings.anchor_evm_contract)):
        return {"state": "unavailable", "provider": "evm",
                "detail": "EVM wallet configuration is incomplete"}
    try:
        from web3 import Web3

        w3 = Web3(Web3.HTTPProvider(settings.anchor_evm_rpc_url))
        account = w3.eth.account.from_key(settings.anchor_evm_private_key)
        balance = int(w3.eth.get_balance(account.address))
        floor = int(settings.anchor_wallet_min_balance_wei)
        return {
            "state": "below_floor" if floor > 0 and balance < floor else "ok",
            "provider": "evm",
            "address": account.address,
            "balance_wei": str(balance),
            "floor_wei": str(floor),
        }
    except Exception:  # noqa: BLE001 - do not expose RPC or key-bearing errors
        return {"state": "unavailable", "provider": "evm",
                "detail": "wallet balance could not be read"}


@router.get("/v1/anchors")
def list_anchor_monitor(
    org_id: str | None = None,
    staff: StaffUser = Depends(require_platform_role("viewer")),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    oid = _uuid(org_id, "org id") if org_id else None
    if oid is not None:
        set_org_scope_for_staff(db, oid)

    org_stmt = select(Organization).order_by(Organization.name.asc())
    anchor_stmt = select(ChainAnchor).order_by(
        ChainAnchor.anchored_at.desc(), ChainAnchor.id.desc())
    head_stmt = select(AuditLog.org_id, func.max(AuditLog.seq)).group_by(AuditLog.org_id)
    confirmed_stmt = select(
        ChainAnchor.org_id, func.max(ChainAnchor.confirmed_at)
    ).where(ChainAnchor.status == "confirmed").group_by(ChainAnchor.org_id)
    if oid is not None:
        org_stmt = org_stmt.where(Organization.id == oid)
        anchor_stmt = anchor_stmt.where(ChainAnchor.org_id == oid)
        head_stmt = head_stmt.where(AuditLog.org_id == oid)
        confirmed_stmt = confirmed_stmt.where(ChainAnchor.org_id == oid)

    orgs = db.execute(org_stmt).scalars().all()
    anchors = db.execute(anchor_stmt).scalars().all()
    heads = dict(db.execute(head_stmt).all())
    last_confirmed = dict(db.execute(confirmed_stmt).all())
    latest = {}
    for anchor in anchors:
        latest.setdefault(anchor.org_id, anchor)

    now = datetime.now(timezone.utc)
    organizations = []
    status_counts = {}
    failed_orgs = []
    stale_orgs = []
    for org in orgs:
        anchor = latest.get(org.id)
        status = anchor.status if anchor else "never"
        status_counts[status] = status_counts.get(status, 0) + 1
        confirmed_at = _utc(last_confirmed.get(org.id))
        stale = bool(
            confirmed_at is not None
            and settings.anchor_stale_alert_seconds > 0
            and (now - confirmed_at).total_seconds() > settings.anchor_stale_alert_seconds
        )
        if status == "failed":
            failed_orgs.append(str(org.id))
        if stale:
            stale_orgs.append(str(org.id))
        organizations.append({
            "org_id": str(org.id),
            "org_name": org.name,
            "plan_tier": org.plan_tier,
            "current_head_seq": int(heads.get(org.id) or 0),
            "last_confirmed_at": _iso(confirmed_at),
            "stale": stale,
            "latest": _serialize(anchor) if anchor else None,
        })

    return {
        "provider": settings.anchor_provider,
        "enabled": bool(settings.anchor_enabled),
        "wallet": _wallet_status(settings),
        "summary": {
            "organizations": len(organizations),
            "by_status": status_counts,
            "failed_orgs": failed_orgs,
            "stale_orgs": stale_orgs,
            "stale_after_seconds": settings.anchor_stale_alert_seconds,
        },
        "organizations": organizations,
    }


@router.post("/v1/anchors/{org_id}/anchor")
def reanchor(
    org_id: str,
    request: Request,
    staff: StaffUser = Depends(require_platform_role("operator")),
    db: Session = Depends(get_db),
):
    oid = _uuid(org_id, "org id")
    org = db.get(Organization, oid)
    if org is None:
        raise HTTPException(status_code=404, detail="organization not found")
    _, head_seq = anchor_engine.head_of(db, oid)
    if head_seq == 0:
        raise HTTPException(status_code=409, detail="organization has no audit logs to anchor")

    # anchor_org owns its provider transaction and commits internally. Stage the
    # admin row first so that commit includes both the action and anchor receipt.
    record_admin_action(
        db, staff, "anchor.reanchor", target_org_id=oid,
        target_type="organization", target_id=str(oid),
        detail={"force": True, "head_seq": head_seq}, ip=client_ip(request),
    )
    try:
        anchor = anchor_engine.anchor_org(db, oid, force=True)
    except Exception as exc:  # noqa: BLE001 - provider failure must not leak details
        db.rollback()
        raise HTTPException(status_code=502, detail="anchor provider unavailable") from exc
    if anchor is None:
        db.rollback()
        raise HTTPException(status_code=409, detail="nothing to anchor")
    return {"organization_id": str(oid), "anchor": _serialize(anchor)}
