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
