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


def budgeted_ready_state(max_steps: int) -> RunState:
    return apply(
        RunState.initial("run-1"),
        event(0, EventType.RUN_CREATED, payload={"max_steps": max_steps}),
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


def test_step_scoped_verification_returns_run_to_ready_and_advances_turn() -> None:
    state = apply(
        ready_state(),
        event(
            2,
            EventType.TOOL_REQUESTED,
            payload={"step_id": "step-1", "tool_call_id": "tool-1"},
        ),
        event(3, EventType.TOOL_AUTHORIZED, payload={"tool_call_id": "tool-1"}),
        event(4, EventType.TOOL_STARTED, payload={"tool_call_id": "tool-1"}),
        event(5, EventType.TOOL_SUCCEEDED, payload={"tool_call_id": "tool-1"}),
        event(
            6,
            EventType.VERIFICATION_SUCCEEDED,
            payload={"scope": "step", "step_id": "step-1"},
        ),
    )

    assert state.status is RunStatus.READY
    assert state.turn_index == 1
    assert state.active_step_id is None


def test_step_scoped_verification_must_match_active_step() -> None:
    state = apply(
        ready_state(),
        event(
            2,
            EventType.TOOL_REQUESTED,
            payload={"step_id": "step-1", "tool_call_id": "tool-1"},
        ),
        event(3, EventType.TOOL_AUTHORIZED, payload={"tool_call_id": "tool-1"}),
        event(4, EventType.TOOL_STARTED, payload={"tool_call_id": "tool-1"}),
        event(5, EventType.TOOL_SUCCEEDED, payload={"tool_call_id": "tool-1"}),
    )

    with pytest.raises(InvalidTransitionError, match="active step"):
        reduce(
            state,
            event(
                6,
                EventType.VERIFICATION_SUCCEEDED,
                payload={"scope": "step", "step_id": "step-2"},
            ),
        )


def test_step_budget_is_fixed_by_run_creation_and_exhaustion_fails_run() -> None:
    state = apply(
        budgeted_ready_state(1),
        event(
            2,
            EventType.TOOL_REQUESTED,
            payload={"step_id": "step-1", "tool_call_id": "tool-1"},
        ),
        event(3, EventType.TOOL_AUTHORIZED, payload={"tool_call_id": "tool-1"}),
        event(4, EventType.TOOL_STARTED, payload={"tool_call_id": "tool-1"}),
        event(5, EventType.TOOL_SUCCEEDED, payload={"tool_call_id": "tool-1"}),
        event(
            6,
            EventType.VERIFICATION_SUCCEEDED,
            payload={"scope": "step", "step_id": "step-1"},
        ),
    )

    exhausted = reduce(
        state,
        event(
            7,
            EventType.RUN_STEP_BUDGET_EXHAUSTED,
            payload={"completed_steps": 1, "max_steps": 1},
        ),
    )

    assert state.max_steps == 1
    assert exhausted.status is RunStatus.FAILED
    assert exhausted.failure_reason == "step budget exhausted: 1/1 steps consumed"


def test_step_budget_cannot_be_exhausted_before_durable_turn_reaches_limit() -> None:
    state = budgeted_ready_state(2)

    with pytest.raises(InvalidTransitionError, match="durable budget"):
        reduce(
            state,
            event(
                2,
                EventType.RUN_STEP_BUDGET_EXHAUSTED,
                payload={"completed_steps": 0, "max_steps": 2},
            ),
        )


def test_model_action_failure_terminates_ready_run() -> None:
    state = reduce(
        budgeted_ready_state(2),
        event(
            2,
            EventType.MODEL_ACTION_FAILED,
            payload={"reason": "adapter returned invalid output"},
        ),
    )

    assert state.status is RunStatus.FAILED
    assert state.failure_reason == "adapter returned invalid output"


def test_durable_model_action_binds_invocation_step_and_completion() -> None:
    requested = reduce(
        budgeted_ready_state(1),
        event(
            2,
            EventType.MODEL_ACTION_REQUESTED,
            payload={
                "invocation_id": "invoke-1",
                "observation_json": "{}",
                "step_id": "step-1",
                "turn_index": 0,
            },
        ),
    )
    proposed = reduce(
        requested,
        event(
            3,
            EventType.MODEL_ACTION_PROPOSED,
            payload={
                "action_type": "final_answer",
                "answer": "done",
                "invocation_id": "invoke-1",
                "step_id": "step-1",
                "turn_index": 0,
            },
        ),
    )
    completed = reduce(
        proposed,
        event(
            4,
            EventType.COMPLETION_ACCEPTED,
            payload={
                "answer_sha256": "digest",
                "model_action_event_id": "evt-3",
                "step_id": "step-1",
                "summary": "accepted",
            },
        ),
    )

    assert requested.status is RunStatus.MODEL_PENDING
    assert proposed.status is RunStatus.ACTION_PENDING
    assert proposed.active_model_action_event_id == "evt-3"
    assert completed.status is RunStatus.COMPLETED
    assert completed.active_model_invocation_id is None
    assert completed.active_model_action_event_id is None


def test_durable_model_action_rejects_mismatched_dispatch_reference() -> None:
    state = apply(
        budgeted_ready_state(1),
        event(
            2,
            EventType.MODEL_ACTION_REQUESTED,
            payload={
                "invocation_id": "invoke-1",
                "observation_json": "{}",
                "step_id": "step-1",
                "turn_index": 0,
            },
        ),
        event(
            3,
            EventType.MODEL_ACTION_PROPOSED,
            payload={
                "action_type": "tool_call",
                "arguments_json": "{}",
                "invocation_id": "invoke-1",
                "step_id": "step-1",
                "tool_call_id": "tool-1",
                "tool_name": "read_file",
                "turn_index": 0,
            },
        ),
    )

    with pytest.raises(InvalidTransitionError, match="active model action"):
        reduce(
            state,
            event(
                4,
                EventType.TOOL_REQUESTED,
                payload={
                    "model_action_event_id": "different-event",
                    "step_id": "step-1",
                    "tool_call_id": "tool-1",
                },
            ),
        )


def test_completion_acceptance_is_the_only_new_model_path_to_completed() -> None:
    state = reduce(
        budgeted_ready_state(1),
        event(
            2,
            EventType.COMPLETION_ACCEPTED,
            payload={
                "answer_sha256": "digest",
                "step_id": "step-1",
                "summary": "accepted",
            },
        ),
    )

    assert state.status is RunStatus.COMPLETED
    assert state.turn_index == 1


def test_completion_rejection_consumes_action_and_returns_to_ready() -> None:
    state = reduce(
        budgeted_ready_state(2),
        event(
            2,
            EventType.COMPLETION_REJECTED,
            payload={
                "answer_sha256": "digest",
                "step_id": "step-1",
                "summary": "rejected",
            },
        ),
    )

    assert state.status is RunStatus.READY
    assert state.turn_index == 1


@pytest.mark.parametrize(
    "event_type",
    [EventType.COMPLETION_ACCEPTED, EventType.COMPLETION_REJECTED],
)
def test_completion_event_cannot_bypass_exhausted_budget(
    event_type: EventType,
) -> None:
    state = apply(
        budgeted_ready_state(1),
        event(
            2,
            EventType.COMPLETION_REJECTED,
            payload={
                "answer_sha256": "digest",
                "step_id": "step-1",
                "summary": "rejected",
            },
        ),
    )

    with pytest.raises(InvalidTransitionError, match="durable model step budget"):
        reduce(
            state,
            event(
                3,
                event_type,
                payload={
                    "answer_sha256": "digest",
                    "step_id": "step-2",
                    "summary": "cannot be accepted",
                },
            ),
        )


@pytest.mark.parametrize("max_steps", [0, -1, True, 1.5, "2"])
def test_run_creation_rejects_invalid_step_budget(max_steps: object) -> None:
    with pytest.raises(InvalidTransitionError, match="positive integer"):
        reduce(
            RunState.initial("run-1"),
            event(0, EventType.RUN_CREATED, payload={"max_steps": max_steps}),
        )


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


def test_timed_out_tool_terminates_with_distinct_failure_reason() -> None:
    state = apply(
        ready_state(),
        event(2, EventType.TOOL_REQUESTED, payload={"tool_call_id": "tool-1"}),
        event(3, EventType.TOOL_AUTHORIZED, payload={"tool_call_id": "tool-1"}),
        event(4, EventType.TOOL_STARTED, payload={"tool_call_id": "tool-1"}),
        event(
            5,
            EventType.TOOL_TIMED_OUT,
            payload={
                "tool_call_id": "tool-1",
                "reason": "tool execution exceeded 2.5 seconds",
                "timeout_seconds": 2.5,
            },
        ),
    )

    assert state.status is RunStatus.FAILED
    assert state.failure_reason == "tool execution exceeded 2.5 seconds"
    assert state.active_tool_call_id is None


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
                "max_attempts": 3,
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


def test_user_gate_attempts_are_durable_and_exhaustion_is_fail_closed() -> None:
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
                "ownership_mode": "user_gate",
                "max_attempts": 2,
            },
        ),
        event(
            4,
            EventType.GATE_EVALUATED,
            payload={
                "tool_call_id": "tool-1",
                "proposal_digest": "a" * 64,
                "revision": 1,
                "attempt": 1,
                "max_attempts": 2,
                "outcome": "retry",
                "reason": "incomplete explanation",
            },
        ),
    )

    assert state.status is RunStatus.AWAITING_GATE
    assert state.active_gate_attempts == 1
    assert state.active_gate_max_attempts == 2

    blocked = reduce(
        state,
        event(
            5,
            EventType.GATE_EVALUATED,
            payload={
                "tool_call_id": "tool-1",
                "proposal_digest": "a" * 64,
                "revision": 1,
                "attempt": 2,
                "max_attempts": 2,
                "outcome": "block",
                "reason": "attempt limit exhausted",
            },
        ),
    )

    assert blocked.status is RunStatus.FAILED
    assert blocked.failure_reason == "attempt limit exhausted"


