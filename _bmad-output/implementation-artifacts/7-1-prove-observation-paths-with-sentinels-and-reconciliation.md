# Story 7.1: Prove Observation Paths with Sentinels and Reconciliation

Status: ready-for-dev

## Story

As a release owner,
I want sentinel events traced through each configured observation path,
so that continuous-capture claims reflect current source behavior rather than stale documentation.

## Acceptance Criteria

1. **Given** current `SessionDiscoveryPoller`, `RunObserveCapture`, and `LiveTailCapture` implementations  
   **When** observation conformance runs for the configured poll, replay, and live-tail modes  
   **Then** unique non-secret sentinels traverse discovery, capture, spool/graph evidence, telemetry, manifest, and reconciliation boundaries  
   **And** path-specific delivery, ordering, duplicate, gap, restart, and terminal-flush results are retained.
2. **Given** a missing, duplicated, reordered, delayed, or unreconciled sentinel  
   **When** observation qualification closes  
   **Then** the affected path fails with a typed reason  
   **And** no continuous-capture completeness claim is emitted.
3. **Given** a configured observation mode  
   **When** its source implementation or semantic mapping changes  
   **Then** a new conformance hash and protocol version are required  
   **And** prior sentinel evidence remains bound to its original implementation.

## Tasks / Subtasks

- [ ] Define the observation-qualification domain contract (AC: 1-3)
  - [ ] Model configured path/mode, source and semantic-map hashes, expected sentinel sequence, terminal-flush expectation, protocol ID, qualification status, and stable typed failure reasons.
  - [ ] Define the observation-only estimand as the path-specific expected-sentinel delivery proportion plus ordering, pre/post-idempotency duplicate, gap, restart-recovery, frozen-deadline delay, and terminal-flush behavior over the frozen conformance window; freeze the eligible-event denominator and deadline before injection.
  - [ ] Explicitly prohibit interpreting this estimand as coding efficacy, retrieval quality, safety, economic value, causal treatment effect, or production-wide reliability.
- [ ] Build deterministic, non-secret sentinel injection and evidence (AC: 1)
  - [ ] Generate opaque high-entropy sentinel IDs containing no repository, user, prompt, code, credential, treatment, or private metadata.
  - [ ] Exercise the two real configured compositions independently: discovery polling plus `RunObserveCapture` for `ingest.intake_source="replay"`, and discovery polling plus `LiveTailCapture` (including its replay backstop) for `"file_watch"`; polling is a shared discovery layer, not a third `intake_source`.
  - [ ] Retain expected/observed records at discovery, capture, pre-deduplication input, idempotent spool, daemon ingestion, MCP-visible graph behavior, telemetry, artifact-manifest, terminal flush, and reconciliation boundaries without opening a daemon-owned graph.
- [ ] Reconcile each path independently and fail closed (AC: 1, 2)
  - [ ] Compare native product records, spool/CAS evidence, daemon/MCP observations, required telemetry classes, manifests, and ledger transitions; OTLP receipt or a health counter alone is never completeness proof.
  - [ ] Detect missing, duplicate, reordered, delayed, restart-gap, shutdown-loss, partial-success, and authority-conflict cases and emit one immutable per-path decision.
  - [ ] Forbid passing one configured composition with evidence from another; qualify `replay` and `file_watch` separately while retaining discovery-poller results within each composition.
- [ ] Bind qualification to implementation and protocol identity (AC: 3)
  - [ ] Canonically hash the relevant source, dependency/runtime lock, configuration, semantic map, sentinel contract, and reconciliation policy.
  - [ ] Require a new protocol/conformance identity on source, mapping, mode, or frozen-version drift; never relabel prior evidence.
- [ ] Add deterministic local conformance and fault coverage (AC: 1-3)
  - [ ] Extend existing poller/replay/live-tail tests with fake clocks, restart, duplicates, ordering faults, delayed delivery, final drain, and LRU teardown.
  - [ ] Add evaluator contract/fault tests using synthetic session files and local fake evidence infrastructure; CI performs no Copilot/OpenAI calls and assumes no private production data.

## Dependencies and Prerequisites

