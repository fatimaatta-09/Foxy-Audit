"""Generic staff data browser (Phase 4 #3) — view/search every table, edit or
delete rows on the ones where that's actually safe.

TABLE_REGISTRY is the single source of truth for what this surface can touch.
A table name from the URL is ONLY ever used as a dict lookup key into this
registry — never interpolated into SQL or used to look up an arbitrary model
by string, so there is no path from a crafted `table` value to an unintended
table. Four tables are permanently locked to read-only here because mutating
them through a generic UI would defeat the reason they exist:

  - audit_logs     — the tamper-evident hash chain; an undetected edit here
                      is the exact failure mode the chain exists to prevent.
  - admin_actions  — append-only staff audit trail; if this were editable a
                      compromised staff account could erase its own tracks.
  - chain_anchors  — receipts of an external blockchain publish; editing the
                      local record doesn't undo the on-chain fact, it just
                      makes our own database lie about it.
  - stripe_events  — the webhook idempotency log; deleting a row means a
                      replayed webhook gets reprocessed as if it were new.

organizations and staff_users are viewable here but NOT editable here either
— they already have purpose-built, audited mutation endpoints (suspend/
enable, disable) that a raw field-editor would bypass.

Every edit/delete still writes exactly one admin_actions row, same as every
other staff mutation in this codebase.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import String, and_, cast, func, inspect, or_, select
from sqlalchemy.orm import Session

from ..admin_audit import client_ip, record_admin_action
from ..auth import require_platform_role, require_step_up_dep
from ..db import get_db
from ..models import (
    AdminAction, ApiKey, AuditLog, ChainAnchor, Invoice, MarketingLead,
    Organization, OrgPolicy, StaffUser, StripeEvent, TrafficEvent, UsageDaily,
    User, WorkerHeartbeat,
)

router = APIRouter()

# Never returned by this endpoint, on ANY table, regardless of role. Viewing a
# hash is bad hygiene even though it's one-way — there's no legitimate reason
# for it and it costs nothing to strip.
# Columns this browser must never render, on any table. The *_key_enc columns hold
# customers' BYOK provider keys: staff never read a tenant's provider key, not even
# as ciphertext (see app/crypto_secrets.py).
# `payload` is the raw webhook body a processor sent us (M3d · #102). Paddle's
# carries the customer's name, email and billing address, and Stripe's carries
# the equivalent — dormant only because no Stripe payload has ever been written.
# Neither belongs in a generic staff browser: a product that sells on data
# minimisation should not hand a reviewer a table of its customers' addresses
# because it was one line less work than a purpose-built route.
#
# Safe as a GLOBAL rule, checked rather than assumed: of the 14 registered
# tables, `stripe_events` is the only one carrying a column of this name, and it
# appears in neither that table's `search` nor its `filterable` list — so nothing
# staff legitimately read or filter on is lost. `payment_events` is deliberately
# not registered here at all; it has a purpose-built route in `admin_billing`
# that builds every item from an explicit field list.
_NEVER_EXPOSE = {"password_hash", "key_hash", "api_key_hash",
                 "gemini_key_enc", "openai_key_enc", "payload"}

TABLE_REGISTRY: dict[str, dict[str, Any]] = {
    # "filterable" is a SEPARATE, explicit allowlist from "search": search is
    # ILIKE substring matching meant for text fields shown in the UI's search
    # box; filterable is exact-match via ?col_<name>=<value> and may include
    # identifiers/FKs/enums that don't belong in free-text search. Both are
    # allowlists a column must be named on to be usable — being a real column
    # on the model is NOT sufficient by itself (see the col_ filter loop below,
    # which was the actual bug: it originally trusted any real column name).
    "organizations": {"model": Organization, "pk": "id",
        "search": ["name", "contact_email", "plan_tier"],
        "filterable": ["id", "name", "plan_tier", "subscription_status", "suspended", "contact_email"],
        "editable": [], "deletable": False},
    "audit_logs": {"model": AuditLog, "pk": "id",
        "search": ["policy_tag", "grading_status"],
        "filterable": ["id", "org_id", "seq", "policy_tag", "grading_status"],
        "editable": [], "deletable": False},
    "org_policies": {"model": OrgPolicy, "pk": "org_id", "search": [], "filterable": ["org_id"],
        "editable": ["pii_detection", "prompt_injection", "regulated_data_mode", "max_token_threshold"],
        "deletable": False},
    "users": {"model": User, "pk": "id", "search": ["email", "role"],
        "filterable": ["id", "org_id", "email", "role", "disabled"],
        "editable": ["role", "disabled"], "deletable": False},
    "api_keys": {"model": ApiKey, "pk": "id", "search": ["name", "key_prefix", "status"],
        "filterable": ["id", "org_id", "name", "status"],
        # NOT editable here: status is a paired field with revoked_at, and
        # flipping it back to "active" alone (bypassing DELETE /v1/keys/{id},
        # which sets both together) silently un-revokes a live credential.
        # Revoke/rotate stays exclusively on the audited customer-facing route.
        "editable": [], "deletable": False},
    "chain_anchors": {"model": ChainAnchor, "pk": "id",
        "search": ["chain", "status", "tx_hash"],
        "filterable": ["id", "org_id", "chain", "status"], "editable": [], "deletable": False},
    "staff_users": {"model": StaffUser, "pk": "id",
        "search": ["email", "platform_role"],
        "filterable": ["id", "email", "platform_role", "disabled"], "editable": [], "deletable": False},
    "admin_actions": {"model": AdminAction, "pk": "id",
        "search": ["action", "target_type"],
        "filterable": ["id", "staff_user_id", "target_org_id", "action", "target_type"],
        "editable": [], "deletable": False},
    "traffic_events": {"model": TrafficEvent, "pk": "id",
        "search": ["site", "path", "method"],
        "filterable": ["id", "org_id", "site", "method", "status_code"], "editable": [], "deletable": False},
    "marketing_leads": {"model": MarketingLead, "pk": "id",
        "search": ["email", "name", "company", "status", "source"],
        "filterable": ["id", "email", "status", "source", "converted_org_id"],
        "editable": ["name", "company", "status", "source"], "deletable": True},
    "stripe_events": {"model": StripeEvent, "pk": "id",
        "search": ["type", "status", "stripe_event_id"],
        "filterable": ["id", "org_id", "type", "status"], "editable": [], "deletable": False},
    "invoices": {"model": Invoice, "pk": "id",
        "search": ["status", "currency", "stripe_invoice_id"],
        "filterable": ["id", "org_id", "status", "stripe_invoice_id"],
        "editable": ["status"], "deletable": True},
    "usage_daily": {"model": UsageDaily, "pk": "id", "search": [],
        "filterable": ["id", "org_id", "day"],
        "editable": ["logs_count", "tokens_sum", "breach_count", "graded_count", "failed_count", "pending_count"],
        "deletable": True},
    "worker_heartbeat": {"model": WorkerHeartbeat, "pk": "id", "search": [], "filterable": ["id"],
        "editable": [], "deletable": False},
}

# Locked outright — never editable/deletable no matter what a future edit to
# the registry above might accidentally allow. Belt-and-braces on top of the
# per-table "editable"/"deletable" flags.
_HARD_LOCKED = frozenset({"audit_logs", "admin_actions", "chain_anchors", "stripe_events"})


class UpdateBody(BaseModel):
    fields: dict[str, Any]


def _spec(table: str) -> dict[str, Any]:
    spec = TABLE_REGISTRY.get(table)
    if spec is None:
        raise HTTPException(status_code=404, detail="unknown table")
    return spec


def _jsonable(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _serialize(obj: Any, columns) -> dict[str, Any]:
    return {
        col.name: _jsonable(getattr(obj, col.name))
        for col in columns
        if col.name not in _NEVER_EXPOSE
    }


def _coerce(column, value: Any) -> Any:
    """Cast an incoming JSON value to the column's Python type. Internal tool
    with a matching purpose-built frontend — this is a safety net against a
    mistyped field, not a defense against a hostile caller."""
    py_type = column.type.python_type
    if value is None or isinstance(value, py_type):
        return value
    try:
        if py_type is bool:
            return value if isinstance(value, bool) else str(value).strip().lower() in ("1", "true", "yes")
        return py_type(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"bad value for {column.name!r}")


def _lookup(db: Session, spec: dict[str, Any], row_id: str):
    model = spec["model"]
    pk_attr = getattr(model, spec["pk"])
    try:
        pk_val = uuid.UUID(row_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="invalid id")
    obj = db.execute(select(model).where(pk_attr == pk_val)).scalar_one_or_none()
    if obj is None:
        raise HTTPException(status_code=404, detail="row not found")
    return obj


@router.get("/v1/data/tables")
def list_tables(staff: StaffUser = Depends(require_platform_role("viewer"))):
    return [
        {"table": name, "editable": bool(spec["editable"]) and name not in _HARD_LOCKED,
         "deletable": spec["deletable"] and name not in _HARD_LOCKED}
        for name, spec in TABLE_REGISTRY.items()
    ]


@router.get("/v1/data/{table}")
def list_rows(
    table: str,
    request: Request,
    q: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
    staff: StaffUser = Depends(require_platform_role("viewer")),
    db: Session = Depends(get_db),
):
    spec = _spec(table)
    model = spec["model"]
    mapper = inspect(model)
    columns = list(mapper.columns)
    col_by_name = {c.name: c for c in columns}

    stmt = select(model)
    count_stmt = select(func.count()).select_from(model)

    conditions = []
    filterable = set(spec["filterable"])
    for key, val in request.query_params.items():
        if key.startswith("col_") and val:
            col_name = key[4:]
            # Being a real column is NOT sufficient — must be on this table's
            # explicit filterable allowlist. Without this, an exact-match
            # filter on e.g. password_hash/key_hash would work as a yes/no
            # oracle (total==1 confirms a guessed hash matches a real row)
            # even though _serialize() already strips those fields from the
            # response body — filtering-as-oracle leaks the same secret via a
            # side channel. _NEVER_EXPOSE is checked again here directly as a
            # second, independent guard in case a future edit ever puts a
            # sensitive column on some table's filterable list by mistake.
            if col_name not in filterable or col_name in _NEVER_EXPOSE:
                continue
            col = col_by_name.get(col_name)
            if col is not None:
                conditions.append(cast(col, String) == val)
    if q and spec["search"]:
        like = f"%{q}%"
        text_conditions = [cast(col_by_name[f], String).ilike(like)
                            for f in spec["search"] if f in col_by_name]
        if text_conditions:
            conditions.append(or_(*text_conditions))
    if conditions:
        stmt = stmt.where(and_(*conditions))
        count_stmt = count_stmt.where(and_(*conditions))

    total = db.execute(count_stmt).scalar_one()
    pk_col = col_by_name[spec["pk"]]
    stmt = stmt.order_by(pk_col.desc()).offset((page - 1) * limit).limit(limit)
    rows = db.execute(stmt).scalars().all()

    return {
        "total": total,
        "rows": [_serialize(r, columns) for r in rows],
        "editable_fields": [] if table in _HARD_LOCKED else spec["editable"],
        "deletable": spec["deletable"] and table not in _HARD_LOCKED,
    }


@router.patch("/v1/data/{table}/{row_id}", dependencies=[Depends(require_step_up_dep)])
def update_row(
    table: str, row_id: str, body: UpdateBody, request: Request,
    staff: StaffUser = Depends(require_platform_role("operator")),
    db: Session = Depends(get_db),
):
    spec = _spec(table)
    editable = [] if table in _HARD_LOCKED else spec["editable"]
    if not editable:
        raise HTTPException(status_code=403, detail="this table is read-only in the data browser")

    bad_fields = sorted(set(body.fields) - set(editable))
    if bad_fields:
        raise HTTPException(status_code=400, detail=f"not editable: {', '.join(bad_fields)}")

    obj = _lookup(db, spec, row_id)
    mapper = inspect(spec["model"])
    col_by_name = {c.name: c for c in mapper.columns}
    for name, value in body.fields.items():
        setattr(obj, name, _coerce(col_by_name[name], value))

    record_admin_action(
        db, staff, f"data.edit.{table}", target_type=table, target_id=row_id,
        detail={"fields": sorted(body.fields)}, ip=client_ip(request),
    )
    db.commit()
    return {"status": "updated"}


@router.delete("/v1/data/{table}/{row_id}", dependencies=[Depends(require_step_up_dep)])
def delete_row(
    table: str, row_id: str, request: Request,
    staff: StaffUser = Depends(require_platform_role("operator")),
    db: Session = Depends(get_db),
):
    spec = _spec(table)
    deletable = spec["deletable"] and table not in _HARD_LOCKED
    if not deletable:
        raise HTTPException(status_code=403, detail="this table does not allow deletion")

    obj = _lookup(db, spec, row_id)
    mapper = inspect(spec["model"])
    snapshot = _serialize(obj, mapper.columns)

    record_admin_action(
        db, staff, f"data.delete.{table}", target_type=table, target_id=row_id,
        detail={"snapshot": snapshot}, ip=client_ip(request),
    )
    db.delete(obj)
    db.commit()
    return {"status": "deleted"}
