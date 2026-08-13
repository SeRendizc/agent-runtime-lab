# Progress

## 2026-07-23 — Repository scaffold

Completed:

- Established the `agent-runtime-lab` repository identity.
- Defined authorization, durable events, recovery, replay, idempotency,
  validation, and policy enforcement as the project scope.
- Positioned CodeOwnership as a runtime policy and flagship experiment rather
  than a separate general-purpose coding agent.

## 2026-07-26 — R1 deterministic core

Completed:

- Defined immutable execution events and run state.
- Implemented a pure lifecycle reducer with transition, sequence, duplicate,
  run-identity, tool-identity, and terminal-state invariants.
- Implemented deterministic ordered replay through the same reducer.

Verified evidence:

- 25 tests passed.
- Ruff check and format check passed.

## 2026-08-04 — R2 durable tool execution and recovery

Completed:

- Added stable tool-effect identity, `ToolIntent`, `ToolReceipt`, and recovery
  decisions.
- Added append-only SQLite event and tool-effect stores.
- Persisted intent before execution and receipt after execution.
- Covered crashes after intent, after tool execution, and after receipt.
- Added an immutable Runtime-owned Tool Registry.
- Rejected unknown tools before intent persistence and external execution.
- Removed caller-controlled retry semantics from `DurableToolExecutor`.
- Returned an existing receipt without re-executing the tool.
- Failed closed with `UnsafeToolRetryError` when an incomplete effect could not
  be proven safe to retry.

Verified evidence at the end of R2.7b:

- 9 durable executor tests passed.
- 4 Tool Registry tests passed.
- 61 tests passed in the full suite.
- Ruff check and format check passed.

Trust boundary:

- Registration does not mean authorization.
- An idempotency key is stable request identity, not an end-to-end exactly-once
  guarantee unless the downstream system also deduplicates by that key.

## 2026-08-09 — R3.1 trusted risk and ownership classification

R3.1a completed:

- Added immutable `PlanStep` contracts for claimed paths, proposed tools,
  dependencies, and model-reported risk tags.
- Added Runtime-owned `RiskRule`, `RiskEvaluator`, and `RiskAssessment`.
- Defined `effective_tags = claimed_tags | derived_tags`, so model output can
  add risk but cannot erase Runtime-derived risk.
- Added deterministic path normalization and tool-name matching.

R3.1b completed:

- Added `OwnershipMode`: `AUTO < PAIR < USER_GATE`.
- Added policy rules that map effective risks to minimum ownership modes.
- Added a global context minimum that can only make a decision stricter.
- Implemented `classify_step` and explainable `OwnershipDecision` fields:
  policy minimum, context minimum, final mode, risk assessment, and reasons.
- Added 8 ownership policy contract tests.

Evidence:

```text
Branch: durable-tool-execution-recovery
Commit: 4b4146f
Ownership Policy: 8 passed
Full suite: 75 passed in 2.28s
Ruff: All checks passed!
Format: 30 files already formatted
Worktree: clean
Remote: branch pushed
PR: not opened
```

Lucas understanding gate:

- Explained why claimed risks cannot erase derived risks.
- Explained why a global `PAIR` minimum upgrades `AUTO` but cannot lower
  `USER_GATE`.
- Distinguished policy minimum, context minimum, and final mode.
- Distinguished plan classification from execution authorization.

## Current milestone — R3.2 ToolRequest authorization

Implemented and verified:

- Added immutable, canonical `ToolRequest` with run, step, call, tool, and
  argument identity.
- Added deterministic `ALLOW`, `DENY`, and `ESCALATE` decisions with reasons.
- Added trusted path argument metadata to `ToolDefinition`.
- Added a workspace boundary that rejects traversal, absolute paths, Windows
  drive paths, and malformed targets.
- Re-evaluated real tool names and normalized paths through Runtime-owned risk
  and ownership policy.
- Covered normal allow, unknown tool, path escape, malformed path, core path,
  destructive tool, global PAIR minimum, and repeated-decision cases.

