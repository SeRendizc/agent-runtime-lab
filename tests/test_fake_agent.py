from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_runtime_lab.authorized_tool_runtime import AuthorizedToolRuntime
from agent_runtime_lab.domain.errors import InvalidTransitionError
from agent_runtime_lab.domain.events import EventType, ExecutionEvent
from agent_runtime_lab.domain.state import RunStatus
from agent_runtime_lab.durable_tool_executor import DurableToolExecutor
from agent_runtime_lab.fake_agent import FakeAgent, FakeAgentCheckpoint
from agent_runtime_lab.model_adapter import (
    ModelInput,
    StaticModelAdapter,
    ToolCallAction,
    request_model_action,
    tool_request_from_action,
)
from agent_runtime_lab.ownership.authorization import (
    AuthorizationContext,
    ToolRequest,
    WorkspaceBoundary,
)
from agent_runtime_lab.ownership.policy import (
    OwnershipMode,
    OwnershipPolicy,
    OwnershipRule,
)
from agent_runtime_lab.ownership.risk_evaluator import RiskEvaluator, RiskRule
from agent_runtime_lab.persistence.sqlite_store import SQLiteEventStore
from agent_runtime_lab.persistence.sqlite_tool_effect_store import SQLiteToolEffectStore
from agent_runtime_lab.restricted_file_tools import (
    RestrictedFileToolRunner,
    make_restricted_file_registry,
)
from agent_runtime_lab.verification import ReceiptVerifier, VerificationExpectation

NOW = datetime(2026, 8, 11, tzinfo=UTC)


class SimulatedCrash(RuntimeError):
    pass


class CrashAfterToolResult:
    def reach(self, checkpoint: FakeAgentCheckpoint) -> None:
        assert checkpoint is FakeAgentCheckpoint.AFTER_TOOL_RESULT
        raise SimulatedCrash("crashed after durable tool result")


def make_runtime(
    tmp_path: Path,
    workspace: Path,
) -> tuple[AuthorizedToolRuntime, SQLiteEventStore, SQLiteToolEffectStore]:
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
            risk_evaluator=RiskEvaluator(
                rules=(
                    RiskRule(
                        tag="write_operation",
                        tool_names=frozenset({"write_file"}),
                    ),
                )
            ),
            policy=OwnershipPolicy(
                rules=(
                    OwnershipRule(
                        risk_tags=frozenset({"write_operation"}),
                        minimum_mode=OwnershipMode.PAIR,
                        reason="writes require pair review",
                    ),
                )
            ),
        ),
        clock=lambda: NOW,
    )
    return runtime, events, effects


def initialize_run(events: SQLiteEventStore, run_id: str) -> None:
    for sequence, event_type in enumerate((EventType.RUN_CREATED, EventType.RUN_STARTED)):
        events.append(
            ExecutionEvent.build(
                event_id=f"{run_id}:{sequence}:{event_type.value}",
                run_id=run_id,
                sequence=sequence,
                event_type=event_type,
                occurred_at=NOW,
            )
        )


def request(
    *,
    run_id: str,
    tool_name: str = "read_file",
    arguments: dict[str, str] | None = None,
) -> ToolRequest:
    return ToolRequest.build(
        run_id=run_id,
        step_id="step-1",
        tool_call_id="call-1",
        tool_name=tool_name,
        arguments=arguments or {"path": "notes.txt"},
    )


