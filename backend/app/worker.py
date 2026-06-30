"""Background Gemini grading worker — runs in-process daemon threads.

IMPORTANT: This module intentionally uses Python's standard-library
ThreadPoolExecutor instead of Celery. Reasons:

  1. No external broker (Redis) required — the XPRIZE demo runs with
     only Postgres, which is already in docker-compose.yml.
  2. Identical throughput for a demo workload (sub-10 req/s).
  3. Failure/retry logic is preserved; we simply re-submit to the pool.

For production, swap submit_batch() with a Celery/Redis call by:
  - Adding `redis` service to docker-compose.yml
  - Reinstating the Celery task decorator
  - Starting `celery -A app.worker worker` alongside uvicorn

The contract the router depends on is just:
    worker.submit_batch(batch_payloads: list[dict]) -> None
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session
from sqlalchemy.exc import OperationalError

from . import gemini
from .chain import GENESIS_HASH, compute_chain_hash
from .db import SessionLocal
from .models import AuditLog, OrgPolicy

log = logging.getLogger("foxy.worker")

# Daemon pool — threads do not block process exit.
_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="foxy-grader")


def _process_batch(batch_payloads: list[dict[str, Any]], retries_left: int = 3) -> None:
    """Do the actual DB work in a pool thread."""
    if not batch_payloads:
        return

    org_id = batch_payloads[0]["org_id"]
    db: Session = SessionLocal()
    try:
        db.execute(text("SET LOCAL app.current_org = :oid"), {"oid": org_id})

        # Fetch org's policy config once for the whole batch
        import uuid as _uuid
        policy_row = db.get(OrgPolicy, _uuid.UUID(org_id))
        policy_config = {
            "pii_detection": policy_row.pii_detection,
            "prompt_injection": policy_row.prompt_injection,
            "regulated_data_mode": policy_row.regulated_data_mode,
            "max_token_threshold": policy_row.max_token_threshold,
        } if policy_row else None

        prev = db.execute(
            select(AuditLog.seq, AuditLog.chain_hash)
            .where(AuditLog.org_id == org_id)
            .order_by(AuditLog.seq.desc())
            .limit(1)
            .with_for_update()
        ).first()

        prev_seq = prev.seq if prev else 0
        prev_hash = prev.chain_hash if prev else GENESIS_HASH

        objects_to_save = []
        for item in batch_payloads:
            # Gemini evaluation — fail-open so the chain row is always written.
            try:
                verdict = gemini.evaluate(item, policy_config)
                verdict_dict = verdict.model_dump()
            except Exception as exc:
                log.error("Gemini evaluation failed for item, using safe fallback: %s", exc)
                verdict_dict = {
                    "policy_breach": False,
                    "reason": "gemini_unavailable",
                    "risk_score": 0,
                }

            seq = prev_seq + 1
            chain_hash = compute_chain_hash(
                org_id=org_id,
                prompt_hash=item["prompt_hash"],
                response_hash=item["response_hash"],
                token_count=item["token_count"],
                policy_tag=item["policy_tag"],
                seq=seq,
                prev_hash=prev_hash,
            )
            row = AuditLog(
                org_id=org_id,
                seq=seq,
                prompt_hash=item["prompt_hash"],
                response_hash=item["response_hash"],
                token_count=item["token_count"],
                policy_tag=item["policy_tag"],
                pii_signals=item.get("pii_signals"),
                prev_hash=prev_hash,
                chain_hash=chain_hash,
                gemini_verdict=verdict_dict,
            )
            objects_to_save.append(row)
            prev_seq = seq
            prev_hash = chain_hash

        if objects_to_save:
            db.bulk_save_objects(objects_to_save)
            db.commit()
            log.info(
                "Saved batch of %d logs for org %s (seq %d–%d)",
                len(objects_to_save), org_id, objects_to_save[0].seq, objects_to_save[-1].seq,
            )

    except OperationalError as exc:
        db.rollback()
        log.warning("DB lock contention — retrying batch (retries_left=%d): %s", retries_left, exc)
        if retries_left > 0:
            time.sleep(1)
            _POOL.submit(_process_batch, batch_payloads, retries_left - 1)
    except Exception as exc:
        db.rollback()
        log.error("Unhandled error in log batch — retrying (retries_left=%d): %s", retries_left, exc)
        if retries_left > 0:
            time.sleep(2)
            _POOL.submit(_process_batch, batch_payloads, retries_left - 1)
    finally:
        db.close()


def submit_batch(batch_payloads: list[dict[str, Any]]) -> None:
    """Fire-and-forget: enqueue batch for background Gemini grading + DB insert.

    Returns immediately (HTTP 202 pattern). The batch is processed by a daemon
    thread — the caller never waits for Gemini or the DB write.
    """
    if not batch_payloads:
        return
    _POOL.submit(_process_batch, batch_payloads)
