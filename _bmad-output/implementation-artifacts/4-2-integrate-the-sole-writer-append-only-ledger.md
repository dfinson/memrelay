# Story 4.2: Integrate the Sole-Writer Append-Only Ledger

Status: ready-for-dev

## Story

As an execution controller,
I want one SQLite WAL writer to append lifecycle, evidence, and inclusion records,
So that workers cannot corrupt operational truth or place large evidence in the ledger.

## Acceptance Criteria

1. **Given** disposable workers executing attempts  
   **When** they need a lifecycle change or artifact link  
   **Then** they emit typed transition intents and never open SQLite directly  
   **And** the Inspect control process validates and appends experiment, run, attempt, transition, artifact-link, and inclusion records as sole writer.
2. **Given** an existing ledger record  
   **When** repository APIs request update or deletion  
   **Then** the operation is unavailable or rejected  
   **And** crash/reopen preserves exact append history and retry lineage.
3. **Given** prompts, patches, traces, grader bodies, Inspect events, or other large evidence  
   **When** persistence occurs  
   **Then** only CAS references and digests enter SQLite  
   **And** DuckDB and analysis code cannot mutate or use SQLite as analysis state.

## Dependencies and Prerequisites

- **Authoritative direct graph dependency:** Story 1.1 only, exactly as declared in `epics.md`.
- Story 1.1 supplies `LedgerPort`, the run/attempt split, typed errors, and the deterministic fake ledger.
- Story 4.1 CAS is an integration prerequisite for durable artifact links, though ledger schema/contract work can proceed against typed refs.
- Workers and orchestration from Epic 2 must emit intents across the existing process boundary; they never receive a database path or connection.
- Paid execution and inclusion remain blocked until sole-writer enforcement, append-only migration, crash recovery, and Collector adapter conformance all pass.
- Exact traceability: FR12, FR13, FR43; NFR1, NFR29, NFR31; AR17, AR18, AR28.

## Tasks / Subtasks

- [ ] Define typed worker-to-control intents (AC: 1)
  - [ ] Add immutable intents for experiment/run/attempt creation, run transition, attempt terminal classification, artifact link, retry link, and inclusion decision.
  - [ ] Include opaque intent ID, source attempt, expected prior state/digest, UTC and monotonic timing where applicable, evidence refs, and stable reason code.
  - [ ] Make duplicate delivery idempotent by intent ID plus canonical payload digest; conflicting reuse fails closed.
- [ ] Implement SQLite WAL schema and repository behind `LedgerPort` (AC: 1, 2)
  - [ ] Append separate normalized rows for identities, transitions, terminal attempts, artifact links, retry lineage, and inclusion decisions.
  - [ ] Enable WAL and integrity/foreign-key settings explicitly; serialize all writes through one control-owned connection.
  - [ ] Expose history/read projections without update/delete methods.
- [ ] Integrate the sole writer into Inspect control composition (AC: 1)
  - [ ] Validate every intent against domain state/exposure/retry policy before one transaction appends it.
  - [ ] Return typed acknowledgement/rejection; preserve rejected intent evidence without inventing lifecycle transitions.
  - [ ] Ensure worker launch environments contain no evaluator SQLite path, handle, library-specific object, or inherited connection; architecture tests also reject `sqlite3` imports/opens from worker code.
- [ ] Enforce thin-ledger boundaries (AC: 3)
  - [ ] Accept only opaque IDs, canonical digests, small typed metadata, and Story 4.1 `ArtifactRef`s.
  - [ ] Reject payload bodies, prompts, patches, traces, grader/judge bodies, Inspect events, provider payloads, credentials, and repository names.
- [ ] Implement append-only migrations and crash recovery (AC: 2)
  - [ ] Version and hash schema migrations in an append-only migration journal; add tables/columns without rewriting prior rows or event meaning.
  - [ ] Reopen after process kill/WAL recovery with byte-for-byte canonical history and intact retry lineage.
  - [ ] Detect partial intent application and replay safely without double transition/inclusion records.
- [ ] Enforce analysis separation (AC: 3)
  - [ ] Provide no analysis mutation API; mechanically reject imports/connections from `analysis`.
  - [ ] Leave atomic reconciled Parquet publication and read-only DuckDB to Epic 5.
- [ ] Add lifecycle, concurrency, migration, crash, and boundary tests (AC: 1-3).

## Developer Context

