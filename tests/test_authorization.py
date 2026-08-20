from __future__ import annotations

from pathlib import Path

from agent_runtime_lab.ownership.authorization import (
    AuthorizationContext,
    AuthorizationOutcome,
    ToolRequest,
    WorkspaceBoundary,
    authorize,
)
from agent_runtime_lab.ownership.policy import (
    OwnershipMode,
    OwnershipPolicy,
    OwnershipRule,
)
from agent_runtime_lab.ownership.risk_evaluator import RiskEvaluator, RiskRule
from agent_runtime_lab.tool_registry import ToolDefinition, ToolRegistry


def make_context(
    workspace_root: Path,
    *,
    minimum_mode: OwnershipMode = OwnershipMode.AUTO,
) -> AuthorizationContext:
    registry = ToolRegistry(
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
    evaluator = RiskEvaluator(
        rules=(
            RiskRule(
                tag="core_learning_path",
                path_patterns=("src/model/**",),
            ),
            RiskRule(
                tag="destructive_tool",
                tool_names=frozenset({"delete_file"}),
            ),
        )
    )
    policy = OwnershipPolicy(
        rules=(
            OwnershipRule(
                risk_tags=frozenset({"core_learning_path"}),
                minimum_mode=OwnershipMode.USER_GATE,
                reason="core learning path requires explicit user ownership",
            ),
            OwnershipRule(
                risk_tags=frozenset({"destructive_tool"}),
                minimum_mode=OwnershipMode.USER_GATE,
                reason="destructive tools require explicit user approval",
            ),
        )
    )
    return AuthorizationContext(
        registry=registry,
        workspace=WorkspaceBoundary(workspace_root),
        risk_evaluator=evaluator,
        policy=policy,
        minimum_mode=minimum_mode,
    )


def make_request(
    *,
    tool_name: str = "read_file",
    arguments: dict[str, object] | None = None,
) -> ToolRequest:
    return ToolRequest.build(
        run_id="run-1",
        step_id="step-1",
        tool_call_id="call-1",
        tool_name=tool_name,
        arguments=arguments if arguments is not None else {"path": "README.md"},
    )


def test_registered_auto_request_inside_workspace_is_allowed(tmp_path: Path) -> None:
    decision = authorize(make_request(), make_context(tmp_path))

    assert decision.outcome is AuthorizationOutcome.ALLOW
    assert decision.normalized_paths == ("README.md",)
    assert decision.ownership_decision is not None
    assert decision.ownership_decision.mode is OwnershipMode.AUTO


def test_unknown_tool_is_denied(tmp_path: Path) -> None:
    decision = authorize(
        make_request(tool_name="shell_anything"),
        make_context(tmp_path),
    )

    assert decision.outcome is AuthorizationOutcome.DENY
    assert decision.ownership_decision is None
    assert decision.reasons == ("tool 'shell_anything' is not registered",)


def test_parent_traversal_is_denied(tmp_path: Path) -> None:
    decision = authorize(
        make_request(arguments={"path": "../outside.txt"}),
        make_context(tmp_path),
    )

    assert decision.outcome is AuthorizationOutcome.DENY
    assert "escapes the configured root" in decision.reasons[0]


def test_absolute_path_is_denied_even_when_it_points_inside_workspace(
    tmp_path: Path,
) -> None:
    decision = authorize(
        make_request(arguments={"path": str(tmp_path / "README.md")}),
        make_context(tmp_path),
    )

    assert decision.outcome is AuthorizationOutcome.DENY
    assert "must be relative" in decision.reasons[0]


def test_windows_drive_path_is_denied_portably(tmp_path: Path) -> None:
    decision = authorize(
        make_request(arguments={"path": "C:\\outside.txt"}),
        make_context(tmp_path),
    )

    assert decision.outcome is AuthorizationOutcome.DENY
    assert "must be relative" in decision.reasons[0]


def test_missing_required_path_argument_is_denied(tmp_path: Path) -> None:
    decision = authorize(
        make_request(arguments={}),
        make_context(tmp_path),
    )

    assert decision.outcome is AuthorizationOutcome.DENY
    assert "must be a non-empty string" in decision.reasons[0]


def test_core_path_is_escalated_to_user_gate(tmp_path: Path) -> None:
    decision = authorize(
        make_request(
            tool_name="write_file",
            arguments={"path": "src/model/kv_cache.py", "text": "change"},
        ),
        make_context(tmp_path),
    )

    assert decision.outcome is AuthorizationOutcome.ESCALATE
    assert decision.ownership_decision is not None
    assert decision.ownership_decision.mode is OwnershipMode.USER_GATE
    assert decision.ownership_decision.risk_assessment.derived_tags == frozenset(
        {"core_learning_path"}
    )


def test_concrete_delete_request_is_escalated_despite_any_earlier_auto_plan(
    tmp_path: Path,
) -> None:
    decision = authorize(
        make_request(
            tool_name="delete_file",
            arguments={"path": "notes.txt"},
        ),
        make_context(tmp_path),
    )

    assert decision.outcome is AuthorizationOutcome.ESCALATE
    assert decision.ownership_decision is not None
    assert decision.ownership_decision.mode is OwnershipMode.USER_GATE
    assert "destructive_tool" in decision.ownership_decision.risk_assessment.effective_tags


def test_global_pair_minimum_escalates_an_otherwise_auto_request(
    tmp_path: Path,
) -> None:
    decision = authorize(
        make_request(),
        make_context(tmp_path, minimum_mode=OwnershipMode.PAIR),
    )

    assert decision.outcome is AuthorizationOutcome.ESCALATE
    assert decision.ownership_decision is not None
    assert decision.ownership_decision.mode is OwnershipMode.PAIR


def test_same_request_and_context_produce_identical_decisions(tmp_path: Path) -> None:
    request = make_request()
    context = make_context(tmp_path)

    assert authorize(request, context) == authorize(request, context)
