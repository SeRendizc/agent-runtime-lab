# R4j-c: Snapshot Recovery Failure Injection

## Goal

Prove that process interruption around Snapshot persistence, Snapshot-tail replay,
and full-replay fallback cannot turn a disposable accelerator into a second source
of truth or repeat an external Tool effect.

## Checkpoints

`SnapshotCheckpoint` exposes three deterministic crash boundaries:

1. `AFTER_SNAPSHOT_PERSISTED`: the new Snapshot transaction has committed, but
   `create_snapshot` has not returned to its caller.
2. `BEFORE_TAIL_REPLAY`: a Snapshot has passed validation, but no Event after its
   prefix has been reduced yet.
3. `BEFORE_FULL_REPLAY`: no valid Snapshot is available, but authoritative Event
   replay has not started yet.

The optional `SnapshotFailureInjector` is test infrastructure. Production behavior
is unchanged when no injector is configured, and an injector cannot be configured
without the same Snapshot/Event Store instance.

## Recovery Invariants

- A crash after Snapshot commit may leave a valid new Snapshot; recovery can use it.
- A crash before tail replay consumes no Event and mutates no Run State.
- A crash before full replay does not convert Snapshot corruption into Run failure.
- Repeating either replay path is pure: it must not call the Model Adapter or Tool.
- Gate recovery continues from the persisted Tool Action and Receipt. Tests require
  exactly one `tool.requested`, `tool.started`, and `tool.succeeded` Event across a
  Snapshot-boundary crash and a second recovery crash.
- The final recovered State remains identical to ordinary durable recovery.

## Scope Boundary

This unit closes Snapshot-specific failure windows. It does not claim process-level
Tool termination, transactional external side effects, or durable persistence of a
Model Action in the interval before its first Runtime Event. Those are separate
contracts and must not be inferred from replay safety.
