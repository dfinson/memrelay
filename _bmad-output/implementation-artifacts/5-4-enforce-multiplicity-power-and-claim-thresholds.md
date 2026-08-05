# Story 5.4: Enforce Multiplicity, Power, and Claim Thresholds

Status: ready-for-dev

## Story

As a research lead,
I want frozen multiplicity, power, interval, and gate calculations,
So that outcomes cannot weaken confirmatory standards.

## Acceptance Criteria

1. **Given** confirmatory claim families  
   **When** inference runs  
   **Then** familywise alpha is `0.05` with Holm control, simultaneous intervals are emitted, and target power is `0.80`  
   **And** a 512-unit primary with frozen simulated power below `0.80` remains estimation-only without silent enrollment expansion.
2. **Given** reliability or qualitative benefit claims  
   **When** gates are evaluated  
   **Then** the point difference must be at least `+0.05` and the simultaneous 95% lower bound must exceed `0`  
   **And** qualitative results use the `[0,1]` blinded panel scale and require the panel gate.
3. **Given** no-regression, cost, or active-wall-time claims  
   **When** gates are evaluated  
   **Then** no-regression uses a one-sided 97.5% lower bound above `-0.02`; superiority requires a ratio at most `0.90`, simultaneous 95% upper bound below `1.0`, and reliability/quality lower bounds above `-0.02`  
   **And** thresholds frozen before the first study trial cannot be weakened after pilot or primary outcomes.

## Dependencies and Prerequisites

- **Authoritative direct graph dependency:** Story 5.3 only, exactly as declared in `epics.md`.
- Story 5.3 supplies frozen estimands, complete ITT tables, assignment-unit maps, and sensitivity outputs.
- Story 3.4 supplies immutable panel reliability/calibration/blinding gate evidence.
- Frozen protocol supplies confirmatory families, endpoint order, sidedness, assignment design, simulation cells, seeds, and stage size before outcomes.

## Tasks / Subtasks

- [ ] Implement immutable claim-family and threshold records (AC: 1-3)
  - [ ] Freeze family membership, endpoint IDs, direction, margins, gates, ordering, alpha `0.05`, Holm method/version, power `0.80`, and analysis/interval procedure before enrollment.
  - [ ] Encode the frozen mode cardinalities: reliability has 3 endpoints (`EP-PRIM-SUCCESS`, `EP-QUAL`, `EP-HARM`); efficiency has 5 (`EP-SUCC-NI`, `EP-QUAL`, `EP-COST`, `EP-WALL`, `EP-HARM`) with cost, wall, or their registered intersection selected before enrollment; dual has 5 (`EP-PRIM-SUCCESS`, `EP-QUAL`, `EP-COST`, `EP-WALL`, `EP-HARM`) with every preregistered efficiency component required.
  - [ ] Canonicalize/hash the record and reject mutation, family switching, endpoint salvage, or threshold relaxation after outcome access.
- [ ] Implement Holm FWER control and compatible simultaneous intervals (AC: 1)
  - [ ] Correctly handle ties, missing/blocked endpoints, monotonic adjusted decisions, and deterministic endpoint ordering.
  - [ ] Derive claim decisions from the same frozen family/procedure used for intervals; marginal intervals or raw p-values cannot decide claims.
  - [ ] Preserve raw estimates, raw/adjusted p-values, simultaneous bounds, ranks, family hash, and gate trace.
  - [ ] Treat final architecture as authoritative over the older TEA `STAT-MULTIPLICITY` row's graphical/weighted-Bonferroni wording: v1 uses Holm only. Add a conformance fixture that rejects graphical recycling or unequal-weight substitution as a protocol change.
- [ ] Implement exact frozen benefit/non-inferiority gates (AC: 2, 3)
  - [ ] Reliability benefit: risk-difference point estimate `>= +0.05` and simultaneous 95% lower bound `> 0`.
  - [ ] Qualitative benefit: blinded `[0,1]` panel-score difference `>= +0.05`, simultaneous 95% lower bound `> 0`, and panel gate passed.
  - [ ] No-regression: one-sided 97.5% lower bound `> -0.02`.
  - [ ] Cost or active-wall superiority: ratio point estimate `<= 0.90`, simultaneous 95% upper bound `< 1.0`, and simultaneous reliability and qualitative lower bounds each `> -0.02`.
  - [ ] Release fitness later requires at least one target benefit, all categorical gates, and endpoint-appropriate non-target non-inferiority: difference-scale simultaneous lower bounds `> -0.02` and cost/wall ratio simultaneous upper bounds `< 1.10`.
