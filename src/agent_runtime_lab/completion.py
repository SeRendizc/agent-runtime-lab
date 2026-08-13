"""Trusted validation for untrusted final-answer proposals."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum

from agent_runtime_lab.model_adapter import FinalAnswerAction, ModelInput


class CompletionOutcome(StrEnum):
    """Trusted disposition of one final-answer proposal."""

    ACCEPTED = "accepted"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class CompletionCheck:
    """One inspectable completion condition."""

    name: str
    passed: bool
    message: str

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("completion check name must be a non-empty string")
        if not isinstance(self.passed, bool):
            raise ValueError("completion check passed must be a boolean")
        if not isinstance(self.message, str) or not self.message:
            raise ValueError("completion check message must be a non-empty string")


@dataclass(frozen=True, slots=True)
class CompletionExpectation:
    """Application-owned conditions for accepting a final answer."""

    expected_answer: str
    require_verified_observation: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.expected_answer, str) or not self.expected_answer:
            raise ValueError("expected_answer must be a non-empty string")
        if not isinstance(self.require_verified_observation, bool):
            raise ValueError("require_verified_observation must be a boolean")


@dataclass(frozen=True, slots=True)
class CompletionResult:
    """Trusted evidence bound to the exact proposed answer."""

    outcome: CompletionOutcome
    answer_sha256: str
    checks: tuple[CompletionCheck, ...]
    summary: str

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, CompletionOutcome):
            raise ValueError("outcome must be a CompletionOutcome")
        if (
            not isinstance(self.answer_sha256, str)
            or len(self.answer_sha256) != 64
            or any(character not in "0123456789abcdef" for character in self.answer_sha256)
        ):
            raise ValueError("answer_sha256 must be a lowercase SHA-256 digest")
        if not isinstance(self.checks, tuple) or not self.checks:
            raise ValueError("checks must be a non-empty tuple")
        if not all(isinstance(check, CompletionCheck) for check in self.checks):
            raise ValueError("checks must contain CompletionCheck values")
        if not isinstance(self.summary, str) or not self.summary:
            raise ValueError("summary must be a non-empty string")
        checks_passed = all(check.passed for check in self.checks)
        if checks_passed != (self.outcome is CompletionOutcome.ACCEPTED):
            raise ValueError("completion outcome must agree with its checks")

    @property
    def accepted(self) -> bool:
        return self.outcome is CompletionOutcome.ACCEPTED


class CompletionVerifier:
    """Deterministically validate a proposal against trusted Runtime context."""

    def verify(
        self,
        action: FinalAnswerAction,
        context: ModelInput,
        expectation: CompletionExpectation,
    ) -> CompletionResult:
        observation = context.observation
        verification = observation.get("verification")
        has_verified_observation = (
            isinstance(verification, dict)
            and isinstance(verification.get("summary"), str)
            and isinstance(verification.get("checks"), list)
        )
        checks = (
            CompletionCheck(
                name="answer_matches",
                passed=action.answer == expectation.expected_answer,
                message=(
                    "answer matched the trusted expectation"
                    if action.answer == expectation.expected_answer
                    else "answer did not match the trusted expectation"
                ),
            ),
            CompletionCheck(
                name="verified_observation",
                passed=(not expectation.require_verified_observation or has_verified_observation),
                message=(
                    "verified observation was present"
                    if has_verified_observation
                    else "verified observation was absent"
                ),
            ),
        )
        accepted = all(check.passed for check in checks)
        return CompletionResult(
            outcome=(CompletionOutcome.ACCEPTED if accepted else CompletionOutcome.REJECTED),
            answer_sha256=hashlib.sha256(action.answer.encode("utf-8")).hexdigest(),
            checks=checks,
            summary=(
                "completion proposal accepted" if accepted else "completion proposal rejected"
            ),
        )
