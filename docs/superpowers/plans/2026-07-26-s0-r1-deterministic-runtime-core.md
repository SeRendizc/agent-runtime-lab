# S0 + R1 Deterministic Runtime Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish an importable Python 3.11 project and implement the deterministic Event, State, Reducer, and Replay contracts that every subsequent Agent Runtime subsystem will depend on.

**Architecture:** This slice is a pure, dependency-free reliability kernel. Immutable execution events are validated and folded by a pure reducer into immutable run state; replay uses the same reducer and rejects gaps, cross-run events, illegal transitions, terminal-state mutations, and conflicting duplicate delivery. Persistence, tools, sandboxing, workers, providers, eval integration, and CodeOwnership are outside this plan and will consume these contracts through later plans.

**Tech Stack:** Python 3.11, standard-library dataclasses/enums/json/hashlib/datetime, setuptools `src/` layout, pytest, Ruff, PowerShell, GitHub Actions-ready commands.

---

## Scope and file map

This plan deliberately implements only S0 and R1 from the approved design.
It produces working, testable software on its own.

Files created or modified:

```text
pyproject.toml
    Setuptools src-layout discovery and existing pytest/Ruff configuration.

src/agent_runtime_lab/__init__.py
    Public package version and stable R1 exports.

src/agent_runtime_lab/domain/__init__.py
    Domain-level exports.

src/agent_runtime_lab/domain/errors.py
    Typed contract errors used by event validation and reduction.

src/agent_runtime_lab/domain/events.py
    Immutable, canonical, fingerprintable ExecutionEvent and EventType.

src/agent_runtime_lab/domain/state.py
    Immutable RunState, RunStatus, terminal statuses, duplicate lookup.

src/agent_runtime_lab/domain/reducer.py
    Pure state transition function and lifecycle invariants.

src/agent_runtime_lab/domain/replay.py
    Ordered event replay using the same reducer.

tests/test_package.py
    Packaging and public API smoke test.

tests/domain/test_events.py
    Event validation, canonical payload, and fingerprint tests.

tests/domain/test_state.py
    Initial-state and terminal-status tests.

tests/domain/test_reducer.py
    Lifecycle, authorization, duplicate, sequence, terminal, and cancel tests.

tests/domain/test_replay.py
    Full replay, duplicate delivery, and corrupt-order tests.

README.md
    Evidence-backed current R1 capability boundary.

docs/progress.md
    Verified S0/R1 result, commands, learning gate, and next milestone.
```

The implementation must not modify or stage `.idea/`.

### Task 1: Establish the importable `src/` package and isolated interpreter

**Files:**
- Create: `src/agent_runtime_lab/__init__.py`
- Create: `tests/test_package.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Write the failing package smoke test**

Create `tests/test_package.py`:

```python
import agent_runtime_lab


def test_package_exposes_version() -> None:
    assert agent_runtime_lab.__version__ == "0.1.0"
```

- [ ] **Step 2: Run the test with the known Python 3.11 test interpreter**

Run:

```powershell
& '..\agent-eval-lab\.venv\Scripts\python.exe' -m pytest tests/test_package.py -v
```

Expected: collection fails with `ModuleNotFoundError: No module named 'agent_runtime_lab'`.

- [ ] **Step 3: Add the package entry point**

Create `src/agent_runtime_lab/__init__.py`:

```python
"""Reliable execution primitives for agentic workloads."""

__version__ = "0.1.0"
```

- [ ] **Step 4: Replace the empty setuptools package list**

In `pyproject.toml`, replace:

```toml
[tool.setuptools]
packages = []
```

with:

```toml
[tool.setuptools]
package-dir = {"" = "src"}