- [ ] Implement frozen simulation-based power evaluation (AC: 1)
  - [ ] Simulate the exact assignment, estimator, pair/block/cluster/sequence hierarchy, Holm family, endpoint dependence, missingness/attrition/contamination sensitivity, and censoring policy.
  - [ ] Run at least 10,000 trials per retained cell in the finite frozen Cartesian grid with reproducible seeds and independent spot checks; retain every enumerated cell, record rejected positive-definiteness/invalid cells explicitly, and require the worst owner-ratified plausible cell to meet target power.
  - [ ] If the fixed 512-unit primary is below `0.80` for its target margin, emit `estimation_only`; never increase N, drop endpoints, change cells, or weaken thresholds automatically.
  - [ ] Consume only DG-5 baseline-only or arm-blind-escrow nuisance inputs; decoded pilot efficacy cannot tune effects, thresholds, families, tasks, or simulation cells.
- [ ] Implement immutable claim-gate decision records (AC: 1-3)
  - [ ] Emit pass/fail/blocked/indeterminate/estimation-only with source, derivation, protocol, family, threshold, power, panel, and categorical-gate hashes.
- [ ] Add boundary, multiplicity, simulation, and anti-relaxation tests (AC: 1-3).
  - [ ] Cover the frozen fixed-information look: efficacy output is denied before final information, while separately governed safety/budget monitoring remains available.

## Developer Context

All comparison operators are intentional and strict: lower bounds must exceed, not equal, `0` or `-0.02`; superiority upper bounds must be below, not equal, `1.0`. Cost/wall ratio `0.90` alone is insufficient. Frozen Holm families and simultaneous bounds prevent post-hoc endpoint salvage. A low-powered fixed primary is valuable estimation evidence, not a license to expand or tune.

### Architecture and Version Guardrails

- Follow AD-03, AD-07, AD-13, AD-15, AD-17, AD-24 and the Architecture Implementation Freeze.
- Familywise alpha is exactly `0.05`; target power exactly `0.80`; Holm is mandatory.
- Panel gate remains weighted kappa or ICC `>=0.70`, calibration MAE `<=0.10`, and blinded classifier 95% upper AUC `<=0.60`.

### File Structure Requirements

- **NEW:** `evaluation/src/memrelay_eval/analysis/{multiplicity,intervals,power,gates}.py`
- **UPDATE:** `evaluation/src/memrelay_eval/analysis/{estimands,estimators,schemas,queries}.py`
- **UPDATE:** `evaluation/src/memrelay_eval/domain/{entities,errors,policies}.py`
- **NEW/UPDATE:** immutable protocol/claim-gate schemas under `evaluation/schemas/`
- **NEW:** unit/golden tests under `evaluation/tests/{unit,golden}/analysis/`

### Existing Behavior to Preserve

- Preserve Story 5.3 estimands/ITT rows and Story 3 panel evidence; inference never rewrites inputs.
- Preserve unavailable/blocked endpoints and categorical failures instead of converting them to non-significance.
- Preserve model, environment, product/engine, and history strata.

### Testing Requirements

- Boundary tests cover equality immediately at/below/above `+0.05`, `0`, `-0.02`, `0.90`, `1.0`, and the non-target economic ratio margin `1.10`.
- Holm golden vectors include ties, reordered inputs, blocked endpoints, and family cardinalities.
- Power fixtures verify pair/block/cluster/sequence designs, missingness mechanisms, attrition, contamination, endpoint correlations, censoring, deterministic seeds, and estimation-only decisions.
- Mutation tests prove outcome-aware threshold/family/N changes require a new protocol and never revise existing decisions.

### Anti-Patterns

- Do not use unadjusted p-values, marginal intervals, BH for confirmatory families, normal approximations inconsistent with assignment, or point estimates alone.
- Do not change endpoint families, weights, sidedness, margins, simulation cells, N, or panel rules after outcomes.
- Do not let cost/time savings caused by failures pass without reliability/quality non-inferiority.

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` — Epic 5 / Story 5.4; FR50]
- [Source: `ARCHITECTURE-SPINE.md` — Implementation Freeze]
- [Source: `IMPLEMENTATION-DESIGN.md` — §§24.2-24.3]
- [Source: technical research — simulation power contract; multiplicity and compatible intervals]
- [Source: `_bmad-output/test-artifacts/test-design-qa.md` — `STAT-MULTIPLICITY`, `STAT-INTERVAL-COMPAT`, `STAT-POWER-SIM`, `STAT-MODE-*`]
- [Source: predecessor Stories 3.4 and 5.3]

## Dev Agent Record

### Agent Model Used

TBD by implementation agent

### Debug Log References

### Completion Notes List

- Ultimate context engine analysis completed - comprehensive developer guide created

### File List
