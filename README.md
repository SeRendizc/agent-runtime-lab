# Agent Runtime Lab

Reliability infrastructure for agentic LLM execution.

Agent Runtime Lab is the runtime layer of **Reliable Agentic LLM Systems —
Runtime · Evaluation · Inference**. It studies how an agent execution can be
authorized, persisted, recovered, replayed, and validated without silently
duplicating tool effects or bypassing user gates.

## Current status

The active development branch is `durable-tool-execution-recovery`. The
deterministic R1 core, durable R2 tool-effect path, R3 ownership and gate path,
R4a restricted file-tool path, R4b verification/Fake Agent loop, R4c
verification crash recovery, R4d timeout evidence, R4e static Model Adapter /
Action boundary, R4f durable multi-turn path, and R4g persisted step budget are
implemented on that branch.
R3.3c proposal revision rollover, concurrent duplicate semantics, and its
understanding correction are complete. The R4a demonstration now lives under
`examples/` rather than the reusable Runtime package.

Published R3.3a evidence is `f880cdd` / `6955650`. R3.3b implementation and
understanding-gate evidence is `c2989b3` plus `90a384e`. R4a implementation
evidence begins at `13e29bf`, with recovery, integration, demo, and Windows
reparse coverage in `157c46f`, `a44d0db`, `6ac67b7`, and `18a1425`:

- immutable events, run state, lifecycle reduction, and ordered replay;
- SQLite tool intents and receipts with crash-window recovery;
- Runtime-owned tool registry and fail-closed unsafe retry handling;
- structured `PlanStep` contracts and trusted risk derivation;
- ownership policy with `AUTO < PAIR < USER_GATE`;
- deterministic `classify_step` decisions with explainable policy and context
  minimums;
- immutable concrete `ToolRequest` and deterministic `ALLOW` / `DENY` /
  `ESCALATE` decisions;
- trusted tool-path schemas and a workspace boundary that rejects traversal,
  absolute paths, Windows drive paths, and malformed targets;
- authorization-aware orchestration that prevents denied or escalated requests
  from reaching the external tool executor;
- durable PAIR / USER_GATE proposals bound to the exact request, revision, and
  proposal digest;
- event-replayed pause, approval, rejection, restart, and pre-tool recovery;
- operational separation between PAIR approval and USER_GATE answer evaluation;
- canonical gate answers, durable attempt counts, `PASS / RETRY / BLOCK`,
  bounded retry exhaustion, and post-pass crash recovery;
- proposal revision rollover, policy-mode upgrades, and stale-reference
  invalidation across restart;
- canonical UTF-8 `read_file`, `write_file`, and `delete_file` definitions and
  one matching in-process runner;
- exact arguments, a 1 MiB default limit, execution-time path revalidation,
  symlink/reparse rejection, regular-file checks, and sanitized OS failures;
- same-directory staged writes with flush, `fsync`, and atomic `os.replace`;
- real temporary-workspace effects through AUTO, PAIR, USER_GATE, durable
  Intent/Receipt recovery, and Event replay;
- safe retry for incomplete reads and fail-closed recovery for incomplete
  writes and deletes;
- immutable Receipt expectations and ordered Runtime-owned verification checks;
- durable `verification.succeeded` / `verification.failed` evidence that alone
  moves `VERIFYING` to a terminal state;
- a static Fake Agent proving a real read through Authorization, Receipt,
  Verification, Event replay, and `COMPLETED` while DENY/Gate cannot self-pass;
- recovery from a crash after `TOOL_SUCCEEDED` by loading the original durable
  Receipt and verifying it without invoking the Tool again;
- a distinct Runner-enforced timeout signal, durable `TIMED_OUT` Receipt,
  `tool.timed_out` Event, replayed `FAILED` state, and lossless migration of the
  pre-R4d SQLite receipt constraint;