Evidence:

```text
Branch: durable-tool-execution-recovery
Commit: 2add6c8
Authorization: 10 passed
Full suite: 85 passed in 0.33s
Ruff: All checks passed!
Format: 32 files already formatted
Remote: branch pushed
PR: not opened
```

Lucas understanding gate — completed 2026-08-10:

- Explained that plan-level `AUTO` cannot authorize a later concrete request.
- Distinguished Registry rejection from Workspace Boundary rejection.
- Explained that valid high-ownership work escalates for human participation
  rather than being denied as an invalid request.
- Clarified that ordinary low-risk writes may remain `AUTO`; modification alone
  does not imply USER_GATE.

## Current milestone — R3.3 durable ownership gates

R3.3a implemented and verified:

- Added `AWAITING_GATE`, `TOOL_ESCALATED`, `GATE_APPROVED`, and `GATE_REJECTED`
  to the deterministic Event/Reducer state machine.
- Added an exact `GateReference` bound to `run_id`, `tool_call_id`,
  `proposal_digest`, and Runtime-owned revision.
- Derived the proposal digest from the immutable concrete request, normalized
  paths, ownership mode, and revision.
- Added `AuthorizedToolRuntime` to enforce the complete
  Authorization -> Event/Reducer -> DurableToolExecutor path.
- Ensured `DENY` and `ESCALATE` never persist a Tool Intent or invoke the Tool.
- Rebuilt the original `ToolRequest` from persisted events after approval;
  callers cannot substitute new execution arguments during resume.
- Rechecked current Authorization before executing an approved proposal.
- Persisted PAIR and USER_GATE mode, approval actor/reason, rejection reason,
  and exact proposal reference.
- Covered process restart while awaiting a gate and the crash window after
  `GATE_APPROVED` but before `TOOL_STARTED`.
- Rejected stale or mismatched proposal revisions without executing a Tool.

Evidence:

```text
Branch: durable-tool-execution-recovery (publish target)
Published code commit: f880cdd
Published docs commit: 6955650
R3.3 targeted tests: 18 passed
Full suite: 93 passed in 0.51s
Ruff: All checks passed!
Format: 35 files already formatted
PR: not opened
```

Important boundary:

- A Gate reference proves which persisted proposal is being resolved; it is not
  a digital signature or user-authentication mechanism.
- Only a trusted UI/API adapter may construct and submit `GateResolution` in a
  deployed system. The model must never receive that capability as a Tool.
- R3.3a uses one shared approve/reject substrate for PAIR and USER_GATE while
  retaining their exact mode. R3.3b will add USER_GATE answer evaluation and
  durable attempt counts through the reserved `evaluate_gate` learning boundary.
- Revision rollover/new-proposal invalidation is represented in the contract
  but replacement-proposal orchestration is not yet implemented.

Lucas understanding gate — completed 2026-08-10:

- Explain why `ESCALATE` is a coarse Runtime action while PAIR/USER_GATE are
  precise ownership modes.
- Explain why approval must reference the exact proposal rather than use a
  boolean `approved=True`.
- Explain which guarantee proposal digest provides and which authentication
  guarantee it deliberately does not provide.

Lucas explained that exact proposal binding prevents the approved object from
being substituted, while it neither proves the proposal is correct nor
authenticates the approving actor. He also distinguished the shared pause/replay
substrate from the different PAIR and USER_GATE evaluation semantics.

## R3.3b — USER_GATE evaluation attempts

Implementation, verification, and Lucas understanding gate complete:

- Added immutable canonical `GateAnswerSubmission`.
- Added persistable `GateEvaluation` with `PASS`, `RETRY`, and `BLOCK`.
- Added `gate.evaluated` events and durable attempt/max-attempt state.
- Enforced monotonic attempts and fail-closed retry exhaustion in the Reducer.
- Prevented `GateResolution.approve(...)` from bypassing USER_GATE evaluation.
- Preserved PAIR review through the existing approval/rejection path.
- Replayed failed attempts across restart and continued at the next attempt.
- Recovered a passed answer after a crash before `TOOL_STARTED`.
- Implemented the default `evaluate_gate(gate, answer)` contract:
  - explicit `refuse=True` returns `BLOCK`;
  - missing or incorrectly typed fields return `RETRY`;
  - mismatched tool/path identity returns `RETRY`;
  - fewer than 20 non-whitespace explanation characters returns `RETRY`;
  - an exact, sufficiently explained answer returns `PASS`.
