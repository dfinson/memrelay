# Story 2.3: Launch Disposable Credential-Isolated Processes

Status: done

## Story

As a trial operator,
I want disposable processes launched from minimal credential allowlists,
So that process boundaries cannot leak provider credentials or state across attempts.

## Acceptance Criteria

1. **Given** Inspect control, Copilot worker, memrelay daemon, MCP client, grader, judge, Collector, and analysis processes  
   **When** each is launched from a minimal allowlist  
   **Then** only Copilot worker/judges receive host Copilot auth, only the daemon receives its OpenAI key, and every other process receives no provider credential  
   **And** OpenAI variables never enter agent/MCP processes and GitHub/Copilot credentials never enter framework processes.
2. **Given** disposable process environments containing synthetic credential canaries  
   **When** process-boundary conformance runs  
   **Then** each process can observe only canaries authorized for its credential domain  
   **And** any canary crossing a prohibited process boundary fails conformance with preserved non-secret evidence.
3. **Given** process completion, failure, timeout, or cancellation  
   **When** process cleanup runs as compensating work  
   **Then** worker, socket, and process cleanup evidence is recorded  
   **And** the disposable worker is never reused across attempts.

## Dependencies and Prerequisites

- **Formal graph dependency:** Story 2.2 only.
- Consume credential references as variable-name + target-process metadata and the conservative exposure/retry contracts established by Epic 1 when present; do not import those stories' orchestration or make them new graph prerequisites.
- Process launch failure does not become retryable unless exposure evidence and the frozen protocol authorize it.
- Use fakes for each process role first; real provider processes are introduced only by their owning stories.

## Tasks / Subtasks

- [x] Define process roles and explicit environment allowlists (AC: 1)
  - [x] Start from a minimal baseline rather than filtering an inherited full environment.
  - [x] Bind each credential reference to exactly one role and reject unknown/key-bearing variables.
- [x] Implement disposable local launcher and bounded supervision (AC: 1, 3)
  - [x] Give each child attempt-local cwd, roots, telemetry identity, stdio policy, timeout, and process-group/job ownership.
  - [x] Capture typed start/exit/cancel/timeout/cleanup records without secret values.
- [x] Implement canary-based boundary conformance (AC: 2)
  - [x] Probe every process role and every prohibited credential-domain crossing.
  - [x] Store only canary ID/location/verdict; never echo a real or synthetic secret value.
- [x] Implement idempotent process/socket cleanup and nonreuse (AC: 3)
- [x] Add Windows/POSIX, inheritance, crash-tree, timeout, and cancellation tests (AC: 1-3)

## Developer Context

Credential isolation is by OS process. Task-agent and judge workers use host Copilot authentication only; they never receive OpenAI variables. The memrelay framework daemon receives its configured OpenAI credential only and no GitHub/Copilot export. Inspect control, MCP, grader, Collector, analysis, artifact, and ledger processes receive neither. Judges are fresh and role-separated from task agents even though both use Copilot auth.

### Architecture Compliance

- Follow AD-06, AD-08, AD-09, AD-17, AD-18 and Implementation Design §§15.2-15.3.
- One disposable worker/process tree per attempt; bounded local concurrency; no worker reuse.
- Environment variables are credential delivery only, not general evaluator configuration.
- All launcher evidence is redacted and treatment-neutral. Fake `ArtifactStorePort`, `LedgerPort`, and `TelemetryPort` implementations remain unpaid-only until the Story 4.1 CAS, Story 4.2 ledger, and Story 4.3 Collector adapters pass conformance.
- Cleanup does not erase terminal evidence; ambiguous exposure prohibits retry.

### Library and Version Requirements

- Python 3.13; use stdlib subprocess/process APIs unless an already locked evaluator dependency is required.
- Future process pins remain exact: Copilot SDK `1.0.8`, Inspect `0.3.252`, OTel `1.44.0`, Collector `0.158.0`.
- Framework model is `gpt-4.1-mini-2025-04-14`; credential material is not embedded in config.

### Expected File Paths

- **NEW:** `evaluation/src/memrelay_eval/adapters/process/{launcher,environment,cleanup}.py`
- **UPDATE:** `evaluation/src/memrelay_eval/orchestration/{worker,attempt,limits}.py`
- **UPDATE:** `evaluation/src/memrelay_eval/domain/{entities,ports,errors,policies}.py`
- **NEW:** `evaluation/tests/contract/process/test_environment_allowlists.py`
- **NEW:** `evaluation/tests/fault/process/test_cleanup.py`
- **READ ONLY:** `src/memrelay/_subprocess.py`; evaluator must not alter product subprocess behavior

### Existing Behavior to Preserve

- Story 2.2 workspace ownership and Story 1 exposure/retry/immutability rules.
- Product daemon/MCP startup semantics remain unchanged; evaluator launches them as shipped later.
- Logs/manifests omit prompts, code, repository/user names, credentials, treatment labels, and provider payloads.

### Testing Requirements

- Full role x credential-domain allow/deny matrix and synthetic-canary scans.
- Verify children cannot observe undeclared ambient variables and environment dumps are never persisted.
- Process-tree timeout/cancel/crash tests prove bounded cleanup and partial evidence retention.
- Reuse/collision tests prove a completed or failed worker cannot service another attempt.
- CI uses inert child fakes only and performs no paid/network calls.

### Anti-Patterns

- Do not copy `os.environ`, pass OpenAI variables to agent/MCP, or pass GitHub tokens to daemon/framework.
- Do not use one long-lived worker, shared socket, or shared process cache across attempts.
- Do not echo leaked values in errors/evidence.
- Do not classify every launch error as pre-exposure or retry repeatedly.

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` — “Story 2.3”]
- [Source: `ARCHITECTURE-SPINE.md` — AD-06, AD-08, AD-09, AD-17, AD-18]
- [Source: `IMPLEMENTATION-DESIGN.md` — §§5, 15, 20]
- [Source: technical research — corrected trust boundaries and credential isolation]
- [Source: `test-design-qa.md` — `SECRET-OPENAI-ISOLATION`, `AUTH-SDK-BYOK-DENY`]

## Dev Agent Record

### Agent Model Used

GPT-5.6 Terra

### Debug Log References

### Completion Notes List

- Added stdlib-only process adapters with explicit role/domain credential allowlists.
- Added inert child-process synthetic canary conformance with value-free boundary evidence.
- Added per-role/per-attempt disposable launch ownership, process-tree timeout/cancellation
  compensation, terminal socket cleanup, and no-reuse enforcement.
- Added bounded attempt-worker coordination plus contract, fault, and architecture tests.

### File List

- evaluation/src/memrelay_eval/adapters/process/__init__.py
- evaluation/src/memrelay_eval/adapters/process/environment.py
- evaluation/src/memrelay_eval/adapters/process/cleanup.py
- evaluation/src/memrelay_eval/adapters/process/launcher.py
- evaluation/src/memrelay_eval/orchestration/limits.py
- evaluation/src/memrelay_eval/orchestration/worker.py
- evaluation/src/memrelay_eval/domain/errors.py
- evaluation/src/memrelay_eval/domain/ports.py
- evaluation/tests/contract/process/test_environment_allowlists.py
- evaluation/tests/fault/process/test_process_cleanup.py
- evaluation/tests/architecture/test_process_boundary.py
- _bmad-output/implementation-artifacts/sprint-status.yaml

### Change Log

- 2026-08-07: Implemented disposable credential-isolated process launch conformance.
