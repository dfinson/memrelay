# Story 2.5: Verify Agent and Environment Parity

Status: ready-for-dev

## Story

As an experiment controller,
I want arm-neutral parity hashes before task exposure,
So that treatment content or access is the only permitted difference.

## Acceptance Criteria

1. **Given** a provisioned attempt  
   **When** parity is computed  
   **Then** it hashes SDK/runtime versions, exact model ID, reasoning, context, prompt bytes, tool schemas, permissions, network, limits, timeout, workspace layout, built-in memory, cross-session store, retry policy, effective configuration, and host fingerprint  
   **And** only treatment content or access may differ.
2. **Given** paired arms in a block  
   **When** their parity records disagree before exposure  
   **Then** execution fails as pre-exposure infrastructure failure with evidence  
   **And** no task is delivered or inference started.
3. **Given** OS/build, CPU, memory, storage class, power mode, runtime, limits, network, or background-load policy changes  
   **When** the environment fingerprint is checked  
   **Then** the changed fingerprint creates a separate environment stratum  
   **And** it is never silently pooled with the prior fingerprint.

## Dependencies and Prerequisites

- Story 1.5 provides redacted effective configuration and environment fingerprint inputs.
- Stories 2.1, 2.2, and 2.4 provide locked runtime/models, workspace layout, and exact agent controls.
- Comparison occurs before task delivery/inference. A mismatch may use Story 1.7's sole retry only when conclusively unexposed, protocol-authorized, and freshly isolated.

## Tasks / Subtasks

- [ ] Define canonical parity/environment records (AC: 1, 3)
  - [ ] Include every AC1 field with exact bytes/units and schema version.
  - [ ] Reuse the sole RFC 8785 canonicalizer and lower-case SHA-256.
- [ ] Build treatment-neutral parity projection and allowlist (AC: 1)
  - [ ] Disable/pin Copilot built-in memory and cross-session store identically.
  - [ ] Permit only protocol-declared treatment content/access differences; reject path/tool/schema/budget drift.
- [ ] Gate paired attempts before exposure (AC: 2)
  - [ ] Compare canonical records before task delivery.
  - [ ] Emit typed pre-exposure failure and evidence; do not launch inference.
- [ ] Implement environment-stratum rule (AC: 3)
  - [ ] Any fingerprint change creates a new stratum/protocol linkage and prevents ordinary pooling.
- [ ] Add one-field-at-a-time mutation, ordering, redaction, and retry-boundary tests (AC: 1-3)

## Developer Context

Parity is a blocking causal contract. It includes execution substrate, agent configuration, host conditions, workspaces, tools, permissions, built-in memory/store settings, retries, and redacted effective config. Treatment labels are not part of ordinary parity records. Treatment differences are declared by opaque protocol projections and are the only allowed deltas.

### Architecture Compliance

- Follow AD-03, AD-07, AD-12, AD-17, AD-18, AD-19, AD-24.
- Canonical bytes come only from Story 1.3. No `json.dumps(sort_keys=True)` identity.
- Environment strata, product/engine strata, and controlled/dynamic history protocols are distinct and never silently pooled.
- Fake `ArtifactStorePort`, `LedgerPort`, and `TelemetryPort` implementations record unpaid conformance; durable parity evidence requires Story 4.1 CAS, Story 4.2 ledger, and Story 4.3 Collector conformance.
- Mismatch is pre-exposure only if evidence conclusively proves no task/inference/treatment access.

### Library and Version Requirements

- Python 3.11; exact Copilot SDK/runtime/model locks and Inspect `0.3.252`.
- No new fingerprint/parity package is required; use domain records and shared canonicalizer.
- Product framework pins are compared later per stratum, not mutated here.

### Expected File Paths

- **UPDATE:** `evaluation/src/memrelay_eval/domain/{entities,ids,errors,policies}.py`
- **UPDATE:** `evaluation/src/memrelay_eval/domain/environment.py`
- **UPDATE:** `evaluation/src/memrelay_eval/orchestration/{attempt,stages,limits,configuration,blocks}.py`
- **UPDATE:** `evaluation/src/memrelay_eval/adapters/fakes.py`
- **NEW:** `evaluation/tests/unit/orchestration/test_parity.py`
- **NEW:** `evaluation/tests/contract/test_environment_strata.py`

### Existing Behavior to Preserve

- Story 1 frozen inputs, redaction, concealment, exposure, retry lineage, and no favorable substitution.
- Story 2.2 unique workspace roots and Story 2.4 exact Inspect/SDK translation.
- No task delivery or provider call may occur after a parity failure.

### Testing Requirements

- Golden identical-arm parity plus mutation test for every AC1/fingerprint field.
- Canonical ordering/bytes, treatment allowlist, opaque IDs, and secret/treatment-label scans.
- Prove changed fingerprints cannot be queried/aggregated as the old stratum without explicit stratification.
- Retry truth table proves at most one fresh, authorized, conclusively pre-exposure replacement.
- CI uses fakes and no paid calls.

### Anti-Patterns

- Do not ignore “minor” runtime, tool-schema, permission, cache, retry, power-mode, or background-load drift.
- Do not place treatment labels/secrets in parity artifacts.
- Do not compute parity after exposure, silently normalize differences, or pool changed environments.
- Do not rerun until parity happens to match.

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` — “Story 2.5”]
- [Source: `ARCHITECTURE-SPINE.md` — AD-03, AD-12, AD-18, AD-24]
- [Source: `IMPLEMENTATION-DESIGN.md` — §§5, 13.3, 15.4, 21.3]
- [Source: `test-design-qa.md` — `ARM-PARITY`, `MODEL-UNAVAILABLE-PAUSE`, `GRADE-IMAGE-PRESTART`]

## Dev Agent Record

### Agent Model Used

TBD by implementation agent

### Debug Log References

### Completion Notes List

- Ultimate context engine analysis completed - comprehensive developer guide created

### File List
