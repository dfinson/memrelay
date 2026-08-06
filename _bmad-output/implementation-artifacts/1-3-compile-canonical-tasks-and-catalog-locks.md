# Story 1.3: Compile Canonical Tasks and Catalog Locks

Status: review

## Story

As an evaluator engineer,
I want valid catalog YAML compiled deterministically into canonical execution inputs,
So that every downstream plan is bound to byte-identical, hash-addressed inputs.

## Acceptance Criteria

1. **Given** the same valid catalog bytes and referenced inputs  
   **When** `memrelay-eval compile-catalog` runs repeatedly  
   **Then** one shared RFC 8785 canonicalizer emits byte-identical task inputs, opaque assignment inputs, fixture manifests, and traceability maps  
   **And** every identity uses SHA-256 over canonical JSON with the digest field omitted during digest calculation.
2. **Given** generated output or `catalog-lock.json`  
   **When** a byte is manually changed or an independent package attempts a second identity canonicalizer  
   **Then** CI detects and rejects the mismatch  
   **And** catalog code imports neither Inspect nor the Copilot SDK and makes no live provider call.
3. **Given** a successful or failed compile command  
   **When** it terminates  
   **Then** it writes a command manifest containing input and output hashes, runtime-lock reference, protocol ID, and typed terminal status  
   **And** failure leaves no artifact that can be mistaken for a valid lock.

## Dependencies and Prerequisites

- **Formal graph dependency:** Story 1.2.
- Story 1.2 must provide the valid catalog/schema, semantic validation, diagnostics, and version policy.
- Story 1.1 domain `ArtifactRef`, manifest contract, CLI composition, and unpaid fake artifact/telemetry paths.
- Fixture content validation and task eligibility are completed in Story 1.4; this compiler must expose their required seams without claiming those gates passed.
- **Repository baseline:** all `evaluation/` UPDATE seams are produced by Stories 1.1-1.2; the current checkout has no evaluator package or generated catalog tree.

## Tasks / Subtasks

- [ ] Implement the only identity canonicalizer (AC: 1, 2)
  - [ ] Emit UTF-8 RFC 8785 bytes; reject non-finite numbers and unsupported values.
  - [ ] Remove only the declared digest field from the identity projection, SHA-256 the canonical bytes, then attach the digest.
  - [ ] Expose one importable API and add an architectural test forbidding competing canonicalization/hashing paths.
- [ ] Implement deterministic catalog compilation (AC: 1)
  - [ ] Validate before compile and project canonical task inputs without importing Inspect types.
  - [ ] Emit opaque assignment inputs, fixture manifest inputs, traceability maps with source locations, and catalog lock.
  - [ ] Sort only where the contract defines set semantics; preserve authored ordering where order is meaningful.
  - [ ] Normalize source locations to stable catalog-root-relative coordinates; never place checkout roots, usernames, or host-specific separators in identity-bearing output.
- [ ] Make output publication atomic and verifiable (AC: 1-3)
  - [ ] Build in a same-filesystem sibling staging directory, verify all bytes/hashes, then publish the complete directory/set with replace semantics that are tested on Windows.
  - [ ] On failure, retain the prior valid lock unchanged and leave no partial/valid-looking output.
  - [ ] Recompute generated outputs in CI and fail on byte divergence or hand edits.
- [ ] Emit per-command manifests on success, failure, and interruption (AC: 3)
  - [ ] Include canonical input/output hashes, runtime-lock reference, protocol ID, typed terminal status, schema versions, and generator version.
  - [ ] Label output `unpaid_conformance`; it is not enrollment or efficacy evidence.
- [ ] Add determinism, independent-vector, tamper, architecture-boundary, and no-network tests (AC: 1-3)

## Developer Context

Identity is over bytes, not Python objects. RFC 8785/JCS differs from `json.dumps(sort_keys=True)`, especially for number serialization; no local convenience serializer may establish identity. Generated task inputs are domain-owned records that a later Inspect adapter translates at its boundary.

### Architecture Compliance

- One shared canonicalizer supplies identity bytes to catalog, future ledger, evidence, and analysis.
- JSON is UTF-8, RFC 8785, explicit schema-versioned, and forbids NaN/Infinity.
- Generated outputs are never hand-edited.
- Catalog imports domain only; no Inspect/Copilot/live-provider dependency.
- Command behavior is non-interactive, fail-closed, atomic, and typed.
- Deterministic compilation/fakes remain unpaid conformance. A catalog lock is necessary but not sufficient for durable/paid enrollment.

### Library and Version Requirements

- Python 3.13 and the Story 1.1 lock.
- RFC 8785 is the exact canonicalization standard; SHA-256 comes from Python `hashlib`.
- The architecture does not ratify a Python RFC 8785 package/version. If a package is used, pin it exactly in `evaluation/uv.lock`, wrap it behind `catalog/canonical.py`, verify against independent RFC vectors, and forbid any second implementation.
- No `github-copilot-sdk`, Inspect, OpenAI, OTel, DuckDB, or PyArrow call is permitted.