- immutable Runtime-owned `ModelInput`, untrusted `ToolCallAction` and
  `FinalAnswerAction`, deterministic `StaticModelAdapter`, and trusted
  Action-to-`ToolRequest` compilation;
- replayed `turn_index` and active step identity, step-scoped verification that
  returns a run to `READY`, and a two-turn static Agent that resumes after a
  SQLite/Runtime restart without repeating the first Action;
- a positive `max_steps` fixed by `run.created`, plus a durable
  `run.step_budget_exhausted` failure that is checked before invoking the Model
  Adapter or submitting another Tool request;
- trusted completion evidence and distinct accepted/rejected Events that bind
  a final answer to the current durable step and prior verification;
- a bounded Agent loop that dispatches Tool and Completion Actions until a
  terminal State, durable Gate pause, or persisted budget exhaustion;
- Gate-resume recovery that reconstructs the approved Tool Action and Receipt
  without re-invoking either the Tool or Adapter;
- 206 tests pass with 1 environment-dependent symbolic-link skip, Ruff passes,
  and 48 package/test Python files pass the format check.

R3.3 and R4a-R4i engineering are published on the development branch; R4j-a is
implemented and verified in the current change. Nothing is claimed as merged
to `main`. R4a is an in-process restricted file runner for a dedicated
non-secret temporary workspace. Its path check and I/O are not one
kernel-atomic operation, so a hostile concurrent process can still create a
check/use race. It is not a
production sandbox, secret-redaction layer, Shell, or complete agent loop. R4d
records a timeout already enforced by a Runner/worker boundary; it does not
claim that the synchronous in-process Runtime can safely interrupt arbitrary
tool code.

The R4a demonstration is intentionally outside the core package and runs with:

```powershell
.venv\Scripts\python.exe examples\r4a_restricted_file_demo.py
```

The intended scope is:

- explicit authorization and user gates;
- durable execution events and tool receipts;
- crash-safe recovery;
- deterministic trace replay;
- idempotent tool execution;
- post-action validation and evidence;
- policy integration for skills such as CodeOwnership.

CodeOwnership is planned as a flagship policy and demonstration skill, not as a
second general-purpose coding agent.

## Ownership boundary

Plan classification and tool authorization are intentionally separate:

```text
PlanStep
  -> trusted risk derivation
  -> OwnershipDecision
  -> AUTO / PAIR / USER_GATE minimum

Real ToolRequest
  -> authorize again using the actual tool, arguments, and target paths
  -> deny: record failure without a tool effect
  -> allow: execute through the Durable Tool Executor
  -> escalate: persist an exact Gate Proposal and stop
       -> PAIR: matching review approval resumes the persisted request
       -> USER_GATE: evaluate a bounded answer as PASS / RETRY / BLOCK
       -> rejection: fail without a tool effect
```

`OwnershipDecision` is not an authorization token. `AuthorizationOutcome` is
also not a gate approval: `ESCALATE` means the Runtime must persist and wait.
R3.3 now enforces that wait in the reducer and durable execution path. A
USER_GATE cannot be bypassed with the PAIR approval API.

After authorization or an exact Gate approval, R4a persists the original
request as a Tool Intent and invokes the restricted runner. The runner validates
the original arguments and workspace path again immediately before real I/O;
it never treats an authorization decision as permanent filesystem proof.

Gate references bind an approval to the exact proposal, but do not authenticate
the human actor. Production identity and session authentication belong at the
trusted UI/API boundary. USER_GATE now evaluates an explicit refusal, exact
tool/path binding, typed answer fields, and a minimum non-whitespace risk
explanation before returning `PASS`, `RETRY`, or `BLOCK`.

When trusted policy changes while a request is waiting, `revise_gate()`
re-authorizes the persisted request and appends `gate.revised` with both the
expected predecessor identity and the new `revision + 1` identity. Reducer
replay atomically replaces the active proposal, resets attempts for the new
USER_GATE revision, and makes every prior digest/revision unusable at the
resolution entry points.

