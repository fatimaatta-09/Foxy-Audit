"""Content-blind SDK capture coverage for customer workspaces.

This report turns the SDK's durable ``client_id``/``client_seq`` signals into an
auditor-facing evidence check. It intentionally does not claim complete capture:
calls that bypass the SDK are outside the observable system boundary.
"""

from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import resolve_org
from ..db import get_db
from ..models import AuditLog, Organization
from ..schemas import CoverageClient, CoverageGap, CoverageResponse
from .verify import verify_chain

router = APIRouter()


@router.get("/v1/coverage", response_model=CoverageResponse)
def get_capture_coverage(
    limit: int = Query(default=100, ge=1, le=500),
    org: Organization = Depends(resolve_org),
    db: Session = Depends(get_db),
):
    """Report continuity of SDK-reported client sequences without raw content."""
    scoped = AuditLog.org_id == org.id
    identified = (AuditLog.client_id.is_not(None)
                  & AuditLog.client_seq.is_not(None))

    total_events = db.execute(
        select(func.count()).select_from(AuditLog).where(scoped)
    ).scalar_one()
    identified_events = db.execute(
        select(func.count()).select_from(AuditLog).where(scoped, identified)
    ).scalar_one()
    events_without_identity = int(total_events) - int(identified_events)
    instrumented_clients = db.execute(
        select(func.count(func.distinct(AuditLog.client_id)))
        .where(scoped, identified)
    ).scalar_one()
    last_event_at = db.execute(
        select(func.max(func.coalesce(AuditLog.occurred_at, AuditLog.created_at)))
        .where(scoped)
    ).scalar_one()

    # One aggregate row per client keeps the response bounded even when a client
    # has produced a large ledger. The window query below returns only gaps.
    client_stats = db.execute(
        select(
            AuditLog.client_id.label("client_id"),
            func.count().label("events"),
            func.min(AuditLog.client_seq).label("first_client_seq"),
            func.max(AuditLog.client_seq).label("last_client_seq"),
            func.min(AuditLog.seq).label("server_seq_start"),
            func.max(AuditLog.seq).label("server_seq_end"),
            func.max(func.coalesce(AuditLog.occurred_at, AuditLog.created_at))
            .label("last_seen_at"),
        )
        .where(scoped, identified)
        .group_by(AuditLog.client_id)
        .order_by(AuditLog.client_id.asc())
    ).all()

    ordered_seq = select(
        AuditLog.client_id.label("client_id"),
        AuditLog.client_seq.label("client_seq"),
        func.lag(AuditLog.client_seq).over(
            partition_by=AuditLog.client_id,
            order_by=(AuditLog.client_seq.asc(), AuditLog.seq.asc()),
        ).label("previous_client_seq"),
    ).where(scoped, identified).subquery("ordered_client_seq")

    gap_rows = db.execute(
        select(
            ordered_seq.c.client_id,
            ordered_seq.c.client_seq,
            ordered_seq.c.previous_client_seq,
        ).where(
            ordered_seq.c.previous_client_seq.is_not(None),
            ordered_seq.c.client_seq > ordered_seq.c.previous_client_seq + 1,
        )
    ).all()
    gaps_by_client: dict[str, list[CoverageGap]] = defaultdict(list)
    for row in gap_rows:
        start = int(row.previous_client_seq) + 1
        end = int(row.client_seq) - 1
        gaps_by_client[str(row.client_id)].append(
            CoverageGap(start=start, end=end, count=end - start + 1)
        )

    # Repeated client sequence values are a separate signal from a missing range:
    # they can indicate a reused/reset client spool and should be visible to an
    # auditor rather than silently treated as continuous.
    duplicate_rows = db.execute(
        select(
            AuditLog.client_id.label("client_id"),
            AuditLog.client_seq.label("client_seq"),
            func.count().label("occurrences"),
        )
        .where(scoped, identified)
        .group_by(AuditLog.client_id, AuditLog.client_seq)
        .having(func.count() > 1)
        .order_by(AuditLog.client_id.asc(), AuditLog.client_seq.asc())
    ).all()
    duplicates_by_client: dict[str, list[int]] = defaultdict(list)
    duplicate_count = 0
    for row in duplicate_rows:
        client_id = str(row.client_id)
        duplicates_by_client[client_id].append(int(row.client_seq))
        duplicate_count += int(row.occurrences) - 1

    for client_id, stats in ((str(row.client_id), row) for row in client_stats):
        first = int(stats.first_client_seq)
        if first > 1:
            gaps_by_client[client_id].insert(
                0, CoverageGap(start=1, end=first - 1, count=first - 1)
            )

    missing_events = sum(gap.count for gaps in gaps_by_client.values() for gap in gaps)
    anomalous_clients = set(gaps_by_client) | set(duplicates_by_client)

    chain_ok, first_broken_seq, chain_detail = verify_chain(db, org.id)
    if chain_ok is None:
        chain_verification = "not_checked"
    elif chain_ok:
        chain_verification = "verified"
    else:
        chain_verification = "failed"
        chain_detail = f"{chain_detail} (first broken seq {first_broken_seq})"

    if not total_events:
        status = "unknown"
        message = "No audit events have been captured yet; coverage cannot be assessed."
    elif chain_verification == "failed":
        status = "partial"
        message = "Capture evidence exists, but the server chain needs investigation."
    elif missing_events or duplicate_count or events_without_identity:
        status = "partial"
        message = "Foxy observed capture gaps or incomplete client identity signals."
    elif chain_verification != "verified":
        status = "partial"
        message = "Observed client sequences are continuous, but the full chain was not checked."
    else:
        status = "verified"
        message = "Observed SDK client sequences are continuous and the server chain is intact."

    clients = []
    for stats in client_stats[:limit]:
        client_id = str(stats.client_id)
        clients.append(CoverageClient(
            client_id=client_id,
            events=int(stats.events),
            first_client_seq=int(stats.first_client_seq),
            last_client_seq=int(stats.last_client_seq),
            server_seq_start=int(stats.server_seq_start),
            server_seq_end=int(stats.server_seq_end),
            last_seen_at=stats.last_seen_at,
            missing_ranges=gaps_by_client.get(client_id, []),
            duplicate_client_sequences=duplicates_by_client.get(client_id, [])[:20],
        ))

    return CoverageResponse(
        status=status,
        scope="sdk-reported client sequences in this workspace",
        message=message,
        total_events=int(total_events),
        identified_events=int(identified_events),
        events_without_client_identity=events_without_identity,
        instrumented_clients=int(instrumented_clients),
        clients_with_anomalies=len(anomalous_clients),
        missing_events=missing_events,
        duplicate_client_sequences=duplicate_count,
        chain_verified=chain_ok,
        chain_verification=chain_verification,
        chain_detail=chain_detail,
        last_event_at=last_event_at,
        clients=clients,
        limitations=[
            "Coverage measures events that reached Foxy with client identity; it cannot detect calls that bypass the SDK.",
            "A client reset or a first observation after an earlier deployment can appear as an unobserved sequence range.",
            "Raw prompts and responses are not read or stored to calculate this report.",
        ],
    )
