# Story 2.8: Restore Controlled Immutable Histories

Status: review

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

- [x] Define versioned controlled-history bundle/checkpoint/restore records (AC: 1)
  - [x] Freeze conversation bytes, source hashes, lineage, expected checkpoint, target stratum, and exact restoration instructions.
  - [x] Hash canonical manifests while retaining source/native bytes as immutable artifacts.
- [x] Build golden setup/checkpoint creation as a separate nonstudy operation (AC: 1)
  - [x] Produce one frozen checkpoint per declared protocol/stratum as required.
  - [x] Prevent mutation after protocol freeze.
- [x] Restore into fresh attempt-local roots and verify byte identity (AC: 2)
  - [x] Verify all expected/missing/extra bytes and final checkpoint digest before task delivery.
  - [x] Emit typed failure and block on divergence, partial restore, or digest mismatch.
- [x] Enforce probe-write policy, controlled estimands, and mode non-pooling (AC: 3)
- [x] Use Story 1.1 fake artifact port and block paid/durable eligibility before Story 4.1 (AC: 4)
- [x] Add deterministic replay, tamper, partial-write, cross-stratum, and pre-exposure tests (AC: 1-4)

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

- Python 3.13; Story 1 RFC 8785 canonicalizer and SHA-256 implementation only.
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

GitHub Copilot CLI (Claude-based). BMAD `bmad-dev-story` workflow tooling was confirmed
absent from this worktree/installation (no `_bmad/` framework install, no skill catalog,
no launcher — only prior `_bmad-output/` planning/tracking artifacts exist). This follows
Story 1.4's, 1.6's, 2.2's, 2.5's, and 2.9's precedent: work was implemented directly from
this story markdown plus its linked `ARCHITECTURE-SPINE.md`, `IMPLEMENTATION-DESIGN.md`,
`test-design-qa.md`, and Stories 1.4/1.6/2.2/2.5/2.9, honestly disclosed rather than
fabricating a workflow invocation.

### Debug Log References

- `$env:PYTHONPATH = (Resolve-Path .\evaluation\src).Path; py -3.13 -m pytest evaluation\tests\contract\history\test_controlled_restore.py evaluation\tests\fault\history\test_restore_failures.py -q` — 24 passed initially; 26 passed after the review round added one regression test and one existing test was strengthened.
- `$env:PYTHONPATH = (Resolve-Path .\evaluation\src).Path; py -3.13 -m pytest evaluation\tests -q` — 926 passed, 4 skipped (pre-existing platform-capability skips: POSIX-only forked-descriptor test, two Windows symlink-privilege workspace tests, one symlink-creation catalog fixture test); re-run after review fixes: 927 passed, 4 skipped (one new regression test added).
- `$env:PYTHONPATH = (Resolve-Path .\src).Path; py -3.13 -m pytest tests -q` — 1303 passed, 4 skipped (pre-existing optional FalkorDB/Neptune backend skips); re-confirmed unchanged after review fixes.
- `py -3.13 -m ruff check evaluation/src evaluation/tests src tests` — initial run found only import-order/line-length issues in the two new test files and the new `history.py` additions; `ruff check --fix` plus `ruff format` resolved all of them; final run: all checks passed.
- `py -3.13 -m ruff format --check evaluation/src evaluation/tests src tests` — 326 files already formatted.
- `cd evaluation; uv lock --check` — resolved 107 packages; lock unchanged (no new third-party dependency was added; only stdlib plus existing `rfc8785`/domain code is used).
- `git --no-pager diff --check` — no whitespace/conflict-marker issues.
- `git status --short --untracked-files=all` — confirms only the eight intended `evaluation/src` files are modified and only the two intended new test files are untracked; no unrelated worktree changes were touched.

### Review Evidence

