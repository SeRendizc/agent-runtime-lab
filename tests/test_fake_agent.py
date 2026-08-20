from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_runtime_lab.authorized_tool_runtime import (
    AuthorizedToolRuntime,
    RuntimeToolOutcome,
    SnapshotCheckpoint,
    SnapshotFailureInjector,
)
from agent_runtime_lab.completion import CompletionExpectation, CompletionOutcome
from agent_runtime_lab.domain.errors import InvalidTransitionError, StepBudgetExhaustedError
from agent_runtime_lab.domain.events import EventType, ExecutionEvent
from agent_runtime_lab.domain.state import RunStatus
from agent_runtime_lab.durable_tool_executor import DurableToolExecutor
from agent_runtime_lab.fake_agent import (
    FakeAgent,
    FakeAgentCheckpoint,
    ModelDrivenFakeAgent,
    ModelLoopCheckpoint,
    ModelLoopOutcome,
)
from agent_runtime_lab.model_adapter import (
    FinalAnswerAction,
    ModelAction,
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
from agent_runtime_lab.ownership.gates import (
    GateAnswerSubmission,
    GateResolution,
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


class CrashBeforeRecoveredVerification:
    def reach(self, checkpoint: ModelLoopCheckpoint) -> None:
        assert checkpoint is ModelLoopCheckpoint.BEFORE_RECOVERED_VERIFICATION
        raise SimulatedCrash("crashed before recovered verification")


class CrashBeforeSnapshotTail:
    def reach(self, checkpoint: SnapshotCheckpoint) -> None:
        assert checkpoint is SnapshotCheckpoint.BEFORE_TAIL_REPLAY
        raise SimulatedCrash("crashed before snapshot tail replay")


class CrashAfterModelActionReturned:
    def reach(self, checkpoint: ModelLoopCheckpoint) -> None:
        assert checkpoint is ModelLoopCheckpoint.AFTER_MODEL_ACTION_RETURNED
        raise SimulatedCrash("crashed after model action returned")


class CrashAfterModelActionPersisted:
    def reach(self, checkpoint: ModelLoopCheckpoint) -> None:
        if checkpoint is ModelLoopCheckpoint.AFTER_MODEL_ACTION_RETURNED:
            return
        assert checkpoint is ModelLoopCheckpoint.AFTER_MODEL_ACTION_PERSISTED
        raise SimulatedCrash("crashed after model action persisted")


class PathExpectationResolver:
    def __init__(self, digests: dict[str, str]) -> None:
        self._digests = digests

    def expectation_for(
        self,
        context: ModelInput,
        action: ToolCallAction,
    ) -> VerificationExpectation:
        del context
        path = action.arguments["path"]
        assert isinstance(path, str)
        return VerificationExpectation(path=path, sha256=self._digests[path])


def make_runtime(
    tmp_path: Path,
    workspace: Path,
    *,
    write_mode: OwnershipMode = OwnershipMode.PAIR,
    enable_snapshots: bool = False,
    snapshot_failure_injector: SnapshotFailureInjector | None = None,
) -> tuple[AuthorizedToolRuntime, SQLiteEventStore, SQLiteToolEffectStore]:
    registry = make_restricted_file_registry()
    boundary = WorkspaceBoundary(workspace)
    events = SQLiteEventStore(tmp_path / "runtime.db")
    effects = SQLiteToolEffectStore(tmp_path / "runtime.db")
    runtime = AuthorizedToolRuntime(
        event_store=events,
        snapshot_store=events if enable_snapshots else None,
        snapshot_failure_injector=snapshot_failure_injector,
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
                        minimum_mode=write_mode,
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


def initialize_budgeted_run(
    events: SQLiteEventStore,
    run_id: str,
    *,
    max_steps: int,
) -> None:
    events.append(
        ExecutionEvent.build(
            event_id=f"{run_id}:0:{EventType.RUN_CREATED.value}",
            run_id=run_id,
            sequence=0,
            event_type=EventType.RUN_CREATED,
            occurred_at=NOW,
            payload={"max_steps": max_steps},
        )
    )
    events.append(
        ExecutionEvent.build(
            event_id=f"{run_id}:1:{EventType.RUN_STARTED.value}",
            run_id=run_id,
            sequence=1,
            event_type=EventType.RUN_STARTED,
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


def test_model_driven_agent_advances_two_durable_turns_across_restart(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    first_content = "first durable turn"
    second_content = "second durable turn"
    workspace.joinpath("first.txt").write_text(first_content, encoding="utf-8")
    workspace.joinpath("second.txt").write_text(second_content, encoding="utf-8")
    adapter = StaticModelAdapter(
        actions=(
            ToolCallAction.build(
                tool_call_id="call-1",
                tool_name="read_file",
                arguments={"path": "first.txt"},
            ),
            ToolCallAction.build(
                tool_call_id="call-2",
                tool_name="read_file",
                arguments={"path": "second.txt"},
            ),
        )
    )
    runtime, events, effects = make_runtime(tmp_path, workspace)
    initialize_run(events, "run-two-turns")
    first_agent = ModelDrivenFakeAgent(
        runtime=runtime,
        verifier=ReceiptVerifier(),
        adapter=adapter,
        run_id="run-two-turns",
    )

    first = first_agent.run_tool_turn(
        VerificationExpectation(
            path="first.txt",
            sha256=hashlib.sha256(first_content.encode("utf-8")).hexdigest(),
        )
    )

    assert first.context.turn_index == 0
    assert first.context.step_id == "step-1"
    assert first.state.status is RunStatus.READY
    assert first.state.turn_index == 1
    events.close()
    effects.close()

    recovered_runtime, recovered_events, recovered_effects = make_runtime(tmp_path, workspace)
    second_agent = ModelDrivenFakeAgent(
        runtime=recovered_runtime,
        verifier=ReceiptVerifier(),
        adapter=StaticModelAdapter(actions=adapter.actions),
        run_id="run-two-turns",
    )

    second = second_agent.run_tool_turn(
        VerificationExpectation(
            path="second.txt",
            sha256=hashlib.sha256(second_content.encode("utf-8")).hexdigest(),
        )
    )

    assert second.context.turn_index == 1
    assert second.context.step_id == "step-2"
    assert second.context.observation["verification"]["summary"] == (
        "all verification checks passed"
    )
    assert second.action.tool_call_id == "call-2"
    assert second.state.status is RunStatus.READY
    assert second.state.turn_index == 2
    requested = [
        item
        for item in recovered_events.load("run-two-turns")
        if item.event_type is EventType.TOOL_REQUESTED
    ]
    assert [item.payload["step_id"] for item in requested] == ["step-1", "step-2"]
    assert [item.payload["tool_call_id"] for item in requested] == ["call-1", "call-2"]
    recovered_events.close()
    recovered_effects.close()


def test_model_step_budget_survives_restart_and_blocks_adapter_and_tool(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    content = "one allowed model turn"
    workspace.joinpath("notes.txt").write_text(content, encoding="utf-8")
    runtime, events, effects = make_runtime(tmp_path, workspace)
    initialize_budgeted_run(events, "run-budget", max_steps=1)
    first_agent = ModelDrivenFakeAgent(
        runtime=runtime,
        verifier=ReceiptVerifier(),
        adapter=StaticModelAdapter(
            actions=(
                ToolCallAction.build(
                    tool_call_id="call-1",
                    tool_name="read_file",
                    arguments={"path": "notes.txt"},
                ),
            )
        ),
        run_id="run-budget",
    )

    first = first_agent.run_tool_turn(
        VerificationExpectation(
            path="notes.txt",
            sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        )
    )

    assert first.state.status is RunStatus.READY
    assert first.state.turn_index == 1
    assert first.state.max_steps == 1
    events.close()
    effects.close()

    class ForbiddenAdapter:
        def next_action(self, context: ModelInput) -> ModelAction:
            raise AssertionError("adapter must not be called after budget exhaustion")

    recovered_runtime, recovered_events, recovered_effects = make_runtime(tmp_path, workspace)
    recovered_agent = ModelDrivenFakeAgent(
        runtime=recovered_runtime,
        verifier=ReceiptVerifier(),
        adapter=ForbiddenAdapter(),
        run_id="run-budget",
    )

    with pytest.raises(StepBudgetExhaustedError, match="1/1"):
        recovered_agent.run_tool_turn(VerificationExpectation(path="notes.txt", sha256="0" * 64))

    recovered = recovered_runtime.load_state("run-budget")
    persisted = recovered_events.load("run-budget")
    assert recovered.status is RunStatus.FAILED
    assert recovered.turn_index == 1
    assert recovered.max_steps == 1
    assert persisted[-1].event_type is EventType.RUN_STEP_BUDGET_EXHAUSTED
    assert persisted[-1].payload == {"completed_steps": 1, "max_steps": 1}
    assert sum(item.event_type is EventType.TOOL_REQUESTED for item in persisted) == 1
    recovered_events.close()
    recovered_effects.close()


def test_model_driven_tool_turn_rejects_final_answer_without_completion_contract(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime, events, effects = make_runtime(tmp_path, workspace)
    initialize_run(events, "run-final-answer")
    agent = ModelDrivenFakeAgent(
        runtime=runtime,
        verifier=ReceiptVerifier(),
        adapter=StaticModelAdapter(
            actions=(FinalAnswerAction(answer="unverified completion claim"),)
        ),
        run_id="run-final-answer",
    )

    with pytest.raises(InvalidTransitionError, match="tool-call action"):
        agent.run_tool_turn(VerificationExpectation(path="notes.txt", sha256="0" * 64))

    assert runtime.load_state("run-final-answer").status is RunStatus.READY
    assert len(events.load("run-final-answer")) == 2
    events.close()
    effects.close()


def test_model_completion_requires_trusted_verification_and_survives_restart(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    content = "durable completion evidence"
    workspace.joinpath("notes.txt").write_text(content, encoding="utf-8")
    runtime, events, effects = make_runtime(tmp_path, workspace)
    initialize_budgeted_run(events, "run-completion", max_steps=2)
    actions: tuple[ModelAction, ...] = (
        ToolCallAction.build(
            tool_call_id="call-1",
            tool_name="read_file",
            arguments={"path": "notes.txt"},
        ),
        FinalAnswerAction(answer="done"),
    )
    first_agent = ModelDrivenFakeAgent(
        runtime=runtime,
        verifier=ReceiptVerifier(),
        adapter=StaticModelAdapter(actions=actions),
        run_id="run-completion",
    )

    tool_turn = first_agent.run_tool_turn(
        VerificationExpectation(
            path="notes.txt",
            sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        )
    )
    assert tool_turn.state.status is RunStatus.READY
    assert tool_turn.state.turn_index == 1
    events.close()
    effects.close()

    recovered_runtime, recovered_events, recovered_effects = make_runtime(tmp_path, workspace)
    recovered_agent = ModelDrivenFakeAgent(
        runtime=recovered_runtime,
        verifier=ReceiptVerifier(),
        adapter=StaticModelAdapter(actions=actions),
        run_id="run-completion",
    )

    completed = recovered_agent.run_completion_turn(CompletionExpectation(expected_answer="done"))

    assert completed.completion.outcome is CompletionOutcome.ACCEPTED
    assert completed.state.status is RunStatus.COMPLETED
    assert completed.state.turn_index == 2
    persisted = recovered_events.load("run-completion")
    assert persisted[-1].event_type is EventType.COMPLETION_ACCEPTED
    assert persisted[-1].payload["answer"] == "done"
    assert persisted[-1].payload["step_id"] == "step-2"
    recovered_events.close()
    recovered_effects.close()


def test_rejected_completion_consumes_budget_before_adapter_can_run_again(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime, events, effects = make_runtime(tmp_path, workspace)
    initialize_budgeted_run(events, "run-rejected-completion", max_steps=1)
    agent = ModelDrivenFakeAgent(
        runtime=runtime,
        verifier=ReceiptVerifier(),
        adapter=StaticModelAdapter(actions=(FinalAnswerAction(answer="unsupported claim"),)),
        run_id="run-rejected-completion",
    )

    rejected = agent.run_completion_turn(CompletionExpectation(expected_answer="done"))

    assert rejected.completion.outcome is CompletionOutcome.REJECTED
    assert rejected.state.status is RunStatus.READY
    assert rejected.state.turn_index == 1
    assert events.load("run-rejected-completion")[-1].event_type is EventType.COMPLETION_REJECTED

    with pytest.raises(StepBudgetExhaustedError, match="1/1"):
        agent.run_completion_turn(CompletionExpectation(expected_answer="done"))

    assert runtime.load_state("run-rejected-completion").status is RunStatus.FAILED
    assert (
        events.load("run-rejected-completion")[-1].event_type is EventType.RUN_STEP_BUDGET_EXHAUSTED
    )
    events.close()
    effects.close()


def test_bounded_loop_runs_tool_rejects_completion_then_completes(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    content = "bounded loop evidence"
    workspace.joinpath("notes.txt").write_text(content, encoding="utf-8")
    runtime, events, effects = make_runtime(tmp_path, workspace)
    initialize_budgeted_run(events, "run-loop", max_steps=3)
    agent = ModelDrivenFakeAgent(
        runtime=runtime,
        verifier=ReceiptVerifier(),
        adapter=StaticModelAdapter(
            actions=(
                ToolCallAction.build(
                    tool_call_id="call-1",
                    tool_name="read_file",
                    arguments={"path": "notes.txt"},
                ),
                FinalAnswerAction(answer="not done"),
                FinalAnswerAction(answer="done"),
            )
        ),
        run_id="run-loop",
    )

    result = agent.run_loop(
        tool_expectations=PathExpectationResolver(
            {"notes.txt": hashlib.sha256(content.encode("utf-8")).hexdigest()}
        ),
        completion_expectation=CompletionExpectation(expected_answer="done"),
    )

    assert result.outcome is ModelLoopOutcome.COMPLETED
    assert result.state.status is RunStatus.COMPLETED
    assert result.state.turn_index == 3
    assert len(result.actions) == 3
    assert len(result.tool_results) == 1
    assert len(result.verifications) == 1
    assert [item.outcome for item in result.completions] == [
        CompletionOutcome.REJECTED,
        CompletionOutcome.ACCEPTED,
    ]
    event_types = [item.event_type for item in events.load("run-loop")]
    assert event_types[-6:] == [
        EventType.MODEL_ACTION_REQUESTED,
        EventType.MODEL_ACTION_PROPOSED,
        EventType.COMPLETION_REJECTED,
        EventType.MODEL_ACTION_REQUESTED,
        EventType.MODEL_ACTION_PROPOSED,
        EventType.COMPLETION_ACCEPTED,
    ]
    events.close()
    effects.close()


def test_model_action_unknown_window_fails_without_recalling_adapter(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    class OneCallAdapter:
        calls = 0

        def next_action(self, context: ModelInput) -> ModelAction:
            self.calls += 1
            assert context.turn_index == 0
            return FinalAnswerAction(answer="done")

    adapter = OneCallAdapter()
    runtime, events, effects = make_runtime(tmp_path, workspace)
    initialize_budgeted_run(events, "run-action-unknown", max_steps=1)
    crashing_agent = ModelDrivenFakeAgent(
        runtime=runtime,
        verifier=ReceiptVerifier(),
        adapter=adapter,
        run_id="run-action-unknown",
        loop_failure_injector=CrashAfterModelActionReturned(),
    )

    with pytest.raises(SimulatedCrash, match="action returned"):
        crashing_agent.run_loop(
            tool_expectations=PathExpectationResolver({}),
            completion_expectation=CompletionExpectation(
                expected_answer="done",
                require_verified_observation=False,
            ),
        )

    assert adapter.calls == 1
    assert runtime.load_state("run-action-unknown").status is RunStatus.MODEL_PENDING
    assert [event.event_type for event in events.load("run-action-unknown")][-1] is (
        EventType.MODEL_ACTION_REQUESTED
    )
    events.close()
    effects.close()

    class ForbiddenAdapter:
        def next_action(self, context: ModelInput) -> ModelAction:
            del context
            raise AssertionError("unknown Adapter outcome must not be retried")

    recovered_runtime, recovered_events, recovered_effects = make_runtime(tmp_path, workspace)
    recovered_agent = ModelDrivenFakeAgent(
        runtime=recovered_runtime,
        verifier=ReceiptVerifier(),
        adapter=ForbiddenAdapter(),
        run_id="run-action-unknown",
    )
    result = recovered_agent.run_loop(
        tool_expectations=PathExpectationResolver({}),
        completion_expectation=CompletionExpectation(
            expected_answer="done",
            require_verified_observation=False,
        ),
    )

    assert result.outcome is ModelLoopOutcome.FAILED
    assert result.state.status is RunStatus.FAILED
    assert result.state.failure_reason == "model adapter outcome is unknown after interruption"
    assert recovered_events.load("run-action-unknown")[-1].event_type is (
        EventType.MODEL_ACTION_FAILED
    )
    recovered_events.close()
    recovered_effects.close()


def test_persisted_model_action_resumes_without_recalling_adapter(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    class OneCallAdapter:
        calls = 0

        def next_action(self, context: ModelInput) -> ModelAction:
            self.calls += 1
            return FinalAnswerAction(answer="done")

    adapter = OneCallAdapter()
    runtime, events, effects = make_runtime(tmp_path, workspace)
    initialize_budgeted_run(events, "run-action-durable", max_steps=1)
    crashing_agent = ModelDrivenFakeAgent(
        runtime=runtime,
        verifier=ReceiptVerifier(),
        adapter=adapter,
        run_id="run-action-durable",
        loop_failure_injector=CrashAfterModelActionPersisted(),
    )

    with pytest.raises(SimulatedCrash, match="action persisted"):
        crashing_agent.run_loop(
            tool_expectations=PathExpectationResolver({}),
            completion_expectation=CompletionExpectation(
                expected_answer="done",
                require_verified_observation=False,
            ),
        )

    assert adapter.calls == 1
    assert runtime.load_state("run-action-durable").status is RunStatus.ACTION_PENDING
    events.close()
    effects.close()

    class ForbiddenAdapter:
        calls = 0

        def next_action(self, context: ModelInput) -> ModelAction:
            self.calls += 1
            raise AssertionError("durable Action must be replayed")

    forbidden = ForbiddenAdapter()
    recovered_runtime, recovered_events, recovered_effects = make_runtime(tmp_path, workspace)
    recovered_agent = ModelDrivenFakeAgent(
        runtime=recovered_runtime,
        verifier=ReceiptVerifier(),
        adapter=forbidden,
        run_id="run-action-durable",
    )
    result = recovered_agent.run_loop(
        tool_expectations=PathExpectationResolver({}),
        completion_expectation=CompletionExpectation(
            expected_answer="done",
            require_verified_observation=False,
        ),
    )

    assert result.outcome is ModelLoopOutcome.COMPLETED
    assert result.state.turn_index == 1
    assert forbidden.calls == 0
    event_types = [event.event_type for event in recovered_events.load("run-action-durable")]
    assert event_types.count(EventType.MODEL_ACTION_REQUESTED) == 1
    assert event_types.count(EventType.MODEL_ACTION_PROPOSED) == 1
    assert event_types[-1] is EventType.COMPLETION_ACCEPTED
    recovered_events.close()
    recovered_effects.close()


def test_persisted_tool_action_dispatches_once_without_recalling_adapter(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    workspace.joinpath("notes.txt").write_text("durable tool action", encoding="utf-8")
    action = ToolCallAction.build(
        tool_call_id="call-durable",
        tool_name="read_file",
        arguments={"path": "notes.txt"},
    )
    runtime, events, effects = make_runtime(tmp_path, workspace)
    initialize_budgeted_run(events, "run-durable-tool-action", max_steps=1)
    crashing_agent = ModelDrivenFakeAgent(
        runtime=runtime,
        verifier=ReceiptVerifier(),
        adapter=StaticModelAdapter(actions=(action,)),
        run_id="run-durable-tool-action",
        loop_failure_injector=CrashAfterModelActionPersisted(),
    )

    with pytest.raises(SimulatedCrash, match="action persisted"):
        crashing_agent.run_loop(
            tool_expectations=PathExpectationResolver({}),
            completion_expectation=CompletionExpectation(expected_answer="unused"),
        )

    events.close()
    effects.close()

    class ForbiddenAdapter:
        calls = 0

        def next_action(self, context: ModelInput) -> ModelAction:
            self.calls += 1
            raise AssertionError("persisted Tool Action must be replayed")

    forbidden = ForbiddenAdapter()
    recovered_runtime, recovered_events, recovered_effects = make_runtime(tmp_path, workspace)
    recovered_agent = ModelDrivenFakeAgent(
        runtime=recovered_runtime,
        verifier=ReceiptVerifier(),
        adapter=forbidden,
        run_id="run-durable-tool-action",
    )
    result = recovered_agent.run_loop(
        tool_expectations=PathExpectationResolver({"notes.txt": "0" * 64}),
        completion_expectation=CompletionExpectation(expected_answer="unused"),
    )

    assert result.outcome is ModelLoopOutcome.FAILED
    assert forbidden.calls == 0
    event_types = [event.event_type for event in recovered_events.load("run-durable-tool-action")]
    assert event_types.count(EventType.MODEL_ACTION_REQUESTED) == 1
    assert event_types.count(EventType.MODEL_ACTION_PROPOSED) == 1
    assert event_types.count(EventType.TOOL_REQUESTED) == 1
    assert event_types.count(EventType.TOOL_STARTED) == 1
    assert event_types.count(EventType.TOOL_SUCCEEDED) == 1
    assert event_types[-1] is EventType.VERIFICATION_FAILED
    recovered_events.close()
    recovered_effects.close()


def test_bounded_loop_pauses_at_gate_without_reinvoking_adapter(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime, events, effects = make_runtime(tmp_path, workspace)
    initialize_budgeted_run(events, "run-loop-gate", max_steps=2)

    class CountingAdapter:
        calls = 0

        def next_action(self, context: ModelInput) -> ModelAction:
            self.calls += 1
            return ToolCallAction.build(
                tool_call_id="call-write",
                tool_name="write_file",
                arguments={"path": "notes.txt", "content": "paused"},
            )

    adapter = CountingAdapter()

    class ForbiddenExpectations:
        def expectation_for(
            self,
            context: ModelInput,
            action: ToolCallAction,
        ) -> VerificationExpectation:
            raise AssertionError("paused tool must not resolve execution expectations")

    agent = ModelDrivenFakeAgent(
        runtime=runtime,
        verifier=ReceiptVerifier(),
        adapter=adapter,
        run_id="run-loop-gate",
    )

    result = agent.run_loop(
        tool_expectations=ForbiddenExpectations(),
        completion_expectation=CompletionExpectation(expected_answer="done"),
    )

    assert result.outcome is ModelLoopOutcome.PAUSED
    assert result.state.status is RunStatus.AWAITING_GATE
    assert adapter.calls == 1
    assert len(result.actions) == 1
    assert len(result.tool_results) == 1
    assert result.tool_results[0].outcome is RuntimeToolOutcome.AWAITING_GATE
    assert not workspace.joinpath("notes.txt").exists()
    events.close()
    effects.close()


def test_bounded_loop_stops_after_failed_tool_verification(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    workspace.joinpath("notes.txt").write_text("actual", encoding="utf-8")
    runtime, events, effects = make_runtime(tmp_path, workspace)
    initialize_budgeted_run(events, "run-loop-verification", max_steps=2)

    class ForbiddenSecondActionAdapter:
        calls = 0

        def next_action(self, context: ModelInput) -> ModelAction:
            self.calls += 1
            if self.calls > 1:
                raise AssertionError("failed verification must stop the loop")
            return ToolCallAction.build(
                tool_call_id="call-1",
                tool_name="read_file",
                arguments={"path": "notes.txt"},
            )

    adapter = ForbiddenSecondActionAdapter()
    agent = ModelDrivenFakeAgent(
        runtime=runtime,
        verifier=ReceiptVerifier(),
        adapter=adapter,
        run_id="run-loop-verification",
    )

    result = agent.run_loop(
        tool_expectations=PathExpectationResolver({"notes.txt": "0" * 64}),
        completion_expectation=CompletionExpectation(expected_answer="done"),
    )

    assert result.outcome is ModelLoopOutcome.FAILED
    assert result.state.status is RunStatus.FAILED
    assert result.state.turn_index == 0
    assert adapter.calls == 1
    assert len(result.verifications) == 1
    assert result.verifications[0].passed is False
    assert events.load("run-loop-verification")[-1].event_type is EventType.VERIFICATION_FAILED
    events.close()
    effects.close()


def test_bounded_loop_budget_exhaustion_becomes_durable_failure(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    content = "only action"
    workspace.joinpath("notes.txt").write_text(content, encoding="utf-8")
    runtime, events, effects = make_runtime(tmp_path, workspace)
    initialize_budgeted_run(events, "run-loop-budget", max_steps=1)
    agent = ModelDrivenFakeAgent(
        runtime=runtime,
        verifier=ReceiptVerifier(),
        adapter=StaticModelAdapter(
            actions=(
                ToolCallAction.build(
                    tool_call_id="call-1",
                    tool_name="read_file",
                    arguments={"path": "notes.txt"},
                ),
            )
        ),
        run_id="run-loop-budget",
    )

    result = agent.run_loop(
        tool_expectations=PathExpectationResolver(
            {"notes.txt": hashlib.sha256(content.encode("utf-8")).hexdigest()}
        ),
        completion_expectation=CompletionExpectation(expected_answer="done"),
    )

    assert result.outcome is ModelLoopOutcome.FAILED
    assert result.state.status is RunStatus.FAILED
    assert result.state.failure_reason == "step budget exhausted: 1/1 steps consumed"
    assert len(result.actions) == 1
    assert events.load("run-loop-budget")[-1].event_type is EventType.RUN_STEP_BUDGET_EXHAUSTED
    events.close()
    effects.close()


def test_bounded_loop_fails_closed_when_static_adapter_is_exhausted(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime, events, effects = make_runtime(tmp_path, workspace)
    initialize_budgeted_run(events, "run-loop-adapter", max_steps=2)
    agent = ModelDrivenFakeAgent(
        runtime=runtime,
        verifier=ReceiptVerifier(),
        adapter=StaticModelAdapter(actions=()),
        run_id="run-loop-adapter",
    )

    result = agent.run_loop(
        tool_expectations=PathExpectationResolver({}),
        completion_expectation=CompletionExpectation(
            expected_answer="done",
            require_verified_observation=False,
        ),
    )

    assert result.outcome is ModelLoopOutcome.FAILED
    assert result.state.status is RunStatus.FAILED
    assert result.actions == ()
    assert events.load("run-loop-adapter")[-1].event_type is EventType.MODEL_ACTION_FAILED
    events.close()
    effects.close()


def test_bounded_loop_rejects_legacy_run_without_persisted_budget(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime, events, effects = make_runtime(tmp_path, workspace)
    initialize_run(events, "run-loop-unbounded")
    agent = ModelDrivenFakeAgent(
        runtime=runtime,
        verifier=ReceiptVerifier(),
        adapter=StaticModelAdapter(actions=(FinalAnswerAction(answer="done"),)),
        run_id="run-loop-unbounded",
    )

    with pytest.raises(InvalidTransitionError, match="requires max_steps"):
        agent.run_loop(
            tool_expectations=PathExpectationResolver({}),
            completion_expectation=CompletionExpectation(
                expected_answer="done",
                require_verified_observation=False,
            ),
        )

    assert runtime.load_state("run-loop-unbounded").status is RunStatus.READY
    assert len(events.load("run-loop-unbounded")) == 2
    events.close()
    effects.close()


def test_pair_gate_loop_recovers_after_crash_without_repeating_adapter_or_tool(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    content = "approved durable write"
    actions: tuple[ModelAction, ...] = (
        ToolCallAction.build(
            tool_call_id="call-write",
            tool_name="write_file",
            arguments={"path": "notes.txt", "content": content},
        ),
        FinalAnswerAction(answer="done"),
    )
    runtime, events, effects = make_runtime(tmp_path, workspace, enable_snapshots=True)
    initialize_budgeted_run(events, "run-pair-resume", max_steps=2)
    first_agent = ModelDrivenFakeAgent(
        runtime=runtime,
        verifier=ReceiptVerifier(),
        adapter=StaticModelAdapter(actions=actions),
        run_id="run-pair-resume",
    )

    paused = first_agent.run_loop(
        tool_expectations=PathExpectationResolver({}),
        completion_expectation=CompletionExpectation(expected_answer="done"),
    )
    assert paused.outcome is ModelLoopOutcome.PAUSED
    proposal = paused.tool_results[0].gate_proposal
    assert proposal is not None

    approved = runtime.resolve_gate(GateResolution.approve(proposal.reference, actor="lucas"))
    assert approved.state.status is RunStatus.VERIFYING
    assert workspace.joinpath("notes.txt").read_text(encoding="utf-8") == content
    runtime.create_snapshot("run-pair-resume")
    events.close()
    effects.close()

    class ForbiddenAdapter:
        def next_action(self, context: ModelInput) -> ModelAction:
            del context
            raise AssertionError("snapshot recovery must not call the Adapter")

    snapshot_crashing_runtime, snapshot_crashing_events, snapshot_crashing_effects = make_runtime(
        tmp_path,
        workspace,
        enable_snapshots=True,
        snapshot_failure_injector=CrashBeforeSnapshotTail(),
    )
    snapshot_crashing_agent = ModelDrivenFakeAgent(
        runtime=snapshot_crashing_runtime,
        verifier=ReceiptVerifier(),
        adapter=ForbiddenAdapter(),
        run_id="run-pair-resume",
    )
    expectation = PathExpectationResolver(
        {"notes.txt": hashlib.sha256(content.encode("utf-8")).hexdigest()}
    )

    with pytest.raises(SimulatedCrash, match="snapshot tail replay"):
        snapshot_crashing_agent.resume_loop(
            tool_expectations=expectation,
            completion_expectation=CompletionExpectation(expected_answer="done"),
        )

    snapshot_event_types = [
        event.event_type for event in snapshot_crashing_events.load("run-pair-resume")
    ]
    assert snapshot_event_types.count(EventType.TOOL_REQUESTED) == 1
    assert snapshot_event_types.count(EventType.TOOL_STARTED) == 1
    assert snapshot_event_types.count(EventType.TOOL_SUCCEEDED) == 1
    snapshot_crashing_events.close()
    snapshot_crashing_effects.close()

    crashing_runtime, crashing_events, crashing_effects = make_runtime(
        tmp_path,
        workspace,
        enable_snapshots=True,
    )
    crashing_agent = ModelDrivenFakeAgent(
        runtime=crashing_runtime,
        verifier=ReceiptVerifier(),
        adapter=StaticModelAdapter(actions=actions),
        run_id="run-pair-resume",
        loop_failure_injector=CrashBeforeRecoveredVerification(),
    )
    with pytest.raises(SimulatedCrash, match="recovered verification"):
        crashing_agent.resume_loop(
            tool_expectations=expectation,
            completion_expectation=CompletionExpectation(expected_answer="done"),
        )

    assert crashing_runtime.load_state("run-pair-resume").status is RunStatus.VERIFYING
    crashing_events.close()
    crashing_effects.close()

    class FinalOnlyAdapter:
        calls = 0

        def next_action(self, context: ModelInput) -> ModelAction:
            self.calls += 1
            assert context.turn_index == 1
            return FinalAnswerAction(answer="done")

    recovered_runtime, recovered_events, recovered_effects = make_runtime(
        tmp_path,
        workspace,
        enable_snapshots=True,
    )
    adapter = FinalOnlyAdapter()
    recovered_agent = ModelDrivenFakeAgent(
        runtime=recovered_runtime,
        verifier=ReceiptVerifier(),
        adapter=adapter,
        run_id="run-pair-resume",
    )

    completed = recovered_agent.resume_loop(
        tool_expectations=expectation,
        completion_expectation=CompletionExpectation(expected_answer="done"),
    )

    assert completed.outcome is ModelLoopOutcome.COMPLETED
    assert completed.state.turn_index == 2
    assert adapter.calls == 1
    assert len(completed.recovered_receipts) == 1
    assert [type(action) for action in completed.actions] == [
        ToolCallAction,
        FinalAnswerAction,
    ]
    event_types = [event.event_type for event in recovered_events.load("run-pair-resume")]
    assert event_types.count(EventType.TOOL_REQUESTED) == 1
    assert event_types.count(EventType.TOOL_STARTED) == 1
    assert event_types.count(EventType.TOOL_SUCCEEDED) == 1
    assert event_types.count(EventType.VERIFICATION_SUCCEEDED) == 1
    assert event_types[-1] is EventType.COMPLETION_ACCEPTED
    recovered_events.close()
    recovered_effects.close()


def test_user_gate_pass_resumes_same_persisted_tool_turn(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    content = "user gated write"
    actions: tuple[ModelAction, ...] = (
        ToolCallAction.build(
            tool_call_id="call-user-write",
            tool_name="write_file",
            arguments={"path": "notes.txt", "content": content},
        ),
        FinalAnswerAction(answer="done"),
    )
    runtime, events, effects = make_runtime(
        tmp_path,
        workspace,
        write_mode=OwnershipMode.USER_GATE,
    )
    initialize_budgeted_run(events, "run-user-resume", max_steps=2)
    agent = ModelDrivenFakeAgent(
        runtime=runtime,
        verifier=ReceiptVerifier(),
        adapter=StaticModelAdapter(actions=actions),
        run_id="run-user-resume",
    )
    paused = agent.run_loop(
        tool_expectations=PathExpectationResolver({}),
        completion_expectation=CompletionExpectation(expected_answer="done"),
    )
    proposal = paused.tool_results[0].gate_proposal
    assert proposal is not None

    passed = runtime.submit_gate_answer(
        GateAnswerSubmission.build(
            proposal.reference,
            actor="lucas",
            answer={
                "tool_name": "write_file",
                "path": "notes.txt",
                "risk_explanation": (
                    "写入会修改工作区文件内容，执行前必须确认目标路径和预期结果正确无误"
                ),
                "refuse": False,
            },
        )
    )
    assert passed.state.status is RunStatus.VERIFYING

    completed = agent.resume_loop(
        tool_expectations=PathExpectationResolver(
            {"notes.txt": hashlib.sha256(content.encode("utf-8")).hexdigest()}
        ),
        completion_expectation=CompletionExpectation(expected_answer="done"),
    )

    assert completed.outcome is ModelLoopOutcome.COMPLETED
    assert completed.state.status is RunStatus.COMPLETED
    assert completed.state.turn_index == 2
    assert len(completed.recovered_receipts) == 1
    assert workspace.joinpath("notes.txt").read_text(encoding="utf-8") == content
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
