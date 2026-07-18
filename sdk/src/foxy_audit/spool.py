"""Small durable SQLite spool for metadata-only audit events.

The spool deliberately stores commitments and operational metadata, never the
prompt or response. SQLite WAL makes the hand-off durable before the caller's
process continues; delivery can therefore resume after a crash or outage.
"""

from __future__ import annotations

import json
import hashlib
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any


def default_path() -> str:
    configured = os.getenv("FOXY_SPOOL_PATH")
    if configured:
        return configured
    return str(Path.home() / ".foxy-audit" / "spool.sqlite3")


class EventSpool:
    def __init__(self, path: str | None = None) -> None:
        self.path = str(path or default_path())
        parent = Path(self.path).parent
        parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        return conn

    @contextmanager
    def _connection(self):
        """Commit and close every SQLite handle, including on Windows."""
        conn = self._connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS spool_events (
                    event_id TEXT PRIMARY KEY,
                    endpoint TEXT NOT NULL,
                    api_key TEXT NOT NULL,
                    client_id TEXT NOT NULL,
                    client_seq INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at REAL NOT NULL DEFAULT 0,
                    last_error TEXT
                );
                CREATE INDEX IF NOT EXISTS ix_spool_due
                    ON spool_events(next_attempt_at);
                CREATE TABLE IF NOT EXISTS spool_client_state (
                    client_id TEXT PRIMARY KEY,
                    next_seq INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS spool_identity (
                    identity_key TEXT PRIMARY KEY,
                    client_id TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS spool_receipts (
                    event_id TEXT PRIMARY KEY,
                    receipt TEXT NOT NULL,
                    received_at REAL NOT NULL
                );
                """
            )

    def get_or_create_client_id(self, endpoint: str, api_key: str) -> str:
        """Return a stable local identity without storing the key in identity state."""
        identity_key = hashlib.sha256(
            f"{endpoint}\0{api_key}".encode("utf-8")
        ).hexdigest()
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT client_id FROM spool_identity WHERE identity_key = ?",
                (identity_key,),
            ).fetchone()
            if row:
                return str(row["client_id"])
            client_id = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO spool_identity(identity_key, client_id) VALUES (?, ?)",
                (identity_key, client_id),
            )
            return client_id

    def enqueue(self, endpoint: str, api_key: str, payload: dict[str, Any]) -> dict[str, Any]:
        event_id = str(payload["event_id"])
        client_id = str(payload["client_id"])
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT payload FROM spool_events WHERE event_id = ?", (event_id,)
            ).fetchone()
            if existing:
                return json.loads(existing["payload"])
            state = conn.execute(
                "SELECT next_seq FROM spool_client_state WHERE client_id = ?",
                (client_id,),
            ).fetchone()
            client_seq = int(state["next_seq"]) if state else 1
            conn.execute(
                "INSERT INTO spool_client_state(client_id, next_seq) VALUES (?, ?) "
                "ON CONFLICT(client_id) DO UPDATE SET next_seq = excluded.next_seq",
                (client_id, client_seq + 1),
            )
            enriched = {**payload, "client_seq": client_seq}
            conn.execute(
                "INSERT INTO spool_events(event_id, endpoint, api_key, client_id, "
                "client_seq, payload) VALUES (?, ?, ?, ?, ?, ?)",
                (event_id, endpoint, api_key, client_id, client_seq,
                 json.dumps(enriched, separators=(",", ":"), sort_keys=True)),
            )
            return enriched

    def due(self, limit: int = 50) -> list[sqlite3.Row]:
        with self._connection() as conn:
            return conn.execute(
                "SELECT * FROM spool_events WHERE next_attempt_at <= ? "
                "ORDER BY rowid LIMIT ?",
                (time.time(), limit),
            ).fetchall()

    def ack(self, rows: list[sqlite3.Row], response: dict[str, Any]) -> None:
        if not rows:
            return
        with self._connection() as conn:
            for row in rows:
                conn.execute(
                    "INSERT OR REPLACE INTO spool_receipts(event_id, receipt, received_at) "
                    "VALUES (?, ?, ?)",
                    (row["event_id"], json.dumps(response, sort_keys=True), time.time()),
                )
                conn.execute("DELETE FROM spool_events WHERE event_id = ?", (row["event_id"],))

    def retry(self, rows: list[sqlite3.Row], error: str) -> None:
        if not rows:
            return
        with self._connection() as conn:
            for row in rows:
                attempts = int(row["attempts"]) + 1
                delay = min(300.0, 2.0 ** min(attempts, 8))
                conn.execute(
                    "UPDATE spool_events SET attempts = ?, next_attempt_at = ?, last_error = ? "
                    "WHERE event_id = ?",
                    (attempts, time.time() + delay, error[:500], row["event_id"]),
                )

    def has(self, event_id: str) -> bool:
        with self._connection() as conn:
            return conn.execute(
                "SELECT 1 FROM spool_events WHERE event_id = ?", (event_id,)
            ).fetchone() is not None

    def receipt(self, event_id: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT receipt FROM spool_receipts WHERE event_id = ?", (event_id,)
            ).fetchone()
            return json.loads(row["receipt"]) if row else None


def open_spool(path: str | None = None) -> EventSpool:
    return EventSpool(path)
