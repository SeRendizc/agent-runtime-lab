import pytest

from agent_runtime_lab.domain.errors import EventValidationError
from agent_runtime_lab.domain.tool_effects import (
    RecoveryDecision,
    ToolIntent,
    ToolOutcome,
    ToolReceipt,
    decide_recovery,
    derive_effect_id,
)


def test_effect_id_is_stable_for_same_logical_call() -> None:
    first = derive_effect_id(
        run_id="run-1",
        tool_call_id="tool-1",
    )
    second = derive_effect_id(
        run_id="run-1",
        tool_call_id="tool-1",
    )

    assert first == second


def test_different_tool_calls_have_different_effect_ids() -> None:
    first = derive_effect_id(
        run_id="run-1",
        tool_call_id="tool-1",
    )
    second = derive_effect_id(
        run_id="run-1",
        tool_call_id="tool-2",
    )

    assert first != second


def test_intent_canonicalizes_arguments() -> None:
    intent = ToolIntent.build(
        run_id="run-1",
        tool_call_id="tool-1",
        tool_name="append_file",
        arguments={
            "path": "result.txt",
            "content": "hello",
        },
    )

    assert intent.arguments_json == ('{"content":"hello","path":"result.txt"}')
    assert intent.idempotency_key == intent.effect_id


def test_receipt_means_the_attempt_has_a_known_result() -> None:
    intent = ToolIntent.build(
        run_id="run-1",
        tool_call_id="tool-1",
        tool_name="append_file",
    )
    receipt = ToolReceipt.build(
        effect_id=intent.effect_id,
        outcome=ToolOutcome.SUCCEEDED,
        output={"bytes_written": 5},
    )

    decision = decide_recovery(
        intent=intent,
        receipt=receipt,
        retry_is_idempotent=False,
    )

    assert decision is RecoveryDecision.COMPLETED


def test_missing_receipt_allows_retry_for_idempotent_tool() -> None:
    intent = ToolIntent.build(
        run_id="run-1",
        tool_call_id="tool-1",
        tool_name="put_file",
    )

    decision = decide_recovery(
        intent=intent,
        receipt=None,
        retry_is_idempotent=True,
    )

    assert decision is RecoveryDecision.SAFE_RETRY


def test_missing_receipt_is_unknown_for_non_idempotent_tool() -> None:
    intent = ToolIntent.build(
        run_id="run-1",
        tool_call_id="tool-1",
        tool_name="append_file",
    )

    decision = decide_recovery(
        intent=intent,
        receipt=None,
        retry_is_idempotent=False,
    )

    assert decision is RecoveryDecision.UNKNOWN


def test_mismatched_receipt_is_rejected() -> None:
    intent = ToolIntent.build(
        run_id="run-1",
        tool_call_id="tool-1",
        tool_name="append_file",
    )
    wrong_receipt = ToolReceipt.build(
        effect_id=derive_effect_id(
            run_id="run-1",
            tool_call_id="tool-2",
        ),
        outcome=ToolOutcome.SUCCEEDED,
    )

    with pytest.raises(EventValidationError, match="does not match"):
        decide_recovery(
            intent=intent,
            receipt=wrong_receipt,
            retry_is_idempotent=True,
        )
