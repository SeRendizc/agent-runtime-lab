from datetime import UTC, datetime, timedelta

import pytest

from agent_runtime_lab.domain.errors import InvalidTransitionError
from agent_runtime_lab.domain.events import EventType, ExecutionEvent
from agent_runtime_lab.trace import TRACE_SCHEMA_VERSION, build_run_trace

NOW = datetime(2026, 8, 15, tzinfo=UTC)


def event(
    sequence: int,
    event_type: EventType,
    payload: dict[str, object] | None = None,
) -> ExecutionEvent:
    return ExecutionEvent.build(
        event_id=f"evt-{sequence}",
        run_id="run-trace",
        sequence=sequence,
        event_type=event_type,
        occurred_at=NOW + timedelta(milliseconds=sequence * 10),
        payload=payload,
    )


def completed_events(*, answer: str = "secret answer") -> tuple[ExecutionEvent, ...]:
    return (
        event(0, EventType.RUN_CREATED, {"max_steps": 1}),
        event(1, EventType.RUN_STARTED),
        event(
            2,
            EventType.MODEL_ACTION_REQUESTED,
            {
                "invocation_id": "invoke-1",
                "observation_json": '{"private":"observation"}',
                "step_id": "step-1",
                "turn_index": 0,
            },
        ),
        event(
            3,
            EventType.MODEL_ACTION_PROPOSED,
            {
                "action_type": "final_answer",
                "answer": answer,
                "invocation_id": "invoke-1",
                "step_id": "step-1",
                "turn_index": 0,
            },
        ),
        event(
            4,
            EventType.COMPLETION_ACCEPTED,
            {
                "answer": answer,
                "answer_sha256": "digest",
                "model_action_event_id": "evt-3",
                "step_id": "step-1",
                "summary": "accepted",
            },
        ),
    )


def test_trace_is_deterministic_versioned_and_replay_derived() -> None:
    first = build_run_trace("run-trace", completed_events())
    second = build_run_trace("run-trace", completed_events())

    assert first.schema_version == TRACE_SCHEMA_VERSION == 1
    assert first.final_status == "completed"
    assert first.canonical_json() == second.canonical_json()
    assert first.digest() == second.digest()
    assert [(item.state_before, item.state_after) for item in first.records] == [
        ("new", "created"),
        ("created", "ready"),
        ("ready", "model_pending"),
        ("model_pending", "action_pending"),
        ("action_pending", "completed"),
    ]


def test_trace_redacts_raw_payload_but_binds_it_by_digest() -> None:
    first = build_run_trace("run-trace", completed_events(answer="private one"))
    second = build_run_trace("run-trace", completed_events(answer="private two"))

    exported = first.canonical_json()
    assert "private one" not in exported
    assert "observation" not in exported
    assert first.records[3].metadata == {
        "action_type": "final_answer",
        "invocation_id": "invoke-1",
        "step_id": "step-1",
        "turn_index": 0,
    }
    assert first.records[3].payload_sha256 != second.records[3].payload_sha256
    assert first.digest() != second.digest()


def test_trace_exports_stable_eval_metrics() -> None:
    trace = build_run_trace("run-trace", completed_events())

    assert trace.metrics.as_dict() == {
        "duration_ms": 40,
        "event_count": 5,
        "gate_escalation_count": 0,
        "model_action_count": 1,
        "runtime_steps": 1,
        "tool_request_count": 0,
        "verification_failure_count": 0,
        "verification_success_count": 0,
    }


def test_trace_refuses_an_invalid_history_instead_of_exporting_it() -> None:
    invalid = (
        event(0, EventType.RUN_CREATED),
        event(1, EventType.COMPLETION_ACCEPTED, {"step_id": "step-1"}),
    )

    with pytest.raises(InvalidTransitionError):
        build_run_trace("run-trace", invalid)
