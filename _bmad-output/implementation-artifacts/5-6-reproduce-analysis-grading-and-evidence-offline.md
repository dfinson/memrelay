# Story 5.6: Reproduce Analysis, Grading, and Evidence Offline

Status: ready-for-dev

## Story

As an independent reviewer,
I want network-off deterministic replay and clearly separated stochastic reruns,
So that published tables and evidence can be independently verified.

## Acceptance Criteria

1. **Given** a retained evidence bundle  
   **When** network-off replay runs  
   **Then** analysis categories/counts reproduce exactly, numeric values meet `1e-10` absolute or `1e-8` relative tolerance, and figure hashes match exactly  
   **And** no provider credential or network call is available.
2. **Given** grader and normalized evidence inputs  
   **When** they are replayed  
   **Then** grader binary/test outcomes match exactly, continuous scores meet `1e-6`, and canonical timestamp/path-normalized evidence hashes match  
   **And** mismatches identify the source and derivation hashes.
3. **Given** a requested stochastic rerun or independent replication  
   **When** it executes  
   **Then** it receives a new protocol/run identity and is reported separately from deterministic reproduction  
   **And** it cannot overwrite or backfill the original confirmatory result.

## Dependencies and Prerequisites

- **Authoritative direct graph dependencies:** Stories 4.8, 5.2, 5.4, and 5.5 only, exactly as declared in `epics.md`.
- Replay consumes immutable report inputs, runtime/dependency locks, source/derivation manifests, graders, and figures; it never resolves mutable “latest” inputs.
- Stochastic rerun/replication requires ordinary governed execution and is not part of network-off deterministic replay.

## Tasks / Subtasks

- [ ] Define a sealed reproduction bundle and inventory (AC: 1, 2)
  - [ ] Include exact assigned-unit and eligible-outcome Parquet/schema/source hashes, derivation registry/SQL/code, protocol/claim decisions, runtime/lock, seeds, figure toolchain/fonts, grader/snapshot/dependency hashes, normalization policy, and expected outputs.
  - [ ] Pin the executable/OS/environment identity or a reproducible image/lock plus every local wheel/model/font needed for replay; the bundle must not depend on a mutable package index or ambient cache.
  - [ ] Resolve exclusively from verified CAS/backup evidence and reject missing, changed, unauthorized, or mutable path-only inputs.
- [ ] Implement credential-free network denial and preflight (AC: 1)
  - [ ] Launch analysis/grader replay from a minimal environment with no Copilot/OpenAI/provider credentials.
  - [ ] Deny DNS/socket/provider access and prove no dependency/model/extension download or remote fallback occurs.
- [ ] Implement deterministic analysis replay (AC: 1)
  - [ ] Rebuild derived tables, Holm/simultaneous bounds, power/decision records, safety/audit outputs, and figures from immutable inputs.
  - [ ] Compare categories/counts exactly, numerics with `1e-10` absolute or `1e-8` relative tolerance, and figure bytes/hashes exactly.
  - [ ] Preserve missingness, attrition, exposure, contamination, and all decision records; do not recompute with changed thresholds.
- [ ] Implement grader and evidence replay (AC: 2)
  - [ ] Re-run the identical frozen artifact/grader contract; compare binary/test sets exactly and continuous scores within `1e-6`, excluding timing.
  - [ ] Rebuild normalized evidence after canonical timestamp/path normalization and require exact hashes.
  - [ ] Emit a mismatch tree identifying earliest divergent source hash, derivation hash, environment/version, field, and downstream impact.
- [ ] Separate stochastic rerun and independent replication workflows (AC: 3)
  - [ ] Allocate new protocol/run/attempt/evidence identities and immutable lineage; never write into original output locations.
  - [ ] Label conclusion class (null/harm/indeterminate/positive) separately and prevent reruns from backfilling original missingness or claim gates.
- [ ] Emit immutable reproduction comparison/decision records (AC: 1-3).
- [ ] Add no-network, tamper, mismatch-localization, tolerance, and identity-separation tests (AC: 1-3).

## Developer Context

There are three distinct claims: deterministic analysis/evidence replay, deterministic grader reproduction, and stochastic/independent replication. Do not conflate them. Immutable report inputs include the frozen thresholds and decision records; a replay verifies the original computation rather than applying current code/config opportunistically.

### Architecture and Version Guardrails

- Follow AD-05, AD-09, AD-15, AD-17, AD-22, AD-25.
- Use locked Python `3.13`, DuckDB `1.5.5`, PyArrow `25.0.0`, and the exact frozen analysis/grader dependencies.
- Network-off analysis, graders, and evidence processes receive no provider credentials.

### File Structure Requirements

- **NEW:** `evaluation/src/memrelay_eval/analysis/replay.py`
- **UPDATE:** `evaluation/src/memrelay_eval/analysis/{queries,reports}.py`
- **UPDATE:** `evaluation/src/memrelay_eval/evidence/{backup,manifest}.py`
- **UPDATE:** `evaluation/src/memrelay_eval/cli/{main,commands}.py`
- **NEW/UPDATE:** reproduction bundle/comparison schemas under `evaluation/schemas/`
- **NEW:** tests under `evaluation/tests/{integration,fault,golden}/reproduction/`

### Existing Behavior to Preserve

- Preserve original evidence, reports, claims, failures, missingness, exclusions, and all source/derivation hashes.
- Preserve access classifications and protected grader inputs during packaging and replay.
- Preserve the distinction between successful reproduction and favorable scientific conclusion.

### Testing Requirements

- Network tests fail on any socket/DNS/download attempt and assert empty credential allowlists.
- Golden tests cover exact/tolerance boundaries, row/order changes, environment drift, source corruption, derivation changes, and figure metadata drift.
- Identity tests prove stochastic reruns cannot share original protocol/run IDs or mutate original manifests.

### Anti-Patterns

- Do not pip-install/download during replay, use live prices/models, choose latest inputs, relax tolerances, or normalize away substantive differences.
- Do not treat a stochastic rerun as deterministic replay, overwrite original outputs, or use replication to repair an original claim.
- Do not expose protected artifacts in mismatch diagnostics.

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` — Epic 5 / Story 5.6; FR53]
- [Source: `ARCHITECTURE-SPINE.md` — AD-05, AD-09, AD-22, AD-25]
- [Source: `IMPLEMENTATION-DESIGN.md` — §§16, 22.1, 24.4-25]
- [Source: technical research — reproducibility claims and tolerances]
- [Source: `_bmad-output/test-artifacts/test-design-qa.md` — `REPRO-*`]
- [Source: predecessor Stories 4.8 and 5.2-5.5]

## Dev Agent Record

### Agent Model Used

TBD by implementation agent

### Debug Log References

### Completion Notes List

- Ultimate context engine analysis completed - comprehensive developer guide created

### File List