[tool.setuptools.packages.find]
where = ["src"]
```

- [ ] **Step 5: Create the Runtime-specific virtual environment**

Run:

```powershell
& 'C:\Users\ASUS\anaconda3\python.exe' -m venv .venv
& .\.venv\Scripts\python.exe --version
```

Expected: `Python 3.11.5`.

- [ ] **Step 6: Install the project and development dependencies**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Expected: exit code `0` and an editable installation of `agent-runtime-lab==0.1.0`.

- [ ] **Step 7: Verify import, test, and lint**

Run:

```powershell
& .\.venv\Scripts\python.exe -c "import agent_runtime_lab; print(agent_runtime_lab.__version__)"
& .\.venv\Scripts\python.exe -m pytest tests/test_package.py -v
& .\.venv\Scripts\python.exe -m ruff check src tests
& .\.venv\Scripts\python.exe -m ruff format --check src tests
```

Expected:

```text
0.1.0
1 passed
All checks passed!
Ruff format check exits successfully.
```

- [ ] **Step 8: Commit the package baseline**

```powershell
git add pyproject.toml src/agent_runtime_lab/__init__.py tests/test_package.py
git commit -m "build: establish runtime package baseline"
```

### Task 2: Define typed contract errors and immutable execution events

**Files:**
- Create: `src/agent_runtime_lab/domain/errors.py`
- Create: `src/agent_runtime_lab/domain/events.py`
- Create: `src/agent_runtime_lab/domain/__init__.py`
- Create: `tests/domain/test_events.py`

- [ ] **Step 1: Write failing event-contract tests**

Create `tests/domain/test_events.py`:

```python
from datetime import UTC, datetime

import pytest

from agent_runtime_lab.domain.errors import EventValidationError
from agent_runtime_lab.domain.events import EventType, ExecutionEvent


NOW = datetime(2026, 7, 26, 0, 0, tzinfo=UTC)


def make_event(**overrides: object) -> ExecutionEvent:
    values: dict[str, object] = {
        "event_id": "evt-1",
        "run_id": "run-1",
        "sequence": 0,
        "event_type": EventType.RUN_CREATED,
        "occurred_at": NOW,
        "payload": {"z": 1, "a": "value"},
    }
    values.update(overrides)
    return ExecutionEvent.build(**values)


def test_payload_is_canonical_and_returned_as_a_copy() -> None:
    source = {"z": 1, "a": {"nested": True}}
    event = make_event(payload=source)

    source["z"] = 99
    first = event.payload
    first["z"] = 100

    assert event.payload == {"a": {"nested": True}, "z": 1}
    assert event.payload_json == '{"a":{"nested":true},"z":1}'


def test_equivalent_payload_order_has_same_fingerprint() -> None:
    left = make_event(payload={"a": 1, "b": 2})
    right = make_event(payload={"b": 2, "a": 1})

    assert left.fingerprint() == right.fingerprint()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("event_id", ""),
        ("run_id", ""),
        ("sequence", -1),
        ("occurred_at", datetime(2026, 7, 26)),
    ],
)
def test_invalid_event_fields_are_rejected(field: str, value: object) -> None:
    with pytest.raises(EventValidationError):
        make_event(**{field: value})


def test_non_object_payload_is_rejected() -> None:
    with pytest.raises(EventValidationError, match="JSON object"):
        ExecutionEvent(
            event_id="evt-1",
            run_id="run-1",
            sequence=0,
            event_type=EventType.RUN_CREATED,
            occurred_at=NOW,
            payload_json="[]",
        )
```

- [ ] **Step 2: Run the tests to verify missing modules fail**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/domain/test_events.py -v
```

Expected: collection fails because `agent_runtime_lab.domain` does not exist.

- [ ] **Step 3: Add the typed error hierarchy**

Create `src/agent_runtime_lab/domain/errors.py`:

```python
"""Typed failures for deterministic runtime contracts."""


class RuntimeContractError(ValueError):
    """Base class for invalid deterministic-runtime input."""


class EventValidationError(RuntimeContractError):
    """An event is malformed before reduction."""


class RunMismatchError(RuntimeContractError):
    """An event belongs to a different run."""


class SequenceError(RuntimeContractError):
    """An event sequence is missing, duplicated, or out of order."""


class DuplicateEventConflictError(RuntimeContractError):
    """An event ID was reused with different content."""


class InvalidTransitionError(RuntimeContractError):
    """An event cannot be applied in the current state."""


class TerminalStateError(RuntimeContractError):
    """A new event attempted to mutate a terminal run."""
```

- [ ] **Step 4: Add the event model**

Create `src/agent_runtime_lab/domain/events.py`:

