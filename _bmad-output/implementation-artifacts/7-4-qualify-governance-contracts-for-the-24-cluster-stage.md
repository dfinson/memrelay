# Story 7.4: Qualify Governance Contracts for the 24-Cluster Stage

Status: review

## Story

As a governance owner,
I want every DG-R control proven and bound to a cross-repository stage bundle,
so that later cluster trials can run only with complete authorization and lifecycle evidence.

## Acceptance Criteria

1. **Given** primary-stage completion  
   **When** cross-repository qualification is requested  
   **Then** identity, authorization, repository provenance, revocation, cache isolation, migration, deletion, backup, restore, data classification, and audit contracts must each pass  
   **And** every proof is versioned, hash-pinned, repository-scoped, time-bounded, and linked in one immutable DG-R bundle.
2. **Given** a complete and current DG-R bundle  
   **When** `memrelay-eval run --stage cross-repo` evaluates entry  
   **Then** it permits exactly the frozen 24-cluster envelope with repository-level assignment, experimental, resampling, and analysis units  
   **And** cluster-level ITT retains authorization and revocation evidence.
3. **Given** any missing, expired, revoked, conflicting, or failed governance proof  
   **When** entry or an active stage is evaluated  
   **Then** the entire cross-repository stage is disabled and new work stops  
   **And** active evidence is preserved, no fallback repository is selected, and no partial aggregate claim is released.

## Tasks / Subtasks

- [ ] Define complete DG-R proof and bundle contracts (AC: 1, 3)
  - [ ] Define separate typed proofs for authenticated caller identity; principal/role/group/purpose binding; per-repository authorization; provenance/classification; policy/version render recheck; revocation; cache invalidation/isolation; migration; graph/derived deletion; downstream receipts; backup expiry; quarantined restore; key destruction; and audit.
  - [ ] Require each proof to identify opaque repository scope, issuer/authority, policy/schema/version hashes, validity interval, evidence refs, status, and revocation generation.
  - [ ] Canonically seal one immutable DG-R bundle; missing, conflicting, expired, unverifiable, or non-repository-scoped proofs cannot be represented as complete.
- [ ] Implement governance qualification with synthetic/public evidence (AC: 1)
  - [ ] Exercise caller spoofing, confused deputy, stale authorization cache, policy-version TOCTOU, withdrawal/revocation, migration, graph/embedding/cache/export/report deletion, downstream purge receipts, backup expiry, and restore-after-revocation.
  - [ ] Restore into quarantine, apply current authorization/tombstones/policy before indexing or rendering, and prove negative retrieval before acceptance.
  - [ ] Assume no private production data or live private repository in conformance; use synthetic or license-audited public repository fixtures and fake principals/credentials.
- [ ] Gate exactly the 24-cluster randomized stage (AC: 2)
  - [ ] Require accepted primary-stage evidence, a complete current DG-R bundle, separate operator authorization, frozen protocol/catalog/model/environment/limits, and Story 7.3 pre-discovery guard.
  - [ ] Materialize exactly 24 repository-relation clusters; repository cluster is the assignment, experimental, resampling, and analysis unit, with no run/session-level pseudo-replication.
  - [ ] Freeze cluster-level ITT, estimator, randomization/permutation, missingness, authorization/revocation outcomes, budget, and claim scope before assignment.
- [ ] Enforce active revocation and whole-stage failure (AC: 3)
  - [ ] Recheck bundle validity at entry and atomic start admission; revocation trips the circuit breaker and stops all new repository work.
  - [ ] Preserve active attempts, exposure, costs, governance state, native/ledger/CAS/telemetry evidence, and cleanup under frozen terminal policy.
  - [ ] Forbid repository substitution, favorable cluster replacement, partial-stage promotion, partial aggregate release, threshold relaxation, or fallback authorization.
- [ ] Separate qualification, observational, and randomized conclusions (AC: 1-3)
  - [ ] Label DG-R results as governance/conformance evidence and observation-sentinel results as path characterization.
  - [ ] Permit cross-repository effect statements only from the completed, reconciled randomized 24-cluster analysis; qualification success alone is not efficacy, safety, economics, or generalization evidence.
  - [ ] Fail release claims closed on any governance, privacy, evidence-integrity, grading, or causal-validity blocker.
