# Story 2.2: Provision Isolated Workspace Providers

Status: ready-for-dev

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

- [ ] Define provider-neutral workspace handles/specifications (AC: 1)
  - [ ] Allocate every root named in AC1 from an opaque attempt ID without encoding treatment.
  - [ ] Record canonical root ownership and reject collision, reuse, symlink/junction escape, or shared writability.
- [ ] Implement temporary git-worktree provider (AC: 1)
  - [ ] Create from the frozen revision; preserve dirty-state policy and immutable source hash.
  - [ ] Keep all agent/cache/staging/product roots attempt-local.
- [ ] Implement isolated-clone provider with identical observable contract (AC: 1)
  - [ ] Clone from the frozen local source without network and verify revision/content policy.
  - [ ] Run the same isolation suite against both providers.
- [ ] Implement idempotent compensating cleanup (AC: 2)
  - [ ] Record cleanup attempts/results without deleting lifecycle or terminal evidence.
  - [ ] Quarantine failed cleanup roots; never assign them to another attempt.
- [ ] Add collision, concurrency, crash, cancellation, and platform contract tests (AC: 1-2)

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

- Python 3.11 standard library plus local `git`; no new workspace framework is specified.
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

TBD by implementation agent

### Debug Log References

### Completion Notes List

- Ultimate context engine analysis completed - comprehensive developer guide created

### File List
