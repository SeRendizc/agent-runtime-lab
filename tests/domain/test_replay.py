from datetime import UTC, datetime

import pytest

from agent_runtime_lab.domain.errors import SequenceError
from agent_runtime_lab.domain.events import EventType, ExecutionEvent
from agent_runtime_lab.domain.replay import replay
from agent_runtime_lab.domain.state import RunStatus

NOW = datetime(2026, 7, 26, 0, 0, tzinfo=UTC)


def event(
    sequence: int,
    event_type: EventType,
    *,
    event_id: str | None = None,
    payload: dict[str, object] | None = None,
) -> ExecutionEvent:
    return ExecutionEvent.build(
        event_id=event_id or f"evt-{sequence}",
        run_id="run-1",
        sequence=sequence,
        event_type=event_type,
        occurred_at=NOW,
        payload=payload,
    )


def completed_events() -> list[ExecutionEvent]:
    return [
        event(0, EventType.RUN_CREATED),
        event(1, EventType.RUN_STARTED),
        event(2, EventType.TOOL_REQUESTED, payload={"tool_call_id": "tool-1"}),
        event(3, EventType.TOOL_AUTHORIZED, payload={"tool_call_id": "tool-1"}),
        event(4, EventType.TOOL_STARTED, payload={"tool_call_id": "tool-1"}),
        event(5, EventType.TOOL_SUCCEEDED, payload={"tool_call_id": "tool-1"}),
        event(6, EventType.VERIFICATION_SUCCEEDED),
    ]


def test_replay_derives_completed_state() -> None:
    state = replay("run-1", completed_events())

    assert state.status is RunStatus.COMPLETED
    assert state.next_sequence == 7


def test_replay_is_deterministic() -> None:
    events = completed_events()

    assert replay("run-1", events) == replay("run-1", events)


def test_replay_accepts_exact_duplicate_delivery() -> None:
    events = completed_events()
    events.insert(3, events[2])

    state = replay("run-1", events)

    assert state.status is RunStatus.COMPLETED
    assert state.next_sequence == 7


def test_replay_rejects_out_of_order_events() -> None:
    events = completed_events()
    events[2], events[3] = events[3], events[2]

    with pytest.raises(SequenceError):
        replay("run-1", events)
