import pytest

from agent_runtime_lab.domain.errors import EventValidationError
from agent_runtime_lab.domain.state import TERMINAL_STATUSES, RunState, RunStatus


def test_initial_state_is_empty_and_expects_sequence_zero() -> None:
    state = RunState.initial("run-1")

    assert state.run_id == "run-1"
    assert state.status is RunStatus.NEW
    assert state.next_sequence == 0
    assert state.turn_index == 0
    assert state.active_tool_call_id is None
    assert state.active_step_id is None
    assert state.failure_reason is None
    assert state.applied_event_fingerprints == ()


def test_empty_run_id_is_rejected() -> None:
    with pytest.raises(EventValidationError, match="run_id"):
        RunState.initial("")


def test_terminal_statuses_are_explicit() -> None:
    assert TERMINAL_STATUSES == frozenset(
        {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}
    )
