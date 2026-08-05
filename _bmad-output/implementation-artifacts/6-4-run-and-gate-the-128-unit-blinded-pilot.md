# Story 6.4: Run and Gate the 128-Unit Blinded Pilot

Status: ready-for-dev

## Story

As a research lead,
I want a fixed blinded pilot that estimates operating characteristics without entering confirmation,
so that variance and panel defects are learned without weakening thresholds.

## Acceptance Criteria

1. **Given** an accepted integration exit bundle  
   **When** the pilot starts  
   **Then** exactly 128 assigned units across 16 tasks use frozen assignments, holdouts, blinding, panel, evidence, budget, and analysis contracts  
   **And** pilot data are permanently marked non-confirmatory.
2. **Given** pilot completion  
   **When** exit gates run  
   **Then** overall evidence completeness is at least 98%, panel and blinding gates pass, and variance, ICC, attrition, harm, and frozen power simulation are published  
   **And** pilot outcomes cannot weaken thresholds or enter the primary confirmatory estimate.
3. **Given** evidence completeness below 98% or a failed panel, blinding, security, governance, grading, evidence, or causal gate  
   **When** pilot closes  
   **Then** repair requires a fresh 128-unit pilot under a new stage ID  
   **And** no subset or regraded favorable result advances.

## Tasks / Subtasks

- [ ] Seal and independently authorize the blinded pilot (AC: 1)
  - [ ] Require the accepted integration exit and freeze exactly 16 tasks, 128 assignments, holdouts, assignment seed/blocks, blinding transform, panel/rubric, evidence matrix, limits, analysis, and price/model/catalog/config hashes.
  - [ ] Permanently stamp every pilot artifact, dataset, analysis, and report `non-confirmatory`.
- [ ] Execute the fixed plan with pause/resume safety (AC: 1)
  - [ ] Preserve assignment-unit ITT; sequences may contain 2-3 sessions but remain one assigned/inferential unit.
  - [ ] Make start/resume idempotent: identical repeated requests reuse the sealed plan and receipts, while conflicts fail closed; resume only never-started units after lock and evidence verification.
  - [ ] Retain all attempts, attrition, failures, exposure, and costs.
- [ ] Calculate and seal pilot exits without outcome-aware promotion (AC: 2, 3)
  - [ ] Require 100% of mandatory primary fields plus overall evidence completeness `>=98%`, weighted kappa/ICC `>=0.70`, calibration MAE `<=0.10`, and blinded classifier 95% upper AUC `<=0.60`; the aggregate threshold never waives a missing required item or categorical blocker.
  - [ ] Publish variance, ICC, attrition, harm, and the frozen enumerable power simulation while blinded; run every retained registered cell for at least 10,000 trials with fixed seeds and an independent spot-check.
  - [ ] Deny decoded efficacy and interim efficacy monitoring; do not tune effects, thresholds, families, simulation cells, or tasks, select favorable evidence, or reuse pilot outcomes in confirmation.
  - [ ] Any listed gate failure rejects the entire stage; repair requires a fresh 128-unit stage and independent authorization.
- [ ] Apply bounded paid-call monitoring and runbooks (AC: 1-3)
  - [ ] Freeze hard caps: 32M task-agent tokens, separate AI-credit cap, 6M framework input, 2M framework output plus USD cap, task-class active-time caps, five local elapsed days, concurrency two.
  - [ ] Monitor assignment/session counts, blind integrity, panel completion/drift, evidence completeness, attrition/harm, cost/time/quota, backups, and categorical alerts.
  - [ ] Alert response pauses starts, preserves active evidence, keeps operators blinded, repairs independently, rejects the old pilot, and requires fresh authorization.
- [ ] Test pilot cardinality, gates, labeling, and recovery (AC: 1-3)
  - [ ] Exercise 97.99/98% completeness, exact panel thresholds, leakage, missing panel records, interrupted resume, multi-session unit counting, and anti-salvage behavior.

## Dependencies

These are the exact formal story dependencies; the accepted Story 6.3 exit is the additional operational entry gate stated in AC1.

- Story 3.4: calibrated panel reliability gate.
- Story 5.4: frozen multiplicity, power, and claim thresholds.
- Story 6.1: stage bundle and CLI enforcement.
- Operational entry additionally requires the accepted integration exit specified by AC1.

