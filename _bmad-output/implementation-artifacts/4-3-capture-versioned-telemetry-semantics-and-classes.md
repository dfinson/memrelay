# Story 4.3: Capture Versioned Telemetry Semantics and Classes

Status: review

## Story

As an observability engineer,
I want local telemetry with a frozen semantic map and required span registry,
So that all execution layers can be reconciled without leaking sensitive payloads.

## Acceptance Criteria

1. **Given** evaluator bootstrap  
   **When** telemetry components are verified  
   **Then** OpenTelemetry SDK/exporters are exactly `1.44.0`, `otelcol-contrib` is Windows amd64 `0.158.0` archive `otelcol-contrib_0.158.0_windows_amd64.tar.gz` SHA-256 `4314abde3c8acc67af58bb8d7611aa991fd80abe4a412695167f956d9fff3005`  
   **And** OpenInference is `0.1.31`, OpenAI instrumentation `0.1.53` only after pinned-client compatibility, and OTel GenAI Development fields pass through `memrelay.eval.genai-map/1.0.0`.
2. **Given** control, provisioning, Copilot, MCP, daemon, memory, framework, grader, judge, artifact, export, cost, reconciliation, and cleanup activity  
   **When** spans are emitted  
   **Then** required span classes and `memrelay.eval.*` IDs, stratum, history mode, provider, credential domain, cost source, evidence class, exposure state, and failure code are present  
   **And** closed process boundaries use opaque correlation and span links rather than fabricated parentage.
3. **Given** Collector shutdown, drop, duplicate, out-of-order, or partial-success faults  
   **When** telemetry conformance runs  
   **Then** every injected fault is detected and recorded  
   **And** one Collector per invocation is accepted only if export, shutdown, and reconciliation proofs pass.

## Dependencies and Prerequisites

- **Authoritative direct graph dependency:** Story 2.10 only, exactly as declared in `epics.md`.
- Story 2.10 supplies the native-evidence inventory, redaction/canary scanning, `TelemetryPort`, and unpaid fake provenance.
- Story 4.1 supplies the durable CAS for raw OTLP/Collector/config/fault evidence; Story 4.2 supplies lifecycle/artifact references, never trace bodies.
- OpenAI auto-instrumentation is conditional on compatibility with the frozen client; incompatibility fails conformance rather than changing pins.
- Paid study execution and inclusion remain blocked until the concrete Collector/OTLP adapter and sole-writer SQLite ledger both pass conformance.
- Exact traceability: FR39; NFR16, NFR23, NFR30, NFR38; AR6-AR8, AR29.

## Tasks / Subtasks

- [x] Freeze telemetry dependencies and bootstrap verification (AC: 1)
  - [x] Pin OTel API/SDK/exporters `1.44.0`, OpenInference `0.1.31`, and conditional OpenAI instrumentation `0.1.53` in the evaluator lock only.
  - [x] Locate only the exact Collector archive, verify the frozen SHA-256 before extraction/execution, and retain verification evidence.
  - [x] Add local OTLP receiver/export pipeline and deterministic shutdown/flush handling; no managed backend.
- [x] Implement semantic map `memrelay.eval.genai-map/1.0.0` (AC: 1, 2)
  - [x] Map OTel GenAI Development fields behind the compatibility layer; never expose unstable fields as domain contracts.
  - [x] Require schema version and opaque experiment/protocol/run/attempt/scenario/stratum IDs, history mode, provider, credential domain, cost source, evidence class, exposure state, and failure code.
- [x] Implement and version the required span-class registry (AC: 2)
  - [x] Cover control/assignment, provisioning/cleanup, Copilot session/model request, MCP, daemon, memory write/retrieval, framework extraction/embedding, grader, judge/adjudication, artifact, Inspect export, cost, and evidence reconciliation.
  - [x] Require `memrelay.eval.attempt_id` on every attempt span and link emitted classes to independent native evidence expectations.
  - [x] Create the attempt identity before emitting assignment/control spans that belong to an attempt; do not omit the field or substitute a run ID.
- [x] Instrument adapter boundaries without sensitive payload capture (AC: 2)
  - [x] Omit prompts, code, repository/user names, credentials, treatment labels, provider payloads, and secret values by default.
  - [x] Use trace context only where genuinely propagated; otherwise create opaque correlation records and span links.
- [x] Implement local Collector lifecycle and correlation evidence (AC: 2, 3)
  - [x] Start one Collector per invocation, prove readiness, export, bounded flush, shutdown, and process cleanup.
  - [x] Preserve raw export/native source evidence by CAS reference and environment/configuration fingerprints by hash.
  - [x] Treat any bounded JSONL repair transport as temporary, versioned, expiry-controlled conformance evidence only; it cannot silently qualify paid execution or replace the required Collector topology.
- [x] Add telemetry fault and secret-boundary suites (AC: 3)
  - [x] Inject drop, duplicate, out-of-order, partial-success, stalled export, Collector crash, shutdown race, and missing class.
  - [x] Prove raw order/duplicates remain auditable, canonical projections are deterministic, and OTLP delivery alone never marks complete.

## Developer Context

Telemetry is observation, not lifecycle authority. The local Collector transports OTLP; completeness comes only from reconciliation against native SDK, Inspect, memrelay, grader, artifact, cost, and ledger authorities. Every process has a minimal environment and opaque correlation identity. Preserve native, secret-safe evidence without placing prompts/code/provider payloads into spans. Environment fingerprint changes create a separate stratum and are linked by hash, not copied as sensitive resource attributes.

