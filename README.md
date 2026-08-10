# Agent Runtime Lab

Reliability infrastructure for agentic LLM execution.

Agent Runtime Lab is the runtime layer of **Reliable Agentic LLM Systems —
Runtime · Evaluation · Inference**. It studies how an agent execution can be
authorized, persisted, recovered, replayed, and validated without silently
duplicating tool effects or bypassing user gates.

## Current status

The active development branch is `durable-tool-execution-recovery`. The
deterministic R1 core, durable R2 tool-effect path, R3.1 ownership step
classification, the R3.2 authorization contract, and the R3.3a durable gate
control path are implemented and published on that branch. R3.3b USER_GATE
attempt persistence is locally implemented and verified; its final evaluator
remains the explicit developer-owned learning boundary.

Published R3.3a evidence is `f880cdd` / `6955650`. Local R3.3b scaffold evidence
is `c2989b3`:

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
- 100 tests pass, Ruff passes, and 35 files pass the format check.

R3.3a has been pushed to the development branch; the R3.3b scaffold is not yet
published and is not claimed as complete until Lucas implements and explains
`evaluate_gate(...)`. Nothing is claimed as merged to `main`. The runtime is
still a validation spike, not a production sandbox or a complete agent loop.

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

Gate references bind an approval to the exact proposal, but do not authenticate
the human actor. Production identity and session authentication belong at the
trusted UI/API boundary. Durable USER_GATE attempt infrastructure is present,
while the final domain-specific `evaluate_gate(...)` logic remains intentionally
developer-owned.

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
