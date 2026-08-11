"""Run a sanitized temporary-workspace demonstration of R4a file tools."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from agent_runtime_lab.authorized_tool_runtime import AuthorizedToolRuntime
from agent_runtime_lab.domain.errors import UnsafeToolRetryError
from agent_runtime_lab.domain.events import EventType, ExecutionEvent
from agent_runtime_lab.domain.tool_effects import ToolIntent, derive_effect_id
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

DEMO_TIME = datetime(2026, 8, 11, 0, 0, tzinfo=UTC)


def _make_runtime(
    database_path: Path,
    workspace: Path,
) -> tuple[AuthorizedToolRuntime, SQLiteEventStore, SQLiteToolEffectStore]:
    registry = make_restricted_file_registry()
    boundary = WorkspaceBoundary(workspace)
    events = SQLiteEventStore(database_path)
    effects = SQLiteToolEffectStore(database_path)
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
        clock=lambda: DEMO_TIME,
    )
    return runtime, events, effects


def _initialize_run(events: SQLiteEventStore, run_id: str) -> None:
    for sequence, event_type in enumerate((EventType.RUN_CREATED, EventType.RUN_STARTED)):
        events.append(
            ExecutionEvent.build(
                event_id=f"{run_id}:{sequence}:{event_type.value}",
                run_id=run_id,
                sequence=sequence,
                event_type=event_type,
                occurred_at=DEMO_TIME,
            )
        )


def _request(
    run_id: str,
    tool_name: str,
    arguments: dict[str, str],
) -> ToolRequest:
    return ToolRequest.build(
        run_id=run_id,
        step_id="demo-step",
        tool_call_id="demo-call",
        tool_name=tool_name,
        arguments=arguments,
    )


def _event_values(events: SQLiteEventStore, run_id: str) -> list[str]:
    return [event.event_type.value for event in events.load(run_id)]


def run_demo() -> dict[str, Any]:
    """Execute R4a scenarios and return non-sensitive structured evidence."""

    with TemporaryDirectory(prefix="agent-runtime-r4a-") as temporary_root:
        root = Path(temporary_root)
        workspace = root / "workspace"
        workspace.mkdir()
        database_path = root / "runtime.db"
        workspace.joinpath("input.txt").write_text(
            "temporary demo content",
            encoding="utf-8",
        )
        workspace.joinpath("delete-me.txt").write_text("disposable", encoding="utf-8")
        summary: dict[str, Any] = {}

        runtime, events, effects = _make_runtime(database_path, workspace)
        _initialize_run(events, "demo-read")
        read_result = runtime.submit(_request("demo-read", "read_file", {"path": "input.txt"}))
        assert read_result.receipt is not None
        summary["read_file"] = {
            "events": _event_values(events, "demo-read"),
            "receipt_outcome": read_result.receipt.outcome.value,
            "path": read_result.receipt.output["path"],
            "bytes": read_result.receipt.output["bytes"],
            "sha256": read_result.receipt.output["sha256"],
        }
        events.close()
        effects.close()

        runtime, events, effects = _make_runtime(database_path, workspace)
        _initialize_run(events, "demo-write")
        paused_write = runtime.submit(
            _request(
                "demo-write",
                "write_file",
                {"path": "output.txt", "content": "temporary demo content"},
            )
        )
        assert paused_write.gate_proposal is not None
        write_result = runtime.resolve_gate(
            GateResolution.approve(
                paused_write.gate_proposal.reference,
                actor="demo-reviewer",
                reason="reviewed exact temporary output",
            )
        )
        assert write_result.receipt is not None
        summary["write_file"] = {
            "events": _event_values(events, "demo-write"),
            "receipt_outcome": write_result.receipt.outcome.value,
            "path": write_result.receipt.output["path"],
            "bytes": write_result.receipt.output["bytes"],
            "sha256": write_result.receipt.output["sha256"],
            "replaced": write_result.receipt.output["replaced"],
        }
        events.close()
        effects.close()

        runtime, events, effects = _make_runtime(database_path, workspace)
        _initialize_run(events, "demo-delete")
        paused_delete = runtime.submit(
            _request("demo-delete", "delete_file", {"path": "delete-me.txt"})
        )
        assert paused_delete.gate_proposal is not None
        delete_result = runtime.submit_gate_answer(
            GateAnswerSubmission.build(
                paused_delete.gate_proposal.reference,
                actor="demo-operator",
                answer={
                    "tool_name": "delete_file",
                    "path": "delete-me.txt",
                    "risk_explanation": (
                        "Deleting this temporary file permanently removes selected demo data."
                    ),
                    "refuse": False,
                },
            )
        )
        assert delete_result.receipt is not None
        summary["delete_file"] = {
            "events": _event_values(events, "demo-delete"),
            "receipt_outcome": delete_result.receipt.outcome.value,
            "path": delete_result.receipt.output["path"],
            "deleted": delete_result.receipt.output["deleted"],
        }
        events.close()
        effects.close()

        runtime, events, effects = _make_runtime(database_path, workspace)
        _initialize_run(events, "demo-escape")
        escape_request = _request(
            "demo-escape",
            "write_file",
            {"path": "../outside.txt", "content": "blocked"},
        )
        escape_result = runtime.submit(escape_request)
        summary["denied_escape"] = {
            "outcome": escape_result.outcome.value,
            "intent_persisted": effects.load_intent(
                derive_effect_id(
                    run_id=escape_request.run_id,
                    tool_call_id=escape_request.tool_call_id,
                )
            )
            is not None,
            "outside_changed": root.joinpath("outside.txt").exists(),
        }
        events.close()
        effects.close()

        recovery_intent = ToolIntent.build(
            run_id="demo-recovery",
            tool_call_id="demo-call",
            tool_name="write_file",
            arguments={"path": "unknown.txt", "content": "not retried"},
        )
        with SQLiteToolEffectStore(database_path) as recovery_store:
            recovery_store.save_intent(recovery_intent)
            recovery_executor = DurableToolExecutor(
                store=recovery_store,
                runner=RestrictedFileToolRunner(WorkspaceBoundary(workspace)),
                registry=make_restricted_file_registry(),
            )
            try:
                recovery_executor.execute(intent=recovery_intent)
            except UnsafeToolRetryError as exc:
                recovery_error = type(exc).__name__
            else:
                raise AssertionError("non-idempotent recovery unexpectedly retried")
        summary["fail_closed_recovery"] = {
            "tool_name": "write_file",
            "automatic_retry": False,
            "error_type": recovery_error,
            "target_exists": workspace.joinpath("unknown.txt").exists(),
        }

        return summary


def main() -> None:
    """Print the sanitized R4a demonstration as deterministic JSON."""

    print(json.dumps(run_demo(), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
