# Story 1.4: Govern Fixtures, Traceability, and Task Eligibility

Status: review

## Story

As a study reviewer,
I want every fixture and priority scenario linked to governed evidence and claims,
So that unauthorized data and untraceable tasks cannot enter a study.

## Acceptance Criteria

1. **Given** a fixture reference  
   **When** it is compiled  
   **Then** its opaque ID, relative path, SHA-256, media type, revision, license, provenance, extraction path, classification, and redistribution policy are verified  
   **And** a missing, escaping, changed, or unauthorized fixture fails compilation.
2. **Given** a P0 or P1 scenario  
   **When** traceability is validated  
   **Then** valid risk, gate, endpoint, expected-evidence, and claim IDs resolve with no orphan  
   **And** the generated traceability map retains source locations and hashes.
3. **Given** an initial-study task or history  
   **When** eligibility is evaluated  
   **Then** only synthetic or license-audited public data are accepted  
   **And** private histories, personal data, proprietary repositories, real credentials, and unauthorized inputs are rejected with a task disposition.
4. **Given** any candidate task or history governed by FR52  
   **When** study eligibility is reviewed  
   **Then** a memory-necessity review and shortcut audit show that the intended memory capability, rather than identifiers, formatting artifacts, repository clues, or other shortcuts, is necessary for success  
   **And** contamination and canary checks, development/pilot/confirmatory holdout separation, and baseline and gold-grader stability checks must pass before an immutable eligible or rejected task disposition is recorded with its evidence and reasons.

## Dependencies and Prerequisites

- **Formal graph dependency:** Story 1.3.
- Story 1.3 must provide the compiler, canonicalizer, traceability output, and catalog lock.
- Story 1.2 typed/source-located validation.
- No candidate becomes eligible merely because schema/compilation succeeds. All AC4 review evidence is mandatory.
- Cross-repository/private/proprietary inputs remain mechanically denied; later governance cannot be anticipated here.
- **Repository baseline:** fixture, compiler, generated-manifest, and evaluator-test paths are dependency-produced seams. Existing root `tests/eval/` fixtures remain product regression evidence and must not be copied, relabeled, or treated as eligible study assets without this story's full disposition.

## Tasks / Subtasks

- [x] Verify fixture manifests inside the catalog root (AC: 1)
  - [x] Resolve relative paths without following escapes/symlinks outside the governed root.
  - [x] Verify exact bytes, lowercase SHA-256, media type, revision, license, provenance, extraction path, classification, and redistribution policy.
  - [x] Return typed disposition/failure for missing, changed, escaping, unauthorized, or unredistributable inputs.
- [x] Close P0/P1 traceability (AC: 2)
  - [x] Resolve every risk, gate, endpoint, expected-evidence, and claim reference with no orphan.
  - [x] Resolve and hash every referenced fixture and expected-evidence definition required by the catalog/TEA handoff; a named evidence class without a governed definition is unresolved.
  - [x] Preserve source location and source/generated hashes.
  - [x] Generate from catalog data; never hand-author mapping rows.
- [x] Implement initial data-eligibility policy (AC: 3)
  - [x] Accept only synthetic or explicitly license-audited public data.
  - [x] Detect/reject private history, personal data, proprietary repositories, credential material, and unauthorized input.
  - [x] Produce immutable eligible/rejected dispositions with codes, evidence refs, reviewer role, and canonical hash.
- [x] Implement study-validity review records and gates (AC: 4)
  - [x] Record memory-necessity and shortcut audits.
  - [x] Record contamination search and unique canary results.
  - [x] Enforce development/pilot/confirmatory holdout separation.
  - [x] Require frozen baseline and gold grader stability evidence before `eligible`.
- [x] Integrate gates into compile publication and add fault/golden tests (AC: 1-4)

## Developer Context

