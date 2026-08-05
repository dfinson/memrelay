# Story 7.2: Bind Release Evidence to Qualified Observation Scope

Status: ready-for-dev

## Story

As a release owner,
I want observation and regression evidence mapped to narrowly bounded release statements,
so that product fixtures and sentinels cannot be promoted into efficacy claims.

## Acceptance Criteria

1. **Given** passed observation sentinel evidence and existing retrieval or release-roundtrip fixtures  
   **When** release evidence mapping runs  
   **Then** each artifact is labeled by exact product path, configuration, version, observation mode, evidence class, and supported claim  
   **And** fixtures remain bounded product regression evidence, not downstream efficacy evidence.
2. **Given** a proposed continuous-capture or release statement  
   **When** the supporting mode lacks passed sentinel and reconciliation evidence  
   **Then** the statement is rejected or explicitly marked unqualified  
   **And** favorable evidence from another path or mode cannot substitute.
3. **Given** a passed bounded release statement  
   **When** a local report is generated  
   **Then** it links sentinel, configuration, source, reconciliation, protocol, and gate hashes  
   **And** it does not imply safety, economics, generalization, or cross-repository fitness beyond tested evidence.

## Tasks / Subtasks

- [ ] Define immutable release-evidence and claim-scope contracts (AC: 1-3)
  - [ ] Record artifact/evidence ID, exact path/mode, source/config/runtime versions and hashes, evidence class, tested population, protocol, gate, supported statement, exclusions, and qualification status.
  - [ ] Keep `EV-FIXTURE-RETRIEVAL`/`CL-WIRING-RANK`, `EV-ROUNDTRIP-MCP`/`CL-PIPELINE-SEAM`, and Story 7.1 path qualification as separate authorities.
  - [ ] Model unsupported, expired, drifted, conflicting, and unqualified statements as typed fail-closed decisions.
- [ ] Implement exact evidence-to-statement mapping (AC: 1)
  - [ ] Require every claim to resolve all source and derivation hashes and a passed gate for the same path/mode/configuration/version.
  - [ ] Preserve observational estimands as path delivery/reconciliation findings; keep them separate from randomized treatment estimands and outcomes.
  - [ ] Prevent construction, fixtures, sentinels, engine-upper-bound evidence, pilot results, and observational characterization from becoming shipped-product confirmatory efficacy.
- [ ] Reject unsupported or substituted claims (AC: 2)
  - [ ] Reject cross-mode, cross-version, cross-configuration, cross-stratum, cross-history, cross-model, or cross-population evidence substitution.
  - [ ] Mark a statement unqualified only when the frozen reporting policy expressly permits that label; release-fitness decisions otherwise fail closed.
- [ ] Generate local evidence-linked reporting and enforce release wording (AC: 1-3)
  - [ ] Emit local reports only, with exact sentinel/config/source/reconciliation/protocol/gate links and explicit “supports/does not support” scope.
  - [ ] Correct the existing overbroad wording in `docs/release-gate.md` and `tests/integration/test_release_gate_roundtrip.py` to `EV-ROUNDTRIP-MCP` / `CL-PIPELINE-SEAM`, without weakening the test.
  - [ ] Add a checked-out, hermetic bounded-release-gate job to `.github/workflows/release.yml`; both TestPyPI and PyPI publication jobs must depend on its success. The current checkout-free `install-verify` job cannot run the source test and is not a substitute.
- [ ] Add claim-boundary and preservation tests (AC: 1-3)
  - [ ] Golden-test passed, null, harmful, indeterminate, expired, unqualified, and categorically blocked statement records.
  - [ ] Test mode substitution, stale hashes, missing sentinel evidence, fixture-only evidence, engine/product confusion, randomized/observational confusion, and cross-repository overreach.

## Dependencies and Prerequisites

- Story 5.7: evidence-linked local reports, bounded claim registry, and release-fitness claim rules.
- Story 7.1: immutable per-path observation qualification and implementation hash.
- **Authoritative direct graph dependencies:** Stories 5.7 and 7.1 only, exactly as declared in `epics.md`.
- Story 4.5 fail-closed reconciliation and Story 6.1 immutable command/stage manifests remain prerequisites through Story 5.7.
- Existing product fixtures are inputs, not predecessor implementation proof for the evaluator.

