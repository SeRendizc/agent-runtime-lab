# R4j-a Gate Resume and Crash Recovery

## Problem

R4i correctly returns `PAUSED` when a Tool Action reaches PAIR or USER_GATE.
After approval or PASS, the Tool runs and the durable Run becomes `VERIFYING`.
Calling the Adapter again at the old turn would regenerate an Action that has
already been approved and executed, risking duplicate effects or divergence.

## Durable Reconstruction

`load_model_tool_recovery` requires `VERIFYING` plus an active model `step_id`.
It reconstructs:

- the original Runtime-owned `ModelInput` from replayed turn and prior
  observation;
- the exact `ToolCallAction` from persisted `tool.requested` payload;
- the successful `ToolReceipt` referenced by durable `tool.succeeded`.

No Adapter call or Tool execution occurs during reconstruction.

## Resume Flow

```text
AWAITING_GATE
-> exact PAIR approval or USER_GATE PASS
-> Tool executes once and persists Receipt
-> VERIFYING
-> reconstruct context / Action / Receipt
-> trusted Verification
-> durable turn_index increment
-> Adapter called for next turn only
-> bounded loop continues
```

## Failure Injection

`BEFORE_RECOVERED_VERIFICATION` simulates a crash after durable Gate execution
but before the pure Verification Event. On another restart, reconstruction
finds the same Receipt and repeats only Verification. Tests assert exactly one
`tool.requested`, `tool.started`, and `tool.succeeded` Event.

## Snapshot Boundary

Snapshot is deferred to R4j-b. It must identify and validate the Event prefix
used to produce the cached State, then replay the remaining tail. An unchecked
serialized `RunState` would compete with the Event log and could silently
restore stale or corrupted state, so it is not accepted as an implementation.
