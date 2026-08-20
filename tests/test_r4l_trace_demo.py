import json

from examples.r4l_trace_demo import run_demo


def test_trace_demo_completes_and_exports_sanitized_evidence() -> None:
    summary = run_demo()
    encoded = json.dumps(summary, ensure_ascii=False, sort_keys=True)

    assert summary["schema_version"] == 1
    assert summary["final_status"] == "completed"
    assert summary["run_id"] == "r4l-trace-demo"
    assert summary["metrics"] == {
        "duration_ms": 0,
        "event_count": 12,
        "gate_escalation_count": 0,
        "model_action_count": 2,
        "runtime_steps": 2,
        "tool_request_count": 1,
        "verification_failure_count": 0,
        "verification_success_count": 1,
    }
    assert len(summary["trace_digest"]) == 64
    assert summary["event_types"][-1] == "completion.accepted"
    assert "trace demo private content" not in encoded
    assert "trace demo complete" not in encoded
