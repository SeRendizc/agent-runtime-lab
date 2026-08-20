"""Deterministically rebuild run state from ordered execution events."""

from collections.abc import Iterable

from agent_runtime_lab.domain.events import ExecutionEvent
from agent_runtime_lab.domain.reducer import reduce
from agent_runtime_lab.domain.state import RunState


def replay(run_id: str, events: Iterable[ExecutionEvent]) -> RunState:
    state = RunState.initial(run_id)
    return replay_tail(state, events)


def replay_tail(state: RunState, events: Iterable[ExecutionEvent]) -> RunState:
    """Apply events after an already-derived, validated state prefix."""

    for event in events:
        state = reduce(state, event)
    return state
