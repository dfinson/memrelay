# Story 5.1: Materialize Reconciled Terminal Parquet

Status: ready-for-dev

## Story

As a data engineer,
I want versioned Arrow schemas and Parquet datasets built only from reconciled terminal evidence,
So that confirmatory analysis has a typed immutable input boundary.

## Acceptance Criteria

1. **Given** reconciled included and excluded terminal records from Story 4.5  
   **When** materialization runs  
   **Then** PyArrow is exactly `25.0.0` and versioned schemas preserve IDs, assignment and analysis units, stratum, history mode, outcomes, failures, attrition, zero costs, unavailable values, evidence, and inclusion status  
   **And** no unreconciled operational row enters confirmatory tables.
2. **Given** two independent readers  
   **When** they read a dataset version  
   **Then** rows, Arrow types, nulls, units, and ordering keys agree exactly  
   **And** source manifest hashes, schema hash, protocol, population, endpoint, stratum, history mode, and materialization hash are attached.
3. **Given** rematerialization from identical reconciled inputs  
   **When** category and numeric outputs are compared  
   **Then** categories and counts match exactly and numeric values match within `1e-10` absolute or `1e-8` relative  
   **And** changed inputs produce a new dataset version rather than mutation.

## Dependencies and Prerequisites

- **Authoritative direct graph dependency:** Story 4.5 only, exactly as declared in `epics.md`.
- Story 4.5 supplies immutable reconciliation and inclusion decisions; Stories 4.1-4.4 are reached through that predecessor and remain the source authorities.
- Stories 4.6-4.7 are not direct graph dependencies, but their immutable quantity and selected monetary-view records are required before publishing an economics-capable dataset. Story 4.8 is not a dependency of this story.
- The `evaluation/` project is not present in the current tree; implement only after predecessor contracts establish the referenced modules and schemas.

## Tasks / Subtasks

- [ ] Define versioned Arrow schemas and canonical dataset keys (AC: 1, 2)
  - [ ] Preserve opaque experiment/run/attempt/assignment/task/history/sequence/repository/model/environment IDs and explicit assignment, experimental, resampling, clustering, observation, and analysis units.
  - [ ] Represent `zero`, `null`, and typed `unavailable` distinctly; retain failures, timeouts, attrition, exposure state, contamination flags, inclusion status/reason, and all assigned-unit denominator fields.
  - [ ] Pin field names, Arrow types, units, dictionaries/categories, nullability, ordering keys, schema version, and schema SHA-256.
- [ ] Build the reconciled-input resolver (AC: 1)
  - [ ] Accept only terminal Story 4.5 decisions and verified immutable source manifests; materialize an assigned-unit table containing every reconciled included/excluded decision and an eligible-outcome table containing only measurements authorized for confirmatory use.
  - [ ] Require downstream ITT construction to left-join eligible outcomes onto the complete assigned-unit denominator. Excluded outcomes never become valid endpoint measurements, but excluded assignments remain present as missing/excluded rows for attrition, missingness, bounds, and categorical decisions.
  - [ ] Reject absent/ambiguous reconciliation, authority conflict, corrupt hash, mutable path-only identity, or incomplete assignment lineage.
  - [ ] Freeze the exact ordered input-manifest set before writing and retain its canonical hash.
- [ ] Implement deterministic atomic Parquet materialization with PyArrow `25.0.0` (AC: 1-3)
  - [ ] Normalize timestamps, decimals, dictionary ordering, row groups, metadata, and row order without changing source meaning.
  - [ ] Write to a new staged dataset version, verify it with two independent readers, then atomically publish; never overwrite a published version.
  - [ ] Store dataset files and lineage manifests through the CAS/artifact authority.
- [ ] Emit immutable dataset and derivation manifests (AC: 2, 3)
  - [ ] Bind source hashes, schema hash, protocol, population, endpoint, stratum, history mode, environment/model strata, materializer/runtime lock, output hashes, units, and ordering contract.
  - [ ] Record typed success/failure and decision records for rejected rows or datasets; do not silently filter.