- Kept injected Fake Evaluators for deterministic Runtime infrastructure tests
  while adding real default-evaluator integration tests.
- Removed the obsolete developer-owned placeholder exception.

Evidence:

```text
Attempt infrastructure commit: c2989b3
Concrete evaluator commit: 90a384e
R3.3 targeted tests: 31 passed
Full suite: 106 passed in 0.93s
Ruff: All checks passed!
Format: 35 files already formatted
Remote: published through 478a8e1 on durable-tool-execution-recovery
```

Workflow decision:

- Accelerated mode is `AI implements and verifies -> AI teaches from the real
  code -> Lucas answers the understanding gate -> continue`.
- Knowledge ownership is measured by explanation and review, not by requiring
  Lucas to personally type reserved functions.

Lucas understanding gate — completed 2026-08-10:

- Distinguished pure answer evaluation from Runtime state mutation and effects.
- Explained why answer tool/path identity must match the proposal while final
  execution still uses the persisted `ToolRequest`.
- Located retry exhaustion in Runtime lifecycle policy rather than in the pure
  evaluator.

## R3.3c — proposal revision rollover

Implementation, verification, and Lucas correction complete:

- Added `gate.revised` as the only event that replaces an active gate proposal.
- Required the event to name the exact active predecessor digest and revision.
- Enforced `new_revision == previous_revision + 1` and a new proposal digest.
- Re-authorized the original persisted `ToolRequest` under the current trusted
  policy before constructing the replacement proposal.
- Atomically replayed the new digest, revision, ownership mode, and USER_GATE
  attempt limit while resetting attempts to zero for the new proposal.
- Invalidated old PAIR approvals and USER_GATE answers at Runtime entry points.
- Covered restart after rollover and policy upgrades from PAIR to USER_GATE.
- Kept concurrent rollover deterministic through the Event Store transaction:
  exact redelivery is coalesced into one durable event, while a conflicting
  proposal for the same event identity fails closed instead of being promoted
  to an unreviewed later revision.

Evidence:

```text
Published code commit: f810ca9
Concurrent Event Store evidence: 2fdc4f6
Targeted Runtime + Reducer + Event Store tests: 46 passed
Full suite: 115 passed
Ruff: All checks passed!
Format: 35 Python files already formatted
Remote: published on durable-tool-execution-recovery
```

Lucas correction confirmed:

- a new proposal replaces the active proposal identity rather than branching
  into an independently resolvable state;
- a revision is newly derived from the persisted request and current policy,
  not copied from the unsuccessful proposal parameters;
- concurrent exact redelivery coalesces to one durable event, while conflicting
  duplicate content fails closed instead of being renamed or auto-promoted.

## Current milestone — R4a restricted file tools

Engineering complete; code-grounded understanding gate remains separate:

- Added canonical trusted metadata and one in-process runner for exactly
  `read_file`, `write_file`, and `delete_file`.
- Enforced exact string arguments without coercion and a trusted 1 MiB default
  UTF-8 byte limit.
- Revalidated original relative paths immediately before I/O, rejected every
  `..` component, absolute/drive paths, directories, non-regular files,
  missing write parents, symbolic links, and Windows reparse points.
- Returned only relative paths and structured byte/digest evidence; expected OS
  failures retain an errno/winerror code without the absolute workspace root or
  full host error text.
- Implemented same-directory exclusive staging, flush, `fsync`, and
  `os.replace` for complete-file writes, with ordinary-failure cleanup.
- Proved real temporary-workspace effects through AUTO read, PAIR write,
  USER_GATE delete, escape denial before Intent, and restart from the exact
  persisted request.
