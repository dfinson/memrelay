# Story 2.10: Preserve Native Evidence and Scan Secret Boundaries

Status: ready-for-dev

## Story

As an evidence operator,
I want every attempt export and surface scanned against native evidence and credential canaries,
So that contaminated or incomplete trials cannot proceed to grading.

## Acceptance Criteria

1. **Given** a running or terminal attempt  
   **When** native evidence is exported  
   **Then** Inspect `.eval` and JSON, SDK events and terminal status, patch, usage, limits, cancellation, typed failures, monotonic active-agent time, and separate provisioning/queue/backoff/cleanup times are retained by artifact reference  
   **And** capped and failed runs are not dropped.
2. **Given** environments, workspaces, prompts, logs, tools, traces, manifests, configuration, or artifacts  
   **When** secret-canary scanning runs  
   **Then** OpenAI, GitHub, Copilot, and synthetic canaries are checked against process-specific prohibitions  
   **And** a confirmed credential leak blocks the stage and preserves evidence without echoing the secret.
3. **Given** raw telemetry or logs  
   **When** they are emitted  
   **Then** prompts, code, repository names, usernames, credentials, treatment labels, and provider payloads are omitted by default  
   **And** treatment labels never appear in agent-visible resources, baggage, prompts, or logs.
4. **Given** native evidence artifacts before Story 4.1  
   **When** they are written or resolved  
   **Then** Story 2.10 consumes the Story 1.1 `ArtifactStorePort` and `ArtifactManifest` schema `1.0.0`, using deterministic fake artifacts solely for unpaid conformance until Story 4.1 provides the durable filesystem CAS  
   **And** paid execution and study inclusion remain blocked until Story 4.1 passes conformance.
5. **Given** evidence lifecycle or telemetry before Stories 4.2 and 4.3  
   **When** it is emitted  
   **Then** it flows through the Story 1.1 domain-owned `LedgerPort` and `TelemetryPort` using deterministic fakes for unpaid conformance  
   **And** durable study execution and inclusion remain blocked until the concrete sole-writer SQLite ledger and Collector adapters pass conformance.

## Dependencies and Prerequisites

- Authoritative direct dependencies are Stories 2.3 and 2.4. Later strata add memrelay/provider evidence to the same contract.
- Story 1.1 supplies fake evidence ports, lifecycle/failure records, redaction, `ArtifactManifest` schema `1.0.0`, and study-eligibility gating. Story 1.2 is catalog-only and is not a dependency.
- This story preserves evidence behind ports; it does not implement Epic 4 durable artifact store, SQLite ledger, Collector, or Parquet lake.

## Tasks / Subtasks

- [ ] Define typed artifact manifest and native execution/timing inventory (AC: 1)
  - [ ] Reference immutable `.eval`, Inspect JSON, SDK events, memrelay provenance, provider usage/cost, grader/judge, trace/log artifacts.
  - [ ] Record content hashes, media/schema types, producer/authority, attempt/stratum/regime IDs, and redacted locations.
- [ ] Normalize only treatment-neutral domain fields and retain capped/failed attempts (AC: 1)
  - [ ] Preserve native payloads byte-for-byte; keep summaries/projections derived and independently hashed.
- [ ] Implement the pre-grading native-authority consistency gate (Architecture: AD-15)
  - [ ] Compare terminal state, usage, retries, costs, and artifact completeness across applicable sources.
  - [ ] Record all conflicts and prevent grading or automated success/inclusion; leave complete terminal reconciliation and inclusion decisions to Story 4.5.
- [ ] Implement fail-closed process-specific secret/canary scanning before grading (AC: 2)
  - [ ] Scan environment projections, logs, traces, artifacts, manifests, and serialized telemetry.
  - [ ] Preserve only redacted finding evidence; never echo matched values.
- [ ] Enforce raw telemetry/log default omission and treatment-label concealment (AC: 3)
- [ ] Enforce fake `ArtifactStorePort` provenance and study-ineligibility before Story 4.1 (AC: 4)
- [ ] Enforce fake `LedgerPort`/`TelemetryPort` provenance and study-ineligibility before Stories 4.2/4.3 (AC: 5)
- [ ] Add tamper, disagreement, hidden-retry, secret-format, encoding/archive, and grading-gate tests (AC: 1-5)

## Developer Context

Native sources remain separate authorities; normalized domain records are treatment-neutral projections, not replacements. Inspect controls execution, while SDK terminal/runtime records corroborate it. Provider usage/cost, memrelay provenance, graders, judges, and telemetry retain their own provenance. Any disagreement is visible and blocks—never resolve by selecting the favorable source.

