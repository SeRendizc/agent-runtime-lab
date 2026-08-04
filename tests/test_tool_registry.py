from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from agent_runtime_lab.domain.errors import (
    DuplicateToolDefinitionError,
    UnknownToolError,
)
from agent_runtime_lab.tool_registry import ToolDefinition, ToolRegistry


def test_registry_resolves_trusted_tool_definition() -> None:
    definition = ToolDefinition(
        tool_name="append_file",
        retry_is_idempotent=False,
    )
    registry = ToolRegistry([definition])

    assert registry.resolve("append_file") is definition


def test_registry_rejects_unknown_tool() -> None:
    registry = ToolRegistry([])

    with pytest.raises(
        UnknownToolError,
        match="is not registered",
    ):
        registry.resolve("untrusted_tool")


def test_registry_rejects_duplicate_tool_name() -> None:
    with pytest.raises(
        DuplicateToolDefinitionError,
        match="registered more than once",
    ):
        ToolRegistry(
            [
                ToolDefinition(
                    tool_name="append_file",
                    retry_is_idempotent=False,
                ),
                ToolDefinition(
                    tool_name="append_file",
                    retry_is_idempotent=True,
                ),
            ]
        )


def test_tool_definition_is_immutable() -> None:
    definition = ToolDefinition(
        tool_name="append_file",
        retry_is_idempotent=False,
    )

    with pytest.raises(FrozenInstanceError):
        definition.retry_is_idempotent = True
