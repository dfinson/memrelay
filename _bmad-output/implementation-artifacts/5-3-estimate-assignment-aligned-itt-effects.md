# Story 5.3: Estimate Assignment-Aligned ITT Effects

Status: review

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

- [x] Define a frozen estimand/estimator registry (AC: 1)
- [x] Bind each estimator to protocol, endpoint, contrast, population, assignment mechanism, treatment strategy, intercurrent-event policy, summary measure, and assignment/experimental/observation/resampling/clustering/analysis units.
- [x] Controlled immutable histories use controlled-access effects; dynamic histories use whole-sequence total-policy effects; product/engine, model, history, and environment strata never pool.
- [x] Reject estimator selection from observed effect size, significance, missingness pattern, or favorable result.
- [x] Implement assignment-aligned primary ITT construction (AC: 1, 2)
- [x] Start from Story 5.1's complete assigned-unit table and left-join the first/sole authorized eligible outcome under the frozen retry rule; never start from eligible outcomes and thereby lose excluded assignments.
- [x] Encode terminal, attrition, and unavailable evidence status without dropping assignments.
- [x] Treat ambiguous exposure as exposed; retain assignment regardless of actual treatment access/use.
- [x] Implement exposure-aware secondary bounds without replacing ITT (AC: 2)
- [x] Report exposure strata descriptively and retain nonrandomized sensitivity boundaries.
- [x] Produce frozen worst/best and pattern-mixture bounds for missing outcomes and attrition.
- [x] Never use a complete-case or per-protocol primary estimate.
- [x] Implement paired, blocked, clustered, and sequence-aware estimation (AC: 1)
- [x] Require registered fresh pairs, otherwise use frozen within-block randomization.
- [x] Preserve controlled blocks and dynamic sequence resampling units.
- [x] Preserve equal-task weights and use a log-ratio randomization statistic with zero-comparator rejection.
- [x] Prevent repeated observations, attempts, and mixed sequence units from increasing independent N.
- [x] Provide CMH, GLMM/GEE sensitivities and fail closed when a qualified CR2/wild-bootstrap backend is absent.
- [x] Implement balance, missingness, attrition, and operational sensitivities (AC: 2, 3)
- [x] Report allocation/order/concurrency, fingerprint, model role, task/history/repository, quota, throttle, provider-time, exposure, attrition, and contamination balance.
- [x] Retain frozen worst/best and pattern-mixture results.
- [x] Reject effect estimation across changed fingerprint or model-role strata.
- [x] Emit immutable ITT tables and estimator decision records (AC: 1-3)
- [x] Bind source/derivation hashes, protocol, sealed assignment plan, registry, units, and diagnostics.
- [x] Add focused golden and synthetic assignment/terminal-condition coverage.

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

GPT-5.6 Terra

### Debug Log References

- Focused Story 5.3 tests and Ruff checks: 11 passed.
- `py -3.13 -m pytest evaluation\\tests -q`: 1237 passed, 46 skipped.

### Completion Notes List

- Added an immutable frozen estimand registry, sealed assignment-analysis disclosure lock,
  assignment-aligned ITT left join, deterministic blocked/pair/sequence inference, frozen
  bounds, CMH/GLMM/GEE sensitivities, and operational balance diagnostics.
- Exact terminal attempt/reconciliation lineage and Story 5.1 file/derivation hashes are
  verified before analysis; incomplete, mixed, unsupported, or post-freeze inputs fail closed.
- CR2 and wild-cluster-bootstrap output remains explicitly indeterminate until a qualified
  registered backend is available; no substitute uncertainty claim is emitted.

### File List

- `_bmad-output/implementation-artifacts/{5-3-estimate-assignment-aligned-itt-effects.md,sprint-status.yaml}`
- `evaluation/{schemas/{assignment-aligned-itt-table,assignment-balance-diagnostic-report,frozen-estimator-decision}.schema.json,src/memrelay_eval/analysis/{__init__,diagnostics,estimands,estimators,queries,schemas}.py}`
- `evaluation/src/memrelay_eval/domain/errors.py`
- `evaluation/tests/{unit/analysis/{test_assignment_units,test_itt_outcomes}.py,contract/analysis/{test_estimand_registry,test_parquet_schemas}.py,golden/analysis/{test_estimators.py,estimators/blocked-itt.json}}`
