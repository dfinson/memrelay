# Story 2.6: Run the Shipped Daemon and MCP Product Stratum

Status: review

## Story

As a product evaluator,
I want trials to use the shipped daemon and MCP path in isolated state,
So that measured outcomes include the actual product lifecycle and transport overhead.

## Acceptance Criteria

1. **Given** a product-stratum attempt  
   **When** treatment is provisioned  
   **Then** an isolated `MEMRELAY_HOME` and pinned configuration start a real daemon, verify `health`, and expose only shipped `memory_recall`, `memory_detail`, and `memory_note` through isolated MCP  
   **And** evaluator code never opens the daemon-owned graph directly.
2. **Given** framework inference is configured  
   **When** preflight runs  
   **Then** it requires direct OpenAI `byo-key`, exact model `gpt-4.1-mini-2025-04-14`, pinned base URL/client, daemon-only key, and rejects `borrow-host`, LiteLLM, local, or any unexpected fallback  
   **And** local embeddings use digest-pinned `BAAI/bge-small-en-v1.5` with no embedding API calls.
3. **Given** product execution terminates  
   **When** state is collected  
   **Then** daemon, MCP, graph, spool, socket/loopback, health, tool success/error/zero-result, observation-path, process, and cleanup evidence is preserved  
   **And** controls maintain equivalent tool visibility, schemas, permissions, budgets, and accounting.
4. **Given** product lifecycle or telemetry before Stories 4.2 and 4.3  
   **When** it is emitted  
   **Then** it flows through the Story 1.1 domain-owned `LedgerPort` and `TelemetryPort` using deterministic fakes for unpaid conformance  
   **And** durable study execution and inclusion remain blocked until the concrete sole-writer SQLite ledger and Collector adapters pass conformance.

## Dependencies and Prerequisites

- Authoritative direct dependencies are Stories 2.4 and 2.5; Stories 2.2-2.3 provide their transitive isolated roots/processes. Story 1.3 provides immutable stratum identities.
- Product framework credentials are configured before launch and delivered only to the daemon process per Story 2.3.
- Use fake product/agent ports for ordinary CI. Live OpenAI/Copilot execution is explicit, bounded, and blocked from study inclusion until Epic 4 durable evidence passes.

## Tasks / Subtasks

- [x] Implement shipped daemon lifecycle adapter (AC: 1, 3)
  - [x] Launch the shipped foreground `memrelay _serve` entry point (the process seam used by `memrelay start`) so the evaluator owns and can supervise the attempt-local process tree; there is no `memrelay-daemon` console script.
  - [x] Bind the attempt-owned home, endpoint, spool, and pinned configuration path without inheriting the host environment.
  - [x] Verify `LiveHealthBackend` readiness and require the daemon-owned canonical observation artifact before state evidence is persisted.
- [x] Implement shipped MCP client/tool contract (AC: 1, 3)
  - [x] Supply the live agent only the shipped `memrelay mcp` command and exact `memory_recall`, `memory_detail`, `memory_note` contract; reject extra tools.
  - [x] Prove the task-agent/MCP process has no normalized or case-variant OpenAI credential.
- [x] Enforce framework provider/model/embedding strategy (AC: 2)
  - [x] Require local `BAAI/bge-small-en-v1.5`, direct OpenAI `byo-key`, dated model, daemon-only key.
  - [x] Pause before launch on borrow-host, LiteLLM, local/unknown fallback, missing key, or alternate endpoint/provider.
- [x] Route product process startup through the Story 1.1 ledger claim and telemetry ports using deterministic fakes for unpaid conformance (AC: 4)
- [x] Create product-stratum identity chain and non-pooling guards at orchestration aggregation (Architecture: AD-03, AD-20)
- [x] Add shipped-surface, process crash/cleanup, credential, observation-evidence, and stratum tests (AC: 1-4)
- [ ] Require explicit live invocation plus frozen positive Copilot/OpenAI call, credit/token, USD, active-time, and wall-time caps; record planned/consumed quantities and stop before overage.

## Developer Context

This story evaluates the actual user-facing product path: shipped daemon plus shipped MCP tools. Do not instantiate `MemoryEngine` in the task process, write the graph directly, invent evaluation-only memory tools, or bypass ingestion. The daemon is the sole graph writer. Product/framework results form a distinct estimand from Story 2.7's direct-engine upper bound.

### Architecture Compliance

- Follow AD-03, AD-06, AD-07, AD-08, AD-09, AD-18, AD-20, AD-22, AD-23, and AD-24. AD-03/AD-20 govern stratum identity and product seams; AD-23 governs eligible data, not stratum separation.
- Product/framework stratum identity must exist at protocol, assignment, run, endpoint, usage, cost, analysis, report, and claim layers.
- Agent tool payloads remain treatment-neutral; task-agent sees memory only via shipped MCP.
- Inspect remains execution authority through Story 2.4. The product adapter is a treatment/process boundary and must return native evidence references rather than create a competing terminal status.
- Complete parity and preflight before task delivery or provider inference. Assignment resolution, memory provisioning, task delivery, inference, and any treatment access are recorded exposure events; ambiguity is exposed.
- Retry remains one conclusively pre-exposure infrastructure replacement; no post-exposure retry or best-of-N.
- Fake `ArtifactStorePort`, `LedgerPort`, and `TelemetryPort` evidence is unpaid-only. Paid/study provenance requires Story 4.1 CAS, Story 4.2 ledger, and Story 4.3 Collector conformance.

### Library and Version Requirements

