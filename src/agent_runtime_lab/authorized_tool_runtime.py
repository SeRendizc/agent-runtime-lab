"""Authorization-aware orchestration around durable tool effects."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

from agent_runtime_lab.domain.errors import GateReferenceMismatchError, InvalidTransitionError
from agent_runtime_lab.domain.events import EventType, ExecutionEvent
from agent_runtime_lab.domain.replay import replay
from agent_runtime_lab.domain.state import RunState, RunStatus
from agent_runtime_lab.domain.tool_effects import ToolIntent, ToolOutcome, ToolReceipt
from agent_runtime_lab.durable_tool_executor import DurableToolExecutor
from agent_runtime_lab.ownership.authorization import (
    AuthorizationContext,
    AuthorizationDecision,
    AuthorizationOutcome,
    ToolRequest,
    authorize,
)
from agent_runtime_lab.ownership.gates import (
    GateAction,
    GateProposal,
    GateReference,
    GateResolution,
)


class EventStore(Protocol):
    """Append and replay immutable Runtime events."""

    def append(self, event: ExecutionEvent) -> None:
        """Persist one event."""

    def load(self, run_id: str) -> list[ExecutionEvent]:
        """Load one run in sequence order."""


class RuntimeToolOutcome(StrEnum):
    """Caller-facing outcome of one orchestration step."""

    EXECUTED = "executed"
    DENIED = "denied"
    AWAITING_GATE = "awaiting_gate"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class RuntimeToolResult:
    """Inspectable result without hiding the resulting durable state."""

    outcome: RuntimeToolOutcome
    state: RunState
    authorization: AuthorizationDecision | None = None
    gate_proposal: GateProposal | None = None
    receipt: ToolReceipt | None = None


def _utc_now() -> datetime:
    return datetime.now(UTC)


class AuthorizedToolRuntime:
    """Enforce authorization and durable gates before external effects."""

    def __init__(
        self,
        *,
        event_store: EventStore,
        executor: DurableToolExecutor,
        authorization_context: AuthorizationContext,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._event_store = event_store
        self._executor = executor
        self._authorization_context = authorization_context
        self._clock = clock

    def submit(self, request: ToolRequest) -> RuntimeToolResult:
        """Authorize a concrete request, then deny, pause, or execute it."""

        self._append(
            request.run_id,
            EventType.TOOL_REQUESTED,
            {
                "arguments_json": request.arguments_json,
                "step_id": request.step_id,
                "tool_call_id": request.tool_call_id,
                "tool_name": request.tool_name,
            },
        )
        decision = authorize(request, self._authorization_context)

        if decision.outcome is AuthorizationOutcome.DENY:
            state = self._append(
                request.run_id,
                EventType.TOOL_DENIED,
                {
                    "reason": "; ".join(decision.reasons),
                    "tool_call_id": request.tool_call_id,
                },
            )
            return RuntimeToolResult(
                outcome=RuntimeToolOutcome.DENIED,
                state=state,
                authorization=decision,
            )

        if decision.outcome is AuthorizationOutcome.ESCALATE:
            proposal = GateProposal.build(
                request=request,
                decision=decision,
                revision=1,
            )
            state = self._append(
                request.run_id,
                EventType.TOOL_ESCALATED,
                {
                    "ownership_mode": proposal.ownership_mode.value,
                    "proposal_digest": proposal.reference.proposal_digest,
                    "reasons": list(proposal.reasons),
                    "revision": proposal.reference.revision,
                    "tool_call_id": request.tool_call_id,
                },
            )
            return RuntimeToolResult(
                outcome=RuntimeToolOutcome.AWAITING_GATE,
                state=state,
                authorization=decision,
                gate_proposal=proposal,
            )

        self._append(
            request.run_id,
            EventType.TOOL_AUTHORIZED,
            self._authorization_payload(request, decision),
        )
        return self._execute(request, authorization=decision)

    def resolve_gate(self, resolution: GateResolution) -> RuntimeToolResult:
        """Resolve and, on approval, execute the exact persisted proposal."""

        state = self.load_state(resolution.reference.run_id)
        self._validate_reference(state, resolution.reference)
        request = self._load_active_request(
            resolution.reference.run_id,
            resolution.reference.tool_call_id,
        )

        payload = {
            "actor": resolution.actor,
            "proposal_digest": resolution.reference.proposal_digest,
            "reason": resolution.reason,
            "revision": resolution.reference.revision,
            "tool_call_id": resolution.reference.tool_call_id,
        }

        if resolution.action is GateAction.REJECT:
            rejected_state = self._append(
                resolution.reference.run_id,
                EventType.GATE_REJECTED,
                payload,
            )
            return RuntimeToolResult(
                outcome=RuntimeToolOutcome.REJECTED,
                state=rejected_state,
            )

        current_decision = authorize(request, self._authorization_context)
        if current_decision.outcome is AuthorizationOutcome.DENY:
            raise GateReferenceMismatchError(
                "active proposal is no longer allowed by current authorization policy"
            )
        if current_decision.outcome is AuthorizationOutcome.ESCALATE:
            current_proposal = GateProposal.build(
                request=request,
                decision=current_decision,
                revision=resolution.reference.revision,
            )
            if current_proposal.reference != resolution.reference:
                raise GateReferenceMismatchError(
                    "active proposal no longer matches current authorization policy"
                )

        self._append(
            resolution.reference.run_id,
            EventType.GATE_APPROVED,
            payload,
        )
        return self._execute(request, authorization=current_decision)

    def recover(self, run_id: str) -> RuntimeToolResult:
        """Continue an approved or already-started durable effect after restart."""

        state = self.load_state(run_id)
        if state.status not in {RunStatus.TOOL_READY, RunStatus.TOOL_RUNNING}:
            raise InvalidTransitionError(
                f"tool recovery requires tool_ready or tool_running, got {state.status.value}"
            )
        if state.active_tool_call_id is None:
            raise InvalidTransitionError("tool recovery requires an active tool call")

        request = self._load_active_request(run_id, state.active_tool_call_id)
        return self._execute(
            request,
            append_started=state.status is RunStatus.TOOL_READY,
        )

    def load_state(self, run_id: str) -> RunState:
        """Rebuild current state exclusively from durable events."""

        return replay(run_id, self._event_store.load(run_id))

    def _execute(
        self,
        request: ToolRequest,
        *,
        authorization: AuthorizationDecision | None = None,
        append_started: bool = True,
    ) -> RuntimeToolResult:
        if append_started:
            self._append(
                request.run_id,
                EventType.TOOL_STARTED,
                {"tool_call_id": request.tool_call_id},
            )
        intent = ToolIntent.build(
            run_id=request.run_id,
            tool_call_id=request.tool_call_id,
            tool_name=request.tool_name,
            arguments=request.arguments,
        )
        receipt = self._executor.execute(intent=intent)

        if receipt.outcome is ToolOutcome.FAILED:
            reason = receipt.output.get("message")
            if not isinstance(reason, str) or not reason:
                reason = "tool execution failed"
            state = self._append(
                request.run_id,
                EventType.TOOL_FAILED,
                {
                    "reason": reason,
                    "tool_call_id": request.tool_call_id,
                },
            )
        else:
            state = self._append(
                request.run_id,
                EventType.TOOL_SUCCEEDED,
                {
                    "effect_id": receipt.effect_id,
                    "tool_call_id": request.tool_call_id,
                },
            )

        return RuntimeToolResult(
            outcome=RuntimeToolOutcome.EXECUTED,
            state=state,
            authorization=authorization,
            receipt=receipt,
        )

    def _append(
        self,
        run_id: str,
        event_type: EventType,
        payload: Mapping[str, Any],
    ) -> RunState:
        state = self.load_state(run_id)
        sequence = state.next_sequence
        event = ExecutionEvent.build(
            event_id=f"{run_id}:{sequence}:{event_type.value}",
            run_id=run_id,
            sequence=sequence,
            event_type=event_type,
            occurred_at=self._clock(),
            payload=payload,
        )
        self._event_store.append(event)
        return replay(run_id, (*self._event_store.load(run_id),))

    def _load_active_request(self, run_id: str, tool_call_id: str) -> ToolRequest:
        for event in reversed(self._event_store.load(run_id)):
            if event.event_type is not EventType.TOOL_REQUESTED:
                continue
            if event.payload.get("tool_call_id") != tool_call_id:
                continue
            return ToolRequest(
                run_id=run_id,
                step_id=self._payload_text(event.payload, "step_id"),
                tool_call_id=tool_call_id,
                tool_name=self._payload_text(event.payload, "tool_name"),
                arguments_json=self._payload_text(event.payload, "arguments_json"),
            )
        raise GateReferenceMismatchError("active gate has no matching persisted tool request")

    @staticmethod
    def _payload_text(payload: Mapping[str, Any], field: str) -> str:
        value = payload.get(field)
        if not isinstance(value, str) or not value:
            raise GateReferenceMismatchError(f"persisted request has invalid {field}")
        return value

    @staticmethod
    def _validate_reference(state: RunState, reference: GateReference) -> None:
        if state.status is not RunStatus.AWAITING_GATE:
            raise GateReferenceMismatchError("run is not awaiting a gate resolution")
        if reference.tool_call_id != state.active_tool_call_id:
            raise GateReferenceMismatchError("gate reference does not match active tool call")
        if reference.proposal_digest != state.active_gate_proposal_digest:
            raise GateReferenceMismatchError("gate reference does not match active proposal")
        if reference.revision != state.active_gate_revision:
            raise GateReferenceMismatchError("gate reference does not match active revision")

    @staticmethod
    def _authorization_payload(
        request: ToolRequest,
        decision: AuthorizationDecision,
    ) -> dict[str, Any]:
        ownership_mode = None
        if decision.ownership_decision is not None:
            ownership_mode = decision.ownership_decision.mode.value
        return {
            "normalized_paths": list(decision.normalized_paths),
            "outcome": decision.outcome.value,
            "ownership_mode": ownership_mode,
            "reasons": list(decision.reasons),
            "tool_call_id": request.tool_call_id,
        }
