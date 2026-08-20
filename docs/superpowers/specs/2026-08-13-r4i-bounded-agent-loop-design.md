# R4i Bounded Agent Loop

## Goal

Compose the already trusted Tool and Completion turns into one automatic loop
without introducing an unbounded `while`, hidden Adapter cursor, automatic Gate
approval, or a second source of step-budget truth.

## Control Flow

```text
load replayed RunState and require max_steps
-> Runtime build_model_input performs early budget check
-> Adapter proposes exactly one Action
-> ToolCallAction: authorize / Gate / execute / verify
-> FinalAnswerAction: trusted completion verification
-> continue only after durable turn_index increment
-> return COMPLETED, FAILED, or PAUSED
```

The loop itself has no counter. Its only progress measure is replayed
`turn_index`, and its only limit is `run.created.max_steps`. A legacy Run with
no persisted budget cannot enter this API.

## Exit Semantics

- Completion accepted: return `COMPLETED`.
- Completion rejected: consume the Action and continue while budget remains.
- Tool verification succeeds: consume the Tool Action and continue.
- Tool verification fails, Tool fails/times out, Authorization denies, Gate
  blocks/rejects, or budget exhausts: return `FAILED`.
- PAIR or USER_GATE escalation: return `PAUSED` immediately. The loop does not
  approve, execute, resolve expectations, or invoke the Adapter again.
- Invalid, exhausted, or failing Adapter: append sanitized
  `model.action_failed`, then return `FAILED`.

## Trusted Expectations

The model can propose Tool arguments but cannot decide what evidence proves the
Tool step correct. `ToolExpectationResolver` belongs to trusted application
code and receives the Runtime-owned context plus the proposed Action. It is
called only after the Tool produced a successful Receipt awaiting verification.

## Recovery Boundary

R4i stops cleanly at a durable Gate pause. General orchestration after Gate
approval, crash injection at loop boundaries, and Snapshot acceleration remain
R4j work. Event Replay remains authoritative; R4i adds no volatile progress
state that must be reconstructed.
