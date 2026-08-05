# Story 5.7: Generate Evidence-Linked Reports and Bounded Claims

Status: ready-for-dev

## Story

As a release decision-maker,
I want local reports that bind every conclusion to its tested scope and evidence,
So that null, harmful, indeterminate, and positive results are communicated without overclaiming.

## Acceptance Criteria

1. **Given** completed stage analysis  
   **When** `memrelay-eval report --stage <stage>` runs  
   **Then** the generated local report includes intervals, diagnostics, Pareto surfaces, harm tails, costs, active and non-active time, panel metrics, gates, and claim decisions  
   **And** every table, figure, and claim links protocol, population, model, endpoint, stratum, history regime, source hashes, derivation hash, and evidence/gate IDs.
2. **Given** a proposed efficacy, safety, economic, or release claim  
   **When** claim bounding runs  
   **Then** construction, component tests, deterministic fixtures, unreconciled trials, engine upper bounds, and pilot outcomes cannot be represented as shipped-product confirmatory efficacy  
   **And** null, harmful, indeterminate, and positive conclusions use frozen language.
3. **Given** release fitness evaluation  
   **When** gates are applied  
   **Then** at least one reliability, qualitative, cost, or wall-time benefit passes, every non-target primary outcome passes non-inferiority, and every categorical gate passes  
   **And** the claim is limited to the tested population, model, stratum, protocol, and history regime.

## Dependencies and Prerequisites

- **Authoritative direct graph dependencies:** Stories 4.7, 5.2, 5.4, and 5.5 only, exactly as declared in `epics.md`.
- Story 5.6 is a promotion gate, not a direct implementation dependency: without successful reproduction evidence a report remains explicitly `draft/unverified` and cannot support release fitness.
- Reports are the only v1 presentation surface; no managed tracker, cloud warehouse, interactive UI, or viewer-owned evidence.

## Tasks / Subtasks

- [ ] Define immutable report inputs and report/claim schemas (AC: 1)
  - [ ] Freeze exact dataset, table, figure, estimator, interval, Holm family, power, safety, panel, cost revision, gate, and decision-record hashes before rendering.
  - [ ] Reject mutable “latest”, missing lineage, unreconciled inputs, or mixed strata/history/environment/model records.
  - [ ] Record renderer/version/runtime, template hash, ordered inputs, outputs, protocol, and typed terminal status.
- [ ] Generate deterministic local reports (AC: 1)
  - [ ] Include simultaneous and marginal descriptive intervals with clear decision authority, ITT and exposure-aware/per-protocol sensitivity bounds, missingness/attrition/contamination diagnostics, balance, Pareto surfaces, harm tails, safety denominators/bounds, costs, active/queue/provisioning/backoff/cleanup time, panel metrics, and categorical gates.
  - [ ] Link every cell/figure/claim to source table/file hashes, derivation hash, protocol, population, model, endpoint, product/engine stratum, history regime, environment, units, evidence, and gate IDs.
  - [ ] Keep Copilot subscription, framework OpenAI, and local-resource quantities/amounts separately labeled; bind the selected immutable price/revision.
- [ ] Implement bounded claim classification and frozen language (AC: 2)
  - [ ] Emit only `null`, `harmful`, `indeterminate`, or `positive` from immutable decision records; do not regenerate thresholds from outcomes.
  - [ ] Prevent construction/conformance, component tests, deterministic fixtures, pilot data, unreconciled trials, and direct-engine upper bounds from becoming shipped-product confirmatory efficacy.
  - [ ] Safety language states the exact rate upper bound, denominator, coverage, and sensitivity; never “safe”, “zero risk”, or “no events means no risk”.
- [ ] Implement release-fitness decision records (AC: 3)
  - [ ] Require at least one frozen reliability/qualitative/cost/wall benefit decision to pass.
  - [ ] Require endpoint-appropriate non-target non-inferiority: each difference-scale simultaneous lower bound must exceed `-0.02`, each non-target cost/wall ratio simultaneous upper bound must be below `1.10`, the panel gate applies wherever qualitative evidence is used, and every categorical gate must pass.
  - [ ] For cost/wall benefits, require ratio `<=0.90`, simultaneous upper bound `<1.0`, and reliability/quality simultaneous lower bounds `>-0.02`; reliability/qualitative benefits require point `>=+0.05` and simultaneous lower bound `>0`.
  - [ ] Limit the decision to tested population, exact model, product/engine stratum, protocol, environment, endpoint family, and controlled/dynamic history regime.
