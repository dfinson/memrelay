# Story 3.1: Freeze Snapshots and Run Deterministic Graders

Status: ready-for-dev

## Story

As a benchmark maintainer,
I want credential-free deterministic grading over immutable terminal snapshots,
so that executable correctness is reproducible and cannot be influenced by treatment or live services.

## Acceptance Criteria

1. **Given** a terminal attempt from Story 2.10  
   **When** the workspace is frozen  
   **Then** baseline revision, terminal revision, patch, files, and canonical timestamp/path-normalized snapshot are immutable SHA-256 artifacts  
   **And** later workspace mutation cannot change grader inputs.
2. **Given** a frozen grader contract  
   **When** the grader process starts  
   **Then** benchmark-native and hidden tests, dependencies, tamper checks, scope checks, network policy, grader version, and hashes are pinned  
   **And** the process receives opaque IDs but no assignment, provider credentials, or unrestricted network.
3. **Given** repeated grading of identical snapshot and contract hashes  
   **When** results are compared  
   **Then** binary and test outcomes match exactly and continuous scores match within `1e-6`, excluding timing  
   **And** executable results, objective components, terminal status, and artifact references are preserved.
4. **Given** snapshot or grader artifacts before Story 4.1  
   **When** they are written or resolved  
   **Then** Story 3.1 consumes the Story 1.1 `ArtifactStorePort` and `ArtifactManifest` schema `1.0.0`, using deterministic fake artifacts solely for unpaid conformance until Story 4.1 provides the durable filesystem CAS  
   **And** paid execution and study inclusion remain blocked until Story 4.1 passes conformance.

## Dependencies and Prerequisites

- **Authoritative direct graph dependencies:** Stories 2.2 and 2.10, exactly as declared in `epics.md`. Consume their `WorkspacePort`, terminal snapshot/export, native evidence, secret-scan, and typed failure contracts without redefining them.
- Stories 1.1 and 1.7 are inherited/transitive contract sources, not additional direct graph edges. They supply `ArtifactStorePort`, manifest `1.0.0`, append-only attempt terminals, and fake-port eligibility policy.
- This story is prerequisite to Stories 3.2 and 3.6. Story 4.1 is a hard qualification dependency for paid use, not implementation scope here.
- Exact traceability: FR33; NFR13, NFR22, NFR30, NFR37, NFR38; AR20, AR29.

## Tasks / Subtasks

- [ ] Define immutable snapshot and frozen grader domain records (AC: 1, 2)
  - [ ] Record baseline/terminal revisions, exact patch/files, canonical normalization policy, raw/canonical hashes, grader/dependency/network/scope/tamper contracts, and opaque attempt IDs.
- [ ] Freeze workspace through the existing `WorkspacePort` and detach grading from the mutable workspace (AC: 1)
  - [ ] Prove post-freeze file, clock, path, and workspace mutations cannot alter resolved grader bytes.
- [ ] Implement the credential-free executable grader adapter (AC: 2)
  - [ ] Launch with a minimal environment, no GitHub/Copilot/OpenAI credentials, assignment access, provider client, or unrestricted network.
  - [ ] Run benchmark-native/hidden tests and objective tamper/patch-scope checks under the pinned contract; emit typed failures and partial evidence.
- [ ] Implement deterministic replay comparison and authoritative hard outcome (AC: 3)
  - [ ] Require exact binary/test equality and continuous-score tolerance `1e-6`; timing is evidence but excluded from equality.
  - [ ] Preserve every result and disagreement; executable failure and categorical blockers are hard, non-overridable outcomes.
  - [ ] Apply the frozen flaky-test policy without best-of-N: task intake requires five fresh baseline passes and five fresh gold-patch passes; a failing candidate may rerun at most twice only to classify a preregistered flaky signature, and the frozen aggregate—not the favorable run—determines the result.
  - [ ] On grader failure, re-grade only the identical frozen snapshot under the identical contract and bounded retry count; unresolved grading remains `unavailable` plus a grading blocker, never pass or zero.