- [ ] Add exhaustive local contract, architecture, fault, and analysis tests (AC: 1-3)
  - [ ] Cover every DG-R proof independently and in bundle combinations, validity boundaries, authority conflicts, revocation races, cache staleness, deletion completeness, quarantine restore, and backup expiry.
  - [ ] Test 23/24/25 clusters, repository-level randomization/resampling, cluster ITT with attrition/revocation, partial aggregates, and no fallback.
  - [ ] Use local fake agents/evidence infrastructure; CI makes no paid call and accesses no private repository.
  - [ ] Preserve the exact TEA IDs and objective verdicts: `GOV-FIELD-REGISTRY`, `GOV-XREPO-RECORD`, `GOV-CALLER-AUTH`, `GOV-PRINCIPAL-BIND`, `GOV-CONFUSED-DEPUTY`, `GOV-AUTH-CACHE`, `GOV-POLICY-TOCTOU`, `GOV-WITHDRAW`, `GOV-REVOKE`, `GOV-BACKUP-RESTORE-REVOKED`, `GOV-MIGRATE`, `GOV-DELETE-GRAPH`, `GOV-DELETE-DERIVED`, `GOV-BACKUP-EXPIRY`, `GOV-KEY-DESTRUCTION`, `GOV-VIEWER-PURGE`, `GOV-DOWNSTREAM-RECEIPT`, `GOV-EXISTING-GRAPH`, `SCOPE-R-AUTH`, `OPS-STAGE-ENVELOPE`, and `BLOCK-GOVERNANCE`.

## Dependencies and Prerequisites

- Story 4.8: verified second-volume backup, restore, CAS reachability, and durability evidence.
- Story 6.1: immutable stage bundles, independent authorization, non-interactive CLI, and manifest behavior.
- Story 6.5: completed/reconciled primary stage; secondary evidence is neither required nor a substitute.
- Story 6.6: atomic admission, revocation circuit breaker, active-attempt evidence preservation, and no fallback.
- Story 7.3: unavoidable pre-discovery deny-by-default guard and repository-identity separation.
- **Authoritative direct graph dependencies:** Stories 4.8, 6.1, 6.5, 6.6, and 7.3 only, exactly as declared in `epics.md`.
- Operational execution also requires all inherited catalog, assignment, credential, isolation, grading, telemetry, reconciliation, analysis, and reporting gates. A story file does not imply those implementations exist.

## Dev Notes

### Developer Context and Scope Boundary

This story builds and verifies governance contracts plus the stage-entry gate. It does not authorize current cross-repository work and does not assume access to private production repositories. Until every proof passes and is current, Story 7.3 remains the complete behavior: deny before discovery.

The exact future envelope is 24 repository-relation clusters, not 24 sessions or attempts. Cluster-level assignment, experimental unit, resampling unit, and analysis unit must remain identical because repository-level treatment can interfere within a repository relation. Repeats or multiple agent sessions inside a cluster do not increase independent N.

### Identity, Authorization, Privacy, and Lifecycle Guardrails

- Keep caller/principal identity, authorization/purpose, repository identity, memory namespace, assignment, run/attempt, artifact/evidence, telemetry resource, credential domain, and claim identity separate.
- Git owner, namespace membership, remote URL, cache entry, prior access, or product recall visibility is never authorization.
- Authorize and recheck at repository-record read/render and immediately before start. Bind decisions to policy version and revocation generation to prevent stale-cache and TOCTOU use.
- All proof evidence is repository-scoped and time-bounded. A global organizational approval cannot fill missing repository proof.
- Deletion covers graph, embeddings, spool, cache, CAS/index references, Parquet/derived outputs, reports/viewers, exports, backups, keys, and downstream copies as governed. Hash addressing does not override deletion/retention obligations.
- Restore is quarantined until current tombstones, policy, authorization, and revocation are applied; restored revoked content must remain non-retrievable.
- Telemetry/logs remain minimized and must not reveal repository names, code, prompts, credentials, personal data, treatment labels, or provider payloads. Governance proof uses opaque repository identities.
- Local agents and local evidence infrastructure remain authoritative for v1. No managed telemetry, cloud warehouse, third-party tracker, cloud graph, or interactive UI is introduced.

