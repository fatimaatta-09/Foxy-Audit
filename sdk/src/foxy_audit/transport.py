"""Thin HTTP wrapper for streaming metadata to the Foxy Audit backend.

Uses `requests` (one stable, ubiquitous dependency) on a background thread
rather than an async client, so the SDK stays non-blocking in any host app —
sync or async — without requiring a running event loop. Raises on failure;
the dispatcher is responsible for catching so nothing reaches the host app.
"""

from __future__ import annotations

import requests


def post_log(endpoint: str, api_key: str, payload: dict, timeout: float = 5.0) -> dict:
    """POST ingest metadata to ``{endpoint}/v1/logs`` and return the parsed body.

    The response is expected to contain ``{"log_id", "seq", "chain_hash", "verdict"}``.
    """
    url = f"{endpoint.rstrip('/')}/v1/logs"
    resp = requests.post(
        url,
        json=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()
