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

Pending before marking R3.2 complete:

- Lucas explains classification versus authorization, registry versus
  workspace rejection, and escalation versus denial.
- Then connect the verified authorization contract to the reducer and durable
  executor.

Still deferred:

- Real model integration.
- Multi-agent orchestration.
- Long-term memory or RAG.
- Cloud sandboxing and production worker orchestration.
- Starting the standalone CodeOwnership Skill or cross-project S6 work before
  Runtime authorization and gate recovery are stable.
