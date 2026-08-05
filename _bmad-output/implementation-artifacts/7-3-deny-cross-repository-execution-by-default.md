# Story 7.3: Deny Cross-Repository Execution by Default

Status: ready-for-dev

## Story

As a governance owner,
I want cross-repository planning and execution mechanically disabled,
so that no private or unauthorized repository is accessed before the complete governance gate passes.

## Acceptance Criteria

1. **Given** evaluator v1 installation, ordinary configuration, or a stage request  
   **When** a repository differs from the authorized task repository or `--stage cross-repo` is requested  
   **Then** execution is denied before repository discovery, clone, cache lookup, assignment, or task exposure  
   **And** no environment flag, safe default, or operator convenience option can bypass the gate.
2. **Given** a denied request  
   **When** evidence is recorded  
   **Then** only treatment-neutral request identity, authorization decision, policy version, and typed reason are retained  
   **And** no repository content, name, credential, or private metadata enters telemetry or artifacts.
3. **Given** CI and non-cross-repository stages  
   **When** architecture tests run  
   **Then** cross-repository adapters are unreachable unless a verified governance qualification artifact is supplied  
   **And** revocation immediately returns the system to deny-by-default.

## Tasks / Subtasks

- [ ] Define repository and authorization identities without conflation (AC: 1-3)
  - [ ] Model opaque task-repository identity, requested repository identity, authenticated principal, authorization/purpose/policy versions, decision, validity window, and revocation state.
  - [ ] Keep repository authorization identity separate from memory namespace/owner, git remote display name, cache key, treatment assignment, experiment/run identity, telemetry resource, and report label.
  - [ ] Treat owner/org grouping and namespace membership as retrieval context only, never authorization.
- [ ] Add an unavoidable pre-discovery deny guard (AC: 1)
  - [ ] Place the guard before repository resolution/discovery, remote inspection, clone/worktree creation, cache lookup, assignment, credential acquisition, task materialization, and exposure.
  - [ ] Deny `cross-repo` by default and deny any repository identity unequal to the already-authorized task repository.
  - [ ] Reject environment flags, force switches, fallback repositories, permissive defaults, stale “last approved” artifacts, and operator bypasses.
- [ ] Record privacy-minimized denial evidence (AC: 2)
  - [ ] Append only the AC2 payload: opaque treatment-neutral request identity, authorization decision, policy version/hash, and typed reason through domain ports. Any ledger-envelope time is infrastructure metadata and must not carry or derive repository identity.
  - [ ] Prove telemetry, manifests, logs, errors, and command output contain no repository name/path/content/hash derived from private content, credential, remote metadata, username, or treatment label.
- [ ] Enforce architecture reachability and revocation (AC: 3)
  - [ ] Keep cross-repository adapters absent from ordinary composition; require a verified, current Story 7.4 qualification artifact at the only enabling port.
  - [ ] Recheck authorization and governance bundle at entry and immediately before each start; revocation atomically closes admission and trips the Story 6.6 breaker.
  - [ ] Preserve already-started evidence under frozen policy without starting new discovery/work or selecting a replacement repository.
- [ ] Add deny, privacy, and structural tests (AC: 1-3)
  - [ ] Test all CLI/config/environment entry paths, same-owner/different-repo, aliases/remotes, forks, case/path normalization, stale cache, expired/revoked artifacts, race-to-start, and restart.
  - [ ] Use only synthetic repository identifiers and local fakes; assert forbidden discovery/clone/cache/credential methods were never called.
  - [ ] Add import/composition tests proving non-cross-repository stages cannot reach cross-repository adapters.

## Dependencies and Prerequisites

- **Authoritative direct graph dependency:** Story 1.1 only, exactly as declared in `epics.md`; it provides treatment-neutral domain IDs, typed failures, ports, and the separate evaluator project.
- Stories 1.5, 6.1, and 6.6 are later integration consumers/providers, not prerequisites for the deny policy. When their seams exist, configuration must not create authorization, the stage CLI must invoke this guard, and revocation must close admission through the shared breaker.
- Story 7.4 is not a prerequisite for deny-by-default; it is the only future qualification artifact allowed to unlock the guarded adapter path.

## Dev Notes

### Developer Context and Scope

This story implements prohibition, not cross-repository capability. Evaluator v1 must remain safe even when no governance service, private repository, production identity provider, or real authorization data exists. Tests use synthetic identities and spies that prove no discovery occurred.

Product `SPEC.md` currently groups memory by namespace and may infer namespace from GitHub owner. That is not repository authorization. A same-owner repository, configured namespace member, cached clone, remembered remote, or repository name observed in a task cannot authorize access. Repository authorization is a distinct domain identity and decision whose proof exists before any repository-touching operation.

### Architecture, Privacy, Telemetry, and Claim Guardrails

