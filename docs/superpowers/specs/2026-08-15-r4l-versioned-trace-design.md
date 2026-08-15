# R4l Versioned Trace and Acceptance Design

Date: 2026-08-15

## Goal

Export a stable, deterministic trajectory for evaluation without turning Trace into a
second source of Runtime truth or leaking raw model/tool data.

## Source of truth

`build_run_trace(run_id, events)` starts from `RunState.initial` and applies the normal
Reducer to every Event. A corrupt or illegal Event history therefore fails exactly as
normal Replay does. Trace never repairs, skips, or reinterprets Events.

## Trace v1

Each record contains:

- sequence, Event identity/type/time, and Event fingerprint;
- state before and after the Event;
- SHA-256 of the exact canonical payload;
- a small allowlist of non-sensitive index fields.

Raw arguments, observations, file content, answers, gate answers, and failure bodies
are not exported. Their payload digest still makes a changed source Event produce a
different Trace digest.

The run envelope contains schema version, run identity, derived final status, records,
and stable evaluation metrics: Event count, model Actions, Tool requests, Gate
escalations, verification outcomes, durable Runtime steps, and duration.

## Boundaries

- Trace is disposable and rebuildable; Event Log remains authoritative.
- Trace v1 does not claim token/cost metrics because R4k does not yet persist trusted
  Provider usage metadata.
- Trace v1 does not join raw Tool Receipts; Events retain exact effect references.
- Downstream Agent Eval may consume the canonical JSON but cannot mutate Runtime State.