- Proved incomplete reads are safely retried while incomplete writes and
  deletes raise `UnsafeToolRetryError` without repeating a real effect.
- Added `examples/r4a_restricted_file_demo.py`, which emits only relative paths,
  Event types, Receipt outcomes, digests, and fail-closed recovery evidence.

Evidence:

```text
Plan commit: fb9fca1
Runner implementation: 13e29bf
Recovery tests: 157c46f
Authorization/Gate integration: a44d0db
Demo: 6ac67b7
Windows reparse evidence: 18a1425
R4a targeted tests: 30 passed, 1 skipped
Full suite: 145 passed, 1 skipped
Ruff: All checks passed!
Format: 40 Python files already formatted
Compileall: passed
Demo: exited 0 with sanitized JSON
```

Trust boundary:

- This is a real in-process file runner, not a process, Docker, cloud, or
  kernel-enforced sandbox.
- Authorization, execution-time path validation, and sandbox isolation are
  different controls; R4a implements the first two only.
- Path validation and I/O retain a documented check/use race. The demo and
  integration tests therefore use dedicated non-secret temporary workspaces.
- A successful read Receipt intentionally contains bounded file content; R4a
  does not claim secret redaction.
- Process-crash recovery is covered. Machine power loss and directory-metadata
  durability are not.

## Current milestone — R4b verification evidence and Fake Agent

Engineering implemented and verified:

- Moved the R4a runnable demonstration from the reusable package to
  `examples/r4a_restricted_file_demo.py` without changing its behavior.
- Added immutable `VerificationExpectation`, `VerificationCheck`, and
  `VerificationResult` contracts plus a pure `ReceiptVerifier`.
- Verification checks the durable Receipt outcome, exact relative path, and
  exact SHA-256 without echoing file content, paths, or digests in messages.
- Added `AuthorizedToolRuntime.record_verification(...)`, which persists
  ordered checks through the existing verification Events. The Reducer remains
  the authority that moves `VERIFYING` to `COMPLETED` or `FAILED`.
- Added a static `FakeAgent` that submits one immutable request and cannot
  declare success itself. DENIED and AWAITING_GATE requests raise rather than
  producing verification evidence.
- Proved a real temporary file flows through Authorization, restricted read,
  durable Receipt, verification Event, replay, and `COMPLETED`; a wrong digest
  deterministically ends in `FAILED`.

Evidence:

```text
Design: b6c846a
Plan: 4e9b2e6
Implementation: 2529cd5
R4b targeted tests: 14 passed
Full suite: 159 passed, 1 skipped
Ruff: All checks passed!
Format: 46 Python files already formatted
```

Next executable step:

1. Teach R4a/R4b from the real files and complete the understanding check.
2. Do not generalize the static Fake Agent into an unbounded loop or real model
   adapter before the next design is approved.

Still deferred:

- Real model integration.
- Multi-agent orchestration.
- Long-term memory or RAG.
- Cloud sandboxing and production worker orchestration.
- Starting the standalone CodeOwnership Skill or cross-project S6 work before
  Runtime authorization and gate recovery are stable.

## Current milestone — R4c verification crash recovery

Engineering, publication, and Lucas understanding gate complete:

- Added a Fake Agent checkpoint immediately after a durable Tool Result and
  before verification, allowing the exact crash window to be injected.
- Added Runtime recovery of the successful Receipt referenced by the persisted
  `tool.succeeded.effect_id` while the replayed state is `VERIFYING`.
- Added a read-only Durable Executor receipt lookup; recovery does not call the
  Tool Runner or persist a second Intent/Receipt.
- Added fail-closed errors for non-`VERIFYING` recovery and missing or
  inconsistent verification evidence.
- Proved restart recovery reaches `COMPLETED` with exactly one `tool.started`
  Event.

Evidence:

```text
Import fix: d753665
R4c targeted tests: 17 passed
Full suite: 161 passed, 1 skipped
Ruff: All checks passed!
Format: 46 Python files already formatted
```

