from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from agent_runtime_lab.authorized_tool_runtime import (
    AuthorizedToolRuntime,
    RuntimeToolOutcome,
)
from agent_runtime_lab.domain.errors import GateReferenceMismatchError, InvalidTransitionError
from agent_runtime_lab.domain.events import EventType, ExecutionEvent
from agent_runtime_lab.domain.state import RunStatus
from agent_runtime_lab.domain.tool_effects import derive_effect_id
from agent_runtime_lab.durable_tool_executor import DurableToolExecutor
from agent_runtime_lab.ownership.authorization import (
    AuthorizationContext,
    ToolRequest,
    WorkspaceBoundary,
)
from agent_runtime_lab.ownership.gates import (
    GateAnswerSubmission,
    GateEvaluation,
    GateEvaluationOutcome,
    GateProposal,
    GateResolution,
    evaluate_gate,
)
from agent_runtime_lab.ownership.policy import (
    OwnershipMode,
    OwnershipPolicy,
    OwnershipRule,
)
from agent_runtime_lab.ownership.risk_evaluator import RiskEvaluator, RiskRule
from agent_runtime_lab.persistence.sqlite_store import SQLiteEventStore
from agent_runtime_lab.persistence.sqlite_tool_effect_store import SQLiteToolEffectStore
from agent_runtime_lab.tool_registry import ToolDefinition, ToolRegistry

NOW = datetime(2026, 8, 10, 0, 0, tzinfo=UTC)


class RecordingToolRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any], str]] = []

    def invoke(
        self,
        *,
        tool_name: str,
        arguments: Mapping[str, Any],
        idempotency_key: str,
    ) -> Mapping[str, Any]:
        self.calls.append((tool_name, dict(arguments), idempotency_key))
        return {"ok": True}


def make_registry() -> ToolRegistry:
    return ToolRegistry(
        [
            ToolDefinition(
                tool_name="read_file",
                retry_is_idempotent=True,
                path_argument_names=("path",),
            ),
            ToolDefinition(
                tool_name="write_file",
                retry_is_idempotent=False,
                path_argument_names=("path",),
            ),
            ToolDefinition(
                tool_name="delete_file",
                retry_is_idempotent=False,
                path_argument_names=("path",),
            ),
        ]
    )


def make_authorization_context(root: Path, registry: ToolRegistry) -> AuthorizationContext:
    return AuthorizationContext(
        registry=registry,
        workspace=WorkspaceBoundary(root),
        risk_evaluator=RiskEvaluator(
            rules=(
                RiskRule(tag="write_operation", tool_names=frozenset({"write_file"})),
                RiskRule(tag="destructive_tool", tool_names=frozenset({"delete_file"})),
            )
        ),
        policy=OwnershipPolicy(
            rules=(
                OwnershipRule(
                    risk_tags=frozenset({"write_operation"}),
                    minimum_mode=OwnershipMode.PAIR,
                    reason="writes require pair review",
                ),
                OwnershipRule(
                    risk_tags=frozenset({"destructive_tool"}),
                    minimum_mode=OwnershipMode.USER_GATE,
                    reason="deletion requires explicit user approval",
                ),
            )
        ),
    )


def initialize_run(store: SQLiteEventStore, run_id: str = "run-1") -> None:
    store.append(
        ExecutionEvent.build(
            event_id=f"{run_id}:0:created",
            run_id=run_id,
            sequence=0,
            event_type=EventType.RUN_CREATED,
            occurred_at=NOW,
        )
    )
    store.append(
        ExecutionEvent.build(
            event_id=f"{run_id}:1:started",
            run_id=run_id,
            sequence=1,
            event_type=EventType.RUN_STARTED,
            occurred_at=NOW,
        )
    )


def make_request(
    *,
    tool_name: str,
    path: str = "README.md",
    tool_call_id: str = "call-1",
) -> ToolRequest:
    return ToolRequest.build(
        run_id="run-1",
        step_id="step-1",
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        arguments={"path": path},
    )