- Story 2.6: isolated shipped daemon/MCP product stratum and observation evidence.
- Story 4.3: versioned telemetry semantics and injected transport-fault evidence.
- Story 4.5: cross-authority fail-closed reconciliation and immutable inclusion decisions.
- Story 6.1: immutable stage/command manifests and protocol-version enforcement.
- **Authoritative direct graph dependencies:** Stories 2.6, 4.3, 4.5, and 6.1 only, exactly as declared in `epics.md`.
- Story 1.4 data eligibility is an inherited constraint, not an additional direct dependency: use synthetic or license-audited public fixtures only.
- This story qualifies observation behavior only after its predecessor contracts exist; it does not make paid or study execution eligible by itself.

## Dev Notes

### Developer Context and Current Product Behavior

Current source already implements continuous observation paths; do not follow stale TEA statements that observation is absent:

- `SessionDiscoveryPoller` discovers trace files whose mtime is within `session_freshness_s`, excludes registered internal extraction sessions, starts/stops one `SessionCapture` per session, applies an LRU cap, and reports cumulative/active counts. A transient discovery failure preserves current captures.
- `RunObserveCapture` periodically replays the growing trace with `final=False`; `stop()` cancels the loop and performs the authoritative `final=True` drain. Spool `idempotency_key` uniqueness owns deduplication.
- `LiveTailCapture` is a latency optimization composed with an unchanged replay backstop. Both write to the same spool; replay, not the tail offset, owns losslessness. Stop drains the tail/source and then the replay backstop.
- `default_poller_factory` selects `RunObserveCapture` for `intake_source="replay"` and `LiveTailCapture` for `file_watch`; both share `<home>/spool/spool.db`.
- Existing tests prove selected lifecycles and one fixture roundtrip, but they are bounded regression evidence. They do not prove continuous-capture completeness across faults.

### Architecture, Telemetry, Privacy, and Claim Guardrails

- Build evaluator code under the separate Python 3.11 `evaluation/` project. Domain contracts are stdlib-only; adapters translate to domain-owned records; adapters do not import each other.
- Preserve product ownership: daemon is the graph's sole writer; product qualification uses daemon/MCP and native spool/health seams, never direct graph reads.
- Telemetry is qualified evidence, not operational truth. Use local OTel `1.44.0`, Collector `0.158.0` with the frozen archive hash, OpenInference `0.1.31`, OpenAI instrumentation `0.1.53` only where compatible, and `memrelay.eval.genai-map/1.0.0`.
- Telemetry must omit prompts, code, repository names, usernames, credentials, provider payloads, and treatment labels by default. Sentinel IDs are synthetic and non-secret.
- Use local evidence infrastructure only: append-only SQLite WAL, SHA-256 CAS/manifests, Collector, Parquet, read-only DuckDB, and generated local reports. No managed telemetry, cloud warehouse, tracker, or private production trace is required.
- A pass supports only: “the named configured observation path, source/mapping/configuration versions, and conformance window passed the frozen sentinel and reconciliation contract.” It cannot support efficacy, safety, economics, generalized production completeness, or cross-repository fitness.
- Qualification fails closed. Missing primary evidence, source/telemetry disagreement, or hash drift yields an unqualified path and blocks any release statement that depends on it.
- Preserve TEA identities in the executable catalog and generated traceability: `RELEASE-CONTINUOUS` is the sentinel decision; `EV-ROUNDTRIP-MCP` remains only `CL-PIPELINE-SEAM`; applicable `TEL-DROP`, `TEL-DUPLICATE`, `TEL-OUT-OF-ORDER`, `TEL-PRIMARY`, and `TEL-FAILURE` records remain separate evidence authorities.

### File Targets

- **NEW after foundation stories:** `evaluation/src/memrelay_eval/domain/observation.py`
- **UPDATE:** `evaluation/src/memrelay_eval/adapters/memrelay/product.py`; if an internal `observation.py` helper is needed, it must remain behind this existing Story 2.6 product adapter and must not create a second launcher, graph reader, or product authority.
- **UPDATE/CREATE:** `evaluation/src/memrelay_eval/evidence/required.py`, `reconcile.py`, `manifest.py`
- **UPDATE/CREATE:** `evaluation/src/memrelay_eval/adapters/telemetry/semantics.py`, `reconcile.py`
- **UPDATE/CREATE:** `evaluation/src/memrelay_eval/cli/commands.py`
- **NEW:** `evaluation/schemas/observation-qualification.schema.json`
- **NEW:** `evaluation/tests/contract/test_observation_sentinels.py`
- **NEW:** `evaluation/tests/fault/test_observation_reconciliation.py`
- **PRESERVE/UPDATE only if the conformance seam requires it:** `src/memrelay/daemon/session_discovery.py`, `src/memrelay/daemon/runtime.py`
- **PRESERVE/EXTEND:** `tests/unit/test_session_discovery.py`, `tests/unit/test_live_tail_capture.py`, `tests/integration/test_session_discovery_e2e.py`, `tests/integration/test_filewatch_parity_e2e.py`, `tests/integration/test_observe_to_engine_e2e.py`, `tests/integration/test_release_gate_roundtrip.py`