def test_fake_agent_closes_real_read_with_verification_evidence(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    content = "hello fake agent"
    workspace.joinpath("notes.txt").write_text(content, encoding="utf-8")
    runtime, events, effects = make_runtime(tmp_path, workspace)
    initialize_run(events, "run-success")
    agent = FakeAgent(
        runtime=runtime,
        verifier=ReceiptVerifier(),
        request=request(run_id="run-success"),
    )

    result = agent.run(
        VerificationExpectation(
            path="notes.txt",
            sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        )
    )

    assert result.state.status is RunStatus.COMPLETED
    assert result.verification.passed is True
    assert [event.event_type for event in events.load("run-success")][-5:] == [
        EventType.TOOL_REQUESTED,
        EventType.TOOL_AUTHORIZED,
        EventType.TOOL_STARTED,
        EventType.TOOL_SUCCEEDED,
        EventType.VERIFICATION_SUCCEEDED,
    ]
    evidence_json = json.dumps(events.load("run-success")[-1].payload)
    assert content not in evidence_json
    assert str(workspace) not in evidence_json
    events.close()
    effects.close()


def test_static_model_action_flows_through_runtime_owned_request_identity(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    content = "adapter boundary evidence"
    workspace.joinpath("notes.txt").write_text(content, encoding="utf-8")
    runtime, events, effects = make_runtime(tmp_path, workspace)
    initialize_run(events, "run-model-action")
    context = ModelInput.build(
        run_id="run-model-action",
        step_id="step-1",
        turn_index=0,
        state_status=runtime.load_state("run-model-action").status,
    )
    adapter = StaticModelAdapter(
        actions=(
            ToolCallAction.build(
                tool_call_id="call-1",
                tool_name="read_file",
                arguments={"path": "notes.txt"},
            ),
        )
    )
    action = request_model_action(adapter, context)
    agent = FakeAgent(
        runtime=runtime,
        verifier=ReceiptVerifier(),
        request=tool_request_from_action(context, action),
    )

    result = agent.run(
        VerificationExpectation(
            path="notes.txt",
            sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        )
    )

    assert result.state.status is RunStatus.COMPLETED
    requested = events.load("run-model-action")[2]
    assert requested.event_type is EventType.TOOL_REQUESTED
    assert requested.payload["step_id"] == "step-1"
    assert requested.payload["tool_call_id"] == "call-1"
    events.close()
    effects.close()


def test_fake_agent_wrong_digest_fails_run(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    workspace.joinpath("notes.txt").write_text("hello", encoding="utf-8")
    runtime, events, effects = make_runtime(tmp_path, workspace)
    initialize_run(events, "run-failed-verification")
    agent = FakeAgent(
        runtime=runtime,
        verifier=ReceiptVerifier(),
        request=request(run_id="run-failed-verification"),
    )

    result = agent.run(VerificationExpectation(path="notes.txt", sha256="0" * 64))

    assert result.state.status is RunStatus.FAILED
    assert result.verification.passed is False
    assert events.load("run-failed-verification")[-1].event_type is EventType.VERIFICATION_FAILED
    events.close()
    effects.close()


def test_fake_agent_recovers_verification_without_rerunning_tool(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    content = "durable verification evidence"
    workspace.joinpath("notes.txt").write_text(content, encoding="utf-8")
    runtime, events, effects = make_runtime(tmp_path, workspace)
    initialize_run(events, "run-verification-recovery")
    tool_request = request(run_id="run-verification-recovery")
    crashing_agent = FakeAgent(
        runtime=runtime,
        verifier=ReceiptVerifier(),
        request=tool_request,
        failure_injector=CrashAfterToolResult(),
    )
    expectation = VerificationExpectation(
        path="notes.txt",
        sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
    )

    with pytest.raises(SimulatedCrash, match="after durable tool result"):
        crashing_agent.run(expectation)

    events_before_recovery = events.load("run-verification-recovery")
    assert runtime.load_state("run-verification-recovery").status is RunStatus.VERIFYING
    assert events_before_recovery[-1].event_type is EventType.TOOL_SUCCEEDED
    events.close()
    effects.close()

    recovered_runtime, recovered_events, recovered_effects = make_runtime(tmp_path, workspace)
    recovered_agent = FakeAgent(
        runtime=recovered_runtime,
        verifier=ReceiptVerifier(),
        request=tool_request,
    )

    result = recovered_agent.recover_verification(expectation)

    persisted_events = recovered_events.load("run-verification-recovery")
    assert result.state.status is RunStatus.COMPLETED
    assert result.verification.passed is True
    assert result.receipt.effect_id == events_before_recovery[-1].payload["effect_id"]
    assert persisted_events[-1].event_type is EventType.VERIFICATION_SUCCEEDED
    assert sum(event.event_type is EventType.TOOL_STARTED for event in persisted_events) == 1
    recovered_events.close()
    recovered_effects.close()


def test_fake_agent_recovery_rejects_non_verifying_run(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime, events, effects = make_runtime(tmp_path, workspace)
    initialize_run(events, "run-ready")
    agent = FakeAgent(
        runtime=runtime,
        verifier=ReceiptVerifier(),
        request=request(run_id="run-ready"),
    )

    with pytest.raises(
        InvalidTransitionError,
        match="verification recovery requires verifying, got ready",
    ):
        agent.recover_verification(VerificationExpectation(path="notes.txt", sha256="0" * 64))

    events.close()
    effects.close()


@pytest.mark.parametrize(
    ("run_id", "tool_name", "arguments", "expected_status"),
    [
        (
            "run-denied",
            "read_file",
            {"path": "../outside.txt"},
            RunStatus.FAILED,
        ),
        (
            "run-gated",
            "write_file",
            {"path": "output.txt", "content": "waiting"},
            RunStatus.AWAITING_GATE,
        ),
    ],
)
def test_fake_agent_cannot_claim_denied_or_gated_request_completed(
    tmp_path: Path,
    run_id: str,
    tool_name: str,
    arguments: dict[str, str],
    expected_status: RunStatus,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime, events, effects = make_runtime(tmp_path, workspace)
    initialize_run(events, run_id)
    agent = FakeAgent(
        runtime=runtime,
        verifier=ReceiptVerifier(),
        request=request(run_id=run_id, tool_name=tool_name, arguments=arguments),
    )

    with pytest.raises(InvalidTransitionError, match="requires an executed tool receipt"):
        agent.run(VerificationExpectation(path="output.txt", sha256="0" * 64))

    assert runtime.load_state(run_id).status is expected_status
    assert not any(
        event.event_type in {EventType.VERIFICATION_SUCCEEDED, EventType.VERIFICATION_FAILED}
        for event in events.load(run_id)
    )
    events.close()
    effects.close()
