# Story 3.6: Normalize Outcomes Under Separate Authorities

Status: done

## Story

As a research lead,
I want explicit outcome authority and normalized endpoint records,
so that hard correctness, qualitative quality, and categorical blockers cannot substitute for one another.

## Acceptance Criteria

1. **Given** deterministic grader, panel, calibration, and adjudication records  
   **When** outcome normalization runs  
   **Then** executable correctness is authoritative for hard pass/fail and panel quality is authoritative only for its separate qualitative co-primary endpoint  
   **And** endpoint records retain scorer, rubric, grader, snapshot, and evidence hashes.
2. **Given** failed tests, security violations, governance failures, evidence-integrity failures, grading failures, or causal-validity failures  
   **When** panel scores are favorable  
   **Then** those scores cannot reverse the categorical or executable outcome  
   **And** the conflicting evidence is preserved for reconciliation.
3. **Given** a scorer attempts assignment access or cross-adapter import  
   **When** architecture and contract tests run  
   **Then** the access or import fails  
   **And** scoring remains dependent on domain ports and blinded evidence only.

## Dependencies and Prerequisites

- **Authoritative direct graph dependencies:** Stories 3.1, 3.4, and 3.5, exactly as declared in `epics.md`. Story 3.2 view/leakage evidence and all three Story 3.3 original judge records arrive transitively; do not add direct edges or redefine them.
- Story 4.1 CAS qualification remains mandatory before paid/study-eligible artifact resolution; Epic 4 reconciliation later decides inclusion.
- This story normalizes evidence; it does not estimate treatment effects, impute missing outcomes, or materialize confirmatory Parquet.
- Exact traceability: FR37; NFR14, NFR36, NFR45; AR20.

## Tasks / Subtasks

- [x] Define separate versioned hard, qualitative, and categorical outcome records (AC: 1)
  - [x] Retain opaque IDs, status/value/unavailable reason, authority, scorer/rubric/grader/snapshot/protocol hashes, source evidence, and derivation hash.
  - [x] Represent adjudication as `not_triggered`, `completed`, or `failed/blocked`; a retained no-threshold evaluation is required when no call occurred, while a required failed adjudication blocks qualitative finalization.
- [x] Implement explicit authority policy (AC: 1, 2)
  - [x] Hard endpoint derives only from deterministic grader authority.
  - [x] Qualitative co-primary endpoint derives only from the three-judge panel plus applicable reliability/adjudication policy.
  - [x] Security, governance, evidence-integrity, grading, and causal-validity blockers remain separate hard gates.
  - [x] Require a prospectively frozen qualitative aggregation/scale/weighting and missingness policy; do not invent averaging, weights, or fallback values during normalization.
- [x] Enforce all qualitative prerequisites (AC: 1, 2)
  - [x] Require three fresh blinded judge records, frozen human calibration, agreement, drift, duplicate/sentinel checks, arm-classifier 95% upper AUC `<=0.60`, and partial/homogeneous-panel shared-bias gate.
  - [x] Treat failed/missing panel gates as blocked/unavailable qualitative confirmation, never zero, pass, or an executable rewrite.
- [x] Append adjudication correctly (AC: 1, 2)
  - [x] Use it only for threshold-crossed disputed criteria; retain originals; prohibit it from waiving panel gates, executable failure, or categorical blockers.
- [x] Preserve authority conflicts for downstream reconciliation (AC: 2)
  - [x] Favorable panel quality alongside hard failure remains a valid multi-record state; never collapse to the favorable result.
  - [x] Preserve hard or qualitative `unavailable` separately from `failed`; never coerce unresolved grader/panel evidence to pass, fail, or zero.
- [x] Enforce architecture boundaries mechanically (AC: 3)
  - [x] AST/import tests reject scoring-to-assignment and cross-adapter imports; runtime ports expose only blinded evidence/domain records.
- [x] Add exhaustive authority-table, missingness, conflict, tamper, lineage, and import-boundary tests (AC: 1-3).

## Developer Context

Normalization must make invalid states unrepresentable: executable correctness and qualitative quality are two co-primary endpoints with different authorities, while categorical blockers dominate eligibility/claims without erasing either endpoint. One blocker is sufficient and non-overridable; aggregates, judges, adjudicators, operators, or favorable evidence cannot waive it. Preserve conflicts because downstream reconciliation must see why a run is excluded or a claim blocked.