- [ ] Route all artifacts through fake `ArtifactStorePort` until durable CAS qualification (AC: 4)
  - [ ] Mark provenance `unpaid_conformance`; reject paid execution or inclusion before Story 4.1 conformance.
  - [ ] Preserve the inherited fake-ledger/fake-telemetry barrier: Story 4.1 qualifies artifact durability only; paid/study eligibility still requires Stories 4.2 and 4.3 and later reconciliation.
- [ ] Add unit, contract, fault, and no-network tests (AC: 1-4)
  - [ ] Cover baseline/gold repeat stability, dependency outage, allowed/forbidden network, pre/post-start image drift, crash, tamper, patch scope, hash corruption, and fake-artifact gating.

## Developer Context

Executable grading is a hard authority, not a heuristic or panel input. It runs after execution over a frozen snapshot in a separate credential-free process. Preserve raw grader output and normalized result separately. Do not re-run the task agent after grader failure; any permitted re-grade uses the identical snapshot and contract. A favorable qualitative score can never reverse failed tests, tamper, security, governance, grading, evidence-integrity, or causal-validity blockers.

### Architecture and File Requirements

- Follow AD-01, AD-05, AD-09, AD-13, AD-15, AD-17, and AD-22. `scoring` depends on domain ports, never assignment resolution; external process types terminate in the grader adapter.
- Expected paths once predecessor stories exist (extend their types; do not create a second snapshot, manifest, canonicalization, or terminal-status model):
  - **UPDATE:** `evaluation/src/memrelay_eval/domain/{entities,ports,policies,errors}.py`
  - **UPDATE:** `evaluation/src/memrelay_eval/scoring/service.py`
  - **NEW/UPDATE:** `evaluation/src/memrelay_eval/adapters/grader/executable.py`
  - **UPDATE:** `evaluation/src/memrelay_eval/evidence/{manifest,required}.py`
  - **NEW:** `evaluation/tests/{unit,contract,fault}/scoring/`
- The `evaluation/` tree is not present at story-authoring time; create/update it only through completed predecessor contracts. Do not modify `src/memrelay/` or root `pyproject.toml`.

### Library, Test, and Version Guardrails

- Evaluator Python is exactly 3.13; preserve the independent `evaluation/pyproject.toml` and `uv.lock`.
- CI is provider-free and no-network. Do not add a grading service or provider SDK; use stdlib process/filesystem primitives plus the benchmark's frozen dependencies.
- Validate canonical SHA-256 identity, mutation resistance, deterministic replay, five-of-five baseline/gold intake, bounded flaky classification, identical-artifact re-grade, environment credential absence, restricted network, typed crash evidence, fake-port ineligibility, and hard-blocker non-overridability.
- Preserve the product Python range and root dependency bounds. Any grader, dependency, schema, normalization, threshold, or network-policy change requires a new protocol version.

### Preserved Behavior and Anti-Patterns

- Preserve Story 2 workspace isolation, attempt evidence, exposure/retry lineage, capped/failed attempts, and treatment-neutral identifiers.
- Do not grade a live workspace, fetch unpinned dependencies, infer success from partial output, normalize away raw bytes, expose hidden tests, or treat timing as deterministic score content.
- Do not let a judge/adjudicator override executable failure; do not claim fake artifacts are CAS-qualified; do not make any provider call.

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` — Epic 3; Story 3.1; FR33]
- [Source: `ARCHITECTURE-SPINE.md` — AD-09, AD-13, AD-15, AD-22; Stack; Implementation Freeze]
- [Source: `IMPLEMENTATION-DESIGN.md` — §§5-7, 19.1, 19.5-19.6, 22.1, 24-25]
- [Source: technical research — “Recommended Technical Stack”; executable-grader and immutable-evidence guidance]
- [Source: `_bmad-output/test-artifacts/test-design-qa.md` — `GRADE-*`, `TEL-EXECUTABLE`, `ITT-GRADER-FAILURE`, `BLOCK-*`]
- [Source: `_bmad-output/test-artifacts/test-design-architecture.md` — R-010, R-018, R-026]
- [Source: `SPEC.md` — §2 and §8 product ownership/boundary]
- [Source: `pyproject.toml` — Python and product dependency/tooling bounds]

## Dev Agent Record

### Agent Model Used

TBD by implementation agent

### Debug Log References

### Completion Notes List

- Ultimate context engine analysis completed - comprehensive developer guide created

### File List
