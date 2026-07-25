# Progress

## 2026-07-23 — Repository scaffold

Completed:

- Established the `agent-runtime-lab` repository identity.
- Defined its role under Reliable Agentic LLM Systems.
- Recorded the initial scope: authorization, durable events, recovery, replay,
  idempotency, validation, and policy enforcement.
- Positioned CodeOwnership as a runtime policy and flagship experiment rather
  than a separate general-purpose coding agent.
- Added minimal Python project metadata.

Current evidence:

- Documentation and packaging metadata only.
- No runtime execution engine has been implemented.
- No crash-recovery, persistence, replay, idempotency, or enforcement claims
  have been validated yet.

Next milestone:

1. Define the execution-event and tool-receipt data model.
2. Specify replay invariants and idempotency keys.
3. Add focused tests for duplicate tool delivery and interrupted execution.
4. Implement the smallest persistent execution path that satisfies those
   contracts.

Deferred:

- General-purpose agent loop.
- Broad tool catalog.
- Web dashboard.
- Multi-agent orchestration.
- CodeOwnership product UI.

## 2026-07-26 — S0/R1 deterministic core

Completed:

- Added a Python 3.11 `src/` package with editable-install support.
- Defined canonical immutable execution events and typed contract errors.
- Defined immutable run state and explicit terminal states.
- Implemented a pure lifecycle reducer with sequence, run, transition, tool
  identity, duplicate-delivery, and terminal-state invariants.
- Implemented deterministic ordered replay through the same reducer.

Verified evidence:

- `python -m pytest -v`: 25 tests pass.
- `python -m ruff check src tests`: passes.
- `python -m ruff format --check src tests`: passes.
- Exact duplicate delivery is a no-op; conflicting reuse of an event ID fails.
- Out-of-order and cross-run events fail explicitly.
- Terminal state accepts exact redelivery but rejects new mutation.

Lucas understanding gate:

- Explain Event versus State.
- Explain why Reducer must have no I/O, clock, or randomness.
- Explain exact duplicate delivery versus conflicting duplicate identity.
- Predict the state sequence for one successful tool lifecycle.
- Explain why R1 replay is not yet durable crash recovery.

Next milestone:

- R2 SQLite Event Store, Tool Intent/Receipt persistence, and crash-window
  recovery contracts.