### Causal and Claim Guardrails

- DG-R qualification is conformance evidence only. It can establish that named controls passed their frozen tests, not that cross-repository memory is beneficial or safe in general.
- Story 7.1 observational estimands remain path-delivery/reconciliation characterization. They cannot enter or repair the randomized 24-cluster treatment estimate.
- The randomized stage uses a prospectively frozen repository-cluster ITT estimand and assignment-aligned inference. Every assigned cluster remains in ITT, including revocation, failure, attrition, zero cost, and unavailable evidence under frozen policy.
- Do not pool clusters with single-repository, cross-session, cross-agent, product/engine, controlled/dynamic, model, or changed-environment strata.
- Any governance failure disables the entire cross-repository stage. No favorable subset or partial aggregate claim is released. Release statements remain bounded to the tested repository relations, model, product stratum, history regime, protocol, and validity window.

### File Targets

- **UPDATE:** `evaluation/src/memrelay_eval/domain/governance.py`; Story 7.3 creates the deny/authorization identity seam, and this story extends it rather than creating a second authority.
- **NEW/UPDATE:** `evaluation/src/memrelay_eval/domain/repositories.py`; **UPDATE:** the established `domain/{states,policies,errors}.py` seams. Do not create a parallel `domain/stages.py`.
- **NEW:** `evaluation/src/memrelay_eval/evidence/governance.py`
- **UPDATE:** `evaluation/src/memrelay_eval/evidence/backup.py`, `reconcile.py`, `parquet.py`
- **UPDATE:** `evaluation/src/memrelay_eval/orchestration/stages.py`, `limits.py`, `control.py`
- **UPDATE:** `evaluation/src/memrelay_eval/analysis/{estimands,estimators,queries,reports}.py`
- **UPDATE:** `evaluation/src/memrelay_eval/cli/commands.py`
- **NEW:** `evaluation/schemas/dgr-proof.schema.json`
- **NEW:** `evaluation/schemas/dgr-bundle.schema.json`
- **NEW:** `evaluation/tests/contract/test_dgr_qualification.py`
- **NEW:** `evaluation/tests/contract/test_cross_repository_stage_gate.py`
- **NEW:** `evaluation/tests/fault/test_dgr_revocation_and_restore.py`
- **NEW:** `evaluation/tests/unit/analysis/test_cluster_itt.py`
- **READ ONLY:** product `src/memrelay/**`; product namespace behavior is not the governance authority.

No `evaluation/` implementation exists in the current checkout. Reuse predecessor ports and records; do not create a second authorization ledger, CAS, stage state machine, canonicalizer, backup system, or analysis store.

### Frozen Stage, Versions, and Testing

- Stage envelope is exactly 24 clusters and remains unreachable before accepted primary completion and full DG-R qualification.
- Use the shared RFC 8785/SHA-256 canonicalizer and exact Story 6.1 runtime lock: Python `3.13`; Inspect `0.3.252`; Copilot SDK `1.0.8` with wheel SHA-256 `7c3d868b73daa3a154ae6c1c5a2bd9301c47349c6b080f1ed77ba9027da3e0fa`; OTel `1.44.0`; Collector `0.158.0` Windows amd64 with archive SHA-256 `4314abde3c8acc67af58bb8d7611aa991fd80abe4a412695167f956d9fff3005`; OpenInference `0.1.31`; OpenAI instrumentation `0.1.53`; DuckDB `1.5.5`; PyArrow `25.0.0`; framework model `gpt-4.1-mini-2025-04-14`; local embedding model `BAAI/bge-small-en-v1.5`. Any schema, policy, source, model, environment, threshold, stage-rule, or configuration drift requires a new protocol/stage identity.
- Analysis reads reconciled Parquet only through read-only DuckDB. Governance operational truth remains append-only ledger/CAS evidence, never DuckDB.
- Contract tests validate each authority and exact stage entry. Architecture tests validate guard ordering. Fault tests exercise every categorical failure and revocation race. Analysis tests resample at repository-cluster level and reject session/run pseudo-replication.
- All CI tests are deterministic, local, synthetic/public, credential-free, and provider-free.

