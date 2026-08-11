import json
from pathlib import Path

from agent_runtime_lab.r4a_demo import run_demo


def test_demo_returns_sanitized_real_runtime_evidence() -> None:
    summary = run_demo()
    encoded = json.dumps(summary, ensure_ascii=False, sort_keys=True)

    assert summary["read_file"]["receipt_outcome"] == "succeeded"
    assert summary["write_file"]["receipt_outcome"] == "succeeded"
    assert summary["delete_file"]["receipt_outcome"] == "succeeded"
    assert summary["read_file"]["path"] == "input.txt"
    assert summary["write_file"]["path"] == "output.txt"
    assert summary["delete_file"]["path"] == "delete-me.txt"
    assert summary["denied_escape"] == {
        "outcome": "denied",
        "intent_persisted": False,
        "outside_changed": False,
    }
    assert summary["fail_closed_recovery"]["automatic_retry"] is False
    assert summary["fail_closed_recovery"]["write_file"] == {
        "error_type": "UnsafeToolRetryError",
        "target_exists": False,
    }
    assert summary["fail_closed_recovery"]["delete_file"] == {
        "error_type": "UnsafeToolRetryError",
        "target_preserved": True,
    }
    assert all(item["events"] for item in summary.values() if "events" in item)
    assert "temporary demo content" not in encoded
    assert not any(Path(value).is_absolute() for value in _all_strings(summary))


def _all_strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [item for child in value.values() for item in _all_strings(child)]
    if isinstance(value, list):
        return [item for child in value for item in _all_strings(child)]
    return []