- [ ] Prevent post-hoc changes and preserve decision history (AC: 1-3)
  - [ ] Any threshold, family, version, endpoint, scope, price selection, or language-policy change requires a new protocol/report/claim identity.
  - [ ] Never overwrite prior reports or decision records; append superseding lineage with rationale that is independent of outcome favorability.
- [ ] Add report golden, claim-lint, scope, threshold, lineage, and categorical-override tests (AC: 1-3).

## Developer Context

The report is a projection of immutable analysis decisions, not a place to reanalyze data or choose thresholds. Causal language must follow assignment and tested scope. Exposure/per-protocol, complete-case, exploratory, pilot, engine, and sensitivity results are visibly labeled and cannot replace confirmatory ITT. Positive, null, harm, and indeterminate are equally valid terminal conclusions.

### Architecture and Claim Guardrails

- Follow AD-03, AD-05, AD-07, AD-13, AD-15, AD-16, AD-22, AD-23, AD-24.
- Apply the frozen thresholds from Story 5.4 exactly; no post-hoc relaxation, family switching, favorable cost view, or endpoint salvage.
- Engine results always say `engine upper bound`; pilot is non-confirmatory; cross-repository claims remain prohibited.

### File Structure Requirements

- **UPDATE:** `evaluation/src/memrelay_eval/analysis/reports.py`
- **NEW:** `evaluation/src/memrelay_eval/analysis/claims.py`
- **UPDATE:** `evaluation/src/memrelay_eval/analysis/{queries,gates,safety}.py`
- **UPDATE:** `evaluation/src/memrelay_eval/cli/{main,commands}.py`
- **NEW/UPDATE:** report/claim/decision schemas and frozen templates under `evaluation/`
- **NEW:** tests under `evaluation/tests/{unit,contract,golden}/reports/`
- **OUTPUT ONLY:** immutable local artifacts under `evaluation/artifacts/reports/<report-id>/`

### Existing Behavior to Preserve

- Preserve all source analyses, prices, claims, failures, categorical blocks, and prior report bytes.
- Preserve provider/cost/time distinctions, ITT denominators, unavailable values, and simultaneous-bound decision authority.
- Preserve protected evidence access; reports link governed artifacts without embedding restricted bodies.

### Testing Requirements

- Golden reports cover positive, null, harmful, indeterminate, estimation-only, panel-blocked, low-power, missingness-sensitive, and categorical-blocked outcomes.
- Promotion tests prove a missing/failed Story 5.6 reproduction record forces `draft/unverified` labeling and blocks release fitness without preventing local draft rendering.
- Claim lint rejects overbroad product/causal/safety/economic language, engine-to-product promotion, pilot confirmation, and missing scope/lineage.
- Boundary tests exercise exact threshold equality and prove strict operators.
- Re-rendering identical immutable inputs produces exact output hashes.

### Anti-Patterns

- Do not recompute statistics in templates, choose “best” price/estimand/sensitivity, hide failed/attrited units, or present per-protocol as randomized.
- Do not call fixtures efficacy, engine results product outcomes, pilot confirmatory, zero events safe, or a positive subgroup a broad release pass.
- Do not weaken thresholds, overwrite decisions, omit null/harm results, or add an interactive/cloud reporting platform.

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` — Epic 5 / Story 5.7; FR64-FR65]
- [Source: `ARCHITECTURE-SPINE.md` — AD-03, AD-05, AD-15-AD-16; Implementation Freeze]
- [Source: `IMPLEMENTATION-DESIGN.md` — §§2, 11, 18-19, 24.2-24.5]
- [Source: technical research — claim ladder; bounded safety language; cost estimands; categorical release blockers]
- [Source: `_bmad-output/test-artifacts/test-design-qa.md` — `SAFETY-LANGUAGE`, `REPRO-*`, release evidence boundaries]
- [Source: predecessor Stories 4.7 and 5.2-5.6]

## Dev Agent Record

### Agent Model Used

TBD by implementation agent

### Debug Log References

### Completion Notes List

- Ultimate context engine analysis completed - comprehensive developer guide created

### File List
