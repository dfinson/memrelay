# Story 6.6: Stop New Attempts with Evidence-Preserving Circuit Breakers

Status: ready-for-dev

## Story

As a study operator,
I want frozen resource and integrity circuit breakers,
so that overruns stop enrollment without erasing active-attempt evidence.

## Acceptance Criteria

1. **Given** per-run and stage token, tool, Copilot AI-credit, framework input/output/USD, active-time, elapsed-time, quota, throttle, model, infrastructure-failure, and evidence-loss limits  
   **When** any limit is reached  
   **Then** no new attempt starts and a typed circuit-breaker record is appended  
   **And** attempts already started are allowed to terminate or cancel under policy while preserving partial evidence.
2. **Given** active attempts during a stop  
   **When** terminal handling runs  
   **Then** each attempt remains in ITT with immutable terminal classification, costs, exposure state, and available evidence  
   **And** capped runs are never dropped or replaced.
3. **Given** quota, throttle, unavailable models, provider contention, or repeated infrastructure failure  
   **When** the stage is summarized  
   **Then** these are observable outcomes or strata with arm-balanced order/concurrency diagnostics  
   **And** no threshold or provider fallback is selected automatically.

## Tasks / Subtasks

- [ ] Define frozen limit and breaker domain contracts (AC: 1)
  - [ ] Model per-run/stage token, tool, AI-credit, framework input/output/USD, active/elapsed time, quota, throttle, model, infrastructure-failure, evidence-loss, authorization, and governance-revocation limits with units and source hashes.
  - [ ] Append idempotent typed `open|tripped|draining|closed` records; breaker reset/closure never deletes the triggering event or authorizes promotion.
- [ ] Enforce atomic start admission and stop propagation (AC: 1)
  - [ ] Atomically reserve consumption and recheck the immutable per-run, stage, model, and provider envelopes immediately before each start receipt; deny and trip when the requested start would exceed a cap so concurrent workers cannot oversubscribe.
  - [ ] On trip, reject all new starts, notify local control, and drain or cancel already-started attempts only under frozen policy.
- [ ] Preserve active and terminal evidence (AC: 1, 2)
  - [ ] Retain partial native/ledger/CAS/telemetry/cost/exposure/cleanup evidence and immutable terminal classifications in ITT.
  - [ ] Never replace capped, failed, canceled, ambiguous-exposure, or post-exposure attempts; retain the one authorized pre-exposure retry rule.
- [ ] Implement pause/resume and independent reauthorization (AC: 1-3)
  - [ ] Repeated trip delivery is idempotent; resume verifies repair evidence, unchanged locks, reconciliation/backup health, remaining limits, and a new role-attributed authorization scoped to the same stage and breaker reason.
  - [ ] Limit changes, model substitution, new provider, or threshold changes require a new protocol/stage; an old tripped stage cannot silently continue.
- [ ] Add local monitoring, alerts, and runbooks (AC: 1-3)
  - [ ] Expose remaining/consumed envelopes, breaker state/reason, active/draining counts, quota reset/throttle/model status, infrastructure-failure rate, evidence-loss rate, arm/order/concurrency balance, and backup/reconciliation lag.
  - [ ] Alerts stop new work for each integrity/resource signal. Runbooks preserve evidence, isolate credentials if needed, drain/cancel, reconcile/backup, classify scope, independently authorize repair/resume or reject/rerun, and record provider-time strata.
  - [ ] DG-R revocation immediately trips cross-repository work, preserves active evidence, and forbids fallback repositories or partial aggregate claims.
- [ ] Add concurrency, fault, and accounting tests (AC: 1-3)
  - [ ] Race starts against trips; test exact cap boundaries, duplicate signals, restart/resume, partial telemetry, model loss, quota reset, repeated infrastructure failure, evidence loss, and active cancellation.

## Dependencies

These are the exact formal story dependencies from the epic graph.

- Story 1.7: immutable terminal and retry policy.
- Story 4.3: telemetry semantics and fault evidence.
- Story 4.6: separate quantity/cost ledgers.
- Story 6.1: stage bundles, state, authorization, and CLI.

## Dev Notes

### Developer Context

