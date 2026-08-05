# Story 2.1: Lock the Copilot Runtime and Qualified Models

Status: ready-for-dev

## Story

As a trial operator,
I want the official Copilot SDK runtime and eligible models discovered and hash-locked,
So that trials pause rather than silently substitute a changed execution substrate.

## Acceptance Criteria

1. **Given** `memrelay-eval bootstrap --backup-root <second-volume-path>`  
   **When** Copilot bootstrap runs  
   **Then** it accepts only `github-copilot-sdk==1.0.8` wheel `github_copilot_sdk-1.0.8-py3-none-any.whl` with SHA-256 `7c3d868b73daa3a154ae6c1c5a2bd9301c47349c6b080f1ed77ba9027da3e0fa`  
   **And** `python -m copilot download-runtime` runs once, records runtime version, binary SHA-256, transport, auth mode, and non-secret subscription identity in `runtime-lock.json`, then requires `COPILOT_SKIP_CLI_DOWNLOAD=1`.
2. **Given** the complete native `CopilotClient.list_models()` response  
   **When** `memrelay-eval lock-models` runs the frozen eight-task nonstudy qualification  
   **Then** it filters required capabilities; ranks by executable passes, protected-check fraction, active wall time, then native ID; selects `M0`, optional distinct-family `M1`, and qualifying low-credit `M2`; and maximizes judge-model diversity  
   **And** it freezes IDs, capabilities, reasoning, context, qualification evidence, and runtime in `model-lock.json`.
3. **Given** a missing or changed runtime, locked model, or required capability  
   **When** a stage starts  
   **Then** the stage pauses with a typed conformance failure  
   **And** no download, model substitution, public-name inference, or manual preference occurs.

## Dependencies and Prerequisites

- **Formal graph dependency:** Story 1.1 only.
- Story 1.1 supplies the separate Python 3.13 evaluator, typed domain records, CLI composition, and deterministic fake artifact/ledger/telemetry ports.
- If Stories 1.3 and 1.5 have already landed, reuse their sole RFC 8785 canonicalizer and immutable freeze/configuration records. They are not prerequisites in the authoritative graph; do not duplicate them or make this story depend on their orchestration.
- Bootstrap may acquire and verify the runtime, but qualification is nonstudy and cannot enroll a task or imply efficacy. Paid calls require explicit invocation and a finite eight-task/model envelope.

## Tasks / Subtasks

- [ ] Implement verified SDK/runtime bootstrap (AC: 1)
  - [ ] Pin the exact wheel and verify its SHA-256 before installation/use.
  - [ ] Download the bundled runtime exactly once, hash the executable bytes, record transport/auth/non-secret identity, and reject later implicit downloads.
  - [ ] Emit typed, redacted command/runtime manifests through Story 1 ports; keep fake-port evidence `unpaid_conformance`.
- [ ] Archive and project the complete native model catalog (AC: 2)
  - [ ] Preserve raw `list_models()` bytes plus a canonical capability projection; unsupported fields are `unavailable`, never inferred.
  - [ ] Implement the frozen eight-task arm-blind qualification and deterministic ranking/tie-breaks.
  - [ ] Select M0/M1/M2 and judge candidates mechanically; record omission reasons rather than substitutes.
  - [ ] Before any paid call, freeze the eligible-model count and an aggregate envelope of exactly eight task sessions per eligible model, with explicit Copilot credit, token, active-time, and wall-time caps; refuse missing caps and never retry a qualification task.
- [ ] Enforce runtime/model locks at every stage boundary (AC: 3)
  - [ ] Compare wheel, runtime, native catalog, selected IDs, capabilities, reasoning, and context.
  - [ ] Pause/version on drift; never repair by redownload or model substitution inside a block.
- [ ] Add offline fakes plus bounded paid conformance tests (AC: 1-3)

## Developer Context

The task-agent inference boundary is immutable: local Python 3.13 Inspect orchestration -> custom official Copilot SDK agent -> SDK-bundled runtime -> GitHub Copilot service under the owner's current subscription. Inspect is not a model provider. SDK BYOK, custom providers, OpenAI-compatible Copilot endpoints, public model-name guessing, and alternate task clients are prohibited. `list_models()` is runtime truth.

