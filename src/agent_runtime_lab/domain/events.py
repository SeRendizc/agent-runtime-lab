"""Immutable execution events with canonical JSON payloads."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from agent_runtime_lab.domain.errors import EventValidationError


class EventType(StrEnum):
    RUN_CREATED = "run.created"
    RUN_STARTED = "run.started"
    RUN_PAUSED = "run.paused"
    RUN_RESUMED = "run.resumed"
    RUN_CANCELLED = "run.cancelled"
    TOOL_REQUESTED = "tool.requested"
    TOOL_AUTHORIZED = "tool.authorized"
    TOOL_ESCALATED = "tool.escalated"
    TOOL_DENIED = "tool.denied"
    GATE_APPROVED = "gate.approved"
    GATE_REJECTED = "gate.rejected"
    TOOL_STARTED = "tool.started"
    TOOL_SUCCEEDED = "tool.succeeded"
    TOOL_FAILED = "tool.failed"
    VERIFICATION_SUCCEEDED = "verification.succeeded"
    VERIFICATION_FAILED = "verification.failed"


def _canonical_payload(payload: Mapping[str, Any]) -> str:
    try:
        encoded = json.dumps(
            dict(payload),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise EventValidationError("payload must contain valid JSON values") from exc
    return encoded


@dataclass(frozen=True, slots=True)
class ExecutionEvent:
    event_id: str
    run_id: str
    sequence: int
    event_type: EventType
    occurred_at: datetime
    payload_json: str = "{}"

    def __post_init__(self) -> None:
        if not self.event_id:
            raise EventValidationError("event_id must not be empty")
        if not self.run_id:
            raise EventValidationError("run_id must not be empty")
        if self.sequence < 0:
            raise EventValidationError("sequence must be non-negative")
        if self.occurred_at.utcoffset() is None:
            raise EventValidationError("occurred_at must be timezone-aware")

        try:
            decoded = json.loads(self.payload_json)
        except json.JSONDecodeError as exc:
            raise EventValidationError("payload_json must be valid JSON") from exc
        if not isinstance(decoded, dict):
            raise EventValidationError("payload_json must encode a JSON object")
        object.__setattr__(self, "payload_json", _canonical_payload(decoded))

    @classmethod
    def build(
        cls,
        *,
        event_id: str,
        run_id: str,
        sequence: int,
        event_type: EventType,
        occurred_at: datetime,
        payload: Mapping[str, Any] | None = None,
    ) -> ExecutionEvent:
        return cls(
            event_id=event_id,
            run_id=run_id,
            sequence=sequence,
            event_type=event_type,
            occurred_at=occurred_at,
            payload_json=_canonical_payload(payload or {}),
        )

    @property
    def payload(self) -> dict[str, Any]:
        return json.loads(self.payload_json)

    def fingerprint(self) -> str:
        canonical_event = json.dumps(
            {
                "event_id": self.event_id,
                "event_type": self.event_type.value,
                "occurred_at": self.occurred_at.isoformat(),
                "payload": self.payload,
                "run_id": self.run_id,
                "sequence": self.sequence,
            },
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(canonical_event.encode("utf-8")).hexdigest()
