# R4e Model Adapter and Action Boundary Design

## Goal

Introduce the smallest model-facing contract required before a bounded Agent
Loop exists. A model may propose an Action, but it cannot mutate Runtime State,
authorize a Tool, or declare a run complete.

## Trust boundary

```text
Runtime-owned ModelInput
    -> ModelAdapter.next_action(...)
    -> untrusted ModelAction
    -> Action validation
    -> Runtime-owned ToolRequest identity
    -> existing Authorization / Gate / Tool / Verification path
```

`run_id`, `step_id`, `turn_index`, current `RunStatus`, and the previous trusted
observation belong to `ModelInput`. A `ToolCallAction` contains only a proposed
tool-call ID, tool name, and canonical JSON arguments. The Runtime copies its own
run and step identity into the `ToolRequest`; those fields do not come from the
model Action.

## Actions

- `ToolCallAction`: proposes one Tool invocation and must still pass the full
  Runtime authorization and execution path.
- `FinalAnswerAction`: proposes answer text only. R4e deliberately provides no
  conversion from this Action to `COMPLETED`; a future loop must define trusted
  completion verification and a terminal Event.

Unknown adapter return types fail closed at `request_model_action(...)`.

## Static adapter

`StaticModelAdapter` indexes an immutable Action tuple using the Runtime-supplied
`turn_index`. It has no internal cursor. Recreating the adapter after a process
restart and replaying the same input therefore returns the same Action.

Exhausting the script raises `ModelAdapterExhaustedError`; it never invents a
final answer or silently resets to the first Action.

## R4e non-goals

- no real model provider or network call;
- no prompt construction or token accounting;
- no multi-step state-machine cycle;
- no step budget yet;
- no model-authored Verification Expectation;
- no direct mapping from model text to terminal Runtime State.

The next design may introduce durable turn/step Events and a bounded loop. Only
then can a persistent maximum-step budget be exercised rather than existing as
dead configuration.
