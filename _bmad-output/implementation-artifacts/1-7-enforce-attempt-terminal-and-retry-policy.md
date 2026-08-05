# Story 1.7: Enforce Attempt Terminal and Retry Policy

Status: review

## Story

As an experiment controller,
I want immutable attempt outcomes and a single narrowly authorized retry path,
So that failures remain in ITT and favorable substitution is impossible.

## Acceptance Criteria

1. **Given** an attempt reaches any terminal condition  
   **When** its terminal record is appended  
   **Then** the classification and partial evidence are immutable and linked to the original run  
   **And** crashes, timeouts, provider failures, quota exhaustion, grading failure, cancellation, and evidence incompleteness remain observable outcomes.
2. **Given** a conclusively pre-exposure infrastructure failure and a protocol that authorizes retry  
   **When** retry is requested  
   **Then** exactly one new attempt ID with fresh isolation is linked to the same assignment  
   **And** the original attempt and its evidence remain unchanged.
3. **Given** ambiguous or post-exposure failure, an existing retry, or a request for best-of-N, repeated-until-success, or favorable substitution  
   **When** retry or inclusion is requested  
   **Then** it is rejected with a typed reason  
   **And** Inspect, SDK, memrelay, and grader internal retries are separately bounded and recorded.
4. **Given** attempt lifecycle or telemetry before Stories 4.2 and 4.3  
   **When** it is emitted  
   **Then** it flows through the Story 1.1 domain-owned `LedgerPort` and `TelemetryPort` using deterministic fakes for unpaid conformance  
   **And** durable study execution and inclusion remain blocked until the concrete sole-writer SQLite ledger and Collector adapters pass conformance.

## Dependencies and Prerequisites

- **Formal graph dependency:** Story 1.1 only.
- Story 1.1 must provide the run/attempt domain, exposure-state/error shape, and fake ports.
- Story 1.6 is an integration peer, not a formal prerequisite in `epics.md`. Keep retry policy dependent on a domain-owned conservative exposure decision/evidence contract rather than importing Story 1.6 orchestration. When Story 1.6 is present, its classifier supplies that contract.
- Fresh workspace/process isolation is an Epic 2 implementation. This story creates a requirement/specification record and refuses retry if the eventual provisioner cannot attest fresh isolation.
- **Repository baseline:** all evaluator UPDATE paths are created by Story 1.1; `evaluation/orchestration/attempt.py` may be new because no current evaluator tree exists.

## Tasks / Subtasks

- [ ] Implement immutable terminal append policy (AC: 1)
  - [ ] Map every frozen terminal classification to typed evidence requirements.
  - [ ] Link terminal record and partial evidence to run/assignment/attempt without changing run transitions.
  - [ ] Reject second terminal records or updates/deletes.
- [ ] Implement one-retry authorizer (AC: 2)
  - [ ] Require protocol authorization, exact `infrastructure_failed_pre_exposure`, conclusive unexposed evidence, no prior retry, and fresh-isolation attestation.
  - [ ] Create a new opaque attempt ID linked to the same run and assignment.
  - [ ] Preserve parent terminal/evidence bytes unchanged.
  - [ ] Treat absent, unknown, contradictory, or non-conclusive exposure evidence as exposed/non-retryable without requiring the Story 1.6 concrete classifier.
- [ ] Implement no-favorable-substitution guards (AC: 3)
  - [ ] Reject ambiguous/post-exposure, retry-of-retry, best-of-N, repeat-until-success, and outcome-driven selection with stable typed reasons.
  - [ ] Record bounded internal retry policies/counters separately for Inspect, SDK, memrelay, and grader.
  - [ ] Keep every assigned unit/attempt visible for future ITT analysis.
- [ ] Route unpaid records through fake ports and enforce eligibility denial (AC: 4)
- [ ] Add state/property/fault/lineage tests (AC: 1-4)

## Developer Context

Run lifecycle records are not attempt outcomes. A failed attempt does not invent a run transition. Only one conclusively pre-exposure infrastructure failure may be retried if the frozen protocol already permits it. Provider failure, quota exhaustion, timeout, agent failure, grader failure, and evidence failure after exposure are retained outcomes, not reasons to rerun.

### Architecture Compliance

- Frozen terminal vocabulary from Story 1.1/Implementation Design §8.2.
- Follow AD-18 and §20.2 exactly: one maximum, same assignment, new attempt ID, fresh isolation, immutable original, no post-exposure retry.
- Ambiguous exposure is exposed.
- Internal subsystem retries are evidence, not invisible implementation details.
- Fake records are unpaid conformance only; no durable inclusion before concrete ledger/Collector/reconciliation conformance.

### Library and Version Requirements