## Dev Notes

### Developer Context

The pilot estimates operating characteristics only. It cannot authorize itself, weaken frozen thresholds, provide confirmatory efficacy, or contribute to the primary estimate. The final architecture freezes alpha `0.05`, target power `0.80`, benefit margin `+0.05`, non-inferiority `-0.02`, and cost/wall ratio `0.90`; these final decisions supersede earlier research text that left owner thresholds open.

Current-checkout fact: no `evaluation/` tree exists and predecessor stories are ready-for-dev guides, not implemented learnings. Implement only after their contracts and the accepted integration exit exist; current product regression evidence cannot satisfy pilot readiness or completion.

### Architecture and Preservation

- Reuse the canonical stage scheduler, bundles, reconciliation, panel gate, power simulator, and report labeling. Never duplicate statistics in orchestration.
- Verify the Story 6.1 frozen runtime/version lock unchanged: Python `3.11`, Copilot SDK `1.0.8` plus its frozen wheel digest, Inspect `0.3.252`, OTel `1.44.0`, Collector `0.158.0` plus archive digest, OpenInference `0.1.31`, OpenAI instrumentation `0.1.53`, DuckDB `1.5.5`, PyArrow `25.0.0`, framework model `gpt-4.1-mini-2025-04-14`, and local embedding model `BAAI/bge-small-en-v1.5`.
- Locks include catalog/config/model/price plus holdout, task, assignment, rubric, judge, transform, endpoint, analysis, and limit versions.
- Missing evidence fails closed; no regrading, complete-case subset, per-protocol replacement, favorable task selection, or decoded repair.
- Paid execution is explicit and local; cross-repository work stays denied until DG-R after primary.

### File Targets

- **UPDATE:** `evaluation/src/memrelay_eval/orchestration/stages.py`, `control.py`, `limits.py`
- **UPDATE:** `evaluation/src/memrelay_eval/analysis/{power,gates,queries,reports}.py` only for frozen stage-aware simulation/gate wiring; do not duplicate predecessor statistical logic in orchestration.
- **UPDATE:** `evaluation/src/memrelay_eval/cli/commands.py`
- **NEW:** `evaluation/tests/unit/orchestration/test_pilot_plan.py`
- **NEW:** `evaluation/tests/contract/test_pilot_exit_gate.py`
- **NEW:** `evaluation/tests/fault/test_pilot_blinding_and_resume.py`
- **NEW:** `evaluation/docs/runbooks/pilot.md`

### Testing and Anti-Pattern Guardrails

- Test strict comparison operators and exact threshold boundaries.
- Verify every output and lineage path retains non-confirmatory classification.
- Preserve TEA stable coverage IDs and objective verdicts in generated traceability: `PILOT-READINESS`, `PILOT-COMPLETION`, `STAT-POWER-SIM`, `STAT-FIXED-LOOK`, `BLIND-TRANSFORM`, `BLIND-LEAK-CLASSIFIER`, `ARM-MI-ENVELOPE`, `OPS-STAGE-ENVELOPE`, and `ITT-NO-FAVORABLE-SUB`.
- Do not count sessions as independent units, decode treatment, tune simulation cells after results, transfer pilot estimates into primary outcomes, auto-promote, replace failed units, or allow exploratory evidence to satisfy a gate.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story-6.4-Run-and-Gate-the-128-Unit-Blinded-Pilot]
- [Source: _bmad-output/planning-artifacts/architecture/architecture-dfinson-solid-happiness-2026-08-05/IMPLEMENTATION-DESIGN.md#24.2-Fixed-Statistical-and-Claim-Policy]
- [Source: _bmad-output/test-artifacts/test-design-qa.md#Blinded-pilot-and-enumerable-simulation]
- [Source: _bmad-output/planning-artifacts/research/technical-evaluating-memrelay-collective-recall-for-coding-agent-reliability-cost-and-efficiency-research-2026-08-04.md#Development-Workflows-and-Tooling]

## Dev Agent Record

### Agent Model Used

To be recorded by the implementing dev agent.

### Debug Log References

### Completion Notes List

- Ultimate context engine analysis completed - comprehensive developer guide created

### File List
