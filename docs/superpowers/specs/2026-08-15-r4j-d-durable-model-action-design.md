# R4j-d: Durable Model Action Boundary

## Problem

Persisting only the final Adapter Action leaves an unobservable crash window. If the
process stops after the Adapter returns but before `model.action_proposed` commits,
the Runtime cannot distinguish “the Adapter was never called” from “the Adapter
returned and its result was lost.” Calling it again can duplicate cost or produce a
different Action.

## Two-Event Contract

Each bounded model turn now uses two durable Events:

1. `model.action_requested` is appended before the Adapter call. It binds a stable
   invocation ID to the trusted run, step, turn, and canonical observation.
2. `model.action_proposed` is appended after validation of the Adapter result. It
   binds the exact Tool or Final Answer payload to the active invocation.

The Reducer derives two explicit states:

- `MODEL_PENDING`: the invocation intent exists but no durable result exists;
- `ACTION_PENDING`: the exact Action exists but has not yet been dispatched.

## Recovery Policy

- `MODEL_PENDING` is an unknown Adapter outcome. Without provider-side idempotency or
  a durable response lookup, recovery must not call the Adapter again. The Run writes
  a sanitized `model.action_failed` Event and fails closed.
- `ACTION_PENDING` reconstructs the original `ModelInput` and exact Action from the
  two Events. The loop dispatches that Action without calling the Adapter.
- Tool and Completion Events reference the exact `model.action_proposed` Event. A
  stale or conflicting reference is rejected by the Reducer.
- Legacy Event histories without the new Action Events retain their existing replay
  semantics. New bounded-loop writes always use the two-Event protocol.

## Failure Injection

`AFTER_MODEL_ACTION_RETURNED` simulates the unknown window before proposal
persistence. A restart proves that the Adapter is not recalled and the Run fails
closed.

`AFTER_MODEL_ACTION_PERSISTED` simulates a crash before dispatch. Restart tests prove
that both Final Answer and Tool Actions are reconstructed, the old Adapter is not
called, and the Tool lifecycle is emitted exactly once.

## Snapshot Compatibility

Snapshot schema version 2 includes the active invocation and Action Event identities.
Schema-v1 Snapshots are discarded as incompatible accelerators and recovery falls
back to authoritative Event replay.