## Dev Notes

### Developer Context and Claim Boundaries

This story is a claim-governance layer, not a new efficacy experiment. The repository currently has:

- `tests/eval` deterministic retrieval/ranking fixtures: bounded wiring/ranking regression evidence only.
- `tests/integration/test_release_gate_roundtrip.py`: one synthetic fixture session through capture → spool → daemon/engine → MCP rendering, bounded to the pipeline seam.
- Story 7.1 sentinel qualification: observational evidence about the named configured capture path under a frozen fault contract.

The present `docs/release-gate.md` and roundtrip test prose overstate the single fixture as the “whole trust contract.” The TEA artifacts identify this wording and the lack of a release-workflow dependency as open blockers. Fixing those blockers is within this release-evidence story, but the corrected mechanism must not imply that a green regression test establishes efficacy, safety, economics, production completeness, or generalization.

### Architecture, Causal, Telemetry, and Governance Guardrails

- Claim identity always includes protocol, population, model, product/engine stratum, history regime, endpoint/estimand, observation mode where applicable, configuration/version, evidence, and gate IDs.
- Observational findings answer whether a configured path transported/reconciled sentinels under its test window. Randomized findings answer frozen assignment-aligned treatment estimands. Never combine or phrase one as the other.
- Product daemon/MCP and direct-engine strata remain separate. Engine results are labeled `engine upper bound`; they cannot support shipped-product efficacy.
- Telemetry is acceptable only after semantic/version qualification and reconciliation against native authorities. A telemetry span, fixture pass, or favorable count is not a claim.
- Missing, contradictory, expired, corrupt, or unauthorized evidence fails closed. One categorical security, governance, grading, evidence-integrity, or causal-validity failure overrides aggregate performance.
- Reports must preserve privacy defaults: no prompts, code, repository names, usernames, credentials, provider payloads, or treatment labels. Use opaque evidence identities and local generated reports.
- No private production data is assumed. Initial evidence uses synthetic or license-audited public inputs. Cross-repository fitness is always out of scope until Story 7.4 qualifies every DG-R control and the separate randomized 24-cluster stage completes.
- Preserve TEA stable identities and verdicts: `EV-FIXTURE-RETRIEVAL` supports only `CL-WIRING-RANK`; `EV-ROUNDTRIP-MCP` supports only `CL-PIPELINE-SEAM`; `RELEASE-CONTINUOUS` is path-specific observation qualification; `RELEASE-DOC-WORDING` and `RELEASE-CI-ENFORCEMENT` remain blocked until their repository diffs and tests pass.

### File Targets

- **UPDATE:** `evaluation/src/memrelay_eval/analysis/claims.py`; Story 5.7 already owns the claim domain and this story must not create a second claim authority under `domain/`.
- **NEW/UPDATE:** `evaluation/src/memrelay_eval/evidence/release_map.py`
- **UPDATE:** `evaluation/src/memrelay_eval/analysis/reports.py`
- **UPDATE:** `evaluation/src/memrelay_eval/cli/commands.py`
- **NEW:** `evaluation/schemas/release-evidence-map.schema.json`
- **NEW:** `evaluation/tests/unit/evidence/test_release_evidence_map.py`
- **NEW:** `evaluation/tests/contract/test_bounded_release_claims.py`
- **NEW:** `evaluation/tests/golden/test_release_report_claim_scope.py`
- **UPDATE:** `docs/release-gate.md`
- **UPDATE:** `.github/workflows/release.yml`
- **PRESERVE:** `tests/eval/**`.
- **UPDATE WORDING/METADATA ONLY:** `tests/integration/test_release_gate_roundtrip.py`; replace whole-trust/pre-release-trust claims with the bounded pipeline-seam identity, preserving execution and assertions unless an assertion is added to encode that same identity.

