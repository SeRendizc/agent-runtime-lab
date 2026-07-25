"""Pure state reduction for the R1 execution lifecycle."""

from dataclasses import replace

from agent_runtime_lab.domain.errors import (
    DuplicateEventConflictError,
    InvalidTransitionError,
    RunMismatchError,
    SequenceError,
    TerminalStateError,
)
from agent_runtime_lab.domain.events import EventType, ExecutionEvent
from agent_runtime_lab.domain.state import RunState, RunStatus


def _expect(state: RunState, expected: RunStatus, event: ExecutionEvent) -> None:
    if state.status is not expected:
        raise InvalidTransitionError(
            f"{event.event_type.value} requires {expected.value}, got {state.status.value}"
        )


def _required_text(event: ExecutionEvent, field: str) -> str:
    value = event.payload.get(field)
    if not isinstance(value, str) or not value:
        raise InvalidTransitionError(f"{event.event_type.value} requires non-empty payload.{field}")
    return value


def _expect_active_tool(state: RunState, event: ExecutionEvent) -> str:
    tool_call_id = _required_text(event, "tool_call_id")
    if tool_call_id != state.active_tool_call_id:
        raise InvalidTransitionError(
            f"{event.event_type.value} does not match active tool call "
            f"{state.active_tool_call_id!r}"
        )
    return tool_call_id


def _transition(state: RunState, event: ExecutionEvent) -> RunState:
    if event.event_type is EventType.RUN_CANCELLED:
        return replace(
            state,
            status=RunStatus.CANCELLED,
            active_tool_call_id=None,
            failure_reason=None,
        )

    if event.event_type is EventType.RUN_CREATED:
        _expect(state, RunStatus.NEW, event)
        return replace(state, status=RunStatus.CREATED)

    if event.event_type is EventType.RUN_STARTED:
        _expect(state, RunStatus.CREATED, event)
        return replace(state, status=RunStatus.READY)

    if event.event_type is EventType.RUN_PAUSED:
        _expect(state, RunStatus.READY, event)
        return replace(state, status=RunStatus.PAUSED)

    if event.event_type is EventType.RUN_RESUMED:
        _expect(state, RunStatus.PAUSED, event)
        return replace(state, status=RunStatus.READY)

    if event.event_type is EventType.TOOL_REQUESTED:
        _expect(state, RunStatus.READY, event)
        return replace(
            state,
            status=RunStatus.TOOL_PENDING,
            active_tool_call_id=_required_text(event, "tool_call_id"),
        )

    if event.event_type is EventType.TOOL_AUTHORIZED:
        _expect(state, RunStatus.TOOL_PENDING, event)
        _expect_active_tool(state, event)
        return replace(state, status=RunStatus.TOOL_READY)

    if event.event_type is EventType.TOOL_DENIED:
        _expect(state, RunStatus.TOOL_PENDING, event)
        _expect_active_tool(state, event)
        reason = _required_text(event, "reason")
        return replace(
            state,
            status=RunStatus.FAILED,
            active_tool_call_id=None,
            failure_reason=reason,
        )

    if event.event_type is EventType.TOOL_STARTED:
        _expect(state, RunStatus.TOOL_READY, event)
        _expect_active_tool(state, event)
        return replace(state, status=RunStatus.TOOL_RUNNING)

    if event.event_type is EventType.TOOL_SUCCEEDED:
        _expect(state, RunStatus.TOOL_RUNNING, event)
        _expect_active_tool(state, event)
        return replace(
            state,
            status=RunStatus.VERIFYING,
            active_tool_call_id=None,
        )

    if event.event_type is EventType.TOOL_FAILED:
        _expect(state, RunStatus.TOOL_RUNNING, event)
        _expect_active_tool(state, event)
        reason = _required_text(event, "reason")
        return replace(
            state,
            status=RunStatus.FAILED,
            active_tool_call_id=None,
            failure_reason=reason,
        )

    if event.event_type is EventType.VERIFICATION_SUCCEEDED:
        _expect(state, RunStatus.VERIFYING, event)
        return replace(state, status=RunStatus.COMPLETED)

    if event.event_type is EventType.VERIFICATION_FAILED:
        _expect(state, RunStatus.VERIFYING, event)
        return replace(
            state,
            status=RunStatus.FAILED,
            failure_reason=_required_text(event, "reason"),
        )

    raise InvalidTransitionError(f"unsupported event type: {event.event_type.value}")


def reduce(state: RunState, event: ExecutionEvent) -> RunState:
    """Apply one event without I/O, time access, randomness, or mutation."""
    if event.run_id != state.run_id:
        raise RunMismatchError(
            f"event run {event.run_id!r} does not match state run {state.run_id!r}"
        )

    fingerprint = event.fingerprint()
    existing = state.fingerprint_for(event.event_id)
    if existing is not None:
        if existing == fingerprint:
            return state
        raise DuplicateEventConflictError(
            f"event_id {event.event_id!r} was reused with different content"
        )

    if state.is_terminal:
        raise TerminalStateError(f"cannot mutate terminal state {state.status.value}")

    if event.sequence != state.next_sequence:
        raise SequenceError(f"expected sequence {state.next_sequence}, got {event.sequence}")

    transitioned = _transition(state, event)
    return replace(
        transitioned,
        next_sequence=state.next_sequence + 1,
        applied_event_fingerprints=state.applied_event_fingerprints
        + ((event.event_id, fingerprint),),
    )
