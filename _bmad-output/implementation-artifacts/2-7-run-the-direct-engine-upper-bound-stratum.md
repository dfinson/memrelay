# Story 2.7: Run the Direct-Engine Upper-Bound Stratum

Status: ready-for-dev

## Story

As a product researcher,
I want a separately governed direct-engine treatment,
So that I can estimate an upper bound without presenting it as shipped-product efficacy.

## Acceptance Criteria

1. **Given** an engine-stratum attempt  
   **When** it is provisioned  
   **Then** `await MemoryEngine.from_config(...)` receives its own configuration and graph and only public async `note`, `search`, `detail`, `health`, and `close` methods are used  
   **And** it never opens a live or daemon-owned product graph.
2. **Given** the engine adapter receives external results  
   **When** they cross the adapter boundary  
   **Then** plain dictionaries are converted into domain-owned records  
   **And** engine construction, health, note, search, detail, close, graph, and rendering-contract evidence is retained.
3. **Given** product and engine results  
   **When** protocol, assignment, endpoint, cost, analysis, report, or claim identity is created  
   **Then** each stratum has a separate identity and aggregation without an explicit stratified operation fails  
   **And** every engine claim is labeled `engine upper bound`, never product efficacy.

## Dependencies and Prerequisites

- Authoritative direct dependencies are Stories 2.4 and 2.5. Story 2.6 supplies adjacent product/framework behavior and is not replaced by this upper-bound stratum.
- Story 2.2 provides isolated graph/home state; Story 2.3 keeps OpenAI credentials out of task-agent processes.
- The engine is in-process only inside an isolated direct-engine framework worker, never inside the Inspect control or Copilot task-agent process; that framework worker alone receives the framework credential.

## Tasks / Subtasks

- [ ] Implement direct-engine process adapter against public async API only (AC: 1)
  - [ ] Construct with `MemoryEngine.from_config`; expose only `note`, `search`, `detail`, `health`, `close`.
  - [ ] Own a unique graph and guarantee close/cleanup on every terminal path.
  - [ ] Use the same frozen framework embedding/LLM configuration as the product stratum.
- [ ] Convert external values into domain records and preserve complete engine/rendering evidence (AC: 2)
- [ ] Define immutable direct-engine protocol/run/endpoint identities (AC: 3)
  - [ ] Tag the stratum at first authority record and carry through usage/cost/analysis/report/claim artifacts.
  - [ ] Reject joins, exports, or estimators that pool product and direct-engine strata.
- [ ] Preserve upper-bound claim language (AC: 3)
  - [ ] Mark outputs `mechanism_upper_bound`, never shipped-product efficacy.
- [ ] Add API-surface, boundary-conversion, isolation, cleanup, credential, and non-pooling tests (AC: 1-3)

## Developer Context

This is a distinct estimand: mechanism potential through the public engine API. It deliberately omits daemon, shipped MCP, and capture/discovery overhead. That omission is why it must never support a product-efficacy claim. It still uses the same configured models and an isolated graph.

### Architecture Compliance

- Follow AD-03, AD-07, AD-08, AD-09, AD-18, AD-20, AD-22, and AD-24 and Implementation Design §§7.2 and 11.2. Section 18 defines cost ledgers, not the engine seam.
- Import no internal Graphiti/store implementation and write no graph outside `MemoryEngine`.
- Run it in-process within its isolated framework worker and give only that framework credential domain the OpenAI key; task agent and Inspect control remain Copilot-only and credential-free respectively.
- Inspect remains execution authority through Story 2.4; the engine adapter returns treatment evidence and never invents a second execution terminal status. Any engine access is treatment exposure and ambiguity is exposed.
- Identity separation covers protocol, assignment, runtime, endpoint, cost, analysis, report, and claim.
- Fake `ArtifactStorePort`, `LedgerPort`, and `TelemetryPort` implementations remain unpaid-only; durable/study provenance requires Story 4.1 CAS, Story 4.2 ledger, and Story 4.3 Collector conformance.

### Library and Version Requirements

- Preserve the complete root `pyproject.toml` and resolved product environment by hash rather than reconstructing a partial lock; current relevant bounds include `traceforge-toolkit>=0.1,<0.1.2`, `litellm>=1.0,<1.92`, `graphiti-core>=0.29,<0.30`, `ladybug>=0.18,<0.18.1`, `fastembed>=0.3,<1`, and `mcp>=1.0,<2`.
- Same framework configuration as Story 2.6: local `BAAI/bge-small-en-v1.5`, direct OpenAI `byo-key`, `gpt-4.1-mini-2025-04-14`.
- Python 3.13; no second engine/client library.

### Expected File Paths

- **NEW:** `evaluation/src/memrelay_eval/adapters/memrelay/engine.py`
- **UPDATE:** `evaluation/src/memrelay_eval/orchestration/{control,attempt,stages}.py`
- **UPDATE:** `evaluation/src/memrelay_eval/domain/{entities,ids,policies}.py`
- **NEW:** `evaluation/tests/contract/memrelay/test_engine_api.py`
- **NEW:** `evaluation/tests/integration/test_engine_stratum_fake.py`
- **NEW:** `evaluation/tests/unit/test_stratum_non_pooling.py`
- **READ ONLY / preserve:** `src/memrelay/engine/graphiti.py`, `src/memrelay/config.py`

### Existing Behavior to Preserve

- `MemoryEngine.from_config`, `note`, `search`, `detail`, `health`, and `close` remain the only evaluator-facing public engine seams.
- Existing graph readiness, serialization, note/search/detail semantics, health, and close behavior are not modified.
- Product daemon/MCP and its records remain untouched and separately reportable.

### Testing Requirements

- API spy fails on any private/internal method, direct store/Graphiti access, daemon start, or MCP call.
- Unique graph/process tests and fault injection verify `close`/cleanup and partial evidence.
- Configuration equality test proves the two strata use the same embedding/LLM setup while identities differ.
- Schema/query/analysis tests prove cross-stratum pooling is impossible and reports use upper-bound language.
- Live calls are explicit and require a frozen positive OpenAI call/token/USD and wall-time envelope with planned/consumed evidence; CI uses fakes only and the worker stops before any cap is exceeded.

### Anti-Patterns

- Do not call direct engine from the task-agent process or expose it as an extra tool in product trials.
- Do not import internal graph/store objects, start the daemon/MCP, or share a graph.
- Do not label direct-engine results as product efficacy or pool any costs/effects.
- Do not retry post-exposure or run unbounded paid calls.

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` — “Story 2.7”]
- [Source: `ARCHITECTURE-SPINE.md` — AD-03, AD-07-AD-09, AD-18, AD-20, AD-22, AD-24]
- [Source: `IMPLEMENTATION-DESIGN.md` — §§5, 7.2, 11.2, 18]
- [Source: `src/memrelay/engine/graphiti.py` — public `MemoryEngine` async API]
- [Source: `test-design-qa.md` — stratum separation and claim-boundary scenarios]

## Dev Agent Record

### Agent Model Used

TBD by implementation agent

### Debug Log References

### Completion Notes List

- Ultimate context engine analysis completed - comprehensive developer guide created

### File List