### Architecture Compliance

- Follow AD-02, AD-17, AD-19, AD-22 and the frozen model algorithm in Implementation Design §13.
- Raw native catalog, canonical projection, qualification evidence, and locks are immutable artifacts; IDs remain treatment-neutral.
- `ArtifactStorePort`, `LedgerPort`, and `TelemetryPort` fakes are `unpaid_conformance` only. Paid/study provenance requires the Story 4.1 filesystem CAS, Story 4.2 sole-writer SQLite ledger, and Story 4.3 Collector adapters to pass conformance; no fake lock authorizes a study.
- Current-subscription authentication proof records identity/provenance without credentials.
- Any missing model/capability creates a typed pause/new stratum; never silently pool.

### Library and Version Requirements

- Python **3.13**; `github-copilot-sdk==1.0.8`; accepted wheel/digest exactly as AC1.
- SDK-bundled runtime downloaded once; later runs set `COPILOT_SKIP_CLI_DOWNLOAD=1`.
- Inspect AI `0.3.252` is a later orchestration dependency; do not route qualification through an Inspect provider.
- Root `pyproject.toml` is read-only. Preserve its complete dependency set and resolved product environment by hash rather than reconstructing a partial evaluator-side lock.

### Expected File Paths

- **UPDATE:** `evaluation/pyproject.toml`, `evaluation/uv.lock`
- **NEW:** `evaluation/src/memrelay_eval/adapters/copilot/{client,catalog,session}.py`
- **UPDATE:** `evaluation/src/memrelay_eval/orchestration/{control,stages}.py`
- **UPDATE:** `evaluation/src/memrelay_eval/domain/{entities,errors,policies}.py`
- **UPDATE:** `evaluation/src/memrelay_eval/cli/{main,commands}.py`
- **NEW:** `evaluation/tests/{unit,contract,integration}/copilot/`
- **READ ONLY:** root `pyproject.toml`; no product dependency/source update

### Existing Behavior to Preserve

- Product packaging, CLI, daemon/MCP, provider auth, and root dependency bounds remain unchanged.
- Epic 1 canonicalization, opaque identity, redaction, command manifests, and study-ineligibility of fakes remain authoritative.
- A failed bootstrap/qualification cannot replace the prior valid lock or leave a valid-looking partial lock.

### Testing Requirements

- Wheel/runtime digest mismatch, missing runtime, implicit redownload, auth provenance, and redaction tests.
- Golden catalog projection and repeated eight-task ranking tests, including ties and missing M1/M2.
- Drift matrix for model removal, changed capabilities/reasoning/context/runtime; every case pauses.
- Assert zero Inspect provider calls, SDK BYOK/custom providers, OpenAI credentials, public-name substitution, or unbounded qualification loops.
- CI uses only fakes; any live qualification test is explicit, finite, metered, and never automatic.
- A live `lock-models` invocation performs one eight-task suite per eligible archived native model and no other inference. It records planned versus consumed sessions/credits/tokens/time and stops before exceeding any frozen aggregate cap.

### Anti-Patterns

- Do not hardcode model IDs, reuse research-time CLI `1.0.78` as a trial pin, or choose models manually.
- Do not treat successful qualification as study evidence.
- Do not expose credentials or full environment dumps.
- Do not retry paid qualification until success or continue after a frozen model disappears.

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` — “Story 2.1”; AR3-AR5, AR35-AR36]
- [Source: `ARCHITECTURE-SPINE.md` — AD-17, AD-19, AD-22; Stack; Implementation Freeze]
- [Source: `IMPLEMENTATION-DESIGN.md` — §§5, 13, 22, 24.1, 24.4]
- [Source: technical research — “Corrected runtime architecture and trust boundaries”; model catalog selection]
- [Source: `test-design-qa.md` — `MODEL-*`, `AUTH-*`, `TOOL-INSPECT-EVAL`]
- [Source: TEA handoff — runtime/catalog blockers and quality gates]

## Dev Agent Record

### Agent Model Used

TBD by implementation agent

### Debug Log References

### Completion Notes List

- Ultimate context engine analysis completed - comprehensive developer guide created

### File List