def test_user_gate_cannot_use_pair_approval_event() -> None:
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
                "ownership_mode": "user_gate",
                "max_attempts": 2,
            },
        ),
    )

    with pytest.raises(InvalidTransitionError, match="requires pair mode"):
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


def test_gate_revision_atomically_replaces_active_identity_and_resets_attempts() -> None:
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
                "ownership_mode": "user_gate",
                "max_attempts": 2,
            },
        ),
        event(
            4,
            EventType.GATE_EVALUATED,
            payload={
                "tool_call_id": "tool-1",
                "proposal_digest": "a" * 64,
                "revision": 1,
                "attempt": 1,
                "max_attempts": 2,
                "outcome": "retry",
                "reason": "incomplete",
            },
        ),
    )

    revised = reduce(
        state,
        event(
            5,
            EventType.GATE_REVISED,
            payload={
                "tool_call_id": "tool-1",
                "previous_proposal_digest": "a" * 64,
                "previous_revision": 1,
                "proposal_digest": "b" * 64,
                "revision": 2,
                "ownership_mode": "pair",
            },
        ),
    )

    assert revised.status is RunStatus.AWAITING_GATE
    assert revised.active_gate_proposal_digest == "b" * 64
    assert revised.active_gate_revision == 2
    assert revised.active_gate_mode == "pair"
    assert revised.active_gate_attempts == 0
    assert revised.active_gate_max_attempts is None


@pytest.mark.parametrize(
    ("previous_digest", "previous_revision", "new_revision", "message"),
    [
        ("c" * 64, 1, 2, "proposal"),
        ("a" * 64, 2, 3, "active gate revision"),
        ("a" * 64, 1, 3, "increment"),
    ],
)
def test_gate_revision_rejects_stale_or_skipped_predecessor(
    previous_digest: str,
    previous_revision: int,
    new_revision: int,
    message: str,
) -> None:
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

    with pytest.raises(InvalidTransitionError, match=message):
        reduce(
            state,
            event(
                4,
                EventType.GATE_REVISED,
                payload={
                    "tool_call_id": "tool-1",
                    "previous_proposal_digest": previous_digest,
                    "previous_revision": previous_revision,
                    "proposal_digest": "b" * 64,
                    "revision": new_revision,
                    "ownership_mode": "pair",
                },
            ),
        )
