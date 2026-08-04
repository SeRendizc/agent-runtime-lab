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


class InvalidTransitionError(RuntimeContractError):
    """An event cannot be applied in the current state."""


class TerminalStateError(RuntimeContractError):
    """A new event attempted to mutate a terminal run."""
