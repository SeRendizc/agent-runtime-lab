"""Run one real restricted Tool turn and export a sanitized Trace v1 summary."""

from __future__ import annotations

import hashlib
import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from agent_runtime_lab.authorized_tool_runtime import AuthorizedToolRuntime
from agent_runtime_lab.completion import CompletionExpectation
from agent_runtime_lab.domain.events import EventType, ExecutionEvent
from agent_runtime_lab.durable_tool_executor import DurableToolExecutor
from agent_runtime_lab.fake_agent import ModelDrivenFakeAgent, ModelLoopOutcome
from agent_runtime_lab.model_adapter import (
    FinalAnswerAction,
    ModelInput,
    StaticModelAdapter,
    ToolCallAction,
)
from agent_runtime_lab.ownership.authorization import AuthorizationContext, WorkspaceBoundary
from agent_runtime_lab.ownership.policy import OwnershipPolicy
from agent_runtime_lab.ownership.risk_evaluator import RiskEvaluator
from agent_runtime_lab.persistence.sqlite_store import SQLiteEventStore
from agent_runtime_lab.persistence.sqlite_tool_effect_store import SQLiteToolEffectStore
from agent_runtime_lab.restricted_file_tools import (
    RestrictedFileToolRunner,
    make_restricted_file_registry,
)
from agent_runtime_lab.trace import build_run_trace
from agent_runtime_lab.verification import ReceiptVerifier, VerificationExpectation

NOW = datetime(2026, 8, 15, tzinfo=UTC)


class DemoExpectationResolver:
    def __init__(self, expected_sha256: str) -> None:
        self._expected_sha256 = expected_sha256

    def expectation_for(
        self,
        context: ModelInput,
        action: ToolCallAction,
    ) -> VerificationExpectation:
        del context
        path = action.arguments.get("path")
        if path != "input.txt":
            raise ValueError("demo adapter proposed an unexpected path")
        return VerificationExpectation(path=path, sha256=self._expected_sha256)


def run_demo() -> dict[str, object]:
    """Return deterministic evidence without exposing paths, content, or answers."""

    run_id = "r4l-trace-demo"
    content = "trace demo private content"
    expected_answer = "trace demo complete"
    with tempfile.TemporaryDirectory(prefix="agent-runtime-r4l-") as directory:
        root = Path(directory)
        workspace = root / "workspace"
        workspace.mkdir()
        workspace.joinpath("input.txt").write_text(content, encoding="utf-8")
        expected_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()

        registry = make_restricted_file_registry()
        boundary = WorkspaceBoundary(workspace)
        events = SQLiteEventStore(root / "runtime.db")
        effects = SQLiteToolEffectStore(root / "runtime.db")
        runtime = AuthorizedToolRuntime(
            event_store=events,
            executor=DurableToolExecutor(
                store=effects,
                runner=RestrictedFileToolRunner(boundary),
                registry=registry,
            ),
            authorization_context=AuthorizationContext(
                registry=registry,
                workspace=boundary,
                risk_evaluator=RiskEvaluator(rules=()),
                policy=OwnershipPolicy(),
            ),
            clock=lambda: NOW,
        )
        events.append(
            ExecutionEvent.build(
                event_id=f"{run_id}:0:created",
                run_id=run_id,
                sequence=0,
                event_type=EventType.RUN_CREATED,
                occurred_at=NOW,
                payload={"max_steps": 2},
            )
        )
        events.append(
            ExecutionEvent.build(
                event_id=f"{run_id}:1:started",
                run_id=run_id,
                sequence=1,
                event_type=EventType.RUN_STARTED,
                occurred_at=NOW,
            )
        )
        agent = ModelDrivenFakeAgent(
            runtime=runtime,
            verifier=ReceiptVerifier(),
            adapter=StaticModelAdapter(
                actions=(
                    ToolCallAction.build(
                        tool_call_id="call-read",
                        tool_name="read_file",
                        arguments={"path": "input.txt"},
                    ),
                    FinalAnswerAction(answer=expected_answer),
                )
            ),
            run_id=run_id,
        )
        result = agent.run_loop(
            tool_expectations=DemoExpectationResolver(expected_sha256),
            completion_expectation=CompletionExpectation(expected_answer=expected_answer),
        )
        if result.outcome is not ModelLoopOutcome.COMPLETED:
            raise RuntimeError("R4l trace demo did not complete")

        trace = build_run_trace(run_id, events.load(run_id))
        summary: dict[str, object] = {
            "event_types": [record.event_type for record in trace.records],
            "metrics": trace.metrics.as_dict(),
            "run_id": trace.run_id,
            "schema_version": trace.schema_version,
            "final_status": trace.final_status,
            "trace_digest": trace.digest(),
        }
        events.close()
        effects.close()
        return summary


def main() -> None:
    print(json.dumps(run_demo(), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
