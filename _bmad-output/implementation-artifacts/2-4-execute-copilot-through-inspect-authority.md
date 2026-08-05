# Story 2.4: Execute Copilot Through Inspect Authority

Status: ready-for-dev

## Story

As a trial operator,
I want Inspect to orchestrate a direct official Copilot SDK custom agent,
So that execution limits and truth remain native without an alternate inference route.

## Acceptance Criteria

1. **Given** Inspect AI `0.3.252`  
   **When** a task executes  
   **Then** the adapter uses the official `@agent` surface or only the pinned release's documented `agent_bridge()`  
   **And** the custom agent calls the official Copilot SDK directly with zero Inspect model-provider calls and no OpenAI-compatible Copilot endpoint.
2. **Given** a fresh task-agent session  
   **When** Inspect schedules, cancels, times out, or terminates it  
   **Then** the exact task metadata, limits, model controls, native terminal state, event references, patch references, usage, cancellation, and typed failure return to Inspect  
   **And** partial evidence survives every terminal path.
3. **Given** Inspect `.eval`, native JSON export, and the SDK terminal record  
   **When** their terminal statuses are compared  
   **Then** Inspect is execution authority and the SDK record is mandatory corroboration  
   **And** disagreement blocks reconciliation rather than selecting a favorable source.
4. **Given** execution lifecycle or telemetry before Stories 4.2 and 4.3  
   **When** it is emitted  
   **Then** it flows through the Story 1.1 domain-owned `LedgerPort` and `TelemetryPort` using deterministic fakes for unpaid conformance  
   **And** durable study execution and inclusion remain blocked until the concrete sole-writer SQLite ledger and Collector adapters pass conformance.

## Dependencies and Prerequisites

- Stories 1.3, 1.6, 1.7, 2.1, and 2.3 provide compiled domain tasks, concealed assignment, exposure/retry policy, exact runtime/model locks, and isolated processes.
- The adapter consumes domain records and terminates all Inspect/Copilot SDK types at adapter boundaries.
- Implement fake runtime/Inspect adapters first. Real bounded conformance is explicit and cannot run in ordinary CI.

## Tasks / Subtasks

- [ ] Implement the pinned official Inspect custom-agent adapter (AC: 1)
  - [ ] Verify the exact `0.3.252` API and use `@agent`; use documented `agent_bridge()` only if that release requires it.
  - [ ] Add a hard audit that no Inspect model/provider object or request is created.
- [ ] Implement direct official Copilot SDK session translation (AC: 1, 2)
  - [ ] Pass exact locked model ID/capabilities/reasoning/context, tools, permissions, limits, and cancellation.
  - [ ] Return domain-owned terminal, event, patch, usage, and failure references; preserve partial evidence.
- [ ] Preserve and compare native authorities (AC: 2, 3)
  - [ ] Retain `.eval`, Inspect native JSON, and SDK terminal/event artifacts independently.
  - [ ] Treat Inspect as execution authority; require SDK corroboration; block every disagreement.
- [ ] Route lifecycle/telemetry via fake domain ports and enforce study-ineligibility (AC: 4)
- [ ] Gate every live adapter-conformance invocation behind explicit operator action and a frozen positive integer session/call, Copilot-credit, token, active-time, and wall-time envelope; refuse absent caps and stop before overage.
- [ ] Add cancellation, timeout, crash, hidden-retry, authority-conflict, and no-provider tests (AC: 1-4)

## Developer Context

Inspect owns scheduling, limits, `.eval`, JSON export, and execution truth. It does not supply task inference. The custom agent directly invokes `github-copilot-sdk` under current-subscription auth. Never use an Inspect model provider, generic model bridge, SDK BYOK/custom provider, or unofficial OpenAI-compatible Copilot route. The task-agent path is local and host-native.

### Architecture Compliance

- Follow AD-04, AD-08, AD-09, AD-15, AD-18, AD-19.
- One isolated SDK session per attempt; no hidden retry. Internal SDK/Inspect retry counts are bounded evidence.
- Preserve native artifacts by reference; do not copy event bodies into the thin ledger.
- Exposure begins conservatively at task delivery/inference/treatment access; ambiguous means exposed.
- Fake `LedgerPort`, `TelemetryPort`, and `ArtifactStorePort` support unpaid conformance only; durable execution waits for Epic 4.

### Library and Version Requirements

- Python 3.13; `inspect-ai==0.3.252`; `github-copilot-sdk==1.0.8` and frozen wheel/runtime digests from Story 2.1.
- Use official SDK/runtime only. No OpenAI SDK/provider for task inference.
- Do not loosen the evaluator lock or product dependency bounds.

### Expected File Paths

- **NEW:** `evaluation/src/memrelay_eval/adapters/inspect/{agent,task,export}.py`
- **NEW/UPDATE:** `evaluation/src/memrelay_eval/adapters/copilot/{client,session,events}.py`
- **NEW/UPDATE:** `evaluation/src/memrelay_eval/orchestration/{control,attempt}.py`
- **UPDATE:** `evaluation/src/memrelay_eval/domain/{entities,ports,errors}.py`
- **NEW:** `evaluation/tests/contract/inspect/`
- **NEW:** `evaluation/tests/integration/test_inspect_copilot_fake.py`
- **NEW:** `evaluation/tests/fault/test_execution_terminal_paths.py`
- **READ ONLY:** product source; no product inference adapter changes

### Existing Behavior to Preserve

- Exact Story 2.1 runtime/model locks and Story 2.3 process credential boundary.
- Story 1 run lifecycle versus attempt-terminal separation, concealed assignment, partial evidence, and single pre-exposure retry.
- Inspect `.eval`, JSON, and SDK native records remain independent immutable authorities.

### Testing Requirements

- Adapter contract proves exact task/limits/model/tool/permission translation and domain-owned outputs.
- Instrumented fake proves zero Inspect provider calls and rejects custom SDK provider/BYOK/OpenAI endpoint.
- Every schedule/cancel/timeout/crash/terminal path retains partial references and typed failure.
- Pairwise terminal conflict tests all block; no favorable source selection.
- CI is fully fake/unpaid. Live conformance records planned and consumed sessions/calls/credits/tokens/time, has fixed positive caps supplied by the frozen conformance protocol, and is never invoked by ordinary test discovery.

### Anti-Patterns

- Do not implement `CopilotSdkSolver` through Inspect's model API or invent a bridge class.
- Do not flatten Copilot behind an OpenAI-compatible endpoint.
- Do not hide SDK/Inspect retries, discard partial `.eval`, or reconcile by choosing success.
- Do not perform unbounded paid calls or retry post-exposure.

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` — “Story 2.4”]
- [Source: `ARCHITECTURE-SPINE.md` — AD-04, AD-15, AD-18, AD-19]
- [Source: `IMPLEMENTATION-DESIGN.md` — §§5, 10.3, 14, 20]
- [Source: technical research — Inspect custom Copilot SDK solver boundary]
- [Source: `test-design-qa.md` — `AUTH-*`, `TOOL-INSPECT-*`, `ITT-*`]

## Dev Agent Record

### Agent Model Used

TBD by implementation agent

### Debug Log References

### Completion Notes List

- Ultimate context engine analysis completed - comprehensive developer guide created

### File List
