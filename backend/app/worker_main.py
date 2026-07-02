"""Entry point for the durable grading poller.

Run it as its own process alongside the API:

    python -m app.worker_main      # from backend/, with the venv active

or as the `worker` service in docker-compose.yml. It drains the audit_logs
grading outbox forever (see app/worker.py).
"""

from __future__ import annotations

import logging

from .worker import run_forever

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    run_forever()
