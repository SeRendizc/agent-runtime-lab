"""Authorization-aware orchestration around durable tool effects."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

from agent_runtime_lab.completion import CompletionOutcome, CompletionResult
from agent_runtime_lab.domain.errors import (
    GateReferenceMismatchError,
    InvalidTransitionError,
    MissingVerificationEvidenceError,
    StepBudgetExhaustedError,
)
from agent_runtime_lab.domain.events import EventType, ExecutionEvent
from agent_runtime_lab.domain.replay import replay, replay_tail
from agent_runtime_lab.domain.state import RunState, RunStatus
from agent_runtime_lab.domain.tool_effects import ToolIntent, ToolOutcome, ToolReceipt
from agent_runtime_lab.durable_tool_executor import DurableToolExecutor
from agent_runtime_lab.model_adapter import (
    FinalAnswerAction,
    ModelAction,
    ModelInput,
    ToolCallAction,
)
from agent_runtime_lab.ownership.authorization import (
    AuthorizationContext,
    AuthorizationDecision,
    AuthorizationOutcome,
    ToolRequest,
    authorize,
)
from agent_runtime_lab.ownership.gates import (
    GateAction,
    GateAnswerSubmission,
    GateEvaluation,
    GateEvaluationOutcome,
    GateProposal,
    GateReference,
    GateResolution,
    evaluate_gate,
)
from agent_runtime_lab.ownership.policy import OwnershipMode
from agent_runtime_lab.verification import VerificationOutcome, VerificationResult


class EventStore(Protocol):
    """Append and replay immutable Runtime events."""

    def append(self, event: ExecutionEvent) -> None:
        """Persist one event."""

    def load(self, run_id: str) -> list[ExecutionEvent]:
        """Load one run in sequence order."""


class SnapshotStore(Protocol):
    """Store validated state accelerators bound to Event prefixes."""

    def save_snapshot(self, state: RunState) -> None:
        """Persist a replaceable snapshot for one derived state."""

    def load_snapshot(self, run_id: str) -> RunState | None:
        """Load a snapshot only when its integrity bindings validate."""

    def load_tail(self, run_id: str, start_sequence: int) -> list[ExecutionEvent]:
        """Load events after the snapshot prefix."""


class SnapshotCheckpoint(StrEnum):
    """Crash boundaries around disposable Snapshot recovery."""

    AFTER_SNAPSHOT_PERSISTED = "after_snapshot_persisted"
    BEFORE_TAIL_REPLAY = "before_tail_replay"
    BEFORE_FULL_REPLAY = "before_full_replay"


class SnapshotFailureInjector(Protocol):
    """Observe Snapshot recovery boundaries and optionally crash."""

    def reach(self, checkpoint: SnapshotCheckpoint) -> None:
        """Handle one Snapshot checkpoint."""


class RuntimeToolOutcome(StrEnum):
    """Caller-facing outcome of one orchestration step."""

    EXECUTED = "executed"
    DENIED = "denied"
    AWAITING_GATE = "awaiting_gate"
    GATE_RETRY = "gate_retry"
    BLOCKED = "blocked"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class RuntimeToolResult:
    """Inspectable result without hiding the resulting durable state."""

    outcome: RuntimeToolOutcome
    state: RunState
    authorization: AuthorizationDecision | None = None
    gate_proposal: GateProposal | None = None
    gate_evaluation: GateEvaluation | None = None
    gate_attempt: int | None = None
    receipt: ToolReceipt | None = None


@dataclass(frozen=True, slots=True)
class ModelToolRecovery:
    """Persisted model Tool turn reconstructed without calling the Adapter."""

    context: ModelInput
    action: ToolCallAction
    receipt: ToolReceipt


@dataclass(frozen=True, slots=True)
class DurableModelAction:
    """One exact Adapter Action persisted before Runtime dispatch."""

    event_id: str
    invocation_id: str
    context: ModelInput
    action: ModelAction


def _utc_now() -> datetime:
    return datetime.now(UTC)


class AuthorizedToolRuntime:
    """Enforce authorization and durable gates before external effects."""

    def __init__(
        self,
        *,
        event_store: EventStore,
        snapshot_store: SnapshotStore | None = None,
        snapshot_failure_injector: SnapshotFailureInjector | None = None,
        executor: DurableToolExecutor,
        authorization_context: AuthorizationContext,
        gate_evaluator: Callable[[GateProposal, Mapping[str, Any]], GateEvaluation] = evaluate_gate,
        user_gate_max_attempts: int = 3,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if user_gate_max_attempts < 1:
            raise ValueError("user_gate_max_attempts must be positive")
        if snapshot_store is not None and snapshot_store is not event_store:
            raise ValueError("snapshot_store must be the configured event_store instance")
        if snapshot_failure_injector is not None and snapshot_store is None:
            raise ValueError("snapshot failure injection requires snapshot_store")
        self._event_store = event_store
        self._snapshot_store = snapshot_store
        self._snapshot_failure_injector = snapshot_failure_injector
        self._executor = executor
        self._authorization_context = authorization_context
        self._gate_evaluator = gate_evaluator
        self._user_gate_max_attempts = user_gate_max_attempts
        self._clock = clock

    def submit(self, request: ToolRequest) -> RuntimeToolResult:
        """Authorize a concrete request, then deny, pause, or execute it."""

        request_payload: dict[str, Any] = {
            "arguments_json": request.arguments_json,
            "step_id": request.step_id,
            "tool_call_id": request.tool_call_id,
            "tool_name": request.tool_name,
        }
        if request.model_action_event_id is not None:
            request_payload["model_action_event_id"] = request.model_action_event_id
        self._append(
            request.run_id,
            EventType.TOOL_REQUESTED,
            request_payload,
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
            escalation_payload: dict[str, Any] = {
                "ownership_mode": proposal.ownership_mode.value,
                "proposal_digest": proposal.reference.proposal_digest,
                "reasons": list(proposal.reasons),
                "revision": proposal.reference.revision,
                "tool_call_id": request.tool_call_id,
            }
            if proposal.ownership_mode is OwnershipMode.USER_GATE:
                escalation_payload["max_attempts"] = self._user_gate_max_attempts
            state = self._append(
                request.run_id,
                EventType.TOOL_ESCALATED,
                escalation_payload,
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
        if (
            resolution.action is GateAction.APPROVE
            and state.active_gate_mode == OwnershipMode.USER_GATE.value
        ):
            raise InvalidTransitionError(
                "USER_GATE approval requires an evaluated answer, not GateResolution.approve"
            )
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

    def submit_gate_answer(self, submission: GateAnswerSubmission) -> RuntimeToolResult:
        """Evaluate and persist one USER_GATE attempt for the exact proposal."""

        state = self.load_state(submission.reference.run_id)
        self._validate_reference(state, submission.reference)
        if state.active_gate_mode != OwnershipMode.USER_GATE.value:
            raise InvalidTransitionError("gate answers are only valid for USER_GATE")
        if state.active_gate_max_attempts is None:
            raise InvalidTransitionError("active USER_GATE has no attempt limit")

        request = self._load_active_request(
            submission.reference.run_id,
            submission.reference.tool_call_id,
        )
        proposal = self._load_active_proposal(request, submission.reference)
        current_decision = authorize(request, self._authorization_context)
        if current_decision.outcome is AuthorizationOutcome.DENY:
            raise GateReferenceMismatchError(
                "active proposal is no longer allowed by current authorization policy"
            )
        if current_decision.outcome is AuthorizationOutcome.ESCALATE:
            current_proposal = GateProposal.build(
                request=request,
                decision=current_decision,
                revision=submission.reference.revision,
            )
            if current_proposal.reference != submission.reference:
                raise GateReferenceMismatchError(
                    "active proposal no longer matches current authorization policy"
                )

        evaluation = self._gate_evaluator(proposal, submission.answer)
        if not isinstance(evaluation, GateEvaluation):
            raise TypeError("gate_evaluator must return GateEvaluation")
        attempt = state.active_gate_attempts + 1
        if (
            evaluation.outcome is GateEvaluationOutcome.RETRY
            and attempt >= state.active_gate_max_attempts
        ):
            evaluation = GateEvaluation(
                outcome=GateEvaluationOutcome.BLOCK,
                reason=f"attempt limit exhausted: {evaluation.reason}",
            )

        evaluated_state = self._append(
            submission.reference.run_id,
            EventType.GATE_EVALUATED,
            {
                "actor": submission.actor,
                "answer_json": submission.answer_json,
                "attempt": attempt,
                "max_attempts": state.active_gate_max_attempts,
                "outcome": evaluation.outcome.value,
                "proposal_digest": submission.reference.proposal_digest,
                "reason": evaluation.reason,
                "revision": submission.reference.revision,
                "tool_call_id": submission.reference.tool_call_id,
            },
        )

        if evaluation.outcome is GateEvaluationOutcome.PASS:
            return self._execute(
                request,
                authorization=current_decision,
                gate_evaluation=evaluation,
                gate_attempt=attempt,
            )
        if evaluation.outcome is GateEvaluationOutcome.RETRY:
            return RuntimeToolResult(
                outcome=RuntimeToolOutcome.GATE_RETRY,
                state=evaluated_state,
                authorization=current_decision,
                gate_proposal=proposal,
                gate_evaluation=evaluation,
                gate_attempt=attempt,
            )
        return RuntimeToolResult(
            outcome=RuntimeToolOutcome.BLOCKED,
            state=evaluated_state,
            authorization=current_decision,
            gate_proposal=proposal,
            gate_evaluation=evaluation,
            gate_attempt=attempt,
        )

    def revise_gate(self, reference: GateReference) -> RuntimeToolResult:
        """Replace one active proposal using the current trusted authorization policy."""

        state = self.load_state(reference.run_id)
        self._validate_reference(state, reference)
        request = self._load_active_request(reference.run_id, reference.tool_call_id)
        decision = authorize(request, self._authorization_context)
        if decision.outcome is not AuthorizationOutcome.ESCALATE:
            raise GateReferenceMismatchError(
                "current authorization policy no longer produces a gate proposal"
            )

        proposal = GateProposal.build(
            request=request,
            decision=decision,
            revision=reference.revision + 1,
        )
        payload: dict[str, Any] = {
            "ownership_mode": proposal.ownership_mode.value,
            "previous_proposal_digest": reference.proposal_digest,
            "previous_revision": reference.revision,
            "proposal_digest": proposal.reference.proposal_digest,
            "reasons": list(proposal.reasons),
            "revision": proposal.reference.revision,
            "tool_call_id": reference.tool_call_id,
        }
        if proposal.ownership_mode is OwnershipMode.USER_GATE:
            payload["max_attempts"] = self._user_gate_max_attempts
        revised_state = self._append(reference.run_id, EventType.GATE_REVISED, payload)
        return RuntimeToolResult(
            outcome=RuntimeToolOutcome.AWAITING_GATE,
            state=revised_state,
            authorization=decision,
            gate_proposal=proposal,
        )

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
        """Rebuild state from Events, optionally accelerating a validated prefix."""

        if self._snapshot_store is not None:
            snapshot = self._snapshot_store.load_snapshot(run_id)
            if snapshot is not None:
                self._reach_snapshot_checkpoint(SnapshotCheckpoint.BEFORE_TAIL_REPLAY)
                return replay_tail(
                    snapshot,
                    self._snapshot_store.load_tail(run_id, snapshot.next_sequence),
                )
            self._reach_snapshot_checkpoint(SnapshotCheckpoint.BEFORE_FULL_REPLAY)
        return replay(run_id, self._event_store.load(run_id))

    def create_snapshot(self, run_id: str) -> RunState:
        """Persist a disposable accelerator derived by full Event replay."""

        if self._snapshot_store is None:
            raise RuntimeError("snapshot_store is not configured")
        state = replay(run_id, self._event_store.load(run_id))
        self._snapshot_store.save_snapshot(state)
        self._reach_snapshot_checkpoint(SnapshotCheckpoint.AFTER_SNAPSHOT_PERSISTED)
        return state

    def _reach_snapshot_checkpoint(self, checkpoint: SnapshotCheckpoint) -> None:
        if self._snapshot_failure_injector is not None:
            self._snapshot_failure_injector.reach(checkpoint)

    def build_model_input(self, run_id: str) -> ModelInput:
        """Build the next deterministic Model input from replayed Runtime facts."""

        state = self.load_state(run_id)
        if state.status is not RunStatus.READY:
            raise InvalidTransitionError(f"model input requires ready, got {state.status.value}")
        if state.max_steps is not None and state.turn_index >= state.max_steps:
            exhausted = self._append(
                run_id,
                EventType.RUN_STEP_BUDGET_EXHAUSTED,
                {
                    "completed_steps": state.turn_index,
                    "max_steps": state.max_steps,
                },
            )
            raise StepBudgetExhaustedError(exhausted.failure_reason or "step budget exhausted")
        return ModelInput.build(
            run_id=run_id,
            step_id=f"step-{state.turn_index + 1}",
            turn_index=state.turn_index,
            state_status=state.status,
            observation=self._load_step_observation(run_id),
        )

    def begin_model_action(self, context: ModelInput) -> str:
        """Persist Adapter invocation intent before crossing the model boundary."""

        state = self.load_state(context.run_id)
        if (
            state.status is not RunStatus.READY
            or context.state_status is not RunStatus.READY
            or context.turn_index != state.turn_index
            or context.step_id != f"step-{state.turn_index + 1}"
        ):
            raise InvalidTransitionError("model action context is stale or untrusted")
        invocation_id = f"{context.run_id}:{context.step_id}:model-invocation"
        self._append(
            context.run_id,
            EventType.MODEL_ACTION_REQUESTED,
            {
                "invocation_id": invocation_id,
                "observation_json": context.observation_json,
                "step_id": context.step_id,
                "turn_index": context.turn_index,
            },
        )
        return invocation_id

    def persist_model_action(
        self,
        context: ModelInput,
        invocation_id: str,
        action: ModelAction,
    ) -> DurableModelAction:
        """Persist one validated Adapter Action before dispatching it."""

        state = self.load_state(context.run_id)
        if (
            state.status is not RunStatus.MODEL_PENDING
            or state.active_model_invocation_id != invocation_id
            or state.active_step_id != context.step_id
            or state.turn_index != context.turn_index
        ):
            raise InvalidTransitionError("model action does not match the active invocation")
        payload: dict[str, Any] = {
            "invocation_id": invocation_id,
            "step_id": context.step_id,
            "turn_index": context.turn_index,
        }
        if isinstance(action, ToolCallAction):
            payload.update(
                {
                    "action_type": "tool_call",
                    "arguments_json": action.arguments_json,
                    "tool_call_id": action.tool_call_id,
                    "tool_name": action.tool_name,
                }
            )
        elif isinstance(action, FinalAnswerAction):
            payload.update({"action_type": "final_answer", "answer": action.answer})
        else:
            raise TypeError("unsupported model action")
        state = self._append(context.run_id, EventType.MODEL_ACTION_PROPOSED, payload)
        if state.active_model_action_event_id is None:
            raise InvalidTransitionError("persisted model action has no active event identity")
        return self.load_pending_model_action(context.run_id)

    def load_pending_model_action(self, run_id: str) -> DurableModelAction:
        """Reconstruct the exact unconsumed Action without calling the Adapter."""

        state = self.load_state(run_id)
        if (
            state.status is not RunStatus.ACTION_PENDING
            or state.active_model_action_event_id is None
            or state.active_model_invocation_id is None
            or state.active_step_id is None
        ):
            raise InvalidTransitionError("run has no durable pending model action")
        requested: ExecutionEvent | None = None
        proposed: ExecutionEvent | None = None
        for event in self._event_store.load(run_id):
            if (
                event.event_type is EventType.MODEL_ACTION_REQUESTED
                and event.payload.get("invocation_id") == state.active_model_invocation_id
            ):
                requested = event
            if event.event_id == state.active_model_action_event_id:
                proposed = event
        if requested is None or proposed is None:
            raise InvalidTransitionError("durable model action evidence is incomplete")
        context = ModelInput(
            run_id=run_id,
            step_id=self._payload_text(requested.payload, "step_id"),
            turn_index=requested.payload.get("turn_index"),
            state_status=RunStatus.READY,
            observation_json=self._payload_text(requested.payload, "observation_json"),
        )
        action_type = self._payload_text(proposed.payload, "action_type")
        if action_type == "tool_call":
            action: ModelAction = ToolCallAction(
                tool_call_id=self._payload_text(proposed.payload, "tool_call_id"),
                tool_name=self._payload_text(proposed.payload, "tool_name"),
                arguments_json=self._payload_text(proposed.payload, "arguments_json"),
            )
        elif action_type == "final_answer":
            action = FinalAnswerAction(answer=self._payload_text(proposed.payload, "answer"))
        else:
            raise InvalidTransitionError("durable model action type is invalid")
        return DurableModelAction(
            event_id=proposed.event_id,
            invocation_id=state.active_model_invocation_id,
            context=context,
            action=action,
        )

    def record_model_action_failure(self, run_id: str, reason: str) -> RunState:
        """Fail a ready Run with a sanitized model-boundary reason."""

        if not isinstance(reason, str) or not reason:
            raise ValueError("model action failure reason must be non-empty")
        return self._append(
            run_id,
            EventType.MODEL_ACTION_FAILED,
            {"reason": reason},
        )

    def record_verification(
        self,
        run_id: str,
        result: VerificationResult,
    ) -> RunState:
        """Persist trusted verification evidence and derive the terminal state."""

        payload: dict[str, Any] = {
            "checks": [
                {
                    "name": check.name,
                    "passed": check.passed,
                    "message": check.message,
                }
                for check in result.checks
            ],
            "summary": result.summary,
        }
        if result.outcome is VerificationOutcome.FAILED:
            payload["reason"] = result.summary
            event_type = EventType.VERIFICATION_FAILED
        else:
            event_type = EventType.VERIFICATION_SUCCEEDED
        return self._append(run_id, event_type, payload)

    def record_step_verification(
        self,
        run_id: str,
        result: VerificationResult,
    ) -> RunState:
        """Persist one trusted tool-step result without claiming the run is complete."""

        state = self.load_state(run_id)
        if state.status is not RunStatus.VERIFYING or state.active_step_id is None:
            raise InvalidTransitionError(
                "step verification requires a verifying run with an active step"
            )
        if result.outcome is VerificationOutcome.FAILED:
            return self.record_verification(run_id, result)
        payload: dict[str, Any] = {
            "checks": [
                {
                    "name": check.name,
                    "passed": check.passed,
                    "message": check.message,
                }
                for check in result.checks
            ],
            "scope": "step",
            "step_id": state.active_step_id,
            "summary": result.summary,
        }
        return self._append(run_id, EventType.VERIFICATION_SUCCEEDED, payload)

    def record_completion(
        self,
        context: ModelInput,
        action: FinalAnswerAction,
        result: CompletionResult,
    ) -> RunState:
        """Persist trusted evidence for one exact final-answer proposal."""

        if not isinstance(result, CompletionResult):
            raise TypeError("completion verifier must return CompletionResult")
        state = self.load_state(context.run_id)
        if state.status not in {RunStatus.READY, RunStatus.ACTION_PENDING}:
            raise InvalidTransitionError(
                f"completion recording requires ready or action_pending, got {state.status.value}"
            )
        if (
            context.state_status is not RunStatus.READY
            or context.turn_index != state.turn_index
            or context.step_id != f"step-{state.turn_index + 1}"
        ):
            raise InvalidTransitionError("completion context is stale or untrusted")

        answer_sha256 = hashlib.sha256(action.answer.encode("utf-8")).hexdigest()
        if result.answer_sha256 != answer_sha256:
            raise InvalidTransitionError("completion evidence does not match proposed answer")
        model_action_event_id = state.active_model_action_event_id
        if state.status is RunStatus.ACTION_PENDING:
            pending = self.load_pending_model_action(context.run_id)
            if pending.context != context or pending.action != action:
                raise InvalidTransitionError("completion does not match durable model action")
        payload = {
            "answer": action.answer,
            "answer_sha256": answer_sha256,
            "checks": [
                {
                    "name": check.name,
                    "passed": check.passed,
                    "message": check.message,
                }
                for check in result.checks
            ],
            "step_id": context.step_id,
            "summary": result.summary,
        }
        if model_action_event_id is not None:
            payload["model_action_event_id"] = model_action_event_id
        event_type = (
            EventType.COMPLETION_ACCEPTED
            if result.outcome is CompletionOutcome.ACCEPTED
            else EventType.COMPLETION_REJECTED
        )
        return self._append(context.run_id, event_type, payload)

    def load_verification_receipt(self, run_id: str) -> ToolReceipt:
        """Recover durable successful evidence for a run awaiting verification."""

        state = self.load_state(run_id)
        if state.status is not RunStatus.VERIFYING:
            raise InvalidTransitionError(
                f"verification recovery requires verifying, got {state.status.value}"
            )

        for event in reversed(self._event_store.load(run_id)):
            if event.event_type is not EventType.TOOL_SUCCEEDED:
                continue
            effect_id = event.payload.get("effect_id")
            if not isinstance(effect_id, str) or not effect_id:
                raise MissingVerificationEvidenceError(
                    "tool.succeeded has no durable effect identity"
                )
            receipt = self._executor.load_receipt(effect_id)
            if receipt is None or receipt.outcome is not ToolOutcome.SUCCEEDED:
                raise MissingVerificationEvidenceError(
                    "verifying run has no matching successful receipt"
                )
            return receipt

        raise MissingVerificationEvidenceError(
            "verifying run has no persisted tool.succeeded event"
        )

    def load_model_tool_recovery(self, run_id: str) -> ModelToolRecovery:
        """Reconstruct one verifying model Tool turn entirely from durable facts."""

        state = self.load_state(run_id)
        if state.status is not RunStatus.VERIFYING or state.active_step_id is None:
            raise InvalidTransitionError(
                "model tool recovery requires a verifying run with an active step"
            )
        for event in reversed(self._event_store.load(run_id)):
            if event.event_type is not EventType.TOOL_REQUESTED:
                continue
            if event.payload.get("step_id") != state.active_step_id:
                continue
            context = ModelInput.build(
                run_id=run_id,
                step_id=state.active_step_id,
                turn_index=state.turn_index,
                state_status=RunStatus.READY,
                observation=self._load_step_observation(run_id),
            )
            action = ToolCallAction(
                tool_call_id=self._payload_text(event.payload, "tool_call_id"),
                tool_name=self._payload_text(event.payload, "tool_name"),
                arguments_json=self._payload_text(event.payload, "arguments_json"),
            )
            return ModelToolRecovery(
                context=context,
                action=action,
                receipt=self.load_verification_receipt(run_id),
            )
        raise MissingVerificationEvidenceError("verifying model turn has no persisted tool request")

    def _execute(
        self,
        request: ToolRequest,
        *,
        authorization: AuthorizationDecision | None = None,
        gate_evaluation: GateEvaluation | None = None,
        gate_attempt: int | None = None,
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

        if receipt.outcome is ToolOutcome.TIMED_OUT:
            reason = receipt.output.get("message")
            if not isinstance(reason, str) or not reason:
                reason = "tool execution timed out"
            state = self._append(
                request.run_id,
                EventType.TOOL_TIMED_OUT,
                {
                    "reason": reason,
                    "timeout_seconds": receipt.output.get("timeout_seconds"),
                    "tool_call_id": request.tool_call_id,
                },
            )
        elif receipt.outcome is ToolOutcome.FAILED:
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
            gate_evaluation=gate_evaluation,
            gate_attempt=gate_attempt,
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
                model_action_event_id=event.payload.get("model_action_event_id"),
            )
        raise GateReferenceMismatchError("active gate has no matching persisted tool request")

    def _load_step_observation(self, run_id: str) -> dict[str, Any]:
        for event in reversed(self._event_store.load(run_id)):
            if event.event_type is not EventType.VERIFICATION_SUCCEEDED:
                continue
            if event.payload.get("scope") != "step":
                continue
            return {
                "verification": {
                    "checks": event.payload.get("checks", []),
                    "summary": event.payload.get("summary", ""),
                }
            }
        return {}

    def _load_active_proposal(
        self,
        request: ToolRequest,
        reference: GateReference,
    ) -> GateProposal:
        for event in reversed(self._event_store.load(request.run_id)):
            if event.event_type not in {EventType.TOOL_ESCALATED, EventType.GATE_REVISED}:
                continue
            if event.payload.get("tool_call_id") != request.tool_call_id:
                continue
            if event.payload.get("proposal_digest") != reference.proposal_digest:
                continue
            if event.payload.get("revision") != reference.revision:
                continue
            reasons = event.payload.get("reasons")
            if not isinstance(reasons, list) or not all(
                isinstance(reason, str) and reason for reason in reasons
            ):
                raise GateReferenceMismatchError("persisted gate has invalid reasons")
            return GateProposal(
                reference=reference,
                step_id=request.step_id,
                tool_name=request.tool_name,
                arguments_json=request.arguments_json,
                ownership_mode=OwnershipMode(self._payload_text(event.payload, "ownership_mode")),
                reasons=tuple(reasons),
            )
        raise GateReferenceMismatchError("active gate has no persisted proposal")

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
