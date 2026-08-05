# Story 1.6: Conceal Assignment and Record Exposure

Status: ready-for-dev

## Story

As an experiment controller,
I want opaque assignment resolved only inside provisioning with explicit exposure evidence,
So that operators, graders, and ordinary artifacts cannot infer treatment.

## Acceptance Criteria

1. **Given** a frozen enrollment plan  
   **When** assignment runs  
   **Then** it seals the algorithm version, seed commitment, blocks, ordered input hashes, and assignment-plan hash  
   **And** ordinary manifests contain only opaque experiment, run, and assignment IDs.
2. **Given** an attempt specification outside the provisioning boundary  
   **When** it is inspected, logged, exported, or passed to scoring  
   **Then** it contains no human-readable arm label or assignment resolution  
   **And** only the provisioning authority can resolve the treatment.
3. **Given** provisioning and execution events  
   **When** exposure is classified  
   **Then** assignment resolution, memory provisioning, task delivery, inference, treatment access, first monotonic exposure time, and supporting evidence are recorded  
   **And** missing or ambiguous exposure evidence is classified as exposed.
4. **Given** assignment lifecycle or telemetry before Stories 4.2 and 4.3  
   **When** it is emitted  
   **Then** it flows through the Story 1.1 domain-owned `LedgerPort` and `TelemetryPort` using deterministic fakes for unpaid conformance  
   **And** durable study execution and inclusion remain blocked until the concrete sole-writer SQLite ledger and Collector adapters pass conformance.

## Dependencies and Prerequisites

- **Formal graph dependencies:** Story 1.4 and Story 1.5.
- Story 1.4 must provide eligible task/history dispositions and Story 1.5 the sealed enrollment plan.
- Story 1.1 opaque IDs, ports, typed errors, and deterministic fake ledger/telemetry adapters.
- Provisioning itself is not implemented here; expose a narrow resolution authority/port consumed by Epic 2.
- The final artifacts freeze an assignment algorithm/version but do not choose one. Do not invent a default, seed derivation, blocking rule, or balance tolerance: require a protocol-supplied registered algorithm/version and fail closed when it is absent or unknown. Deterministic fixture algorithms are unpaid conformance only.
- **Repository baseline:** every evaluator path is created by prior Epic 1 stories; no assignment or exposure implementation exists in the current checkout.

## Tasks / Subtasks

- [ ] Implement deterministic concealed assignment over frozen inputs (AC: 1)
  - [ ] Consume only sealed algorithm version, seed commitment, block definitions, and ordered hashes.
  - [ ] Produce opaque assignment identity/plan hash and an access-separated resolution record.
  - [ ] Verify balance/block invariants without exposing arm labels in ordinary results.
  - [ ] Keep seed material and resolution records in the access-separated authority; ordinary artifacts retain only the frozen commitment and opaque IDs.
- [ ] Implement provisioning-only resolution authority (AC: 2)
  - [ ] Use capability/narrow interface enforcement, not a public entity field.
  - [ ] Ensure attempt specs, logs, exports, scoring views, exceptions, and telemetry are treatment-neutral.
  - [ ] Deny resolution from catalog, scoring, CLI display, and ordinary manifest code paths.
- [ ] Implement exposure evidence and conservative classifier (AC: 3)
  - [ ] Record assignment resolution, memory provisioning, task delivery, first inference, treatment access/retrieval, first monotonic exposure time, and evidence refs.
  - [ ] Classify any missing/contradictory/ambiguous evidence as exposed.
  - [ ] Keep wall-clock timestamps for audit and monotonic timestamps for ordering/duration.
- [ ] Route unpaid lifecycle/telemetry through domain ports (AC: 4)
  - [ ] Emit deterministic fake records labeled unpaid conformance.
  - [ ] Enforce a hard eligibility barrier pending durable SQLite/Collector conformance.
- [ ] Add concealment, leakage, determinism, authorization, and exposure-boundary tests (AC: 1-4)

## Developer Context

