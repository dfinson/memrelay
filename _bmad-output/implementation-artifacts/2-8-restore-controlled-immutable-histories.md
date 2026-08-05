# Story 2.8: Restore Controlled Immutable Histories

Status: ready-for-dev

## Story

As an experiment controller,
I want byte-identical pre-treatment histories restored to every controlled arm,
So that controlled-effect comparisons start from the same immutable evidence.

## Acceptance Criteria

1. **Given** a controlled history  
   **When** its bundle is built before assignment exposure  
   **Then** immutable CAS references preserve ordered episodes, actors, scopes, revisions, provenance, validity windows, expected graph inputs, protocol ID, and content SHA-256  
   **And** no treatment-generated content enters the source bundle.
2. **Given** the same controlled bundle is restored to different arms  
   **When** provisioning completes  
   **Then** restore manifests and parity hashes prove byte-identical inputs  
   **And** any mismatch blocks exposure.
3. **Given** probe-time writes or a query combining history modes  
   **When** protocol and analysis rules are enforced  
   **Then** writes are disabled, discarded, or separately recorded exactly as frozen and only controlled-effect estimands are allowed  
   **And** controlled and dynamic outcomes cannot be pooled.
4. **Given** controlled-history artifacts before Story 4.1  
   **When** they are written or resolved  
   **Then** Story 2.8 consumes the Story 1.1 `ArtifactStorePort` and `ArtifactManifest` schema `1.0.0`, using deterministic fake artifacts solely for unpaid conformance until Story 4.1 provides the durable filesystem CAS  
   **And** paid execution and study inclusion remain blocked until Story 4.1 passes conformance.

## Dependencies and Prerequisites

- Authoritative direct dependencies are Stories 1.4, 1.6, 2.2, and 2.5. Story 1.5 provides protocol/configuration freeze and Story 1.3 canonical identity.
- Stories 2.6 and 2.7 provide stratum-specific restore targets when those strata are executed.
- Controlled history is not dynamic history. It cannot consume prior attempt outcomes or switch regimes when restore fails.

## Tasks / Subtasks

- [ ] Define versioned controlled-history bundle/checkpoint/restore records (AC: 1)
  - [ ] Freeze conversation bytes, source hashes, lineage, expected checkpoint, target stratum, and exact restoration instructions.
  - [ ] Hash canonical manifests while retaining source/native bytes as immutable artifacts.
- [ ] Build golden setup/checkpoint creation as a separate nonstudy operation (AC: 1)
  - [ ] Produce one frozen checkpoint per declared protocol/stratum as required.
  - [ ] Prevent mutation after protocol freeze.
- [ ] Restore into fresh attempt-local roots and verify byte identity (AC: 2)
  - [ ] Verify all expected/missing/extra bytes and final checkpoint digest before task delivery.
  - [ ] Emit typed failure and block on divergence, partial restore, or digest mismatch.
- [ ] Enforce probe-write policy, controlled estimands, and mode non-pooling (AC: 3)
- [ ] Use Story 1.1 fake artifact port and block paid/durable eligibility before Story 4.1 (AC: 4)
- [ ] Add deterministic replay, tamper, partial-write, cross-stratum, and pre-exposure tests (AC: 1-4)

## Developer Context

Controlled-history replay is immutable byte-identical restoration, not semantic reconstruction. Golden setup is frozen before trials. Every attempt starts from a fresh root and proves its restored bytes/digests before task exposure. Product and direct-engine checkpoints remain distinct even when seeded from the same conversation manifest.

### Architecture Compliance

- Follow AD-05, AD-07, AD-10, AD-12, AD-17, AD-20, AD-24.
- Controlled and dynamic regimes are separate protocols/estimands and never pool.
- Restoration runs before task delivery and inference; no opportunistic repair or favorable substitute.
- Immutable artifacts hold manifests/checkpoints; thin ledger holds references/statuses only.
- Fake `ArtifactStorePort`, `LedgerPort`, and `TelemetryPort` implementations prove unpaid conformance only. Paid/study restoration requires Story 4.1 CAS, Story 4.2 ledger, and Story 4.3 Collector conformance.
- Restore retry is permitted at most once only after a conclusively pre-exposure infrastructure failure, with fresh roots and protocol authorization; no retry may repair divergent history.

### Library and Version Requirements

- Python 3.11; Story 1 RFC 8785 canonicalizer and SHA-256 implementation only.
- Use the same frozen product/direct framework versions and models as Stories 2.6-2.7.
- Do not add an archive/snapshot dependency unless final architecture is amended.

### Expected File Paths

- **UPDATE:** `evaluation/src/memrelay_eval/domain/{entities,ids,errors,policies}.py`
- **UPDATE:** `evaluation/src/memrelay_eval/orchestration/{history,stages,attempt}.py`
- **UPDATE:** `evaluation/src/memrelay_eval/adapters/fakes.py`
- **NEW:** `evaluation/tests/contract/history/test_controlled_restore.py`
- **NEW:** `evaluation/tests/fault/history/test_restore_failures.py`

### Existing Behavior to Preserve

- Fresh workspace/graph ownership, immutable freeze records, and product/direct stratum separation.
- Product graph remains daemon-owned; restoration must use an approved prestart/import seam and never create a second concurrent graph writer.
- This story implements restoration through the domain `TreatmentPort`; concrete product/engine adapter integration belongs to those strata and is conditional because Stories 2.6/2.7 are not graph prerequisites.
- Restore failure remains pre-exposure and leaves complete immutable failure evidence.

### Testing Requirements

- Repeat restoration across fresh roots/platform-normalized paths and assert byte-identical digests.
- One-bit/source/checkpoint/instruction/lineage tamper and missing/extra/partial-file tests all block.
- Verify no task delivery/inference/provider call precedes restore verification.
- Cross-regime/cross-stratum checkpoint substitution and manual mutation are rejected.
- Crash/fault tests preserve partial immutable artifacts and prevent root reuse.

### Anti-Patterns

- Do not regenerate “equivalent” history, normalize bytes after freeze, or repair manually.
- Do not copy a product checkpoint into direct-engine protocol without distinct frozen identity.
- Do not fall back to dynamic history or continue after a mismatch.
- Do not count golden setup as study efficacy or perform unbounded provider calls.

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` — “Story 2.8”]
- [Source: `ARCHITECTURE-SPINE.md` — AD-05, AD-07, AD-10, AD-12, AD-17, AD-20, AD-24]
- [Source: `IMPLEMENTATION-DESIGN.md` — §§5, 12.1, 12.3]
- [Source: `test-design-qa.md` — `HIST-CARRYOVER`, prestart and restore integrity scenarios]

## Dev Agent Record

### Agent Model Used

TBD by implementation agent

### Debug Log References

### Completion Notes List

- Ultimate context engine analysis completed - comprehensive developer guide created

### File List
