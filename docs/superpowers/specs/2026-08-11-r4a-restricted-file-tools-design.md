# R4a Restricted File Tools Design

Date: 2026-08-11
Status: approach approved; written specification awaiting user review

## 1. Objective

R4a replaces the test-only `RecordingToolRunner` path with one real but tightly
bounded file-tool path. It proves that the existing Registry, Authorization,
Gate, durable Intent/Receipt, recovery, and Event Replay contracts still hold
when a tool creates an actual filesystem effect.

R4a is a validation spike, not a production sandbox. It deliberately does not
add a shell, subprocess execution, network access, model integration, multi-agent
orchestration, long-term memory, RAG, or cloud isolation.

## 2. Tool set and semantics

The first real tool set contains exactly three UTF-8 file tools:

| Tool | Exact arguments | Result | Retry contract |
| --- | --- | --- | --- |
| `read_file` | `path: str` | relative path, UTF-8 content, byte count, SHA-256 | safe retry |
| `write_file` | `path: str`, `content: str` | relative path, byte count, SHA-256, replacement flag | unknown result fails closed |
| `delete_file` | `path: str` | relative path and deleted flag | unknown result fails closed |

Unknown or extra arguments are rejected. Booleans, numbers, lists, and null are
not coerced into strings. `write_file` replaces one complete file; append,
patch, directory creation, recursive deletion, globbing, and alternate encodings
are outside R4a.

`read_file` is retry-safe because it creates no external side effect. A repeated
read may observe newer content, so the receipt records the bytes and digest that
were actually observed. `write_file` and `delete_file` remain conservatively
non-idempotent: after an Intent exists without a Receipt, Runtime cannot know
whether another actor changed or recreated the target, so automatic retry is
forbidden.

## 3. Trusted composition

A new `restricted_file_tools.py` module owns both:

- the trusted `ToolDefinition` values used by `ToolRegistry`; and
- `RestrictedFileToolRunner`, which implements the existing `ToolRunner`
  protocol.

Keeping definitions and handlers in one module provides one canonical
composition path and reduces accidental metadata drift. A factory returns the
canonical registry for these three tools. Tests assert that the registry
metadata and runner dispatch table contain the same names and retry policies.
The application composition root remains trusted: R4a does not claim it can
protect against trusted host code deliberately wiring a different Registry into
the Executor.

`AuthorizedToolRuntime` and `DurableToolExecutor` retain their current roles:

```text
ToolRequest
-> Registry and Authorization
-> optional durable Gate
-> Tool Intent
-> RestrictedFileToolRunner
-> Tool Receipt
-> TOOL_SUCCEEDED or TOOL_FAILED Event
```

The runner never writes Events, changes `RunState`, resolves Gates, or chooses
recovery policy.

## 4. Workspace enforcement

Authorization remains the first boundary. The runner receives the same trusted
`WorkspaceBoundary` and revalidates the original path immediately before I/O.
This second check is mandatory because an approved request can wait while the
filesystem changes.

Execution rejects:

- empty, absolute, drive-qualified, or escaping paths;
- any existing symbolic-link or Windows reparse-point component;
- directories and non-regular-file targets;
- a missing or non-directory parent for writes;
- files larger than the configured read/write byte limit;
- invalid UTF-8 input or output.

Only relative paths appear in successful output and expected failure messages.
Host absolute paths and full `OSError` strings are not persisted in Receipts.
Ordinary OS failures are converted to a typed, sanitized tool error containing
the operation, relative path, and stable errno/winerror code.

The runner checks every existing path component and then performs the operation,
but Python path checks and file I/O are not one kernel-level atomic operation.
A hostile process could still race a reparse-point change between check and use.
Therefore R4a must not claim TOCTOU-proof containment or production sandboxing.
All integration tests and the demo use dedicated temporary workspaces, never the
repository or arbitrary user files.

## 5. Write and delete behavior