- Python 3.13; standard-library domain types only.
- Reuse Story 1.6 exposure records and Story 1.1 ID/error/port types.
- No retry library is required or authorized; policy is explicit domain code.
- Do not import Inspect `0.3.252` or Copilot SDK `1.0.8` yet; store future internal-retry policy as domain-owned records.

### Expected File Paths

- **UPDATE:** `evaluation/src/memrelay_eval/domain/{entities,states,errors,policies}.py`
- **NEW/UPDATE:** `evaluation/src/memrelay_eval/orchestration/attempt.py`
- **NEW:** `evaluation/src/memrelay_eval/orchestration/retry.py`
- **UPDATE:** `evaluation/src/memrelay_eval/adapters/fakes.py`
- **NEW:** `evaluation/tests/unit/domain/test_attempt_terminal.py`
- **NEW:** `evaluation/tests/unit/orchestration/test_retry.py`
- **NEW:** `evaluation/tests/contract/test_attempt_lineage.py`
- **NEW:** `evaluation/tests/fault/test_partial_terminal_evidence.py`

### Existing Behavior to Preserve

- Run transition graph, assignment concealment, exposure conservatism, opaque IDs, append-only records, and all parent evidence.
- No retry may alter catalog, protocol, assignment, or frozen configuration.
- Current product retry/backoff behavior is not modified; evaluator later records/bounds it at adapter boundaries.

### Testing Requirements

- Exhaustive terminal vocabulary tests with partial evidence and stable reason codes.
- Property tests prove exactly one immutable terminal record per attempt.
- Retry truth table covers protocol flag, exposure class, terminal class, existing retry, isolation attestation, and same assignment.
- Byte/hash comparison proves parent evidence unchanged after retry creation.
- Explicit rejection tests for best-of-N, repeated success, favorable later attempt, re-randomization, and post-exposure failure.
- Fake port records remain deterministic, redacted, and ineligible.
- Crash/interruption tests preserve terminal/partial evidence intent without falsely claiming durable persistence.

### Anti-Patterns

- Do not treat all infrastructure/provider failures as pre-exposure.
- Do not retry quota, timeout, agent, grader, or evidence failures based only on failure category.
- Do not overwrite or “supersede” a failed attempt.
- Do not select the most favorable attempt for inclusion.
- Do not imply in-memory persistence survives a process crash; durable recovery is later work.

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` — “Story 1.7”]
- [Source: `ARCHITECTURE-SPINE.md` — AD-11, AD-18]
- [Source: `IMPLEMENTATION-DESIGN.md` — §§8.2-8.3, 20.1-20.2]
- [Source: TEA handoff — `ITT-*` scenario guidance]
- [Source: `test-design-qa.md` — `ITT-AGENT-FAILURE`, `ITT-TIMEOUT`, `ITT-GRADER-FAILURE`, `ITT-INFRA-PRE`, `ITT-INFRA-POST`, `ITT-NO-FAVORABLE-SUB`]

## Dev Agent Record

### Agent Model Used

GPT-5.6 Terra

### Debug Log References

- `PYTHONPATH=evaluation/src py -3.12 -m pytest evaluation/tests/unit/orchestration/test_retry.py evaluation/tests/unit/domain/test_attempt_terminal.py evaluation/tests/contract/test_attempt_lineage.py evaluation/tests/fault/test_partial_terminal_evidence.py` - 31 passed
- `PYTHONPATH=evaluation/src py -3.12 -m pytest evaluation/tests` - 133 passed
- `py -3.12 -m compileall -q evaluation/src` - passed
- `git diff --check` - passed
### Completion Notes List

- Added immutable attempt terminal recording without modifying run lifecycle state.
- Added conservative one-time retry authorization for only conclusively unexposed pre-exposure infrastructure failures, with fresh-isolation evidence and original evidence retained.
- Added atomic fake-ledger retry reservations for per-subsystem internal retries and retry authorizations.
- Persisted exposure and isolation evidence references with retry authorization lineage.
- Verified terminal vocabulary, lineage, fail-closed exposure handling, no-favorable-substitution guards, and concurrent retry-limit behavior through deterministic conformance tests.

### File List

- `evaluation/src/memrelay_eval/adapters/fakes.py`
- `evaluation/src/memrelay_eval/domain/entities.py`
- `evaluation/src/memrelay_eval/domain/errors.py`
- `evaluation/src/memrelay_eval/domain/ports.py`
- `evaluation/src/memrelay_eval/domain/states.py`
- `evaluation/src/memrelay_eval/orchestration/__init__.py`
- `evaluation/src/memrelay_eval/orchestration/attempt.py`
- `evaluation/src/memrelay_eval/orchestration/retry.py`
- `evaluation/tests/contract/test_attempt_lineage.py`
- `evaluation/tests/fault/test_partial_terminal_evidence.py`
- `evaluation/tests/unit/domain/test_attempt_terminal.py`
- `evaluation/tests/unit/orchestration/test_retry.py`
