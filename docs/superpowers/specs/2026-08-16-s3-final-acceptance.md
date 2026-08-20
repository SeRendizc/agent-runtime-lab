# S3 Agent Runtime Lab — Final Acceptance

Date: 2026-08-16  
Branch: `durable-tool-execution-recovery`

## Decision

S3 is accepted as complete on the development branch. This acceptance does not
claim a merge to `main`, production hardening, or the start of S4.

## Accepted capability chain

The accepted system provides one evidence-backed execution path:

1. Immutable Events and the normal Reducer own lifecycle state and replay.
2. Runtime-owned policy classifies work and authorizes the concrete request.
3. PAIR and USER_GATE persist exact proposals and stop execution until a valid
   resolution exists.
4. Tool Intent is persisted before execution and Receipt after execution;
   restart recovery does not silently duplicate an uncertain external effect.
5. Restricted file tools revalidate paths at execution time and record durable
   results.
6. Runtime-owned Verification, not model assertion, determines whether a tool
   step succeeded.
7. A persisted per-Run step budget bounds the Agent loop.
8. The durable Model Action boundary distinguishes a requested call with an
   unknown Provider outcome from an exact proposed Action.
9. The OpenAI-compatible Adapter turns exactly one complete Provider response
   into one untrusted Action and fails closed on ambiguous output.
10. Trace is derived from authoritative Events through the normal Reducer,
    redacted for Eval use, and bound by payload and whole-Trace digests.

## Verification evidence

Final local acceptance run:

```text
pytest: 240 passed, 1 skipped
ruff check: passed
ruff format --check: 58 files already formatted
worktree before acceptance documentation: clean
```

The single DeepSeek wire smoke was reported PASS by Lucas on 2026-08-16 from
an external environment with Provider egress. The current restricted container
did not perform that external call, so the two evidence sources are recorded
separately.

The R4k and R4l understanding gates are complete. In particular, the accepted
reasoning distinguishes Provider schema guidance from Runtime authorization,
authoritative Events from derived Trace, and payload/Trace integrity bindings
from authenticity or correctness guarantees.

## Residual boundaries

Acceptance intentionally does not claim:

- a production OS or cloud sandbox;
- safe arbitrary Shell execution;
- end-to-end Provider exactly-once behavior without Provider idempotency or
  invocation lookup;
- automatic retry of a Model request whose Provider outcome is unknown;
- persisted Provider token or monetary-cost accounting;
- actor authentication supplied by a Gate reference;
- multi-agent scheduling or distributed coordination;
- a merge of the development branch into `main`.

## S4 boundary

S4 CodeOwnership remains unstarted. Its location, package boundary, and first
integration path must be discussed independently. S3 acceptance supplies the
Runtime contracts that S4 may consume; it does not predetermine how S4 should
be organized.
