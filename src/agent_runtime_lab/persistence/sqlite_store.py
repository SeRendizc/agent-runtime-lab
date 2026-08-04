"""SQLite-backed append-only storage for execution events."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from agent_runtime_lab.domain.errors import (
    DuplicateEventConflictError,
    SequenceError,
)
from agent_runtime_lab.domain.events import EventType, ExecutionEvent


class SQLiteEventStore:
    """Persist immutable execution events in per-run sequence order."""

    def __init__(self, database_path: str | Path) -> None:
        self._connection = sqlite3.connect(str(database_path))
        self._connection.row_factory = sqlite3.Row
        self._initialize_schema()

    def _initialize_schema(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS execution_events (
                event_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                sequence INTEGER NOT NULL CHECK (sequence >= 0),
                event_type TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                UNIQUE (run_id, sequence)
            );

            CREATE INDEX IF NOT EXISTS idx_execution_events_run_sequence
            ON execution_events (run_id, sequence);
            """
        )
        self._connection.commit()

    def append(self, event: ExecutionEvent) -> None:
        """Atomically append one event or accept an exact redelivery."""

        self._connection.execute("BEGIN IMMEDIATE")

        try:
            existing = self._connection.execute(
                """
                SELECT fingerprint
                FROM execution_events
                WHERE event_id = ?
                """,
                (event.event_id,),
            ).fetchone()

            if existing is not None:
                if existing["fingerprint"] != event.fingerprint():
                    raise DuplicateEventConflictError(
                        f"event_id {event.event_id!r} was reused with different content"
                    )

                self._connection.commit()
                return

            row = self._connection.execute(
                """
                SELECT COALESCE(MAX(sequence), -1) AS last_sequence
                FROM execution_events
                WHERE run_id = ?
                """,
                (event.run_id,),
            ).fetchone()

            expected_sequence = int(row["last_sequence"]) + 1

            if event.sequence != expected_sequence:
                raise SequenceError(f"expected sequence {expected_sequence}, got {event.sequence}")

            self._connection.execute(
                """
                INSERT INTO execution_events (
                    event_id,
                    run_id,
                    sequence,
                    event_type,
                    occurred_at,
                    payload_json,
                    fingerprint
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.run_id,
                    event.sequence,
                    event.event_type.value,
                    event.occurred_at.isoformat(),
                    event.payload_json,
                    event.fingerprint(),
                ),
            )

            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

    def load(self, run_id: str) -> list[ExecutionEvent]:
        """Load one run's events in deterministic sequence order."""

        rows = self._connection.execute(
            """
            SELECT
                event_id,
                run_id,
                sequence,
                event_type,
                occurred_at,
                payload_json
            FROM execution_events
            WHERE run_id = ?
            ORDER BY sequence ASC
            """,
            (run_id,),
        ).fetchall()

        return [
            ExecutionEvent(
                event_id=row["event_id"],
                run_id=row["run_id"],
                sequence=row["sequence"],
                event_type=EventType(row["event_type"]),
                occurred_at=datetime.fromisoformat(row["occurred_at"]),
                payload_json=row["payload_json"],
            )
            for row in rows
        ]

    def count(self, run_id: str) -> int:
        row = self._connection.execute(
            """
            SELECT COUNT(*) AS event_count
            FROM execution_events
            WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()

        return int(row["event_count"])

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> SQLiteEventStore:
        return self

    def __exit__(
        self,
        exc_type: object,
        exc_value: object,
        traceback: object,
    ) -> None:
        self.close()
