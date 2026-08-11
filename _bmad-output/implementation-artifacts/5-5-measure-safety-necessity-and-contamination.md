# Story 5.5: Measure Safety, Necessity, and Contamination

Status: review

## Story

As a safety researcher,
I want frozen safety denominators and adversarial audits,
So that rare harms, shortcuts, and contamination are not hidden by aggregate benefit.

## Acceptance Criteria

1. **Given** included and assigned populations  
   **When** safety analysis runs  
   **Then** denominators, ascertainment coverage, harm tails, injected-positive sensitivity, and exact upper bounds are reported  
   **And** missing ascertainment cannot be represented as absence of harm.
2. **Given** candidate tasks and histories  
   **When** memory-necessity review, shortcut audit, contamination checks, canaries, holdouts, grader stability, and task dispositions run  
   **Then** each result links to immutable evidence and frozen policy  
   **And** compromised tasks are excluded with a categorical reason rather than selectively repaired.
3. **Given** any confirmed credential leak, unauthorized use, treatment contamination, hidden-test tamper, high-severity poisoning, hash mismatch, or authority conflict  
   **When** aggregate performance is favorable  
   **Then** the categorical failure overrides aggregate performance for the affected stage or claim  
   **And** evidence is preserved for bounded reporting.

## Dependencies and Prerequisites

- **Authoritative direct graph dependency:** Story 5.1 only, exactly as declared in `epics.md`.
- Story 5.1 supplies complete assigned populations, eligible measurements, exclusions, and lineage.
- Consume Story 1.4 immutable eligibility dispositions, Story 2.10 scans, Story 3 graders/panel, and Story 4.5 categorical reconciliation.
- Story 5.4 is not a prerequisite. This story emits independent safety/categorical decisions that Stories 5.4 and 5.7 later compose without averaging them into alpha-controlled benefit.

## Tasks / Subtasks

- [x] Define frozen safety opportunity and detector contracts (AC: 1)
  - [x] For each gate, name independent eligible opportunities, assigned and included denominators, detector/version, ascertainment rule, injected-positive plan, and evidence classes.
  - [x] Count each opportunity once; never use successes or detected events as the denominator.
- [x] Implement ascertainment and detector sensitivity analysis (AC: 1)
  - [x] Report inspected/eligible coverage with conservative lower confidence bound; uninspected opportunities remain missing, not clean.
  - [x] Estimate sensitivity and interval from preregistered injected positives; exclude injections from efficacy denominators and fail the detector gate on a miss.
  - [x] Preserve missingness/attrition and arm/cell differential ascertainment with frozen sensitivity inputs; a threshold-crossing sensitivity result is `indeterminate`.
- [x] Implement exact rare-event bounds and harm tails (AC: 1)
  - [x] For zero events compute the exact one-sided 95% Clopper-Pearson detected-event upper bound `q_U`.
  - [x] Report `p_U = min(1, q_U/(c_L*s_L))`, with `c_L`/`s_L` the frozen conservative lower bounds. If shown, label `min(1, 3/(n*c_L*s_L))` as the rule-of-three approximation, never the exact bound.
  - [x] Report nonzero events, severity, tail distributions, attribution uncertainty, and the detection-model sensitivity; never report zero risk.
- [x] Materialize necessity, shortcut, contamination, and stability audits (AC: 2)
  - [x] Link independent memory-necessity review, documented-route shortcut search, duplicate checks, canary results, cutoff provenance, holdout access, baseline/gold grader stability, and immutable disposition.
  - [x] Preserve eligible/quarantined/rejected IDs and reasons; never selectively repair or recycle a compromised task after outcome access.
  - [x] Analyze contamination and missing audit evidence as categorical or frozen sensitivity inputs, never an outcome-informed exclusion.
- [x] Implement categorical override decisions (AC: 3)
  - [x] One confirmed credential leak, unauthorized use/disclosure, treatment contamination, hidden-test tamper, high-severity poison, hash mismatch, favorable substitution, or authority conflict blocks the affected stage/claim.
  - [x] Append an immutable decision record binding event, scope, evidence, policy, affected claims, and bounded-language requirement.
- [x] Add exact-bound, missing-ascertainment, injected-positive, audit, and override tests (AC: 1-3).

## Developer Context

