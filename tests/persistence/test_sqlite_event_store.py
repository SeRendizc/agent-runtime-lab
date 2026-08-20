import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier

import pytest

from agent_runtime_lab.domain.errors import (
    DuplicateEventConflictError,
    SequenceError,
)
from agent_runtime_lab.domain.events import EventType, ExecutionEvent
from agent_runtime_lab.domain.replay import replay, replay_tail
from agent_runtime_lab.domain.state import RunStatus
from agent_runtime_lab.persistence.sqlite_store import SQLiteEventStore

NOW = datetime(2026, 8, 4, 0, 0, tzinfo=UTC)


def make_event(
    sequence: int,
    event_type: EventType,
    *,
    event_id: str | None = None,
    run_id: str = "run-1",
    payload: dict[str, object] | None = None,
) -> ExecutionEvent:
    return ExecutionEvent.build(
        event_id=event_id or f"{run_id}-evt-{sequence}",
        run_id=run_id,
        sequence=sequence,
        event_type=event_type,
        occurred_at=NOW,
        payload=payload,
    )


def test_event_survives_store_reopening(tmp_path: Path) -> None:
    database_path = tmp_path / "runtime.db"
    expected = make_event(0, EventType.RUN_CREATED)

    first_store = SQLiteEventStore(database_path)
    first_store.append(expected)
    first_store.close()

    reopened_store = SQLiteEventStore(database_path)

    assert reopened_store.load("run-1") == [expected]

    reopened_store.close()


def test_loaded_events_can_rebuild_state(tmp_path: Path) -> None:
    store = SQLiteEventStore(tmp_path / "runtime.db")
    store.append(make_event(0, EventType.RUN_CREATED))
    store.append(make_event(1, EventType.RUN_STARTED))

    loaded_events = store.load("run-1")
    recovered_state = replay("run-1", loaded_events)

    assert recovered_state.status is RunStatus.READY
    assert recovered_state.next_sequence == 2

    store.close()


def test_exact_duplicate_append_is_idempotent(tmp_path: Path) -> None:
    store = SQLiteEventStore(tmp_path / "runtime.db")
    event = make_event(0, EventType.RUN_CREATED)

    store.append(event)
    store.append(event)

    assert store.count("run-1") == 1
    assert store.load("run-1") == [event]

    store.close()


def test_conflicting_duplicate_event_id_is_rejected(
    tmp_path: Path,
) -> None:
    store = SQLiteEventStore(tmp_path / "runtime.db")
    original = make_event(
        0,
        EventType.RUN_CREATED,
        event_id="evt-shared",
    )
    conflicting = make_event(
        0,
        EventType.RUN_STARTED,
        event_id="evt-shared",
    )

    store.append(original)

    with pytest.raises(
        DuplicateEventConflictError,
        match="different content",
    ):
        store.append(conflicting)

    assert store.load("run-1") == [original]

    store.close()


def test_sequence_gap_is_rejected_without_partial_write(
    tmp_path: Path,
) -> None:
    store = SQLiteEventStore(tmp_path / "runtime.db")
    out_of_order = make_event(1, EventType.RUN_STARTED)

    with pytest.raises(SequenceError, match="expected sequence 0"):
        store.append(out_of_order)

    assert store.load("run-1") == []

    store.close()


def test_each_run_has_an_independent_sequence(
    tmp_path: Path,
) -> None:
    store = SQLiteEventStore(tmp_path / "runtime.db")
    run_one_event = make_event(
        0,
        EventType.RUN_CREATED,
        run_id="run-1",
    )
    run_two_event = make_event(
        0,
        EventType.RUN_CREATED,
        run_id="run-2",
    )

    store.append(run_one_event)
    store.append(run_two_event)

    assert store.load("run-1") == [run_one_event]
    assert store.load("run-2") == [run_two_event]

    store.close()


def _append_from_new_connection(
    database_path: Path,
    event: ExecutionEvent,
    barrier: Barrier,
) -> None:
    with SQLiteEventStore(database_path) as store:
        barrier.wait(timeout=5)
        store.append(event)


def test_concurrent_exact_redelivery_commits_one_event(tmp_path: Path) -> None:
    database_path = tmp_path / "runtime.db"
    with SQLiteEventStore(database_path):
        pass
    event = make_event(
        0,
        EventType.GATE_REVISED,
        event_id="run-1:0:gate.revised",
        payload={"proposal_digest": "a" * 64, "revision": 2},
    )
    barrier = Barrier(2)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(_append_from_new_connection, database_path, event, barrier)
            for _ in range(2)
        ]
        for future in futures:
            future.result()

    with SQLiteEventStore(database_path) as store:
        assert store.load("run-1") == [event]


