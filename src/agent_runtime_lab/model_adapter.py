"""Untrusted Model Actions separated from trusted Runtime execution identity."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, TypeAlias

from agent_runtime_lab.domain.errors import (
    InvalidTransitionError,
    ModelActionValidationError,
    ModelAdapterExhaustedError,
)
from agent_runtime_lab.domain.state import RunStatus
from agent_runtime_lab.ownership.authorization import ToolRequest


def _required_text(value: str, field: str) -> None:
    if not isinstance(value, str) or not value:
        raise ModelActionValidationError(f"{field} must be a non-empty string")


def _canonical_object_json(value: Mapping[str, Any], field: str) -> str:
    try:
        return json.dumps(
            dict(value),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ModelActionValidationError(f"{field} must contain valid JSON object values") from exc


def _validate_object_json(value: str, field: str) -> str:
    try:
        decoded = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ModelActionValidationError(f"{field} must be valid JSON") from exc
    if not isinstance(decoded, dict):
        raise ModelActionValidationError(f"{field} must encode a JSON object")
    return _canonical_object_json(decoded, field)


@dataclass(frozen=True, slots=True)
class ModelInput:
    """Immutable Runtime-owned context exposed to a Model Adapter."""

    run_id: str
    step_id: str
    turn_index: int
    state_status: RunStatus
    observation_json: str = "{}"

    def __post_init__(self) -> None:
        _required_text(self.run_id, "run_id")
        _required_text(self.step_id, "step_id")
        if not isinstance(self.turn_index, int) or isinstance(self.turn_index, bool):
            raise ModelActionValidationError("turn_index must be an integer")
        if self.turn_index < 0:
            raise ModelActionValidationError("turn_index must be non-negative")
        if not isinstance(self.state_status, RunStatus):
            raise ModelActionValidationError("state_status must be a RunStatus")
        object.__setattr__(
            self,
            "observation_json",
            _validate_object_json(self.observation_json, "observation_json"),
        )

    @classmethod
    def build(
        cls,
        *,
        run_id: str,
        step_id: str,
        turn_index: int,
        state_status: RunStatus,
        observation: Mapping[str, Any] | None = None,
    ) -> ModelInput:
        return cls(
            run_id=run_id,
            step_id=step_id,
            turn_index=turn_index,
            state_status=state_status,
            observation_json=_canonical_object_json(
                observation or {},
                "observation",
            ),
        )

    @property
    def observation(self) -> dict[str, Any]:
        """Return a fresh decoded copy of the previous trusted observation."""

        return json.loads(self.observation_json)


@dataclass(frozen=True, slots=True)
class ToolCallAction:
    """An untrusted model proposal to invoke one named tool."""

    tool_call_id: str
    tool_name: str
    arguments_json: str

    def __post_init__(self) -> None:
        _required_text(self.tool_call_id, "tool_call_id")
        _required_text(self.tool_name, "tool_name")
        object.__setattr__(
            self,
            "arguments_json",
            _validate_object_json(self.arguments_json, "arguments_json"),
        )

    @classmethod
    def build(
        cls,
        *,
        tool_call_id: str,
        tool_name: str,
        arguments: Mapping[str, Any] | None = None,
    ) -> ToolCallAction:
        return cls(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            arguments_json=_canonical_object_json(arguments or {}, "arguments"),
        )

    @property
    def arguments(self) -> dict[str, Any]:
        """Return a fresh decoded copy of the proposed arguments."""

        return json.loads(self.arguments_json)


@dataclass(frozen=True, slots=True)
class FinalAnswerAction:
    """An untrusted model proposal to return text, not a completion Event."""

    answer: str

    def __post_init__(self) -> None:
        _required_text(self.answer, "answer")


ModelAction: TypeAlias = ToolCallAction | FinalAnswerAction


class ModelAdapter(Protocol):
    """Return one proposed Action without receiving Runtime mutation capability."""

    def next_action(self, context: ModelInput) -> ModelAction:
        """Propose one Action for the immutable Runtime-owned context."""


@dataclass(frozen=True, slots=True)
class StaticModelAdapter:
    """Deterministic scripted adapter indexed only by the supplied turn."""

    actions: tuple[ModelAction, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.actions, tuple):
            raise ModelActionValidationError("static adapter actions must be an immutable tuple")
        for action in self.actions:
            if not isinstance(action, (ToolCallAction, FinalAnswerAction)):
                raise ModelActionValidationError("static adapter contains unsupported action")

    def next_action(self, context: ModelInput) -> ModelAction:
        try:
            return self.actions[context.turn_index]
        except IndexError as exc:
            raise ModelAdapterExhaustedError(
                f"static adapter has no action for turn {context.turn_index}"
            ) from exc


def request_model_action(adapter: ModelAdapter, context: ModelInput) -> ModelAction:
    """Validate one adapter result before it crosses into Runtime control."""

    action = adapter.next_action(context)
    if not isinstance(action, (ToolCallAction, FinalAnswerAction)):
        raise ModelActionValidationError(
            f"model adapter returned unsupported action {type(action).__name__}"
        )
    return action


def tool_request_from_action(
    context: ModelInput,
    action: ModelAction,
) -> ToolRequest:
    """Compile a tool proposal using trusted Runtime run and step identity."""

    if context.state_status is not RunStatus.READY:
        raise InvalidTransitionError(
            f"tool action compilation requires ready, got {context.state_status.value}"
        )
    if not isinstance(action, ToolCallAction):
        raise ModelActionValidationError("tool request compilation requires a tool-call action")
    return ToolRequest(
        run_id=context.run_id,
        step_id=context.step_id,
        tool_call_id=action.tool_call_id,
        tool_name=action.tool_name,
        arguments_json=action.arguments_json,
    )
