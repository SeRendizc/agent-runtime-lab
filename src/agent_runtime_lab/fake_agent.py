"""A static Fake Agent for one bounded Runtime verification loop."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from agent_runtime_lab.authorized_tool_runtime import (
    AuthorizedToolRuntime,
    RuntimeToolOutcome,
    RuntimeToolResult,
)
from agent_runtime_lab.completion import (
    CompletionExpectation,
    CompletionResult,
    CompletionVerifier,
)
from agent_runtime_lab.domain.errors import InvalidTransitionError, StepBudgetExhaustedError
from agent_runtime_lab.domain.state import RunState, RunStatus
from agent_runtime_lab.domain.tool_effects import ToolReceipt
from agent_runtime_lab.model_adapter import (
    FinalAnswerAction,
    ModelAction,
    ModelAdapter,
    ModelInput,
    ToolCallAction,
    request_model_action,
    tool_request_from_action,
)
from agent_runtime_lab.ownership.authorization import ToolRequest
from agent_runtime_lab.verification import (
    ReceiptVerifier,
    VerificationExpectation,
    VerificationResult,
)


@dataclass(frozen=True, slots=True)
class FakeAgentRunResult:
    """Evidence returned after the Runtime reaches a verified terminal state."""

    tool_result: RuntimeToolResult
    verification: VerificationResult
    state: RunState


@dataclass(frozen=True, slots=True)
class FakeAgentVerificationRecoveryResult:
    """Evidence returned after resuming verification without rerunning a tool."""

    receipt: ToolReceipt
    verification: VerificationResult
    state: RunState


@dataclass(frozen=True, slots=True)
class ModelDrivenToolTurnResult:
    """Evidence for one verified, non-terminal model-proposed tool turn."""

    context: ModelInput
    action: ToolCallAction
    tool_result: RuntimeToolResult
    verification: VerificationResult
    state: RunState


@dataclass(frozen=True, slots=True)
class ModelDrivenCompletionResult:
    """Evidence for one Runtime-validated final-answer proposal."""

    context: ModelInput
    action: FinalAnswerAction
    completion: CompletionResult
    state: RunState


class ModelLoopOutcome(StrEnum):
    """Why one bounded model loop returned control to its caller."""

    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"


@dataclass(frozen=True, slots=True)
class ModelLoopResult:
    """Inspectable evidence accumulated by one bounded loop invocation."""

    outcome: ModelLoopOutcome
    state: RunState
    actions: tuple[ModelAction, ...]
    tool_results: tuple[RuntimeToolResult, ...]
    verifications: tuple[VerificationResult, ...]
    completions: tuple[CompletionResult, ...]
    recovered_receipts: tuple[ToolReceipt, ...] = ()


class ToolExpectationResolver(Protocol):
    """Return trusted verification criteria for one proposed Tool Action."""

    def expectation_for(
        self,
        context: ModelInput,
        action: ToolCallAction,
    ) -> VerificationExpectation:
        """Resolve application-owned criteria without trusting model arguments."""


class ModelLoopCheckpoint(StrEnum):
    """Crash boundary exposed by the durable model loop."""

    BEFORE_RECOVERED_VERIFICATION = "before_recovered_verification"
    AFTER_MODEL_ACTION_RETURNED = "after_model_action_returned"
    AFTER_MODEL_ACTION_PERSISTED = "after_model_action_persisted"


class ModelLoopFailureInjector(Protocol):
    """Observe a model-loop recovery checkpoint and optionally crash."""

    def reach(self, checkpoint: ModelLoopCheckpoint) -> None:
        """Handle one recovery checkpoint."""


class FakeAgentCheckpoint(StrEnum):
    """Crash boundary exposed by the bounded Fake Agent."""

    AFTER_TOOL_RESULT = "after_tool_result"


class FakeAgentFailureInjector(Protocol):
    """Observe a Fake Agent checkpoint and optionally simulate a crash."""

    def reach(self, checkpoint: FakeAgentCheckpoint) -> None:
        """Handle one checkpoint."""


class FakeAgent:
    """Submit one immutable request and defer completion to Runtime verification."""

    def __init__(
        self,
        *,
        runtime: AuthorizedToolRuntime,
        verifier: ReceiptVerifier,
        request: ToolRequest,
        failure_injector: FakeAgentFailureInjector | None = None,
    ) -> None:
        self._runtime = runtime
        self._verifier = verifier
        self._request = request
        self._failure_injector = failure_injector

    def run(self, expectation: VerificationExpectation) -> FakeAgentRunResult:
        """Execute the exact request and persist trusted verification evidence."""

        tool_result = self._runtime.submit(self._request)
        if (
            tool_result.outcome is not RuntimeToolOutcome.EXECUTED
            or tool_result.receipt is None
            or tool_result.state.status is not RunStatus.VERIFYING
        ):
            raise InvalidTransitionError("fake agent requires an executed tool receipt")

        if self._failure_injector is not None:
            self._failure_injector.reach(FakeAgentCheckpoint.AFTER_TOOL_RESULT)

        verification = self._verifier.verify(tool_result.receipt, expectation)
        state = self._runtime.record_verification(self._request.run_id, verification)
        return FakeAgentRunResult(
            tool_result=tool_result,
            verification=verification,
            state=state,
        )

    def recover_verification(
        self,
        expectation: VerificationExpectation,
    ) -> FakeAgentVerificationRecoveryResult:
        """Resume a VERIFYING run from durable evidence without rerunning its tool."""

        receipt = self._runtime.load_verification_receipt(self._request.run_id)
        verification = self._verifier.verify(receipt, expectation)
        state = self._runtime.record_verification(self._request.run_id, verification)
        return FakeAgentVerificationRecoveryResult(
            receipt=receipt,
            verification=verification,
            state=state,
        )


class ModelDrivenFakeAgent:
    """Execute one durable tool turn selected by an untrusted Model Adapter."""

    def __init__(
        self,
        *,
        runtime: AuthorizedToolRuntime,
        verifier: ReceiptVerifier,
        adapter: ModelAdapter,
        run_id: str,
        completion_verifier: CompletionVerifier | None = None,
        loop_failure_injector: ModelLoopFailureInjector | None = None,
    ) -> None:
        self._runtime = runtime
        self._verifier = verifier
        self._adapter = adapter
        self._run_id = run_id
        self._completion_verifier = completion_verifier or CompletionVerifier()
        self._loop_failure_injector = loop_failure_injector

    def run_tool_turn(
        self,
        expectation: VerificationExpectation,
    ) -> ModelDrivenToolTurnResult:
        """Run and verify exactly one tool Action, then return to READY."""

        context = self._runtime.build_model_input(self._run_id)
        proposed: ModelAction = request_model_action(self._adapter, context)
        if not isinstance(proposed, ToolCallAction):
            raise InvalidTransitionError("model-driven tool turn requires a tool-call action")
        request = tool_request_from_action(context, proposed)
        tool_result = self._runtime.submit(request)
        if (
            tool_result.outcome is not RuntimeToolOutcome.EXECUTED
            or tool_result.receipt is None
            or tool_result.state.status is not RunStatus.VERIFYING
        ):
            raise InvalidTransitionError("model-driven tool turn requires an executed tool receipt")
        verification = self._verifier.verify(tool_result.receipt, expectation)
        state = self._runtime.record_step_verification(self._run_id, verification)
        return ModelDrivenToolTurnResult(
            context=context,
            action=proposed,
            tool_result=tool_result,
            verification=verification,
            state=state,
        )

    def run_completion_turn(
        self,
        expectation: CompletionExpectation,
    ) -> ModelDrivenCompletionResult:
        """Validate exactly one final-answer Action through the trusted Runtime."""

        context = self._runtime.build_model_input(self._run_id)
        proposed = request_model_action(self._adapter, context)
        if not isinstance(proposed, FinalAnswerAction):
            raise InvalidTransitionError(
                "model-driven completion turn requires a final-answer action"
            )
        completion = self._completion_verifier.verify(proposed, context, expectation)
        state = self._runtime.record_completion(context, proposed, completion)
        return ModelDrivenCompletionResult(
            context=context,
            action=proposed,
            completion=completion,
            state=state,
        )

    def run_loop(
        self,
        *,
        tool_expectations: ToolExpectationResolver,
        completion_expectation: CompletionExpectation,
    ) -> ModelLoopResult:
        """Run bounded model Actions until terminal state or durable Gate pause."""

        initial = self._runtime.load_state(self._run_id)
        if initial.status not in {
            RunStatus.READY,
            RunStatus.MODEL_PENDING,
            RunStatus.ACTION_PENDING,
        }:
            raise InvalidTransitionError(
                "model loop requires ready, model_pending, or action_pending, "
                f"got {initial.status.value}"
            )
        if initial.max_steps is None:
            raise InvalidTransitionError(
                "bounded model loop requires max_steps persisted at run creation"
            )

        actions: list[ModelAction] = []
        tool_results: list[RuntimeToolResult] = []
        verifications: list[VerificationResult] = []
        completions: list[CompletionResult] = []
        return self._continue_loop(
            tool_expectations=tool_expectations,
            completion_expectation=completion_expectation,
            actions=actions,
            tool_results=tool_results,
            verifications=verifications,
            completions=completions,
            recovered_receipts=[],
        )

    def resume_loop(
        self,
        *,
        tool_expectations: ToolExpectationResolver,
        completion_expectation: CompletionExpectation,
    ) -> ModelLoopResult:
        """Resume after an approved Gate effect without recalling the Adapter."""

        recovery = self._runtime.load_model_tool_recovery(self._run_id)
        if self._loop_failure_injector is not None:
            self._loop_failure_injector.reach(ModelLoopCheckpoint.BEFORE_RECOVERED_VERIFICATION)
        expectation = tool_expectations.expectation_for(
            recovery.context,
            recovery.action,
        )
        if not isinstance(expectation, VerificationExpectation):
            raise TypeError("tool expectation resolver must return VerificationExpectation")
        verification = self._verifier.verify(recovery.receipt, expectation)
        state = self._runtime.record_step_verification(
            self._run_id,
            verification,
        )
        actions: list[ModelAction] = [recovery.action]
        verifications = [verification]
        recovered_receipts = [recovery.receipt]
        if state.status is RunStatus.FAILED:
            return self._loop_result(
                ModelLoopOutcome.FAILED,
                actions,
                [],
                verifications,
                [],
                recovered_receipts,
            )
        return self._continue_loop(
            tool_expectations=tool_expectations,
            completion_expectation=completion_expectation,
            actions=actions,
            tool_results=[],
            verifications=verifications,
            completions=[],
            recovered_receipts=recovered_receipts,
        )

    def _continue_loop(
        self,
        *,
        tool_expectations: ToolExpectationResolver,
        completion_expectation: CompletionExpectation,
        actions: list[ModelAction],
        tool_results: list[RuntimeToolResult],
        verifications: list[VerificationResult],
        completions: list[CompletionResult],
        recovered_receipts: list[ToolReceipt],
    ) -> ModelLoopResult:
        """Continue only from READY using replayed turn and budget facts."""

        while True:
            current = self._runtime.load_state(self._run_id)
            if current.status is RunStatus.MODEL_PENDING:
                self._runtime.record_model_action_failure(
                    self._run_id,
                    "model adapter outcome is unknown after interruption",
                )
                return self._loop_result(
                    ModelLoopOutcome.FAILED,
                    actions,
                    tool_results,
                    verifications,
                    completions,
                    recovered_receipts,
                )
            if current.status is RunStatus.ACTION_PENDING:
                pending = self._runtime.load_pending_model_action(self._run_id)
                context = pending.context
                proposed = pending.action
                model_action_event_id = pending.event_id
            else:
                try:
                    context = self._runtime.build_model_input(self._run_id)
                except StepBudgetExhaustedError:
                    return self._loop_result(
                        ModelLoopOutcome.FAILED,
                        actions,
                        tool_results,
                        verifications,
                        completions,
                        recovered_receipts,
                    )
                invocation_id = self._runtime.begin_model_action(context)
                try:
                    proposed = request_model_action(self._adapter, context)
                except Exception:
                    self._runtime.record_model_action_failure(
                        self._run_id,
                        "model adapter failed to produce a valid bounded action",
                    )
                    return self._loop_result(
                        ModelLoopOutcome.FAILED,
                        actions,
                        tool_results,
                        verifications,
                        completions,
                        recovered_receipts,
                    )
                if self._loop_failure_injector is not None:
                    self._loop_failure_injector.reach(
                        ModelLoopCheckpoint.AFTER_MODEL_ACTION_RETURNED
                    )
                pending = self._runtime.persist_model_action(
                    context,
                    invocation_id,
                    proposed,
                )
                model_action_event_id = pending.event_id
                if self._loop_failure_injector is not None:
                    self._loop_failure_injector.reach(
                        ModelLoopCheckpoint.AFTER_MODEL_ACTION_PERSISTED
                    )
            actions.append(proposed)

            if isinstance(proposed, FinalAnswerAction):
                completion = self._completion_verifier.verify(
                    proposed,
                    context,
                    completion_expectation,
                )
                completions.append(completion)
                state = self._runtime.record_completion(context, proposed, completion)
                if state.status is RunStatus.COMPLETED:
                    return self._loop_result(
                        ModelLoopOutcome.COMPLETED,
                        actions,
                        tool_results,
                        verifications,
                        completions,
                        recovered_receipts,
                    )
                continue

            tool_result = self._runtime.submit(
                tool_request_from_action(
                    context,
                    proposed,
                    model_action_event_id=model_action_event_id,
                )
            )
            tool_results.append(tool_result)
            if tool_result.outcome is not RuntimeToolOutcome.EXECUTED:
                outcome = (
                    ModelLoopOutcome.PAUSED
                    if tool_result.outcome is RuntimeToolOutcome.AWAITING_GATE
                    else ModelLoopOutcome.FAILED
                )
                return self._loop_result(
                    outcome,
                    actions,
                    tool_results,
                    verifications,
                    completions,
                    recovered_receipts,
                )
            if tool_result.receipt is None or tool_result.state.status is not RunStatus.VERIFYING:
                return self._loop_result(
                    ModelLoopOutcome.FAILED,
                    actions,
                    tool_results,
                    verifications,
                    completions,
                    recovered_receipts,
                )

            expectation = tool_expectations.expectation_for(context, proposed)
            if not isinstance(expectation, VerificationExpectation):
                raise TypeError("tool expectation resolver must return VerificationExpectation")
            verification = self._verifier.verify(tool_result.receipt, expectation)
            verifications.append(verification)
            state = self._runtime.record_step_verification(
                self._run_id,
                verification,
            )
            if state.status is RunStatus.FAILED:
                return self._loop_result(
                    ModelLoopOutcome.FAILED,
                    actions,
                    tool_results,
                    verifications,
                    completions,
                    recovered_receipts,
                )

    def _loop_result(
        self,
        outcome: ModelLoopOutcome,
        actions: list[ModelAction],
        tool_results: list[RuntimeToolResult],
        verifications: list[VerificationResult],
        completions: list[CompletionResult],
        recovered_receipts: list[ToolReceipt] | None = None,
    ) -> ModelLoopResult:
        return ModelLoopResult(
            outcome=outcome,
            state=self._runtime.load_state(self._run_id),
            actions=tuple(actions),
            tool_results=tuple(tool_results),
            verifications=tuple(verifications),
            completions=tuple(completions),
            recovered_receipts=tuple(recovered_receipts or ()),
        )
