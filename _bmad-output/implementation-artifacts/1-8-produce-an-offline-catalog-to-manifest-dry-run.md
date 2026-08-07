---
baseline_commit: 205d087f84db73485bc2cd06d0f606d43cdc2155
---

# Story 1.8: Produce an Offline Catalog-to-Manifest Dry Run

Status: review

## Story

As an evaluator engineer,
I want non-interactive fake-provider planning and dry-run commands,
So that CI proves a usable study plan without paid calls or efficacy claims.

## Acceptance Criteria

1. **Given** the synthetic catalog and fake Copilot, OpenAI, and memrelay ports  
   **When** the offline dry run executes  
   **Then** it validates, freezes, assigns opaquely, and emits a deterministic planned-run manifest without network or provider credentials  
   **And** no Copilot or OpenAI request is made.
2. **Given** any `memrelay-eval` planning command  
   **When** it succeeds, fails, or is interrupted  
   **Then** it is non-interactive and emits input hashes, output hashes, runtime lock, protocol ID, and typed terminal status  
   **And** logs omit prompts, code, repository names, usernames, credentials, treatment labels, and provider payloads by default.
3. **Given** construction, fixtures, component tests, or the dry-run output  
   **When** a reportable status is generated  
   **Then** it is labeled implementation or conformance evidence only  
   **And** it cannot be represented as product efficacy, safety, economic value, or release fitness.

## Dependencies and Prerequisites

- **Formal graph dependencies:** Story 1.4, Story 1.6, and Story 1.7.
- Those stories must provide governed catalog/fixtures/eligibility, concealed assignment, and attempt policy.
- Story 1.5 effective configuration/freeze and Story 1.3 command manifests/canonical compiler are transitive requirements.
- Use only synthetic eligible fixtures and deterministic fake ports. This is not the future bootstrap that downloads a Copilot runtime or proves a second-volume restore.
- **Repository baseline:** all evaluator code/config/test seams are dependency-created or new in this story. Existing product `tests/eval/` and roundtrip tests remain bounded regressions and cannot substitute for this offline evaluator integration proof.

## Tasks / Subtasks

- [x] Compose an offline planning application path (AC: 1)
  - [x] Load and validate the synthetic catalog, verify fixtures/eligibility, compile, freeze fixture runtime/model/price inputs, and assign opaquely.
  - [x] Produce only a deterministic `planned` run manifest and referenced conformance artifacts.
  - [x] Distinguish planning assignment from lifecycle progression: the manifest may contain opaque precomputed assignment identities/commitments, but this dry run appends no real `assigned`, `provisioned`, `running`, or later transition.
  - [x] Inject fake Copilot, OpenAI, memrelay, artifact, ledger, and telemetry ports; install a network-deny guard.
- [x] Implement non-interactive planning/dry-run CLI commands (AC: 1, 2)
  - [x] Require explicit file arguments or safe local defaults; never prompt.
  - [x] Emit a command manifest for success, typed failure, keyboard interruption, and controlled cancellation.
  - [x] Make exit codes stable and machine-readable.
- [x] Enforce redaction and evidence labels (AC: 2, 3)
  - [x] Redact prohibited log fields and scan all emitted artifacts with canaries.
  - [x] Mark manifests `implementation_evidence` or `unpaid_conformance`; reject `study`, `included`, efficacy, safety, economics, or release-fitness labels.
  - [x] Ensure ordinary manifests expose opaque IDs only.
- [x] Add deterministic end-to-end and negative/fault tests (AC: 1-3)
  - [x] Repeat across clean processes/directories and compare canonical outputs.
  - [x] Fail if any socket/DNS/provider client is invoked or credential is present.
  - [x] Exercise invalid catalog, ineligible fixture, freeze failure, assignment failure, and interruption.

## Developer Context

This is Epic 1's integration proof: catalog-to-planned-manifest only. It deliberately stops before provisioning/running/exporting/scoring/reconciliation/inclusion. The broader first vertical slice through report requires later epics. A fake provider's terminal response is not an experiment and must never be used to imply memrelay benefit.

### Architecture Compliance

- CI and this dry run make zero Copilot/OpenAI calls (AD-22).
- Commands are non-interactive, fail-closed, deterministic, typed, redacted, and manifest-producing.
- Reuse one canonicalizer and immutable hashes throughout.
- Ordinary output remains treatment-neutral.
- Deterministic fake/unpaid conformance paths are explicitly separated from durable/paid eligibility: durable study runs require locked real runtime/model evidence, isolated workers, concrete sole-writer SQLite/Collector/CAS, backup/restore, complete reconciliation, and explicit paid-stage authorization.

