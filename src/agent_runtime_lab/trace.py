"""Versioned, deterministic traces derived from authoritative Runtime Events."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from agent_runtime_lab.domain.events import EventType, ExecutionEvent
from agent_runtime_lab.domain.reducer import reduce
from agent_runtime_lab.domain.state import RunState

TRACE_SCHEMA_VERSION = 1

_SAFE_PAYLOAD_FIELDS = frozenset(
    {
        "action_type",
        "attempt",
        "completed_steps",
        "effect_id",
        "gate_mode",
        "invocation_id",
        "max_attempts",
        "max_steps",
        "mode",
        "model_action_event_id",
        "outcome",
        "proposal_digest",
        "revision",
        "scope",
        "step_id",
        "tool_call_id",
        "tool_name",
        "turn_index",
    }
)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_payload_metadata(event: ExecutionEvent) -> dict[str, Any]:
    payload = event.payload
    metadata: dict[str, Any] = {}
    for field_name in sorted(_SAFE_PAYLOAD_FIELDS):
        value = payload.get(field_name)
        if value is None or isinstance(value, (str, int, float, bool)):
            if field_name in payload:
                metadata[field_name] = value
    return metadata


@dataclass(frozen=True, slots=True)
class TraceEvent:
    """One Event plus its deterministic state transition and safe indexes."""

    sequence: int
    event_id: str
    event_type: str
    occurred_at: str
    state_before: str
    state_after: str
    payload_sha256: str
    event_fingerprint: str
    metadata_json: str

    @property
    def metadata(self) -> dict[str, Any]:
        return json.loads(self.metadata_json)

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_fingerprint": self.event_fingerprint,
            "event_id": self.event_id,
            "event_type": self.event_type,
            "metadata": self.metadata,
            "occurred_at": self.occurred_at,
            "payload_sha256": self.payload_sha256,
            "sequence": self.sequence,
            "state_after": self.state_after,
            "state_before": self.state_before,
        }


@dataclass(frozen=True, slots=True)
class TraceMetrics:
    """Small stable metric surface for downstream evaluation."""

    event_count: int
    model_action_count: int
    tool_request_count: int
    gate_escalation_count: int
    verification_success_count: int
    verification_failure_count: int
    runtime_steps: int
    duration_ms: int

    def as_dict(self) -> dict[str, int]:
        return {
            "duration_ms": self.duration_ms,
            "event_count": self.event_count,
            "gate_escalation_count": self.gate_escalation_count,
            "model_action_count": self.model_action_count,
            "runtime_steps": self.runtime_steps,
            "tool_request_count": self.tool_request_count,
            "verification_failure_count": self.verification_failure_count,
            "verification_success_count": self.verification_success_count,
        }


@dataclass(frozen=True, slots=True)
class RunTrace:
    """A disposable trace view that can always be rebuilt from Events."""

    run_id: str
    final_status: str
    records: tuple[TraceEvent, ...]
    metrics: TraceMetrics
    schema_version: int = TRACE_SCHEMA_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "final_status": self.final_status,
            "metrics": self.metrics.as_dict(),
            "records": [record.as_dict() for record in self.records],
            "run_id": self.run_id,
            "schema_version": self.schema_version,
        }

    def canonical_json(self) -> str:
        return _canonical_json(self.as_dict())

    def digest(self) -> str:
        return _sha256_text(self.canonical_json())


def build_run_trace(run_id: str, events: Iterable[ExecutionEvent]) -> RunTrace:
    """Replay Events once and emit a deterministic redacted Trace."""

    ordered_events = tuple(events)
    state = RunState.initial(run_id)
    records: list[TraceEvent] = []
    for event in ordered_events:
        state_before = state.status.value
        state = reduce(state, event)
        records.append(
            TraceEvent(
                sequence=event.sequence,
                event_id=event.event_id,
                event_type=event.event_type.value,
                occurred_at=event.occurred_at.isoformat(),
                state_before=state_before,
                state_after=state.status.value,
                payload_sha256=_sha256_text(event.payload_json),
                event_fingerprint=event.fingerprint(),
                metadata_json=_canonical_json(_safe_payload_metadata(event)),
            )
        )

    duration_ms = 0
    if len(ordered_events) > 1:
        duration = ordered_events[-1].occurred_at - ordered_events[0].occurred_at
        duration_ms = max(0, int(duration.total_seconds() * 1000))
    event_types = tuple(event.event_type for event in ordered_events)
    metrics = TraceMetrics(
        event_count=len(ordered_events),
        model_action_count=event_types.count(EventType.MODEL_ACTION_PROPOSED),
        tool_request_count=event_types.count(EventType.TOOL_REQUESTED),
        gate_escalation_count=event_types.count(EventType.TOOL_ESCALATED),
        verification_success_count=event_types.count(EventType.VERIFICATION_SUCCEEDED),
        verification_failure_count=event_types.count(EventType.VERIFICATION_FAILED),
        runtime_steps=state.turn_index,
        duration_ms=duration_ms,
    )
    return RunTrace(
        run_id=run_id,
        final_status=state.status.value,
        records=tuple(records),
        metrics=metrics,
    )
