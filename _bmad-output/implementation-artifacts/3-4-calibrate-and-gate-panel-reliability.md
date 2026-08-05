# Story 3.4: Calibrate and Gate Panel Reliability

Status: ready-for-dev

## Story

As a research lead,
I want prospectively frozen calibration, agreement, drift, and bias checks,
so that unreliable panel scores cannot support qualitative claims.

## Acceptance Criteria

1. **Given** the frozen panel protocol  
   **When** panel qualification and each stage run  
   **Then** human-labeled calibration items, duplicate items, sentinels, per-criterion agreement, drift, leave-one-judge-out, and generator-versus-judge-family sensitivity are computed  
   **And** calibration items and thresholds cannot be changed after outcome access.
2. **Given** panel results  
   **When** reliability gates are evaluated  
   **Then** weighted kappa or ICC must be at least `0.70`, human-calibration MAE at most `0.10`, and blinded arm-classifier 95% upper AUC bound at most `0.60`  
   **And** failure blocks confirmatory qualitative claims without changing executable outcomes.
3. **Given** a homogeneous or partially homogeneous panel  
   **When** its quality is assessed  
   **Then** the prospectively frozen stronger human-calibration threshold and shared-bias sensitivity analysis are applied  
   **And** missing diversity is reported rather than filled with an unqualified model.

## Dependencies and Prerequisites

- **Authoritative direct graph dependency:** Story 3.3 only, exactly as declared in `epics.md`. Story 3.2 classifier/blinding evidence and Story 3.1 executable outcomes arrive transitively; do not add graph edges or redefine their contracts.
- Human labels, calibration set, sentinel/duplicate schedule, diversity classification, metrics, and all thresholds must be sealed before study outcome access.
- This story gates Story 3.6 qualitative normalization; it never modifies executable outcomes.
- Exact traceability: FR35; NFR11, NFR26, NFR37, NFR45; AR20, AR31, AR48.

## Tasks / Subtasks

- [ ] Define and seal the panel qualification protocol (AC: 1)
  - [ ] Hash human gold labels/provenance, calibration items, duplicates, sentinels, allocation/order, metric selection, drift windows, sensitivity methods, and thresholds.
- [ ] Compute criterion-appropriate agreement and human calibration (AC: 1, 2)
  - [ ] Use weighted kappa or ICC as frozen; require `>=0.70`; compute human-label MAE and require `<=0.10`.
- [ ] Compute drift, sentinel, duplicate, leave-one-judge-out, and family sensitivity evidence (AC: 1)
  - [ ] Preserve per-criterion/per-judge results and missing/unavailable values; do not average away a failed frozen gate.
- [ ] Bind Story 3.2 leakage evidence into panel qualification (AC: 2)
  - [ ] Require classifier 95% upper AUC `<=0.60` and zero direct leakage; treat failure as a hard qualitative-claim blocker.
- [ ] Enforce partial/homogeneous-panel policy (AC: 3)
  - [ ] Require a protocol-supplied stronger human-calibration threshold strictly below `0.10` plus a versioned shared-bias sensitivity rule; the final architecture supplies no numeric stronger default, so missing/unsealed values fail closed and must never be invented from outcomes.
- [ ] Emit immutable pass/fail gate evidence and enforce non-overridability (AC: 1-3)
  - [ ] A failed agreement, calibration, drift, sentinel, leakage, or shared-bias gate blocks confirmatory qualitative claims; favorable averages/adjudication cannot waive it; executable outcomes stay unchanged.
  - [ ] Require protocol-supplied frozen pass rules for drift, duplicates, sentinels, leave-one-out, and family sensitivity. Missing results or thresholds are blocked/unavailable, not silently passing.
- [ ] Add deterministic metric, boundary, drift, sentinel, missingness, diversity, and freeze tests (AC: 1-3).

## Developer Context

Panel reliability is a collection of explicit gates, not one blended score. Human calibration is required, not optional decoration. Sentinel and duplicate failures, drift, family sensitivity, and shared-bias evidence remain visible. A panel that lacks diversity is not backfilled with unqualified models; it must satisfy the stronger threshold already frozen in the protocol. Never derive or relax thresholds after viewing outcomes.

### Architecture and File Requirements

- Follow AD-05, AD-12, AD-13, AD-15, AD-17, and the frozen panel gate in Implementation Design §24.2.
- Expected paths:
  - **UPDATE:** `evaluation/src/memrelay_eval/domain/{entities,policies,errors}.py`
  - **NEW:** `evaluation/src/memrelay_eval/scoring/{calibration,reliability}.py`
  - **UPDATE:** `evaluation/src/memrelay_eval/scoring/service.py`
  - **NEW:** `evaluation/schemas/panel-gate.schema.json`
  - **NEW:** `evaluation/tests/{unit,contract}/scoring/test_{calibration,reliability}.py`
- `evaluation/` is absent at authoring time. Extend predecessor immutable records; no product/root-manifest changes.

### Library, Test, and Version Guardrails

- Python 3.13. Prefer explicit, deterministic local statistics with frozen formulas; do not add an unapproved analytics/provider dependency. This story makes zero provider/session calls.
- CI uses synthetic records and no network/provider calls. Test exact threshold boundaries, criterion metric selection, order invariance, missing judges, drift windows, sentinel failures, classifier confidence bounds, and homogeneous-panel stronger gates.
- Any calibration corpus/gold label, threshold, metric, window, diversity rule, sentinel, or sensitivity change requires a new protocol version.

### Preserved Behavior and Anti-Patterns

- Preserve individual judge records, sealed order, blinded views, model/runtime hashes, executable results, and categorical blockers.
- Do not tune gold labels, drop difficult duplicates, pool criteria with incompatible scales, use average agreement to hide a failed required criterion, substitute a model, or let adjudication repair failed panel qualification.
- Do not make provider calls here; do not claim qualitative confirmation from fake artifacts or before Story 4.1 CAS qualification inherited from Story 3.3. Preserve the inherited fake-ledger/fake-telemetry barrier as well.

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` — Story 3.4; FR35; NFR11]
- [Source: `ARCHITECTURE-SPINE.md` — AD-13; Implementation Freeze]
- [Source: `IMPLEMENTATION-DESIGN.md` — §§19.3, 19.5-19.6, 24.2-24.3]
- [Source: technical research — calibration, agreement/error, sentinel and sensitivity guidance]
- [Source: `_bmad-output/test-artifacts/test-design-qa.md` — `BLIND-CALIBRATE`, `BLIND-GUESS`, `BLIND-SENSITIVITY`, `BLIND-LEAKAGE-BOUND`]
- [Source: `_bmad-output/test-artifacts/test-design-architecture.md` — R-012]
- [Source: predecessor Story 3.3 — three fresh immutable judge records and diversity labeling]

## Dev Agent Record

### Agent Model Used

TBD by implementation agent

### Debug Log References

### Completion Notes List

- Ultimate context engine analysis completed - comprehensive developer guide created

### File List