- [ ] Add schema, round-trip, determinism, corruption, and fail-closed tests (AC: 1-3)
  - [ ] Cover included/excluded, exposed/unexposed, retry lineage, failures, attrition, zero cost, unavailable usage, nulls, extreme numeric values, and changed input versioning.
  - [ ] Assert exact category/count equality and the required numeric tolerances.

## Developer Context

This story creates the only typed boundary that confirmatory analysis may consume. It does not estimate effects. The source set is immutable and frozen before materialization; a path is never authority. Preserve every assigned unit needed for complete ITT and later missingness, attrition, exposure, contamination, per-protocol sensitivity, and harm bounds. An excluded measurement cannot contribute as an observed confirmatory endpoint, but its assignment must remain in the ITT denominator and governed sensitivity inputs.

### Architecture and Version Guardrails

- Follow AD-03, AD-05, AD-07, AD-11, AD-15, AD-16, AD-17, AD-24.
- Use Python `3.13`, PyArrow exactly `25.0.0`, RFC 8785 canonical JSON, and lowercase SHA-256.
- Product/engine, controlled/dynamic, model, and changed-environment strata remain separate. Dynamic histories retain sequence-level units.
- Do not add evaluator dependencies to the root `pyproject.toml` or memrelay wheel.

### File Structure Requirements

- **NEW/UPDATE:** `evaluation/src/memrelay_eval/evidence/parquet.py`
- **NEW/UPDATE:** `evaluation/src/memrelay_eval/analysis/schemas.py`
- **UPDATE:** `evaluation/src/memrelay_eval/evidence/{manifest,required,reconcile}.py`
- **UPDATE:** `evaluation/src/memrelay_eval/domain/{entities,errors,policies}.py`
- **NEW/UPDATE:** versioned schemas under `evaluation/schemas/` and Parquet artifacts under `evaluation/artifacts/parquet/<dataset-version>/`
- **NEW:** unit/contract/golden tests under `evaluation/tests/{unit,contract,golden}/analysis/`
- **READ ONLY:** operational SQLite, source CAS blobs, prior reconciliation, quantity, price, and inclusion records.

### Existing Behavior to Preserve

- Preserve append-only lifecycle, CAS identity, sole-writer ledger ownership, provider/cost provenance, zero-versus-unavailable semantics, all attempts, and immutable inclusion decisions.
- Preserve the exact assignment and interference hierarchy; never flatten sequence/history/repository clusters into run independence.
- Preserve source classifications, retention, secret flags, and backup reachability.

### Testing Requirements

- Two-reader tests must independently compare rows, schema/types, nulls, units, categories, and ordering.
- Fault tests cover partial writes, duplicate rows, missing manifests, corrupt blobs, stale reconciliation, schema drift, incompatible units, and publication interruption.
- Boundary tests prove that no unreconciled row appears in either table, excluded measurements cannot enter eligible outcomes, and excluded assignments cannot disappear from the assigned-unit denominator.
- No-network tests prove materialization needs no provider credentials or operational database mutation.

### Anti-Patterns

- Do not query unreconciled SQLite directly, mutate source Parquet, coerce `unavailable` to zero/null, drop failed or attrited assignments, or publish partial datasets.
- Do not pool strata, infer treatment from IDs, hand-edit generated schema/lineage, or treat excluded rows as confirmatory.
- Do not implement DuckDB queries, estimators, thresholds, safety conclusions, or reports in this story.

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` — Epic 5 / Story 5.1; FR48-FR49]
- [Source: `ARCHITECTURE-SPINE.md` — AD-05, AD-15; Consistency Conventions; Stack]
- [Source: `IMPLEMENTATION-DESIGN.md` — §§5-6, 16, 22.1, 24.1, 24.4]
- [Source: technical research — Data Formats and Standards; reproducibility tolerances]
- [Source: `_bmad-output/test-artifacts/test-design-qa.md` — `TOOL-PARQUET-*`, `REPRO-ANALYSIS`, `TEL-MISSINGNESS`]
- [Source: predecessor Stories 4.5-4.8 — reconciliation, costs, revisions, and durable evidence]

## Dev Agent Record

### Agent Model Used

TBD by implementation agent

### Debug Log References

### Completion Notes List

- Ultimate context engine analysis completed - comprehensive developer guide created

### File List
