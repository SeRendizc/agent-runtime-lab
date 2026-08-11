"""Typed failures for deterministic runtime contracts."""


class RuntimeContractError(ValueError):
    """Base class for invalid deterministic-runtime input."""


class EventValidationError(RuntimeContractError):
    """An event is malformed before reduction."""


class RunMismatchError(RuntimeContractError):
    """An event belongs to a different run."""


class SequenceError(RuntimeContractError):
    """An event sequence is missing, duplicated, or out of order."""


class DuplicateEventConflictError(RuntimeContractError):
    """An event ID was reused with different content."""


class DuplicateToolEffectConflictError(RuntimeContractError):
    """A tool-effect identity was reused with different content."""


class MissingToolIntentError(RuntimeContractError):
    """A receipt was persisted before its corresponding intent."""


class UnsafeToolRetryError(RuntimeContractError):
    """An incomplete non-idempotent tool effect cannot be retried safely."""


class UnknownToolError(RuntimeContractError):
    """A requested tool is not registered with the runtime."""


class DuplicateToolDefinitionError(RuntimeContractError):
    """A tool name cannot have multiple trusted definitions."""


class ToolArgumentValidationError(RuntimeContractError):
    """A real tool received malformed or unexpected arguments."""


class WorkspaceExecutionError(RuntimeContractError):
    """A real tool target violates the execution-time workspace boundary."""


class RestrictedToolExecutionError(RuntimeError):
    """A restricted tool hit a sanitized expected filesystem failure."""


class InvalidTransitionError(RuntimeContractError):
    """An event cannot be applied in the current state."""


class GateReferenceMismatchError(RuntimeContractError):
    """A gate resolution does not identify the active durable proposal."""


class TerminalStateError(RuntimeContractError):
    """A new event attempted to mutate a terminal run."""
