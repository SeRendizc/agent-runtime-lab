"""Recovery queries for persisted external tool effects."""

from __future__ import annotations

from typing import Protocol

from agent_runtime_lab.domain.errors import MissingToolIntentError
from agent_runtime_lab.domain.tool_effects import (
    RecoveryDecision,
    ToolIntent,
    ToolReceipt,
    decide_recovery,
)


class ToolEffectReader(Protocol):
    """Read persisted facts required for tool-effect recovery."""

    def load_intent(self, effect_id: str) -> ToolIntent | None:
        """Load one persisted intent."""

    def load_receipt(self, effect_id: str) -> ToolReceipt | None:
        """Load one persisted receipt."""


def inspect_recovery(
    *,
    store: ToolEffectReader,
    effect_id: str,
    retry_is_idempotent: bool,
) -> RecoveryDecision:
    """Derive a recovery decision from persisted tool-effect facts."""

    intent = store.load_intent(effect_id)

    if intent is None:
        raise MissingToolIntentError(f"no persisted intent exists for effect_id {effect_id!r}")

    receipt = store.load_receipt(effect_id)

    return decide_recovery(
        intent=intent,
        receipt=receipt,
        retry_is_idempotent=retry_is_idempotent,
    )
