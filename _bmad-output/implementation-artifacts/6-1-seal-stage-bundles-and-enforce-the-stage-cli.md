# Story 6.1: Seal Stage Bundles and Enforce the Stage CLI

Status: ready-for-dev

## Story

As a study operator,
I want immutable stage entry and exit bundles enforced by non-interactive commands,
so that no stage can promote itself merely because its processes completed.

## Acceptance Criteria

1. **Given** a stage configuration  
   **When** its entry bundle is sealed  
   **Then** exact catalog, protocol, SDK, runtime, model, environment, grader, judge, telemetry, price, limit, and preceding-exit hashes are recorded  
   **And** post-seal changes require a new protocol or stage ID.
2. **Given** `memrelay-eval run --stage integration|pilot|primary|secondary|cross-repo`  
   **When** the preceding immutable exit bundle is absent, corrupt, rejected, or incomplete  
   **Then** entry is refused with a typed status before enrollment  
   **And** no automatic fallback topology or stage promotion occurs.
3. **Given** any bootstrap, lock, compile, conformance, run, reconcile, analyze, or report command  
   **When** it terminates  
   **Then** it is non-interactive and writes a manifest with input/output hashes, runtime lock, protocol ID, and typed terminal status  
   **And** paid execution requires explicit operator invocation or approved scheduling.

## Tasks / Subtasks

- [ ] Define immutable stage policy and bundle contracts (AC: 1, 2)
  - [ ] Add treatment-neutral stage IDs, `planned|authorized|running|paused|closing|accepted|rejected` transitions, typed reasons, and canonical entry/exit bundle records.
  - [ ] Hash bundles with the shared RFC 8785/SHA-256 service; bind schema/version identifiers, all frozen inputs, the accepted preceding-exit digest, reconciliation/decision receipts, and authorization artifact IDs.
  - [ ] Reject mutation, skipped stages, stale authorization, re-entry after rejection, and cross-repository entry without a current DG-R qualification.
- [ ] Enforce explicit, independent stage authorization (AC: 1, 2, 3)
  - [ ] Separate process completion from reconciliation, exit acceptance, and authorization of the next stage.
  - [ ] Require an immutable operator/scheduler authorization artifact scoped to one stage ID, envelope, protocol, frozen entry digest, authorizer identity/role, and validity period.
  - [ ] Prevent the execution command or stage process from accepting its own exit or minting its next-stage authorization; successful construction, reconciliation, or process completion is never authorization.
- [ ] Implement idempotent pause/resume and command behavior (AC: 2, 3)
  - [ ] Repeated sealing or resume with identical hashes returns the existing result; conflicting inputs fail closed.
  - [ ] Resume only unfinished planned units after verifying locks, authorization, prior receipts, ledger/CAS consistency, and circuit-breaker state; never rerun terminal units or replace attempts.
  - [ ] Every invocation, including idempotent replays, emits exactly one append-only command manifest linked to any reused result and preserves partial evidence.
- [ ] Wire the non-interactive stage CLI (AC: 2, 3)
  - [ ] Add `run --stage integration|pilot|primary|secondary|cross-repo` entry guards and common manifest handling for all required commands.
  - [ ] Reject prompts, ambient non-secret environment configuration, automatic topology/provider/model fallback, and paid execution from CI.
- [ ] Add monitor/alert runbooks and tests (AC: 1-3)
  - [ ] Emit treatment-neutral, outcome-blind local structured status for stage state, planned/started/terminal counts, reconciliation completeness, quota/cost/time headroom, throttle/model health, evidence-loss signals, and authorization expiry.
  - [ ] Define alerts and operator actions for lock drift, incomplete evidence, categorical blockers, exhausted envelopes, stale authorization, backup failure, and DG-R revocation; alerts pause/stop new work but never mutate evidence.
  - [ ] Cover canonicalization, tamper, missing predecessor, idempotent replay, crash/resume, manifest terminal paths, deny-by-default cross-repo, and no-network/no-paid-CI behavior.

## Dependencies

These are the exact formal story dependencies; later stage stories consume this contract but are not prerequisites.

- Story 1.8: offline catalog-to-manifest command behavior.
- Stories 2.1 and 2.10: runtime/model locks and native terminal evidence.
- Story 3.6: separate outcome authorities and categorical blockers.
- Story 4.5: immutable reconciliation/inclusion decisions.
- Story 5.7: evidence-linked reports and bounded claims.

## Dev Notes

### Developer Context

Epic 6 is the operational gate layer over prior immutable evidence contracts. A stage is not a shell workflow: it is a domain state machine whose authority is split among execution completion, reconciliation/exit acceptance, and independent authorization for the next envelope. The fixed progression is bootstrap/conformance → 32-run integration → 128-unit blinded pilot → 512-unit primary → optional secondary roles (96 each, no more than 192 total). The 24-cluster cross-repository stage remains unreachable until primary completion and a complete, current DG-R bundle. Exploratory, construction, fixture, conformance, engine, pilot, or merely successful process evidence cannot promote a stage or support confirmatory claims.

