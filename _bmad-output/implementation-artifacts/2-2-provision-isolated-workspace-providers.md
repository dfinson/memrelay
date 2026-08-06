# Story 2.2: Provision Isolated Workspace Providers

Status: review

## Story

As a trial operator,
I want one fresh workspace with unique roots and state per attempt,
So that concurrent trials cannot contaminate one another.

## Acceptance Criteria

1. **Given** an attempt starts through worktree or isolated-clone provisioning  
   **When** its workspace is created  
   **Then** workspace, agent session, cache, staging, telemetry identity, `MEMRELAY_HOME`, graph, spool, socket or port, and configuration roots are unique  
   **And** both providers satisfy the same isolation contract with no shared writable state.
2. **Given** attempt completion, failure, timeout, or cancellation  
   **When** workspace cleanup runs as compensating work  
   **Then** cache, graph ownership, workspace, and provider cleanup evidence is recorded  
   **And** attempt-local roots and state cannot be reused by another attempt.

## Dependencies and Prerequisites

- **Formal graph dependency:** Story 1.1 only. It defines `WorkspacePort`-compatible domain seams, opaque attempt IDs, cleanup records, and fake evidence ports.
- Worktrees are preferred when supported; isolated clones are the mandatory equivalent fallback. Neither provider may escape the repository or use shared mutable caches.
- This story provisions local host-native state only. It does not launch Copilot, daemon, MCP, graders, or paid trials.

## Tasks / Subtasks

- [x] Define provider-neutral workspace handles/specifications (AC: 1)
  - [x] Allocate every root named in AC1 from an opaque attempt ID without encoding treatment.
  - [x] Record canonical root ownership and reject collision, reuse, symlink/junction escape, or shared writability.
- [x] Implement temporary git-worktree provider (AC: 1)
  - [x] Create from the frozen revision; preserve dirty-state policy and immutable source hash.
  - [x] Keep all agent/cache/staging/product roots attempt-local.
- [x] Implement isolated-clone provider with identical observable contract (AC: 1)
  - [x] Clone from the frozen local source without network and verify revision/content policy.
  - [x] Run the same isolation suite against both providers.
- [x] Implement idempotent compensating cleanup (AC: 2)
  - [x] Record cleanup attempts/results without deleting lifecycle or terminal evidence.
  - [x] Quarantine failed cleanup roots; never assign them to another attempt.
- [x] Add collision, concurrency, crash, cancellation, and platform contract tests (AC: 1-2)

## Developer Context

Workspace isolation is a causal boundary, not a convenience directory. Every attempt receives fresh host-native state; workers are never reused. Cleanup is append-only compensating work and must not roll back evidence. Containers may later test credential-free components, but subscription-authenticated task agents stay host-native.

### Architecture Compliance

- Follow AD-06, AD-08, AD-11, AD-24 and Implementation Design §15.1.
- Implement behind domain `WorkspacePort`; orchestration depends on the port, never a concrete provider.
- Worktree and clone providers must pass the same contract. No fallback topology is selected after exposure.
- Environment fingerprint/storage class and workspace layout feed Story 2.5 parity.
- Emit cleanup/ownership evidence through deterministic fake `ArtifactStorePort`, `LedgerPort`, and `TelemetryPort` implementations. They remain `unpaid_conformance` until the Story 4.1 CAS, Story 4.2 ledger, and Story 4.3 Collector adapters pass conformance.
- A provisioning retry is permitted at most once, only when protocol-authorized and conclusively pre-exposure; it must allocate a fully fresh workspace.

### Library and Version Requirements

- Python 3.13 standard library plus local `git`; no new workspace framework is specified.
- Preserve evaluator lock and root product dependency bounds. No Copilot, Inspect, OpenAI, OTel, or memrelay engine call belongs here.

### Expected File Paths

- **NEW:** `evaluation/src/memrelay_eval/adapters/workspace/{base,worktree,clone}.py`
- **UPDATE:** `evaluation/src/memrelay_eval/orchestration/{worker,attempt,stages}.py`
- **UPDATE:** `evaluation/src/memrelay_eval/domain/{entities,ports,errors}.py`
- **NEW:** `evaluation/tests/contract/workspace/test_isolation.py`
- **NEW:** `evaluation/tests/fault/workspace/test_cleanup.py`
- **READ ONLY:** product source and root `pyproject.toml`

