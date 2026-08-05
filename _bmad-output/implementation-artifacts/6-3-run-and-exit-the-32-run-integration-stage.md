# Story 6.3: Run and Exit the 32-Run Integration Stage

Status: ready-for-dev

## Story

As a study operator,
I want the fixed integration envelope enforced and reconciled,
so that infrastructure failures are discovered before pilot outcomes exist.

## Acceptance Criteria

1. **Given** an accepted conformance bundle hash  
   **When** integration starts  
   **Then** exactly 32 runs are planned as 8 synthetic scenarios across `YL` and `TR` with 2 repeats  
   **And** run order and concurrency are bounded, arm-balanced, and recorded.
2. **Given** all integration attempts terminate  
   **When** the exit gate runs  
   **Then** at least 30 of 32 attempts are infrastructure-complete, every terminal attempt has complete reconciled evidence, and categorical blockers are zero  
   **And** the immutable exit bundle includes run, reconciliation, backup, parity, cost, and fault summaries.
3. **Given** fewer than 30 infrastructure-complete attempts, incomplete terminal evidence, or any categorical blocker  
   **When** integration closes  
   **Then** the stage fails and the entire 32-run stage must be rerun under a new stage ID after repair  
   **And** no favorable subset advances.

## Tasks / Subtasks

- [ ] Seal and authorize the integration plan (AC: 1)
  - [ ] Require an independently accepted conformance exit and immutable catalog/config/model/price/limit locks.
  - [ ] Materialize exactly `8 synthetic scenarios × R-COP-M0 × YL/TR × 2 repeats = 32` opaque planned runs; reject duplicate/missing/extra cells.
  - [ ] Precompute an arm-balanced order and bounded concurrency schedule without treatment-revealing operator views.
- [ ] Execute with idempotent pause/resume (AC: 1, 2)
  - [ ] Persist planned/start/terminal receipts before advancing; resume starts only never-started planned runs.
  - [ ] Preserve terminal/active evidence and apply only the single authorized conclusively pre-exposure retry policy; retries do not change the 32 assigned-run denominator.
- [ ] Reconcile and decide the exit (AC: 2, 3)
  - [ ] Count infrastructure-complete attempts with the frozen classifier; require `>=30/32`, complete reconciled terminal evidence for all attempts, and zero categorical blockers.
  - [ ] Seal run/reconciliation/backup/parity/cost/fault summaries and a typed accepted/rejected decision.
  - [ ] On failure, reject the whole stage; repair requires a fresh stage ID and complete 32-run rerun.
- [ ] Enforce bounded paid operation and runbooks (AC: 1-3)
  - [ ] Freeze hard caps: 3.2M task-agent tokens, separate AI-credit formula/result, 0.6M framework input, 0.2M framework output plus USD cap, per-run tool caps, 15 active minutes/run, and 8 local elapsed hours.
  - [ ] Monitor treatment-neutral plan/start/terminal counts, order/concurrency balance, quota/throttle/model state, cost/time headroom, infrastructure failures, reconciliation, backup, and evidence loss without exposing efficacy outcomes.
  - [ ] Alerts pause new starts; runbook preserves/drains active attempts, diagnoses blind to outcomes, repairs, rejects the old stage, and requests fresh authorization.
- [ ] Add plan, gate, fault, and recovery tests (AC: 1-3)
  - [ ] Include 29/30/32 boundaries, incomplete evidence, each categorical blocker, schedule imbalance, duplicate resume, cap trip, interruption, and favorable-subset rejection.

## Dependencies

This is the exact formal dependency from the epic graph.

- Story 6.2: accepted bootstrap/conformance bundle.

## Dev Notes

### Developer Context

Integration is infrastructure evidence, not product efficacy. Its declared entry is an accepted conformance bundle; exit is at least 30 infrastructure-complete attempts, complete evidence for every terminal attempt, and zero categorical blockers. Process success or 30 favorable outcomes is insufficient. An old rejected stage is immutable and cannot be resumed into acceptance after repair.