If the bounded Fake Agent crashes after the durable Tool Result but before a
Verification Event, the run remains `VERIFYING`. Recovery follows the persisted
`tool.succeeded.effect_id` to the original Receipt, reruns only the pure
verification checks, and then records the terminal Event. It never resubmits
the ToolRequest or repeats the external effect.

R4e gives a future model only immutable `ModelInput` and accepts one validated
Action in return. A Tool Action does not carry trusted run or step identity;
the Runtime adds those fields when compiling the existing `ToolRequest`. A
Final Answer remains an untrusted proposal and has no API that directly emits
`verification.succeeded` or changes State to `COMPLETED`.

R4f distinguishes legacy run-scoped verification from new step-scoped
verification. Legacy Events without a scope still replay to `COMPLETED`.
Step-scoped success binds to the active `step_id`, advances the durable
`turn_index`, and returns to `READY`. The next `ModelInput` is rebuilt from
replayed State plus the previous persisted verification summary. It never
depends on an Adapter-owned cursor.

R4g fixes a positive `max_steps` in the creation Event for new bounded runs.
Replay therefore restores the same budget and consumed `turn_index` after a
restart. Before requesting another Action, the Runtime checks those durable
facts. Exhaustion appends `run.step_budget_exhausted`, moves `READY` to
`FAILED`, and raises a typed error without calling the Model Adapter or Tool.
Legacy `run.created` Events without `max_steps` retain their original replay
meaning; any future upgrade must use explicit versioned migration rather than
silently reinterpret historical Events.

R4h adds a separate completion contract. `FinalAnswerAction` remains an
untrusted proposal; a trusted `CompletionVerifier` binds its exact answer to
application-owned expectations and the persisted verification observation.
Only `completion.accepted` moves `READY` to `COMPLETED`. A rejected proposal
appends `completion.rejected`, consumes that model Action, and returns to
`READY` while budget remains. Runtime performs the early budget check before
the Adapter call, and Reducer independently rejects completion or model tool
Events that would exceed the durable budget.

R4i composes those trusted turns into `run_loop`. The loop refuses legacy Runs
without persisted `max_steps`, requests exactly one Action per turn, and keeps
running only after a durable turn increment. Tool verification failure and
budget exhaustion return `FAILED`; accepted completion returns `COMPLETED`;
PAIR or USER_GATE escalation returns `PAUSED` without approving, executing, or
asking the Adapter again. Invalid, exhausted, or failing Adapters append a
sanitized `model.action_failed` Event so the Run cannot remain ambiguously
`READY` after loop failure.

R4j-a resumes a loop that paused for PAIR or USER_GATE after the exact proposal
is approved and its Tool effect reaches durable `VERIFYING`. The Runtime
reconstructs the original `ModelInput`, `ToolCallAction`, and Receipt from
Events and the Tool Effect Store; it does not ask the Adapter to recreate the
approved Action. Verification advances the original turn, then the loop calls
the Adapter only for the next turn. Failure injection immediately before the
recovered Verification proves another restart still does not repeat the Tool.

## Learning workflow

The accelerated project workflow is AI implementation and verification first,
followed by a code-grounded explanation and a Lucas understanding gate. Knowledge
ownership is demonstrated by explaining the mechanism and its boundaries; it
does not require Lucas to type each core function personally.

## Relationship to the other labs

```text
CodeOwnership Skill
    -> Agent Runtime Lab
       -> reliable execution, recovery, replay, and enforcement
    -> Agent Eval Lab
       -> traces, experiments, and failure evidence

Decoder Inference Lab
    -> model and inference-mechanism experiments
```

## Development setup

Agent Runtime Lab targets Python 3.11 or newer.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m pytest -q
python -m ruff check src tests
python -m ruff format --check src tests
```

See [docs/progress.md](docs/progress.md) for the evidence-backed milestone
history and current next step.