```python
"""Immutable execution events with canonical JSON payloads."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Mapping

from agent_runtime_lab.domain.errors import EventValidationError


class EventType(StrEnum):
    RUN_CREATED = "run.created"
    RUN_STARTED = "run.started"
    RUN_PAUSED = "run.paused"
    RUN_RESUMED = "run.resumed"
    RUN_CANCELLED = "run.cancelled"
    TOOL_REQUESTED = "tool.requested"
    TOOL_AUTHORIZED = "tool.authorized"
    TOOL_DENIED = "tool.denied"
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
```

- [ ] **Step 5: Add domain exports**

Create `src/agent_runtime_lab/domain/__init__.py`:

```python
"""Deterministic runtime domain contracts."""

from agent_runtime_lab.domain.events import EventType, ExecutionEvent

__all__ = ["EventType", "ExecutionEvent"]
```

- [ ] **Step 6: Run tests and quality checks**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/domain/test_events.py -v
& .\.venv\Scripts\python.exe -m ruff check src tests
& .\.venv\Scripts\python.exe -m ruff format --check src tests
```

Expected: `7 passed`, Ruff check passes, and all files are formatted.

- [ ] **Step 7: Commit the event contract**

```powershell
git add src/agent_runtime_lab/domain tests/domain/test_events.py
git commit -m "feat: define immutable execution events"
```

### Task 3: Define immutable run state and terminal semantics

**Files:**
- Create: `src/agent_runtime_lab/domain/state.py`
- Create: `tests/domain/test_state.py`
- Modify: `src/agent_runtime_lab/domain/__init__.py`

- [ ] **Step 1: Write failing state tests**

Create `tests/domain/test_state.py`:

```python
import pytest

from agent_runtime_lab.domain.errors import EventValidationError
from agent_runtime_lab.domain.state import TERMINAL_STATUSES, RunState, RunStatus


def test_initial_state_is_empty_and_expects_sequence_zero() -> None:
    state = RunState.initial("run-1")

    assert state.run_id == "run-1"
    assert state.status is RunStatus.NEW
    assert state.next_sequence == 0
    assert state.active_tool_call_id is None
    assert state.failure_reason is None
    assert state.applied_event_fingerprints == ()


def test_empty_run_id_is_rejected() -> None:
    with pytest.raises(EventValidationError, match="run_id"):
        RunState.initial("")


def test_terminal_statuses_are_explicit() -> None:
    assert TERMINAL_STATUSES == frozenset(
        {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}
    )
```

- [ ] **Step 2: Run tests to verify the state module is missing**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/domain/test_state.py -v
```

Expected: collection fails with `No module named 'agent_runtime_lab.domain.state'`.

- [ ] **Step 3: Add the state model**

Create `src/agent_runtime_lab/domain/state.py`:

```python
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
    TOOL_READY = "tool_ready"
    TOOL_RUNNING = "tool_running"
    VERIFYING = "verifying"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATUSES = frozenset(
    {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}
)


@dataclass(frozen=True, slots=True)
class RunState:
    run_id: str
    status: RunStatus = RunStatus.NEW
    next_sequence: int = 0
    active_tool_call_id: str | None = None
    failure_reason: str | None = None
    applied_event_fingerprints: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not self.run_id:
            raise EventValidationError("run_id must not be empty")
        if self.next_sequence < 0:
            raise EventValidationError("next_sequence must be non-negative")

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
```

- [ ] **Step 4: Export state contracts**

Replace `src/agent_runtime_lab/domain/__init__.py` with:

```python
"""Deterministic runtime domain contracts."""

from agent_runtime_lab.domain.events import EventType, ExecutionEvent
from agent_runtime_lab.domain.state import TERMINAL_STATUSES, RunState, RunStatus

__all__ = [
    "TERMINAL_STATUSES",
    "EventType",
    "ExecutionEvent",
    "RunState",
    "RunStatus",
]
```

