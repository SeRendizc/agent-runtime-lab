# R4b Verification Evidence and Fake Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Runtime-owned Receipt verification and a static Fake Agent that closes one real restricted-file run through a durable verification Event.

**Architecture:** A pure `ReceiptVerifier` converts a durable `ToolReceipt` and trusted expectation into immutable checks. `AuthorizedToolRuntime` persists the result through existing verification events, while `FakeAgent` only sequences an exact prebuilt request, verification, and final replayed state. The R4a demo moves out of the core package into `examples/`.

**Tech Stack:** Python 3.11+, dataclasses, enums, existing SQLite Runtime, pytest, Ruff.

---

### Task 1: Move the R4a demonstration out of the core package

**Files:**
- Create: `examples/__init__.py`
- Move: `src/agent_runtime_lab/r4a_demo.py` -> `examples/r4a_restricted_file_demo.py`
- Modify: `tests/test_r4a_demo.py`
- Modify: `README.md`
- Modify: `docs/progress.md`

- [ ] Write a failing import test that imports `run_demo` from
  `examples.r4a_restricted_file_demo` and no longer imports the core module.
- [ ] Run `tests/test_r4a_demo.py`; expect collection failure because the
  example module does not exist.
- [ ] Move the implementation without changing its behavior, update the module
  command to `.venv\Scripts\python.exe examples\r4a_restricted_file_demo.py`,
  and delete the core demo module.
- [ ] Run `tests/test_r4a_demo.py`; expect the sanitized evidence test to pass.

### Task 2: Add immutable Receipt verification

**Files:**
- Create: `src/agent_runtime_lab/verification.py`
- Create: `tests/test_verification.py`

- [ ] Write failing tests for a successful Receipt, failed Receipt, missing
  output fields, path mismatch, and digest mismatch.
- [ ] Run `tests/test_verification.py`; expect collection failure because the
  verification module does not exist.
- [ ] Implement:

```python
class VerificationOutcome(StrEnum):
    PASSED = "passed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class VerificationExpectation:
    path: str
    sha256: str


@dataclass(frozen=True, slots=True)
class VerificationCheck:
    name: str
    passed: bool
    message: str


@dataclass(frozen=True, slots=True)
class VerificationResult:
    outcome: VerificationOutcome
    checks: tuple[VerificationCheck, ...]
    summary: str
```

`ReceiptVerifier.verify(receipt, expectation)` must always return the ordered
checks `receipt_succeeded`, `path_matches`, and `sha256_matches`. Messages must
not contain the actual content, expected path, digest, or host path.

- [ ] Run `tests/test_verification.py`; expect all focused tests to pass.

### Task 3: Persist verification through the Runtime

**Files:**
- Modify: `src/agent_runtime_lab/authorized_tool_runtime.py`
- Create: `tests/test_runtime_verification.py`

- [ ] Write failing tests that start from `VERIFYING`, call
  `record_verification`, and assert `COMPLETED` plus
  `verification.succeeded`, or `FAILED` plus `verification.failed`.
- [ ] Run `tests/test_runtime_verification.py`; expect
  `AuthorizedToolRuntime` to lack `record_verification`.
- [ ] Implement `record_verification(run_id, result) -> RunState`. Persist only
  `summary` and ordered `{name, passed, message}` checks. Include `reason` only
  for the failed event because the existing Reducer requires it.
- [ ] Run the focused Runtime verification tests; expect both paths to pass.

### Task 4: Close the real loop with a static Fake Agent

**Files:**
- Create: `src/agent_runtime_lab/fake_agent.py`
- Create: `tests/test_fake_agent.py`

- [ ] Write a failing real-file E2E test whose prebuilt `read_file` request
  reads a temporary UTF-8 file and whose correct expectation ends in
  `COMPLETED` with `verification.succeeded`.
- [ ] Add a wrong-digest test that ends in `FAILED`, plus denied and gated
  request tests that raise `InvalidTransitionError` rather than claiming
  completion.
- [ ] Run `tests/test_fake_agent.py`; expect collection failure because the
  Fake Agent module does not exist.
- [ ] Implement immutable `FakeAgentRunResult` and `FakeAgent`. `run` must call
  `runtime.submit`, require `EXECUTED`, a Receipt, and `VERIFYING`, then verify,
  record the result, and return the final state.
- [ ] Run `tests/test_fake_agent.py`; expect all real E2E paths to pass and
  evidence JSON to exclude file content and the absolute workspace path.

### Task 5: Verify, document, publish, and teach

**Files:**
- Modify: `README.md`
- Modify: `docs/progress.md`
- Modify the four existing S3 roadmap files in `ai-dev-platform` after Runtime publication.

- [ ] Run focused and full pytest.
- [ ] Run Ruff check and format check.
- [ ] Update exact test counts, commands, commits, scope, and the distinction
  between model request, Runtime verification, and terminal state.
- [ ] Commit and push `durable-tool-execution-recovery`.
- [ ] Sync the four S3 roadmap files, run the existing 22 WSL/Linux tests,
  commit, and push `main`.
- [ ] Teach from the real files: explain why the Fake Agent cannot self-declare
  success, how Receipt evidence becomes a verification Event, and what R4b
  deliberately does not verify yet.