### Architecture Compliance

- Follow AD-09, AD-14, AD-15, AD-17, AD-22, AD-24, AD-25.
- One Collector per invocation remains an assumption until all mandatory fault/export/shutdown/reconciliation proofs pass.
- OpenTelemetry/OpenInference objects terminate at the telemetry adapter; domain sees only domain-owned records.

### Frozen Version Requirements

- Exact pins and Collector archive/hash are normative. Any change requires a new protocol/version, not an opportunistic upgrade.

### File Structure Requirements

- **UPDATE:** `evaluation/pyproject.toml`, `evaluation/uv.lock`
- **NEW/UPDATE:** `evaluation/src/memrelay_eval/adapters/telemetry/{otel,semantics,reconcile}.py`
- **UPDATE:** instrumentation call sites in orchestration/adapters; do not create cross-adapter imports.
- **NEW/UPDATE:** `evaluation/collector/{collector.yaml,semantic-map.yaml}`
- **NEW:** `evaluation/schemas/telemetry-evidence.schema.json`
- **NEW:** `evaluation/tests/contract/telemetry/`
- **NEW:** `evaluation/tests/fault/telemetry/`
- **READ ONLY:** provider credentials, product payload bodies, future Parquet/DuckDB code.

### Existing Behavior to Preserve

- Preserve Story 2.10 omission rules, redacted secret-scan evidence, native event bytes, terminal status, timing separation, and fake telemetry ineligibility.
- Preserve lifecycle truth in Inspect/ledger and provider truth in native provider evidence.
- Preserve treatment concealment and process-specific credential separation.

### Testing Requirements

- Verify exact package/archive hashes and compatibility before any adapter is qualified.
- Contract tests validate every required class/field, mapping determinism, links across closed boundaries, environment fingerprint hash, and absence of prohibited attributes.
- Fault tests map to `TEL-DROP`, `TEL-DUPLICATE`, `TEL-OUT-OF-ORDER`, `TEL-PRIMARY`, `TEL-FAILURE`, and Collector conformance.
- CI is local, credential-free, no paid calls, and uses deterministic receivers/fault injectors.

### Anti-Patterns

- Do not infer completeness from a successful OTLP response, fabricate parent spans, flatten provider identities, or put treatment labels/secrets in resources or baggage.
- Do not let Collector/telemetry update lifecycle state or SQLite directly.
- Do not add managed telemetry, a cloud warehouse, third-party tracker, or unpinned semantic fields.
- Do not auto-select a fallback when Collector/archive/compatibility proof fails; emit typed conformance failure and keep paid stages blocked.
- Do not implement Story 4.4 identity policy, Story 4.5 final inclusion, cost arithmetic, Parquet publication, or DuckDB analytics here.

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` — Epic 4 / Story 4.3; FR39]
- [Source: `ARCHITECTURE-SPINE.md` — AD-14, AD-15, AD-22, AD-24]
- [Source: `IMPLEMENTATION-DESIGN.md` — §§15.4, 17, 22, 24.1, 25]
- [Source: technical research — conditional telemetry completeness and local OTel/OTLP commitments]
- [Source: `_bmad-output/test-artifacts/test-design-qa.md` — R-009, R-035; `TEL-*`, `TOOL-OTEL-*`]
- [Source: predecessor Story 2.10 — native evidence, redaction, fake telemetry, and durable-adapter blocker]

## Dev Agent Record

### Agent Model Used

GPT-5.6 Terra (gpt-5.6-terra)

### Debug Log References

- `py -3.13 -m pytest evaluation\tests -q` — 995 passed, 4 skipped.
- `py -3.13 -m pytest -q` — 1303 passed, 4 skipped.
- `python -m ruff check evaluation\src evaluation\tests && python -m ruff format --check evaluation\src evaluation\tests` — passed.
- `uv lock --project evaluation --check` — passed under WSL CPython 3.13.14.
- `git diff --check` — passed.

### Completion Notes List

- Added a frozen `memrelay.eval.genai-map/1.0.0` compatibility mapper, strict opaque semantic context, and a versioned registry covering every required span class.
- Added local Collector archive verification/extraction/lifecycle, local OTLP configuration, bounded export, and opaque cross-process OTel links without fabricated parentage.
- Added fail-closed raw-order reconciliation for dropped, duplicate, out-of-order, partial-success, shutdown, and conflicting telemetry without selecting a favorable source.
- Preserved raw telemetry as CAS evidence only, kept the ledger outside telemetry persistence, and extended the unpaid deterministic fake with prevalidated semantic spans.
- Added schema, secret-boundary, malformed/version/class, concurrency/replay, lifecycle timeout, and reconciliation contract/fault coverage.

### File List

- `_bmad-output/implementation-artifacts/{4-3-capture-versioned-telemetry-semantics-and-classes.md,sprint-status.yaml}`
- `evaluation/{pyproject.toml,uv.lock,collector/{collector.yaml,semantic-map.yaml},schemas/telemetry-evidence.schema.json}`
- `evaluation/src/memrelay_eval/{adapters/fakes.py,adapters/telemetry/{__init__,otel,reconcile,semantics}.py,domain/errors.py}`
- `evaluation/tests/{contract/telemetry/test_semantics.py,fault/telemetry/{test_collector_lifecycle,test_reconciliation}.py}`