Assignment concealment is causal policy. Ordinary manifests may link opaque IDs but must not reveal arm codes, treatment names, resolved adapter, or treatment-specific tool/path details. The provisioning authority alone can translate assignment identity into a treatment. Exposure ambiguity always removes retry eligibility.

### Architecture Compliance

- Follow AD-11, AD-12, AD-18 and Implementation Design §§8.3, 10.1-10.2.
- Scoring cannot access assignment resolution; catalog cannot import orchestration.
- Preserve append-only records and immutable assignment plan.
- Structured logs remain treatment-neutral.
- Fake `LedgerPort`/`TelemetryPort` outputs are deterministic/unpaid; concrete durable/paid eligibility waits for Stories 4.2/4.3 plus reconciliation.

### Library and Version Requirements

- Python 3.13 and standard-library domain policy.
- Reuse Story 1.3 RFC 8785/SHA-256 and Story 1.5 immutable freeze records.
- Use a deterministic PRNG/assignment algorithm identified and frozen by the plan; do not silently substitute algorithms. The final architecture does not name a third-party assignment library.
- No Inspect, Copilot SDK, OpenAI, OTel Collector, database, or scoring library is needed.

### Expected File Paths

- **NEW:** `evaluation/src/memrelay_eval/domain/assignment.py`
- **UPDATE:** `evaluation/src/memrelay_eval/domain/{entities,ports,policies,errors}.py`
- **NEW/UPDATE:** `evaluation/src/memrelay_eval/orchestration/assignment.py`
- **NEW:** `evaluation/src/memrelay_eval/orchestration/exposure.py`
- **UPDATE:** `evaluation/src/memrelay_eval/adapters/fakes.py`
- **NEW:** `evaluation/tests/unit/orchestration/test_assignment.py`
- **NEW:** `evaluation/tests/unit/orchestration/test_exposure.py`
- **NEW:** `evaluation/tests/contract/test_assignment_concealment.py`

### Existing Behavior to Preserve

- Frozen plan/config/eligibility identities and all catalog/compiler invariants.
- Domain standard-library purity and no assignment access from scoring/catalog.
- No treatment labels in ordinary artifacts, logs, command manifests, diagnostics, or telemetry.
- Fake ports remain incapable of study inclusion.

### Testing Requirements

- Golden assignment reproducibility using the same frozen inputs and sensitivity to each committed input.
- Unknown/missing algorithm-version tests fail closed; fixture algorithms are labeled unpaid and cannot authorize enrollment.
- Block/balance tests and stable ordered-input handling.
- Serialization/snapshot/canary scans across every ordinary surface for arm/treatment labels and resolution details.
- Capability tests prove only provisioning authority resolves.
- Exposure truth-table covers all event combinations; missing/ambiguous/contradictory always means exposed.
- Assert fake lifecycle/telemetry records are deterministic, redacted, append-only, and study-ineligible.
- No-network/no-provider run.

### Anti-Patterns

- Do not put arm names in IDs, enums serialized to ordinary views, filenames, logs, or exceptions.
- Do not expose a `treatment` field on general `AttemptSpec`.
- Do not let scoring/catalog/graders resolve assignments.
- Do not treat “no exposure record” as unexposed.
- Do not implement real provisioning, durable ledger/Collector, or paid execution.

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` — “Story 1.6”]
- [Source: `ARCHITECTURE-SPINE.md` — AD-11, AD-12, AD-18]
- [Source: `IMPLEMENTATION-DESIGN.md` — §§8.3, 10.1-10.2, 17.2]
- [Source: TEA handoff — “Quality gates”; R-002/R-008 mapping]
- [Source: `test-design-qa.md` — `ARM-PARITY`, `ITT-INFRA-PRE`, `ITT-INFRA-POST`, telemetry requirements]

## Dev Agent Record

### Agent Model Used

TBD by implementation agent

### Debug Log References

### Completion Notes List

- Ultimate context engine analysis completed - comprehensive developer guide created

### File List