Lucas understanding gate — completed 2026-08-11:

- Explained that resubmitting the persisted request would represent a new Tool
  execution and could repeat an external side effect.
- Explained why missing Receipt evidence after `tool.succeeded` must fail closed
  instead of rerunning the Tool.
- Restricted verification recovery to `VERIFYING`; `READY` has no successful
  Receipt to verify and `COMPLETED` is already terminal.

## Current milestone — R4d durable timeout evidence

Engineering, publication, and Lucas understanding gate complete:

- Added `ToolTimeoutError(timeout_seconds)` as the typed signal emitted only
  after a Tool Runner or isolated worker has enforced its own deadline.
- Added a distinct durable `ToolOutcome.TIMED_OUT` Receipt rather than folding
  timeout into a generic Tool failure.
- Added `tool.timed_out`; Reducer replay deterministically moves
  `TOOL_RUNNING -> FAILED` and preserves the timeout reason.
- Added a transactional SQLite migration that expands the legacy Receipt
  outcome constraint while preserving existing Intent/Receipt evidence.
- Kept deadline enforcement out of the synchronous in-process executor: it
  observes and persists the Runner's timeout, but cannot safely kill arbitrary
  Python tool code.

Evidence:

```text
Implementation: 714626d
R4d targeted tests: 57 passed
Full suite: 165 passed, 1 skipped
Ruff: All checks passed!
Format: 46 Python files already formatted
Diff check: clean
```

Lucas understanding gate — completed 2026-08-11:

- Distinguished a caller-observed thread wait timeout from actual Tool
  termination.
- Explained why timeout evidence must remain distinct for recovery, audit, and
  retry policy because background execution may be unknown.
- Explained why the SQLite migration must preserve existing Receipts as durable
  recovery evidence.

## R4e static Model Adapter / Action boundary

Engineering, publication, and Lucas understanding gate completed:

- Added immutable canonical `ModelInput` containing Runtime-owned run, step,
  turn, state-status, and trusted observation fields.
- Added untrusted `ToolCallAction` and `FinalAnswerAction` contracts.
- Added a `ModelAdapter` Protocol and deterministic `StaticModelAdapter` with no
  hidden cursor; the Runtime-supplied `turn_index` selects the Action.
- Added fail-closed validation for unknown Action types and exhausted static
  scripts.
- Added `tool_request_from_action(...)`, which only accepts a Tool Action in
  `READY` and supplies trusted `run_id` / `step_id` from `ModelInput`.
- Deliberately left `FinalAnswerAction` unable to emit a completion Event or
  mutate Runtime State.
- Proved a static Tool Action flows through the existing real restricted-file,
  authorization, durable Receipt, verification, and terminal-state path.

Evidence:

```text
Implementation: 08fc8fd
R4e targeted tests: 18 passed
Full suite: 177 passed, 1 skipped
Ruff: All checks passed!
Format: 46 package/test Python files already formatted
Diff check: clean
```

Lucas understanding gate — completed 2026-08-12:

- Explained that model-proposed Tool Actions still require Runtime-owned
  identity, authorization, gates, durable execution, and verification.
- Explained that Runtime-supplied `turn_index` makes static Action selection
  reproducible after restart instead of depending on a lost in-memory cursor.
- Explained that `FinalAnswerAction` is not completion because trusted evidence,
  a completion Event, and Reducer-owned terminal transition are absent.

## R4f durable verified tool turns

Engineering, publication, and Lucas understanding gate complete:

- Added replayed `turn_index` and `active_step_id` to immutable `RunState`.
- Preserved backward compatibility: legacy `verification.succeeded` Events
  without `scope` still complete their historical single-step runs.
- Added step-scoped verification bound to the active `step_id`; successful
  verification performs `VERIFYING -> READY`, advances `turn_index`, and clears
  the active step. Failed verification remains terminal and never advances.
- Added Runtime-owned `build_model_input(...)`, deriving `step-N` and
  `turn_index` from replayed State rather than caller or model input.