SQLite is durable operational truth, not an evidence warehouse or analytics engine. Inspect is execution authority; the ledger records immutable identity/lifecycle projections and references. The control process is the only writer. Disposable workers communicate domain-owned typed intents and cannot open SQLite even for “read-only convenience.” Intent replay, process crash, and acknowledgement loss must be idempotent without hiding conflicts. Run transitions and immutable attempt terminal classifications remain distinct.

“Sole writer” here means the evaluator's Inspect control process is the sole writer of the evaluator SQLite ledger. It is separate from the shipped memrelay daemon's existing sole ownership of its isolated graph/spool; this story must not open or bypass that product-owned state.

### Architecture Compliance

- Follow AD-04, AD-05, AD-08, AD-11, AD-15, AD-17, AD-18, AD-22.
- Python 3.11; stdlib `sqlite3`; WAL mode. Do not introduce an ORM or external SDK types into domain/ledger contracts.

### Frozen Version Requirements

- Preserve the frozen lifecycle `planned -> assigned -> provisioned -> running -> exported -> scored -> reconciled -> included|excluded`.
- Preserve one protocol-authorized retry only for conclusively pre-exposure infrastructure failure; both attempts and their link remain.

### File Structure Requirements

- **UPDATE:** `evaluation/src/memrelay_eval/domain/{entities,states,ports,errors,policies}.py`
- **NEW/UPDATE:** `evaluation/src/memrelay_eval/ledger/{schema,sqlite,repository}.py`
- **UPDATE:** `evaluation/src/memrelay_eval/orchestration/{control,worker,attempt}.py`
- **UPDATE:** `evaluation/src/memrelay_eval/cli/{main,commands}.py` for composition only.
- **NEW:** `evaluation/tests/contract/ledger/test_repository.py`
- **NEW:** `evaluation/tests/fault/ledger/test_crash_reopen.py`
- **NEW:** `evaluation/tests/architecture/test_sole_writer_boundary.py`
- **READ ONLY:** CAS blob bodies, product source, future `analysis/` implementations.

### Existing Behavior to Preserve

- Keep fake ledger ordering/provenance and early-story unpaid eligibility behavior.
- Preserve opaque treatment-neutral IDs, exact lifecycle validation, all failed attempts, exposure decisions, retry links, partial evidence, and typed rejections.
- Preserve native evidence in CAS; never duplicate or normalize it into SQLite.
- Preserve exact Inspect `.eval`/JSON and native SDK authority outside SQLite; a ledger transition or telemetry span cannot manufacture execution success.

### Testing Requirements

- Run adapter contracts against fake and SQLite implementations; only SQLite may report durable qualification.
- Fault at begin/append/commit/ack/WAL-checkpoint/reopen boundaries and prove exact-once logical append by intent identity.
- Concurrent worker tests must show one serial writer, deterministic valid history, rejected stale intents, no locked-database leakage, and no worker connection.
- Migration tests open every supported prior schema and prove prior rows/digests unchanged.
- Static/runtime tests prohibit `analysis` from SQLite mutation and prohibit large/secret-bearing fields.

### Anti-Patterns

- Do not let each worker own a connection, serialize SQLite writes with retries in workers, or use a shared file lock as a substitute for the control-owned writer.
- Do not update/delete history, collapse attempt terminals into run transitions, auto-repair conflicts, or treat telemetry as lifecycle truth.
- Do not store native event streams or use SQLite for reporting/analysis.
- Do not implement Collector semantics, full reconciliation policy, cost ledgers, Parquet materialization, or DuckDB queries here.

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` — Epic 4 / Story 4.2; FR12, FR13, FR43]
- [Source: `ARCHITECTURE-SPINE.md` — AD-04, AD-05, AD-08, AD-11, AD-18]
- [Source: `IMPLEMENTATION-DESIGN.md` — §§5-8, 10, 20, 22]
- [Source: `_bmad-output/test-artifacts/test-design-architecture.md` — R-008, R-026]
- [Source: `_bmad-output/test-artifacts/test-design-qa.md` — `ITT-*`, `TEL-FAILURE`, `BLOCK-EVIDENCE`]
- [Source: predecessor Stories 1.1, 1.7, 2.10 — port, lifecycle, retry, and native-evidence contracts]

## Dev Agent Record

### Agent Model Used

TBD by implementation agent

### Debug Log References

### Completion Notes List

- Ultimate context engine analysis completed - comprehensive developer guide created

### File List