### Existing Behavior to Preserve

- Epic 1 opaque IDs, frozen revision/input hashes, exposure conservatism, append-only fake records, and no paid eligibility.
- User worktree, current checkout, git config, ignored files, and product caches must not be mutated.
- A cleanup failure cannot erase the attempt directory before evidence is captured.

### Testing Requirements

- Parallel allocations prove unique roots/resources and no shared writable inode/path/cache/session state.
- Both providers run byte-identical contract assertions, including Windows junction/path handling.
- Fault injection at each create/destroy step proves idempotent cleanup and quarantine.
- No-network clone test; no ambient credential/provider process; no reuse after success or failure.
- Assert the provider layer cannot import or invoke Copilot, OpenAI, Inspect, or memrelay inference; this story has a zero paid-call budget.

### Anti-Patterns

- Do not share `MEMRELAY_HOME`, graph, spool, socket, cache, agent session, telemetry identity, or staging roots.
- Do not use the current working tree as an attempt workspace.
- Do not delete evidence as “rollback,” reuse a dirty root, or make cleanup success an outcome substitution.
- Do not broaden into process credentials or task execution.

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` — “Story 2.2”]
- [Source: `ARCHITECTURE-SPINE.md` — AD-06, AD-08, AD-24; Assumptions]
- [Source: `IMPLEMENTATION-DESIGN.md` — §§5, 10.2, 15.1, 25]
- [Source: `test-design-qa.md` — `ARM-PARITY`, `GRADE-IMAGE-*`, `HIST-CARRYOVER`]

## Dev Agent Record

### Agent Model Used

GPT-5.6 Terra (gpt-5.6-terra)

### Debug Log References

- `$env:PYTHONPATH = (Resolve-Path .\evaluation\src).Path; C:\Python312\python.exe -m pytest .\evaluation\tests`
- `wsl -d Ubuntu -e /usr/bin/env bash -lc 'cd /mnt/c/Users/davidfinson/.copilot/repos/copilot-worktrees/memrelay/dfinson-refactored-enigma/evaluation && UV_PROJECT_ENVIRONMENT=/tmp/memrelay-eval-py313 uv run --python 3.13 --extra dev pytest tests'`
- `$env:PYTHONPATH = (Resolve-Path .\src).Path; C:\Python312\python.exe -m pytest`
- `C:\Python312\python.exe -m ruff check .`
- `C:\Python312\python.exe -m ruff format --check .`
### Completion Notes List

- Ultimate context engine analysis completed - comprehensive developer guide created
- Added provider-neutral workspace specifications, handles, frozen snapshots, ownership registry,
  cleanup records, and deterministic unpaid-conformance ownership/cleanup evidence.
- Added temporary worktree and isolated local-clone providers with private Git metadata, no writable
  remotes, frozen revision/content verification, opaque telemetry identities, and attempt-local roots.
- Added provider-parity, collision, source-mutation, cleanup-fault, quarantine, no-network, and
  paid-runtime import boundary contract coverage, including Windows junction/reparse and cross-process
  ownership-race cases.
- Hardened all allocation, ownership, quarantine, and cleanup paths against symlinks, junctions, and
  reparse points; live attempt roots are host-private temporary directories, ownership/quarantine
  tombstones use Windows handle-relative creation, and cleanup rejects nested reparse descendants.
- Git materialization disables credential helpers, network transports, and recursive submodules except
  the explicit local frozen-source transfer.
- Windows evaluator suite: 125 passed, 2 skipped because this host lacks symlink privilege. Pinned
  evaluator runtime validation: CPython 3.13.14 via isolated WSL uv environment, 122 passed and
  4 Windows-junction tests skipped by explicit platform capability.

### File List

- `_bmad-output/implementation-artifacts/2-2-provision-isolated-workspace-providers.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
- `evaluation/src/memrelay_eval/adapters/workspace/__init__.py`
- `evaluation/src/memrelay_eval/adapters/workspace/base.py`
- `evaluation/src/memrelay_eval/adapters/workspace/clone.py`
- `evaluation/src/memrelay_eval/adapters/workspace/worktree.py`
- `evaluation/tests/contract/workspace/test_isolation.py`
- `evaluation/tests/fault/workspace/test_cleanup.py`
