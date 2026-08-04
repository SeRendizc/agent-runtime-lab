from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from agent_runtime_lab.domain.errors import UnsafeToolRetryError
from agent_runtime_lab.domain.tool_effects import ToolIntent, ToolOutcome
from agent_runtime_lab.durable_tool_executor import DurableToolExecutor
from agent_runtime_lab.persistence.sqlite_tool_effect_store import (
    SQLiteToolEffectStore,
)


class RecordingToolRunner:
    def __init__(
        self,
        *,
        output: Mapping[str, Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.output = output or {}
        self.error = error
        self.calls: list[tuple[str, dict[str, Any], str]] = []

    def invoke(
        self,
        *,
        tool_name: str,
        arguments: Mapping[str, Any],
        idempotency_key: str,
    ) -> Mapping[str, Any]:
        self.calls.append((tool_name, dict(arguments), idempotency_key))

        if self.error is not None:
            raise self.error

        return self.output


def make_intent() -> ToolIntent:
    return ToolIntent.build(
        run_id="run-1",
        tool_call_id="tool-call-1",
        tool_name="append_file",
        arguments={"path": "result.txt", "text": "hello"},
    )


def test_new_effect_persists_intent_before_successful_receipt(
    tmp_path: Path,
) -> None:
    intent = make_intent()
    runner = RecordingToolRunner(output={"bytes_written": 5})

    with SQLiteToolEffectStore(tmp_path / "runtime.db") as store:
        executor = DurableToolExecutor(store=store, runner=runner)

        receipt = executor.execute(
            intent=intent,
            retry_is_idempotent=False,
        )

        assert store.load_intent(intent.effect_id) == intent
        assert store.load_receipt(intent.effect_id) == receipt

    assert receipt.outcome is ToolOutcome.SUCCEEDED
    assert receipt.output == {"bytes_written": 5}
    assert runner.calls == [
        (
            "append_file",
            {"path": "result.txt", "text": "hello"},
            intent.idempotency_key,
        )
    ]


def test_completed_redelivery_returns_receipt_without_reexecution(
    tmp_path: Path,
) -> None:
    intent = make_intent()
    runner = RecordingToolRunner(output={"bytes_written": 5})

    with SQLiteToolEffectStore(tmp_path / "runtime.db") as store:
        executor = DurableToolExecutor(store=store, runner=runner)
        first_receipt = executor.execute(
            intent=intent,
            retry_is_idempotent=False,
        )
        second_receipt = executor.execute(
            intent=intent,
            retry_is_idempotent=False,
        )

    assert second_receipt == first_receipt
    assert len(runner.calls) == 1


def test_tool_failure_is_persisted_as_failed_receipt(
    tmp_path: Path,
) -> None:
    intent = make_intent()
    runner = RecordingToolRunner(error=RuntimeError("disk full"))

    with SQLiteToolEffectStore(tmp_path / "runtime.db") as store:
        executor = DurableToolExecutor(store=store, runner=runner)
        receipt = executor.execute(
            intent=intent,
            retry_is_idempotent=False,
        )

        assert store.load_receipt(intent.effect_id) == receipt

    assert receipt.outcome is ToolOutcome.FAILED
    assert receipt.output == {
        "error_type": "RuntimeError",
        "message": "disk full",
    }


def test_incomplete_non_idempotent_effect_is_not_reexecuted(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "runtime.db"
    intent = make_intent()

    with SQLiteToolEffectStore(database_path) as first_store:
        first_store.save_intent(intent)

    runner = RecordingToolRunner(output={"bytes_written": 5})

    with SQLiteToolEffectStore(database_path) as recovered_store:
        executor = DurableToolExecutor(store=recovered_store, runner=runner)

        with pytest.raises(
            UnsafeToolRetryError,
            match="cannot be retried safely",
        ):
            executor.execute(
                intent=intent,
                retry_is_idempotent=False,
            )

    assert runner.calls == []


def test_incomplete_idempotent_effect_is_retried_and_completed(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "runtime.db"
    intent = make_intent()

    with SQLiteToolEffectStore(database_path) as first_store:
        first_store.save_intent(intent)

    runner = RecordingToolRunner(output={"bytes_written": 5})

    with SQLiteToolEffectStore(database_path) as recovered_store:
        executor = DurableToolExecutor(store=recovered_store, runner=runner)
        receipt = executor.execute(
            intent=intent,
            retry_is_idempotent=True,
        )

        assert recovered_store.load_receipt(intent.effect_id) == receipt

    assert receipt.outcome is ToolOutcome.SUCCEEDED
    assert len(runner.calls) == 1
