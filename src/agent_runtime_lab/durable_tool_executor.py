"""Durable orchestration for external tool execution."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any, Protocol

from agent_runtime_lab.domain.errors import UnsafeToolRetryError
from agent_runtime_lab.domain.tool_effects import (
    RecoveryDecision,
    ToolIntent,
    ToolOutcome,
    ToolReceipt,
    decide_recovery,
)
from agent_runtime_lab.tool_registry import ToolRegistry


class ToolEffectStore(Protocol):
    """Persist and read durable facts for tool effects."""

    def save_intent(self, intent: ToolIntent) -> None:
        """Persist an intent before external execution."""

    def load_intent(self, effect_id: str) -> ToolIntent | None:
        """Load a persisted intent."""

    def save_receipt(self, receipt: ToolReceipt) -> None:
        """Persist an observed tool outcome."""

    def load_receipt(self, effect_id: str) -> ToolReceipt | None:
        """Load a persisted receipt."""


class ToolRunner(Protocol):
    """Invoke one external tool without owning recovery policy."""

    def invoke(
        self,
        *,
        tool_name: str,
        arguments: Mapping[str, Any],
        idempotency_key: str,
    ) -> Mapping[str, Any]:
        """Run one external tool call."""


class ExecutionCheckpoint(StrEnum):
    """Durability boundaries where a process may stop."""

    AFTER_INTENT_PERSISTED = "after_intent_persisted"
    AFTER_TOOL_INVOKED = "after_tool_invoked"
    AFTER_RECEIPT_PERSISTED = "after_receipt_persisted"


class FailureInjector(Protocol):
    """Observe execution checkpoints and optionally simulate a crash."""

    def reach(
        self,
        checkpoint: ExecutionCheckpoint,
        *,
        intent: ToolIntent,
    ) -> None:
        """Handle one execution checkpoint."""


class DurableToolExecutor:
    """Persist intent and receipt around one external tool invocation."""

    def __init__(
        self,
        *,
        store: ToolEffectStore,
        runner: ToolRunner,
        registry: ToolRegistry,
        failure_injector: FailureInjector | None = None,
    ) -> None:
        self._store = store
        self._runner = runner
        self._registry = registry
        self._failure_injector = failure_injector

    def execute(
        self,
        *,
        intent: ToolIntent,
    ) -> ToolReceipt:
        """Execute a new effect or safely resolve a durable redelivery."""

        definition = self._registry.resolve(intent.tool_name)
        persisted_intent = self._store.load_intent(intent.effect_id)

        if persisted_intent is None:
            self._store.save_intent(intent)
            self._reach_checkpoint(
                ExecutionCheckpoint.AFTER_INTENT_PERSISTED,
                intent=intent,
            )
        else:
            self._store.save_intent(intent)
            persisted_receipt = self._store.load_receipt(intent.effect_id)

            if persisted_receipt is not None:
                return persisted_receipt

            decision = decide_recovery(
                intent=persisted_intent,
                receipt=None,
                retry_is_idempotent=definition.retry_is_idempotent,
            )
            if decision is RecoveryDecision.UNKNOWN:
                raise UnsafeToolRetryError(
                    f"effect_id {intent.effect_id!r} has no receipt and cannot be retried safely"
                )

        return self._invoke_and_record(intent)

    def _invoke_and_record(self, intent: ToolIntent) -> ToolReceipt:
        try:
            output = self._runner.invoke(
                tool_name=intent.tool_name,
                arguments=intent.arguments,
                idempotency_key=intent.idempotency_key,
            )
        except Exception as exc:
            receipt = ToolReceipt.build(
                effect_id=intent.effect_id,
                outcome=ToolOutcome.FAILED,
                output={
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                },
            )
        else:
            receipt = ToolReceipt.build(
                effect_id=intent.effect_id,
                outcome=ToolOutcome.SUCCEEDED,
                output=output,
            )

        self._reach_checkpoint(
            ExecutionCheckpoint.AFTER_TOOL_INVOKED,
            intent=intent,
        )
        self._store.save_receipt(receipt)
        self._reach_checkpoint(
            ExecutionCheckpoint.AFTER_RECEIPT_PERSISTED,
            intent=intent,
        )
        return receipt

    def _reach_checkpoint(
        self,
        checkpoint: ExecutionCheckpoint,
        *,
        intent: ToolIntent,
    ) -> None:
        if self._failure_injector is not None:
            self._failure_injector.reach(checkpoint, intent=intent)