- [ ] **Step 5: Run state and event tests**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/domain/test_events.py tests/domain/test_state.py -v
& .\.venv\Scripts\python.exe -m ruff check src tests
& .\.venv\Scripts\python.exe -m ruff format --check src tests
```

Expected: `10 passed` and Ruff passes.

- [ ] **Step 6: Commit the state contract**

```powershell
git add src/agent_runtime_lab/domain tests/domain/test_state.py
git commit -m "feat: define immutable run state"
```

### Task 4: Implement the pure lifecycle reducer and invariants

**Files:**
- Create: `src/agent_runtime_lab/domain/reducer.py`
- Create: `tests/domain/test_reducer.py`
- Modify: `src/agent_runtime_lab/domain/__init__.py`

- [ ] **Step 1: Write failing reducer tests**

Create `tests/domain/test_reducer.py`:

```python
from datetime import UTC, datetime

import pytest

from agent_runtime_lab.domain.errors import (
    DuplicateEventConflictError,
    InvalidTransitionError,
    RunMismatchError,
    SequenceError,
    TerminalStateError,
)
from agent_runtime_lab.domain.events import EventType, ExecutionEvent
from agent_runtime_lab.domain.reducer import reduce
from agent_runtime_lab.domain.state import RunState, RunStatus


NOW = datetime(2026, 7, 26, 0, 0, tzinfo=UTC)


def event(
    sequence: int,
    event_type: EventType,
    *,
    event_id: str | None = None,
    run_id: str = "run-1",
    payload: dict[str, object] | None = None,
) -> ExecutionEvent:
    return ExecutionEvent.build(
        event_id=event_id or f"evt-{sequence}",
        run_id=run_id,
        sequence=sequence,
        event_type=event_type,
        occurred_at=NOW,
        payload=payload,
    )


def apply(state: RunState, *events: ExecutionEvent) -> RunState:
    for item in events:
        state = reduce(state, item)
    return state


def ready_state() -> RunState:
    return apply(
        RunState.initial("run-1"),
        event(0, EventType.RUN_CREATED),
        event(1, EventType.RUN_STARTED),
    )


def test_full_authorized_tool_lifecycle_completes() -> None:
    state = apply(
        ready_state(),
        event(2, EventType.TOOL_REQUESTED, payload={"tool_call_id": "tool-1"}),
        event(3, EventType.TOOL_AUTHORIZED, payload={"tool_call_id": "tool-1"}),
        event(4, EventType.TOOL_STARTED, payload={"tool_call_id": "tool-1"}),
        event(5, EventType.TOOL_SUCCEEDED, payload={"tool_call_id": "tool-1"}),
        event(6, EventType.VERIFICATION_SUCCEEDED),
    )

    assert state.status is RunStatus.COMPLETED
    assert state.next_sequence == 7
    assert state.active_tool_call_id is None
    assert len(state.applied_event_fingerprints) == 7


def test_exact_duplicate_delivery_is_idempotent() -> None:
    state = ready_state()
    requested = event(
        2,
        EventType.TOOL_REQUESTED,
        event_id="evt-request",
        payload={"tool_call_id": "tool-1"},
    )

    once = reduce(state, requested)
    twice = reduce(once, requested)

    assert twice == once
    assert twice.next_sequence == 3


def test_duplicate_event_id_with_different_content_is_rejected() -> None:
    state = reduce(ready_state(), event(2, EventType.RUN_PAUSED, event_id="evt-same"))

    with pytest.raises(DuplicateEventConflictError):
        reduce(
            state,
            event(
                2,
                EventType.TOOL_REQUESTED,
                event_id="evt-same",
                payload={"tool_call_id": "tool-1"},
            ),
        )


def test_sequence_gap_is_rejected() -> None:
    with pytest.raises(SequenceError, match="expected sequence 2"):
        reduce(ready_state(), event(3, EventType.RUN_PAUSED))


def test_cross_run_event_is_rejected() -> None:
    with pytest.raises(RunMismatchError):
        reduce(ready_state(), event(2, EventType.RUN_PAUSED, run_id="run-2"))


def test_illegal_transition_is_rejected() -> None:
    with pytest.raises(InvalidTransitionError):
        reduce(ready_state(), event(2, EventType.TOOL_STARTED, payload={"tool_call_id": "x"}))


def test_tool_call_identity_must_remain_stable() -> None:
    state = reduce(
        ready_state(),
        event(2, EventType.TOOL_REQUESTED, payload={"tool_call_id": "tool-1"}),
    )

    with pytest.raises(InvalidTransitionError, match="active tool call"):
        reduce(
            state,
            event(3, EventType.TOOL_AUTHORIZED, payload={"tool_call_id": "tool-2"}),
        )