Circuit breakers are admission controls, not rollback. They stop new attempts while active attempts reach a frozen terminal policy and all evidence remains. A breaker trip is an observable stage outcome; it cannot be hidden by rescheduling, resetting a counter, selecting a fallback, or removing capped runs. Cross-repository revocation is the same fail-closed stop mechanism but remains unreachable until DG-R qualification.

Current-checkout fact: no `evaluation/` tree exists and predecessor stories are ready-for-dev guides, not implemented learnings. Extend the established `orchestration/limits.py`, stage, attempt, ledger, telemetry, and cost seams only after they exist; do not modify product runtime code or reinterpret current product tests as breaker evidence.

### Architecture, Limits, and Preservation

- Reuse `orchestration/limits.py`, stage state, ledger port, telemetry, and cost records; do not add an external queue/control service.
- Freeze the stage-specific envelopes documented in Stories 6.3-6.5 and the research budget table. Copilot AI-credit and framework OpenAI token/USD ledgers remain distinct.
- Verify the Story 6.1 frozen runtime/version lock unchanged; a package, runtime, model, capability, price-table, or environment drift trips/pauses admission and never causes an automatic upgrade or fallback.
- Use a monotonic active-time clock; record provisioning, queue, backoff, and cleanup separately. Unsupported usage is `unavailable`, never zero.
- Preserve sole-writer append-only state, partial evidence, exact assignment denominator, arm-balanced scheduling diagnostics, and no automatic fallback.
- CI uses fake clocks/providers and no paid calls. Production control is local and non-interactive.

### File Targets

- **UPDATE/CREATE:** `evaluation/src/memrelay_eval/orchestration/limits.py`, `stages.py`, `control.py`, `attempt.py`
- **UPDATE:** `evaluation/src/memrelay_eval/domain/errors.py`, `policies.py`
- **UPDATE:** `evaluation/src/memrelay_eval/evidence/costs.py`, `reconcile.py`
- **UPDATE:** `evaluation/src/memrelay_eval/cli/commands.py`
- **NEW:** `evaluation/tests/unit/orchestration/test_limits.py`
- **NEW:** `evaluation/tests/contract/test_circuit_breaker_admission.py`
- **NEW:** `evaluation/tests/fault/test_breaker_concurrency_and_resume.py`
- **NEW:** `evaluation/docs/runbooks/circuit-breakers.md`

### Testing and Anti-Pattern Guardrails

- Use fake monotonic clocks and deterministic meters; property-test unit-safe arithmetic and exact boundary semantics.
- Prove start admission is race-safe through the sole control authority and every trip has evidence lineage.
- Preserve TEA stable coverage IDs and objective verdicts in generated traceability: `OPS-QUOTA-RATE-LIMIT`, `OPS-STAGE-ENVELOPE`, `MODEL-UNAVAILABLE-PAUSE`, `ITT-TIMEOUT`, `ITT-INFRA-PRE`, `ITT-INFRA-POST`, `TEL-FAILURE`, `TEL-DROP`, and `ARM-PARITY`.
- Do not kill by process name, erase active attempts, decrement consumption after failure, reset without evidence, treat unavailable as zero, transfer budgets between providers/models, retry post-exposure, auto-reschedule favorable order, or auto-promote after recovery.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story-6.6-Stop-New-Attempts-with-Evidence-Preserving-Circuit-Breakers]
- [Source: _bmad-output/planning-artifacts/architecture/architecture-dfinson-solid-happiness-2026-08-05/IMPLEMENTATION-DESIGN.md#20.3-Circuit-Breakers]
- [Source: _bmad-output/planning-artifacts/architecture/architecture-dfinson-solid-happiness-2026-08-05/ARCHITECTURE-SPINE.md#AD-22---CI-and-paid-stages-remain-separate-ADOPTED]
- [Source: _bmad-output/planning-artifacts/research/technical-evaluating-memrelay-collective-recall-for-coding-agent-reliability-cost-and-efficiency-research-2026-08-04.md#Deployment-and-Operations-Practices]
- [Source: _bmad-output/test-artifacts/test-design-qa.md#Frozen-ITT-attempts-retries-and-outcomes]

## Dev Agent Record

### Agent Model Used

To be recorded by the implementing dev agent.

### Debug Log References

### Completion Notes List

- Ultimate context engine analysis completed - comprehensive developer guide created

### File List
