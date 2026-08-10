# Agent Runtime Lab

Reliability infrastructure for agentic LLM execution.

Agent Runtime Lab is the runtime layer of **Reliable Agentic LLM Systems —
Runtime · Evaluation · Inference**. It studies how an agent execution can be
authorized, persisted, recovered, replayed, and validated without silently
duplicating tool effects or bypassing user gates.

## Current status

The active development branch is `durable-tool-execution-recovery`. The
deterministic R1 core, durable R2 tool-effect path, R3.1 ownership step
classification, and the R3.2 authorization contract are implemented and
verified on that branch.

Current evidence at `2add6c8`:

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
- 85 tests pass, Ruff passes, and 32 files pass the format check.

These changes have been pushed to the development branch but are not claimed
as merged to `main`. The runtime is still a validation spike, not a production
sandbox or a complete agent loop.

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
  -> allow / deny / escalate
```

`OwnershipDecision` is not an authorization token. R3.2 now rechecks the real
request. The next milestone is to complete the short learning gate, then connect
authorization to the reducer and durable execution path.

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
