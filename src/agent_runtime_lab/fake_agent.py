"""A static Fake Agent for one bounded Runtime verification loop."""

from __future__ import annotations

from dataclasses import dataclass

from agent_runtime_lab.authorized_tool_runtime import (
    AuthorizedToolRuntime,
    RuntimeToolOutcome,
    RuntimeToolResult,
)
from agent_runtime_lab.domain.errors import InvalidTransitionError
from agent_runtime_lab.domain.state import RunState, RunStatus
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


class FakeAgent:
    """Submit one immutable request and defer completion to Runtime verification."""

    def __init__(
        self,
        *,
        runtime: AuthorizedToolRuntime,
        verifier: ReceiptVerifier,
        request: ToolRequest,
    ) -> None:
        self._runtime = runtime
        self._verifier = verifier
        self._request = request

    def run(self, expectation: VerificationExpectation) -> FakeAgentRunResult:
        """Execute the exact request and persist trusted verification evidence."""

        tool_result = self._runtime.submit(self._request)
        if (
            tool_result.outcome is not RuntimeToolOutcome.EXECUTED
            or tool_result.receipt is None
            or tool_result.state.status is not RunStatus.VERIFYING
        ):
            raise InvalidTransitionError("fake agent requires an executed tool receipt")

        verification = self._verifier.verify(tool_result.receipt, expectation)
        state = self._runtime.record_verification(self._request.run_id, verification)
        return FakeAgentRunResult(
            tool_result=tool_result,
            verification=verification,
            state=state,
        )
