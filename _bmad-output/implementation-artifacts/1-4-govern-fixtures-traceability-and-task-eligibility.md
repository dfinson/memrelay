# Story 1.4: Govern Fixtures, Traceability, and Task Eligibility

Status: ready-for-dev

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

- [ ] Verify fixture manifests inside the catalog root (AC: 1)
  - [ ] Resolve relative paths without following escapes/symlinks outside the governed root.
  - [ ] Verify exact bytes, lowercase SHA-256, media type, revision, license, provenance, extraction path, classification, and redistribution policy.
  - [ ] Return typed disposition/failure for missing, changed, escaping, unauthorized, or unredistributable inputs.
- [ ] Close P0/P1 traceability (AC: 2)
  - [ ] Resolve every risk, gate, endpoint, expected-evidence, and claim reference with no orphan.
  - [ ] Resolve and hash every referenced fixture and expected-evidence definition required by the catalog/TEA handoff; a named evidence class without a governed definition is unresolved.
  - [ ] Preserve source location and source/generated hashes.
  - [ ] Generate from catalog data; never hand-author mapping rows.
- [ ] Implement initial data-eligibility policy (AC: 3)
  - [ ] Accept only synthetic or explicitly license-audited public data.
  - [ ] Detect/reject private history, personal data, proprietary repositories, credential material, and unauthorized input.
  - [ ] Produce immutable eligible/rejected dispositions with codes, evidence refs, reviewer role, and canonical hash.
- [ ] Implement study-validity review records and gates (AC: 4)
  - [ ] Record memory-necessity and shortcut audits.
  - [ ] Record contamination search and unique canary results.
  - [ ] Enforce development/pilot/confirmatory holdout separation.
  - [ ] Require frozen baseline and gold grader stability evidence before `eligible`.
- [ ] Integrate gates into compile publication and add fault/golden tests (AC: 1-4)

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

- Python 3.11; stdlib path, MIME, hashing, and immutable record facilities where sufficient.
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

TBD by implementation agent

### Debug Log References

### Completion Notes List

- Ultimate context engine analysis completed - comprehensive developer guide created

### File List
