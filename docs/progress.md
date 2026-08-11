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
- Added `python -m agent_runtime_lab.r4a_demo`, which emits only relative paths,
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

Next executable step:

1. Complete the short R4a understanding gate using the real implementation.
2. Do not add Shell, subprocess execution, or cloud sandboxing in R4a.

Still deferred:

- Real model integration.
- Multi-agent orchestration.
- Long-term memory or RAG.
- Cloud sandboxing and production worker orchestration.
- Starting the standalone CodeOwnership Skill or cross-project S6 work before
  Runtime authorization and gate recovery are stable.
