# Story 6.5: Run Primary and Secondary Model Stages

Status: ready-for-dev

## Story

As a research lead,
I want fixed primary and secondary envelopes analyzed as separate model strata,
so that confirmatory and generalization claims obey their planned populations.

## Acceptance Criteria

1. **Given** accepted pilot gates and locked primary protocol/holdout hashes  
   **When** the primary stage runs  
   **Then** exactly 512 assigned units across 32 tasks are retained in complete ITT  
   **And** exit requires simultaneous intervals, harm tails, Pareto surface, panel and safety results, and explicit claim-gate decisions.
2. **Given** primary evidence is reconciled and qualified `M1` or `M2` roles exist  
   **When** secondary generalization runs  
   **Then** it enrolls 96 units per available role, never more than 192 total, with each role a separate model stratum  
   **And** an unavailable role is recorded and never substituted.
3. **Given** a primary categorical blocker or frozen power below `0.80`  
   **When** stage conclusions are issued  
   **Then** the affected claim family stops or the result is estimation-only, respectively  
   **And** enrollment does not silently expand and secondary evidence cannot repair the primary claim.

## Tasks / Subtasks

- [ ] Seal and independently authorize the primary plan (AC: 1, 3)
  - [ ] Require accepted pilot gates; lock primary protocol, holdout, catalog/config/model/price, 32 tasks, 512 assignments, endpoints, analysis, thresholds, panel/safety, and limits.
  - [ ] Materialize exactly `32 tasks (4 per F1-F8) × R-COP-M0 × 2 arms × 8 repeats = 512` opaque ITT units with bounded, arm-balanced order/concurrency; reject additions or replacements.
- [ ] Execute/resume the primary without selection (AC: 1, 3)
  - [ ] Preserve all failures, attrition, capped outcomes, unavailable fields, exposure, attempts, and costs in ITT.
  - [ ] Make start/resume idempotent: identical repeated requests reuse sealed assignments and receipts, while conflicts fail closed; resume only never-started units under identical locks.
  - [ ] Drift or post-seal changes require a new protocol/stage and cannot expand enrollment.
- [ ] Reconcile, analyze, and decide primary claims (AC: 1, 3)
  - [ ] Require 100% primary evidence completeness, simultaneous intervals, harm tails, Pareto surface, panel/safety results, and explicit bounded claim decisions.
  - [ ] A categorical blocker stops its affected family; power below `0.80` yields estimation-only, not extra enrollment or threshold relaxation.
  - [ ] Enforce the frozen final-information look: operational safety/budget monitoring remains available, but efficacy output is denied until terminal reconciliation and analysis lock.
- [ ] Plan each secondary role as a separate stage/stratum (AC: 2, 3)
  - [ ] After reconciled primary evidence and separate authorization, enroll exactly `16-task balanced subset × one qualified role × 2 arms × 3 repeats = 96` units for each actually qualified `M1`/`M2`; cap total at 192.
  - [ ] Record unavailable roles without substitution; retain distinct assignments, protocols, analyses, evidence, reports, costs, and claims per model stratum.
  - [ ] Prevent secondary, subgroup, sensitivity, engine, or exploratory evidence from repairing a blocked/underpowered primary claim.
- [ ] Apply paid-call envelopes, monitoring, and runbooks (AC: 1-3)
  - [ ] Primary: hard 128M task-agent tokens, separate AI-credit cap, 24M framework input, 8M framework output plus USD cap, task-class active caps, ten elapsed days, concurrency four.
  - [ ] Secondary: hard 48M task-agent tokens across roles with per-role subcaps, 9M framework input, 3M output plus USD cap, five additional days; no unused-role budget transfer without a new authorization.
  - [ ] Monitor outcome-blind ITT/start/terminal counts, model availability/drift, stratum balance, frozen power-policy status, panel completion, safety signals, evidence, cost/time/quota, backups, and categorical alerts; do not expose interim efficacy or claim results. Pause starts and preserve/drain active attempts; never outcome-select a resume.
- [ ] Add primary/secondary boundary and preservation tests (AC: 1-3)
  - [ ] Test 511/512/513, 0/1/2 secondary roles, 96/192/193, unavailable/drifted models, low power, blockers, incomplete evidence, resume, and cross-stratum pooling rejection.

## Dependencies

These are the exact formal story dependencies; accepted pilot gates are the additional operational primary-entry requirement stated in AC1.

- Story 2.1: qualified locked `M0/M1/M2` roles.
- Story 5.4: frozen power, multiplicity, interval, and claim gates.
- Story 5.5: safety, necessity, contamination, and categorical overrides.
- Story 6.1: stage bundle and CLI enforcement.
- Operational primary entry additionally requires the accepted pilot gates specified by AC1.

## Dev Notes

### Developer Context

Primary and secondary stages are separate authorities and populations even though this story implements both. Primary is exactly 512 assigned units/32 tasks and complete ITT. Secondary is optional generalization, exactly 96 units per available qualified role, maximum 192. Neither missing models nor unfavorable primary evidence may change those envelopes. Independent authorization is required for primary and again for each secondary stage.

