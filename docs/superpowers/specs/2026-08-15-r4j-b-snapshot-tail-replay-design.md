# R4j-b: Validated Snapshot + Event Tail Replay

## Goal

Accelerate state recovery without promoting a Snapshot into a second source of truth.
The Event log remains authoritative. A Snapshot is only a replaceable cache of the
state derived from one exact Event prefix.

## Invariants

1. `execution_events` remains append-only. SQLite rejects row updates and deletes.
2. Every Event stores a chain digest over the prior digest and its canonical Event
   fingerprint.
3. A Snapshot records its schema version, `next_sequence`, Event-prefix chain digest,
   canonical `RunState` JSON, and state digest.
4. Snapshot creation first proves that the supplied state equals a full replay of the
   referenced Event prefix.
5. Recovery accepts a Snapshot only when its schema, state digest, Run identity,
   sequence, fingerprint count, and Event-prefix binding all match.
6. A valid Snapshot replays only Events whose sequence is at or after
   `snapshot.next_sequence`.
7. A missing, stale, corrupt, or incompatible Snapshot is ignored; recovery performs
   full Event replay.
8. Snapshot + tail replay must equal full Event replay.

## Runtime API

`AuthorizedToolRuntime` accepts an optional `SnapshotStore`. With no Snapshot store,
existing full-replay behavior is unchanged. `create_snapshot(run_id)` always derives
the saved state using full Event replay. `load_state(run_id)` uses the validated
Snapshot when available and otherwise falls back to full replay. The Snapshot store
must be the same configured Event-store instance, preventing a Snapshot from being
validated against a different log than the Runtime's source of truth.

## Compatibility

Opening a pre-R4j-b SQLite Event table adds the chain-digest column and backfills each
run in sequence order before installing append-only triggers. Existing Runtime callers
do not need to enable Snapshot acceleration.

## Failure policy

Snapshot validation failures do not fail the Run because Snapshot is not truth. They
only remove the optimization path for that load. Event validation and reducer failures
continue to fail closed as before.
