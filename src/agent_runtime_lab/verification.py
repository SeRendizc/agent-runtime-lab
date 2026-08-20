"""Runtime-owned verification of durable tool evidence."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from agent_runtime_lab.domain.tool_effects import ToolOutcome, ToolReceipt


class VerificationOutcome(StrEnum):
    """Overall result of deterministic evidence checks."""

    PASSED = "passed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class VerificationExpectation:
    """Trusted evidence expected from one completed tool effect."""

    path: str
    sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.path, str) or not self.path:
            raise ValueError("verification path must be a non-empty string")
        if not isinstance(self.sha256, str) or not self.sha256:
            raise ValueError("verification sha256 must be a non-empty string")


@dataclass(frozen=True, slots=True)
class VerificationCheck:
    """One named verification observation."""

    name: str
    passed: bool
    message: str


@dataclass(frozen=True, slots=True)
class VerificationResult:
    """Complete ordered evidence used for a terminal Runtime decision."""

    outcome: VerificationOutcome
    checks: tuple[VerificationCheck, ...]
    summary: str

    @property
    def passed(self) -> bool:
        """Return whether every trusted verification check passed."""

        return self.outcome is VerificationOutcome.PASSED


class ReceiptVerifier:
    """Compare a durable Receipt with trusted path and digest expectations."""

    def verify(
        self,
        receipt: ToolReceipt,
        expectation: VerificationExpectation,
    ) -> VerificationResult:
        """Return ordered checks without echoing evidence values."""

        output = receipt.output
        checks = (
            VerificationCheck(
                name="receipt_succeeded",
                passed=receipt.outcome is ToolOutcome.SUCCEEDED,
                message=(
                    "receipt outcome is succeeded"
                    if receipt.outcome is ToolOutcome.SUCCEEDED
                    else "receipt outcome is not succeeded"
                ),
            ),
            VerificationCheck(
                name="path_matches",
                passed=output.get("path") == expectation.path,
                message=(
                    "receipt output path matches expectation"
                    if output.get("path") == expectation.path
                    else "receipt output path does not match expectation"
                ),
            ),
            VerificationCheck(
                name="sha256_matches",
                passed=output.get("sha256") == expectation.sha256,
                message=(
                    "receipt output sha256 matches expectation"
                    if output.get("sha256") == expectation.sha256
                    else "receipt output sha256 does not match expectation"
                ),
            ),
        )
        failed_count = sum(not check.passed for check in checks)
        if failed_count == 0:
            return VerificationResult(
                outcome=VerificationOutcome.PASSED,
                checks=checks,
                summary="all verification checks passed",
            )
        return VerificationResult(
            outcome=VerificationOutcome.FAILED,
            checks=checks,
            summary=f"{failed_count} verification checks failed",
        )
