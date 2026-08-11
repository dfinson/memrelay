# Story 6.1: Seal Stage Bundles and Enforce the Stage CLI

Status: review

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

Claude (Anthropic), operating as the `bmad-orchestrator` dev-story agent in Copilot CLI.

### Debug Log References

- 2026-08-11T13:10:35-04:00 - Targeted Story 6.1 suites (`tests/unit/domain/test_stage_policy.py`, `tests/contract/test_stage_cli.py`, `tests/fault/test_stage_pause_resume.py`): 84 passed under CPython 3.12.10 on native Windows with `evaluation\src` and product `src` on `PYTHONPATH`.
- 2026-08-11T13:08:00-04:00 - Affected `tests/contract` + `tests/unit/domain` regression run: 519 passed, 3 skipped.
- 2026-08-11T13:17:00-04:00 - Full evaluator suite (`tests`): 1394 passed, 46 skipped, 1 failed. The sole failure `tests/unit/judge/test_sdk_runtime.py::test_judge_runtime_uses_sdk_tool_objects_and_denies_unapproved_artifacts` is a pre-existing SDK-session-shape incompatibility (`ConformancePauseError: sdk_judge_session_shape_unsupported`) present on the pre-change baseline and unrelated to Story 6.1 (baseline: 1310 passed / 1 failed; +84 new Story 6.1 tests).
- 2026-08-11T13:10:40-04:00 - Root `ruff check` and `ruff format --check` on all changed files, plus `git diff --check`: passed.
- 2026-08-11T14:00:00-04:00 - A separate context-review pass (different agent invocation, same coordinating session) found four correctness gaps in the initial implementation: (1) an uncaught `CanonicalizationError` in `_canonical_stage_document` could crash the CLI on malformed sealed-bundle bytes (e.g. NaN/Infinity tokens) instead of failing closed with a typed manifest; (2) `StageBundleStore._write_once` had a check-then-act TOCTOU race that let a concurrent differing-content reseal silently clobber an already-sealed bundle instead of raising `stage_bundle_mutation`; (3) `StageEntryBundle.locks` was a mutable `dict` despite the frozen dataclass, so in-process mutation after construction could change the "sealed" digest; (4) `_write_immutable_stage_manifest`'s temp filename lacked a per-call unique token and had no post-publish verification. All four were fixed: `_canonical_stage_document` now catches `CanonicalizationError` and converts it to the typed `StageControlError`; `StageBundleStore._write_once` publishes via `os.link` onto the final path so a losing concurrent writer gets `FileExistsError` instead of clobbering; `StageEntryBundle.locks` is now a `types.MappingProxyType`; `_write_immutable_stage_manifest`'s temp filename includes pid+uuid and the function re-reads and verifies the published bytes. Four new regression tests added to `test_stage_pause_resume.py`. Re-ran targeted suites (88 passed) and the full suite (1398 passed, 46 skipped, 1 pre-existing unrelated failure) after the fixes; `ruff check`/`ruff format --check` pass.

### Completion Notes List

- Added the frozen study-stage domain layer: `StageKind`/`StageState` enums, `StageId`/`StageAuthorizationId`, typed `StageControlError`/`StageAuthorizationError`, the `planned|authorized|running|paused|closing|accepted|rejected` transition graph, the fixed predecessor map, the twelve-field entry-lock policy, and the independent-authorizer-role predicate that rejects self-authorization.
- Implemented immutable `StageEntryBundle`, `StageExitBundle`, and `StageAuthorization` seals over the shared RFC 8785/SHA-256 canonical service; each binds every frozen input hash (catalog, protocol, SDK, runtime lock, model lock, environment, grader, judge, telemetry, price table, limits) plus the accepted preceding-exit digest, and the price/limit envelope digest that scopes authorization. Any post-seal change yields a different digest, so a legitimate change requires a new protocol or stage identity.
- Enforced fail-closed stage entry (`authorize_stage_entry`) that refuses before enrollment with a typed status when the predecessor exit is missing, corrupt, rejected, incomplete, mis-linked, or skipped, when the authorization is mis-scoped, envelope-mismatched, self-minted, or stale, with no automatic topology fallback and no self-promotion.
- Separated independent authorization from process completion: the `run` command only loads an operator/scheduler authorization sealed against the pre-execution entry digest and envelope, and never constructs or mints one.
- Added content-addressed write-once persistence (`StageBundleStore`) with idempotent reuse on identical bytes and fail-closed `stage_bundle_mutation` on conflict, and idempotent `plan_stage_resume` that resumes only unfinished planned units after verifying authorization currency, locks, receipts, ledger/CAS consistency, and circuit-breaker state.
- Wired the non-interactive stage CLI: `run --stage integration|pilot|primary|secondary|cross-repo`. `cross-repo` preserves the Story 7.3 deny-before-discovery refusal byte-for-byte (no repository/credential leakage, no manifest). The four paid stages reject ambient stage configuration and CI-driven paid execution, then emit exactly one append-only command manifest binding command identity, stage, terminal status, exit code, input/output hashes, runtime lock, and protocol id on both success and typed-refusal paths; idempotent replays reuse the same manifest.
- Added the shared `stage_command_manifest` composer, `stage-bundle.schema.json`, `command-manifest.schema.json`, the `docs/runbooks/stage-control.md` monitor/alert runbook, and treatment-neutral outcome-blind `stage_status_projection`/`stage_alert_actions`.
- Added unit domain-policy tests, contract CLI tests, and fault pause/resume tests (84 total). Preserved all Story 1.x-5.x and 7.3 behavior: full suite shows no new failures.

### File List

- `evaluation/src/memrelay_eval/domain/states.py`
- `evaluation/src/memrelay_eval/domain/ids.py`
- `evaluation/src/memrelay_eval/domain/errors.py`
- `evaluation/src/memrelay_eval/domain/policies.py`
- `evaluation/src/memrelay_eval/orchestration/limits.py`
- `evaluation/src/memrelay_eval/orchestration/stages.py`
- `evaluation/src/memrelay_eval/evidence/manifest.py`
- `evaluation/src/memrelay_eval/cli/commands.py`
- `evaluation/src/memrelay_eval/cli/main.py`
- `evaluation/schemas/stage-bundle.schema.json`
- `evaluation/schemas/command-manifest.schema.json`
- `evaluation/docs/runbooks/stage-control.md`
- `evaluation/tests/unit/domain/test_stage_policy.py`
- `evaluation/tests/contract/test_stage_cli.py`
- `evaluation/tests/fault/test_stage_pause_resume.py`
- `_bmad-output/implementation-artifacts/6-1-seal-stage-bundles-and-enforce-the-stage-cli.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
