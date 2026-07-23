# Agent Runtime Lab

Reliability infrastructure for agentic LLM execution.

Agent Runtime Lab is the runtime layer of **Reliable Agentic LLM Systems —
Runtime · Evaluation · Inference**. It will study how an agent execution can be
authorized, persisted, recovered, replayed, and validated without silently
duplicating tool effects or bypassing user gates.

## Current status

This repository is an initial project scaffold. It does not yet claim a
production runtime, durable replay, crash recovery, or enforced sandboxing.

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
```

The first implementation milestone will define the event and replay contracts
before adding an agent loop or broad tool surface.

See [docs/progress.md](docs/progress.md) for the current evidence-backed status.