This story governs eligibility, not task execution. “Public” without a license/provenance audit is insufficient. A valid fixture can still be scientifically ineligible because shortcuts, contamination, unstable graders, or holdout leakage invalidate causal interpretation. Dispositions are immutable evidence; changed inputs create new identities/dispositions.

### Architecture Compliance

- Initial evidence is synthetic or license-audited public only (AD-23).
- Artifact identity uses Story 1.3 canonical bytes/SHA-256; do not add another hashing serializer.
- Paths are relative, normalized, contained, and verified on read.
- Traceability is generated and referentially closed.
- The catalog remains provider-free and cannot query live repositories/services.
- Deterministic fixture and eligibility checks are unpaid conformance; eligibility is a pre-enrollment gate, not proof of efficacy, safety, durable evidence, or paid-run inclusion.

### Library and Version Requirements

- Python 3.13; stdlib path, MIME, hashing, and immutable record facilities where sufficient.
- Reuse the exact locked YAML/schema and RFC 8785 implementations from Stories 1.2-1.3.
- No new provider, licensing service, secret scanner SaaS, benchmark runner, or network dependency.
- Grader stability here consumes governed evidence records; implementation of the grader process belongs to Epic 3.

### Expected File Paths

- **NEW:** `evaluation/src/memrelay_eval/catalog/fixtures.py`
- **NEW:** `evaluation/src/memrelay_eval/catalog/eligibility.py`
- **UPDATE:** `evaluation/src/memrelay_eval/catalog/{validation,compiler,traceability}.py`
- **UPDATE:** `evaluation/schemas/{scenario,traceability}.schema.json`
- **NEW/UPDATE:** `evaluation/catalog/fixtures/`
- **UPDATE:** `evaluation/catalog/generated/{fixture-manifest,traceability}.json`
- **NEW:** `evaluation/tests/unit/catalog/test_fixtures.py`
- **NEW:** `evaluation/tests/unit/catalog/test_eligibility.py`
- **NEW:** `evaluation/tests/contract/catalog/test_traceability.py`
- **NEW:** `evaluation/tests/golden/eligibility/`

### Existing Behavior to Preserve

- Atomic compile publication and prior-lock preservation from Story 1.3.
- Exact source-located diagnostics and version policy from Story 1.2.
- Domain purity and fake-adapter paid-ineligibility from Story 1.1.
- Existing product fixtures/tests remain bounded regression evidence; do not relabel them as evaluator efficacy evidence.

### Testing Requirements

- Fixture matrix: valid, missing, byte-changed, wrong hash/media/revision, absolute path, `..` escape, symlink escape, license/provenance absent, prohibited classification, redistribution denial.
- Run containment cases with Windows separators, drive-qualified/UNC paths, case variation, symlinks, and reparse-point escapes.
- Traceability tests for every P0/P1 namespace, orphan, duplicate, source location, and regeneration identity.
- Eligibility tests for synthetic/public-approved and every prohibited class in AC3.
- Scientific-validity tests for necessity failure, shortcut, canary hit, holdout overlap, baseline instability, and gold instability.
- Verify changed fixture bytes generate a new identity and cannot reuse an earlier eligible disposition.
- No-network/no-provider test.

### Anti-Patterns

