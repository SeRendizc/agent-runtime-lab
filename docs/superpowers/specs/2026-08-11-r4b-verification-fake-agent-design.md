# R4b Verification Evidence and Fake Agent Design

Date: 2026-08-11
Status: design approved; written specification awaiting final review

## Objective

Close the smallest real Runtime loop after R4a:

```text
static Fake Agent request
-> Authorization / optional Gate
-> real restricted file tool
-> durable Receipt
-> Runtime-owned verification
-> verification Event
-> COMPLETED or FAILED
```

The model or Fake Agent may request work, but cannot declare the run complete.
Only a verifier result persisted through the existing state machine can do so.

## Verification contract

Add `verification.py` with immutable contracts:

- `VerificationExpectation`: expected relative path and SHA-256 digest;
- `VerificationCheck`: one named check with pass/fail status and a sanitized
  explanation;
- `VerificationResult`: overall pass/fail plus the complete ordered checks;
- `ReceiptVerifier`: verifies a successful `ToolReceipt` against the
  expectation.

The first verifier checks exactly:

1. Receipt outcome is `SUCCEEDED`;
2. Receipt output contains the exact expected relative path;
3. Receipt output contains the exact expected SHA-256 digest.

Missing or incorrectly typed fields fail verification. Expected values are
trusted Runtime inputs and must be non-empty strings. Verification reports do
not include file content or absolute paths.

## Runtime integration

Add `AuthorizedToolRuntime.record_verification(run_id, result)`. It appends one
of the already-defined events:

- `verification.succeeded` for a passing result;
- `verification.failed` for a failing result.

The payload contains only the ordered named checks and a concise summary. The
existing Reducer remains the authority that permits verification only from
`VERIFYING` and moves the run to `COMPLETED` or `FAILED`. A caller cannot use
this method to bypass earlier Authorization, Gate, or Tool states.

## Fake Agent

Add `fake_agent.py` with a deliberately static `FakeAgent` and
`FakeAgentRunResult`.

The Fake Agent owns one prebuilt immutable `ToolRequest`. `run(expectation)`:

1. submits that exact request to `AuthorizedToolRuntime`;
2. requires a real Receipt, so a denied or waiting request is not treated as
   completion;
3. invokes `ReceiptVerifier`;
4. records the verification result through the Runtime;
5. returns the tool result, verification result, and final replayed state.

R4b demonstrates the AUTO `read_file` path only. PAIR and USER_GATE effects are
already covered by R4a and are not hidden inside an automatic Fake Agent loop.

## Tests and evidence

Test-first coverage must prove:

- correct path and digest pass;
- failed Receipt, missing evidence, path mismatch, and digest mismatch fail;
- passing verification appends `verification.succeeded` and ends `COMPLETED`;
- failing verification appends `verification.failed` and ends `FAILED`;
- a real temporary UTF-8 file flows through Fake Agent, Authorization, real
  `read_file`, durable Receipt, verification, Event replay, and completion;
- Fake Agent cannot treat `DENIED` or `AWAITING_GATE` as success;
- evidence contains no file content or absolute workspace path.

Completion requires focused and full pytest plus Ruff, followed by commit and
push on `durable-tool-execution-recovery` and the normal S3 roadmap sync.

## Explicitly deferred

- real model providers or OpenAI-compatible APIs;
- multi-step planning or an unbounded agent loop;
- Shell, subprocess execution, `run_tests`, network, or cloud sandboxing;
- model-authored verification expectations;
- multi-agent orchestration, memory, RAG, token accounting, and cost metrics;
- generalized coding-task diff or test-suite verification.
