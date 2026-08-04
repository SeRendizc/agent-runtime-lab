from pathlib import Path

import pytest

from agent_runtime_lab.domain.errors import MissingToolIntentError
from agent_runtime_lab.domain.tool_effects import (
    RecoveryDecision,
    ToolIntent,
    ToolOutcome,
    ToolReceipt,
)
from agent_runtime_lab.persistence.sqlite_tool_effect_store import (
    SQLiteToolEffectStore,
)
from agent_runtime_lab.tool_recovery import inspect_recovery


def make_intent() -> ToolIntent:
    return ToolIntent.build(
        run_id="run-1",
        tool_call_id="tool-call-1",
        tool_name="append_file",
        arguments={"path": "result.txt"},
    )


def test_persisted_receipt_means_completed_after_restart(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "runtime.db"
    intent = make_intent()
    receipt = ToolReceipt.build(
        effect_id=intent.effect_id,
        outcome=ToolOutcome.SUCCEEDED,
        output={"bytes_written": 5},
    )

    first_store = SQLiteToolEffectStore(database_path)
    first_store.save_intent(intent)
    first_store.save_receipt(receipt)
    first_store.close()

    recovered_store = SQLiteToolEffectStore(database_path)

    assert (
        inspect_recovery(
            store=recovered_store,
            effect_id=intent.effect_id,
            retry_is_idempotent=False,
        )
        is RecoveryDecision.COMPLETED
    )

    recovered_store.close()


def test_missing_receipt_allows_idempotent_retry(
    tmp_path: Path,
) -> None:
    store = SQLiteToolEffectStore(tmp_path / "runtime.db")
    intent = make_intent()
    store.save_intent(intent)

    assert (
        inspect_recovery(
            store=store,
            effect_id=intent.effect_id,
            retry_is_idempotent=True,
        )
        is RecoveryDecision.SAFE_RETRY
    )

    store.close()


def test_missing_receipt_blocks_non_idempotent_retry(
    tmp_path: Path,
) -> None:
    store = SQLiteToolEffectStore(tmp_path / "runtime.db")
    intent = make_intent()
    store.save_intent(intent)

    assert (
        inspect_recovery(
            store=store,
            effect_id=intent.effect_id,
            retry_is_idempotent=False,
        )
        is RecoveryDecision.UNKNOWN
    )

    store.close()


def test_missing_intent_is_not_a_recoverable_tool_effect(
    tmp_path: Path,
) -> None:
    store = SQLiteToolEffectStore(tmp_path / "runtime.db")

    with pytest.raises(
        MissingToolIntentError,
        match="no persisted intent exists",
    ):
        inspect_recovery(
            store=store,
            effect_id="missing-effect",
            retry_is_idempotent=True,
        )

    store.close()