def test_terminal_state_rejects_new_events_but_accepts_exact_redelivery() -> None:
    cancelled = event(2, EventType.RUN_CANCELLED)
    state = reduce(ready_state(), cancelled)

    assert reduce(state, cancelled) == state
    with pytest.raises(TerminalStateError):
        reduce(state, event(3, EventType.RUN_STARTED))


def test_pause_and_resume_return_to_ready() -> None:
    state = apply(
        ready_state(),
        event(2, EventType.RUN_PAUSED),
        event(3, EventType.RUN_RESUMED),
    )

    assert state.status is RunStatus.READY


def test_denied_tool_terminates_with_reason() -> None:
    state = apply(
        ready_state(),
        event(2, EventType.TOOL_REQUESTED, payload={"tool_call_id": "tool-1"}),
        event(
            3,
            EventType.TOOL_DENIED,
            payload={"tool_call_id": "tool-1", "reason": "outside workspace"},
        ),
    )

    assert state.status is RunStatus.FAILED
    assert state.failure_reason == "outside workspace"
```

- [ ] **Step 2: Run the reducer tests and confirm the module is missing**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/domain/test_reducer.py -v
```

Expected: collection fails with `No module named 'agent_runtime_lab.domain.reducer'`.

- [ ] **Step 3: Implement the pure reducer**

Create `src/agent_runtime_lab/domain/reducer.py`:

