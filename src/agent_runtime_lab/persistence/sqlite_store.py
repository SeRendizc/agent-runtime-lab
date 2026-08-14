"""SQLite-backed append-only storage for execution events."""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime
from pathlib import Path

from agent_runtime_lab.domain.errors import (
    DuplicateEventConflictError,
    SequenceError,
)
from agent_runtime_lab.domain.events import EventType, ExecutionEvent
from agent_runtime_lab.domain.replay import replay
from agent_runtime_lab.domain.state import RunState
from agent_runtime_lab.persistence.snapshots import (
    SNAPSHOT_SCHEMA_VERSION,
    decode_state,
    digest_state,
    encode_state,
)

CHAIN_SEED = "0" * 64


def _next_chain_digest(previous_digest: str, event_fingerprint: str) -> str:
    material = f"{previous_digest}:{event_fingerprint}".encode()
    return hashlib.sha256(material).hexdigest()


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
                chain_digest TEXT NOT NULL,
                UNIQUE (run_id, sequence)
            );

            CREATE INDEX IF NOT EXISTS idx_execution_events_run_sequence
            ON execution_events (run_id, sequence);

            CREATE TABLE IF NOT EXISTS run_snapshots (
                run_id TEXT PRIMARY KEY,
                schema_version INTEGER NOT NULL,
                next_sequence INTEGER NOT NULL CHECK (next_sequence >= 0),
                prefix_chain_digest TEXT NOT NULL,
                state_json TEXT NOT NULL,
                state_digest TEXT NOT NULL
            );
            """
        )
        columns = {
            row["name"] for row in self._connection.execute("PRAGMA table_info(execution_events)")
        }
        added_chain_digest = False
        if "chain_digest" not in columns:
            self._connection.execute("ALTER TABLE execution_events ADD COLUMN chain_digest TEXT")
            added_chain_digest = True
        missing_digest = self._connection.execute(
            "SELECT 1 FROM execution_events WHERE chain_digest IS NULL LIMIT 1"
        ).fetchone()
        if added_chain_digest or missing_digest is not None:
            self._backfill_chain_digests()
        self._connection.executescript(
            """
            CREATE TRIGGER IF NOT EXISTS execution_events_no_update
            BEFORE UPDATE ON execution_events
            BEGIN
                SELECT RAISE(ABORT, 'execution events are immutable');
            END;

            CREATE TRIGGER IF NOT EXISTS execution_events_no_delete
            BEFORE DELETE ON execution_events
            BEGIN
                SELECT RAISE(ABORT, 'execution events are immutable');
            END;
            """
        )
        self._connection.commit()

    def _backfill_chain_digests(self) -> None:
        rows = self._connection.execute(
            """
            SELECT event_id, run_id, fingerprint
            FROM execution_events
            ORDER BY run_id ASC, sequence ASC
            """
        ).fetchall()
        previous_run_id: str | None = None
        previous_digest = CHAIN_SEED
        for row in rows:
            if row["run_id"] != previous_run_id:
                previous_run_id = row["run_id"]
                previous_digest = CHAIN_SEED
            previous_digest = _next_chain_digest(previous_digest, row["fingerprint"])
            self._connection.execute(
                "UPDATE execution_events SET chain_digest = ? WHERE event_id = ?",
                (previous_digest, row["event_id"]),
            )

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
                SELECT sequence AS last_sequence, chain_digest
                FROM execution_events
                WHERE run_id = ?
                ORDER BY sequence DESC
                LIMIT 1
                """,
                (event.run_id,),
            ).fetchone()

            expected_sequence = 0 if row is None else int(row["last_sequence"]) + 1

            if event.sequence != expected_sequence:
                raise SequenceError(f"expected sequence {expected_sequence}, got {event.sequence}")

            previous_digest = CHAIN_SEED if row is None else row["chain_digest"]
            if not isinstance(previous_digest, str):
                raise RuntimeError("event chain digest is missing")
            event_fingerprint = event.fingerprint()
            chain_digest = _next_chain_digest(previous_digest, event_fingerprint)

            self._connection.execute(
                """
                INSERT INTO execution_events (
                    event_id,
                    run_id,
                    sequence,
                    event_type,
                    occurred_at,
                    payload_json,
                    fingerprint,
                    chain_digest
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.run_id,
                    event.sequence,
                    event.event_type.value,
                    event.occurred_at.isoformat(),
                    event.payload_json,
                    event_fingerprint,
                    chain_digest,
                ),
            )

            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

    def load(self, run_id: str) -> list[ExecutionEvent]:
        """Load one run's events in deterministic sequence order."""

        return self.load_tail(run_id, 0)

    def load_tail(self, run_id: str, start_sequence: int) -> list[ExecutionEvent]:
        """Load events at or after a validated snapshot boundary."""

        if start_sequence < 0:
            raise ValueError("start_sequence must be non-negative")
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
            WHERE run_id = ? AND sequence >= ?
            ORDER BY sequence ASC
            """,
            (run_id, start_sequence),
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

    def save_snapshot(self, state: RunState) -> None:
        """Atomically replace a disposable snapshot for the current event prefix."""

        prefix_digest = self._prefix_chain_digest(state.run_id, state.next_sequence)
        if prefix_digest is None:
            raise ValueError("snapshot state does not match a stored event prefix")
        if len(state.applied_event_fingerprints) != state.next_sequence:
            raise ValueError("snapshot state does not cover exactly one event prefix")
        prefix_state = replay(
            state.run_id,
            self.load_tail(state.run_id, 0)[: state.next_sequence],
        )
        if prefix_state != state:
            raise ValueError("snapshot state was not derived from the stored event prefix")

        state_json = encode_state(state)
        self._connection.execute(
            """
            INSERT INTO run_snapshots (
                run_id,
                schema_version,
                next_sequence,
                prefix_chain_digest,
                state_json,
                state_digest
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                schema_version = excluded.schema_version,
                next_sequence = excluded.next_sequence,
                prefix_chain_digest = excluded.prefix_chain_digest,
                state_json = excluded.state_json,
                state_digest = excluded.state_digest
            """,
            (
                state.run_id,
                SNAPSHOT_SCHEMA_VERSION,
                state.next_sequence,
                prefix_digest,
                state_json,
                digest_state(state_json),
            ),
        )
        self._connection.commit()

    def load_snapshot(self, run_id: str) -> RunState | None:
        """Return a snapshot only when its state and event-prefix bindings validate."""

        row = self._connection.execute(
            """
            SELECT
                schema_version,
                next_sequence,
                prefix_chain_digest,
                state_json,
                state_digest
            FROM run_snapshots
            WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            return None

        try:
            if row["schema_version"] != SNAPSHOT_SCHEMA_VERSION:
                return None
            if digest_state(row["state_json"]) != row["state_digest"]:
                return None
            state = decode_state(row["state_json"])
        except (KeyError, TypeError, ValueError):
            return None

        if state.run_id != run_id or state.next_sequence != row["next_sequence"]:
            return None
        if len(state.applied_event_fingerprints) != state.next_sequence:
            return None
        current_prefix_digest = self._prefix_chain_digest(run_id, state.next_sequence)
        if current_prefix_digest != row["prefix_chain_digest"]:
            return None
        return state

    def _prefix_chain_digest(self, run_id: str, next_sequence: int) -> str | None:
        if next_sequence < 0:
            return None
        if next_sequence == 0:
            return CHAIN_SEED
        row = self._connection.execute(
            """
            SELECT chain_digest
            FROM execution_events
            WHERE run_id = ? AND sequence = ?
            """,
            (run_id, next_sequence - 1),
        ).fetchone()
        if row is None or not isinstance(row["chain_digest"], str):
            return None
        return row["chain_digest"]

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