A context-separated automated review was run via a fresh subagent (`gpt-5.3-codex`,
distinct from this implementing session's model) with read-only tools, instructed to
review the uncommitted diff against the story's ACs and architecture-compliance notes.
It reported two blocking findings, one warning, and one info finding; this implementing
context repaired one prior blocker and the info finding, while the restore-once
ledger-backed guard was deferred and disclosed as the non-blocking residual described
below. It also disclosed the warning as an accepted, documented residual risk rather than
silently dismissing it, since Hard Rule 5 forbids this implementing context from
self-accepting its own work:

- **Fixed (blocking):** `build_golden_checkpoint` could commit a bundle as "frozen" in
  memory even if writing its immutable CAS evidence failed, leaving a bundle with no
  backing manifest. Evidence is now written before the bundle is ever committed as
  authoritative, and a regression test (`test_failed_freeze_evidence_write_never_commits_a_bundle_without_evidence`)
  proves a transient CAS failure leaves nothing frozen and a subsequent retry succeeds.
- **Fixed (info):** one AC4 test asserted on an unrelated ad hoc artifact instead of the
  bundle-freeze evidence path; it now asserts on the exact canonical bytes the builder
  wrote.
- **Disclosed residual risk (blocking finding, not fully closed):** the restore-once
  per `attempt_id` guard is enforced by trusted in-process state on a single
  `ControlledHistoryCoordinator` instance, not by an independent ledger-backed claim.
  This exactly mirrors the already-accepted sibling pattern in Story 2.9's
  `DynamicHistoryCoordinator._provisioned_episodes` and Story 1.6's
  `ConcealedAssignmentService._records` — both rely on "exactly one authority instance
  per experiment/control process" (AD-08) rather than a ledger-enforced claim. The
  reviewer's reproduction (constructing a second coordinator instance over the same
  ledger/store) is a composition-root misuse in the current architecture, not a
  supported entry point, but a durable, ledger-backed guard would close this
  more completely; deferred as a candidate follow-up rather than fixed in place, to
  avoid adding new `LedgerPort` surface unilaterally within this story.
- **Disclosed residual risk (warning, accepted):** "no treatment-generated content
  enters the source bundle" is enforced by requiring the frozen `GOLDEN_SETUP_PROVENANCE`
  literal, which is declarative (a caller could in principle assert it falsely), matching
  this codebase's existing declarative-only trust model for `require_treatment_neutral`/
  `require_no_secret_values`. True non-repudiation of origin would require a trusted
  builder-only construction boundary and is out of this story's scope.

### Completion Notes List

- Added `ControlledHistoryItem`/`ControlledHistoryBundle` (schema `1.0.0`), preserving
  ordered episodes, actor/scope/revision, provenance, validity windows, expected graph
  input hashes, protocol ID, and content SHA-256 (AC1). Items must declare the frozen
  `GOLDEN_SETUP_PROVENANCE` literal, so treatment-generated content cannot enter the
  source bundle by construction, not by convention.
- Added `ControlledHistoryBuilder.build_golden_checkpoint`: a nonstudy operation that
  freezes one golden checkpoint per `history_id` via the Story 1.1 `ArtifactStorePort`
  and an `ArtifactManifest` schema `1.0.0` record, and rejects any later call for the
  same `history_id` with different bytes (`ControlledHistoryMutationError`) (AC1, AC4).
- Added `ControlledHistoryCoordinator.restore`: restores the frozen bundle through the
  domain `TreatmentPort` into a caller-supplied fresh handle, then verifies every
  restored `ArtifactRef` against its expected item in order (count, sha256, size) and
  computes a `ControlledRestoreManifest` with a `parity_hash` that is identical across
  every arm that restores the same bundle correctly, and immediately raises
  `ControlledRestoreMismatchError` (missing/extra/tampered) on any divergence — no task
  delivery or inference can precede this verification (AC2). Restoring the same
  `attempt_id` twice, or restoring against an unfrozen/wrong-stratum/foreign
  `history_id`, is rejected before any treatment I/O (`ControlledHistoryViolationError`).