def make_runtime(
    *,
    database_path: Path,
    workspace_root: Path,
    runner: RecordingToolRunner,
    gate_evaluator: Any = evaluate_gate,
    user_gate_max_attempts: int = 3,
    authorization_context: AuthorizationContext | None = None,
) -> tuple[AuthorizedToolRuntime, SQLiteEventStore, SQLiteToolEffectStore]:
    registry = make_registry()
    event_store = SQLiteEventStore(database_path)
    effect_store = SQLiteToolEffectStore(database_path)
    executor = DurableToolExecutor(
        store=effect_store,
        runner=runner,
        registry=registry,
    )
    runtime = AuthorizedToolRuntime(
        event_store=event_store,
        executor=executor,
        authorization_context=(
            authorization_context
            if authorization_context is not None
            else make_authorization_context(workspace_root, registry)
        ),
        gate_evaluator=gate_evaluator,
        user_gate_max_attempts=user_gate_max_attempts,
        clock=lambda: NOW,
    )
    return runtime, event_store, effect_store


def test_auto_request_is_authorized_then_executed(tmp_path: Path) -> None:
    runner = RecordingToolRunner()
    runtime, event_store, effect_store = make_runtime(
        database_path=tmp_path / "runtime.db",
        workspace_root=tmp_path,
        runner=runner,
    )
    initialize_run(event_store)

    result = runtime.submit(make_request(tool_name="read_file"))

    assert result.outcome is RuntimeToolOutcome.EXECUTED
    assert result.state.status is RunStatus.VERIFYING
    assert result.receipt is not None
    assert len(runner.calls) == 1
    assert [event.event_type for event in event_store.load("run-1")][-4:] == [
        EventType.TOOL_REQUESTED,
        EventType.TOOL_AUTHORIZED,
        EventType.TOOL_STARTED,
        EventType.TOOL_SUCCEEDED,
    ]

    event_store.close()
    effect_store.close()


def test_denied_request_never_reaches_durable_effect_store(tmp_path: Path) -> None:
    runner = RecordingToolRunner()
    runtime, event_store, effect_store = make_runtime(
        database_path=tmp_path / "runtime.db",
        workspace_root=tmp_path,
        runner=runner,
    )
    initialize_run(event_store)
    request = make_request(tool_name="read_file", path="../outside.txt")

    result = runtime.submit(request)

    assert result.outcome is RuntimeToolOutcome.DENIED
    assert result.state.status is RunStatus.FAILED
    assert (
        effect_store.load_intent(
            derive_effect_id(run_id=request.run_id, tool_call_id=request.tool_call_id)
        )
        is None
    )
    assert runner.calls == []

    event_store.close()
    effect_store.close()