def test_concurrent_conflicting_redelivery_fails_closed(tmp_path: Path) -> None:
    database_path = tmp_path / "runtime.db"
    with SQLiteEventStore(database_path):
        pass
    first = make_event(
        0,
        EventType.GATE_REVISED,
        event_id="run-1:0:gate.revised",
        payload={"proposal_digest": "a" * 64, "revision": 2},
    )
    conflicting = make_event(
        0,
        EventType.GATE_REVISED,
        event_id="run-1:0:gate.revised",
        payload={"proposal_digest": "b" * 64, "revision": 2},
    )
    barrier = Barrier(2)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(_append_from_new_connection, database_path, event, barrier)
            for event in (first, conflicting)
        ]
        outcomes = []
        for future in futures:
            try:
                future.result()
            except DuplicateEventConflictError:
                outcomes.append("conflict")
            else:
                outcomes.append("committed")

    assert sorted(outcomes) == ["committed", "conflict"]
    with SQLiteEventStore(database_path) as store:
        assert store.count("run-1") == 1


def test_snapshot_plus_tail_matches_full_event_replay(tmp_path: Path) -> None:
    store = SQLiteEventStore(tmp_path / "runtime.db")
    prefix = [
        make_event(0, EventType.RUN_CREATED),
        make_event(1, EventType.RUN_STARTED),
    ]
    for event in prefix:
        store.append(event)
    snapshot_state = replay("run-1", prefix)
    store.save_snapshot(snapshot_state)

    tail = [make_event(2, EventType.RUN_PAUSED)]
    store.append(tail[0])

    loaded_snapshot = store.load_snapshot("run-1")
    assert loaded_snapshot == snapshot_state
    assert store.load_tail("run-1", snapshot_state.next_sequence) == tail
    assert replay_tail(loaded_snapshot, tail) == replay("run-1", store.load("run-1"))

    store.close()


def test_corrupt_snapshot_state_is_discarded(tmp_path: Path) -> None:
    database_path = tmp_path / "runtime.db"
    store = SQLiteEventStore(database_path)
    event = make_event(0, EventType.RUN_CREATED)
    store.append(event)
    store.save_snapshot(replay("run-1", [event]))
    store.close()

    connection = sqlite3.connect(database_path)
    connection.execute(
        "UPDATE run_snapshots SET state_json = ? WHERE run_id = ?",
        ('{"corrupt":true}', "run-1"),
    )
    connection.commit()
    connection.close()

    with SQLiteEventStore(database_path) as reopened:
        assert reopened.load_snapshot("run-1") is None
        assert replay("run-1", reopened.load("run-1")).status is RunStatus.CREATED


def test_snapshot_rejects_state_not_derived_from_event_prefix(tmp_path: Path) -> None:
    store = SQLiteEventStore(tmp_path / "runtime.db")
    event = make_event(0, EventType.RUN_CREATED)
    store.append(event)
    forged = replace(replay("run-1", [event]), status=RunStatus.FAILED)

    with pytest.raises(ValueError, match="not derived"):
        store.save_snapshot(forged)

    assert store.load_snapshot("run-1") is None
    store.close()


def test_snapshot_is_discarded_when_event_prefix_binding_changes(tmp_path: Path) -> None:
    database_path = tmp_path / "runtime.db"
    store = SQLiteEventStore(database_path)
    events = [
        make_event(0, EventType.RUN_CREATED),
        make_event(1, EventType.RUN_STARTED),
    ]
    for event in events:
        store.append(event)
    store.save_snapshot(replay("run-1", events))

    store._connection.execute(  # noqa: SLF001 - deliberate corruption fixture
        "UPDATE run_snapshots SET prefix_chain_digest = ? WHERE run_id = ?",
        ("f" * 64, "run-1"),
    )
    store._connection.commit()  # noqa: SLF001 - deliberate corruption fixture

    assert store.load_snapshot("run-1") is None
    assert replay("run-1", store.load("run-1")).status is RunStatus.READY

    store.close()


def test_event_rows_reject_update_and_delete(tmp_path: Path) -> None:
    store = SQLiteEventStore(tmp_path / "runtime.db")
    store.append(make_event(0, EventType.RUN_CREATED))

    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        store._connection.execute(  # noqa: SLF001 - storage invariant probe
            "UPDATE execution_events SET payload_json = '{}' WHERE run_id = 'run-1'"
        )
    store._connection.rollback()  # noqa: SLF001 - storage invariant probe

    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        store._connection.execute(  # noqa: SLF001 - storage invariant probe
            "DELETE FROM execution_events WHERE run_id = 'run-1'"
        )
    store._connection.rollback()  # noqa: SLF001 - storage invariant probe

    assert store.count("run-1") == 1
    store.close()


def test_opening_legacy_event_table_backfills_chain_digests(tmp_path: Path) -> None:
    database_path = tmp_path / "legacy.db"
    event = make_event(0, EventType.RUN_CREATED)
    connection = sqlite3.connect(database_path)
    connection.executescript(
        """
        CREATE TABLE execution_events (
            event_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            sequence INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            UNIQUE (run_id, sequence)
        );
        """
    )
    connection.execute(
        """
        INSERT INTO execution_events (
            event_id, run_id, sequence, event_type, occurred_at, payload_json, fingerprint
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
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
    connection.commit()
    connection.close()

    with SQLiteEventStore(database_path) as migrated:
        state = replay("run-1", migrated.load("run-1"))
        migrated.save_snapshot(state)
        assert migrated.load_snapshot("run-1") == state
