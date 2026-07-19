"""Content-blind SDK capture coverage calculations shared by evidence surfaces.

The result deliberately measures only events that reached Foxy Audit with a
durable SDK client identity. It is evidence about an observable boundary, not a
claim that every model call in a customer's environment was captured.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import AuditLog
from .schemas import CoverageClient, CoverageGap, CoverageResponse


def calculate_capture_coverage(
    db: Session,
    org_id,
    *,
    limit: int = 100,
    start: datetime | None = None,
    end: datetime | None = None,
    chain_verified: bool | None,
    chain_detail: str,
) -> CoverageResponse:
    """Summarize SDK sequence continuity for an org or a bounded report period.

    When a period is supplied, the first observed client sequence is not treated
    as a gap: its prior events may simply be outside that report's date range.
    """
    conditions = [AuditLog.org_id == org_id]
    if start is not None:
        conditions.append(AuditLog.created_at >= start)
    if end is not None:
        conditions.append(AuditLog.created_at < end)
    identified = AuditLog.client_id.is_not(None) & AuditLog.client_seq.is_not(None)
    identified_conditions = [*conditions, identified]

    total_events = int(db.execute(
        select(func.count()).select_from(AuditLog).where(*conditions)
    ).scalar_one())
    identified_events = int(db.execute(
        select(func.count()).select_from(AuditLog).where(*identified_conditions)
    ).scalar_one())
    events_without_identity = total_events - identified_events
    instrumented_clients = int(db.execute(
        select(func.count(func.distinct(AuditLog.client_id)))
        .where(*identified_conditions)
    ).scalar_one())
    last_event_at = db.execute(
        select(func.max(func.coalesce(AuditLog.occurred_at, AuditLog.created_at)))
        .where(*conditions)
    ).scalar_one()

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
        .where(*identified_conditions)
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
    ).where(*identified_conditions).subquery("ordered_client_seq")

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
        gap_start = int(row.previous_client_seq) + 1
        gap_end = int(row.client_seq) - 1
        gaps_by_client[str(row.client_id)].append(
            CoverageGap(start=gap_start, end=gap_end, count=gap_end - gap_start + 1)
        )

    duplicate_rows = db.execute(
        select(
            AuditLog.client_id.label("client_id"),
            AuditLog.client_seq.label("client_seq"),
            func.count().label("occurrences"),
        )
        .where(*identified_conditions)
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

    # Initial sequence gaps are meaningful only for the all-time workspace
    # report. A date-bounded Passport may begin after a client was deployed.
    if start is None:
        for client_id, stats in ((str(row.client_id), row) for row in client_stats):
            first = int(stats.first_client_seq)
            if first > 1:
                gaps_by_client[client_id].insert(
                    0, CoverageGap(start=1, end=first - 1, count=first - 1)
                )

    missing_events = sum(
        gap.count for gaps in gaps_by_client.values() for gap in gaps
    )
    anomalous_clients = set(gaps_by_client) | set(duplicates_by_client)

    if chain_verified is None:
        chain_verification = "not_checked"
    elif chain_verified:
        chain_verification = "verified"
    else:
        chain_verification = "failed"

    if not total_events:
        status = "unknown"
        message = "No audit events were captured in this scope; coverage cannot be assessed."
    elif chain_verification == "failed":
        status = "partial"
        message = "Capture evidence exists, but the chain needs investigation."
    elif missing_events or duplicate_count or events_without_identity:
        status = "partial"
        message = "Foxy observed capture gaps or incomplete client identity signals."
    elif chain_verification != "verified":
        status = "partial"
        message = "Observed client sequences are continuous, but the chain was not checked."
    else:
        status = "verified"
        message = "Observed SDK client sequences are continuous and the chain is intact."

    scope = (
        "SDK-reported client sequences in this workspace"
        if start is None
        else "SDK-reported client sequences captured during this report period"
    )
    limitations = [
        "Coverage measures events that reached Foxy with client identity; it cannot detect calls that bypass the SDK.",
        "A client reset or a first observation after an earlier deployment can appear as an unobserved sequence range.",
        "Raw prompts and responses are not read or stored to calculate this report.",
    ]
    if start is not None:
        limitations[1] = (
            "The first client sequence in this report is not treated as missing because prior events may be outside the report period."
        )

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
        scope=scope,
        message=message,
        total_events=total_events,
        identified_events=identified_events,
        events_without_client_identity=events_without_identity,
        instrumented_clients=instrumented_clients,
        clients_with_anomalies=len(anomalous_clients),
        missing_events=missing_events,
        duplicate_client_sequences=duplicate_count,
        chain_verified=chain_verified,
        chain_verification=chain_verification,
        chain_detail=chain_detail,
        last_event_at=last_event_at,
        clients=clients,
        limitations=limitations,
    )