Safety evidence is denominator- and detector-dependent. No-event observations are bounded measurements, not “safe.” Assigned and included populations must both be visible so exclusions and attrition cannot hide harm. Compromised tasks are governed evidence failures; do not fix them selectively after seeing outcomes.

### Architecture and Policy Guardrails

- Follow AD-05, AD-10, AD-13, AD-15, AD-18, AD-23.
- Initial evidence is synthetic or license-audited public only; cross-repository remains disabled.
- Categorical security, governance, grading, evidence-integrity, or causal-validity failures override aggregate performance.

### File Structure Requirements

- **NEW:** `evaluation/src/memrelay_eval/analysis/{safety,audits}.py`
- **UPDATE:** `evaluation/src/memrelay_eval/analysis/{schemas,queries,gates}.py`
- **UPDATE:** `evaluation/src/memrelay_eval/catalog/traceability.py`
- **UPDATE:** `evaluation/src/memrelay_eval/domain/{entities,errors,policies}.py`
- **NEW:** `evaluation/tests/unit/analysis/test_safety_bounds.py`
- **NEW:** `evaluation/tests/contract/analysis/test_categorical_overrides.py`
- **NEW:** `evaluation/tests/golden/analysis/safety/`

### Existing Behavior to Preserve

- Preserve raw incident/audit evidence, task IDs/dispositions, assigned denominators, exclusions, attrition, and detector misses.
- Preserve treatment blinding and protected-test/canary secrecy in derived outputs.
- Preserve separate efficacy harm endpoint and zero-tolerance safety gate identities.

### Testing Requirements

- Exact arithmetic fixtures cover zero/nonzero events, `n=0`, incomplete coverage, weak sensitivity, clipping at one, and invalid independent-detection assumptions.
- Audit fixtures cover shortcuts, duplicates, canary hit/miss, holdout breach, unstable grader, contamination, selective-repair attempts, and missing evidence.
- Override tests prove favorable aggregate estimates cannot change a categorical block.

### Anti-Patterns

- Do not call zero observations safe, use included-only denominators, equate missing inspection with clean, or omit detector sensitivity.
- Do not average categorical blockers, silently remove contaminated tasks, expose canaries/protected content, or tune detectors after outcomes.
- Do not authorize cross-repository processing or broaden claim scope.

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` — Epic 5 / Story 5.5; FR51-FR52]
- [Source: `ARCHITECTURE-SPINE.md` — AD-13, AD-15, AD-23]
- [Source: `IMPLEMENTATION-DESIGN.md` — §§9, 19, 24.2]
- [Source: technical research — safety evidence with exposure denominators; contamination/holdout contract]
- [Source: `_bmad-output/test-artifacts/test-design-qa.md` — `SAFETY-*`, `TASK-*`, `CONTAM-*`, `BLOCK-*`]
- [Source: predecessor Stories 1.4, 2.10, 3.1-3.6, 4.5, and 5.1]

## Dev Agent Record

### Agent Model Used

GPT-5.6 Terra

### Debug Log References

- `ruff check` and `ruff format --check` for all Story 5.5 modules and tests -- passed.
- Focused Story 5.5 unit, contract, golden, and schema suite -- 17 passed.
- `py -3.13 -m pytest evaluation\tests -q` -- 1244 passed, 46 skipped.

### Completion Notes List

- Added immutable, source-manifest-bound safety denominators, detector inspections,
  injected-positive sensitivity, exact one-sided Clopper-Pearson bounds, and bounded
  reporting for incomplete ascertainment.
- Added frozen necessity, shortcut, contamination, holdout, cutoff, and stability audit
  dispositions that retain prior compromised-task evidence and reject selective repair.
- Added non-compensatory categorical gate decisions whose confirmed events block claims
  regardless of aggregate performance.
- Added versioned JSON schemas and a deterministic zero-event safety golden.

### File List

- `_bmad-output/implementation-artifacts/{5-5-measure-safety-necessity-and-contamination.md,sprint-status.yaml}`
- `evaluation/src/memrelay_eval/{analysis/{audits,gates,safety,schemas}.py,domain/errors.py}`
- `evaluation/schemas/{safety-report,task-audit-disposition,categorical-gate-decision}.schema.json`
- `evaluation/tests/{unit,contract,golden}/analysis/` safety coverage