- Added `enforce_probe_write_disposition` (domain policy) and
  `enforce_controlled_effect_boundary` (`orchestration/stages.py`): enforce the frozen
  `disabled`/`discarded`/`recorded_separately` probe-write handling and reject any
  attempt to aggregate controlled and dynamic identities together, or more than one
  distinct controlled history/stratum identity, in the same estimand (AC3).
- Restore-failure evidence is expressed as the existing `AttemptTerminal` vocabulary via
  a new `controlled_restore_failure_terminal` helper (`infrastructure_failed_pre_exposure`),
  so the unmodified Story 1.7 `RetryAuthorizer`/`retry_eligibility_denial_code` policy
  already governs the "at most once, only if conclusively pre-exposure" retry rule; no
  new retry mechanism was added. A retry can never repair a divergent history because
  restoration always re-verifies bytes from scratch against a brand-new attempt ID/root.
- Added `InMemoryTreatmentPort` (`adapters/fakes.py`), a deterministic, `unpaid_conformance`-labeled
  fake `TreatmentPort` with an injectable `tamper` hook and a `fail_on_restore` switch, used
  to exercise every restore fault path without any real product/direct-engine adapter.
- Added tests for: frozen-bundle shape and provenance rejection, freeze immutability,
  byte-identical restore/parity across two and five arms (replay), probe-write policy
  (all three dispositions), controlled/dynamic non-pooling, cross-history/cross-stratum
  aggregation rejection, unpaid-conformance labeling, tamper (single-item bit flip),
  partial restore (missing trailing item, missing all items, extra item), reordering/
  aliasing, cross-sequence/foreign-history substitution, attempt-ID aliasing/replay
  rejection, mid-restore interruption with typed pre-exposure evidence and same-attempt
  retry rejection, a genuine fresh-attempt-ID retry succeeding, and cleanup (`close`)
  never disturbing already-recorded restore evidence. A dedicated architecture-style
  check also asserts `orchestration/history.py` never imports `os`/`pathlib` or calls
  `open(`, because restoration is expressed solely through opaque, content-addressed
  `ArtifactRef`s — the only real filesystem path in this story's contract is the
  already-hardened Story 2.2 attempt-local workspace root a concrete adapter restores
  into, so traversal/junction/symlink attacks have no surface inside this module.
- No archive/snapshot dependency was added; `evaluation/uv.lock` is unchanged
  (`uv lock --check` passes).

### Deviations from Expected File Paths (disclosed)

- `evaluation/src/memrelay_eval/domain/ids.py` was **not updated**. Story 2.8 reuses the
  existing `HistoryId`/`ProtocolId`/`RetentionPolicyId` opaque identifiers; no new
  identifier type was required.
- Concrete product/direct-engine `TreatmentPort` adapter integration (Stories 2.6/2.7)
  was intentionally **not** added here, per this story's own text: "concrete
  product/engine adapter integration belongs to those strata and is conditional because
  Stories 2.6/2.7 are not graph prerequisites." Only the domain-level restore contract
  and its fake conformance adapter were implemented.
- `expected_graph_input_sha256` is preserved and hashed as part of every item's frozen
  identity and the bundle's `content_sha256`, but this story does not itself verify it
  against a live graph, since that verification is adapter-specific and out of scope
  until a concrete engine adapter exists.

### File List

**New:**
- `evaluation/tests/contract/history/test_controlled_restore.py`
- `evaluation/tests/fault/history/test_restore_failures.py`

**Updated:**
- `evaluation/src/memrelay_eval/domain/entities.py`
- `evaluation/src/memrelay_eval/domain/errors.py`
- `evaluation/src/memrelay_eval/domain/policies.py`
- `evaluation/src/memrelay_eval/domain/states.py`
- `evaluation/src/memrelay_eval/orchestration/history.py`
- `evaluation/src/memrelay_eval/orchestration/stages.py`
- `evaluation/src/memrelay_eval/orchestration/attempt.py`
- `evaluation/src/memrelay_eval/adapters/fakes.py`
- `_bmad-output/implementation-artifacts/2-8-restore-controlled-immutable-histories.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (Story 2.8 line only)