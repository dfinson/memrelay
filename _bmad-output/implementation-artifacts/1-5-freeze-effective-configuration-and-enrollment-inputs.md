# Story 1.5: Freeze Effective Configuration and Enrollment Inputs

Status: done

## Story

As a study operator,
I want all experimental inputs and effective configuration frozen before enrollment,
So that post-assignment changes cannot silently alter the protocol.

## Acceptance Criteria

1. **Given** CLI arguments, frozen protocol/stage values, evaluator configuration, and safe defaults  
   **When** effective configuration is resolved  
   **Then** precedence is exactly CLI, protocol/stage, evaluator file, then safe defaults  
   **And** environment variables are accepted only as credentials for their named target process.
2. **Given** a pre-enrollment plan  
   **When** it is sealed  
   **Then** catalog, protocol, environment fingerprint, native model catalog, assignment algorithm, seed commitment, blocks, ordered inputs, and price tables are canonicalized and hash-frozen  
   **And** credential values are replaced by structured redaction markers while variable names and target processes are retained.
3. **Given** an assignment has been created  
   **When** any frozen version, endpoint, threshold, stage rule, model selection, or configuration value changes  
   **Then** in-place mutation is rejected  
   **And** a new protocol or attempt with a new immutable configuration artifact is required.

## Dependencies and Prerequisites

- **Formal graph dependency:** Story 1.3.
- Story 1.3 must provide the shared canonicalizer/catalog lock; Story 1.1 provides artifact references and typed configuration failures.
- Story 1.4 is not a formal graph prerequisite, but its immutable eligibility disposition is mandatory before a selected task/history can become enrollment-eligible. This story may freeze fixture dispositions in tests; it must not bypass or duplicate Story 1.4 policy.
- Native model catalog and price records may be deterministic fixtures in unpaid conformance. Real observed catalogs/prices become durable/paid-eligible only after their later acquisition/conformance workflows.
- **Repository baseline:** configuration, freeze, block, environment, schema, CLI, and test UPDATE seams are created by earlier Epic 1 stories; `src/memrelay/config.py` is product code and is not an evaluator seam.

## Tasks / Subtasks

- [x] Implement explicit configuration models and resolver (AC: 1)
  - [x] Apply precedence exactly: CLI > frozen protocol/stage > evaluator file > safe defaults.
  - [x] Reject unknown/ambiguous keys, implicit environment overrides, and secret values in ordinary config.
  - [x] Permit environment references only as credential variable name + named target process; never read/persist a value in the control configuration.
- [x] Implement redacted effective configuration artifacts (AC: 1, 2)
  - [x] Replace credential values with structured markers retaining variable name and process.
  - [x] Canonicalize/hash with Story 1.3 and store through `ArtifactStorePort`.
  - [x] Include source/provenance for every effective field without leaking secrets.
- [x] Implement enrollment freeze (AC: 2)
  - [x] Freeze exact catalog/protocol/environment/model-catalog/assignment/seed/block/ordered-input/price-table identities.
  - [x] Capture environment fingerprint: OS/build, CPU, memory, storage class, power mode, Python/runtime, process limits, network policy, and background-load policy.
  - [x] Produce parity-hash inputs; do not compute treatment-specific parity here.
  - [x] Retain each frozen input's artifact identity, schema/version, digest, and provenance; never embed mutable file paths as the authority.
- [x] Enforce post-assignment immutability (AC: 3)
  - [x] Reject in-place mutation of every frozen input/value.
  - [x] Require new protocol identity or attempt/config artifact as applicable and preserve lineage.
- [x] Add resolver, redaction, canonicalization, fingerprint, mutation, and no-secret tests (AC: 1-3)

## Developer Context

Configuration is data with provenance, not ambient process state. Environment variables are a credential delivery mechanism only at a later named process boundary. The frozen plan contains commitments/hashes, not secrets or treatment labels. Real model IDs are intentionally not invented: later `list_models()` locking materializes them.

### Architecture Compliance

- Follow AD-17 and Implementation Design §21 exactly.
- Use Story 1.3's sole RFC 8785 canonicalizer and SHA-256.
- Changed environment fingerprint creates a separate environment stratum; never silently pool.
- IDs and ordinary artifacts remain treatment-neutral.
- No post-assignment mutation; append a new immutable artifact and lineage.
- Fake model catalog, price, environment, and artifact ports are unpaid conformance only and cannot authorize study inclusion.

### Library and Version Requirements

