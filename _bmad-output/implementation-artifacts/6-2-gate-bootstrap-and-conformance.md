# Story 6.2: Gate Bootstrap and Conformance

Status: ready-for-dev

## Story

As a study operator,
I want a complete bootstrap/conformance gate before enrollment,
so that paid trials cannot start on an unqualified evaluator substrate.

## Acceptance Criteria

1. **Given** a clean Python 3.13 evaluator environment and valid Copilot subscription authentication
   **When** `memrelay-eval bootstrap` and `memrelay-eval conformance` run  
   **Then** runtime, catalog, credential, workspace/clone isolation, grader, judge, telemetry, CAS, backup/restore, and reconciliation contracts are tested  
   **And** `runtime-lock.json` and `conformance-report.json` bind every proof and version/hash from prior stories.
2. **Given** fake providers and the synthetic catalog  
   **When** unpaid CI conformance runs  
   **Then** one catalog-to-report path completes with no Copilot or OpenAI call  
   **And** schema, state machine, concealment, isolation, blinding, telemetry faults, CAS corruption, reconciliation, repricing, Parquet, and no-network tests pass.
3. **Given** any failed proof  
   **When** enrollment is requested  
   **Then** all study enrollment is blocked until repaired conformance is rerun  
   **And** successful construction is labeled conformance, not efficacy evidence.

## Tasks / Subtasks

- [ ] Assemble the bootstrap and conformance proof registry (AC: 1)
  - [ ] Bind each proof to schema, implementation, environment, catalog, protocol, SDK/runtime/model, grader/judge, telemetry, CAS, backup/restore, reconciliation, price, and limit hashes.
  - [ ] Require both worktree and isolated-clone contracts and a clean second-volume restore drill; one passing provider cannot mask another failure.
- [ ] Implement deterministic unpaid vertical-slice conformance (AC: 2)
  - [ ] Use only fake Copilot/OpenAI/memrelay ports and the synthetic catalog from compile through assignment, evidence, reconciliation, Parquet, analysis, and report.
  - [ ] Enforce a network-deny harness and assert no ambient provider credentials or paid request path is reachable.
- [ ] Enforce enrollment blocking and authorization (AC: 1, 3)
  - [ ] Seal `runtime-lock.json` and `conformance-report.json`; any absent, failed, stale, corrupt, incomplete, or conflicting proof rejects stage authorization.
  - [ ] Require role-attributed independent acceptance of the conformance exit and a separately scoped integration authorization; the conformance command cannot mint either from its own success.
- [ ] Bound every provider-touching conformance path (AC: 1-3)
  - [ ] Keep synthetic CI at zero Copilot/OpenAI calls; separately freeze catalog/conformance at hard 1M task-agent tokens, a model-specific AI-credit cap, 0.1M framework input, 0.05M framework output plus USD cap, 15 active minutes/run, and four local elapsed hours.
  - [ ] Refuse a provider qualification without all applicable caps, preserve consumption and terminal evidence on trip, and never represent the bounded qualification sample as efficacy.
- [ ] Make rerun/resume idempotent (AC: 1-3)
  - [ ] Reuse immutable passed proof receipts only when every input/implementation hash matches; rerun invalidated proofs and issue a new report identity.
  - [ ] Preserve failed/interrupted evidence, never overwrite the last report, and prevent partial reports from becoming entry bundles.
- [ ] Add monitoring, alerts, runbooks, and coverage (AC: 1-3)
  - [ ] Report outcome-blind proof totals/status, consumed/remaining qualification envelopes, stale locks, network attempts, backup/restore timing, Collector faults, reconciliation completeness, and enrollment-block reason.
  - [ ] Runbook: stop enrollment, preserve proof artifacts, repair the failed component, rerun the full dependent proof closure, independently accept the new exit, then authorize integration.
  - [ ] Test every proof failure, tamper, stale dependency, interruption, duplicate invocation, no-network path, and conformance-versus-efficacy label.

## Dependencies

These are the exact formal story dependencies from the epic graph.

- Stories 2.2 and 2.3: workspace and process/credential isolation.
- Story 3.6: outcome authority and blockers.
- Stories 4.1, 4.3, 4.5, and 4.8: durable CAS, telemetry, reconciliation, backup/restore.
- Story 5.6: offline replay.
- Story 6.1: stage bundles and CLI enforcement.

## Dev Notes

### Developer Context

Bootstrap/conformance is a declared stage with entry (clean Python 3.13 environment plus valid subscription authentication), exit (every named contract passes), and failure consequence (repair and repeat; no enrollment). It proves substrate readiness only. It must never be promoted into efficacy, safety, economics, or release evidence. Integration remains a separately authorized 32-run stage.