```python
"""Pure state reduction for the R1 execution lifecycle."""

from dataclasses import replace

from agent_runtime_lab.domain.errors import (
    DuplicateEventConflictError,
    InvalidTransitionError,
    RunMismatchError,
    SequenceError,
    TerminalStateError,
)
from agent_runtime_lab.domain.events import EventType, ExecutionEvent
from agent_runtime_lab.domain.state import RunState, RunStatus


def _expect(state: RunState, expected: RunStatus, event: ExecutionEvent) -> None:
    if state.status is not expected:
        raise InvalidTransitionError(
            f"{event.event_type.value} requires {expected.value}, got {state.status.value}"
        )


def _required_text(event: ExecutionEvent, field: str) -> str:
    value = event.payload.get(field)
    if not isinstance(value, str) or not value:
        raise InvalidTransitionError(
            f"{event.event_type.value} requires non-empty payload.{field}"
        )
    return value


def _expect_active_tool(state: RunState, event: ExecutionEvent) -> str:
    tool_call_id = _required_text(event, "tool_call_id")
    if tool_call_id != state.active_tool_call_id:
        raise InvalidTransitionError(
            f"{event.event_type.value} does not match active tool call "
            f"{state.active_tool_call_id!r}"
        )
    return tool_call_id


def _transition(state: RunState, event: ExecutionEvent) -> RunState:
    if event.event_type is EventType.RUN_CANCELLED:
        return replace(
            state,
            status=RunStatus.CANCELLED,
            active_tool_call_id=None,
            failure_reason=None,
        )

    if event.event_type is EventType.RUN_CREATED:
        _expect(state, RunStatus.NEW, event)
        return replace(state, status=RunStatus.CREATED)

    if event.event_type is EventType.RUN_STARTED:
        _expect(state, RunStatus.CREATED, event)
        return replace(state, status=RunStatus.READY)

    if event.event_type is EventType.RUN_PAUSED:
        _expect(state, RunStatus.READY, event)
        return replace(state, status=RunStatus.PAUSED)

    if event.event_type is EventType.RUN_RESUMED:
        _expect(state, RunStatus.PAUSED, event)
        return replace(state, status=RunStatus.READY)

    if event.event_type is EventType.TOOL_REQUESTED:
        _expect(state, RunStatus.READY, event)
        return replace(
            state,
            status=RunStatus.TOOL_PENDING,
            active_tool_call_id=_required_text(event, "tool_call_id"),
        )

    if event.event_type is EventType.TOOL_AUTHORIZED:
        _expect(state, RunStatus.TOOL_PENDING, event)
        _expect_active_tool(state, event)
        return replace(state, status=RunStatus.TOOL_READY)

    if event.event_type is EventType.TOOL_DENIED:
        _expect(state, RunStatus.TOOL_PENDING, event)
        _expect_active_tool(state, event)
        reason = _required_text(event, "reason")
        return replace(
            state,
            status=RunStatus.FAILED,
            active_tool_call_id=None,
            failure_reason=reason,
        )

    if event.event_type is EventType.TOOL_STARTED:
        _expect(state, RunStatus.TOOL_READY, event)
        _expect_active_tool(state, event)
        return replace(state, status=RunStatus.TOOL_RUNNING)

    if event.event_type is EventType.TOOL_SUCCEEDED:
        _expect(state, RunStatus.TOOL_RUNNING, event)
        _expect_active_tool(state, event)
        return replace(
            state,
            status=RunStatus.VERIFYING,
            active_tool_call_id=None,
        )

    if event.event_type is EventType.TOOL_FAILED:
        _expect(state, RunStatus.TOOL_RUNNING, event)
        _expect_active_tool(state, event)
        reason = _required_text(event, "reason")
        return replace(
            state,
            status=RunStatus.FAILED,
            active_tool_call_id=None,
            failure_reason=reason,
        )

    if event.event_type is EventType.VERIFICATION_SUCCEEDED:
        _expect(state, RunStatus.VERIFYING, event)
        return replace(state, status=RunStatus.COMPLETED)

    if event.event_type is EventType.VERIFICATION_FAILED:
        _expect(state, RunStatus.VERIFYING, event)
        return replace(
            state,
            status=RunStatus.FAILED,
            failure_reason=_required_text(event, "reason"),
        )

    raise InvalidTransitionError(f"unsupported event type: {event.event_type.value}")


def reduce(state: RunState, event: ExecutionEvent) -> RunState:
    """Apply one event without I/O, time access, randomness, or mutation."""
    if event.run_id != state.run_id:
        raise RunMismatchError(
            f"event run {event.run_id!r} does not match state run {state.run_id!r}"
        )

    fingerprint = event.fingerprint()
    existing = state.fingerprint_for(event.event_id)
    if existing is not None:
        if existing == fingerprint:
            return state
        raise DuplicateEventConflictError(
            f"event_id {event.event_id!r} was reused with different content"
        )

    if state.is_terminal:
        raise TerminalStateError(f"cannot mutate terminal state {state.status.value}")

    if event.sequence != state.next_sequence:
        raise SequenceError(
            f"expected sequence {state.next_sequence}, got {event.sequence}"
        )

    transitioned = _transition(state, event)
    return replace(
        transitioned,
        next_sequence=state.next_sequence + 1,
        applied_event_fingerprints=state.applied_event_fingerprints
        + ((event.event_id, fingerprint),),
    )
```

- [ ] **Step 4: Export the reducer**

Replace `src/agent_runtime_lab/domain/__init__.py` with:

```python
"""Deterministic runtime domain contracts."""

from agent_runtime_lab.domain.events import EventType, ExecutionEvent
from agent_runtime_lab.domain.reducer import reduce
from agent_runtime_lab.domain.state import TERMINAL_STATUSES, RunState, RunStatus

__all__ = [
    "TERMINAL_STATUSES",
    "EventType",
    "ExecutionEvent",
    "RunState",
    "RunStatus",
    "reduce",
]
```

- [ ] **Step 5: Run reducer and full domain tests**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/domain -v
& .\.venv\Scripts\python.exe -m ruff check src tests
& .\.venv\Scripts\python.exe -m ruff format --check src tests
```

Expected: `20 passed` and Ruff passes.

- [ ] **Step 6: Commit the reducer**

```powershell
git add src/agent_runtime_lab/domain tests/domain/test_reducer.py
git commit -m "feat: implement deterministic lifecycle reducer"
```

### Task 5: Implement ordered replay and corruption detection

**Files:**
- Create: `src/agent_runtime_lab/domain/replay.py`
- Create: `tests/domain/test_replay.py`
- Modify: `src/agent_runtime_lab/domain/__init__.py`
- Modify: `src/agent_runtime_lab/__init__.py`

- [ ] **Step 1: Write failing replay tests**

Create `tests/domain/test_replay.py`:

```python
from datetime import UTC, datetime

