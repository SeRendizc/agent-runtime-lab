from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_runtime_lab.authorized_tool_runtime import AuthorizedToolRuntime
from agent_runtime_lab.domain.events import EventType, ExecutionEvent
from agent_runtime_lab.domain.state import RunStatus
from agent_runtime_lab.durable_tool_executor import DurableToolExecutor
from agent_runtime_lab.ownership.authorization import (
    AuthorizationContext,
    ToolRequest,
    WorkspaceBoundary,
)
from agent_runtime_lab.ownership.policy import OwnershipPolicy
from agent_runtime_lab.ownership.risk_evaluator import RiskEvaluator
from agent_runtime_lab.persistence.sqlite_store import SQLiteEventStore
from agent_runtime_lab.persistence.sqlite_tool_effect_store import SQLiteToolEffectStore
from agent_runtime_lab.restricted_file_tools import (
    RestrictedFileToolRunner,
    make_restricted_file_registry,
)
from agent_runtime_lab.verification import (
    VerificationCheck,
    VerificationOutcome,
    VerificationResult,
)

NOW = datetime(2026, 8, 11, tzinfo=UTC)


@pytest.fixture
def verifying_runtime(
    tmp_path: Path,
) -> tuple[AuthorizedToolRuntime, SQLiteEventStore, SQLiteToolEffectStore]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    workspace.joinpath("notes.txt").write_text("hello", encoding="utf-8")
    registry = make_restricted_file_registry()
    boundary = WorkspaceBoundary(workspace)
    events = SQLiteEventStore(tmp_path / "runtime.db")
    effects = SQLiteToolEffectStore(tmp_path / "runtime.db")
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
            policy=OwnershipPolicy(rules=()),
        ),
        clock=lambda: NOW,
    )
    for sequence, event_type in enumerate((EventType.RUN_CREATED, EventType.RUN_STARTED)):
        events.append(
            ExecutionEvent.build(
                event_id=f"run-verify:{sequence}:{event_type.value}",
                run_id="run-verify",
                sequence=sequence,
                event_type=event_type,
                occurred_at=NOW,
            )
        )
    runtime.submit(
        ToolRequest.build(
            run_id="run-verify",
            step_id="step-1",
            tool_call_id="call-1",
            tool_name="read_file",
            arguments={"path": "notes.txt"},
        )
    )
    assert runtime.load_state("run-verify").status is RunStatus.VERIFYING
    yield runtime, events, effects
    events.close()
    effects.close()


def test_passing_verification_is_persisted_and_completes_run(
    verifying_runtime: tuple[AuthorizedToolRuntime, SQLiteEventStore, SQLiteToolEffectStore],
) -> None:
    runtime, events, _ = verifying_runtime
    result = VerificationResult(
        outcome=VerificationOutcome.PASSED,
        checks=(VerificationCheck("receipt_succeeded", True, "receipt succeeded"),),
        summary="all verification checks passed",
    )

    state = runtime.record_verification("run-verify", result)

    assert state.status is RunStatus.COMPLETED
    persisted = events.load("run-verify")[-1]
    assert persisted.event_type is EventType.VERIFICATION_SUCCEEDED
    assert persisted.payload == {
        "checks": [
            {
                "message": "receipt succeeded",
                "name": "receipt_succeeded",
                "passed": True,
            }
        ],
        "summary": "all verification checks passed",
    }


def test_failing_verification_is_persisted_and_fails_run(
    verifying_runtime: tuple[AuthorizedToolRuntime, SQLiteEventStore, SQLiteToolEffectStore],
) -> None:
    runtime, events, _ = verifying_runtime
    result = VerificationResult(
        outcome=VerificationOutcome.FAILED,
        checks=(VerificationCheck("sha256_matches", False, "sha256 mismatch"),),
        summary="1 verification checks failed",
    )

    state = runtime.record_verification("run-verify", result)

    assert state.status is RunStatus.FAILED
    assert state.failure_reason == result.summary
    persisted = events.load("run-verify")[-1]
    assert persisted.event_type is EventType.VERIFICATION_FAILED
    assert persisted.payload["reason"] == result.summary