No promotion comes from exploratory results, process completion, nominal intervals, or secondary success. Claims remain bounded to tested model, population, product/engine stratum, protocol, and history regime.

Current-checkout fact: no `evaluation/` tree exists and all predecessor stories are ready-for-dev guides, not implemented learnings. Implement only after those seams and accepted pilot gates exist; existing product source/tests are read-only bounded evidence and cannot authorize primary or secondary enrollment.

### Architecture and Preservation

- Apply final frozen inference: familywise alpha `0.05` with Holm, power `0.80`, benefit `+0.05` with simultaneous lower bound `>0`, no-regression lower bound `>-0.02`, and cost/wall ratio `<=0.90` with simultaneous upper bound `<1.0`.
- Verify the Story 6.1 frozen runtime/version lock unchanged: Python `3.11`, Copilot SDK `1.0.8` plus its frozen wheel digest, Inspect `0.3.252`, OTel `1.44.0`, Collector `0.158.0` plus archive digest, OpenInference `0.1.31`, OpenAI instrumentation `0.1.53`, DuckDB `1.5.5`, PyArrow `25.0.0`, framework model `gpt-4.1-mini-2025-04-14`, and local embedding model `BAAI/bge-small-en-v1.5`.
- Preserve complete ITT, model/history/product strata, immutable locks, separate provider/cost ledgers, categorical overrides, Parquet-only analysis, and append-only claim decisions.
- A changed/absent model pauses; no public-name inference, fallback, role substitution, mid-block rescheduling, or pooling.
- The 24-cluster cross-repository envelope is not part of primary/secondary. It remains mechanically denied until primary completion and all DG-R governance controls qualify.

### File Targets

- **UPDATE:** `evaluation/src/memrelay_eval/orchestration/stages.py`, `limits.py`, `control.py`
- **UPDATE:** `evaluation/src/memrelay_eval/analysis/{estimands,estimators,gates,claims,safety,queries,reports}.py` only where needed to bind existing predecessor analysis to immutable stage/stratum inputs.
- **UPDATE:** `evaluation/src/memrelay_eval/evidence/reconcile.py`, `parquet.py`
- **UPDATE:** `evaluation/src/memrelay_eval/cli/commands.py`
- **NEW:** `evaluation/tests/unit/orchestration/test_primary_secondary_plans.py`
- **NEW:** `evaluation/tests/contract/test_primary_secondary_exit_gates.py`
- **NEW:** `evaluation/tests/fault/test_model_stage_pause_resume.py`
- **NEW:** `evaluation/docs/runbooks/primary-secondary.md`

### Testing and Anti-Pattern Guardrails

- Property-test exact cardinality, strata separation, complete ITT, and strict threshold operators.
- Golden reports include positive, null, harmful, indeterminate, estimation-only, panel-blocked, low-power, and categorically blocked outcomes.
- Preserve exact TEA stable IDs and verdicts in generated traceability: the registered one of `STAT-MODE-RELIABILITY`, `STAT-MODE-EFFICIENCY`, or `STAT-MODE-DUAL`; `STAT-MULTIPLICITY`; `STAT-INTERVAL-COMPAT`; `STAT-FIXED-LOOK`; `STAT-POWER-SIM`; `OPS-STAGE-ENVELOPE`; `MODEL-UNAVAILABLE-PAUSE`; `ITT-AGENT-FAILURE`; `ITT-TIMEOUT`; `ITT-GRADER-FAILURE`; `ITT-INFRA-PRE`; `ITT-INFRA-POST`; `ITT-MISSING-PRIMARY`; `ITT-NO-FAVORABLE-SUB`; `SAFETY-EXPOSURE`; `SAFETY-ASCERTAINMENT`; `SAFETY-SENSITIVITY`; `SAFETY-UPPER-BOUND`; `SAFETY-LANGUAGE`; and `COST-PROVIDER-LEDGERS`.
- Do not expand N, substitute models, pool M1/M2/M0, drop failures, use complete-case/per-protocol estimates, allow secondary repair, choose favorable sensitivity/price views, auto-promote, or start cross-repository work.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story-6.5-Run-Primary-and-Secondary-Model-Stages]
- [Source: _bmad-output/planning-artifacts/architecture/architecture-dfinson-solid-happiness-2026-08-05/IMPLEMENTATION-DESIGN.md#24.2-Fixed-Statistical-and-Claim-Policy]
- [Source: _bmad-output/planning-artifacts/architecture/architecture-dfinson-solid-happiness-2026-08-05/IMPLEMENTATION-DESIGN.md#24.3-Stage-Entry-Exit-and-Stop-Rules]
- [Source: _bmad-output/test-artifacts/test-design-qa.md#Estimator-assignment-clustering-and-intervals]
- [Source: _bmad-output/planning-artifacts/research/technical-evaluating-memrelay-collective-recall-for-coding-agent-reliability-cost-and-efficiency-research-2026-08-04.md#Staged-run-dual-provider-budget-tool-and-wall-clock-envelope]

## Dev Agent Record

### Agent Model Used

To be recorded by the implementing dev agent.

### Debug Log References

### Completion Notes List

- Ultimate context engine analysis completed - comprehensive developer guide created

### File List