import pytest

from agent_runtime_lab.domain.errors import SequenceError
from agent_runtime_lab.domain.events import EventType, ExecutionEvent
from agent_runtime_lab.domain.replay import replay
from agent_runtime_lab.domain.state import RunStatus


NOW = datetime(2026, 7, 26, 0, 0, tzinfo=UTC)


def event(
    sequence: int,
    event_type: EventType,
    *,
    event_id: str | None = None,
    payload: dict[str, object] | None = None,
) -> ExecutionEvent:
    return ExecutionEvent.build(
        event_id=event_id or f"evt-{sequence}",
        run_id="run-1",
        sequence=sequence,
        event_type=event_type,
        occurred_at=NOW,
        payload=payload,
    )


def completed_events() -> list[ExecutionEvent]:
    return [
        event(0, EventType.RUN_CREATED),
        event(1, EventType.RUN_STARTED),
        event(2, EventType.TOOL_REQUESTED, payload={"tool_call_id": "tool-1"}),
        event(3, EventType.TOOL_AUTHORIZED, payload={"tool_call_id": "tool-1"}),
        event(4, EventType.TOOL_STARTED, payload={"tool_call_id": "tool-1"}),
        event(5, EventType.TOOL_SUCCEEDED, payload={"tool_call_id": "tool-1"}),
        event(6, EventType.VERIFICATION_SUCCEEDED),
    ]


def test_replay_derives_completed_state() -> None:
    state = replay("run-1", completed_events())

    assert state.status is RunStatus.COMPLETED
    assert state.next_sequence == 7


def test_replay_is_deterministic() -> None:
    events = completed_events()

    assert replay("run-1", events) == replay("run-1", events)


def test_replay_accepts_exact_duplicate_delivery() -> None:
    events = completed_events()
    events.insert(3, events[2])

    state = replay("run-1", events)

    assert state.status is RunStatus.COMPLETED
    assert state.next_sequence == 7


def test_replay_rejects_out_of_order_events() -> None:
    events = completed_events()
    events[2], events[3] = events[3], events[2]

    with pytest.raises(SequenceError):
        replay("run-1", events)
```

- [ ] **Step 2: Run tests to verify the replay module is missing**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/domain/test_replay.py -v
```

Expected: collection fails with `No module named 'agent_runtime_lab.domain.replay'`.

- [ ] **Step 3: Add ordered replay**

Create `src/agent_runtime_lab/domain/replay.py`:

```python
"""Deterministically rebuild run state from ordered execution events."""

from collections.abc import Iterable

from agent_runtime_lab.domain.events import ExecutionEvent
from agent_runtime_lab.domain.reducer import reduce
from agent_runtime_lab.domain.state import RunState


def replay(run_id: str, events: Iterable[ExecutionEvent]) -> RunState:
    state = RunState.initial(run_id)
    for event in events:
        state = reduce(state, event)
    return state
```

- [ ] **Step 4: Export the complete R1 public API**

Replace `src/agent_runtime_lab/domain/__init__.py` with:

```python
"""Deterministic runtime domain contracts."""

from agent_runtime_lab.domain.events import EventType, ExecutionEvent
from agent_runtime_lab.domain.reducer import reduce
from agent_runtime_lab.domain.replay import replay
from agent_runtime_lab.domain.state import TERMINAL_STATUSES, RunState, RunStatus

__all__ = [
    "TERMINAL_STATUSES",
    "EventType",
    "ExecutionEvent",
    "RunState",
    "RunStatus",
    "reduce",
    "replay",
]
```

Replace `src/agent_runtime_lab/__init__.py` with:

```python
"""Reliable execution primitives for agentic workloads."""

from agent_runtime_lab.domain import (
    EventType,
    ExecutionEvent,
    RunState,
    RunStatus,
    reduce,
    replay,
)

__version__ = "0.1.0"

__all__ = [
    "EventType",
    "ExecutionEvent",
    "RunState",
    "RunStatus",
    "__version__",
    "reduce",
    "replay",
]
```