def test_pair_gate_survives_restart_and_executes_only_after_exact_approval(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "runtime.db"
    runner = RecordingToolRunner()
    runtime, event_store, effect_store = make_runtime(
        database_path=database_path,
        workspace_root=tmp_path,
        runner=runner,
    )
    initialize_run(event_store)

    paused = runtime.submit(make_request(tool_name="write_file"))

    assert paused.outcome is RuntimeToolOutcome.AWAITING_GATE
    assert paused.state.status is RunStatus.AWAITING_GATE
    assert paused.state.active_gate_mode == OwnershipMode.PAIR.value
    assert paused.gate_proposal is not None
    assert runner.calls == []

    reference = paused.gate_proposal.reference
    event_store.close()
    effect_store.close()

    recovered, recovered_events, recovered_effects = make_runtime(
        database_path=database_path,
        workspace_root=tmp_path,
        runner=runner,
    )

    assert recovered.load_state("run-1").status is RunStatus.AWAITING_GATE

    completed = recovered.resolve_gate(
        GateResolution.approve(
            reference,
            actor="lucas",
            reason="reviewed the exact write proposal",
        )
    )

    assert completed.outcome is RuntimeToolOutcome.EXECUTED
    assert completed.state.status is RunStatus.VERIFYING
    assert len(runner.calls) == 1
    assert runner.calls[0][0:2] == ("write_file", {"path": "README.md"})
    assert [event.event_type for event in recovered_events.load("run-1")][-3:] == [
        EventType.GATE_APPROVED,
        EventType.TOOL_STARTED,
        EventType.TOOL_SUCCEEDED,
    ]

    recovered_events.close()
    recovered_effects.close()


def test_forged_or_stale_gate_reference_cannot_resume_execution(tmp_path: Path) -> None:
    runner = RecordingToolRunner()
    runtime, event_store, effect_store = make_runtime(
        database_path=tmp_path / "runtime.db",
        workspace_root=tmp_path,
        runner=runner,
    )
    initialize_run(event_store)
    paused = runtime.submit(make_request(tool_name="delete_file"))
    assert paused.gate_proposal is not None

    stale_reference = paused.gate_proposal.reference.model_copy(update={"revision": 2})

    with pytest.raises(GateReferenceMismatchError, match="revision"):
        runtime.resolve_gate(GateResolution.approve(stale_reference, actor="lucas"))

    assert runtime.load_state("run-1").status is RunStatus.AWAITING_GATE
    assert runner.calls == []

    event_store.close()
    effect_store.close()


def test_revised_gate_invalidates_old_reference_and_survives_restart(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "runtime.db"
    runner = RecordingToolRunner()
    runtime, event_store, effect_store = make_runtime(
        database_path=database_path,
        workspace_root=tmp_path,
        runner=runner,
    )
    initialize_run(event_store)
    paused = runtime.submit(make_request(tool_name="write_file"))
    assert paused.gate_proposal is not None
    old_reference = paused.gate_proposal.reference

    revised = runtime.revise_gate(old_reference)

    assert revised.gate_proposal is not None
    new_reference = revised.gate_proposal.reference
    assert new_reference.revision == old_reference.revision + 1
    assert new_reference.proposal_digest != old_reference.proposal_digest
    assert revised.state.active_gate_revision == new_reference.revision
    assert revised.state.active_gate_proposal_digest == new_reference.proposal_digest
    event_store.close()
    effect_store.close()

    recovered, recovered_events, recovered_effects = make_runtime(
        database_path=database_path,
        workspace_root=tmp_path,
        runner=runner,
    )
    with pytest.raises(GateReferenceMismatchError):
        recovered.resolve_gate(GateResolution.approve(old_reference, actor="lucas"))

    completed = recovered.resolve_gate(
        GateResolution.approve(
            new_reference,
            actor="lucas",
            reason="reviewed the current revision",
        )
    )

    assert completed.outcome is RuntimeToolOutcome.EXECUTED
    assert len(runner.calls) == 1
    assert [event.event_type for event in recovered_events.load("run-1")][-4:] == [
        EventType.GATE_REVISED,
        EventType.GATE_APPROVED,
        EventType.TOOL_STARTED,
        EventType.TOOL_SUCCEEDED,
    ]
    recovered_events.close()
    recovered_effects.close()


def test_revision_rollover_resets_user_gate_attempts(tmp_path: Path) -> None:
    runner = RecordingToolRunner()
    evaluator = SequencedGateEvaluator(
        GateEvaluation(outcome=GateEvaluationOutcome.RETRY, reason="incomplete"),
    )
    runtime, event_store, effect_store = make_runtime(
        database_path=tmp_path / "runtime.db",
        workspace_root=tmp_path,
        runner=runner,
        gate_evaluator=evaluator,
    )
    initialize_run(event_store)
    paused = runtime.submit(make_request(tool_name="delete_file"))
    assert paused.gate_proposal is not None
    old_reference = paused.gate_proposal.reference
    retry = runtime.submit_gate_answer(
        GateAnswerSubmission.build(old_reference, actor="lucas", answer={"answer": "weak"})
    )
    assert retry.state.active_gate_attempts == 1

    revised = runtime.revise_gate(old_reference)

    assert revised.gate_proposal is not None
    assert revised.state.active_gate_attempts == 0
    assert revised.state.active_gate_max_attempts == 3
    with pytest.raises(GateReferenceMismatchError):
        runtime.submit_gate_answer(
            GateAnswerSubmission.build(
                old_reference,
                actor="lucas",
                answer={"answer": "stale"},
            )
        )
    assert runner.calls == []
    event_store.close()
    effect_store.close()


def test_policy_upgrade_rollover_changes_pair_to_user_gate(tmp_path: Path) -> None:
    database_path = tmp_path / "runtime.db"
    runner = RecordingToolRunner()
    runtime, event_store, effect_store = make_runtime(
        database_path=database_path,
        workspace_root=tmp_path,
        runner=runner,
    )
    initialize_run(event_store)
    paused = runtime.submit(make_request(tool_name="write_file"))
    assert paused.gate_proposal is not None
    old_reference = paused.gate_proposal.reference
    event_store.close()
    effect_store.close()

    registry = make_registry()
    upgraded_context = make_authorization_context(tmp_path, registry)
    upgraded_context = AuthorizationContext(
        registry=upgraded_context.registry,
        workspace=upgraded_context.workspace,
        risk_evaluator=upgraded_context.risk_evaluator,
        policy=upgraded_context.policy,
        minimum_mode=OwnershipMode.USER_GATE,
    )
    upgraded, upgraded_events, upgraded_effects = make_runtime(
        database_path=database_path,
        workspace_root=tmp_path,
        runner=runner,
        authorization_context=upgraded_context,
    )

    revised = upgraded.revise_gate(old_reference)

    assert revised.gate_proposal is not None
    assert revised.gate_proposal.ownership_mode is OwnershipMode.USER_GATE
    assert revised.state.active_gate_mode == OwnershipMode.USER_GATE.value
    assert revised.state.active_gate_max_attempts == 3
    with pytest.raises(GateReferenceMismatchError):
        upgraded.resolve_gate(GateResolution.approve(old_reference, actor="lucas"))
    with pytest.raises(InvalidTransitionError, match="evaluated answer"):
        upgraded.resolve_gate(
            GateResolution.approve(revised.gate_proposal.reference, actor="lucas")
        )
    assert runner.calls == []
    upgraded_events.close()
    upgraded_effects.close()


def test_approved_gate_can_recover_if_process_stops_before_tool_start(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "runtime.db"
    runner = RecordingToolRunner()
    runtime, event_store, effect_store = make_runtime(
        database_path=database_path,
        workspace_root=tmp_path,
        runner=runner,
    )
    initialize_run(event_store)
    paused = runtime.submit(make_request(tool_name="write_file"))
    assert paused.gate_proposal is not None
    reference = paused.gate_proposal.reference

    event_store.append(
        ExecutionEvent.build(
            event_id="run-1:4:gate.approved",
            run_id="run-1",
            sequence=4,
            event_type=EventType.GATE_APPROVED,
            occurred_at=NOW,
            payload={
                "actor": "lucas",
                "proposal_digest": reference.proposal_digest,
                "reason": "approved before simulated restart",
                "revision": reference.revision,
                "tool_call_id": reference.tool_call_id,
            },
        )
    )
    assert runtime.load_state("run-1").status is RunStatus.TOOL_READY
    event_store.close()
    effect_store.close()

    recovered, recovered_events, recovered_effects = make_runtime(
        database_path=database_path,
        workspace_root=tmp_path,
        runner=runner,
    )
    result = recovered.recover("run-1")

    assert result.state.status is RunStatus.VERIFYING
    assert len(runner.calls) == 1
    assert [event.event_type for event in recovered_events.load("run-1")][-2:] == [
        EventType.TOOL_STARTED,
        EventType.TOOL_SUCCEEDED,
    ]

    recovered_events.close()
    recovered_effects.close()


def test_user_gate_rejection_is_durable_and_never_executes(tmp_path: Path) -> None:
    runner = RecordingToolRunner()
    runtime, event_store, effect_store = make_runtime(
        database_path=tmp_path / "runtime.db",
        workspace_root=tmp_path,
        runner=runner,
    )
    initialize_run(event_store)
    paused = runtime.submit(make_request(tool_name="delete_file"))
    assert paused.gate_proposal is not None
    assert paused.state.active_gate_mode == OwnershipMode.USER_GATE.value

    rejected = runtime.resolve_gate(
        GateResolution.reject(
            paused.gate_proposal.reference,
            actor="lucas",
            reason="deletion is not acceptable",
        )
    )

    assert rejected.outcome is RuntimeToolOutcome.REJECTED
    assert rejected.state.status is RunStatus.FAILED
    assert rejected.state.failure_reason == "deletion is not acceptable"
    assert runtime.load_state("run-1") == rejected.state
    assert runner.calls == []

    event_store.close()
    effect_store.close()


def test_default_gate_evaluator_retries_incomplete_answer(tmp_path: Path) -> None:
    runner = RecordingToolRunner()
    runtime, event_store, effect_store = make_runtime(
        database_path=tmp_path / "runtime.db",
        workspace_root=tmp_path,
        runner=runner,
    )
    initialize_run(event_store)
    paused = runtime.submit(make_request(tool_name="delete_file"))
    assert paused.gate_proposal is not None

    result = runtime.submit_gate_answer(
        GateAnswerSubmission.build(
            paused.gate_proposal.reference,
            actor="lucas",
            answer={"risk_explanation": "I understand the deletion risk"},
        )
    )

    assert result.outcome is RuntimeToolOutcome.GATE_RETRY
    assert result.gate_evaluation is not None
    assert result.gate_evaluation.outcome is GateEvaluationOutcome.RETRY
    assert runtime.load_state("run-1").active_gate_attempts == 1
    assert runner.calls == []
    event_store.close()
    effect_store.close()


def test_default_gate_evaluator_blocks_explicit_refusal(tmp_path: Path) -> None:
    runner = RecordingToolRunner()
    runtime, event_store, effect_store = make_runtime(
        database_path=tmp_path / "runtime.db",
        workspace_root=tmp_path,
        runner=runner,
    )
    initialize_run(event_store)
    paused = runtime.submit(make_request(tool_name="delete_file"))
    assert paused.gate_proposal is not None

    result = runtime.submit_gate_answer(
        GateAnswerSubmission.build(
            paused.gate_proposal.reference,
            actor="lucas",
            answer={"refuse": True},
        )
    )

    assert result.outcome is RuntimeToolOutcome.BLOCKED
    assert result.gate_evaluation is not None
    assert result.gate_evaluation.outcome is GateEvaluationOutcome.BLOCK
    assert result.state.status is RunStatus.FAILED
    assert runner.calls == []
    event_store.close()
    effect_store.close()


def test_default_gate_evaluator_passes_exact_well_explained_answer(
    tmp_path: Path,
) -> None:
    runner = RecordingToolRunner()
    runtime, event_store, effect_store = make_runtime(
        database_path=tmp_path / "runtime.db",
        workspace_root=tmp_path,
        runner=runner,
    )
    initialize_run(event_store)
    paused = runtime.submit(make_request(tool_name="delete_file"))
    assert paused.gate_proposal is not None

    result = runtime.submit_gate_answer(
        GateAnswerSubmission.build(
            paused.gate_proposal.reference,
            actor="lucas",
            answer={
                "tool_name": "delete_file",
                "path": "README.md",
                "risk_explanation": (
                    "Deleting this file removes project documentation and may require "
                    "restoring it from version control."
                ),
                "refuse": False,
            },
        )
    )

    assert result.outcome is RuntimeToolOutcome.EXECUTED
    assert result.gate_evaluation is not None
    assert result.gate_evaluation.outcome is GateEvaluationOutcome.PASS
    assert len(runner.calls) == 1
    assert runner.calls[0][0:2] == ("delete_file", {"path": "README.md"})
    event_store.close()
    effect_store.close()


@pytest.mark.parametrize(
    ("answer", "reason_fragment"),
    [
        (
            {
                "tool_name": "write_file",
                "path": "README.md",
                "risk_explanation": "This deletion permanently removes the selected project file.",
                "refuse": False,
            },
            "tool_name does not match",
        ),
        (
            {
                "tool_name": "delete_file",
                "path": "docs/progress.md",
                "risk_explanation": "This deletion permanently removes the selected project file.",
                "refuse": False,
            },
            "path does not match",
        ),
        (
            {
                "tool_name": "delete_file",
                "path": "README.md",
                "risk_explanation": "too short",
                "refuse": False,
            },
            "at least 20",
        ),
        (
            {
                "tool_name": "delete_file",
                "path": "README.md",
                "risk_explanation": "This deletion permanently removes the selected project file.",
                "refuse": "false",
            },
            "refuse must be a boolean",
        ),
    ],
)
def test_default_gate_evaluator_retries_mismatched_or_invalid_answers(
    tmp_path: Path,
    answer: dict[str, Any],
    reason_fragment: str,
) -> None:
    runner = RecordingToolRunner()
    runtime, event_store, effect_store = make_runtime(
        database_path=tmp_path / "runtime.db",
        workspace_root=tmp_path,
        runner=runner,
    )
    initialize_run(event_store)
    paused = runtime.submit(make_request(tool_name="delete_file"))
    assert paused.gate_proposal is not None

    evaluation = evaluate_gate(paused.gate_proposal, answer)

    assert evaluation.outcome is GateEvaluationOutcome.RETRY
    assert reason_fragment in evaluation.reason
    event_store.close()
    effect_store.close()


def test_pair_and_user_gate_use_distinct_resolution_paths(tmp_path: Path) -> None:
    runner = RecordingToolRunner()
    runtime, event_store, effect_store = make_runtime(
        database_path=tmp_path / "runtime.db",
        workspace_root=tmp_path,
        runner=runner,
    )
    initialize_run(event_store)
    pair = runtime.submit(make_request(tool_name="write_file"))
    assert pair.gate_proposal is not None

    with pytest.raises(InvalidTransitionError, match="only valid for USER_GATE"):
        runtime.submit_gate_answer(
            GateAnswerSubmission.build(
                pair.gate_proposal.reference,
                actor="lucas",
                answer={"explanation": "not a pair approval"},
            )
        )

    event_store.close()
    effect_store.close()

    runner = RecordingToolRunner()
    runtime, event_store, effect_store = make_runtime(
        database_path=tmp_path / "user-gate.db",
        workspace_root=tmp_path,
        runner=runner,
    )
    initialize_run(event_store)
    user_gate = runtime.submit(make_request(tool_name="delete_file"))
    assert user_gate.gate_proposal is not None

    with pytest.raises(InvalidTransitionError, match="evaluated answer"):
        runtime.resolve_gate(
            GateResolution.approve(user_gate.gate_proposal.reference, actor="lucas")
        )

    assert runtime.load_state("run-1").status is RunStatus.AWAITING_GATE
    assert runner.calls == []
    event_store.close()
    effect_store.close()


class SequencedGateEvaluator:
    def __init__(self, *evaluations: GateEvaluation) -> None:
        self._evaluations = list(evaluations)
        self.calls: list[tuple[GateProposal, dict[str, Any]]] = []

    def __call__(
        self,
        gate: GateProposal,
        answer: Mapping[str, Any],
    ) -> GateEvaluation:
        self.calls.append((gate, dict(answer)))
        return self._evaluations.pop(0)


def test_user_gate_retry_survives_restart_then_passes_exact_request(tmp_path: Path) -> None:
    database_path = tmp_path / "runtime.db"
    runner = RecordingToolRunner()
    first_evaluator = SequencedGateEvaluator(
        GateEvaluation(
            outcome=GateEvaluationOutcome.RETRY,
            reason="explanation misses the rollback consequence",
        )
    )
    runtime, event_store, effect_store = make_runtime(
        database_path=database_path,
        workspace_root=tmp_path,
        runner=runner,
        gate_evaluator=first_evaluator,
    )
    initialize_run(event_store)
    paused = runtime.submit(make_request(tool_name="delete_file"))
    assert paused.gate_proposal is not None
    reference = paused.gate_proposal.reference

    retry = runtime.submit_gate_answer(
        GateAnswerSubmission.build(
            reference,
            actor="lucas",
            answer={"explanation": "it deletes the file"},
        )
    )

    assert retry.outcome is RuntimeToolOutcome.GATE_RETRY
    assert retry.state.status is RunStatus.AWAITING_GATE
    assert retry.state.active_gate_attempts == 1
    assert retry.state.active_gate_max_attempts == 3
    assert retry.gate_attempt == 1
    assert runner.calls == []
    event_store.close()
    effect_store.close()

    second_evaluator = SequencedGateEvaluator(
        GateEvaluation(
            outcome=GateEvaluationOutcome.PASS,
            reason="risk and rollback consequence are both explained",
        )
    )
    recovered, recovered_events, recovered_effects = make_runtime(
        database_path=database_path,
        workspace_root=tmp_path,
        runner=runner,
        gate_evaluator=second_evaluator,
    )
    assert recovered.load_state("run-1").active_gate_attempts == 1

    passed = recovered.submit_gate_answer(
        GateAnswerSubmission.build(
            reference,
            actor="lucas",
            answer={"explanation": "deletion removes data and may require backup restoration"},
        )
    )

    assert passed.outcome is RuntimeToolOutcome.EXECUTED
    assert passed.gate_evaluation is not None
    assert passed.gate_evaluation.outcome is GateEvaluationOutcome.PASS
    assert passed.gate_attempt == 2
    assert len(runner.calls) == 1
    assert runner.calls[0][0:2] == ("delete_file", {"path": "README.md"})
    assert [event.event_type for event in recovered_events.load("run-1")][-3:] == [
        EventType.GATE_EVALUATED,
        EventType.TOOL_STARTED,
        EventType.TOOL_SUCCEEDED,
    ]
    recovered_events.close()
    recovered_effects.close()


def test_user_gate_retry_exhaustion_blocks_without_tool_effect(tmp_path: Path) -> None:
    runner = RecordingToolRunner()
    evaluator = SequencedGateEvaluator(
        GateEvaluation(outcome=GateEvaluationOutcome.RETRY, reason="missing consequence"),
        GateEvaluation(outcome=GateEvaluationOutcome.RETRY, reason="still incomplete"),
    )
    runtime, event_store, effect_store = make_runtime(
        database_path=tmp_path / "runtime.db",
        workspace_root=tmp_path,
        runner=runner,
        gate_evaluator=evaluator,
        user_gate_max_attempts=2,
    )
    initialize_run(event_store)
    paused = runtime.submit(make_request(tool_name="delete_file"))
    assert paused.gate_proposal is not None
    reference = paused.gate_proposal.reference

    first = runtime.submit_gate_answer(
        GateAnswerSubmission.build(reference, actor="lucas", answer={"answer": "one"})
    )
    second = runtime.submit_gate_answer(
        GateAnswerSubmission.build(reference, actor="lucas", answer={"answer": "two"})
    )

    assert first.outcome is RuntimeToolOutcome.GATE_RETRY
    assert second.outcome is RuntimeToolOutcome.BLOCKED
    assert second.state.status is RunStatus.FAILED
    assert second.state.failure_reason == "attempt limit exhausted: still incomplete"
    assert second.gate_evaluation is not None
    assert second.gate_evaluation.outcome is GateEvaluationOutcome.BLOCK
    assert runner.calls == []
    assert effect_store.load_intent(derive_effect_id(run_id="run-1", tool_call_id="call-1")) is None
    event_store.close()
    effect_store.close()


def test_passed_user_gate_recovers_if_process_stops_before_tool_start(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "runtime.db"
    runner = RecordingToolRunner()
    runtime, event_store, effect_store = make_runtime(
        database_path=database_path,
        workspace_root=tmp_path,
        runner=runner,
    )
    initialize_run(event_store)
    paused = runtime.submit(make_request(tool_name="delete_file"))
    assert paused.gate_proposal is not None
    reference = paused.gate_proposal.reference

    event_store.append(
        ExecutionEvent.build(
            event_id="run-1:4:gate.evaluated",
            run_id="run-1",
            sequence=4,
            event_type=EventType.GATE_EVALUATED,
            occurred_at=NOW,
            payload={
                "actor": "lucas",
                "answer_json": '{"explanation":"understood"}',
                "attempt": 1,
                "max_attempts": 3,
                "outcome": "pass",
                "proposal_digest": reference.proposal_digest,
                "reason": "accepted before simulated restart",
                "revision": reference.revision,
                "tool_call_id": reference.tool_call_id,
            },
        )
    )
    assert runtime.load_state("run-1").status is RunStatus.TOOL_READY
    event_store.close()
    effect_store.close()

    recovered, recovered_events, recovered_effects = make_runtime(
        database_path=database_path,
        workspace_root=tmp_path,
        runner=runner,
    )
    result = recovered.recover("run-1")

    assert result.state.status is RunStatus.VERIFYING
    assert len(runner.calls) == 1
    assert [event.event_type for event in recovered_events.load("run-1")][-2:] == [
        EventType.TOOL_STARTED,
        EventType.TOOL_SUCCEEDED,
    ]
    recovered_events.close()
    recovered_effects.close()
