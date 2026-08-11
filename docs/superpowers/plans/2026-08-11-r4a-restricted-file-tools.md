# R4a Restricted File Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a real, temporary-workspace-only `read_file` / `write_file` / `delete_file` execution path that preserves the existing authorization, Gate, durable Intent/Receipt, and fail-closed recovery contracts without adding Shell or process execution.

**Architecture:** A focused `restricted_file_tools.py` module owns the canonical trusted definitions and an in-process `RestrictedFileToolRunner`. The runner validates exact arguments, rechecks the original relative path and link/reparse components immediately before I/O, returns structured evidence, and sanitizes expected OS failures. Existing `AuthorizedToolRuntime` and `DurableToolExecutor` remain the orchestration and recovery authorities.

**Tech Stack:** Python 3.11+, standard library (`hashlib`, `os`, `pathlib`, `stat`, `tempfile`), pytest, SQLite-backed existing stores, Ruff.

---

## File map

- Create `src/agent_runtime_lab/restricted_file_tools.py`: canonical file-tool metadata, exact argument validation, execution-time workspace checks, and three real handlers.
- Modify `src/agent_runtime_lab/domain/errors.py`: typed argument, workspace-execution, and sanitized filesystem errors.
- Create `tests/test_restricted_file_tools.py`: focused runner, metadata, path, limit, encoding, and recovery contract tests.
- Create `tests/test_restricted_file_runtime.py`: real-effect Authorization, PAIR, USER_GATE, Event, and replay integration tests.
- Create `src/agent_runtime_lab/r4a_demo.py`: runnable temporary-workspace demonstration with non-sensitive summary output.
- Create `tests/test_r4a_demo.py`: demo execution and output-disclosure regression test.
- Modify `README.md` and `docs/progress.md`: evidence, boundary, commands, and honest non-sandbox status.

### Task 1: Canonical metadata and typed failures

**Files:**
- Modify: `src/agent_runtime_lab/domain/errors.py`
- Create: `src/agent_runtime_lab/restricted_file_tools.py`
- Test: `tests/test_restricted_file_tools.py`

- [ ] **Step 1: Write failing metadata and error tests**

```python
def test_restricted_registry_owns_exact_tool_metadata() -> None:
    registry = make_restricted_file_registry()
    assert registry.resolve("read_file").retry_is_idempotent is True
    assert registry.resolve("write_file").retry_is_idempotent is False
    assert registry.resolve("delete_file").retry_is_idempotent is False
    assert all(
        registry.resolve(name).path_argument_names == ("path",)
        for name in ("read_file", "write_file", "delete_file")
    )


def test_runner_and_registry_expose_the_same_tool_names(tmp_path: Path) -> None:
    runner = RestrictedFileToolRunner(WorkspaceBoundary(tmp_path))
    assert runner.supported_tool_names == frozenset(
        {"read_file", "write_file", "delete_file"}
    )
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `.venv\Scripts\python.exe -m pytest -p no:cacheprovider -q tests\test_restricted_file_tools.py`

Expected: collection fails because `restricted_file_tools` and the new error types do not exist.

- [ ] **Step 3: Add typed errors and canonical definitions**

```python
class ToolArgumentValidationError(RuntimeContractError):
    """A real tool received malformed or unexpected arguments."""


class WorkspaceExecutionError(RuntimeContractError):
    """A real tool target violates the execution-time workspace boundary."""


class RestrictedToolExecutionError(RuntimeError):
    """A restricted tool hit a sanitized expected filesystem failure."""
```

```python
RESTRICTED_FILE_TOOL_DEFINITIONS = (
    ToolDefinition("read_file", retry_is_idempotent=True, path_argument_names=("path",)),
    ToolDefinition("write_file", retry_is_idempotent=False, path_argument_names=("path",)),
    ToolDefinition("delete_file", retry_is_idempotent=False, path_argument_names=("path",)),
)


def make_restricted_file_registry() -> ToolRegistry:
    return ToolRegistry(RESTRICTED_FILE_TOOL_DEFINITIONS)
```

Add a minimal runner with `supported_tool_names` and an `invoke` dispatch implementation that raises `UnknownToolError` for names outside the canonical set.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the Task 1 focused tests. Expected: metadata tests pass.

- [ ] **Step 5: Commit**

```text
git add src/agent_runtime_lab/domain/errors.py src/agent_runtime_lab/restricted_file_tools.py tests/test_restricted_file_tools.py
git commit -m "feat: define restricted file tool catalog"
```

### Task 2: Exact arguments and execution-time workspace checks

**Files:**
- Modify: `src/agent_runtime_lab/restricted_file_tools.py`
- Test: `tests/test_restricted_file_tools.py`

- [ ] **Step 1: Write failing exact-argument and path tests**

Cover these concrete cases:

```python
@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("read_file", {}),
        ("read_file", {"path": "a.txt", "extra": True}),
        ("write_file", {"path": "a.txt"}),
        ("write_file", {"path": "a.txt", "content": 1}),
        ("delete_file", {"path": None}),
    ],
)
def test_tool_arguments_are_exact_and_strict(...):
    with pytest.raises(ToolArgumentValidationError):
        runner.invoke(tool_name=tool_name, arguments=arguments, idempotency_key="effect-1")
