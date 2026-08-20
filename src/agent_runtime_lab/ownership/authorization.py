"""Execution-time authorization for concrete tool requests."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PureWindowsPath
from typing import Any

from pydantic import BaseModel, ConfigDict

from agent_runtime_lab.domain.errors import EventValidationError, UnknownToolError
from agent_runtime_lab.domain.plan import PlanStep
from agent_runtime_lab.ownership.policy import (
    OwnershipContext,
    OwnershipDecision,
    OwnershipMode,
    OwnershipPolicy,
    classify_step,
)
from agent_runtime_lab.ownership.risk_evaluator import RiskEvaluator
from agent_runtime_lab.tool_registry import ToolRegistry


def _required_text(value: str, field: str) -> None:
    if not value:
        raise EventValidationError(f"{field} must not be empty")


def _canonical_json(value: Mapping[str, Any]) -> str:
    try:
        return json.dumps(
            dict(value),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise EventValidationError("tool request arguments must contain valid JSON values") from exc


@dataclass(frozen=True, slots=True)
class ToolRequest:
    """One immutable concrete tool request awaiting authorization."""

    run_id: str
    step_id: str
    tool_call_id: str
    tool_name: str
    arguments_json: str
    model_action_event_id: str | None = None

    def __post_init__(self) -> None:
        _required_text(self.run_id, "run_id")
        _required_text(self.step_id, "step_id")
        _required_text(self.tool_call_id, "tool_call_id")
        _required_text(self.tool_name, "tool_name")
        if self.model_action_event_id is not None:
            _required_text(self.model_action_event_id, "model_action_event_id")

        try:
            arguments = json.loads(self.arguments_json)
        except json.JSONDecodeError as exc:
            raise EventValidationError("arguments_json must be valid JSON") from exc

        if not isinstance(arguments, dict):
            raise EventValidationError("arguments_json must encode a JSON object")

        object.__setattr__(self, "arguments_json", _canonical_json(arguments))

    @classmethod
    def build(
        cls,
        *,
        run_id: str,
        step_id: str,
        tool_call_id: str,
        tool_name: str,
        arguments: Mapping[str, Any] | None = None,
        model_action_event_id: str | None = None,
    ) -> ToolRequest:
        """Build a request with canonical immutable arguments."""

        return cls(
            run_id=run_id,
            step_id=step_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            arguments_json=_canonical_json(arguments or {}),
            model_action_event_id=model_action_event_id,
        )

    @property
    def arguments(self) -> dict[str, Any]:
        """Return a fresh decoded copy of the request arguments."""

        return json.loads(self.arguments_json)


class AuthorizationOutcome(StrEnum):
    """Runtime action for one concrete tool request."""

    ALLOW = "allow"
    DENY = "deny"
    ESCALATE = "escalate"


class AuthorizationDecision(BaseModel):
    """Explainable, deterministic authorization result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    step_id: str
    tool_call_id: str
    tool_name: str
    outcome: AuthorizationOutcome
    normalized_paths: tuple[str, ...] = ()
    ownership_decision: OwnershipDecision | None = None
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WorkspaceBoundary:
    """Resolve relative tool paths inside one trusted workspace root."""

    root: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", self.root.resolve())

    def normalize(self, target: str) -> str:
        """Return a normalized relative path or reject a boundary escape."""

        if not target:
            raise ValueError("workspace path must not be empty")

        portable_target = target.replace("\\", "/")
        if portable_target.startswith("/") or PureWindowsPath(target).drive:
            raise ValueError("workspace path must be relative")

        resolved_target = (self.root / portable_target).resolve()
        if not resolved_target.is_relative_to(self.root):
            raise ValueError("workspace path escapes the configured root")

        return resolved_target.relative_to(self.root).as_posix() or "."


@dataclass(frozen=True, slots=True)
class AuthorizationContext:
    """Trusted Runtime inputs used to authorize concrete requests."""

    registry: ToolRegistry
    workspace: WorkspaceBoundary
    risk_evaluator: RiskEvaluator
    policy: OwnershipPolicy
    minimum_mode: OwnershipMode = OwnershipMode.AUTO


def _decision(
    request: ToolRequest,
    *,
    outcome: AuthorizationOutcome,
    reasons: tuple[str, ...],
    normalized_paths: tuple[str, ...] = (),
    ownership_decision: OwnershipDecision | None = None,
) -> AuthorizationDecision:
    return AuthorizationDecision(
        run_id=request.run_id,
        step_id=request.step_id,
        tool_call_id=request.tool_call_id,
        tool_name=request.tool_name,
        outcome=outcome,
        normalized_paths=normalized_paths,
        ownership_decision=ownership_decision,
        reasons=reasons,
    )


def authorize(
    request: ToolRequest,
    context: AuthorizationContext,
) -> AuthorizationDecision:
    """Re-check a concrete request using only trusted Runtime policy."""

    try:
        definition = context.registry.resolve(request.tool_name)
    except UnknownToolError:
        return _decision(
            request,
            outcome=AuthorizationOutcome.DENY,
            reasons=(f"tool {request.tool_name!r} is not registered",),
        )

    arguments = request.arguments
    normalized_paths: list[str] = []

    for argument_name in definition.path_argument_names:
        target = arguments.get(argument_name)
        if not isinstance(target, str) or not target:
            return _decision(
                request,
                outcome=AuthorizationOutcome.DENY,
                reasons=(f"path argument {argument_name!r} must be a non-empty string",),
            )

        try:
            normalized_paths.append(context.workspace.normalize(target))
        except ValueError as exc:
            return _decision(
                request,
                outcome=AuthorizationOutcome.DENY,
                reasons=(f"invalid path argument {argument_name!r}: {exc}",),
            )

    execution_step = PlanStep(
        step_id=request.step_id,
        title=f"Execute {request.tool_name}",
        description="Concrete tool request evaluated at execution time.",
        affected_paths=tuple(normalized_paths),
        proposed_tools=(request.tool_name,),
    )
    ownership_decision = classify_step(
        execution_step,
        OwnershipContext(
            risk_evaluator=context.risk_evaluator,
            policy=context.policy,
            minimum_mode=context.minimum_mode,
        ),
    )

    if ownership_decision.mode is not OwnershipMode.AUTO:
        return _decision(
            request,
            outcome=AuthorizationOutcome.ESCALATE,
            normalized_paths=tuple(normalized_paths),
            ownership_decision=ownership_decision,
            reasons=(
                f"request requires {ownership_decision.mode.value} ownership",
                *ownership_decision.reasons,
            ),
        )

    return _decision(
        request,
        outcome=AuthorizationOutcome.ALLOW,
        normalized_paths=tuple(normalized_paths),
        ownership_decision=ownership_decision,
        reasons=("registered request is inside the workspace and requires auto mode",),
    )