- Rebuilt the next trusted observation from the previous persisted verification
  summary and checks, without persisting raw file content in Run State.
- Added `ModelDrivenFakeAgent.run_tool_turn(...)` for exactly one verified,
  non-terminal model-proposed Tool Action.
- Proved two tool turns across SQLite close/reopen and Runtime/Adapter rebuild:
  restart selects `call-2 / step-2`, not the first Action again.
- Proved `FinalAnswerAction` remains fail-closed and produces no Runtime Event
  without a future completion contract.

Evidence:

```text
Implementation: b6ad169
R4f targeted tests: 44 passed
Full suite: 181 passed, 1 skipped
Ruff: All checks passed!
Format: 48 Python files already formatted
Diff check: clean
```

Lucas understanding gate — completed 2026-08-12:

- Explained that step-scoped verification proves only one tool step and must
  return the multi-step run to `READY`, not claim `COMPLETED`.
- Explained that `turn_index` must be reconstructed from persisted Events by
  the Reducer rather than stored in volatile Agent memory.
- Explained why legacy unscoped Verification Events retain their historical
  completion meaning until an explicit versioned migration is performed.

## Current milestone — R4g durable model-step budget

Engineering implemented, verified, and published; Lucas understanding gate
completed 2026-08-13:

- Added an optional positive `max_steps` creation contract. New bounded runs
  persist it in `run.created`; legacy creation Events without the field keep
  their historical unbounded replay meaning.
- Reused replayed `turn_index` as the consumed model-action count rather than
  introducing a second counter that could diverge.
- Added `run.step_budget_exhausted` and strict Reducer checks that its
  `completed_steps` and `max_steps` match replayed State and prove exhaustion.
- Added typed `StepBudgetExhaustedError` after the terminal failure Event is
  durably appended.
- Enforced the budget before requesting an Action, so an exhausted run invokes
  neither the Model Adapter nor another Tool.
- Proved the limit survives SQLite close/reopen and Runtime reconstruction:
  one allowed turn remains consumed, the second request fails closed, and only
  one `tool.requested` Event exists.

Evidence:

```text
Implementation: 9137bd1
R4g targeted tests: 53 passed
Full suite: 189 passed, 1 skipped
Ruff: All checks passed!
Format: 48 Python files already formatted
```

Lucas explained that the budget is a per-Run contract rather than a Runtime
constructor default. He also identified the need to preserve old protocol
records; the gate clarified that the pre-Adapter check prevents model cost,
latency, and side effects, while Reducer remains the final invariant owner.
New writers use the new schema, but old logs remain readable until an explicit
versioned migration retires them.

## Current milestone — R4h trusted completion contract

Engineering implemented and verified locally; publication and Lucas
understanding gate remain:

- Added application-owned `CompletionExpectation` and deterministic
  `CompletionVerifier` evidence bound to the exact final-answer SHA-256.
- Required a trusted prior verification observation by default; a bare model
  statement cannot prove task completion.
- Added `completion.accepted` and `completion.rejected`. Only accepted evidence
  enters `COMPLETED`; rejection consumes one model Action and returns to
  `READY` for a bounded correction.
- Bound completion evidence to the current replayed `run_id`, `turn_index`, and
  Runtime-generated `step_id`, rejecting stale contexts and mismatched answers.
- Enforced model-step budget both before Adapter invocation and inside Reducer,
  so direct Event injection cannot bypass the durable limit.
- Proved tool verification, SQLite close/reopen, Adapter reconstruction, and a
  second-turn completion produce exactly one durable accepted Event.

Evidence:

```text
R4h targeted tests: 46 passed
Full suite: 197 passed, 1 skipped
Ruff: All checks passed!
Format: 48 Python files already formatted
Diff check: clean
```

Next executable step:

1. Run the final full suite after Reducer budget hardening.
2. Publish R4h and synchronize the control repository.
3. Teach the completion contract and complete the R4h understanding gate.
4. Build the bounded Agent Loop over the now-trusted tool and completion turns.