- [ ] **Step 5: Run replay and complete test suites**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/domain/test_replay.py -v
& .\.venv\Scripts\python.exe -m pytest -v
& .\.venv\Scripts\python.exe -m ruff check src tests
& .\.venv\Scripts\python.exe -m ruff format --check src tests
```

Expected: replay tests show `4 passed`, the full suite shows `24 passed`,
and Ruff passes.

- [ ] **Step 6: Commit replay and public exports**

```powershell
git add src/agent_runtime_lab tests/domain/test_replay.py
git commit -m "feat: add deterministic event replay"
```

### Task 6: Record evidence and close the S0/R1 learning gate

**Files:**
- Modify: `README.md`
- Modify: `docs/progress.md`

- [ ] **Step 1: Update the README capability boundary**

Replace the current-status paragraph in `README.md`:

```markdown
This repository is an initial project scaffold. It does not yet claim a
production runtime, durable replay, crash recovery, or enforced sandboxing.
```

with:

```markdown
The S0/R1 deterministic core is implemented: immutable execution events,
immutable run state, a pure lifecycle reducer, duplicate-delivery protection,
terminal-state enforcement, and ordered replay. This does not yet claim durable
database persistence, external side-effect idempotency, crash recovery,
sandboxing, worker orchestration, or production readiness.
```

- [ ] **Step 2: Add the verified progress entry**

Append to `docs/progress.md`:

```markdown
## 2026-07-26 鈥?S0/R1 deterministic core

Completed:

- Added a Python 3.11 `src/` package with editable-install support.
- Defined canonical immutable execution events and typed contract errors.
- Defined immutable run state and explicit terminal states.
- Implemented a pure lifecycle reducer with sequence, run, transition, tool
  identity, duplicate-delivery, and terminal-state invariants.
- Implemented deterministic ordered replay through the same reducer.

Verified evidence:

- `python -m pytest -v`: 24 tests pass.
- `python -m ruff check src tests`: passes.
- `python -m ruff format --check src tests`: passes.
- Exact duplicate delivery is a no-op; conflicting reuse of an event ID fails.
- Out-of-order and cross-run events fail explicitly.
- Terminal state accepts exact redelivery but rejects new mutation.

Lucas understanding gate:

- Explain Event versus State.
- Explain why Reducer must have no I/O, clock, or randomness.
- Explain exact duplicate delivery versus conflicting duplicate identity.
- Predict the state sequence for one successful tool lifecycle.
- Explain why R1 replay is not yet durable crash recovery.

Next milestone:

- R2 SQLite Event Store, Tool Intent/Receipt persistence, and crash-window
  recovery contracts.
```

- [ ] **Step 3: Run the final verification matrix**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest -v
& .\.venv\Scripts\python.exe -m ruff check src tests
& .\.venv\Scripts\python.exe -m ruff format --check src tests
& .\.venv\Scripts\python.exe -m compileall -q src tests
git diff --check
git status --short
```

Expected:

```text
24 passed
All checks passed!
All files already formatted
git diff --check exits 0
Only README.md and docs/progress.md are modified; .idea/ remains untracked
```

- [ ] **Step 4: Perform the R1 teaching checkpoint**

Use this concrete event sequence:

```text
run.created
run.started
tool.requested(tool-1)
tool.authorized(tool-1)
tool.started(tool-1)
tool.succeeded(tool-1)
verification.succeeded
```

Ask Lucas to provide:

```text
1. The RunStatus after every event.
2. The expected next_sequence after every event.
3. What happens if tool.requested is delivered twice identically.
4. What happens if the second delivery reuses the event ID with new payload.
5. Why replay cannot repair an external tool that ignores idempotency keys.
```

The milestone is not marked understood until Lucas can answer items 1鈥? and
can restate item 5 after explanation.

- [ ] **Step 5: Commit evidence and progress**

```powershell
git add README.md docs/progress.md
git commit -m "docs: record deterministic runtime core evidence"
```

- [ ] **Step 6: Record the exact final handoff**

Run:

```powershell
git log -6 --oneline --decorate
git status --short
```

Expected:

- Six new focused commits: package baseline, events, state, reducer, replay,
  and documentation.
- `.idea/` remains the only unrelated untracked path.
- No Runtime persistence, Sandbox, Provider, Eval, or CodeOwnership claim is
  presented as implemented by this plan.
