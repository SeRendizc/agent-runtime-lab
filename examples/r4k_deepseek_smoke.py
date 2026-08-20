"""One low-budget live smoke test for the R4k DeepSeek-compatible Adapter."""

from agent_runtime_lab.domain.state import RunStatus
from agent_runtime_lab.model_adapter import FinalAnswerAction, ModelInput, request_model_action
from agent_runtime_lab.openai_compatible_adapter import OpenAICompatibleModelAdapter


def main() -> None:
    expected = "R4K_SMOKE_OK"
    adapter = OpenAICompatibleModelAdapter(
        model="deepseek-chat",
        task=f"Return exactly {expected} and nothing else.",
        tools=(),
        max_tokens=16,
        temperature=0.0,
    )
    context = ModelInput.build(
        run_id="r4k-live-smoke",
        step_id="step-1",
        turn_index=0,
        state_status=RunStatus.READY,
    )
    action = request_model_action(adapter, context)
    if not isinstance(action, FinalAnswerAction) or action.answer.strip() != expected:
        raise RuntimeError("DeepSeek adapter smoke returned an unexpected bounded Action")
    print("DeepSeek adapter smoke: PASS")


if __name__ == "__main__":
    main()