No `evaluation/` tree exists in the current checkout. Predecessor story documents are ready-for-dev guides, not implementation learnings. Create these targets only in dependency order and reuse the domain ports, CAS, reconciliation, analysis, and report contracts established by predecessor stories.

### Frozen Versions, Testing, and Preservation

- Hash the frozen runtime lock rather than introducing dependencies: Python `3.13`; Inspect `0.3.252`; Copilot SDK `1.0.8` plus wheel SHA-256 `7c3d868b73daa3a154ae6c1c5a2bd9301c47349c6b080f1ed77ba9027da3e0fa`; OTel `1.44.0`; Collector `0.158.0` plus archive SHA-256 `4314abde3c8acc67af58bb8d7611aa991fd80abe4a412695167f956d9fff3005`; OpenInference `0.1.31`; OpenAI instrumentation `0.1.53`; DuckDB `1.5.5`; PyArrow `25.0.0`; framework model `gpt-4.1-mini-2025-04-14`; local embedding model `BAAI/bge-small-en-v1.5`; product bounds remain Python `>=3.11,<3.14`, traceforge-toolkit `>=0.1,<0.1.2`, graphiti-core `>=0.29,<0.30`, Ladybug `>=0.18,<0.18.1`, and MCP `>=1.0,<2`.
- Unit tests validate exact scope matching and typed rejection. Contract tests prove claims cannot cross path/mode/version/stratum/history/population boundaries. Golden reports prove language for positive, null, harmful, indeterminate, estimation-only, unqualified, and blocked results.
- Release CI stays deterministic and provider-free. It may enforce the hermetic roundtrip and claim-map validation; it must not make Copilot/OpenAI calls or run paid trials.
- Preserve existing product behavior, daemon ownership, and fixture semantics. The story changes evidence classification and release enforcement, not the memory engine.

### Anti-Patterns

- Do not create a boolean “all trust passed” field, generic “production ready” claim, or evidence union that discards scope.
- Do not let evidence from replay qualify live-tail, another version qualify the current version, or engine/pilot/fixture evidence qualify product efficacy.
- Do not average categorical blockers, infer missing hashes, use mutable “latest” evidence, or publish an unsupported statement as a warning-only success.
- Do not add a dashboard, managed evidence service, cloud tracker, or private-data dependency.
- Do not describe observational sentinel results as randomized evidence or causal effects.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story-7.2-Bind-Release-Evidence-to-Qualified-Observation-Scope]
- [Source: _bmad-output/planning-artifacts/architecture/architecture-dfinson-solid-happiness-2026-08-05/ARCHITECTURE-SPINE.md#AD-03---Separate-product-and-engine-strata-ADOPTED]
- [Source: _bmad-output/planning-artifacts/architecture/architecture-dfinson-solid-happiness-2026-08-05/ARCHITECTURE-SPINE.md#AD-15---Fail-closed-reconciliation-ADOPTED]
- [Source: _bmad-output/planning-artifacts/architecture/architecture-dfinson-solid-happiness-2026-08-05/IMPLEMENTATION-DESIGN.md#2-What-Implementation-Establishes-and-What-Experiments-Establish]
- [Source: _bmad-output/planning-artifacts/architecture/architecture-dfinson-solid-happiness-2026-08-05/IMPLEMENTATION-DESIGN.md#24.2-Fixed-Statistical-and-Claim-Policy]
- [Source: _bmad-output/test-artifacts/test-design-architecture.md#Correct-scope-of-the-deterministic-fixture]
- [Source: _bmad-output/test-artifacts/test-design-qa.md#Dependencies--Test-Blockers]
- [Source: _bmad-output/planning-artifacts/research/technical-evaluating-memrelay-collective-recall-for-coding-agent-reliability-cost-and-efficiency-research-2026-08-04.md#Correct-scope-of-the-deterministic-fixture]
- [Source: docs/release-gate.md]
- [Source: tests/integration/test_release_gate_roundtrip.py]

## Dev Agent Record

### Agent Model Used

To be recorded by the implementing dev agent.

### Debug Log References

### Completion Notes List

- Ultimate context engine analysis completed - comprehensive developer guide created

### File List
