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


def _required_positive_int(event: ExecutionEvent, field: str) -> int:
    value = event.payload.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise InvalidTransitionError(
            f"{event.event_type.value} requires positive integer payload.{field}"
        )
    return value


def _expect_active_gate(state: RunState, event: ExecutionEvent) -> None:
    _expect_active_tool(state, event)
    proposal_digest = _required_text(event, "proposal_digest")
    revision = _required_positive_int(event, "revision")

    if proposal_digest != state.active_gate_proposal_digest:
        raise InvalidTransitionError(
            f"{event.event_type.value} does not match active gate proposal"
        )
    if revision != state.active_gate_revision:
        raise InvalidTransitionError(
            f"{event.event_type.value} does not match active gate revision"
        )


def _transition(state: RunState, event: ExecutionEvent) -> RunState:
    if event.event_type is EventType.RUN_CANCELLED:
        return replace(
            state,
            status=RunStatus.CANCELLED,
            active_step_id=None,
            active_tool_call_id=None,
            active_gate_proposal_digest=None,
            active_gate_revision=None,
            active_gate_mode=None,
            active_gate_attempts=0,
            active_gate_max_attempts=None,
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
        step_id = event.payload.get("step_id")
        if step_id is not None and (not isinstance(step_id, str) or not step_id):
            raise InvalidTransitionError(
                "tool.requested payload.step_id must be a non-empty string"
            )
        return replace(
            state,
            status=RunStatus.TOOL_PENDING,
            active_step_id=step_id,
            active_tool_call_id=_required_text(event, "tool_call_id"),
        )

    if event.event_type is EventType.TOOL_AUTHORIZED:
        _expect(state, RunStatus.TOOL_PENDING, event)
        _expect_active_tool(state, event)
        return replace(state, status=RunStatus.TOOL_READY)

    if event.event_type is EventType.TOOL_ESCALATED:
        _expect(state, RunStatus.TOOL_PENDING, event)
        _expect_active_tool(state, event)
        proposal_digest = _required_text(event, "proposal_digest")
        revision = _required_positive_int(event, "revision")
        ownership_mode = _required_text(event, "ownership_mode")
        if ownership_mode not in {"pair", "user_gate"}:
            raise InvalidTransitionError(
                "tool.escalated requires pair or user_gate payload.ownership_mode"
            )
        max_attempts = None
        if ownership_mode == "user_gate":
            max_attempts = _required_positive_int(event, "max_attempts")
        return replace(
            state,
            status=RunStatus.AWAITING_GATE,
            active_gate_proposal_digest=proposal_digest,
            active_gate_revision=revision,
            active_gate_mode=ownership_mode,
            active_gate_attempts=0,
            active_gate_max_attempts=max_attempts,
        )

    if event.event_type is EventType.GATE_APPROVED:
        _expect(state, RunStatus.AWAITING_GATE, event)
        _expect_active_gate(state, event)
        if state.active_gate_mode != "pair":
            raise InvalidTransitionError(
                "gate.approved requires pair mode; user_gate requires evaluation"
            )
        return replace(
            state,
            status=RunStatus.TOOL_READY,
            active_gate_proposal_digest=None,
            active_gate_revision=None,
            active_gate_mode=None,
            active_gate_attempts=0,
            active_gate_max_attempts=None,
        )

    if event.event_type is EventType.GATE_REVISED:
        _expect(state, RunStatus.AWAITING_GATE, event)
        _expect_active_tool(state, event)
        previous_proposal_digest = _required_text(event, "previous_proposal_digest")
        previous_revision = _required_positive_int(event, "previous_revision")
        if previous_proposal_digest != state.active_gate_proposal_digest:
            raise InvalidTransitionError("gate.revised does not match active gate proposal")
        if previous_revision != state.active_gate_revision:
            raise InvalidTransitionError("gate.revised does not match active gate revision")

        proposal_digest = _required_text(event, "proposal_digest")
        revision = _required_positive_int(event, "revision")
        if revision != previous_revision + 1:
            raise InvalidTransitionError("gate.revised revision must increment the active revision")
        if proposal_digest == previous_proposal_digest:
            raise InvalidTransitionError("gate.revised requires a new proposal digest")

        ownership_mode = _required_text(event, "ownership_mode")
        if ownership_mode not in {"pair", "user_gate"}:
            raise InvalidTransitionError(
                "gate.revised requires pair or user_gate payload.ownership_mode"
            )
        max_attempts = None
        if ownership_mode == "user_gate":
            max_attempts = _required_positive_int(event, "max_attempts")
        return replace(
            state,
            active_gate_proposal_digest=proposal_digest,
            active_gate_revision=revision,
            active_gate_mode=ownership_mode,
            active_gate_attempts=0,
            active_gate_max_attempts=max_attempts,
        )

    if event.event_type is EventType.GATE_EVALUATED:
        _expect(state, RunStatus.AWAITING_GATE, event)
        _expect_active_gate(state, event)
        if state.active_gate_mode != "user_gate":
            raise InvalidTransitionError("gate.evaluated requires an active user_gate")

        attempt = _required_positive_int(event, "attempt")
        max_attempts = _required_positive_int(event, "max_attempts")
        if max_attempts != state.active_gate_max_attempts:
            raise InvalidTransitionError("gate.evaluated max_attempts changed")
        if attempt != state.active_gate_attempts + 1:
            raise InvalidTransitionError(
                "gate.evaluated attempt must increment the durable attempt count"
            )

        outcome = _required_text(event, "outcome")
        reason = _required_text(event, "reason")
        if outcome == "retry":
            if attempt >= max_attempts:
                raise InvalidTransitionError(
                    "gate.evaluated cannot retry after exhausting attempts"
                )
            return replace(state, active_gate_attempts=attempt)
        if outcome == "pass":
            return replace(
                state,
                status=RunStatus.TOOL_READY,
                active_gate_proposal_digest=None,
                active_gate_revision=None,
                active_gate_mode=None,
                active_gate_attempts=0,
                active_gate_max_attempts=None,
            )
        if outcome == "block":
            return replace(
                state,
                status=RunStatus.FAILED,
                active_step_id=None,
                active_tool_call_id=None,
                active_gate_proposal_digest=None,
                active_gate_revision=None,
                active_gate_mode=None,
                active_gate_attempts=0,
                active_gate_max_attempts=None,
                failure_reason=reason,
            )
        raise InvalidTransitionError(
            "gate.evaluated requires pass, retry, or block payload.outcome"
        )

    if event.event_type is EventType.GATE_REJECTED:
        _expect(state, RunStatus.AWAITING_GATE, event)
        _expect_active_gate(state, event)
        reason = _required_text(event, "reason")
        return replace(
            state,
            status=RunStatus.FAILED,
            active_step_id=None,
            active_tool_call_id=None,
            active_gate_proposal_digest=None,
            active_gate_revision=None,
            active_gate_mode=None,
            active_gate_attempts=0,
            active_gate_max_attempts=None,
            failure_reason=reason,
        )

    if event.event_type is EventType.TOOL_DENIED:
        _expect(state, RunStatus.TOOL_PENDING, event)
        _expect_active_tool(state, event)
        reason = _required_text(event, "reason")
        return replace(
            state,
            status=RunStatus.FAILED,
            active_step_id=None,
            active_tool_call_id=None,
            active_gate_proposal_digest=None,
            active_gate_revision=None,
            active_gate_mode=None,
            active_gate_attempts=0,
            active_gate_max_attempts=None,
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
            active_gate_proposal_digest=None,
            active_gate_revision=None,
            active_gate_mode=None,
            active_gate_attempts=0,
            active_gate_max_attempts=None,
        )

    if event.event_type is EventType.TOOL_FAILED:
        _expect(state, RunStatus.TOOL_RUNNING, event)
        _expect_active_tool(state, event)
        reason = _required_text(event, "reason")
        return replace(
            state,
            status=RunStatus.FAILED,
            active_step_id=None,
            active_tool_call_id=None,
            active_gate_proposal_digest=None,
            active_gate_revision=None,
            active_gate_mode=None,
            active_gate_attempts=0,
            active_gate_max_attempts=None,
            failure_reason=reason,
        )

    if event.event_type is EventType.TOOL_TIMED_OUT:
        _expect(state, RunStatus.TOOL_RUNNING, event)
        _expect_active_tool(state, event)
        reason = _required_text(event, "reason")
        return replace(
            state,
            status=RunStatus.FAILED,
            active_step_id=None,
            active_tool_call_id=None,
            active_gate_proposal_digest=None,
            active_gate_revision=None,
            active_gate_mode=None,
            active_gate_attempts=0,
            active_gate_max_attempts=None,
            failure_reason=reason,
        )

    if event.event_type is EventType.VERIFICATION_SUCCEEDED:
        _expect(state, RunStatus.VERIFYING, event)
        scope = event.payload.get("scope", "run")
        if scope == "run":
            return replace(
                state,
                status=RunStatus.COMPLETED,
                active_step_id=None,
            )
        if scope == "step":
            step_id = _required_text(event, "step_id")
            if state.active_step_id is None or step_id != state.active_step_id:
                raise InvalidTransitionError("verification.succeeded does not match active step")
            return replace(
                state,
                status=RunStatus.READY,
                turn_index=state.turn_index + 1,
                active_step_id=None,
            )
        raise InvalidTransitionError("verification.succeeded payload.scope must be run or step")

    if event.event_type is EventType.VERIFICATION_FAILED:
        _expect(state, RunStatus.VERIFYING, event)
        return replace(
            state,
            status=RunStatus.FAILED,
            active_step_id=None,
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