- Preserve the complete root `pyproject.toml` and resolved product environment by hash; do not reconstruct a partial evaluator-side dependency list. Relevant current bounds include `traceforge-toolkit>=0.1,<0.1.2`, `litellm>=1.0,<1.92`, `graphiti-core>=0.29,<0.30`, `ladybug>=0.18,<0.18.1`, `fastembed>=0.3,<1`, and `mcp>=1.0,<2`.
- Local embedding model: `BAAI/bge-small-en-v1.5`.
- Framework LLM: direct OpenAI `byo-key`, `gpt-4.1-mini-2025-04-14`.
- Copilot SDK `1.0.8` and Inspect `0.3.252` remain task orchestration pins; do not add them to product inference.

### Expected File Paths

- **NEW:** `evaluation/src/memrelay_eval/adapters/memrelay/{product,controls}.py`
- **UPDATE:** `evaluation/src/memrelay_eval/orchestration/{control,attempt,stages}.py`
- **UPDATE:** `evaluation/src/memrelay_eval/domain/{entities,ids,policies}.py`
- **NEW:** `evaluation/tests/contract/memrelay/{test_daemon,test_mcp_tools,test_provider_strategy}.py`
- **NEW:** `evaluation/tests/integration/test_product_stratum_fake.py`
- **READ ONLY / preserve:** `src/memrelay/daemon/{lifecycle,runtime}.py`
- **READ ONLY / preserve:** `src/memrelay/mcp/{server,tools}.py`
- **READ ONLY / preserve:** `src/memrelay/daemon/session_discovery.py`, `src/memrelay/daemon/runtime.py`, `src/memrelay/internal_sessions.py`
- **READ ONLY / preserve:** `src/memrelay/config.py`, root `pyproject.toml`

### Existing Behavior to Preserve

- The daemon reached through `memrelay _serve` owns a single `MemoryEngine`, waits for graph readiness, and coordinates poller/ingesters and shutdown.
- MCP remains stateless over daemon/engine seams and exposes only `memory_recall`, `memory_detail`, `memory_note`.
- Current configuration validation, session discovery, Run Observe capture, Live Tail capture, and daemon health/cleanup semantics remain unchanged.
- Evaluator isolation must not mutate user/global product configuration or caches.

### Testing Requirements

- Contract against shipped command and exact three-tool schemas; reject any fourth/direct-engine tool.
- Single-writer probe proves task/MCP cannot mutate graph except via daemon.
- Strategy matrix rejects borrow-host/LiteLLM/local fallback/alternate model and checks daemon-only OpenAI key.
- Parallel attempts prove unique homes/graph/spool/socket/discovery/capture state.
- Identity/cost/report tests prove product and direct-engine rows cannot join or aggregate without explicit separate strata.
- CI fakes both providers. Live tests require explicit opt-in, a frozen finite call/credit/token/USD/time envelope, and circuit-breaker evidence.

### Anti-Patterns

- Do not reimplement memrelay in evaluator code or import the engine as a shortcut.
- Do not give OpenAI credentials to task-agent/MCP/Inspect/Copilot processes.
- Do not bypass discovery/Run Observe/Live Tail, loosen dependency bounds, or pool strata.
- Do not issue unbounded provider calls, retry after exposure, or use fake evidence for study inclusion.

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` — “Story 2.6”]
- [Source: `ARCHITECTURE-SPINE.md` — AD-03, AD-06-AD-09, AD-18, AD-20, AD-22-AD-24]
- [Source: `IMPLEMENTATION-DESIGN.md` — §§5, 7.1, 11.1, 15.3, 16-17, 24.1]
- [Source: `src/memrelay/config.py`; `src/memrelay/daemon/`; `src/memrelay/mcp/`; `src/memrelay/ingest/`]
- [Source: `test-design-qa.md` — product-stratum isolation/security scenarios]

## Dev Agent Record

### Agent Model Used

OpenAI coding agent

### Debug Log References

### Completion Notes List

- Added product-stratum controls for the shipped tool contract, framework preflight, and opaque product identity chain.
- Replaced the in-process daemon shortcut with evaluator-owned `memrelay _serve` process-tree supervision, pinned config delivery, `LiveHealthBackend` readiness, cleanup evidence, and canonical spool-artifact verification.
- Added the daemon-only OpenAI environment boundary and shipped `memrelay mcp` agent command; evaluator self-probes do not stand in for task-agent evidence.
- Added fake-only evaluator contract/integration coverage for strategy and URL rejection, normalized credential leaks, fourth-tool spoofing, crash cleanup, observation evidence, ledger claim/telemetry wiring, and product/engine aggregation denial.

### File List

- `evaluation/src/memrelay_eval/adapters/memrelay/__init__.py`
- `evaluation/src/memrelay_eval/adapters/memrelay/controls.py`
- `evaluation/src/memrelay_eval/adapters/memrelay/product.py`
- `evaluation/src/memrelay_eval/domain/entities.py`
- `evaluation/src/memrelay_eval/domain/ids.py`
- `evaluation/src/memrelay_eval/domain/policies.py`
- `evaluation/src/memrelay_eval/orchestration/attempt.py`
- `evaluation/src/memrelay_eval/orchestration/control.py`
- `evaluation/src/memrelay_eval/orchestration/stages.py`
- `evaluation/tests/contract/memrelay/test_daemon.py`
- `evaluation/tests/contract/memrelay/test_mcp_tools.py`
- `evaluation/tests/contract/memrelay/test_provider_strategy.py`
- `evaluation/tests/integration/test_product_stratum_fake.py`
- `_bmad-output/implementation-artifacts/2-6-run-the-shipped-daemon-and-mcp-product-stratum.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
