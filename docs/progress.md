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
Code commit: 4200871
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

Lucas understanding gate — pending:

- Explain why `ESCALATE` is a coarse Runtime action while PAIR/USER_GATE are
  precise ownership modes.
- Explain why approval must reference the exact proposal rather than use a
  boolean `approved=True`.
- Explain which guarantee proposal digest provides and which authentication
  guarantee it deliberately does not provide.

Still deferred:

- Real model integration.
- Multi-agent orchestration.
- Long-term memory or RAG.
- Cloud sandboxing and production worker orchestration.
- Starting the standalone CodeOwnership Skill or cross-project S6 work before
  Runtime authorization and gate recovery are stable.
