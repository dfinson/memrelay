# Story 4.8: Back Up and Restore Terminal Evidence

Status: review

## Story

As an evidence custodian,
I want terminal evidence atomically copied to an independent local volume and restored,
So that paid trials meet durability and reachability requirements.

## Acceptance Criteria

1. **Given** `memrelay-eval bootstrap --backup-root <second-volume-path>`  
   **When** backup conformance runs  
   **Then** the path is verified on a different local volume and terminal ledger snapshots, manifests, `.eval`, JSON, and newly referenced CAS blobs are atomically copied and hash-verified  
   **And** same-volume, corrupt, or incomplete roots fail conformance without fallback.
2. **Given** a terminal run  
   **When** backup completes  
   **Then** the evidence RPO is at most the active in-flight attempt  
   **And** the backup receipt is linked to the run's required evidence.
3. **Given** only the backup root and documented restore inputs  
   **When** a restore drill runs  
   **Then** ledger-to-artifact reachability and verified reads are reconstructed within the 24-hour RTO  
   **And** any failed atomic-copy, index-rebuild, hash, or restore proof blocks paid pilots.

## Dependencies and Prerequisites

- **Authoritative direct graph dependencies:** Stories 4.1, 4.2, and 4.5 only, exactly as declared in `epics.md`.
- Those predecessors supply CAS/rebuild, ledger snapshots/history, and required-evidence/inclusion records.
- Backup includes Story 2.10 secret-safe native evidence and all applicable terminal manifests; it never relaxes classification, retention, or authorization policy.
- The configured root must be an independent second local volume. No same-volume directory, cloud store, or automatic fallback satisfies this story.
- Paid pilots remain blocked until atomic copy, receipt linkage, clean-room restore, index rebuild, hash verification, RPO, and 24-hour RTO conformance all pass.
- Exact traceability: FR57, FR58; NFR3, NFR30, NFR39, NFR40; AR25, AR29, AR35, AR47.

## Tasks / Subtasks

- [x] Implement independent-volume preflight in bootstrap (AC: 1)
  - [x] Resolve canonical source/target volume identities using platform-appropriate APIs (for example Windows volume GUIDs rather than drive-letter spelling) and prove they differ across aliases, links, and mount points.
  - [x] Validate writable capacity, permissions, filesystem behavior, staging/atomic-rename capability, and no provider credentials in the backup process.
  - [x] Fail closed on ambiguity, same volume, incomplete capability, or policy conflict; select no fallback.
- [x] Define terminal backup inventory and receipt schemas (AC: 1, 2)
  - [x] Snapshot SQLite through a control-owned consistent-snapshot API on the sole-writer boundary (for example the SQLite backup API); never file-copy a live database/WAL/SHM set.
  - [x] Include terminal manifests, required `.eval`/Inspect JSON refs and bytes, all newly reachable CAS blobs, inclusion/reconciliation evidence, and documented restore metadata.
  - [x] Receipt records run/attempt, source/target volume fingerprints, inventory hash, each digest/size, copy/verification status, started/completed time, RPO position, and schema/tool version.
- [x] Implement idempotent atomic backup publication (AC: 1, 2)
  - [x] Copy into a target staging generation, verify every byte/hash and complete inventory, fsync as supported, then atomically publish the generation/receipt.
  - [x] Resume/retry after crash without overwriting prior generations or treating partial staging as complete.
  - [x] Link the verified receipt to required evidence through a typed sole-writer intent.
- [x] Enforce RPO after every terminal run (AC: 2)
  - [x] Track the highest terminal ledger position/inventory included and prove only the active in-flight attempt may be outside the last verified generation.
  - [x] Stop new paid attempts on backup lag/failure while preserving active evidence.
- [x] Implement isolated restore drill (AC: 3)
  - [x] Restore from only backup root plus documented non-secret inputs into a clean, quarantined destination; never attach the snapshot to the live operational ledger.
  - [x] Verify ledger snapshot, rebuild all convenience indexes from verified manifests/blobs, and prove experiment/run/attempt/evidence reachability and verified reads.
  - [x] Apply current tombstone, retention, authorization, and policy versions before any restored object can be indexed or rendered; measure and retain RTO evidence and reject any corrupt, missing, unauthorized, expired, or conflicting object without silent repair.
- [x] Add volume, atomicity, crash, corruption, rebuild, and timed restore tests (AC: 1-3).

## Developer Context

Backup is another verified append-only evidence generation, not mirroring by best effort. A receipt is valid only after every inventory item and digest verifies and the generation is atomically visible. Snapshot consistency must be coordinated with the sole SQLite writer. CAS paths remain convenience indexes; restore proves reachability from authoritative records. Restore does not make revoked/expired data readable: apply the current applicable retention/authorization/tombstone policy before any later indexing or rendering, while preserving governed audit evidence.

The baseline paid-trial durability proof covers eligible synthetic/license-audited public evidence. `GOV-BACKUP-RESTORE-REVOKED`, backup expiry/key destruction, and all other DG-R governance proofs remain additional prerequisites for cross-repository execution; this story must preserve their quarantine seams but must not claim that baseline restore conformance authorizes private or cross-repository data.

### Architecture Compliance

