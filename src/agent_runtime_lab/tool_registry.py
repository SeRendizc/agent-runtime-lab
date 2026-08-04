"""Trusted metadata for runtime-owned tool behavior."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from agent_runtime_lab.domain.errors import (
    DuplicateToolDefinitionError,
    UnknownToolError,
)


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """Describe trusted execution properties for one registered tool."""

    tool_name: str
    retry_is_idempotent: bool


class ToolRegistry:
    """Resolve tool metadata owned by the runtime rather than the model."""

    def __init__(self, definitions: Iterable[ToolDefinition]) -> None:
        self._definitions: dict[str, ToolDefinition] = {}

        for definition in definitions:
            if definition.tool_name in self._definitions:
                raise DuplicateToolDefinitionError(
                    f"tool {definition.tool_name!r} is registered more than once"
                )

            self._definitions[definition.tool_name] = definition

    def resolve(self, tool_name: str) -> ToolDefinition:
        """Return trusted metadata for a registered tool."""

        try:
            return self._definitions[tool_name]
        except KeyError:
            raise UnknownToolError(f"tool {tool_name!r} is not registered") from None