### Architecture and File Requirements

- Follow AD-01, AD-05, AD-12, AD-13, AD-15, AD-17, and dependency rules forbidding scoring access to assignment resolution and adapter-to-adapter imports.
- Expected paths:
  - **UPDATE:** `evaluation/src/memrelay_eval/domain/{entities,ports,policies,errors}.py`
  - **NEW/UPDATE:** `evaluation/src/memrelay_eval/scoring/outcomes.py`
  - **UPDATE:** `evaluation/src/memrelay_eval/scoring/service.py`
  - **UPDATE:** `evaluation/src/memrelay_eval/evidence/required.py`
  - **NEW:** `evaluation/schemas/endpoint-record.schema.json`
  - **NEW:** `evaluation/tests/{unit,contract}/scoring/test_{outcomes,authority,boundaries}.py`
- `evaluation/` does not yet exist. Extend completed predecessor contracts and the one shared canonicalizer; do not update product source or root dependencies.

### Library, Test, and Version Guardrails

- Python 3.13, stdlib-only domain, one shared canonicalizer, lowercase SHA-256, explicit schema versions, and no NaN/Infinity.
- No provider call is permitted during normalization. CI must be no-network and exercise complete authority truth tables, including all adjudication states and unavailable-vs-failed distinctions.
- Test every hard/qualitative/blocker combination, missing/unavailable records, failed panel gates, favorable conflicts, adjudication boundaries, immutable source hashes, and forbidden imports/access.
- Authority mapping, endpoint schema, grader/rubric/panel gate, blocker vocabulary, threshold, or derivation changes require a new protocol version.

### Preserved Behavior and Anti-Patterns

- Preserve every raw grader/judge/adjudication/calibration record, original terminal classification, fake/durable artifact provenance, and reconciliation conflict.
- Do not average endpoints, let quality rescue failed tests, let executable pass waive qualitative failure, convert unavailable to zero, drop blocked candidates, access assignment, or duplicate canonicalization.
- Do not mark fake artifact, ledger, or telemetry evidence study-eligible; Story 4.1 CAS conformance alone is insufficient without later durable adapters and reconciliation. Do not implement Epic 4 inclusion or Epic 5 estimators here.

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` — Story 3.6; FR37; NFR14, NFR36, NFR45]
- [Source: `ARCHITECTURE-SPINE.md` — AD-01, AD-13, AD-15; consistency conventions]
- [Source: `IMPLEMENTATION-DESIGN.md` — §§6.7, 19.3-19.6, 24.2-24.4]
- [Source: technical research — benchmark-native outcome authority and bounded claim guidance]
- [Source: `_bmad-output/test-artifacts/test-design-qa.md` — `GRADE-*`, `BLIND-*`, `BLOCK-*`, `ITT-MISSING-PRIMARY`]
- [Source: `_bmad-output/test-artifacts/test-design-architecture.md` — R-008, R-010, R-012, R-018]
- [Source: predecessor Epic 1/2 guides — domain ports, opaque IDs, fake artifact eligibility, isolation and native evidence]

## Dev Agent Record

### Agent Model Used

TBD by implementation agent

### Debug Log References

### Completion Notes List

- Added immutable endpoint records with explicit deterministic-grader and blinded-panel authorities,
  source evidence hashes, and canonical derivation hashes.
- Added a sealed qualitative aggregation protocol. Missing, malformed, partial, conflicting, or
  unauthorized authority evidence finalizes only as unavailable or blocked; it never substitutes a
  pass, failure, or zero.
- Preserved favorable qualitative outcomes beside executable failures and categorical blocker records
  for later reconciliation. Panel reliability and required adjudication failures block only qualitative
  finalization and never rewrite executable authority.
- Added endpoint schema, authority and tamper tests, and AST checks rejecting scoring imports from
  assignment resolution or adapters.

### File List

- `_bmad-output/implementation-artifacts/3-6-normalize-outcomes-under-separate-authorities.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
- `evaluation/schemas/endpoint-record.schema.json`
- `evaluation/src/memrelay_eval/scoring/__init__.py`
- `evaluation/src/memrelay_eval/scoring/outcomes.py`
- `evaluation/tests/contract/scoring/test_outcome_boundaries.py`
- `evaluation/tests/unit/scoring/test_outcomes.py`
