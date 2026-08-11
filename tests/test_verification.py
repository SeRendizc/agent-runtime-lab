from __future__ import annotations

import hashlib

import pytest

from agent_runtime_lab.domain.tool_effects import ToolOutcome, ToolReceipt
from agent_runtime_lab.verification import (
    ReceiptVerifier,
    VerificationExpectation,
    VerificationOutcome,
)


def make_receipt(
    *,
    outcome: ToolOutcome = ToolOutcome.SUCCEEDED,
    output: dict[str, object] | None = None,
) -> ToolReceipt:
    return ToolReceipt.build(
        effect_id="effect-1",
        outcome=outcome,
        output=output
        or {
            "path": "notes.txt",
            "sha256": hashlib.sha256(b"hello").hexdigest(),
        },
    )


def expectation() -> VerificationExpectation:
    return VerificationExpectation(
        path="notes.txt",
        sha256=hashlib.sha256(b"hello").hexdigest(),
    )


def test_matching_successful_receipt_passes_all_ordered_checks() -> None:
    result = ReceiptVerifier().verify(make_receipt(), expectation())

    assert result.outcome is VerificationOutcome.PASSED
    assert [check.name for check in result.checks] == [
        "receipt_succeeded",
        "path_matches",
        "sha256_matches",
    ]
    assert all(check.passed for check in result.checks)
    assert result.summary == "all verification checks passed"


@pytest.mark.parametrize(
    ("receipt", "failed_checks"),
    [
        (
            make_receipt(outcome=ToolOutcome.FAILED, output={"message": "failed"}),
            {"receipt_succeeded", "path_matches", "sha256_matches"},
        ),
        (make_receipt(output={"path": "notes.txt"}), {"sha256_matches"}),
        (
            make_receipt(
                output={
                    "path": "other.txt",
                    "sha256": hashlib.sha256(b"hello").hexdigest(),
                }
            ),
            {"path_matches"},
        ),
        (
            make_receipt(output={"path": "notes.txt", "sha256": "0" * 64}),
            {"sha256_matches"},
        ),
    ],
)
def test_invalid_receipt_evidence_fails_named_checks(
    receipt: ToolReceipt,
    failed_checks: set[str],
) -> None:
    result = ReceiptVerifier().verify(receipt, expectation())

    assert result.outcome is VerificationOutcome.FAILED
    assert {check.name for check in result.checks if not check.passed} == failed_checks
    assert result.summary == f"{len(failed_checks)} verification checks failed"


def test_verification_messages_do_not_echo_evidence_values() -> None:
    expected = expectation()
    result = ReceiptVerifier().verify(
        make_receipt(output={"path": "wrong.txt", "sha256": "f" * 64}),
        expected,
    )
    encoded = " ".join(check.message for check in result.checks)

    assert expected.path not in encoded
    assert expected.sha256 not in encoded
    assert "wrong.txt" not in encoded
    assert "f" * 64 not in encoded


@pytest.mark.parametrize(("path", "sha256"), [("", "a"), ("notes.txt", "")])
def test_expectation_requires_non_empty_trusted_values(path: str, sha256: str) -> None:
    with pytest.raises(ValueError):
        VerificationExpectation(path=path, sha256=sha256)
