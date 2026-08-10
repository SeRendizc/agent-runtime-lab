from datetime import UTC, datetime

import pytest

from agent_runtime_lab.domain.errors import (
    DuplicateEventConflictError,
    InvalidTransitionError,
    RunMismatchError,
    SequenceError,
    TerminalStateError,
)
from agent_runtime_lab.domain.events import EventType, ExecutionEvent
from agent_runtime_lab.domain.reducer import reduce
from agent_runtime_lab.domain.state import RunState, RunStatus

NOW = datetime(2026, 7, 26, 0, 0, tzinfo=UTC)


def event(
    sequence: int,
    event_type: EventType,
    *,
    event_id: str | None = None,
    run_id: str = "run-1",
    payload: dict[str, object] | None = None,
) -> ExecutionEvent:
    return ExecutionEvent.build(
        event_id=event_id or f"evt-{sequence}",
        run_id=run_id,
        sequence=sequence,
        event_type=event_type,
        occurred_at=NOW,
        payload=payload,
    )


def apply(state: RunState, *events: ExecutionEvent) -> RunState:
    for item in events:
        state = reduce(state, item)
    return state


def ready_state() -> RunState:
    return apply(
        RunState.initial("run-1"),
        event(0, EventType.RUN_CREATED),
        event(1, EventType.RUN_STARTED),
    )


def test_full_authorized_tool_lifecycle_completes() -> None:
    state = apply(
        ready_state(),
        event(2, EventType.TOOL_REQUESTED, payload={"tool_call_id": "tool-1"}),
        event(3, EventType.TOOL_AUTHORIZED, payload={"tool_call_id": "tool-1"}),
        event(4, EventType.TOOL_STARTED, payload={"tool_call_id": "tool-1"}),
        event(5, EventType.TOOL_SUCCEEDED, payload={"tool_call_id": "tool-1"}),
        event(6, EventType.VERIFICATION_SUCCEEDED),
    )

    assert state.status is RunStatus.COMPLETED
    assert state.next_sequence == 7
    assert state.active_tool_call_id is None
    assert len(state.applied_event_fingerprints) == 7


def test_exact_duplicate_delivery_is_idempotent() -> None:
    state = ready_state()
    requested = event(
        2,
        EventType.TOOL_REQUESTED,
        event_id="evt-request",
        payload={"tool_call_id": "tool-1"},
    )

    once = reduce(state, requested)
    twice = reduce(once, requested)

    assert twice == once
    assert twice.next_sequence == 3


def test_duplicate_event_id_with_different_content_is_rejected() -> None:
    state = reduce(ready_state(), event(2, EventType.RUN_PAUSED, event_id="evt-same"))

    with pytest.raises(DuplicateEventConflictError):
        reduce(
            state,
            event(
                2,
                EventType.TOOL_REQUESTED,
                event_id="evt-same",
                payload={"tool_call_id": "tool-1"},
            ),
        )


def test_sequence_gap_is_rejected() -> None:
    with pytest.raises(SequenceError, match="expected sequence 2"):
        reduce(ready_state(), event(3, EventType.RUN_PAUSED))


def test_cross_run_event_is_rejected() -> None:
    with pytest.raises(RunMismatchError):
        reduce(ready_state(), event(2, EventType.RUN_PAUSED, run_id="run-2"))


def test_illegal_transition_is_rejected() -> None:
    with pytest.raises(InvalidTransitionError):
        reduce(ready_state(), event(2, EventType.TOOL_STARTED, payload={"tool_call_id": "x"}))


def test_tool_call_identity_must_remain_stable() -> None:
    state = reduce(
        ready_state(),
        event(2, EventType.TOOL_REQUESTED, payload={"tool_call_id": "tool-1"}),
    )

    with pytest.raises(InvalidTransitionError, match="active tool call"):
        reduce(
            state,
            event(3, EventType.TOOL_AUTHORIZED, payload={"tool_call_id": "tool-2"}),
        )


def test_terminal_state_rejects_new_events_but_accepts_exact_redelivery() -> None:
    cancelled = event(2, EventType.RUN_CANCELLED)
    state = reduce(ready_state(), cancelled)

    assert reduce(state, cancelled) == state
    with pytest.raises(TerminalStateError):
        reduce(state, event(3, EventType.RUN_STARTED))


def test_pause_and_resume_return_to_ready() -> None:
    state = apply(
        ready_state(),
        event(2, EventType.RUN_PAUSED),
        event(3, EventType.RUN_RESUMED),
    )

    assert state.status is RunStatus.READY


def test_denied_tool_terminates_with_reason() -> None:
    state = apply(
        ready_state(),
        event(2, EventType.TOOL_REQUESTED, payload={"tool_call_id": "tool-1"}),
        event(
            3,
            EventType.TOOL_DENIED,
            payload={"tool_call_id": "tool-1", "reason": "outside workspace"},
        ),
    )

    assert state.status is RunStatus.FAILED
    assert state.failure_reason == "outside workspace"


def test_escalated_tool_waits_for_matching_gate_approval() -> None:
    state = apply(
        ready_state(),
        event(2, EventType.TOOL_REQUESTED, payload={"tool_call_id": "tool-1"}),
        event(
            3,
            EventType.TOOL_ESCALATED,
            payload={
                "tool_call_id": "tool-1",
                "proposal_digest": "a" * 64,
                "revision": 1,
                "ownership_mode": "pair",
            },
        ),
    )

    assert state.status is RunStatus.AWAITING_GATE
    assert state.active_gate_proposal_digest == "a" * 64
    assert state.active_gate_revision == 1
    assert state.active_gate_mode == "pair"

    approved = reduce(
        state,
        event(
            4,
            EventType.GATE_APPROVED,
            payload={
                "tool_call_id": "tool-1",
                "proposal_digest": "a" * 64,
                "revision": 1,
            },
        ),
    )

    assert approved.status is RunStatus.TOOL_READY
    assert approved.active_tool_call_id == "tool-1"
    assert approved.active_gate_proposal_digest is None
    assert approved.active_gate_revision is None
    assert approved.active_gate_mode is None


def test_gate_approval_must_match_active_proposal_revision() -> None:
    state = apply(
        ready_state(),
        event(2, EventType.TOOL_REQUESTED, payload={"tool_call_id": "tool-1"}),
        event(
            3,
            EventType.TOOL_ESCALATED,
            payload={
                "tool_call_id": "tool-1",
                "proposal_digest": "a" * 64,
                "revision": 2,
                "ownership_mode": "user_gate",
            },
        ),
    )

    with pytest.raises(InvalidTransitionError, match="revision"):
        reduce(
            state,
            event(
                4,
                EventType.GATE_APPROVED,
                payload={
                    "tool_call_id": "tool-1",
                    "proposal_digest": "a" * 64,
                    "revision": 1,
                },
            ),
        )
