# Story 4.5: Reconcile Required Evidence and Decide Inclusion

Status: ready-for-dev

## Story

As an evidence operator,
I want expected evidence reconciled against independent native authorities,
So that only complete, unconflicted terminal runs enter analysis.

## Acceptance Criteria

1. **Given** a terminal run  
   **When** reconciliation executes  
   **Then** expected telemetry classes are compared with SDK events, Inspect records, memrelay logs, grader/judge records, artifact writes, cost records, and ledger transitions  
   **And** OTLP delivery alone never proves completeness.
2. **Given** the required-evidence matrix  
   **When** `memrelay-eval reconcile --stage <stage>` runs  
   **Then** it verifies assignment/lifecycle, `.eval`, JSON, SDK, telemetry, workspace/patch, treatment, grading, panel/calibration/adjudication, costs or explicit unavailable values, configuration, model/parity, cleanup, transitions, and every hash  
   **And** it appends exactly one immutable included or excluded decision with reconciliation hash and typed reason.
3. **Given** missing primary evidence, corruption, credential leak, unauthorized disclosure, contamination, hidden-test tamper, grading conflict, or causal-validity conflict  
   **When** inclusion is evaluated  
   **Then** it fails closed and blocks the applicable run or stage  
   **And** no aggregate score can override the categorical blocker.

## Dependencies and Prerequisites

- **Authoritative direct graph dependencies:** Stories 3.6, 4.1, 4.2, and 4.4 only, exactly as declared in `epics.md`. Story 4.3 is transitively required through 4.4.
- Those predecessors provide outcome authority, qualified CAS, the sole-writer ledger, and identity validation.
- Consume native evidence from Story 2.10 and grader/panel/calibration/adjudication records from Epic 3 without flattening their authorities.
- Reconciliation may be developed with deterministic fakes, but an included study decision is forbidden until every applicable durable adapter passes conformance.
- Story 4.6 is not a forward dependency. Reconcile the applicable native cost/usage artifacts or matrix-permitted explicit `unavailable` values here; normalized economic analysis remains ineligible until Stories 4.6 and 4.7 pass.
- This story creates immutable inclusion records only. Atomic Parquet publication and read-only DuckDB analysis belong to Stories 5.1/5.2 and must remain blocked on these decisions.
- Exact traceability: FR41, FR43, FR44; NFR14, NFR30-NFR32, NFR45; AR29, AR40.

## Tasks / Subtasks

- [ ] Define a versioned required-evidence matrix (AC: 1, 2)
  - [ ] Key requirements by stage, stratum, history mode, task/failure state, provider path, and applicable grader/panel/adjudication conditions.
  - [ ] Classify fields/evidence as primary-required, conditionally required, explicit `unavailable` permitted, or prohibited.
  - [ ] Require 100% primary evidence for inclusion; pilot overall completeness threshold never waives a primary item or categorical blocker.
  - [ ] Include the zero-missing primary inventory: experiment/run/task/replicate/history/sequence/repository/model IDs; opaque assignment and hash; workspace/image/revision/prompt/tool-policy/budget/grader hashes; start/terminal timestamps and terminal class; executable outcome; patch hash or explicit `no_patch`; attempt/retry lineage; exclusion/quarantine reason; expected-artifact inventory; and reconciliation status.
- [ ] Build cross-authority reconciliation (AC: 1)
  - [ ] Compare Inspect `.eval`/JSON, native SDK events/terminal, memrelay logs, OTel classes, workspace/snapshot/patch, treatment, grader/judge records, artifact writes, cost quantities, configuration/model/parity/environment fingerprints, cleanup, and ledger transitions.
  - [ ] Verify every referenced CAS digest/manifest and preserve contradictory source references.
  - [ ] Treat OTLP success as transport evidence only.
- [ ] Implement deterministic reconciliation reports and identity (AC: 1, 2)
  - [ ] Canonicalize inputs, matrix/version, per-authority findings, completeness counts, blockers, and decision basis into one reconciliation hash.
  - [ ] Make rerun idempotent: the same canonical input yields the same report/decision. Changed evidence may append a new report, but it cannot replace or flip an already-terminal inclusion decision; a conflicting second decision is rejected and requires a new governed run/stage lineage.
- [ ] Append exactly one immutable inclusion decision through the sole writer (AC: 2)
  - [ ] Submit a typed intent containing run/stage, included/excluded, reconciliation hash, typed reasons, and evidence refs.
  - [ ] Reject duplicate-conflicting decisions; preserve retry/attempt lineage and all excluded runs for ITT.
- [ ] Implement categorical fail-closed policy (AC: 3)
  - [ ] Block missing/corrupt evidence, authority conflict, credential leak, disclosure, contamination, tamper, favorable substitution, grading conflict, and causal-validity conflict.
  - [ ] Enforce one blocker is sufficient; scores, percentages, operator override, or favorable panel output cannot waive it.
