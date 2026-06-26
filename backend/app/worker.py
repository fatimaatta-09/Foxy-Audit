"""Background Gemini grading worker — decouples AI evaluation from the HTTP path.

Uses Celery to process batches of logs asynchronously.
"""

from __future__ import annotations

import logging
from typing import Any

from celery import Celery
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.exc import OperationalError

from . import gemini
from .chain import GENESIS_HASH, compute_chain_hash
from .db import SessionLocal
from .models import AuditLog

log = logging.getLogger("foxy.worker")

# Define the Celery application
celery_app = Celery('foxy_worker', broker='redis://localhost:6379/0')

@celery_app.task(bind=True, max_retries=3)
def process_log_batch_task(self, batch_payloads: list[dict[str, Any]]):
    """
    Process a batch of logs for Gemini evaluation and DB insertion.
    """
    if not batch_payloads:
        return

    # We assume all items in a batch belong to the same org
    org_id = batch_payloads[0]["org_id"]
    
    db: Session = SessionLocal()
    try:
        # Lock the org's tail row to serialize concurrent inserts
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
        analyze_prompt_security = gemini.evaluate

        for item in batch_payloads:
            # 1. Call Gemini for security verdict
            try:
                verdict = analyze_prompt_security(item)
                verdict_dict = verdict.model_dump()
            except Exception as e:
                log.error("Gemini evaluation failed: %s", e)
                # If Gemini fails, we could retry the whole task, or save as None.
                # The prompt implies retrying on Gemini API rate limits.
                raise self.retry(exc=e, countdown=5)

            # 2. Compute chain hash
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

            # 3. Create AuditLog model instance
            row = AuditLog(
                org_id=org_id,
                seq=seq,
                prompt_hash=item["prompt_hash"],
                response_hash=item["response_hash"],
                token_count=item["token_count"],
                policy_tag=item["policy_tag"],
                prev_hash=prev_hash,
                chain_hash=chain_hash,
                gemini_verdict=verdict_dict,
            )
            objects_to_save.append(row)

            # Update for next iteration
            prev_seq = seq
            prev_hash = chain_hash

        # 4. Perform a single, efficient database write
        if objects_to_save:
            db.bulk_save_objects(objects_to_save)
            db.commit()
            log.info("Successfully processed and saved batch of %d logs for org %s", len(objects_to_save), org_id)

    except OperationalError as exc:
        db.rollback()
        log.warning("Database lock error, retrying task: %s", exc)
        raise self.retry(exc=exc, countdown=5)
    except Exception as exc:
        db.rollback()
        log.error("Error processing log batch: %s", exc)
        raise self.retry(exc=exc, countdown=5)
    finally:
        db.close()