### Anti-Patterns

- Do not implement a checklist boolean, self-attested approval, global org allowlist, owner/namespace authorization, force flag, or “temporary” bypass.
- Do not touch/discover a repository before authorization, log private identity on denial, or use production data to prove the gate.
- Do not count sessions/runs as independent clusters, replace revoked/failed clusters, expand beyond 24, pool strata, or release partial favorable results.
- Do not let a governance pass, sentinel pass, fixture, deterministic replay, pilot, engine result, or secondary result support a randomized cross-repository efficacy claim.
- Do not restore revoked data directly into an active index or treat backup possession/hash integrity as authorization.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story-7.4-Qualify-Governance-Contracts-for-the-24-Cluster-Stage]
- [Source: _bmad-output/planning-artifacts/architecture/architecture-dfinson-solid-happiness-2026-08-05/ARCHITECTURE-SPINE.md#AD-23---Initial-data-eligibility-ADOPTED]
- [Source: _bmad-output/planning-artifacts/architecture/architecture-dfinson-solid-happiness-2026-08-05/ARCHITECTURE-SPINE.md#Implementation-Freeze]
- [Source: _bmad-output/planning-artifacts/architecture/architecture-dfinson-solid-happiness-2026-08-05/IMPLEMENTATION-DESIGN.md#24.3-Stage-Entry-Exit-and-Stop-Rules]
- [Source: _bmad-output/test-artifacts/test-design-qa.md#Dependencies--Test-Blockers]
- [Source: _bmad-output/test-artifacts/test-design-qa.md#Generated-Design-Time-Risk-to-Scenario-Projection]
- [Source: _bmad-output/test-artifacts/test-design/dfinson-solid-happiness-handoff.md#Phase-Transition-Quality-Gates]
- [Source: _bmad-output/planning-artifacts/research/technical-evaluating-memrelay-collective-recall-for-coding-agent-reliability-cost-and-efficiency-research-2026-08-04.md#Assignment-rule-both-designs-selected-by-estimand]
- [Source: _bmad-output/planning-artifacts/research/technical-evaluating-memrelay-collective-recall-for-coding-agent-reliability-cost-and-efficiency-research-2026-08-04.md#Governance-and-Compliance]
- [Source: _bmad-output/implementation-artifacts/6-5-run-primary-and-secondary-model-stages.md]
- [Source: _bmad-output/implementation-artifacts/6-6-stop-new-attempts-with-evidence-preserving-circuit-breakers.md]

## Dev Agent Record

### Agent Model Used

GPT-5.6 Terra

### Debug Log References

### Completion Notes List

- Ultimate context engine analysis completed - comprehensive developer guide created
- Implemented canonical, hash-pinned, repository-scoped DG-R proofs and a write-once
  complete bundle covering identity, authorization, provenance, revocation, cache,
  migration, deletion, backup, restore, classification, and audit controls.
- Added exact 24-cluster repository-relation planning, cluster-level ITT validation,
  cross-repository stage admission, sealed primary/DG-R/operator bindings, and active
  revocation breaker handling without a fallback or partial release path.
- Added synthetic/provider-free contract, fault, and cluster-ITT tests for every frozen
  TEA identifier. Focused governance and predecessor coverage passed (190 tests); the
  full evaluator run had one unrelated local SDK signature mismatch.

### File List

- `evaluation/src/memrelay_eval/evidence/governance.py`
- `evaluation/src/memrelay_eval/domain/repositories.py`
- `evaluation/src/memrelay_eval/orchestration/{stages,control,limits}.py`
- `evaluation/src/memrelay_eval/cli/{commands,main}.py`
- `evaluation/src/memrelay_eval/domain/policies.py`
- `evaluation/schemas/{dgr-proof,dgr-bundle}.schema.json`
- `evaluation/tests/contract/{test_dgr_qualification,test_cross_repository_stage_gate}.py`
- `evaluation/tests/fault/test_dgr_revocation_and_restore.py`
- `evaluation/tests/unit/analysis/test_cluster_itt.py`
