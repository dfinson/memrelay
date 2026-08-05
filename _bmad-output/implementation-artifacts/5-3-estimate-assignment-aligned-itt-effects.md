# Story 5.3: Estimate Assignment-Aligned ITT Effects

Status: ready-for-dev

## Story

As a causal researcher,
I want frozen estimators aligned to the assigned interference unit,
So that failures, attrition, and clustered treatment remain valid study outcomes.

## Acceptance Criteria

1. **Given** a frozen protocol and analysis dataset  
   **When** an estimator is selected  
   **Then** assignment, experimental, resampling, clustering, and analysis units match the highest treatment-interference level  
   **And** controlled histories use controlled-effect estimands while dynamic histories use sequence-level total-policy estimands.
2. **Given** every assigned unit  
   **When** ITT tables are built  
   **Then** failures, attrition, timeouts, provider outcomes, zero costs, and unavailable evidence remain represented according to the frozen policy  
   **And** no per-protocol or complete-case replacement is silently used.
3. **Given** bounded concurrency, run order, host fingerprint, quota, throttle, or provider contention  
   **When** balance and sensitivity diagnostics run  
   **Then** arm balance and applicable strata are reported  
   **And** changed fingerprints and model roles are analyzed separately.

## Dependencies and Prerequisites

- **Authoritative direct graph dependency:** Story 5.1 only, exactly as declared in `epics.md`.
- Story 5.2 is an optional implementation seam, not a graph prerequisite; when present, use its governed query/lineage service rather than creating a second DuckDB path.
- Estimator, endpoint, assignment, pairing/blocking, missingness, exposure, attrition, contamination, and retry rules must be frozen in the protocol before outcomes.
- Story 5.4 owns multiplicity, simultaneous intervals, power, and claim decisions.

## Tasks / Subtasks

- [ ] Define a frozen estimand/estimator registry (AC: 1)
  - [ ] Bind each estimator to protocol, endpoint, contrast, population, assignment mechanism, treatment strategy, intercurrent-event policy, summary measure, and assignment/experimental/observation/resampling/clustering/analysis units.
  - [ ] Controlled immutable histories use controlled-access effects; dynamic histories use whole-sequence total-policy effects; product/engine, model, history, and environment strata never pool.
  - [ ] Reject estimator selection from observed effect size, significance, missingness pattern, or favorable result.
- [ ] Implement assignment-aligned primary ITT construction (AC: 1, 2)
  - [ ] Start from Story 5.1's complete assigned-unit table and left-join the first/sole authorized eligible outcome under the frozen retry rule; never start from eligible outcomes and thereby lose excluded assignments.
  - [ ] Encode crashes, no/wrong patch, timeouts, post-exposure infrastructure/provider failure, attrition, zero cost, capped wall, and unavailable evidence exactly as frozen.
  - [ ] Treat ambiguous exposure as exposed; retain assignment regardless of actual treatment access/use.
- [ ] Implement exposure-aware secondary bounds without replacing ITT (AC: 2)
  - [ ] Report exposure/access/use strata descriptively and label all per-protocol/as-treated estimates nonrandomized sensitivity analyses.
  - [ ] Produce frozen worst/best and pattern-mixture bounds for missing outcomes, attrition, failed ascertainment, ambiguous exposure, and contamination.
  - [ ] Never drop noncompliers, failures, unexposed assignments, compromised tasks, or missing primary evidence to manufacture a complete-case/per-protocol result.
- [ ] Implement paired, blocked, clustered, and sequence-aware estimation (AC: 1)
  - [ ] Use genuine one-to-one pair-aware/sign-flip or paired permutation logic only when pairing is registered and independently fresh; otherwise use within-block randomization inference.
  - [ ] Permute only within frozen task blocks for controlled fresh runs and whole histories/sequences/teams/clusters for interference designs.
  - [ ] Preserve equal-task weights (`w_t=1/T`) or equal-sequence weights. For ratio endpoints use the ratio of equally task-weighted arithmetic means with the frozen log-ratio test statistic; a zero comparator blocks the ratio rather than receiving an arbitrary constant.
  - [ ] Cluster uncertainty/resampling at the assignment unit, with frozen pretreatment strata only; repeated observations, turns, and attempts never increase independent N.
  - [ ] Implement the registered primary randomization/stratified estimator and frozen CMH plus GLMM/GEE sensitivities. Provide CR2 with t/F degrees of freedom and wild-cluster-bootstrap sensitivity when fewer than 20 independent clusters; material disagreement is `indeterminate`.