`write_file` encodes the full content as UTF-8 before modifying the target. It
exclusively creates a Runtime-named staging file in the same directory, flushes
and fsyncs that file, then uses `os.replace` for an atomic same-filesystem
replacement. The staging path is never accepted from model input. Both the
encoded input and the bytes actually read by `read_file` are checked against
the configured limit.

On an ordinary exception before replacement, the runner removes its own staging
file. A process crash may leave a staging file or may complete replacement before
the Receipt is durable. Runtime must preserve the incomplete Intent and fail
closed; R4a does not silently clean or retry an ambiguous write.

This contract covers process crashes and Runtime redelivery, not machine power
loss. Directory-metadata durability and filesystem-specific power-loss behavior
remain outside R4a.

`delete_file` accepts only an existing regular file and calls `unlink` once.
Missing targets are failures rather than implicit success, because a missing
file does not prove that this logical effect performed the deletion. A crash
after unlink and before Receipt persistence remains an unknown non-idempotent
effect and is not retried.

## 6. Limits and evidence

Read and write byte limits are constructor-controlled trusted configuration with
a conservative default of 1 MiB. The Receipt for a successful read contains the
bounded content because later agent integration needs the tool result and crash
recovery needs the observed result. R4a does not claim secret redaction; callers
must use a dedicated non-secret workspace.

The runnable demonstration creates a temporary workspace and exercises:

1. an authorized read;
2. a PAIR-gated write;
3. a USER_GATE-gated delete;
4. persisted Receipts and replayed post-tool states;
5. rejection of an escaping path;
6. fail-closed recovery for an incomplete write/delete Intent.

The demo reports only temporary relative paths, Event types, Receipt outcomes,
digests, and recovery decisions. It does not print host paths or file contents.

## 7. Error handling

New errors are narrow and typed:

- `ToolArgumentValidationError`: malformed or unexpected arguments;
- `WorkspaceExecutionError`: execution-time path, file-kind, link, or reparse
  violation;
- `RestrictedToolExecutionError`: sanitized expected filesystem failure.

The runner raises these errors. `DurableToolExecutor` continues to convert a
tool exception into a failed `ToolReceipt`; it must not catch a simulated process
crash injected at durability checkpoints. Existing `UnsafeToolRetryError`
continues to represent an ambiguous non-idempotent recovery.

## 8. Test strategy

Implementation follows test-first red/green cycles. Required evidence includes:

- exact argument-schema tests for every tool;
- successful read, atomic write/replace, and delete in `tmp_path`;
- byte limit and UTF-8 failures;
- absolute, traversal, directory, symlink, and available Windows reparse-point
  rejection;
- sanitized failures that do not contain the absolute workspace path;
- canonical Registry metadata and unknown-tool rejection;
- end-to-end Authorization and Gate tests using real temporary file effects;
- read recovery as safe retry;
- write/delete Intent-without-Receipt recovery as fail-closed;
- a runnable temporary-workspace demo;
- full pytest, Ruff check, Ruff format check, and `compileall`.

Windows reparse tests are skipped only when the current account cannot create the
required link type; ordinary symbolic-link coverage remains required on every
platform where creation succeeds.

## 9. Delivery sequence

R4a is delivered in two reviewable batches:

1. **R4a.1 — restricted runner contract:** typed errors, canonical registry,
   argument validation, workspace recheck, three real handlers, and focused
   tests.
2. **R4a.2 — Runtime proof:** real-effect Authorization/Gate integration,
   crash-window recovery evidence, temporary-workspace demo, progress records,
   and the user understanding gate.

R4a is complete only when both batches are published, all verification evidence
is current, and the user can explain why Authorization, path validation, and a
real Sandbox are three different controls.

## 10. Explicitly deferred

- shell commands and `run_tests`;
- subprocesses, timeout, process-tree termination, and cancellation;
- Docker or cloud sandboxing;
- network and environment-variable access;
- directory creation, recursive operations, patches, and binary files;
- real repository mutation in demos or tests;
- automatic compensation or cleanup of ambiguous external effects;
- model, multi-agent, memory, and RAG integration.
