import sqlite3
from pathlib import Path

import pytest

from agent_runtime_lab.domain.errors import (
    DuplicateToolEffectConflictError,
    MissingToolIntentError,
)
from agent_runtime_lab.domain.tool_effects import (
    ToolIntent,
    ToolOutcome,
    ToolReceipt,
)
from agent_runtime_lab.persistence.sqlite_tool_effect_store import (
    SQLiteToolEffectStore,
)


def make_intent(
    *,
    tool_name: str = "append_file",
    arguments: dict[str, object] | None = None,
) -> ToolIntent:
    return ToolIntent.build(
        run_id="run-1",
        tool_call_id="tool-call-1",
        tool_name=tool_name,
        arguments=arguments,
    )


def test_intent_and_receipt_survive_store_reopening(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "runtime.db"
    intent = make_intent(arguments={"path": "result.txt"})
    receipt = ToolReceipt.build(
        effect_id=intent.effect_id,
        outcome=ToolOutcome.SUCCEEDED,
        output={"bytes_written": 5},
    )

    first_store = SQLiteToolEffectStore(database_path)
    first_store.save_intent(intent)
    first_store.close()

    second_store = SQLiteToolEffectStore(database_path)

    assert second_store.load_intent(intent.effect_id) == intent
    assert second_store.load_receipt(intent.effect_id) is None

    second_store.save_receipt(receipt)
    second_store.close()

    recovered_store = SQLiteToolEffectStore(database_path)

    assert recovered_store.load_intent(intent.effect_id) == intent
    assert recovered_store.load_receipt(intent.effect_id) == receipt

    recovered_store.close()


def test_exact_duplicate_intent_is_idempotent(
    tmp_path: Path,
) -> None:
    store = SQLiteToolEffectStore(tmp_path / "runtime.db")
    intent = make_intent(arguments={"path": "result.txt"})

    store.save_intent(intent)
    store.save_intent(intent)

    assert store.load_intent(intent.effect_id) == intent

    store.close()


def test_conflicting_intent_is_rejected(
    tmp_path: Path,
) -> None:
    store = SQLiteToolEffectStore(tmp_path / "runtime.db")
    original = make_intent(arguments={"path": "first.txt"})
    conflicting = make_intent(arguments={"path": "second.txt"})

    store.save_intent(original)

    with pytest.raises(
        DuplicateToolEffectConflictError,
        match="different intent",
    ):
        store.save_intent(conflicting)

    assert store.load_intent(original.effect_id) == original

    store.close()


def test_receipt_without_intent_is_rejected(
    tmp_path: Path,
) -> None:
    store = SQLiteToolEffectStore(tmp_path / "runtime.db")
    intent = make_intent()
    receipt = ToolReceipt.build(
        effect_id=intent.effect_id,
        outcome=ToolOutcome.SUCCEEDED,
    )

    with pytest.raises(
        MissingToolIntentError,
        match="no intent exists",
    ):
        store.save_receipt(receipt)

    assert store.load_receipt(intent.effect_id) is None

    store.close()


def test_exact_duplicate_receipt_is_idempotent(
    tmp_path: Path,
) -> None:
    store = SQLiteToolEffectStore(tmp_path / "runtime.db")
    intent = make_intent()
    receipt = ToolReceipt.build(
        effect_id=intent.effect_id,
        outcome=ToolOutcome.SUCCEEDED,
        output={"bytes_written": 5},
    )

    store.save_intent(intent)
    store.save_receipt(receipt)
    store.save_receipt(receipt)

    assert store.load_receipt(intent.effect_id) == receipt

    store.close()


def test_conflicting_receipt_is_rejected(
    tmp_path: Path,
) -> None:
    store = SQLiteToolEffectStore(tmp_path / "runtime.db")
    intent = make_intent()
    original = ToolReceipt.build(
        effect_id=intent.effect_id,
        outcome=ToolOutcome.SUCCEEDED,
        output={"bytes_written": 5},
    )
    conflicting = ToolReceipt.build(
        effect_id=intent.effect_id,
        outcome=ToolOutcome.FAILED,
        output={"error": "write failed"},
    )

    store.save_intent(intent)
    store.save_receipt(original)

    with pytest.raises(
        DuplicateToolEffectConflictError,
        match="different receipt",
    ):
        store.save_receipt(conflicting)

    assert store.load_receipt(intent.effect_id) == original

    store.close()


def test_legacy_receipt_constraint_is_migrated_without_losing_evidence(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "runtime.db"
    intent = make_intent()
    succeeded = ToolReceipt.build(
        effect_id=intent.effect_id,
        outcome=ToolOutcome.SUCCEEDED,
        output={"ok": True},
    )
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE tool_intents (
                effect_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                tool_call_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                arguments_json TEXT NOT NULL,
                UNIQUE (run_id, tool_call_id)
            );
            CREATE TABLE tool_receipts (
                effect_id TEXT PRIMARY KEY,
                outcome TEXT NOT NULL CHECK (outcome IN ('succeeded', 'failed')),
                output_json TEXT NOT NULL,
                FOREIGN KEY (effect_id) REFERENCES tool_intents (effect_id)
            );
            """
        )
        connection.execute(
            """
            INSERT INTO tool_intents
                (effect_id, run_id, tool_call_id, tool_name, arguments_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                intent.effect_id,
                intent.run_id,
                intent.tool_call_id,
                intent.tool_name,
                intent.arguments_json,
            ),
        )
        connection.execute(
            "INSERT INTO tool_receipts (effect_id, outcome, output_json) VALUES (?, ?, ?)",
            (succeeded.effect_id, succeeded.outcome.value, succeeded.output_json),
        )

    with SQLiteToolEffectStore(database_path) as store:
        assert store.load_intent(intent.effect_id) == intent
        assert store.load_receipt(intent.effect_id) == succeeded

        timed_out_intent = ToolIntent.build(
            run_id="run-2",
            tool_call_id="tool-call-2",
            tool_name="append_file",
        )
        timed_out = ToolReceipt.build(
            effect_id=timed_out_intent.effect_id,
            outcome=ToolOutcome.TIMED_OUT,
            output={"timeout_seconds": 2.5},
        )
        store.save_intent(timed_out_intent)
        store.save_receipt(timed_out)

        assert store.load_receipt(timed_out.effect_id) == timed_out
