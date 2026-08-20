import json
from dataclasses import FrozenInstanceError

import pytest

from agent_runtime_lab.domain.errors import (
    InvalidTransitionError,
    ModelActionValidationError,
    ModelAdapterExhaustedError,
)
from agent_runtime_lab.domain.state import RunStatus
from agent_runtime_lab.model_adapter import (
    FinalAnswerAction,
    ModelInput,
    StaticModelAdapter,
    ToolCallAction,
    request_model_action,
    tool_request_from_action,
)


def model_input(
    *,
    turn_index: int = 0,
    status: RunStatus = RunStatus.READY,
) -> ModelInput:
    return ModelInput.build(
        run_id="run-1",
        step_id=f"step-{turn_index + 1}",
        turn_index=turn_index,
        state_status=status,
        observation={"previous": "verified"},
    )


def test_model_input_and_tool_action_are_canonical_and_immutable() -> None:
    context = model_input()
    action = ToolCallAction.build(
        tool_call_id="call-1",
        tool_name="read_file",
        arguments={"path": "notes.txt", "options": {"encoding": "utf-8"}},
    )

    assert context.observation_json == '{"previous":"verified"}'
    assert action.arguments_json == ('{"options":{"encoding":"utf-8"},"path":"notes.txt"}')

    decoded = action.arguments
    decoded["path"] = "changed.txt"
    assert action.arguments["path"] == "notes.txt"

    with pytest.raises(FrozenInstanceError):
        context.turn_index = 2  # type: ignore[misc]


def test_static_adapter_is_deterministic_without_a_hidden_cursor() -> None:
    first = ToolCallAction.build(
        tool_call_id="call-1",
        tool_name="read_file",
        arguments={"path": "notes.txt"},
    )
    second = FinalAnswerAction(answer="done")
    adapter = StaticModelAdapter(actions=(first, second))

    assert request_model_action(adapter, model_input(turn_index=0)) == first
    assert request_model_action(adapter, model_input(turn_index=0)) == first
    assert request_model_action(adapter, model_input(turn_index=1)) == second

    restarted = StaticModelAdapter(actions=(first, second))
    assert request_model_action(restarted, model_input(turn_index=1)) == second


def test_runtime_identity_is_added_when_tool_action_becomes_request() -> None:
    context = model_input()
    action = ToolCallAction.build(
        tool_call_id="call-1",
        tool_name="read_file",
        arguments={"path": "notes.txt"},
    )

    request = tool_request_from_action(context, action)

    assert request.run_id == "run-1"
    assert request.step_id == "step-1"
    assert request.tool_call_id == "call-1"
    assert request.tool_name == "read_file"
    assert request.arguments == {"path": "notes.txt"}


def test_tool_action_cannot_be_compiled_outside_ready_state() -> None:
    action = ToolCallAction.build(
        tool_call_id="call-1",
        tool_name="read_file",
        arguments={"path": "notes.txt"},
    )

    with pytest.raises(InvalidTransitionError, match="requires ready"):
        tool_request_from_action(
            model_input(status=RunStatus.VERIFYING),
            action,
        )


def test_final_answer_cannot_be_compiled_as_a_tool_request() -> None:
    with pytest.raises(ModelActionValidationError, match="tool-call action"):
        tool_request_from_action(model_input(), FinalAnswerAction(answer="done"))


def test_adapter_output_is_validated_at_the_trust_boundary() -> None:
    class InvalidAdapter:
        def next_action(self, context: ModelInput) -> object:
            del context
            return {"status": "completed"}

    with pytest.raises(ModelActionValidationError, match="unsupported action"):
        request_model_action(InvalidAdapter(), model_input())  # type: ignore[arg-type]


def test_static_adapter_fails_closed_when_script_is_exhausted() -> None:
    adapter = StaticModelAdapter(actions=(FinalAnswerAction(answer="only one action"),))

    with pytest.raises(ModelAdapterExhaustedError, match="turn 1"):
        request_model_action(adapter, model_input(turn_index=1))


def test_static_adapter_rejects_mutable_action_sequences() -> None:
    with pytest.raises(ModelActionValidationError, match="immutable tuple"):
        StaticModelAdapter(  # type: ignore[arg-type]
            actions=[FinalAnswerAction(answer="mutable")]
        )


@pytest.mark.parametrize(
    "bad_observation",
    [[], "text", float("nan")],
)
def test_model_input_rejects_non_object_or_non_json_observation(
    bad_observation: object,
) -> None:
    with pytest.raises(ModelActionValidationError):
        ModelInput(
            run_id="run-1",
            step_id="step-1",
            turn_index=0,
            state_status=RunStatus.READY,
            observation_json=json.dumps(bad_observation),
        )
