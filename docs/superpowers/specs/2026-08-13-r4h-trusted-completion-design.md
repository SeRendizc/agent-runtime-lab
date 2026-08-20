# R4h Trusted Completion Contract

## Problem

`FinalAnswerAction` is model output, not evidence that the task is complete. If
the Adapter could directly set `COMPLETED`, it could bypass tool verification,
application success criteria, Event replay, and the durable model-step budget.

## Contract

```text
Runtime builds ModelInput after the durable budget check
-> Adapter proposes FinalAnswerAction
-> CompletionVerifier checks the exact answer and trusted observation
-> Runtime binds evidence to current run / turn / step
-> completion.accepted or completion.rejected
-> Reducer derives the next State
```

`CompletionExpectation` is application-owned. The static R4h verifier uses an
exact expected answer and, by default, requires the previous step-scoped
verification observation. A future task-specific verifier can replace those
conditions without changing the Event or Reducer boundary.

## State Semantics

- `completion.accepted`: `READY -> COMPLETED`, increments `turn_index`.
- `completion.rejected`: `READY -> READY`, increments `turn_index`.
- Both Events must identify exactly `step-{turn_index + 1}`.
- Both Events are invalid when the replayed model-step budget is exhausted.

A rejected completion consumes an Action because the Adapter was invoked and a
decision was made. If budget remains, the next observation can lead to a
corrected proposal. If not, the existing `run.step_budget_exhausted` path fails
the Run before the Adapter is called again.

## Durable Evidence

The Event stores the proposed answer, its SHA-256 digest, inspectable checks,
the trusted step identity, and a summary. Runtime recomputes the digest and
rejects completion evidence produced for another answer or stale context.

## Compatibility

Legacy `verification.succeeded` Events retain their existing run-scoped or
step-scoped meanings. R4h adds new Event types and does not reinterpret old
logs. New bounded model flows use the completion contract; retiring legacy
completion semantics requires an explicit versioned migration.
