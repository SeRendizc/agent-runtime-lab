from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_runtime_lab.domain.errors import (
    DuplicateEventConflictError,
    SequenceError,
)
from agent_runtime_lab.domain.events import EventType, ExecutionEvent
from agent_runtime_lab.domain.replay import replay
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
