"""Immutable state derived exclusively from execution events."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from agent_runtime_lab.domain.errors import EventValidationError


class RunStatus(StrEnum):
    NEW = "new"
    CREATED = "created"
    READY = "ready"
    TOOL_PENDING = "tool_pending"
    AWAITING_GATE = "awaiting_gate"
    TOOL_READY = "tool_ready"
    TOOL_RUNNING = "tool_running"
    VERIFYING = "verifying"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATUSES = frozenset({RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED})


@dataclass(frozen=True, slots=True)
class RunState:
    run_id: str
    status: RunStatus = RunStatus.NEW
    next_sequence: int = 0
    turn_index: int = 0
    active_step_id: str | None = None
    active_tool_call_id: str | None = None
    active_gate_proposal_digest: str | None = None
    active_gate_revision: int | None = None
    active_gate_mode: str | None = None
    active_gate_attempts: int = 0
    active_gate_max_attempts: int | None = None
    failure_reason: str | None = None
    applied_event_fingerprints: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not self.run_id:
            raise EventValidationError("run_id must not be empty")
        if self.next_sequence < 0:
            raise EventValidationError("next_sequence must be non-negative")
        if self.turn_index < 0:
            raise EventValidationError("turn_index must be non-negative")

    @classmethod
    def initial(cls, run_id: str) -> RunState:
        return cls(run_id=run_id)

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def fingerprint_for(self, event_id: str) -> str | None:
        for applied_event_id, fingerprint in self.applied_event_fingerprints:
            if applied_event_id == event_id:
                return fingerprint
        return None