Current-checkout fact: no `evaluation/` tree exists and all predecessors are ready-for-dev guides, not implemented learnings. Implement only after Story 6.2 and its transitive contracts exist; current product source and regression tests remain read-only and cannot substitute for the 32-run evaluator stage.

### Architecture and Preservation

- Reuse Story 6.1 stage/bundle/authorization and the established `orchestration/limits.py` seam from predecessor runtime/process work; Story 6.6 later consolidates breaker behavior and is not a prerequisite.
- Verify the frozen runtime lock rather than selecting versions: Python `3.11`, Copilot SDK `1.0.8` with wheel digest `7c3d868b73daa3a154ae6c1c5a2bd9301c47349c6b080f1ed77ba9027da3e0fa`, Inspect `0.3.252`, OTel `1.44.0`, Collector `0.158.0` with its frozen archive digest, OpenInference `0.1.31`, OpenAI instrumentation `0.1.53`, DuckDB `1.5.5`, and PyArrow `25.0.0`.
- Preserve opaque assignments, arm concealment, fixed ITT denominator, bounded retries, separate cost ledgers, local evidence/backup, and fail-closed reconciliation.
- All frozen-version and configuration drift pauses new starts. Resume requires identical locks; drift creates a new protocol/stage.
- Cross-repository adapters remain unreachable; the 24-cluster stage belongs after primary plus DG-R qualification only.

### File Targets

- **UPDATE:** `evaluation/src/memrelay_eval/orchestration/stages.py`, `limits.py`, `control.py`
- **UPDATE:** `evaluation/src/memrelay_eval/cli/commands.py`
- **NEW:** `evaluation/tests/unit/orchestration/test_integration_plan.py`
- **NEW:** `evaluation/tests/contract/test_integration_exit_gate.py`
- **NEW:** `evaluation/tests/fault/test_integration_pause_resume.py`
- **NEW:** `evaluation/tests/golden/integration_exit_bundle.json`
- **NEW:** `evaluation/docs/runbooks/integration.md`

### Testing and Anti-Pattern Guardrails

- Assert exact cardinality/cell balance and deterministic schedules from sealed inputs.
- Fault-inject every boundary around start receipts, terminal records, reconciliation, backup, and exit publication.
- Preserve TEA stable coverage IDs and verdicts in generated traceability: `OPS-STAGE-ENVELOPE`, `OPS-QUOTA-RATE-LIMIT`, `ARM-PARITY`, `TEL-FAILURE`, `TEL-DROP`, `ITT-INFRA-PRE`, `ITT-INFRA-POST`, and `ITT-NO-FAVORABLE-SUB`.
- Do not treat repeats as replacement opportunities, drop capped/failed runs, rerun only unfavorable cells, auto-promote, decode treatment for scheduling, relax `30/32`, or relabel integration as pilot/efficacy evidence.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story-6.3-Run-and-Exit-the-32-Run-Integration-Stage]
- [Source: _bmad-output/planning-artifacts/architecture/architecture-dfinson-solid-happiness-2026-08-05/IMPLEMENTATION-DESIGN.md#24.3-Stage-Entry-Exit-and-Stop-Rules]
- [Source: _bmad-output/planning-artifacts/research/technical-evaluating-memrelay-collective-recall-for-coding-agent-reliability-cost-and-efficiency-research-2026-08-04.md#Staged-run-dual-provider-budget-tool-and-wall-clock-envelope]
- [Source: _bmad-output/test-artifacts/test-design-qa.md#Corrected-runtime-credential-telemetry-cost-and-stage-contract]

## Dev Agent Record

### Agent Model Used

To be recorded by the implementing dev agent.

### Debug Log References

### Completion Notes List

- Ultimate context engine analysis completed - comprehensive developer guide created

### File List