### Architecture Compliance

- Follow AD-04, AD-05, AD-08, AD-09, AD-15, AD-18, AD-21, AD-25.
- Immutable artifacts hold native bytes/manifests; the thin ledger stores references/status transitions only.
- Secrets are never accepted identity fields and credential values never enter persisted domain records.
- Grading is downstream of a fail-closed scan; scan failure preserves redacted evidence and blocks.
- Fake `LedgerPort`, `ArtifactStorePort`, `TelemetryPort` artifacts are explicitly `unpaid_conformance`; only Epic 4 durable adapters may establish paid provenance/study inclusion.

### Library and Version Requirements

- Python 3.13 and Story 1 canonicalization/hashing/redaction utilities.
- Source pins represented exactly: Inspect `0.3.252`, Copilot SDK `1.0.8`, OTel Python `1.44.0`.
- Future durable telemetry uses `otelcol-contrib` `0.158.0` Windows amd64 archive `otelcol-contrib_0.158.0_windows_amd64.tar.gz`, SHA-256 `4314abde3c8acc67af58bb8d7611aa991fd80abe4a412695167f956d9fff3005`, plus OpenInference `0.1.31` and OpenAI instrumentation `0.1.53`; do not implement those adapters here.
- No secret-scanner dependency is added unless already frozen or architecture is amended; deterministic scanners must handle configured key/token/canary forms.

### Expected File Paths

- **UPDATE:** `evaluation/src/memrelay_eval/domain/{entities,errors,policies}.py`
- **NEW:** `evaluation/src/memrelay_eval/evidence/{manifest,required,secret_scan}.py`
- **UPDATE:** `evaluation/src/memrelay_eval/adapters/fakes.py`
- **UPDATE:** `evaluation/schemas/artifact-manifest.schema.json`
- **NEW:** `evaluation/tests/contract/evidence/`
- **NEW:** `evaluation/tests/security/test_secret_boundaries.py`
- **NEW:** `evaluation/tests/fault/test_native_evidence_preservation.py`
- **READ ONLY:** Epic 4 durable adapter paths; product sources
- **NOT IN SCOPE:** concrete CAS, SQLite ledger, Collector/telemetry adapters, cost ledger, complete reconciliation, Parquet, or inclusion implementation

### Existing Behavior to Preserve

- All native artifacts and partial evidence from earlier stories survive timeout, cancellation, crash, reconciliation conflict, and scan failure.
- Exposure/retry evidence remains append-only; scanners must detect hidden retry records rather than rewrite them.
- Treatment labels remain absent from task-agent payloads, generic telemetry attributes, filenames, and ordinary evidence projections.

### Testing Requirements

- Completeness matrix for every source/terminal path, including source unavailable versus contradictory.
- Pairwise/multi-source disagreement tests always block and retain all source references.
- Secret corpus covers OpenAI/provider keys, GitHub/PAT/Copilot/subscription material, canaries, encoded/serialized/log/trace forms, and safe false-positive fixtures.
- Tampered native bytes/hash mismatch, partial artifact, hidden retry, and cost/usage mismatch tests.
- Pre-grading gate denies fake-port provenance and any scan/native-authority failure; CI remains fake/unpaid.

### Anti-Patterns

- Do not flatten native evidence into one “truth” JSON or discard contradictory sources.
- Do not choose successful/cheaper/favorable evidence, redact by deleting whole failure records, or log matched secrets.
- Do not claim fake evidence is durable/paid/study-eligible or prematurely build Epic 4 stores.
- Do not grade before scanning, retry post-exposure, or make unbounded provider calls.

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` — “Story 2.10”]
- [Source: `ARCHITECTURE-SPINE.md` — AD-04, AD-05, AD-08, AD-09, AD-15, AD-18, AD-21, AD-25]
- [Source: `IMPLEMENTATION-DESIGN.md` — §§5, 8.2-8.3, 10.3, 14, 16-17, 20, 22, 25]
- [Source: `test-design-qa.md` — telemetry, reconciliation, hidden-retry, and secret-isolation scenarios]
- [Source: TEA handoff — durable-evidence blockers and pre-publication security gates]

## Dev Agent Record

### Agent Model Used

TBD by implementation agent

### Debug Log References

### Completion Notes List

- Ultimate context engine analysis completed - comprehensive developer guide created

### File List
