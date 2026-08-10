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
from agent_runtime_lab.domain.errors import GateReferenceMismatchError
from agent_runtime_lab.domain.events import EventType, ExecutionEvent
from agent_runtime_lab.domain.state import RunStatus
from agent_runtime_lab.domain.tool_effects import derive_effect_id
from agent_runtime_lab.durable_tool_executor import DurableToolExecutor
from agent_runtime_lab.ownership.authorization import (
    AuthorizationContext,
    ToolRequest,
    WorkspaceBoundary,
)
from agent_runtime_lab.ownership.gates import GateResolution
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
        authorization_context=make_authorization_context(workspace_root, registry),
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
