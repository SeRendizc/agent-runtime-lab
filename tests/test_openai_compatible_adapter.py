import json
import threading
from collections.abc import Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from agent_runtime_lab.domain.errors import ModelActionValidationError, ModelProviderError
from agent_runtime_lab.domain.state import RunStatus
from agent_runtime_lab.model_adapter import FinalAnswerAction, ModelInput, ToolCallAction
from agent_runtime_lab.openai_compatible_adapter import (
    RESTRICTED_FILE_MODEL_TOOLS,
    OpenAICompatibleModelAdapter,
    UrllibChatCompletionsTransport,
    parse_chat_completion,
)


def model_input() -> ModelInput:
    return ModelInput.build(
        run_id="run-1",
        step_id="step-2",
        turn_index=1,
        state_status=RunStatus.READY,
        observation={"path": "notes.txt", "verified": True},
    )


class RecordingTransport:
    def __init__(self, response: Mapping[str, Any]) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def create(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
        self.calls.append(
            {
                "url": url,
                "headers": dict(headers),
                "payload": dict(payload),
                "timeout_seconds": timeout_seconds,
            }
        )
        return self.response


def completion_response(content: str = "done") -> dict[str, Any]:
    return {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": content},
            }
        ]
    }


def tool_response(*, arguments: str = '{"path":"notes.txt"}') -> dict[str, Any]:
    return {
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-provider-1",
                            "type": "function",
                            "function": {"name": "read_file", "arguments": arguments},
                        }
                    ],
                },
            }
        ]
    }


def test_real_adapter_builds_one_canonical_chat_completion_request() -> None:
    transport = RecordingTransport(tool_response(arguments='{ "path": "notes.txt" }'))
    adapter = OpenAICompatibleModelAdapter(
        model="deepseek-chat",
        task="Read notes.txt and report its contents.",
        tools=RESTRICTED_FILE_MODEL_TOOLS,
        base_url="https://api.deepseek.com/",
        api_key_provider=lambda: "test-secret",
        timeout_seconds=7.5,
        max_tokens=128,
        transport=transport,
    )

    action = adapter.next_action(model_input())

    assert action == ToolCallAction(
        tool_call_id="call-provider-1",
        tool_name="read_file",
        arguments_json='{"path":"notes.txt"}',
    )
    [call] = transport.calls
    assert call["url"] == "https://api.deepseek.com/chat/completions"
    assert call["headers"] == {
        "Authorization": "Bearer test-secret",
        "Content-Type": "application/json",
    }
    assert call["timeout_seconds"] == 7.5
    payload = call["payload"]
    assert payload["model"] == "deepseek-chat"
    assert payload["n"] == 1
    assert payload["stream"] is False
    assert payload["tool_choice"] == "auto"
    assert [tool["function"]["name"] for tool in payload["tools"]] == [
        "read_file",
        "write_file",
        "delete_file",
    ]
    user_message = json.loads(payload["messages"][1]["content"])
    assert user_message == {
        "task": "Read notes.txt and report its contents.",
        "runtime_context": {
            "observation": {"path": "notes.txt", "verified": True},
            "run_id": "run-1",
            "state_status": "ready",
            "step_id": "step-2",
            "turn_index": 1,
        },
    }


def test_real_adapter_parses_final_answer_without_tools() -> None:
    transport = RecordingTransport(completion_response("verified contents"))
    adapter = OpenAICompatibleModelAdapter(
        model="deepseek-chat",
        task="Answer after verification.",
        tools=(),
        api_key_provider=lambda: "test-secret",
        transport=transport,
    )

    assert adapter.next_action(model_input()) == FinalAnswerAction(answer="verified contents")
    assert "tools" not in transport.calls[0]["payload"]


def test_standard_transport_crosses_a_real_loopback_http_boundary() -> None:
    received: dict[str, Any] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers["Content-Length"])
            received["path"] = self.path
            received["authorization"] = self.headers["Authorization"]
            received["payload"] = json.loads(self.rfile.read(length))
            body = json.dumps(completion_response("wire response")).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            del format, args

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        adapter = OpenAICompatibleModelAdapter(
            model="compatible-test-model",
            task="Return a final answer.",
            tools=(),
            base_url=f"http://127.0.0.1:{server.server_port}",
            api_key_provider=lambda: "wire-secret",
            transport=UrllibChatCompletionsTransport(),
        )

        assert adapter.next_action(model_input()) == FinalAnswerAction(answer="wire response")
    finally:
        server.shutdown()
        server.server_close()
        thread.join()

    assert received["path"] == "/chat/completions"
    assert received["authorization"] == "Bearer wire-secret"
    assert received["payload"]["model"] == "compatible-test-model"


def test_missing_credential_fails_before_transport() -> None:
    transport = RecordingTransport(completion_response())
    adapter = OpenAICompatibleModelAdapter(
        model="deepseek-chat",
        task="Finish.",
        tools=(),
        api_key_env="ABSENT_TEST_PROVIDER_KEY",
        transport=transport,
    )

    with pytest.raises(ModelProviderError, match="ABSENT_TEST_PROVIDER_KEY"):
        adapter.next_action(model_input())
    assert transport.calls == []


@pytest.mark.parametrize(
    ("response", "message"),
    [
        ({"choices": []}, "exactly one choice"),
        (
            {
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {"content": "partial"},
                    }
                ]
            },
            "finish reason",
        ),
        (
            {
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "content": "also answer",
                            "tool_calls": tool_response()["choices"][0]["message"]["tool_calls"],
                        },
                    }
                ]
            },
            "ambiguous",
        ),
        (
            {
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "content": None,
                            "tool_calls": tool_response()["choices"][0]["message"]["tool_calls"]
                            * 2,
                        },
                    }
                ]
            },
            "exactly one tool call",
        ),
        (tool_response(arguments="not-json"), "valid JSON"),
    ],
)
def test_provider_response_ambiguity_or_corruption_fails_closed(
    response: dict[str, Any],
    message: str,
) -> None:
    with pytest.raises(ModelActionValidationError, match=message):
        parse_chat_completion(response)


@pytest.mark.parametrize(
    "base_url",
    ["api.deepseek.com", "ftp://api.deepseek.com", "https://api.deepseek.com?q=secret"],
)
def test_adapter_rejects_unsafe_or_ambiguous_base_urls(base_url: str) -> None:
    with pytest.raises(ValueError, match="base_url"):
        OpenAICompatibleModelAdapter(
            model="deepseek-chat",
            task="Finish.",
            tools=(),
            base_url=base_url,
        )
