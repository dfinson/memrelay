# Story 2.5: Verify Agent and Environment Parity

Status: review

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

- Python 3.13; exact Copilot SDK/runtime/model locks and Inspect `0.3.252`.
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

GPT-5.6 Terra (gpt-5.6-terra)

### Debug Log References

- `$env:PYTHONPATH = (Resolve-Path .\evaluation\src).Path; py -3.13 -m pytest .\evaluation\tests\unit\orchestration\test_parity.py .\evaluation\tests\contract\test_environment_strata.py .\evaluation\tests\contract\inspect\test_inspect_copilot_contract.py .\evaluation\tests\fault\test_inspect_execution_terminal_paths.py` — 57 passed.
- `py -3.13 -m ruff check .\evaluation\src .\evaluation\tests; py -3.13 -m ruff format --check .\evaluation\src .\evaluation\tests` — passed.
- `$env:PYTHONPATH = (Resolve-Path .\evaluation\src).Path; py -3.13 -m pytest .\evaluation\tests` — 784 passed, 4 platform-capability skips.
- `$env:PYTHONPATH = (Resolve-Path .\src).Path; py -3.13 -m pytest .\tests` — 1303 passed, 4 optional-backend skips.
- `$env:PYTHONPATH = (Resolve-Path .\evaluation\src).Path; py -3.13 -m pytest .\evaluation\tests\unit\orchestration\test_parity.py .\evaluation\tests\fault\test_inspect_execution_terminal_paths.py .\evaluation\tests\contract\ledger\test_repository.py` — 107 passed, 1 POSIX-only skip.
- `py -3.13 -m ruff format .\evaluation\src .\evaluation\tests; py -3.13 -m ruff check .\evaluation\src .\evaluation\tests; py -3.13 -m ruff format --check .\evaluation\src .\evaluation\tests` — passed.
- `$env:PYTHONPATH = (Resolve-Path .\evaluation\src).Path; py -3.13 -m pytest .\evaluation\tests` — 796 passed, 4 platform-capability skips.
- `$env:PYTHONPATH = (Resolve-Path .\src).Path; py -3.13 -m pytest .\tests` — 1303 passed, 4 optional-backend skips.
- `git diff --check` — passed.

### Completion Notes List

- Added canonical, lower-case SHA-256 parity records for locked SDK/runtime/model controls, prompt-component bytes, tool schemas, policies, limits, workspace topology, memory/store settings, frozen configuration, and host strata.
- Added sealed protocol delta commitments, frozen enrollment bindings, typed pre-exposure parity evidence, and a scheduler gate that records infrastructure failure before task delivery or inference.
- Added environment-stratum linkage and ordinary aggregation denial, preserving distinct host strata instead of pooling changed fingerprints.
- Added fake-only unit, contract, fault, and existing workspace contract coverage; no provider or paid call is made by CI.
- Remediated review blocker B1: the execution path no longer trusts caller-supplied delta allowances. It derives opaque system-prompt, user-prompt, and access delta commitments from verified canonical protocol-lock bytes, binds both arms to one sealed pair, and rejects forged, swapped, replayed, malformed, duplicate, missing, and mutable-alias inputs.
- Remediated review blocker B2: the sole ledger authority atomically claims each open attempt before either parity handling or scheduler invocation. A terminal or competing claim is denied before task delivery or inference; an authorized retry still requires a fresh attempt ID.

### File List

- `evaluation/src/memrelay_eval/domain/environment.py`
- `evaluation/src/memrelay_eval/domain/errors.py`
- `evaluation/src/memrelay_eval/domain/ports.py`
- `evaluation/src/memrelay_eval/orchestration/parity.py`
- `evaluation/src/memrelay_eval/orchestration/attempt.py`
- `evaluation/src/memrelay_eval/orchestration/inspect.py`
- `evaluation/src/memrelay_eval/adapters/fakes.py`
- `evaluation/src/memrelay_eval/adapters/workspace/base.py`
- `evaluation/src/memrelay_eval/ledger/repository.py`
- `evaluation/src/memrelay_eval/ledger/schema.py`
- `evaluation/tests/unit/orchestration/test_parity.py`
- `evaluation/tests/contract/test_environment_strata.py`
- `evaluation/tests/contract/ledger/test_repository.py`
- `evaluation/tests/contract/workspace/test_isolation.py`
- `evaluation/tests/fault/test_inspect_execution_terminal_paths.py`
- `evaluation/README.md`
- `_bmad-output/implementation-artifacts/2-5-verify-agent-and-environment-parity.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
