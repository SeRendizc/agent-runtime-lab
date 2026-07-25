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
