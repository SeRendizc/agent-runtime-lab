"""OpenAI-compatible HTTP Model Adapter with a fail-closed response boundary."""

from __future__ import annotations

import json
import math
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlparse

from agent_runtime_lab.domain.errors import ModelActionValidationError, ModelProviderError
from agent_runtime_lab.model_adapter import (
    FinalAnswerAction,
    ModelAction,
    ModelInput,
    ToolCallAction,
)


def _canonical_object(value: Mapping[str, Any], field_name: str) -> str:
    try:
        return json.dumps(
            dict(value),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ModelActionValidationError(
            f"{field_name} must contain valid JSON object values"
        ) from exc


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ModelActionValidationError(f"{field_name} must be a non-empty string")
    return value


@dataclass(frozen=True, slots=True)
class ModelToolSpec:
    """Provider-facing tool schema; Runtime authorization remains authoritative."""

    name: str
    description: str
    parameters_json: str

    def __post_init__(self) -> None:
        _required_text(self.name, "tool name")
        _required_text(self.description, "tool description")
        try:
            parameters = json.loads(self.parameters_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ModelActionValidationError("tool parameters must be valid JSON") from exc
        if not isinstance(parameters, dict):
            raise ModelActionValidationError("tool parameters must encode a JSON object")
        object.__setattr__(self, "parameters_json", _canonical_object(parameters, "parameters"))

    @classmethod
    def build(
        cls,
        *,
        name: str,
        description: str,
        parameters: Mapping[str, Any],
    ) -> ModelToolSpec:
        return cls(
            name=name,
            description=description,
            parameters_json=_canonical_object(parameters, "parameters"),
        )

    def as_provider_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": json.loads(self.parameters_json),
            },
        }


RESTRICTED_FILE_MODEL_TOOLS = (
    ModelToolSpec.build(
        name="read_file",
        description="Read one UTF-8 file inside the Runtime workspace.",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    ),
    ModelToolSpec.build(
        name="write_file",
        description="Write UTF-8 content to one file inside the Runtime workspace.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        },
    ),
    ModelToolSpec.build(
        name="delete_file",
        description="Delete one regular file inside the Runtime workspace.",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    ),
)


class ChatCompletionsTransport(Protocol):
    """Send one JSON request across the provider boundary."""

    def create(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout_seconds: float,
    ) -> Mapping[str, Any]: ...


@dataclass(frozen=True, slots=True)
class UrllibChatCompletionsTransport:
    """Standard-library HTTPS transport with sanitized failures."""

    def create(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        request = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                response_body = response.read()
        except urllib.error.HTTPError as exc:
            raise ModelProviderError(f"model provider returned HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ModelProviderError("model provider request failed") from exc
        try:
            decoded = json.loads(response_body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ModelProviderError("model provider returned invalid JSON") from exc
        if not isinstance(decoded, dict):
            raise ModelProviderError("model provider response must be a JSON object")
        return decoded


def parse_chat_completion(response: Mapping[str, Any]) -> ModelAction:
    """Parse exactly one complete provider choice into one untrusted Action."""

    choices = response.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise ModelActionValidationError("provider response must contain exactly one choice")
    choice = choices[0]
    if not isinstance(choice, dict):
        raise ModelActionValidationError("provider choice must be an object")
    finish_reason = choice.get("finish_reason")
    message = choice.get("message")
    if not isinstance(message, dict):
        raise ModelActionValidationError("provider choice message must be an object")

    tool_calls = message.get("tool_calls")
    content = message.get("content")
    if tool_calls:
        if finish_reason != "tool_calls":
            raise ModelActionValidationError("tool call response has invalid finish reason")
        if content not in (None, ""):
            raise ModelActionValidationError("provider returned ambiguous tool call and content")
        if not isinstance(tool_calls, list) or len(tool_calls) != 1:
            raise ModelActionValidationError("provider must propose exactly one tool call")
        tool_call = tool_calls[0]
        if not isinstance(tool_call, dict) or tool_call.get("type") != "function":
            raise ModelActionValidationError("provider tool call must be a function")
        function = tool_call.get("function")
        if not isinstance(function, dict):
            raise ModelActionValidationError("provider tool call function must be an object")
        return ToolCallAction(
            tool_call_id=_required_text(tool_call.get("id"), "provider tool call id"),
            tool_name=_required_text(function.get("name"), "provider tool name"),
            arguments_json=_required_text(function.get("arguments"), "provider tool arguments"),
        )

    if finish_reason != "stop":
        raise ModelActionValidationError("final response has invalid finish reason")
    return FinalAnswerAction(answer=_required_text(content, "provider final content"))


def _validate_base_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("base_url must be an absolute HTTP(S) URL")
    if parsed.query or parsed.fragment:
        raise ValueError("base_url must not contain a query or fragment")
    return base_url.rstrip("/")


@dataclass(frozen=True, slots=True)
class OpenAICompatibleModelAdapter:
    """Call one OpenAI-compatible chat completion for each Runtime-owned turn."""

    model: str
    task: str
    tools: tuple[ModelToolSpec, ...]
    base_url: str = "https://api.deepseek.com"
    api_key_env: str = "DEEPSEEK_API_KEY"
    timeout_seconds: float = 30.0
    max_tokens: int = 512
    temperature: float = 0.0
    transport: ChatCompletionsTransport = field(
        default_factory=UrllibChatCompletionsTransport,
        repr=False,
        compare=False,
    )
    api_key_provider: Callable[[], str | None] | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        _required_text(self.model, "model")
        _required_text(self.task, "task")
        _required_text(self.api_key_env, "api_key_env")
        if not isinstance(self.tools, tuple):
            raise ValueError("tools must be an immutable tuple")
        if len({tool.name for tool in self.tools}) != len(self.tools):
            raise ValueError("tools must have unique names")
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be finite and positive")
        if isinstance(self.max_tokens, bool) or not isinstance(self.max_tokens, int):
            raise ValueError("max_tokens must be an integer")
        if self.max_tokens <= 0:
            raise ValueError("max_tokens must be positive")
        if not math.isfinite(self.temperature) or self.temperature < 0:
            raise ValueError("temperature must be finite and non-negative")
        object.__setattr__(self, "base_url", _validate_base_url(self.base_url))

    def next_action(self, context: ModelInput) -> ModelAction:
        api_key = self._load_api_key()
        response = self.transport.create(
            url=f"{self.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            payload=self._payload(context),
            timeout_seconds=self.timeout_seconds,
        )
        return parse_chat_completion(response)

    def _load_api_key(self) -> str:
        value = (
            self.api_key_provider()
            if self.api_key_provider is not None
            else os.environ.get(self.api_key_env)
        )
        if not isinstance(value, str) or not value:
            raise ModelProviderError(f"model provider credential {self.api_key_env} is not set")
        return value

    def _payload(self, context: ModelInput) -> dict[str, Any]:
        runtime_context = {
            "observation": context.observation,
            "run_id": context.run_id,
            "state_status": context.state_status.value,
            "step_id": context.step_id,
            "turn_index": context.turn_index,
        }
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You propose exactly one next action. Use one available tool when "
                        "work remains; otherwise return the final answer. Runtime identity, "
                        "authorization, execution, verification, and completion are not under "
                        "your control."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {"task": self.task, "runtime_context": runtime_context},
                        ensure_ascii=False,
                        allow_nan=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                },
            ],
            "max_tokens": self.max_tokens,
            "n": 1,
            "stream": False,
            "temperature": self.temperature,
        }
        if self.tools:
            payload["tools"] = [tool.as_provider_tool() for tool in self.tools]
            payload["tool_choice"] = "auto"
        return payload