```

Add direct-runner tests rejecting absolute paths, drive paths, every `..` component, directories, missing write parents, symlink components, and Windows reparse points when creation is supported. Assertions must verify that no file outside `tmp_path` changes.

- [ ] **Step 2: Run focused tests and verify RED**

Expected: the initial dispatch implementation lacks validation and workspace checks.

- [ ] **Step 3: Implement strict argument helpers and path resolution**

Implement `_require_exact_arguments`, `_require_text`, and `_resolve_for_execution`. The resolver must:

```text
reject any original '..' component
call WorkspaceBoundary.normalize(original_path)
walk original existing components with os.lstat
reject stat.S_ISLNK(mode)
reject FILE_ATTRIBUTE_REPARSE_POINT when available
return only (normalized_relative_path, absolute_candidate)
```

The trusted workspace root itself is allowed to be host-configured; only components below it are checked. Do not include the absolute root in raised messages.

- [ ] **Step 4: Run focused tests and verify GREEN**

Expected: all Task 2 tests pass and no outside path changes.

- [ ] **Step 5: Commit**

```text
git add src/agent_runtime_lab/restricted_file_tools.py tests/test_restricted_file_tools.py
git commit -m "feat: enforce restricted file boundaries"
```

### Task 3: Real read, atomic write, and delete handlers

**Files:**
- Modify: `src/agent_runtime_lab/restricted_file_tools.py`
- Test: `tests/test_restricted_file_tools.py`

- [ ] **Step 1: Write failing handler tests**

Required assertions:

```python
assert read_result == {
    "path": "notes.txt",
    "content": "hello",
    "bytes": 5,
    "sha256": hashlib.sha256(b"hello").hexdigest(),
}
assert write_result["path"] == "notes.txt"
assert write_result["replaced"] is False
assert workspace.joinpath("notes.txt").read_text(encoding="utf-8") == "hello"
assert delete_result == {"path": "notes.txt", "deleted": True}
assert not workspace.joinpath("notes.txt").exists()
```

Also cover read/write byte limits, invalid UTF-8 reads, missing read/delete targets, directory targets, replacement reporting, no leftover `.agent-runtime-*` staging file after ordinary success/failure, and sanitized error strings that exclude `str(tmp_path)`.

- [ ] **Step 2: Run focused tests and verify RED**

Expected: handlers are missing.

- [ ] **Step 3: Implement handlers**

`read_file` reads at most `max_bytes + 1`, rejects excess data, decodes strict UTF-8, and hashes the observed bytes.

`write_file` encodes before modifying, enforces the byte limit, creates a same-directory file with `tempfile.mkstemp(prefix=".agent-runtime-", suffix=".tmp")`, flushes and fsyncs it, calls `os.replace`, and removes its staging file in `finally` when it still exists.

`delete_file` requires an existing regular file and calls `unlink` once. Expected `OSError` values are wrapped without persisting `str(exc)`:

```python
code = getattr(exc, "winerror", None) or exc.errno or "unknown"
raise RestrictedToolExecutionError(
    f"{operation} failed for {relative_path!r} (os_code={code})"
) from exc
```

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the complete `tests/test_restricted_file_tools.py` file.

- [ ] **Step 5: Commit**

```text
git add src/agent_runtime_lab/restricted_file_tools.py tests/test_restricted_file_tools.py
git commit -m "feat: execute restricted file tools"
```

### Task 4: Durable recovery contracts with the real runner

**Files:**
- Modify: `tests/test_restricted_file_tools.py`

- [ ] **Step 1: Write recovery tests before changing any production code**

Use `SQLiteToolEffectStore`, `DurableToolExecutor`, the canonical registry, and the real runner to prove:

- an incomplete `read_file` Intent is safely retried and produces a Receipt;
- incomplete `write_file` and `delete_file` Intents raise `UnsafeToolRetryError` without invoking the runner;
- a completed Receipt is returned without repeating a file effect.

- [ ] **Step 2: Run tests**

Expected: tests pass using existing `DurableToolExecutor` behavior. If a test fails, change production code only when the failure proves a real contract gap; do not weaken the test.

- [ ] **Step 3: Commit evidence tests**

```text
git add tests/test_restricted_file_tools.py
git commit -m "test: verify real file recovery contracts"
```

### Task 5: Authorization and Gate integration with real effects

**Files:**
- Create: `tests/test_restricted_file_runtime.py`

- [ ] **Step 1: Write end-to-end tests**

Create an `AuthorizedToolRuntime` using one canonical registry, one shared `WorkspaceBoundary`, the real runner, SQLite Event/Effect stores, and existing risk/policy rules. Prove:

1. `read_file` in AUTO mode reads a real temporary file and ends in `VERIFYING`.
2. `write_file` produces no effect while `AWAITING_GATE`, then writes only after the exact PAIR approval.
3. `delete_file` produces no effect for RETRY/BLOCK answers and deletes only after a valid USER_GATE answer bound to the exact path.
4. an escaping request is DENIED before Intent persistence and causes no effect.
5. restart after approval/evaluation replays the same persisted request rather than answer-derived arguments.

- [ ] **Step 2: Run integration tests and verify behavior**

Run: `.venv\Scripts\python.exe -m pytest -p no:cacheprovider -q tests\test_restricted_file_runtime.py`

Expected: tests pass without modifying core Runtime behavior. Any failure must be diagnosed against Event ordering and durable stores before editing the Runtime.

- [ ] **Step 3: Commit**

```text
git add tests/test_restricted_file_runtime.py
git commit -m "test: prove restricted file runtime integration"
```

### Task 6: Runnable temporary-workspace demo

**Files:**
- Create: `src/agent_runtime_lab/r4a_demo.py`
- Create: `tests/test_r4a_demo.py`

- [ ] **Step 1: Write a failing demo-output test**

Call `run_demo()` and assert that its returned summary includes ordered Event types, Receipt outcomes, relative paths, digests, denied escape evidence, and fail-closed recovery evidence. Assert that it contains neither the temporary absolute root nor any file content.

- [ ] **Step 2: Run test and verify RED**

Expected: `r4a_demo` does not exist.

- [ ] **Step 3: Implement `run_demo()` and module entry point**

The demo must use `TemporaryDirectory`, real SQLite stores, the canonical registry/runner, and actual Authorization/Gate paths. It may create only temporary non-sensitive files. `python -m agent_runtime_lab.r4a_demo` prints JSON with sorted keys and no host path/content.

- [ ] **Step 4: Run demo and its test**

```text
.venv\Scripts\python.exe -m pytest -p no:cacheprovider -q tests\test_r4a_demo.py
.venv\Scripts\python.exe -m agent_runtime_lab.r4a_demo
```

Expected: test passes; command exits 0 with sanitized JSON evidence.

- [ ] **Step 5: Commit**

```text
git add src/agent_runtime_lab/r4a_demo.py tests/test_r4a_demo.py
git commit -m "feat: demonstrate restricted file runtime"
```

### Task 7: Verification, documentation, and publication

**Files:**
- Modify: `README.md`
- Modify: `docs/progress.md`
- Modify after code publication: `D:/Reliable Agentic LLM Systems/ai-dev-platform/roadmap/01_MASTER_PROGRESS.md`
- Modify after code publication: `D:/Reliable Agentic LLM Systems/ai-dev-platform/roadmap/CURRENT_HANDOFF.md`
- Modify after code publication: `D:/Reliable Agentic LLM Systems/ai-dev-platform/roadmap/REPOSITORY_REGISTRY.md`
- Modify after code publication: `D:/Reliable Agentic LLM Systems/ai-dev-platform/roadmap/stages/S3_AGENT_RUNTIME_LAB.md`

- [ ] **Step 1: Run fresh verification**

```text
.venv\Scripts\python.exe -m pytest -p no:cacheprovider -q tests/test_restricted_file_tools.py tests/test_restricted_file_runtime.py tests/test_r4a_demo.py
.venv\Scripts\python.exe -m pytest -p no:cacheprovider -q
.venv\Scripts\ruff.exe check --no-cache src tests
.venv\Scripts\ruff.exe format --check --no-cache src tests
.venv\Scripts\python.exe -m compileall -q src tests
.venv\Scripts\python.exe -m agent_runtime_lab.r4a_demo
git diff --check
```

- [ ] **Step 2: Update Runtime evidence truthfully**

Record exact test counts and commits. State explicitly that R4a is an in-process restricted file runner with a residual path-check/use race, not a production sandbox. Mark R4a engineering complete only after both runner and Runtime/demo evidence pass; leave the user understanding gate separate.

- [ ] **Step 3: Commit and push Runtime evidence**

Use explicit file staging, push `durable-tool-execution-recovery`, and verify local HEAD equals `origin/durable-tool-execution-recovery`.

- [ ] **Step 4: Synchronize ai-dev-platform**

Update only S3, Registry, Master, and Handoff fields whose baseline, milestone, or next action changed. Run its 22 tests in WSL/Linux, commit roadmap-only changes, push `main`, and verify a clean aligned worktree.

- [ ] **Step 5: Final audit**

Re-read the approved design section by section and map every requirement to code, tests, demo output, or an explicit deferred boundary. Do not claim Shell, subprocess isolation, TOCTOU-proof containment, secret redaction, or production sandboxing.