### Library and Version Requirements

- Python 3.13 and the exact `evaluation/uv.lock` from prior stories.
- Use `pytest` for offline integration and subprocess CLI tests.
- Fake ports must not instantiate `github-copilot-sdk==1.0.8`, Inspect `0.3.252`, OpenAI clients, OTel exporters, product daemon, or direct engine.
- Reuse locked YAML/schema/RFC 8785 dependencies; no duplicate serializer/canonicalizer.

### Expected File Paths

- **NEW/UPDATE:** `evaluation/src/memrelay_eval/orchestration/control.py`
- **NEW:** `evaluation/src/memrelay_eval/orchestration/planning.py`
- **UPDATE:** `evaluation/src/memrelay_eval/adapters/fakes.py`
- **UPDATE:** `evaluation/src/memrelay_eval/cli/{main,commands}.py`
- **NEW:** `evaluation/catalog/fixtures/synthetic/`
- **NEW:** `evaluation/tests/integration/test_offline_catalog_to_manifest.py`
- **NEW:** `evaluation/tests/integration/test_planning_command_manifests.py`
- **NEW:** `evaluation/tests/fault/test_offline_planning_interruptions.py`
- **NEW:** `evaluation/tests/golden/offline-plan/`
- **UPDATE:** evaluator CI configuration only if an existing evaluator workflow was created by Story 1.1; do not modify product release claims in this story.

### Existing Behavior to Preserve

- All prior Epic 1 validation, canonicalization, fixture, eligibility, freeze, concealment, exposure, retry, immutability, and fake-port eligibility guarantees.
- Existing product tests/CLI/daemon/MCP and bounded fixture/release-roundtrip claims.
- Prior valid locks/manifests remain unchanged after any dry-run failure/interruption.
- Any fixture `runtime-lock`, model catalog, environment, or price table is explicitly synthetic/unpaid and cannot be confused with the observed durable locks produced by later bootstrap/model-lock workflows.

### Testing Requirements

- End-to-end offline test reaches exactly `planned`, never later lifecycle states.
- Assert assignment planning does not append an `assigned` transition and that fixture runtime/model/price artifacts carry synthetic/unpaid provenance.
- Byte-identical manifests on repeat with identical frozen inputs; changed input changes the expected hash.
- Network sandbox/monkeypatch fails on DNS, sockets, HTTP, SDK/OpenAI client construction, or subprocess provider launch.
- Empty environment and credential-canary environment both produce no secret/provider leakage.
- CLI tests cover success, validation failure, eligibility failure, typed domain failure, interruption, exit codes, and manifest completeness.
- Claim-lint tests reject efficacy/safety/economic/release labels and accept only implementation/unpaid-conformance labels.
- Confirm root product wheel metadata and tests are unaffected.

### Anti-Patterns

- Do not start the daemon, MCP, MemoryEngine, Inspect, Copilot SDK, Collector, or OpenAI.
- Do not advance lifecycle beyond `planned`.
- Do not fake durable persistence, reconciliation, inclusion, paid conformance, or efficacy.
- Do not bypass eligibility because fixtures are synthetic.
- Do not prompt, silently use ambient credentials, or inherit provider environment variables.
- Do not broaden scope into later vertical-slice reporting, stage execution, or release workflow fixes.

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` — “Story 1.8”]
- [Source: `ARCHITECTURE-SPINE.md` — AD-10, AD-17, AD-22, AD-23]
- [Source: `IMPLEMENTATION-DESIGN.md` — §§2, 22, 24.4]
- [Source: technical research — claim ladder and deterministic CI guidance]
- [Source: TEA handoff — “Quality gates”; “Recommended BMAD → TEA Workflow Sequence”]
- [Source: `test-design-qa.md` — “Not in Scope”; “Implementation Planning Handoff”; R-018/R-022/R-031]
- [Source: `SPEC.md` — §2 and §8, product boundaries to preserve]

## Dev Agent Record

### Agent Model Used

Claude Sonnet 5 (bmad-orchestrator attended session)

### Debug Log References

### Completion Notes List

- Ultimate context engine analysis completed - comprehensive developer guide created
- Story 1.8 implementation complete: offline planning pipeline, CLI command, tests
- 44 new tests covering integration, fault, architecture, and CLI compliance
- 771 evaluator tests pass (4 expected platform skips), 1305 product tests pass

### File List

