"""Tenant-scoped AI-system inventory and evidence attribution.

The inventory is deliberately content-blind. It records accountable ownership
and operating context; prompt and response content remains outside Foxy Audit.
SDK ingest validates a supplied system_id against this registry and commits that
UUID to V3 event metadata, making historical attribution independently
verifiable with the chain.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import account_audit
from ..auth import require_role, resolve_org
from ..db import get_db
from ..models import AiSystem, Organization, User

router = APIRouter()

Provider = Literal[
    "openai", "azure_openai", "anthropic", "google", "aws_bedrock",
    "self_hosted", "other",
]
Environment = Literal["development", "staging", "production"]
DataClassification = Literal["public", "internal", "confidential", "regulated"]
RiskTier = Literal["low", "medium", "high", "critical"]
LifecycleStatus = Literal["draft", "active", "retired"]


class AiSystemCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    owner_email: str | None = Field(default=None, max_length=320)
    purpose: str = Field(min_length=1, max_length=256)
    provider: Provider = "other"
    model_name: str | None = Field(
        default=None, max_length=128, pattern=r"^[A-Za-z0-9_.:/-]{1,128}$")
    environment: Environment = "production"
    data_classification: DataClassification = "internal"
    risk_tier: RiskTier = "medium"
    lifecycle_status: LifecycleStatus = "active"

    @field_validator("name", "purpose", "owner_email", "model_name")
    @classmethod
    def _strip_declared_metadata(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("value must not be blank")
        return value


class AiSystemUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    owner_email: str | None = Field(default=None, max_length=320)
    purpose: str | None = Field(default=None, min_length=1, max_length=256)
    provider: Provider | None = None
    model_name: str | None = Field(
        default=None, max_length=128, pattern=r"^[A-Za-z0-9_.:/-]{1,128}$")
    environment: Environment | None = None
    data_classification: DataClassification | None = None
    risk_tier: RiskTier | None = None
    lifecycle_status: LifecycleStatus | None = None

    @field_validator("name", "purpose", "owner_email", "model_name")
    @classmethod
    def _strip_declared_metadata(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("value must not be blank")
        return value


class AiSystemItem(BaseModel):
    id: str
    name: str
    owner_email: str
    purpose: str
    provider: str
    model_name: str | None = None
    environment: str
    data_classification: str
    risk_tier: str
    lifecycle_status: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


def _serialize(system: AiSystem) -> AiSystemItem:
    return AiSystemItem(
        id=str(system.id), name=system.name, owner_email=system.owner_email,
        purpose=system.purpose, provider=system.provider, model_name=system.model_name,
        environment=system.environment, data_classification=system.data_classification,
        risk_tier=system.risk_tier, lifecycle_status=system.lifecycle_status,
        created_at=system.created_at, updated_at=system.updated_at,
    )


def _get_system(db: Session, org_id, system_id: uuid.UUID) -> AiSystem:
    system = db.execute(
        select(AiSystem).where(AiSystem.id == system_id, AiSystem.org_id == org_id)
    ).scalar_one_or_none()
    if system is None:
        # Do not distinguish a foreign tenant's UUID from a missing one.
        raise HTTPException(status_code=404, detail="AI system not found")
    return system


def _ensure_unique_name(db: Session, org_id, name: str, exclude_id: uuid.UUID | None = None) -> None:
    stmt = select(AiSystem.id).where(AiSystem.org_id == org_id, AiSystem.name == name)
    if exclude_id is not None:
        stmt = stmt.where(AiSystem.id != exclude_id)
    if db.execute(stmt).scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="An AI system with that name already exists")


@router.get("/v1/systems", response_model=list[AiSystemItem])
def list_systems(
    org: Organization = Depends(resolve_org),
    db: Session = Depends(get_db),
):
    """List this workspace's declared AI systems. Reads are safe for members and SDKs."""
    systems = db.execute(
        select(AiSystem).where(AiSystem.org_id == org.id)
        .order_by(AiSystem.lifecycle_status.asc(), AiSystem.name.asc())
    ).scalars().all()
    return [_serialize(system) for system in systems]


@router.get("/v1/systems/{system_id}", response_model=AiSystemItem)
def get_system(
    system_id: uuid.UUID,
    org: Organization = Depends(resolve_org),
    db: Session = Depends(get_db),
):
    return _serialize(_get_system(db, org.id, system_id))


@router.post("/v1/systems", response_model=AiSystemItem, status_code=201)
def create_system(
    body: AiSystemCreate,
    admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Register an accountable AI system. Dashboard admins only."""
    _ensure_unique_name(db, admin.org_id, body.name)
    system = AiSystem(
        org_id=admin.org_id,
        name=body.name,
        owner_email=(body.owner_email or admin.email).lower(),
        purpose=body.purpose,
        provider=body.provider,
        model_name=body.model_name,
        environment=body.environment,
        data_classification=body.data_classification,
        risk_tier=body.risk_tier,
        lifecycle_status=body.lifecycle_status,
        created_by=admin.id,
    )
    db.add(system)
    db.flush()
    account_audit.record_account_action(
        db, org_id=admin.org_id, actor_email=admin.email, action="system.create",
        target=system.name,
        detail={"system_id": str(system.id), "risk_tier": system.risk_tier,
                "environment": system.environment, "lifecycle_status": system.lifecycle_status},
    )
    db.commit()
    db.refresh(system)
    return _serialize(system)


@router.put("/v1/systems/{system_id}", response_model=AiSystemItem)
def update_system(
    system_id: uuid.UUID,
    body: AiSystemUpdate,
    admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Update declared operating context without rewriting ledger evidence."""
    system = _get_system(db, admin.org_id, system_id)
    values = body.model_dump(exclude_unset=True)
    if not values:
        raise HTTPException(status_code=422, detail="provide at least one system field to update")
    required_fields = {
        "name", "owner_email", "purpose", "provider", "environment",
        "data_classification", "risk_tier", "lifecycle_status",
    }
    if any(values.get(field) is None for field in required_fields if field in values):
        raise HTTPException(status_code=422, detail="required system fields cannot be null")
    if "name" in values:
        _ensure_unique_name(db, admin.org_id, values["name"], exclude_id=system.id)
    for field, value in values.items():
        setattr(system, field, value)
    account_audit.record_account_action(
        db, org_id=admin.org_id, actor_email=admin.email, action="system.update",
        target=system.name, detail={"system_id": str(system.id), "fields": sorted(values)},
    )
    db.commit()
    db.refresh(system)
    return _serialize(system)


@router.post("/v1/systems/{system_id}/retire", response_model=AiSystemItem)
def retire_system(
    system_id: uuid.UUID,
    admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Retire a system so it cannot receive new events; historical links remain."""
    system = _get_system(db, admin.org_id, system_id)
    if system.lifecycle_status != "retired":
        system.lifecycle_status = "retired"
        account_audit.record_account_action(
            db, org_id=admin.org_id, actor_email=admin.email, action="system.retire",
            target=system.name, detail={"system_id": str(system.id)},
        )
        db.commit()
        db.refresh(system)
    return _serialize(system)
