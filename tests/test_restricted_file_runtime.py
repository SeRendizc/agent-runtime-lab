from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from agent_runtime_lab.authorized_tool_runtime import (
    AuthorizedToolRuntime,
    RuntimeToolOutcome,
)
from agent_runtime_lab.domain.events import EventType, ExecutionEvent
from agent_runtime_lab.domain.state import RunStatus
from agent_runtime_lab.domain.tool_effects import derive_effect_id
from agent_runtime_lab.durable_tool_executor import DurableToolExecutor
from agent_runtime_lab.ownership.authorization import (
    AuthorizationContext,
    ToolRequest,
    WorkspaceBoundary,
)
from agent_runtime_lab.ownership.gates import GateAnswerSubmission, GateResolution
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

NOW = datetime(2026, 8, 11, 0, 0, tzinfo=UTC)


def make_runtime(
    database_path: Path,
    workspace_root: Path,
) -> tuple[AuthorizedToolRuntime, SQLiteEventStore, SQLiteToolEffectStore]:
    registry = make_restricted_file_registry()
    boundary = WorkspaceBoundary(workspace_root)
    event_store = SQLiteEventStore(database_path)
    effect_store = SQLiteToolEffectStore(database_path)
    runtime = AuthorizedToolRuntime(
        event_store=event_store,
        executor=DurableToolExecutor(
            store=effect_store,
            runner=RestrictedFileToolRunner(boundary),
            registry=registry,
        ),
        authorization_context=AuthorizationContext(
            registry=registry,
            workspace=boundary,
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
        ),
        clock=lambda: NOW,
    )
    return runtime, event_store, effect_store


def initialize_run(event_store: SQLiteEventStore, run_id: str) -> None:
    for sequence, event_type in enumerate((EventType.RUN_CREATED, EventType.RUN_STARTED)):
        event_store.append(
            ExecutionEvent.build(
                event_id=f"{run_id}:{sequence}:{event_type.value}",
                run_id=run_id,
                sequence=sequence,
                event_type=event_type,
                occurred_at=NOW,
            )
        )


def make_request(
    *,
    run_id: str,
    tool_name: str,
    arguments: dict[str, str],
) -> ToolRequest:
    return ToolRequest.build(
        run_id=run_id,
        step_id="step-1",
        tool_call_id="call-1",
        tool_name=tool_name,
        arguments=arguments,
    )


def test_auto_read_executes_real_effect_and_records_ordered_events(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    workspace.joinpath("notes.txt").write_text("hello", encoding="utf-8")
    runtime, events, effects = make_runtime(tmp_path / "runtime.db", workspace)
    initialize_run(events, "run-read")

    result = runtime.submit(
        make_request(
            run_id="run-read",
            tool_name="read_file",
            arguments={"path": "notes.txt"},
        )
    )

    assert result.outcome is RuntimeToolOutcome.EXECUTED
    assert result.state.status is RunStatus.VERIFYING
    assert result.receipt is not None
    assert result.receipt.output["content"] == "hello"
    assert [event.event_type for event in events.load("run-read")][-4:] == [
        EventType.TOOL_REQUESTED,
        EventType.TOOL_AUTHORIZED,
        EventType.TOOL_STARTED,
        EventType.TOOL_SUCCEEDED,
    ]
    events.close()
    effects.close()


def test_pair_write_has_no_effect_until_exact_approval_after_restart(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    database_path = tmp_path / "runtime.db"
    runtime, events, effects = make_runtime(database_path, workspace)
    initialize_run(events, "run-write")
    paused = runtime.submit(
        make_request(
            run_id="run-write",
            tool_name="write_file",
            arguments={"path": "notes.txt", "content": "persisted-content"},
        )
    )

    assert paused.outcome is RuntimeToolOutcome.AWAITING_GATE
    assert paused.gate_proposal is not None
    assert not workspace.joinpath("notes.txt").exists()
    reference = paused.gate_proposal.reference
    events.close()
    effects.close()

    recovered, recovered_events, recovered_effects = make_runtime(database_path, workspace)
    completed = recovered.resolve_gate(
        GateResolution.approve(reference, actor="reviewer", reason="exact proposal reviewed")
    )

    assert completed.state.status is RunStatus.VERIFYING
    assert workspace.joinpath("notes.txt").read_text(encoding="utf-8") == "persisted-content"
    recovered_events.close()
    recovered_effects.close()


def test_user_gate_retry_has_no_effect_then_exact_answer_deletes(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "victim.txt"
    target.write_text("temporary", encoding="utf-8")
    runtime, events, effects = make_runtime(tmp_path / "runtime.db", workspace)
    initialize_run(events, "run-delete")
    paused = runtime.submit(
        make_request(
            run_id="run-delete",
            tool_name="delete_file",
            arguments={"path": "victim.txt"},
        )
    )
    assert paused.gate_proposal is not None

    retry = runtime.submit_gate_answer(
        GateAnswerSubmission.build(
            paused.gate_proposal.reference,
            actor="operator",
            answer={"risk_explanation": "incomplete"},
        )
    )
    assert retry.outcome is RuntimeToolOutcome.GATE_RETRY
    assert target.exists()

    completed = runtime.submit_gate_answer(
        GateAnswerSubmission.build(
            paused.gate_proposal.reference,
            actor="operator",
            answer={
                "tool_name": "delete_file",
                "path": "victim.txt",
                "risk_explanation": (
                    "Deleting this temporary file permanently removes the selected test data."
                ),
                "refuse": False,
            },
        )
    )
    assert completed.outcome is RuntimeToolOutcome.EXECUTED
    assert not target.exists()
    events.close()
    effects.close()


def test_user_gate_refusal_blocks_without_delete_effect(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "victim.txt"
    target.write_text("temporary", encoding="utf-8")
    runtime, events, effects = make_runtime(tmp_path / "runtime.db", workspace)
    initialize_run(events, "run-blocked-delete")
    paused = runtime.submit(
        make_request(
            run_id="run-blocked-delete",
            tool_name="delete_file",
            arguments={"path": "victim.txt"},
        )
    )
    assert paused.gate_proposal is not None

    blocked = runtime.submit_gate_answer(
        GateAnswerSubmission.build(
            paused.gate_proposal.reference,
            actor="operator",
            answer={"refuse": True},
        )
    )

    assert blocked.outcome is RuntimeToolOutcome.BLOCKED
    assert blocked.state.status is RunStatus.FAILED
    assert target.read_text(encoding="utf-8") == "temporary"
    events.close()
    effects.close()


def test_escape_is_denied_before_intent_and_has_no_effect(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime, events, effects = make_runtime(tmp_path / "runtime.db", workspace)
    initialize_run(events, "run-escape")
    request = make_request(
        run_id="run-escape",
        tool_name="write_file",
        arguments={"path": "../outside.txt", "content": "blocked"},
    )

    denied = runtime.submit(request)

    assert denied.outcome is RuntimeToolOutcome.DENIED
    assert (
        effects.load_intent(
            derive_effect_id(run_id=request.run_id, tool_call_id=request.tool_call_id)
        )
        is None
    )
    assert not tmp_path.joinpath("outside.txt").exists()
    events.close()
    effects.close()