- Domain policy is standard-library-only and lives in `evaluation/`; orchestration depends on an authorization port; repository/workspace adapters cannot bypass it or import each other.
- Default-deny is mechanically earlier than discovery. Logging and telemetry must not need repository resolution to explain a denial.
- Only opaque request identity and policy decision metadata survive denial. Even hashing a predictable private repository name can disclose membership and is prohibited.
- No private histories, personal data, proprietary source, production credentials, or unauthorized repositories are assumed or accessed.
- Local evidence infrastructure and local agents remain the baseline. No cloud authorization service, managed telemetry, or remote cache is introduced by this story.
- A passed denial test is governance/conformance evidence only. It is not cross-repository readiness, safety proof, or efficacy evidence.
- Story 7.4 qualification permits only the exact frozen 24-cluster stage after all controls pass; this story must not pre-authorize it or expose a dormant force path.
- Preserve TEA stable identities in generated traceability: `SCOPE-R-DENY` is the default-deny verdict; any unauthorized disclosure also trips `BLOCK-SECURITY`, and missing/invalid governance authority trips `BLOCK-GOVERNANCE`. `SCOPE-R-AUTH` cannot pass in this story.

### File Targets

- **NEW after foundation stories:** `evaluation/src/memrelay_eval/domain/repositories.py`
- **NEW:** `evaluation/src/memrelay_eval/domain/governance.py`
- **UPDATE/CREATE:** `evaluation/src/memrelay_eval/orchestration/control.py`, `stages.py`
- **UPDATE when Story 2.2 adapters exist:** `evaluation/src/memrelay_eval/adapters/workspace/{base,worktree,clone}.py`; until then, test ordering through the Story 1.1 workspace port/fakes rather than creating duplicate workspace providers.
- **UPDATE/CREATE:** `evaluation/src/memrelay_eval/cli/commands.py`
- **NEW:** `evaluation/schemas/repository-authorization.schema.json`
- **NEW:** `evaluation/tests/unit/domain/test_cross_repository_deny.py`
- **NEW:** `evaluation/tests/contract/test_pre_discovery_authorization.py`
- **NEW:** `evaluation/tests/architecture/test_cross_repository_unreachable.py`
- **NEW:** `evaluation/tests/fault/test_governance_revocation.py`
- **READ ONLY:** product namespace/repository code under `src/memrelay/**`; evaluator authorization must not be added to or inferred from product namespace behavior.

No `evaluation/` tree exists yet. Use predecessor story contracts when implemented; do not invent parallel ledgers, artifact stores, stage state, configuration layers, or workspace providers.

### Frozen Contract and Testing Requirements

- `memrelay-eval run --stage cross-repo` is refused unless the exact verified Story 7.4 governance artifact is supplied through the frozen stage bundle. Ordinary stages reject repository changes independently.
- Configuration precedence does not create authorization: CLI, protocol, evaluator config, safe defaults, and credential-only environment handling remain frozen.
- Use canonical opaque identities; never persist repository labels in ordinary denial evidence. If the append-only ledger adds its normal UTC envelope time, it is outside the AC2 denial payload and must remain independent of repository data.
- Unit tests validate decisions and normalization without filesystem/network access. Contract tests assert guard ordering. Architecture tests assert adapter reachability. Fault tests race revocation against admission and verify no new repository operation begins.
- CI is non-interactive, local, fake-provider-only, and performs no Copilot/OpenAI or private repository call.
- This story is Python 3.11 standard-library domain/orchestration work and adds no dependency. Preserve product Python `>=3.11,<3.14` and the frozen product bounds (traceforge-toolkit `>=0.1,<0.1.2`, graphiti-core `>=0.29,<0.30`, Ladybug `>=0.18,<0.18.1`, MCP `>=1.0,<2`); later stage integration consumes the exact Story 6.1 runtime lock without changing it.

### Anti-Patterns

- Do not authorize by namespace, owner/org, git remote string, path equality alone, cache hit, clone success, task content, treatment arm, operator role text, or environment flag.
- Do not discover first and authorize later, including harmless-looking remote normalization or cache probing.
- Do not log/hash private repository names, credentials, URLs, paths, branch names, or content on denial.
- Do not ship a force/bypass/debug option, dormant cross-repository default, fallback repository, or stale-approval cache.
- Do not claim that deny-by-default proves the later 24-cluster stage safe or effective.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story-7.3-Deny-Cross-Repository-Execution-by-Default]
- [Source: _bmad-output/planning-artifacts/architecture/architecture-dfinson-solid-happiness-2026-08-05/ARCHITECTURE-SPINE.md#AD-23---Initial-data-eligibility-ADOPTED]
- [Source: _bmad-output/planning-artifacts/architecture/architecture-dfinson-solid-happiness-2026-08-05/IMPLEMENTATION-DESIGN.md#24.5-Explicit-v1-Exclusions]
- [Source: _bmad-output/planning-artifacts/architecture/architecture-dfinson-solid-happiness-2026-08-05/IMPLEMENTATION-DESIGN.md#24.3-Stage-Entry-Exit-and-Stop-Rules]
- [Source: _bmad-output/test-artifacts/test-design-qa.md#Dependencies--Test-Blockers]
- [Source: _bmad-output/test-artifacts/test-design-qa.md#P0---Categorical-and-critical-blockers]
- [Source: _bmad-output/test-artifacts/test-design/dfinson-solid-happiness-handoff.md#Phase-Transition-Quality-Gates]
- [Source: _bmad-output/planning-artifacts/research/technical-evaluating-memrelay-collective-recall-for-coding-agent-reliability-cost-and-efficiency-research-2026-08-04.md#Governance-and-Compliance]
- [Source: SPEC.md#5.1-Namespaces]

## Dev Agent Record

### Agent Model Used

To be recorded by the implementing dev agent.

### Debug Log References

### Completion Notes List

- Ultimate context engine analysis completed - comprehensive developer guide created

### File List