- Follow AD-05, AD-09, AD-15, AD-17, AD-22, AD-25.
- Local second-volume durability is the v1 topology; no managed/cloud backup dependency or fallback.

### Frozen Version Requirements

- Default evidence RPO: at most the active in-flight attempt. Default RTO: 24 hours.
- `memrelay-eval bootstrap --backup-root <second-volume-path>` is the required non-interactive entry point.

### File Structure Requirements

- **UPDATE:** `evaluation/src/memrelay_eval/domain/{entities,ports,errors,policies}.py`
- **NEW:** `evaluation/src/memrelay_eval/evidence/backup.py`
- **UPDATE:** `evaluation/src/memrelay_eval/evidence/{manifest,required,reconcile}.py`
- **UPDATE:** `evaluation/src/memrelay_eval/ledger/{sqlite,repository}.py` for consistent snapshot API only.
- **UPDATE:** `evaluation/src/memrelay_eval/cli/{main,commands}.py`
- **NEW:** `evaluation/schemas/{backup-receipt,restore-report}.schema.json`
- **NEW:** `evaluation/tests/contract/evidence/test_backup_volume.py`
- **NEW:** `evaluation/tests/fault/evidence/test_backup_atomicity.py`
- **NEW:** `evaluation/tests/integration/evidence/test_restore_drill.py`
- **READ ONLY:** product source, future analytics/report publication.

### Existing Behavior to Preserve

- Preserve immutable source bytes/manifests, corruption/conflict evidence, ledger history, inclusion decisions, excluded/failed attempts, secret classifications, and retention links.
- Preserve atomic CAS identity and sole-writer ownership; backup/restore never updates operational history in place.
- Preserve environment fingerprints and source/target volume identities as non-secret canonical evidence.

### Testing Requirements

- Platform tests prove same-volume aliases/symlinks/mount ambiguity are rejected and a genuinely independent-volume fixture is accepted where available.
- Fault every file and publication boundary: short copy, bit flip, missing blob, stale ledger snapshot, WAL interruption, full disk, permission loss, process kill, duplicate retry, and receipt-link failure.
- Clean-room restore tests delete convenience indexes, rebuild deterministically, verify every read, compare reachability/inventory hashes, and retain elapsed RTO evidence.
- Add a quarantined revoked-record fixture proving policy/tombstone application occurs before index/render, while keeping the broader DG-R authorization/deletion program explicitly ineligible.
- Conformance failure is a paid-pilot blocker and cannot be converted to a warning or alternate topology.

### Anti-Patterns

- Do not use same-volume copy, incremental sync success, file timestamps, or directory presence as durability proof.
- Do not copy a live SQLite file without a consistent snapshot, publish a partial generation, skip post-copy hashes, or overwrite receipts.
- Do not auto-fallback to cloud/network storage, ignore expired/revoked policy, or expose secrets during diagnostics.
- Do not materialize Epic 5 Parquet or run DuckDB here. If future reconciled Parquet exists, it is backed up as a versioned artifact only; its publication remains atomic and DuckDB remains read-only.

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` — Epic 4 / Story 4.8; FR57, FR58]
- [Source: `ARCHITECTURE-SPINE.md` — AD-25; durability assumption and capability map]
- [Source: `IMPLEMENTATION-DESIGN.md` — §§16.4, 24.3-24.4, 25]
- [Source: `_bmad-output/test-artifacts/test-design-architecture.md` — R-007, R-030]
- [Source: `_bmad-output/test-artifacts/test-design-qa.md` — durability, restore, and categorical blocker scenarios]
- [Source: predecessor Stories 4.1, 4.2, 4.5 — CAS, snapshot, required-evidence, and inclusion contracts]

## Dev Agent Record

### Agent Model Used

GPT-5.6 Terra (gpt-5.6-terra)

### Debug Log References

### Completion Notes List

- Added canonical Windows volume-GUID/Unix device-identity preflight, atomic rename
  capability probing, control-owned SQLite snapshots, verified immutable inventory
  publication, receipt schemas, retry-safe generation verification, and typed receipt links.
- Added a quarantined restore drill with inventory verification, current-policy-before-index
  enforcement, verified ledger-to-artifact reads, reachability rebuild, and retained RTO report.
- Added focused contract, fault, and integration coverage for second-volume rejection,
  partial generations, receipt-link interruption, tampering, rebuild, and revoked policy.
- Replaced the stale backup sentinel with a kernel-backed cross-platform advisory lease and
  added process-termination plus Windows directory-collision regressions; existing
  generations are accepted only after complete byte-identical receipt verification.
- The local host has no independent writable volume available for a physical
  second-volume drill; bootstrap correctly fails closed until operations supplies one.

### File List

- `_bmad-output/implementation-artifacts/{4-8-back-up-and-restore-terminal-evidence.md,sprint-status.yaml}`
- `evaluation/{README.md,schemas/{backup-receipt,restore-report}.schema.json}`
- `evaluation/src/memrelay_eval/{cli/{commands,main}.py,domain/{errors,ports}.py,evidence/backup.py}`
- `evaluation/src/memrelay_eval/{adapters/artifacts/filesystem.py,ledger/repository.py}`
- `evaluation/tests/{contract/evidence/test_backup_volume.py,fault/evidence/test_backup_atomicity.py,integration/evidence/test_restore_drill.py}`