Current-checkout fact: no `evaluation/` tree exists. All predecessor story files are ready-for-dev guides, not implemented learnings; implement in dependency order and reuse their declared ports/contracts rather than inventing substitutes. Current product source already includes `SessionDiscoveryPoller`, `RunObserveCapture`, and `LiveTailCapture`, but its existing retrieval/roundtrip tests remain bounded product evidence; Epic 6 must neither rewrite those seams nor claim sentinel/release qualification, which belongs to Epic 7.

### Architecture and Frozen Contract

- Python 3.13; evaluator remains a separate hexagonal modular monolith. Domain policy is stdlib-only; orchestration depends on domain ports; adapters do not import each other.
- Preserve exact pins: Copilot SDK `1.0.8` plus frozen wheel hash, Inspect `0.3.252`, OTel `1.44.0`, Collector `0.158.0` plus archive hash, OpenInference `0.1.31`, OpenAI instrumentation `0.1.53`, DuckDB `1.5.5`, PyArrow `25.0.0`, framework model `gpt-4.1-mini-2025-04-14`, and local `BAAI/bge-small-en-v1.5`.
- Catalog, protocol, runtime/SDK, model catalog/selection, environment, schema, grader/rubric/judge, telemetry map, price table, analysis, limits, endpoints, thresholds, stage rules, holdouts, and preceding-exit hashes are immutable locks. Drift pauses entry and requires a new protocol/stage identity; it is never patched in place.
- Required evidence is fail-closed. Missing, contradictory, corrupt, expired, or unauthorized evidence is not a warning and cannot be inferred from OTLP delivery or process success.
- Paid calls are bounded by prospectively sealed token, AI-credit, tool, framework-token/USD, active-time, elapsed-time, quota, throttle, concurrency, and evidence-loss limits. Infrastructure is local: files, SQLite WAL, CAS, Collector, Parquet, read-only DuckDB, and generated reports; no managed telemetry, warehouse, tracker, interactive UI, or cloud graph.

### File Targets

- **UPDATE:** `evaluation/src/memrelay_eval/domain/{states,policies,errors}.py`; keep stage policy in the established domain seams rather than inventing a second lifecycle module.
- **UPDATE/CREATE:** `evaluation/src/memrelay_eval/orchestration/stages.py`, `limits.py`, `control.py`
- **UPDATE/CREATE:** `evaluation/src/memrelay_eval/cli/commands.py`, `main.py`
- **NEW:** `evaluation/schemas/stage-bundle.schema.json`, `command-manifest.schema.json`
- **NEW:** `evaluation/docs/runbooks/stage-control.md`
- **NEW:** `evaluation/tests/unit/domain/test_stage_policy.py`
- **NEW:** `evaluation/tests/contract/test_stage_cli.py`
- **NEW:** `evaluation/tests/fault/test_stage_pause_resume.py`
- **READ ONLY:** product `src/memrelay/**`; evaluator dependencies must not enter the product wheel.

Preserve the sole-writer ledger, immutable CAS/manifests, opaque IDs, terminal attempt records, assignment concealment, one-pre-exposure-retry rule, and Parquet-only analysis boundary.

### Testing and Anti-Pattern Guardrails

- Unit/property tests: legal/illegal transitions, stable hashes, strict envelopes, authorization expiry, idempotency, and typed errors.
- Contract tests: each CLI terminal path writes a complete manifest; paid stages require explicit authorization; no paid calls occur in CI.
- Fault tests: interruption at every publication boundary, corrupt/missing/stale predecessor bundles, ledger/CAS conflict, lock drift, authorization revocation, and duplicate resume.
- Preserve TEA stable coverage IDs in the executable catalog and generated traceability: `OPS-PROTOCOL-FREEZE`, `OPS-STAGE-ENVELOPE`, `OPS-QUOTA-RATE-LIMIT`, `STAT-FIXED-LOOK`, and `SCOPE-R-DENY`.
- Do not implement mutable status files as authority, update/delete lifecycle history, “latest successful run” discovery, force flags, favorable subset promotion, threshold relaxation, automatic provider/model/topology fallback, cross-repo convenience bypass, or cloud services.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story-6.1-Seal-Stage-Bundles-and-Enforce-the-Stage-CLI]
- [Source: _bmad-output/planning-artifacts/architecture/architecture-dfinson-solid-happiness-2026-08-05/ARCHITECTURE-SPINE.md#AD-22---CI-and-paid-stages-remain-separate-ADOPTED]
- [Source: _bmad-output/planning-artifacts/architecture/architecture-dfinson-solid-happiness-2026-08-05/IMPLEMENTATION-DESIGN.md#24.3-Stage-Entry-Exit-and-Stop-Rules]
- [Source: _bmad-output/planning-artifacts/architecture/architecture-dfinson-solid-happiness-2026-08-05/IMPLEMENTATION-DESIGN.md#24.4-Required-CLI-Workflow-and-Artifacts]
- [Source: _bmad-output/test-artifacts/test-design-qa.md#Execution-Strategy]
- [Source: _bmad-output/planning-artifacts/research/technical-evaluating-memrelay-collective-recall-for-coding-agent-reliability-cost-and-efficiency-research-2026-08-04.md#Staged-run-dual-provider-budget-tool-and-wall-clock-envelope]

## Dev Agent Record

### Agent Model Used

To be recorded by the implementing dev agent.

### Debug Log References

### Completion Notes List

- Ultimate context engine analysis completed - comprehensive developer guide created

### File List