- [ ] Wire `memrelay-eval reconcile --stage <stage>` (AC: 2)
  - [ ] Non-interactive, typed exit; write a command manifest with input/output/runtime/protocol hashes and terminal status.
  - [ ] Never read a decoded aggregate to alter thresholds or select favorable records.
- [ ] Add matrix, fault, idempotency, and architecture tests (AC: 1-3).

## Developer Context

Reconciliation is the only path from terminal operational evidence to eligibility. It compares independent authorities; it does not manufacture one blended truth. Inspect remains execution authority, SDK terminal evidence corroborates, and telemetry cannot substitute for missing native evidence. Unavailable is valid only where the frozen matrix allows it. Excluded records remain immutable and available for complete ITT/missingness accounting. Environment fingerprint drift creates a separate stratum and cannot be reconciled away.

An `included` run is eligible only for the analysis/stage scope named by its frozen matrix and conformance bundle. It does not authorize a paid-stage promotion, a cost claim before Stories 4.6/4.7, or cross-repository execution; those gates remain separately fail-closed.

### Architecture Compliance

- Follow AD-04, AD-05, AD-11, AD-13, AD-15, AD-17, AD-22, AD-24.
- Use one RFC 8785 canonicalizer and lowercase SHA-256; version/hash the evidence matrix, schemas, semantic map, authority policy, and derivation.

### Frozen Version Requirements

- Preserve exact categorical authority: executable hard outcome, separate qualitative endpoint, and non-overridable security/governance/evidence/grading/causal blockers.
- Every command is non-interactive, typed, and fail-closed.

### File Structure Requirements

- **UPDATE:** `evaluation/src/memrelay_eval/domain/{entities,ports,policies,errors}.py`
- **NEW/UPDATE:** `evaluation/src/memrelay_eval/evidence/{required,reconcile,manifest}.py`
- **UPDATE:** `evaluation/src/memrelay_eval/adapters/telemetry/reconcile.py`
- **UPDATE:** `evaluation/src/memrelay_eval/cli/{main,commands}.py`
- **NEW:** `evaluation/schemas/{required-evidence,reconciliation-report,inclusion-decision}.schema.json`
- **NEW:** `evaluation/tests/contract/evidence/test_required_matrix.py`
- **NEW:** `evaluation/tests/integration/evidence/test_reconcile_inclusion.py`
- **NEW:** `evaluation/tests/fault/evidence/test_reconcile_fail_closed.py`
- **READ ONLY:** raw native artifacts, scoring authority records, future Parquet/DuckDB implementations.

### Existing Behavior to Preserve

- Preserve every native/normalized source, partial or failed attempt, retry/exposure record, secret finding, contradictory outcome, and fake/durable provenance.
- Preserve separate hard, qualitative, and categorical authorities from Story 3.6.
- Preserve included/excluded and unavailable/zero distinctions; never drop an assigned unit.

### Testing Requirements

- Exhaustive evidence-matrix tests cover every terminal/failure/provider/stratum/history condition and conditional panel/adjudication path.
- Fault tests cover missing class, corrupt hash, stale manifest, native/Inspect disagreement, identity conflict, hidden retry, secret leak, tamper, missing cost versus permitted unavailable, cleanup failure, and fingerprint drift.
- Repeat/reorder/duplicate tests prove stable reconciliation hashes and exactly-one logical decision.
- Architecture tests prove reconciliation reads through ports, ledger writes only via typed intent, and no direct analysis/SQLite mutation.

### Anti-Patterns

- Do not infer success from absence, OTLP delivery, aggregate completeness, or a favorable authority.
- Do not convert missing/unavailable to zero, delete excluded runs, select a favorable attempt, or allow manual overwrite.
- Do not materialize Parquet, open DuckDB, estimate effects, or generate claims in this story.
- Future Parquet publication must be atomic/versioned and include only these reconciled terminal records; DuckDB must be read-only over it.

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` — Epic 4 / Story 4.5; FR41, FR43, FR44]
- [Source: `ARCHITECTURE-SPINE.md` — AD-04, AD-13, AD-15, AD-24]
- [Source: `IMPLEMENTATION-DESIGN.md` — §§16-19.6, 22, 24.3-24.4]
- [Source: technical research — 100% primary completeness and conditional evidence matrix]
- [Source: `_bmad-output/test-artifacts/test-design-qa.md` — R-008, R-009; `TEL-*`, `ITT-MISSING-PRIMARY`, `BLOCK-*`]
- [Source: predecessor Story 3.6 and Epic 4 Stories 4.1-4.4 — authority and durable adapter contracts]

## Dev Agent Record

### Agent Model Used

TBD by implementation agent

### Debug Log References

### Completion Notes List

- Ultimate context engine analysis completed - comprehensive developer guide created

### File List