- Do not equate hash validity with license, authorization, or scientific eligibility.
- Do not trust path normalization without resolved-root containment checks.
- Do not hand-edit traceability or dispositions.
- Do not accept private/proprietary data pending future approval.
- Do not implement graders, pilot enrollment, cross-repository authorization, or efficacy reporting.

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` — “Story 1.4”]
- [Source: `ARCHITECTURE-SPINE.md` — AD-10, AD-23]
- [Source: `IMPLEMENTATION-DESIGN.md` — §§9.2-9.3, 22.1]
- [Source: technical research — “Scenario Catalog Dependency Rule”; task/holdout governance]
- [Source: TEA handoff — “Quality gates”; risk mapping R-001, R-011, R-028]
- [Source: `test-design-qa.md` — `TASK-NECESSITY`, `TASK-SHORTCUT`, `CONTAM-*`, `GRADE-BASELINE-STABILITY`, `GRADE-GOLD-STABILITY`]

## Dev Agent Record

### Agent Model Used

Direct senior-developer implementation (BMAD `bmad-dev-story` workflow tooling was confirmed absent from this worktree/installation; work followed this story markdown and the linked architecture artifacts directly, honestly disclosed rather than fabricating a workflow invocation).

### Debug Log References

- Isolated Python 3.13.5 venv at `evaluation/.venv` (created via `uv`, routed through the corporate package mirror) used for all test execution; this worktree's `src` was placed on `PYTHONPATH` directly rather than an editable pip install, per repository-specific instruction (a sibling worktree may hold the editable install target).
- `evaluation/catalog/generated/*.json`, `catalog-lock.json`, and `compile-manifest.json` were regenerated via `memrelay-eval compile-catalog` (no `--prior-lock`, since `catalog_version` was not bumped) after editing `catalog.yaml`.
- Full-suite runs (`pytest tests`) executed three times; the only failure observed across all runs was the pre-existing, out-of-scope `test_evaluator_has_one_shared_jcs_implementation_and_catalog_import_graph` (flags 4 `json.dumps` findings in `adapters/workspace/base.py`, last touched in merged PR #187, unrelated to this story). One additional intermittent failure (`test_ambient_stale_lock_does_not_change_compilation_identity`) was observed on a single run under the combined catalog+fault suite and did not reproduce on two subsequent full-suite runs; it is a Windows advisory-file-locking timing flake in pre-existing Story 1.3 test infrastructure, not a regression introduced by this story (confirmed by running it alone and by running the full contract/catalog suite alone, both green).

### Completion Notes List

- Implemented `fixtures.py` (AC1 hard gate): resolves fixture paths against the catalog root with symlink/`..`/absolute/drive/UNC escape containment, verifies exact SHA-256/media type/revision/license/provenance/extraction path/classification/redistribution policy, and raises a compile-blocking `FixtureVerificationError` on any violation so an invalid fixture fails the entire catalog compile closed (proven at the full `compile_catalog_command` level in `tests/fault/test_catalog_governance_faults.py`).
- Implemented `eligibility.py` (AC3/AC4 soft, immutable disposition): never raises; always returns a full `eligible`/`rejected` disposition with failure codes, evidence refs, reviewer role, and a canonical digest, so a scientifically ineligible scenario still allows the catalog to compile while recording the rejection (also proven at the full-compile level in the same fault-test file).
- Wired both gates into `compiler.py`'s `_compiled_documents()`/`_traceability_records()`, replacing all placeholder `"not_performed"` fixture/eligibility fields in `tasks.json`, `fixture-manifest.json`, and `traceability.json` with real computed values (identical `eligibility_evaluation` object embedded in both `tasks.json` and `traceability.json`).
- Added `study_validity_ref`/`study_validity` as a new closed reference namespace in `validation.py`/`catalog.yaml` (mirroring the existing `grader_ref` pattern) to carry the AC4 memory-necessity, shortcut-audit, contamination/canary, holdout, and baseline/gold-stability review records referenced by eligibility.
- Regenerated `evaluation/catalog/catalog.yaml` with a real fixture (`catalog-validation.txt`, checked-in SHA-256) and a fully-passing `study_validity` record; added `evaluation/catalog/fixtures/.gitattributes` (`* -text`) so the fixture's bytes/hash are stable across Windows/Linux checkouts (`core.autocrlf` was otherwise a live hazard for this story's hash verification).
- Test coverage added: `test_fixtures.py` (44 cases, full AC1 matrix including Windows-separator/drive/UNC/`..` containment; symlink-escape case is present but environment-skipped — see Blockers/Deviations), `test_eligibility.py` (full AC3/AC4 matrix including every failure code and disposition-identity-changes-with-fixture-bytes behavior), `test_traceability.py` (AC2 contract tests: namespace closure with source locations, orphan-reference compile failure, stable locations, byte-identical regeneration, generated-not-hand-authored proof), `test_eligibility_golden.py` + `tests/golden/eligibility/{eligible-synthetic,rejected-multiple-failures}.{input,expected}.json` (golden-stable disposition bytes), and `tests/fault/test_catalog_governance_faults.py` (5 full-`compile_catalog_command`-level fault tests proving the AC1 hard-gate/AC3-AC4 soft-gate split, and the changed-fixture-bytes/new-disposition-identity requirement, end-to-end).
- Existing `test_compiler_determinism.py` and `test_validation.py` were updated only to reflect the new real (non-placeholder) fixture/eligibility values and the new `study_validity` schema shape; no behavioral assertion about Story 1.3's atomic-publish/version-policy/recovery guarantees was weakened or removed.
- Full evaluation test suite (`pytest tests`, 512 collected) run three times: 503 passed, 3 skipped (this environment cannot create symlinks/reparse points; both the symlink-escape fixture case and two pre-existing workspace-ownership reparse-point tests skip cleanly rather than fail), 1 consistently-failing pre-existing unrelated test.

### Deviations from Expected File Paths (disclosed)

- `evaluation/src/memrelay_eval/catalog/traceability.py` was **not created**; traceability generation lives in `compiler.py`'s `_traceability_records()`/`_compiled_documents()`, alongside the other three generated-document builders (`tasks`, `assignment-inputs`, `fixture-manifest`). Splitting only traceability into its own module would be inconsistent with how the other three generated documents are built and was not required by any test or architecture-boundary check.
- `evaluation/schemas/traceability.schema.json` was **not created**. No other generated document (`tasks.json`, `assignment-inputs.json`, `fixture-manifest.json`) has a dedicated JSON Schema file wired into any validation code path in this repository; adding one only for traceability would be inconsistent with that convention and would not be enforced anywhere. `traceability.json`'s shape is instead pinned by the new `test_traceability.py` contract tests and the existing determinism/golden tests.

### File List

**New:**
- `evaluation/src/memrelay_eval/catalog/fixtures.py`
- `evaluation/src/memrelay_eval/catalog/eligibility.py`
- `evaluation/catalog/fixtures/catalog-validation.txt`
- `evaluation/catalog/fixtures/.gitattributes`
- `evaluation/tests/unit/catalog/test_fixtures.py`
- `evaluation/tests/unit/catalog/test_eligibility.py`
- `evaluation/tests/unit/catalog/test_eligibility_golden.py`
- `evaluation/tests/contract/catalog/test_traceability.py`
- `evaluation/tests/fault/test_catalog_governance_faults.py`
- `evaluation/tests/golden/eligibility/eligible-synthetic.input.json`
- `evaluation/tests/golden/eligibility/eligible-synthetic.expected.json`
- `evaluation/tests/golden/eligibility/rejected-multiple-failures.input.json`
- `evaluation/tests/golden/eligibility/rejected-multiple-failures.expected.json`

**Updated:**
- `evaluation/src/memrelay_eval/catalog/compiler.py`
- `evaluation/src/memrelay_eval/catalog/validation.py`
- `evaluation/schemas/scenario.schema.json`
- `evaluation/catalog/catalog.yaml`
- `evaluation/catalog/generated/tasks.json`
- `evaluation/catalog/generated/assignment-inputs.json`
- `evaluation/catalog/generated/fixture-manifest.json`
- `evaluation/catalog/generated/traceability.json`
- `evaluation/catalog/catalog-lock.json`
- `evaluation/catalog/compile-manifest.json`
- `evaluation/tests/contract/catalog/test_compiler_determinism.py`
- `evaluation/tests/unit/catalog/test_validation.py`
- `_bmad-output/implementation-artifacts/1-4-govern-fixtures-traceability-and-task-eligibility.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (Story 1.4 line only)