- Python 3.13; standard library TOML reader (`tomllib`) is preferred for evaluator configuration.
- Reuse the exact locked RFC 8785 implementation and artifact manifest `1.0.0`.
- Freeze known platform values without invoking them: Copilot SDK `1.0.8` and wheel digest, Inspect `0.3.252`, OTel `1.44.0`, Collector `0.158.0` and archive digest, OpenInference `0.1.31`, OpenAI instrumentation `0.1.53`, DuckDB `1.5.5`, PyArrow `25.0.0`, framework model `gpt-4.1-mini-2025-04-14`, and local embedder `BAAI/bge-small-en-v1.5`.
- Do not invent native Copilot model IDs; accept only an observed/fixture catalog artifact.

### Expected File Paths

- **NEW:** `evaluation/src/memrelay_eval/domain/environment.py`
- **NEW:** `evaluation/src/memrelay_eval/orchestration/configuration.py`
- **NEW:** `evaluation/src/memrelay_eval/orchestration/freeze.py`
- **NEW:** `evaluation/src/memrelay_eval/orchestration/blocks.py`
- **UPDATE:** `evaluation/src/memrelay_eval/domain/{entities,errors,policies}.py`
- **NEW/UPDATE:** `evaluation/schemas/effective-config.schema.json`
- **UPDATE:** `evaluation/src/memrelay_eval/cli/{main,commands}.py`
- **NEW:** `evaluation/tests/unit/orchestration/test_configuration.py`
- **NEW:** `evaluation/tests/unit/orchestration/test_freeze.py`
- **NEW:** `evaluation/tests/contract/test_effective_config_redaction.py`

### Existing Behavior to Preserve

- Catalog locks/eligibility records remain immutable and byte-verifiable.
- Story 1.1 fake adapter eligibility denial.
- Product configuration loading and `src/memrelay/config.py` are not changed.
- No secret value enters manifests, logs, exceptions, telemetry, workspace, or generated catalog artifacts.

### Testing Requirements

- Full precedence table including absent, conflicting, unknown, and invalid values.
- Environment tests prove noncredential variables do not override configuration and credential values are never persisted/read by the resolver.
- Canary secret scan across effective config bytes, logs, telemetry, exceptions, and command manifests.
- Repeat freeze produces byte-identical artifacts for identical inputs; reordered ordered inputs change identity.
- Mutation tests cover every field class in AC3.
- Environment fingerprint tests cover all required dimensions and force new stratum identity on change.
- No-network and fixture-only model/price tests.

### Anti-Patterns

- Do not use environment as a general configuration layer.
- Do not persist secrets, even encrypted or hashed.
- Do not substitute a public model list or guessed model ID.
- Do not mutate an existing plan/config artifact after assignment.
- Do not let fixture/fake catalogs or prices authorize paid enrollment.
- Do not implement assignment resolution, workers, provider discovery, or price acquisition.

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` — “Story 1.5”]
- [Source: `ARCHITECTURE-SPINE.md` — AD-17, AD-19, AD-24]
- [Source: `IMPLEMENTATION-DESIGN.md` — §§13, 15.4, 21, 24.1]
- [Source: TEA handoff — “Known/External Dependencies”; model/catalog gate guidance]
- [Source: `test-design-qa.md` — `OPS-PROTOCOL-FREEZE`, `MODEL-CATALOG-SNAPSHOT`, `ARM-PARITY`]

## Dev Agent Record

### Agent Model Used

GitHub Copilot CLI

### Debug Log References

`py -3.13 -m pytest tests` from `evaluation\` with `PYTHONPATH=evaluation\src` — 608 passed, 4 platform-specific skips.

`py -3.12 -m pytest` from the repository root with `PYTHONPATH=src` — 1305 passed, 2 optional-backend skips.

`py -3.13 -m ruff check evaluation/src evaluation/tests` — passed.

### Completion Notes List

- Ultimate context engine analysis completed - comprehensive developer guide created
- Added explicit non-secret configuration resolution, TOML loading, credential-reference redaction, and canonical effective-configuration artifacts.
- Added environment fingerprint strata, environment-bound blocks, immutable enrollment-plan freezing, input-only parity hashing, assignment mutation guards, and successor lineage checks.
- Preserved Story 1.4 by consuming only its immutable eligible disposition rather than re-evaluating eligibility.
- Fixture-backed artifact stores remain marked as unpaid conformance and cannot authorize study inclusion.

### File List

- `evaluation/src/memrelay_eval/domain/{environment.py,entities.py,errors.py,ids.py,policies.py}`
- `evaluation/src/memrelay_eval/orchestration/{configuration.py,freeze.py,blocks.py}`
- `evaluation/src/memrelay_eval/cli/{commands.py,main.py}`
- `evaluation/schemas/effective-config.schema.json`
- `evaluation/tests/unit/orchestration/{test_configuration.py,test_freeze.py}`
- `evaluation/tests/contract/test_effective_config_redaction.py`
- `evaluation/README.md`
