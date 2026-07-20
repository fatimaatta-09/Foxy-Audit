"""Staff-managed finite evaluation campaigns for judges and design partners."""

from __future__ import annotations

from datetime import datetime, timezone
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..admin_audit import client_ip, record_admin_action
from ..auth import require_platform_role
from ..db import get_db
from ..models import EvaluationCampaign, EvaluationRedemption, StaffUser
from .billing import _normalise_offer_code, _offer_code_hash

router = APIRouter()
_OFFER_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{2,63}$")


class CampaignItem(BaseModel):
    offer_id: str
    label: str
    credits: int
    duration_days: int
    max_redemptions: int
    redemptions: int
    remaining_slots: int
    status: str
    starts_at: str | None = None
    ends_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class CreateCampaignRequest(BaseModel):
    offer_id: str = Field(min_length=3, max_length=64)
    label: str = Field(min_length=1, max_length=128)
    code: str = Field(min_length=16, max_length=128)
    credits: int = Field(gt=0, le=10_000_000)
    duration_days: int = Field(gt=0, le=3650)
    max_redemptions: int = Field(gt=0, le=100_000)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _item(campaign: EvaluationCampaign, redemptions: int) -> CampaignItem:
    return CampaignItem(
        offer_id=campaign.offer_id,
        label=campaign.label,
        credits=campaign.credits,
        duration_days=campaign.duration_days,
        max_redemptions=campaign.max_redemptions,
        redemptions=redemptions,
        remaining_slots=max(0, campaign.max_redemptions - redemptions),
        status=campaign.status,
        starts_at=_iso(campaign.starts_at),
        ends_at=_iso(campaign.ends_at),
        created_at=_iso(campaign.created_at),
        updated_at=_iso(campaign.updated_at),
    )


def _redemption_counts(db: Session) -> dict[str, int]:
    return {
        offer_id: int(count)
        for offer_id, count in db.execute(
            select(EvaluationRedemption.offer_id, func.count(EvaluationRedemption.id))
            .group_by(EvaluationRedemption.offer_id)
        ).all()
    }


@router.get("/v1/evaluation-campaigns", response_model=list[CampaignItem])
def list_campaigns(
    staff: StaffUser = Depends(require_platform_role("viewer")),
    db: Session = Depends(get_db),
):
    counts = _redemption_counts(db)
    campaigns = db.execute(
        select(EvaluationCampaign).order_by(EvaluationCampaign.created_at.desc())
    ).scalars().all()
    return [_item(campaign, counts.get(campaign.offer_id, 0)) for campaign in campaigns]


@router.post("/v1/evaluation-campaigns")
def create_campaign(
    payload: CreateCampaignRequest,
    request: Request,
    staff: StaffUser = Depends(require_platform_role("superadmin")),
    db: Session = Depends(get_db),
):
    offer_id = payload.offer_id.strip().lower()
    label = payload.label.strip()
    code = _normalise_offer_code(payload.code)
    if not _OFFER_ID.fullmatch(offer_id):
        raise HTTPException(status_code=422, detail="offer_id must be a lowercase slug")
    if not label or len(code) < 16:
        raise HTTPException(status_code=422, detail="label and code are required")

    code_hash = _offer_code_hash(code)
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:offer_id))"),
                   {"offer_id": offer_id})
    duplicate = db.execute(
        select(EvaluationCampaign.id).where(
            (EvaluationCampaign.offer_id == offer_id) |
            (EvaluationCampaign.code_hash == code_hash)
        )
    ).scalar_one_or_none()
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="campaign offer_id or code already exists")

    campaign = EvaluationCampaign(
        offer_id=offer_id,
        label=label,
        code_hash=code_hash,
        credits=payload.credits,
        duration_days=payload.duration_days,
        max_redemptions=payload.max_redemptions,
        status="active",
        created_by=staff.id,
    )
    db.add(campaign)
    record_admin_action(
        db, staff, "evaluation_campaign.create", target_type="evaluation_campaign",
        target_id=offer_id,
        detail={"label": label, "credits": payload.credits,
                "duration_days": payload.duration_days,
                "max_redemptions": payload.max_redemptions},
        ip=client_ip(request),
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="campaign offer_id or code already exists")
    db.refresh(campaign)
    # The plaintext code is returned only in this creation response. It is not
    # stored, listed, or included in audit detail.
    return {
        **_item(campaign, 0).model_dump(),
        "redemption_code": code,
    }


@router.post("/v1/evaluation-campaigns/{offer_id}/revoke")
def revoke_campaign(
    offer_id: str,
    request: Request,
    staff: StaffUser = Depends(require_platform_role("operator")),
    db: Session = Depends(get_db),
):
    campaign = db.execute(
        select(EvaluationCampaign).where(EvaluationCampaign.offer_id == offer_id.strip().lower())
    ).scalar_one_or_none()
    if campaign is None:
        raise HTTPException(status_code=404, detail="campaign not found")
    if campaign.status == "revoked":
        raise HTTPException(status_code=409, detail="campaign is already revoked")
    campaign.status = "revoked"
    campaign.updated_at = datetime.now(timezone.utc)
    record_admin_action(
        db, staff, "evaluation_campaign.revoke", target_type="evaluation_campaign",
        target_id=campaign.offer_id, detail={"label": campaign.label},
        ip=client_ip(request),
    )
    db.commit()
    return {"status": campaign.status, "offer_id": campaign.offer_id}
