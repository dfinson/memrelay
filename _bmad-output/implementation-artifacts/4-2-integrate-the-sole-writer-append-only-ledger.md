# Story 4.2: Integrate the Sole-Writer Append-Only Ledger

Status: review

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

- [x] Define typed worker-to-control intents (AC: 1)
  - [x] Add immutable intents for experiment/run/attempt creation, run transition, attempt terminal classification, artifact link, retry link, and inclusion decision.
  - [x] Include opaque intent ID, source attempt, expected prior state/digest, UTC and monotonic timing where applicable, evidence refs, and stable reason code.
  - [x] Make duplicate delivery idempotent by intent ID plus canonical payload digest; conflicting reuse fails closed.
- [x] Implement SQLite WAL schema and repository behind `LedgerPort` (AC: 1, 2)
  - [x] Append separate normalized rows for identities, transitions, terminal attempts, artifact links, retry lineage, and inclusion decisions.
  - [x] Enable WAL and integrity/foreign-key settings explicitly; serialize all writes through one control-owned connection.
  - [x] Expose history/read projections without update/delete methods.
- [x] Integrate the sole writer into Inspect control composition (AC: 1)
  - [x] Validate every intent against domain state/exposure/retry policy before one transaction appends it.
  - [x] Return typed acknowledgement/rejection; preserve rejected intent evidence without inventing lifecycle transitions.
  - [x] Ensure worker launch environments contain no evaluator SQLite path, handle, library-specific object, or inherited connection; architecture tests also reject `sqlite3` imports/opens from worker code.
- [x] Enforce thin-ledger boundaries (AC: 3)
  - [x] Accept only opaque IDs, canonical digests, small typed metadata, and Story 4.1 `ArtifactRef`s.
  - [x] Reject payload bodies, prompts, patches, traces, grader/judge bodies, Inspect events, provider payloads, credentials, and repository names.
- [x] Implement append-only migrations and crash recovery (AC: 2)
  - [x] Version and hash schema migrations in an append-only migration journal; add tables/columns without rewriting prior rows or event meaning.
  - [x] Reopen after process kill/WAL recovery with byte-for-byte canonical history and intact retry lineage.
  - [x] Detect partial intent application and replay safely without double transition/inclusion records.
- [x] Enforce analysis separation (AC: 3)
  - [x] Provide no analysis mutation API; mechanically reject imports/connections from `analysis`.
  - [x] Leave atomic reconciled Parquet publication and read-only DuckDB to Epic 5.
- [x] Add lifecycle, concurrency, migration, crash, and boundary tests (AC: 1-3).

## Developer Context

SQLite is durable operational truth, not an evidence warehouse or analytics engine. Inspect is execution authority; the ledger records immutable identity/lifecycle projections and references. The control process is the only writer. Disposable workers communicate domain-owned typed intents and cannot open SQLite even for “read-only convenience.” Intent replay, process crash, and acknowledgement loss must be idempotent without hiding conflicts. Run transitions and immutable attempt terminal classifications remain distinct.

“Sole writer” here means the evaluator's Inspect control process is the sole writer of the evaluator SQLite ledger. It is separate from the shipped memrelay daemon's existing sole ownership of its isolated graph/spool; this story must not open or bypass that product-owned state.

### Architecture Compliance

- Follow AD-04, AD-05, AD-08, AD-11, AD-15, AD-17, AD-18, AD-22.
- Python 3.13; stdlib `sqlite3`; WAL mode. Do not introduce an ORM or external SDK types into domain/ledger contracts.

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

GPT-5.6 Terra

### Debug Log References

- `PYTHONPATH=<worktree>\evaluation\src python -m pytest evaluation\tests\contract\ledger\test_repository.py evaluation\tests\fault\ledger\test_crash_reopen.py evaluation\tests\architecture\test_sole_writer_boundary.py evaluation\tests\contract\test_fakes.py -q` — 45 passed, 1 Windows-only fork test skipped.
- `PYTHONPATH=<worktree>\evaluation\src python -m pytest evaluation\tests -q` — 144 passed, 1 Windows-only fork test skipped.
- `PYTHONPATH=<worktree>\src python -m pytest -q` — 1305 passed, 2 optional cloud-backend tests skipped.
- `python -m ruff check . && python -m ruff format --check .` — passed.

### Completion Notes List

- Added immutable typed worker-to-control intents with canonical SHA-256 payload digests, typed acknowledgements/rejections, thin rejected-intent evidence, and fail-closed ID reuse.
- Added a sole-control-owned stdlib SQLite WAL ledger with additive migration hash journal, normalized append-only tables, foreign keys, integrity checks, replay-safe receipt handling, and read projections.
- Kept run lifecycle transitions separate from immutable attempt terminals and authorized only one pre-exposure infrastructure retry lineage.
- Added narrow worker intent emission/control handling and mechanical worker/analysis SQLite boundary coverage; no product or future telemetry, reconciliation, backup, or analysis implementation changed.
- Added repository, fault/reopen, concurrency, migration-journal, thin-boundary, and architecture tests.
- Review repairs: added an OS-backed control ownership lease with process-crash release coverage; aligned `WorkerIntentEmitter`, `LedgerControl`, and the unpaid fake; made attempt creation/retry authorization control-bound and pre-exposure-evidence-gated; and bound terminal lifecycle state to one matching inclusion decision.
- Regression coverage confirms `LedgerControl` persists a typed, idempotent `attempt_creation_control_only` rejection through the public `LedgerPort.reject_intent` operation for the SQLite adapter, including conflicting intent-ID reuse.
- Final repairs limit workers to source-attempt-scoped lifecycle and artifact intents; add fork-safe POSIX inherited-handle cleanup; align fake/SQLite retry-source and artifact-ownership validation; and add an explicit Python 3.13 evaluator test step to CI without installing the evaluator editable.
- Malformed intent delivery now preflights safe metadata and evidence-ref types before canonical serialization, uses a body-free rejection digest for safe idempotent replay, and persists typed thin rejections through SQLite, fake, and control paths.

### File List

- `evaluation/src/memrelay_eval/domain/{__init__,errors,ids,intents,policies,ports,states}.py`
- `evaluation/src/memrelay_eval/adapters/fakes.py`
- `evaluation/src/memrelay_eval/ledger/{__init__,schema,sqlite,repository}.py`
- `evaluation/src/memrelay_eval/orchestration/{__init__,control,worker,attempt}.py`
- `evaluation/tests/contract/ledger/test_repository.py`
- `evaluation/tests/fault/ledger/test_crash_reopen.py`
- `evaluation/tests/architecture/test_sole_writer_boundary.py`
- `.github/workflows/ci.yml`
