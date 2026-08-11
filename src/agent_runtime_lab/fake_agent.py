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
from agent_runtime_lab.domain.errors import InvalidTransitionError
from agent_runtime_lab.domain.state import RunState, RunStatus
from agent_runtime_lab.domain.tool_effects import ToolReceipt
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
