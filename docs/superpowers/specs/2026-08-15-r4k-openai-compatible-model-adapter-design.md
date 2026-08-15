# R4k OpenAI-compatible Model Adapter Design

Date: 2026-08-15

## Goal

Replace the static scripted provider at the outer boundary with one real HTTP
implementation while keeping the R4j-d durable Action protocol unchanged.

## Contract

`OpenAICompatibleModelAdapter` receives immutable `ModelInput`, a fixed task, and
provider-facing Tool schemas. It sends exactly one non-streaming Chat Completions
request with `n=1`, then converts exactly one complete choice into either:

- one `ToolCallAction`; or
- one `FinalAnswerAction`.

The Adapter never receives Event Store, Reducer, authorization, Tool execution,
Verification, or completion mutation capabilities. Provider-facing Tool schemas help
generation but do not authorize anything; the Runtime still validates the resulting
Action through its trusted Registry, Policy, Workspace, and durable Event contracts.

## Fail-closed parsing

The boundary rejects missing/multiple choices, multiple Tool calls, Tool plus final
content, non-function calls, malformed arguments, empty final content, and truncated or
otherwise mismatched finish reasons. It does not guess which partial output the model
intended.

## Credentials and transport

Credentials are loaded lazily from a configured environment variable and are never
stored in an Event or Adapter representation. The standard-library transport sends one
HTTP POST and maps network, HTTP, and JSON failures to sanitized `ModelProviderError`
messages without response bodies.

The injectable transport gives deterministic tests the exact same request construction
and response parser without spending provider budget. A live DeepSeek or local vLLM
smoke test is an acceptance check, not the basis of the correctness contract.

## Deliberate limits

- one Action per model invocation; no parallel Tool calls;
- no streaming;
- no automatic Provider retry after an unknown outcome;
- no Provider reasoning trace persistence yet;
- no claim that observations are secret-redacted for production egress.

These limits preserve the R4j-d unknown-outcome and durable Action guarantees. Trace and
demo closure belong to R4l.
