"""Durable identity and recovery contracts for external tool effects."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from agent_runtime_lab.domain.errors import EventValidationError


class ToolOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class RecoveryDecision(StrEnum):
    COMPLETED = "completed"
    SAFE_RETRY = "safe_retry"
    UNKNOWN = "unknown"


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
        raise EventValidationError("tool effect data must contain valid JSON values") from exc


def derive_effect_id(*, run_id: str, tool_call_id: str) -> str:
    """Derive one stable identity for one logical tool effect."""
    _required_text(run_id, "run_id")
    _required_text(tool_call_id, "tool_call_id")

    identity_json = json.dumps(
        {
            "run_id": run_id,
            "tool_call_id": tool_call_id,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(identity_json.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ToolIntent:
    effect_id: str
    run_id: str
    tool_call_id: str
    tool_name: str
    arguments_json: str

    def __post_init__(self) -> None:
        _required_text(self.effect_id, "effect_id")
        _required_text(self.run_id, "run_id")
        _required_text(self.tool_call_id, "tool_call_id")
        _required_text(self.tool_name, "tool_name")

        expected_effect_id = derive_effect_id(
            run_id=self.run_id,
            tool_call_id=self.tool_call_id,
        )
        if self.effect_id != expected_effect_id:
            raise EventValidationError("effect_id does not match run_id and tool_call_id")

        try:
            arguments = json.loads(self.arguments_json)
        except json.JSONDecodeError as exc:
            raise EventValidationError("arguments_json must be valid JSON") from exc

        if not isinstance(arguments, dict):
            raise EventValidationError("arguments_json must encode a JSON object")

        object.__setattr__(
            self,
            "arguments_json",
            _canonical_json(arguments),
        )

    @classmethod
    def build(
        cls,
        *,
        run_id: str,
        tool_call_id: str,
        tool_name: str,
        arguments: Mapping[str, Any] | None = None,
    ) -> ToolIntent:
        return cls(
            effect_id=derive_effect_id(
                run_id=run_id,
                tool_call_id=tool_call_id,
            ),
            run_id=run_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            arguments_json=_canonical_json(arguments or {}),
        )

    @property
    def arguments(self) -> dict[str, Any]:
        return json.loads(self.arguments_json)

    @property
    def idempotency_key(self) -> str:
        return self.effect_id


@dataclass(frozen=True, slots=True)
class ToolReceipt:
    effect_id: str
    outcome: ToolOutcome
    output_json: str

    def __post_init__(self) -> None:
        _required_text(self.effect_id, "effect_id")

        try:
            output = json.loads(self.output_json)
        except json.JSONDecodeError as exc:
            raise EventValidationError("output_json must be valid JSON") from exc

        if not isinstance(output, dict):
            raise EventValidationError("output_json must encode a JSON object")

        object.__setattr__(
            self,
            "output_json",
            _canonical_json(output),
        )

    @classmethod
    def build(
        cls,
        *,
        effect_id: str,
        outcome: ToolOutcome,
        output: Mapping[str, Any] | None = None,
    ) -> ToolReceipt:
        return cls(
            effect_id=effect_id,
            outcome=outcome,
            output_json=_canonical_json(output or {}),
        )

    @property
    def output(self) -> dict[str, Any]:
        return json.loads(self.output_json)


def decide_recovery(
    *,
    intent: ToolIntent,
    receipt: ToolReceipt | None,
    retry_is_idempotent: bool,
) -> RecoveryDecision:
    """Choose a fail-closed recovery action for one persisted tool intent."""
    if receipt is not None:
        if receipt.effect_id != intent.effect_id:
            raise EventValidationError("receipt effect_id does not match intent effect_id")
        return RecoveryDecision.COMPLETED

    if retry_is_idempotent:
        return RecoveryDecision.SAFE_RETRY

    return RecoveryDecision.UNKNOWN