Before modifying either product source file, preserve discovery freshness/internal-session exclusion, replay idempotency, final drain, live-tail replay backstop, LRU teardown, shared spool path, daemon single-writer ownership, and health counters.

### Frozen Versions and Testing Requirements

- Preserve architecture pins: Python `3.11`; product Python `>=3.11,<3.14`; traceforge-toolkit `>=0.1,<0.1.2`; graphiti-core `>=0.29,<0.30`; Ladybug `>=0.18,<0.18.1`; MCP `>=1.0,<2`.
- Evaluator pins remain exact: Inspect `0.3.252`; Copilot SDK `1.0.8` with wheel SHA-256 `7c3d868b73daa3a154ae6c1c5a2bd9301c47349c6b080f1ed77ba9027da3e0fa`; OTel SDK/exporters `1.44.0`; Collector contrib `0.158.0` Windows amd64 with archive SHA-256 `4314abde3c8acc67af58bb8d7611aa991fd80abe4a412695167f956d9fff3005`; OpenInference `0.1.31`; OpenAI instrumentation `0.1.53`; DuckDB `1.5.5`; PyArrow `25.0.0`; framework model `gpt-4.1-mini-2025-04-14`; local embedding model `BAAI/bge-small-en-v1.5`.
- Use `asyncio.run(...)`; the product dev dependencies do not include `pytest-asyncio`.
- Unit tests use injected clocks/discovery/capture/tail sources. Contract tests use synthetic local traces. Fault tests cover every AC2 failure and crash/restart publication boundary.
- Verify byte-identical canonical decisions for identical inputs and changed hashes for any source/mapping/configuration change.

### Anti-Patterns

- Do not replace the current observation implementation, add a second capture stack, or give evaluator code graph ownership.
- Do not call an observed count or OTLP delivery “complete.”
- Do not pool modes, silently deduplicate without retaining duplicate evidence, ignore terminal flush, or infer missing events.
- Do not use real user sessions, private repositories, credentials, production telemetry, treatment labels, or repository names in sentinels/logs.
- Do not promote fixtures, sentinels, component tests, or construction success into causal or product-efficacy claims.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story-7.1-Prove-Observation-Paths-with-Sentinels-and-Reconciliation]
- [Source: _bmad-output/planning-artifacts/architecture/architecture-dfinson-solid-happiness-2026-08-05/ARCHITECTURE-SPINE.md#AD-21---Current-observation-implementation-is-source-truth-ADOPTED]
- [Source: _bmad-output/planning-artifacts/architecture/architecture-dfinson-solid-happiness-2026-08-05/IMPLEMENTATION-DESIGN.md#17.5-Completeness]
- [Source: _bmad-output/planning-artifacts/architecture/architecture-dfinson-solid-happiness-2026-08-05/IMPLEMENTATION-DESIGN.md#Epic-8---Stage-Controls-Faults-and-Release-Integration]
- [Source: _bmad-output/test-artifacts/test-design-qa.md#NFR-Test-Coverage-Plan]
- [Source: _bmad-output/test-artifacts/test-design-qa.md#Execution-Strategy]
- [Source: _bmad-output/planning-artifacts/research/technical-evaluating-memrelay-collective-recall-for-coding-agent-reliability-cost-and-efficiency-research-2026-08-04.md#Correct-scope-of-the-deterministic-fixture]
- [Source: SPEC.md#3.1-Session-Discovery]
- [Source: src/memrelay/daemon/session_discovery.py]
- [Source: src/memrelay/daemon/runtime.py]

## Dev Agent Record

### Agent Model Used

To be recorded by the implementing dev agent.

### Debug Log References

### Completion Notes List

- Ultimate context engine analysis completed - comprehensive developer guide created

### File List