### Expected File Paths

- **NEW:** `evaluation/src/memrelay_eval/catalog/canonical.py`
- **NEW/UPDATE:** `evaluation/src/memrelay_eval/catalog/{compiler,traceability}.py`
- **UPDATE:** `evaluation/src/memrelay_eval/cli/commands.py`
- **NEW:** `evaluation/catalog/generated/{tasks,assignment-inputs,fixture-manifest,traceability}.json`
- **NEW:** `evaluation/catalog/catalog-lock.json`
- **NEW:** `evaluation/tests/unit/catalog/test_canonical.py`
- **NEW:** `evaluation/tests/contract/catalog/test_compiler_determinism.py`
- **NEW:** `evaluation/tests/contract/catalog/test_generated_divergence.py`

### Existing Behavior to Preserve

- Story 1.2 validation and no-write-on-invalid behavior.
- Prior valid generated set/lock survives a failed or interrupted compile.
- Product code/package remains untouched.
- Story 1.1 fake adapters cannot confer paid/study eligibility.

### Testing Requirements

- Run compile repeatedly across clean directories/processes and compare every byte and hash.
- Validate RFC 8785 independent vectors, Unicode/number boundaries, digest omission, and lower-case SHA-256.
- Tamper each generated artifact and lock; regeneration/CI must fail closed.
- Scan/import graph to prove a single canonicalizer and no Inspect/Copilot imports.
- Simulate interruption between staging and publish; no partial valid lock may appear.
- Test Windows path/separator normalization and same-volume publication; temporary paths must not enter generated bytes.
- Assert success/failure/interruption command manifests are complete and redacted.
- Execute all tests with network denied and no provider credentials.

### Anti-Patterns

- Do not use `json.dumps(sort_keys=True)` or duplicate canonicalization.
- Do not hash a document containing its own digest.
- Do not emit Inspect SDK objects or human-readable treatment names.
- Do not overwrite a good lock before all outputs verify.
- Do not call generated planning evidence a study result or implement Story 1.4 governance.

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` — “Story 1.3”]
- [Source: `ARCHITECTURE-SPINE.md` — AD-10, consistency conventions]
- [Source: `IMPLEMENTATION-DESIGN.md` — §§9.3, 22.1, 24.4]
- [Source: technical research — “Scenario Catalog Dependency Rule”]
- [Source: TEA handoff — “Machine-readable catalog acceptance criteria”; “Quality gates”]
- [Source: `test-design-qa.md` — `CATALOG-HASHES`, `CATALOG-MAPPINGS`, `CATALOG-CI`, R-028]

## Dev Agent Record

### Agent Model Used

GPT-5.6 Terra (gpt-5.6-terra)

### Debug Log References

- 2026-08-05T19:57:47-04:00 - `uv run pytest` in `evaluation/`: 202 passed under CPython 3.13.14.
- 2026-08-05T19:57:47-04:00 - Focused canonical/compiler contract suite: 35 passed.
- 2026-08-05T19:57:47-04:00 - Root `ruff check .`, `ruff format --check .`, and `git diff --check`: passed.
- 2026-08-05T19:57:47-04:00 - Product regressions with this worktree's `src` on `PYTHONPATH`: 49 passed.

### Completion Notes List

- Added the sole RFC 8785/JCS identity-byte wrapper, exactly pinned to `rfc8785==0.1.4`.
- Compiled validated catalog YAML to canonical tasks, opaque assignment inputs, fixture manifest inputs, catalog-root-relative traceability, and a digest-verified catalog lock.
- Published the complete catalog root through same-volume sibling staging with verified output hashes and rollback-safe Windows directory replacement.
- Added redacted typed command manifests for successful, failed, and interrupted compiles; fixture content validation and eligibility remain explicitly not performed.
- Added deterministic clean-process, RFC vector, Unicode/number boundary, digest omission, tamper, no-network, import-boundary, interruption, Windows-path, same-volume, and manifest contract coverage.

### File List

- `evaluation/pyproject.toml`
- `evaluation/uv.lock`
- `evaluation/catalog/catalog-lock.json`
- `evaluation/catalog/compile-manifest.json`
- `evaluation/catalog/generated/tasks.json`
- `evaluation/catalog/generated/assignment-inputs.json`
- `evaluation/catalog/generated/fixture-manifest.json`
- `evaluation/catalog/generated/traceability.json`
- `evaluation/src/memrelay_eval/catalog/__init__.py`
- `evaluation/src/memrelay_eval/catalog/canonical.py`
- `evaluation/src/memrelay_eval/catalog/compiler.py`
- `evaluation/src/memrelay_eval/catalog/validation.py`
- `evaluation/src/memrelay_eval/cli/commands.py`
- `evaluation/src/memrelay_eval/cli/main.py`
- `evaluation/tests/unit/catalog/test_canonical.py`
- `evaluation/tests/contract/catalog/test_compiler_determinism.py`
- `evaluation/tests/contract/catalog/test_generated_divergence.py`