- [ ] Implement balance, missingness, attrition, and operational sensitivities (AC: 2, 3)
  - [ ] Report allocation/order/concurrency, host fingerprint, model role, task/history/repository, quota, throttle, provider-time, exposure, attrition, and contamination balance.
  - [ ] Compare observed nonprimary missingness by arm/cell and retain failed/missing-as-worst, best, and frozen pattern-mixture results.
  - [ ] Split changed fingerprints and model roles into separate strata; never “adjust them away” post hoc.
- [ ] Emit immutable ITT tables and estimator decision records (AC: 1-3)
  - [ ] Bind source/derivation hashes, protocol, estimand/estimator version, units, population, strata, and all diagnostics.
- [ ] Add golden synthetic tests for every assignment branch and terminal condition (AC: 1-3).

## Developer Context

The primary causal target is assignment, not observed memory use. Exposure classification changes retry and sensitivity handling, never the assigned arm. Per-protocol/as-treated analyses are bounded diagnostics because tool access/use is post-treatment. Pair-aware methods require real registered pairs; repeated runs do not inflate independent N. Effective N is independent assignment units.

### Architecture and Statistical Guardrails

- Follow AD-03, AD-07, AD-11, AD-12, AD-15, AD-18, AD-24.
- Freeze estimands and estimator selection before outcomes. No outcome-derived weights, covariates, exclusions, thresholds, or family changes.
- Use complete ITT for the 512-unit primary. Inadequate power later yields estimation-only, never silent expansion.

### File Structure Requirements

- **NEW:** `evaluation/src/memrelay_eval/analysis/{estimands,estimators,diagnostics}.py`
- **UPDATE:** `evaluation/src/memrelay_eval/analysis/{schemas,queries}.py`
- **UPDATE:** `evaluation/src/memrelay_eval/domain/{entities,errors,policies}.py`
- **NEW:** `evaluation/tests/unit/analysis/test_itt_outcomes.py`
- **NEW:** `evaluation/tests/unit/analysis/test_assignment_units.py`
- **NEW:** `evaluation/tests/contract/analysis/test_estimand_registry.py`
- **NEW:** `evaluation/tests/golden/analysis/estimators/`

### Existing Behavior to Preserve

- Preserve concealed assignment, immutable attempt/exposure/retry lineage, all failures, zero/unavailable distinctions, and inclusion/evidence blockers.
- Preserve active-wall versus queue/provisioning/backoff/cleanup clocks and separate provider cost provenance.
- Preserve controlled/dynamic and product/engine causal boundaries.

### Testing Requirements

- Golden vectors cover controlled blocked runs, genuine pairs, dynamic sequences, sparse clusters, timeouts, retries, post-exposure failures, attrition, unavailable fields, zero cost, contamination, and changed fingerprints.
- Estimator vectors cover equal-task arithmetic-mean ratios, zero comparators, whole-sequence permutation, CMH/GLMM/GEE sensitivity, CR2 t/F inference, and the under-20-cluster disagreement rule.
- Metamorphic tests prove relabeling only within valid assignment blocks, row-order invariance, no N inflation, and rejection of outcome-derived exclusions.
- Compare exact/permutation results to enumerated small samples.

### Anti-Patterns

- Do not analyze tool calls/turns as independent N, replay treatment-generated history into controls, use observed treatment as the primary exposure, or replace ITT with completers.
- Do not condition on retrieval/use/survival, select favorable attempts, pool environments/models/history modes/strata, or apply arbitrary constants to zero ratios.
- Do not make confirmatory claim decisions here.

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` — Epic 5 / Story 5.3; FR50]
- [Source: `ARCHITECTURE-SPINE.md` — AD-03, AD-07, AD-18, AD-24; Implementation Freeze]
- [Source: `IMPLEMENTATION-DESIGN.md` — §§8, 10, 12, 20, 24.2-24.4]
- [Source: technical research — Experimental ontology; history estimands; assignment rule; frozen ITT table]
- [Source: `_bmad-output/test-artifacts/test-design-qa.md` — `STAT-ESTIMATOR-*`, `STAT-CLUSTER-CORRECTION`, `ITT-*`, `TEL-MISSINGNESS`]
- [Source: predecessor Stories 1.6-1.7, 2.5, 2.8-2.9, 4.5, and 5.1]

## Dev Agent Record

### Agent Model Used

TBD by implementation agent

### Debug Log References

### Completion Notes List

- Ultimate context engine analysis completed - comprehensive developer guide created

### File List
