from agent_runtime_lab.completion import (
    CompletionExpectation,
    CompletionOutcome,
    CompletionVerifier,
)
from agent_runtime_lab.domain.state import RunStatus
from agent_runtime_lab.model_adapter import FinalAnswerAction, ModelInput


def context(*, observation: dict[str, object] | None = None) -> ModelInput:
    return ModelInput.build(
        run_id="run-1",
        step_id="step-1",
        turn_index=0,
        state_status=RunStatus.READY,
        observation=observation,
    )


def test_completion_verifier_rejects_model_claim_without_trusted_observation() -> None:
    result = CompletionVerifier().verify(
        FinalAnswerAction(answer="done"),
        context(),
        CompletionExpectation(expected_answer="done"),
    )

    assert result.outcome is CompletionOutcome.REJECTED
    assert [check.passed for check in result.checks] == [True, False]


def test_completion_verifier_binds_acceptance_to_exact_answer_and_observation() -> None:
    result = CompletionVerifier().verify(
        FinalAnswerAction(answer="done"),
        context(observation={"verification": {"summary": "verified", "checks": []}}),
        CompletionExpectation(expected_answer="done"),
    )

    assert result.accepted is True
    assert len(result.answer_sha256) == 64