Pause on any lock drift or incomplete proof. Resume is allowed only under the same immutable input set; otherwise create a new conformance report/protocol identity. Evidence is fail-closed and append-only.

Current-checkout fact: no `evaluation/` tree exists and predecessor stories are ready-for-dev guides rather than implemented learnings. Build only after those seams exist; product `src/memrelay/**`, current observation classes, and bounded product regression tests remain read-only inputs, not evaluator conformance or release evidence.

### Architecture, Versions, and Preservation

- Apply the complete frozen stack and hashes from Story 6.1; do not “upgrade to latest.” Frozen architecture supersedes older research suggestions.
- `runtime-lock.json` must prove the SDK-bundled runtime downloaded once and later disabled with `COPILOT_SKIP_CLI_DOWNLOAD=1`; `conformance-report.json` must point to immutable proof artifacts.
- Local-only infrastructure and bounded calls apply. Unpaid CI makes zero Copilot/OpenAI calls. Provider qualification, when explicitly invoked, remains bounded and arm-blind, not efficacy.
- Preserve domain-owned ports, fake-versus-durable provenance, sole-writer ledger, exact canonicalizer, credential process boundaries, and second-volume RPO/RTO evidence.

### File Targets

- **UPDATE/CREATE:** `evaluation/src/memrelay_eval/orchestration/stages.py`, `control.py`
- **UPDATE/CREATE:** `evaluation/src/memrelay_eval/cli/commands.py`
- **NEW:** `evaluation/src/memrelay_eval/evidence/conformance.py`
- **NEW:** `evaluation/schemas/conformance-report.schema.json`
- **NEW:** `evaluation/docs/runbooks/conformance.md`
- **NEW:** `evaluation/tests/contract/test_bootstrap_conformance.py`
- **NEW:** `evaluation/tests/integration/test_unpaid_catalog_to_report.py`
- **NEW:** `evaluation/tests/fault/test_conformance_fail_closed.py`
- **READ ONLY:** product code and root `pyproject.toml`; evaluator dependencies remain isolated.

### Testing and Anti-Pattern Guardrails

- Use deterministic fakes for CI and assert sockets/HTTP/provider clients are unreachable.
- Verify schema/state/concealment/isolation/blinding/telemetry/CAS/reconciliation/repricing/Parquet/no-network proof closure.
- Preserve exact TEA stable IDs and objective verdicts in generated traceability: `CATALOG-SCHEMA`, `CATALOG-HASHES`, `CATALOG-MAPPINGS`, `CATALOG-CI`, `MODEL-CATALOG-SNAPSHOT`, `MODEL-SELECTION-PIN`, `AUTH-COPILOT-SUBSCRIPTION`, `AUTH-SDK-BYOK-DENY`, `SECRET-OPENAI-ISOLATION`, `FRAMEWORK-ROUTE-FAIL-CLOSED`, `ARM-PARITY`, `TEL-PROVIDER-IDENTITY`, `COST-PROVIDER-LEDGERS`, `TOOL-INSPECT-CONFORMANCE`, `TOOL-OTEL-CONFORMANCE`, `TOOL-OPENINFERENCE-CONFORMANCE`, and `TOOL-PARQUET-CONFORMANCE`.
- Do not accept aggregate pass percentages, warnings for mandatory proofs, stale receipts, same-volume backup, a single workspace provider, OTLP delivery as completeness, construction as efficacy, manual checkboxes, or automatic fallback.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story-6.2-Gate-Bootstrap-and-Conformance]
- [Source: _bmad-output/planning-artifacts/architecture/architecture-dfinson-solid-happiness-2026-08-05/IMPLEMENTATION-DESIGN.md#22.1-Unpaid-CI]
- [Source: _bmad-output/planning-artifacts/architecture/architecture-dfinson-solid-happiness-2026-08-05/IMPLEMENTATION-DESIGN.md#25-Mandatory-Conformance-Proofs]
- [Source: _bmad-output/test-artifacts/test-design/dfinson-solid-happiness-handoff.md#Phase-Transition-Quality-Gates]
- [Source: _bmad-output/planning-artifacts/implementation-readiness-report-2026-08-05.md#Correction-Verification]

## Dev Agent Record

### Agent Model Used

To be recorded by the implementing dev agent.

### Debug Log References

### Completion Notes List

- Ultimate context engine analysis completed - comprehensive developer guide created

### File List
