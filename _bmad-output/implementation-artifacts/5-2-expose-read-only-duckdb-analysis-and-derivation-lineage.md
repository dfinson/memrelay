# Story 5.2: Expose Read-Only DuckDB Analysis and Derivation Lineage

Status: ready-for-dev

## Story

As a researcher,
I want read-only DuckDB queries over versioned Parquet,
So that analyses are reproducible and cannot mutate operational evidence.

## Acceptance Criteria

1. **Given** DuckDB exactly `1.5.5`  
   **When** `memrelay-eval analyze --stage <stage>` opens data  
   **Then** it reads only reconciled Parquet through a read-only connection  
   **And** no path exists to mutate SQLite, CAS, source Parquet, or operational records.
2. **Given** a query that combines product/engine strata, controlled/dynamic histories, model strata, or changed environment fingerprints  
   **When** no explicit valid stratified operation is declared  
   **Then** schema/query validation rejects it  
   **And** the rejection records the conflicting dimensions.
3. **Given** a derived table, diagnostic, or figure  
   **When** it is emitted  
   **Then** source table hashes, SQL or derivation hash, protocol, population, endpoint, stratum, history mode, units, and gate IDs are recorded  
   **And** deterministic figures reproduce exact hashes.

## Dependencies and Prerequisites

- **Authoritative direct graph dependency:** Story 5.1 only, exactly as declared in `epics.md`.
- Story 5.1 supplies the published assigned-unit and eligible-outcome Parquet tables plus their lineage manifest.
- Analysis receives no provider credentials and no writable operational handle.
- Later Stories 5.3-5.7 consume this adapter; this story supplies safe reads and lineage, not statistical conclusions.

## Tasks / Subtasks

- [ ] Implement a DuckDB `1.5.5` read-only adapter (AC: 1)
  - [ ] Open only an immutable Story 5.1 dataset version from an allowlisted root and verify all hashes before registration.
  - [ ] Use an isolated ephemeral catalog with external access disabled and manifest-listed Parquet registered through controlled code; a DuckDB database-file `read_only` flag alone is not sufficient protection for Parquet.
  - [ ] Enforce a closed query API over registered logical tables, deny caller-supplied arbitrary SQL, extension load/install and network access, and expose no SQLite/CAS mutation API.
  - [ ] Reject path traversal, mutable aliases, unmanifested globs/table functions, `ATTACH`, `COPY`, DDL/DML, `PRAGMA`/`SET`/`CALL`, secret/config mutation, and write/export statements. Governed derived bytes are written by the artifact publisher, never by DuckDB into source roots.
- [ ] Build schema-aware stratification validation (AC: 2)
  - [ ] Require explicit valid operations for product/engine, controlled/dynamic, model, environment, protocol, population, and endpoint dimensions.
  - [ ] Reject unstratified joins/aggregates and record dimension values, query/derivation identity, typed reason, and source hashes without leaking treatment labels.
  - [ ] Preserve sequence/history/repository clustering metadata for downstream estimators.
- [ ] Create a derivation registry and manifest writer (AC: 3)
  - [ ] Hash canonical SQL plus bound parameters, non-SQL derivation code/version, ordered inputs, runtime lock, and output schema.
  - [ ] Attach protocol, population, endpoint, stratum, history mode, units, gates, source table/file/schema hashes, and parent derivations.
  - [ ] Emit immutable decision records for successful and rejected derivations.
- [ ] Produce deterministic table/figure primitives (AC: 3)
  - [ ] Freeze sorting, numeric formatting, categorical order, fonts/rendering dependencies, metadata stripping, and output encoding.
  - [ ] Store outputs atomically by hash; exact input/derivation reruns must reproduce exact figure bytes.
- [ ] Wire `memrelay-eval analyze --stage <stage>` without adding inference policy (AC: 1-3)
  - [ ] Resolve the frozen stage/dataset/analysis plan explicitly; never choose “latest”.
  - [ ] Emit the standard command manifest with immutable inputs, outputs, protocol, runtime lock, and typed terminal status.
- [ ] Add query-sandbox, stratification, lineage, determinism, and mutation-denial tests (AC: 1-3).

## Developer Context

DuckDB is a replaceable read engine, not state. All authority remains in immutable Parquet and manifests. Separate data access from inference: query validation must prevent accidental pooling before an estimator sees rows. Every derived byte must be reproducible from a frozen ordered input set and a hashed derivation.

### Architecture and Version Guardrails

- Follow AD-03, AD-05, AD-07, AD-15, AD-17, AD-22, AD-24.
- Use Python `3.11`, DuckDB exactly `1.5.5`, and Story 5.1 PyArrow `25.0.0` schemas.
- Read reconciled terminal Parquet only. Analysis cannot import ledger mutation or concrete operational adapters.

### File Structure Requirements

- **NEW/UPDATE:** `evaluation/src/memrelay_eval/analysis/{queries,schemas}.py`
- **UPDATE:** `evaluation/src/memrelay_eval/cli/{main,commands}.py`
- **UPDATE:** `evaluation/src/memrelay_eval/domain/{errors,policies}.py`
- **NEW:** `evaluation/tests/contract/analysis/test_duckdb_read_only.py`
- **NEW:** `evaluation/tests/unit/analysis/test_stratification_guards.py`
- **NEW:** `evaluation/tests/golden/analysis/` for table/figure hashes
- **READ ONLY:** `evaluation/artifacts/parquet/`, SQLite, CAS source blobs, and source manifests.

### Existing Behavior to Preserve

- Preserve published Parquet bytes, lineage, units/nulls/order, retention, and access classification.
- Preserve all separation dimensions and highest-interference unit metadata.
- Preserve non-interactive, no-network, credential-free analysis.

### Testing Requirements

- Negative SQL tests cover comments/multi-statements and nested bypasses as well as DDL/DML, `COPY`, `ATTACH`, `PRAGMA`/`SET`/`CALL`, table functions, extension load/install, external paths/URLs, SQLite scans, and source overwrite.
- Cross-product tests reject every undeclared pooling dimension and accept only explicitly valid stratified outputs.
- Golden figures and tables reproduce exact hashes across reruns in the locked environment.

### Anti-Patterns

- Do not use DuckDB as a ledger, write beside source Parquet, select a mutable “latest” dataset, or accept caller SQL without validation.
- Do not bury conflicting strata in a group-by, flatten dynamic sequences, or infer missing dimensions.
- Do not implement effect estimates, Holm, power, safety gates, or claim wording here.

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` — Epic 5 / Story 5.2; FR48-FR49]
- [Source: `ARCHITECTURE-SPINE.md` — AD-05; Analysis convention; Capability Map]
- [Source: `IMPLEMENTATION-DESIGN.md` — §§4-6, 22.1, 24.1, 24.4]
- [Source: technical research — Parquet/DuckDB canonical evidence and report lineage]
- [Source: `_bmad-output/test-artifacts/test-design-qa.md` — `TOOL-PARQUET-*`, `REPRO-ANALYSIS`]
- [Source: predecessor Story 5.1 — immutable reconciled dataset contract]

## Dev Agent Record

### Agent Model Used

TBD by implementation agent

### Debug Log References

### Completion Notes List

- Ultimate context engine analysis completed - comprehensive developer guide created

### File List
