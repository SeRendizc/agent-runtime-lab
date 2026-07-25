"""Deterministically rebuild run state from ordered execution events."""

from collections.abc import Iterable

from agent_runtime_lab.domain.events import ExecutionEvent
from agent_runtime_lab.domain.reducer import reduce
from agent_runtime_lab.domain.state import RunState


def replay(run_id: str, events: Iterable[ExecutionEvent]) -> RunState:
    state = RunState.initial(run_id)
    for event in events:
        state = reduce(state, event)
    return state
