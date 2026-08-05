---
stepsCompleted:
  - 1
  - 2
  - 3
  - 4
  - 5
  - 6
includedDocuments:
  requirements:
    - SPEC.md
    - _bmad-output/planning-artifacts/research/technical-evaluating-memrelay-collective-recall-for-coding-agent-reliability-cost-and-efficiency-research-2026-08-04.md
    - _bmad-output/test-artifacts/test-design-architecture.md
    - _bmad-output/test-artifacts/test-design-qa.md
    - _bmad-output/test-artifacts/test-design/dfinson-solid-happiness-handoff.md
  architecture:
    - _bmad-output/planning-artifacts/architecture/architecture-dfinson-solid-happiness-2026-08-05/ARCHITECTURE-SPINE.md
    - _bmad-output/planning-artifacts/architecture/architecture-dfinson-solid-happiness-2026-08-05/IMPLEMENTATION-DESIGN.md
  epics:
    - _bmad-output/planning-artifacts/epics.md
  ux: []
---

# Implementation Readiness Assessment Report

**Date:** 2026-08-05
**Project:** memrelay Evaluation Platform

## Document Discovery

### Requirements Sources

- `SPEC.md`
- Technical evaluation research report
- TEA test-design architecture
- TEA 161-scenario QA design
- Evaluation implementation handoff

No dedicated evaluation PRD exists. The finalized `epics.md` requirements inventory consolidates these sources into 66 FRs, 45 NFRs, and 48 architecture requirements.

### Architecture Sources

- `ARCHITECTURE-SPINE.md`, 24,414 bytes, final
- `IMPLEMENTATION-DESIGN.md`, 64,284 bytes, final
- Architecture memlog and three reviewer-gate reports are supporting records, not competing architecture versions.

### Epics and Stories

- `epics.md`, 98,469 bytes

### UX

No UX contract exists or is required. Evaluator v1 explicitly excludes an interactive UI and uses generated local reports.

### Discovery Issues

- No duplicate whole/sharded document formats found.
- No conflicting architecture or epic versions found.
- Formal PRD is absent, but the confirmed requirements source set and consolidated requirements inventory provide the assessment baseline.

## UX Alignment Assessment

### UX Document Status

No UX document exists.

### Alignment Issues

None. The requirements and architecture explicitly exclude an interactive UI from evaluator v1. All operator interaction is through non-interactive CLI commands, and generated local reports are the only presentation surface.

### Warnings

None for v1. Introducing an interactive UI, dashboard, or managed presentation service would exceed the frozen scope and require a new requirements and architecture review.

## Epic Quality Review

### Per-Epic Assessment

| Epic | User value | Independent of future epics | Story sizing | Dependencies | Acceptance criteria | Traceability | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1, Reproducible Plans | Pass | Pass | Pass, Story 1.1 is broad | Pass | Pass | Pass | Pass |
| 2, Isolated Trials | Pass | Pass through domain ports and fakes | Pass | Needs adapter-dependency clarification | Pass | Pass | Pass with correction |
| 3, Correctness and Quality | Pass | Pass through domain ports and fakes | Pass | Needs CAS dependency clarification | Pass | Pass | Pass with correction |
| 4, Evidence and Economics | Pass | Pass | Pass | Pass | Pass | Pass | Pass |
| 5, Analysis and Claims | Pass | Pass | Pass, Story 5.5 is broad | Pass | Pass | Pass | Pass |
| 6, Governed Stages | Pass | Pass | Pass, Story 6.5 combines primary and secondary stages | Pass | Pass | Pass | Pass |
| 7, Observation and Cross-Repository Scope | Pass | Pass | Pass | Pass | Pass | Pass | Pass |

### Dependency Analysis

- The 49 stories form an acyclic graph.
- All declared dependencies point backward to existing stories.
- No circular or forward dependency exists.
- All FR1-FR66, NFR1-NFR45, and AR1-AR48 appear in story requirements and acceptance criteria.
- Workspace, Collector, durability, and dynamic model assumptions each have an owning conformance story.

### Critical Violations

None.

### Major Issues

1. **CAS dependency declaration:** Stories 2.8, 2.10, 3.1, 3.2, and 3.3 produce or consume immutable artifact references before Story 4.1 defines the concrete CAS and artifact-manifest schema. The port-first architecture makes this implementable with fakes, but the graph must either move the CAS/schema prerequisite earlier or explicitly depend on Story 4.1.
2. **Ledger and telemetry adapter declaration:** Stories 1.6, 1.7, 2.4, 2.6, and 2.10 emit lifecycle or telemetry through domain ports before Stories 4.2 and 4.3 implement durable SQLite and Collector adapters. The graph must explicitly state fake-port behavior until those concrete adapters land.

### Minor Concerns

1. FR52 is broadly attributed to Epic 1, while its complete necessity, shortcut, contamination, canary, holdout, and grader-stability behavior is implemented by Story 5.5.
2. Story 1.1 is a broad but cohesive foundation story.
3. Story 6.5 combines primary and secondary stages and could be split for independent scheduling.

### Overall Quality Verdict

**PASS with required graph corrections before sprint planning.** No requirement, architecture, acceptance-criteria, or causal-design blocker exists.

### Correction Verification

The required graph corrections were applied and independently verified:

- Story 1.1 now owns `ArtifactRef`, `ArtifactManifest` schema `1.0.0`, domain ports, and deterministic fake adapters for unpaid conformance.
- Early artifact consumers explicitly block paid execution and inclusion until Story 4.1 provides and qualifies the durable CAS.
- Early lifecycle and telemetry producers explicitly use fake domain ports and block durable study execution until Stories 4.2 and 4.3 qualify SQLite and Collector adapters.
- Story 1.4 now covers the complete FR52 task-eligibility contract.
- The corrected 49-story graph remains acyclic with no missing or forward dependency.

## Summary and Recommendations

### Overall Readiness Status

**READY**

### Critical Issues Requiring Immediate Action

None.

### Recommended Next Steps

1. Run BMAD Sprint Planning to materialize the 49-story dependency graph into implementation tracking.
2. Begin with Story 1.1 and the independent Epic 1 branches identified by the graph; do not initiate paid stages before Stories 4.1-4.3 and the conformance gate pass.
3. Split Story 6.5 during sprint planning only if the primary and secondary stage work cannot fit one developer context; this is a scheduling refinement, not a design gap.

### Final Note

The assessment examined document authority, 159 requirements, 66-FR substantive story coverage, UX scope, seven value epics, 49 stories, architecture alignment, and dependency correctness. It found two major graph-clarity issues and three minor sizing/attribution concerns. The major issues and FR52 attribution concern were corrected and independently verified. The remaining sizing cautions do not block implementation.

**Assessment date:** 2026-08-05

**Assessor:** BMAD Implementation Readiness

## PRD-Equivalent Requirements Analysis

### Functional Requirements

FR1: Treat `evaluation/catalog/catalog.yaml` as the sole hand-authored execution source; never execute the TEA Markdown table directly.

FR2: Validate catalog YAML against pinned JSON Schema 2020-12 plus semantic and referential-closure rules before execution.

FR3: Reject scenarios without exactly one injected condition, one procedure, and one objective verdict.

FR4: Deterministically compile valid YAML into RFC 8785 canonical tasks, fixture manifests, opaque assignment inputs, traceability maps, and a catalog lock.

FR5: Calculate catalog and generated-artifact identities with one shared RFC 8785 canonicalizer and SHA-256.

FR6: Enforce major, minor, and patch catalog version changes for breaking, additive, and content-only changes.

FR7: Verify every fixture hash, path, revision, license, provenance, classification, redistribution policy, and extraction path.

FR8: Require every P0/P1 scenario to reference valid risks, gates, endpoints, evidence classes, and claims.

FR9: Freeze catalog, protocol, environment, model catalog, assignment algorithm, seed commitment, blocks, ordered inputs, and price tables before enrollment.

FR10: Create treatment-neutral IDs for experiments, assignments, runs, attempts, artifacts, evidence, endpoints, and claims.

FR11: Resolve treatment only inside provisioning and redact treatment-revealing data from blinded and ordinary views.

FR12: Append and validate the run lifecycle `planned`, `assigned`, `provisioned`, `running`, `exported`, `scored`, `reconciled`, then `included` or `excluded`.

FR13: Retain an immutable terminal classification and evidence record for every attempt.

FR14: Record assignment resolution, memory provisioning, task delivery, inference, treatment access, first exposure, and exposure evidence; ambiguity counts as exposure.

FR15: Permit at most one protocol-authorized retry, only for conclusively pre-exposure infrastructure failure.

FR16: Reject post-exposure retries, best-of-N selection, repeated-until-success execution, and favorable attempt substitution.

FR17: Provision every attempt with a fresh worktree or equivalent isolated clone, workspace, session root, cache, staging root, and telemetry identity.

FR18: Provision a unique `MEMRELAY_HOME`, graph, spool, socket or port, configuration, and worker process per attempt.

FR19: Run coding tasks through fresh local official GitHub Copilot SDK sessions authenticated by the current subscription.

FR20: Use Inspect as execution authority and orchestration control while making zero Inspect model-provider calls for task inference.

FR21: Preserve Inspect `.eval`, native JSON export, native Copilot events and terminal status, patch, usage, limits, cancellation, and typed failures.

FR22: Archive native `list_models()`, run frozen qualification, and automatically lock `M0`, optional `M1/M2`, and judge models.

FR23: Pause a stage when a locked model or capability changes; require a new protocol instead of substitution.

FR24: Verify model, reasoning, context, prompts, tools, permissions, network, limits, runtime, workspace, built-in memory, stores, and retries are arm-identical except treatment.

FR25: Evaluate the shipped product through a real isolated daemon and MCP path.

FR26: Collect daemon, MCP, graph, spool, socket, health, tool, observation-path, and cleanup evidence without opening the daemon-owned graph.

FR27: Evaluate the direct-engine upper bound through an isolated public `MemoryEngine` API and graph.

FR28: Maintain separate assignments, protocols, endpoints, costs, analyses, reports, and claims for product and engine strata.

FR29: Restore byte-identical immutable pre-treatment history to every controlled arm and verify parity.

FR30: Assign dynamic-history sequences before episode one, retain arm-local state and attrition, prohibit crossover, and analyze at sequence level.

FR31: Reject pooling of immutable-replay and dynamic-history outcomes.

FR32: Support protocol-defined `N0`, `E0`, `YL`, `MI`, `TR`, `OR`, `AI`, and `WO` arms with appropriate parity.

FR33: Run frozen benchmark-native tests and tamper/scope checks in a credential-free process over an immutable snapshot.

FR34: Give every primary-analysis candidate at least three independent, fresh, blinded Copilot SDK judge assessments.

FR35: Pin judge models, prompts, rubric, tools, controls, runtime, and presentation order; run calibration, agreement, drift, and bias checks.

FR36: Invoke a fresh blinded adjudicator only when frozen disagreement thresholds are crossed; preserve original judge records.

FR37: Keep executable correctness authoritative for hard pass/fail and panel quality authoritative for its separate co-primary endpoint.

FR38: Produce deterministic blinded evidence views that remove treatment leakage while preserving judgment evidence.

FR39: Capture control, provisioning, Copilot, MCP, daemon, memory, framework, grader, judge, artifact, export, cost, and reconciliation telemetry.

FR40: Emit distinct provider, credential-domain, service, resource, and cost identities for Copilot, framework OpenAI, and local resources.

FR41: Reconcile expected telemetry classes against native SDK, Inspect, memrelay, grading, artifact, cost, and ledger evidence.

FR42: Store raw and derived artifacts in a SHA-256 content-addressed store with versioned manifests and verified reads.

FR43: Require execution, SDK, telemetry, workspace, patch, treatment, grading, judging, cost, configuration, model, parity, cleanup, and transition evidence for terminal runs.

FR44: Include a run only after all evidence and hashes reconcile and no categorical blocker exists.

FR45: Maintain separately queryable Copilot subscription, framework OpenAI, and local-resource cost ledgers.

FR46: Record unsupported usage fields as `unavailable`, never zero, and prohibit incompatible token aggregation.

FR47: Reprice monetary results from retained quantities and append-only price or invoice revisions.

FR48: Materialize only reconciled terminal evidence into versioned Parquet and expose read-only DuckDB analysis.

FR49: Attach source and derivation hashes, protocol, population, endpoint, stratum, history mode, and gates to derived outputs.

FR50: Implement frozen assignment-aligned estimators, ITT, clustering, Holm control, simultaneous intervals, power simulation, and claim thresholds.

FR51: Measure safety denominators, ascertainment, injected-positive sensitivity, exact upper bounds, and bounded claim language.

FR52: Support memory-necessity review, shortcut audits, contamination checks, canaries, holdouts, grader stability, and task dispositions.

FR53: Support network-off analysis replay, grader reproduction, deterministic evidence replay, stochastic reruns, and independent replication.

FR54: Enforce bootstrap/conformance, 32-run integration, 128-unit pilot, 512-unit primary, up-to-192-unit secondary, and gated 24-cluster stages.

FR55: Refuse stage entry without the preceding immutable exit bundle and never auto-promote solely on process success.

FR56: Stop new attempts at quota, cost, time, throttle, model, infrastructure, or evidence-loss limits while preserving active evidence.

FR57: Atomically copy and hash-verify terminal-run evidence to an independent second local volume.

FR58: Rebuild ledger-to-artifact reachability and complete a verified restore before paid pilots.

FR59: Resolve, redact, canonicalize, hash, and retain effective configuration; reject in-place post-assignment changes.

FR60: Scan environments, workspaces, prompts, logs, tools, traces, manifests, and artifacts for secret canaries.

FR61: Initially accept only synthetic or license-audited public data; reject private, personal, proprietary, credentialed, or unauthorized inputs.

FR62: Keep cross-repository execution disabled until all identity, authorization, provenance, revocation, cache, migration, deletion, backup, and restore controls pass.

FR63: Test configured observation paths and require sentinel/reconciliation evidence before continuous-capture claims.

FR64: Prevent construction, component tests, deterministic fixtures, unreconciled trials, or engine results from being represented as product efficacy.

FR65: Generate local evidence-linked reports with intervals, diagnostics, Pareto surfaces, harm tails, gates, and bounded claims.

FR66: Emit a manifest containing input/output hashes, runtime lock, protocol ID, and typed terminal status for every CLI command.

**Total Functional Requirements: 66**

### Non-Functional Requirements

NFR1: Preserve partial evidence and typed terminal state across crashes, timeouts, provider failures, cancellation, and circuit breaking.

NFR2: Retry only conclusively pre-exposure infrastructure failure, once; ambiguous and post-exposure failures remain final.

NFR3: Block paid execution until workspace, Collector, reconciliation, backup, and restore conformance passes.

NFR4: Match assignment, experimental, resampling, and analysis units to the highest treatment-interference level.

NFR5: Never silently pool controlled replay, dynamic histories, model strata, product strata, or engine strata.

NFR6: Retain every assigned unit in ITT, including failures, attrition, zero-cost outcomes, and unavailable evidence.

NFR7: Use familywise alpha `0.05`, Holm-controlled, and target power `0.80`.

NFR8: Require reliability and qualitative benefit of at least `+0.05` with simultaneous 95% lower bound above zero.

NFR9: Require no-regression one-sided 97.5% lower bound above `-0.02`.

NFR10: Require cost or wall-time ratio at most `0.90`, upper bound below `1.0`, and reliability/quality non-inferiority above `-0.02`.

NFR11: Require panel kappa or ICC at least `0.70`, calibration MAE at most `0.10`, and blinded arm-classifier upper AUC at most `0.60`.

NFR12: Never expose OpenAI credentials to task-agent or judge processes or GitHub/Copilot credentials to framework processes.

NFR13: Give deterministic graders, analysis, Collector, MCP thin client, and evidence processes no provider credentials.

NFR14: Block a stage on confirmed credential leak, unauthorized disclosure, treatment contamination, hidden-test tamper, or high-severity poisoning.

NFR15: Use no private histories, personal data, proprietary repositories, or real credentials in initial trials.

NFR16: Omit prompts, code, repository names, usernames, credentials, treatment labels, and provider payloads from telemetry/logs by default.

NFR17: Disable cross-repository reads until complete authorization and revocation/deletion lifecycle controls exist.

NFR18: Measure active-agent wall time with a monotonic clock and report provisioning, queueing, backoff, and cleanup separately.

NFR19: Enforce per-run and stage token, tool, cost, active-time, and elapsed-time caps without dropping capped runs.

NFR20: Produce byte-identical catalog outputs for identical inputs.

NFR21: Reproduce analysis categories/counts exactly, numeric values within `1e-10` absolute or `1e-8` relative, and figure hashes exactly.

NFR22: Reproduce grader binary/test outcomes exactly and continuous scores within `1e-6`, excluding timing.

NFR23: Reproduce deterministic evidence hashes after canonical timestamp/path normalization.

NFR24: Make all evaluator operations non-interactive, typed, and fail-closed.

NFR25: Make no Copilot/OpenAI calls in CI; require explicit invocation or approved scheduling for paid stages.

NFR26: Pause or create a new stratum when model, runtime, configuration, environment, schema, grader, or protocol changes.

NFR27: Require worktree and clone providers to satisfy the same isolation contract.

NFR28: Keep evaluator dependencies out of the memrelay wheel.

NFR29: Keep lifecycle, assignment, attempt, manifest, and evidence records append-only.

NFR30: Hash-verify every artifact on write, read, and copy; prevent inclusion on corruption or authority conflict.

NFR31: Use only reconciled terminal Parquet for confirmatory analysis; never use DuckDB as operational state.

NFR32: Require 100% primary evidence completeness and at least 98% pilot overall completeness.

NFR33: Preserve separate provenance for Copilot, framework OpenAI, local variable resources, fully loaded operations, and study cost.

NFR34: Keep monetary results repriceable and distinguish estimated, subscription-normalized, and invoice-reconciled amounts.

NFR35: Restrict domain imports to the standard library and domain-owned types; terminate external SDK objects at adapters.

NFR36: Prohibit cross-adapter imports, duplicate canonicalizers, live provider calls from catalog, and assignment access from scoring.

NFR37: Version and hash-pin schemas, semantic maps, units, configurations, graders, rubrics, and derivations.

NFR38: Cover schema, state machines, concealment, isolation, blinding, telemetry faults, CAS corruption, reconciliation, repricing, Parquet, and no-network behavior in CI.

NFR39: Meet evidence RPO of at most the active in-flight attempt and RTO of 24 hours.

NFR40: Retain evidence until every linked claim is formally retired.

NFR41: Treat quota, throttling, unavailable models, and provider contention as observable outcomes/strata.

NFR42: Bound and arm-balance concurrency; never reuse workers across attempts.

NFR43: Balance host fingerprints and run order; make changed fingerprints separate strata.

NFR44: Report null, harmful, indeterminate, and positive conclusions; never weaken thresholds after outcomes.

NFR45: Let one categorical security, governance, grading, evidence-integrity, or causal-validity failure override aggregate performance.

**Total Non-Functional Requirements: 45**

### Additional Requirements

AR1: Implement under `evaluation/` with its own `pyproject.toml`, `uv.lock`, schemas, catalog, collector config, tests, artifacts, and `memrelay-eval` CLI.

AR2: Use Python 3.11.

AR3: Use `github-copilot-sdk==1.0.8` and wheel SHA-256 `7c3d868b73daa3a154ae6c1c5a2bd9301c47349c6b080f1ed77ba9027da3e0fa`.

AR4: Download the SDK-bundled Copilot runtime once, hash it, then set `COPILOT_SKIP_CLI_DOWNLOAD=1`.

AR5: Use Inspect AI `0.3.252` through official `@agent` or documented `agent_bridge()`.

AR6: Use OpenTelemetry SDK/exporters `1.44.0`.

AR7: Use `otelcol-contrib 0.158.0` Windows amd64 and archive SHA-256 `4314abde3c8acc67af58bb8d7611aa991fd80abe4a412695167f956d9fff3005`.

AR8: Use OpenInference `0.1.31`, OpenAI instrumentation `0.1.53`, and `memrelay.eval.genai-map/1.0.0`.

AR9: Use DuckDB `1.5.5` and PyArrow `25.0.0`.

AR10: Use direct OpenAI `byo-key` with `gpt-4.1-mini-2025-04-14` and daemon-only credentials.

AR11: Pin framework pricing per million tokens at input `$0.40`, cached input `$0.10`, output `$1.60`.

AR12: Use local `BAAI/bge-small-en-v1.5`; prohibit embedding API calls.

AR13: Preserve product bounds for traceforge, Graphiti, LadybugDB, and MCP Python.

AR14: Use a hexagonal modular monolith with domain-owned ports and append-only ledger.

AR15: Keep domain IDs, entities, lifecycle, policies, failures, and ports standard-library-only.

AR16: Keep catalog parsing, validation, canonicalization, hashing, fixtures, and traceability independent of Inspect/Copilot.

AR17: Make the Inspect control process the sole SQLite WAL writer; workers emit transition intents.

AR18: Keep prompts, patches, traces, grader bodies, and Inspect events out of SQLite.

AR19: Put planning, assignment, provisioning, concurrency, exposure, retries, cleanup, and stages in orchestration through ports.

AR20: Put blinding, grading, judge panels, calibration, reliability, adjudication, and normalization in scoring without assignment access.

AR21: Restrict analysis to reconciled Parquet and prohibit mutation of operational evidence.

AR22: Keep the daemon the sole writer of product graphs.

AR23: Use a distinct graph and public `MemoryEngine` methods for engine evaluation.

AR24: Launch one disposable host-native worker per attempt; record cleanup as compensating work.

AR25: Construct minimal process-specific environment allowlists.

AR26: Apply configuration precedence: explicit CLI, frozen protocol/stage, evaluator config, then safe defaults; use environment only for credentials.

AR27: Use immutable SHA-256 blobs plus experiment, run, attempt, and evidence manifests.

AR28: Keep run transitions and attempt terminal classifications as separate append-only records.

AR29: Require runtime, catalog, credential, isolation, grader, judge, telemetry, CAS, backup/restore, and reconciliation conformance before enrollment.

AR30: Require at least 30 infrastructure-complete attempts of 32, complete terminal evidence, and zero categorical blockers.

AR31: Require at least 98% pilot evidence completeness plus panel and blinding gates; exclude pilot outcomes from confirmation.

AR32: Require complete primary ITT, simultaneous intervals, harm tails, Pareto surface, and claim decisions; inadequate power is estimation-only.

AR33: Run 96 units per available `M1/M2`, maximum 192; never substitute unavailable roles.

AR34: Permit 24 cross-repository clusters only after every DG-R governance control passes.

AR35: Implement `memrelay-eval bootstrap --backup-root <second-volume-path>` and `runtime-lock.json`.

AR36: Implement `memrelay-eval lock-models` and `model-lock.json`.

AR37: Implement `memrelay-eval compile-catalog` and `catalog-lock.json`.

AR38: Implement `memrelay-eval conformance` and `conformance-report.json`.

AR39: Implement stage-gated `memrelay-eval run`.

AR40: Implement fail-closed `memrelay-eval reconcile`.

AR41: Implement Parquet-only `memrelay-eval analyze`.

AR42: Implement evidence-linked `memrelay-eval report`.

AR43: Make the first mergeable slice prove one synthetic catalog-to-report run with fake providers and no paid calls.

AR44: Preserve deterministic compiler, schema, fake-runtime, reconciliation, Parquet, and report artifacts in CI.

AR45: Exclude managed telemetry, cloud warehouses, third-party trackers, interactive UI, cloud graph backends, private histories, and enabled cross-repository execution from v1.

AR46: Use generated local reports as the only v1 presentation surface.

AR47: Block paid trials on failed workspace, Collector, or restore conformance; choose no fallback automatically.

AR48: Require a new protocol version for frozen-version, model-selection, threshold, endpoint, stage-rule, or post-assignment configuration changes.

**Total Additional Requirements: 48**

**Total Requirements: 159**

### Completeness Assessment

There is no standalone evaluation PRD. For implementation-readiness purposes, the confirmed authority chain is the SPEC, technical research, TEA architecture and QA test designs, and final architecture, consolidated without a competing version in the `epics.md` Requirements Inventory. That inventory is therefore the confirmed PRD-equivalent baseline.

The inventory is comprehensive, consistently numbered, and predominantly clear and testable: it specifies observable behaviors, fail-closed conditions, quantitative thresholds, immutable evidence, exact versions, and stage gates. No direct requirement contradiction was found. Three phrases rely on definitions supplied by the confirmed source chain rather than being fully self-defining in the inventory: FR32's arm codes and “appropriate parity,” FR15/NFR2's “conclusively pre-exposure infrastructure failure,” and AR34's “every DG-R governance control.” NFR8 can also be read as requiring both reliability and qualitative benefit, while the detailed planning context applies its threshold to reliability or qualitative-benefit claims. These are traceability/wording ambiguities to preserve during later validation, not blockers to using the inventory as the baseline.

## Epic Coverage Validation

Coverage below is validated against story acceptance criteria, not only the declared FR coverage map. Story IDs include every story that cites the FR and any additional story whose acceptance criteria substantively implement it.

### Coverage Matrix

| FR | Exact requirement | Mapped epic | Implementing stories | Status |
| --- | --- | --- | --- | --- |
| FR1 | Treat `evaluation/catalog/catalog.yaml` as the sole hand-authored execution source; never execute the TEA Markdown table directly. | Epic 1 — Create Reproducible Evaluation Plans | 1.2 | Covered |
| FR2 | Validate catalog YAML against pinned JSON Schema 2020-12 plus semantic and referential-closure rules before execution. | Epic 1 — Create Reproducible Evaluation Plans | 1.2 | Covered |
| FR3 | Reject scenarios without exactly one injected condition, one procedure, and one objective verdict. | Epic 1 — Create Reproducible Evaluation Plans | 1.2 | Covered |
| FR4 | Deterministically compile valid YAML into RFC 8785 canonical tasks, fixture manifests, opaque assignment inputs, traceability maps, and a catalog lock. | Epic 1 — Create Reproducible Evaluation Plans | 1.3 | Covered |
| FR5 | Calculate catalog and generated-artifact identities with one shared RFC 8785 canonicalizer and SHA-256. | Epic 1 — Create Reproducible Evaluation Plans | 1.3 | Covered |
| FR6 | Enforce major, minor, and patch catalog version changes for breaking, additive, and content-only changes. | Epic 1 — Create Reproducible Evaluation Plans | 1.2 | Covered |
| FR7 | Verify every fixture hash, path, revision, license, provenance, classification, redistribution policy, and extraction path. | Epic 1 — Create Reproducible Evaluation Plans | 1.4 | Covered |
| FR8 | Require every P0/P1 scenario to reference valid risks, gates, endpoints, evidence classes, and claims. | Epic 1 — Create Reproducible Evaluation Plans | 1.4 | Covered |
| FR9 | Freeze catalog, protocol, environment, model catalog, assignment algorithm, seed commitment, blocks, ordered inputs, and price tables before enrollment. | Epic 1 — Create Reproducible Evaluation Plans | 1.5 | Covered |
| FR10 | Create treatment-neutral IDs for experiments, assignments, runs, attempts, artifacts, evidence, endpoints, and claims. | Epic 1 — Create Reproducible Evaluation Plans | 1.1 | Covered |
| FR11 | Resolve treatment only inside provisioning and redact treatment-revealing data from blinded and ordinary views. | Epic 1 — Create Reproducible Evaluation Plans | 1.6, 3.2, 2.10 | Covered |
| FR12 | Append and validate the run lifecycle `planned`, `assigned`, `provisioned`, `running`, `exported`, `scored`, `reconciled`, then `included` or `excluded`. | Epic 1 — Create Reproducible Evaluation Plans | 1.1, 4.2 | Covered |
| FR13 | Retain an immutable terminal classification and evidence record for every attempt. | Epic 1 — Create Reproducible Evaluation Plans | 1.1, 1.7, 4.2 | Covered |
| FR14 | Record assignment resolution, memory provisioning, task delivery, inference, treatment access, first exposure, and exposure evidence; ambiguity counts as exposure. | Epic 1 — Create Reproducible Evaluation Plans | 1.6 | Covered |
| FR15 | Permit at most one protocol-authorized retry, only for conclusively pre-exposure infrastructure failure. | Epic 1 — Create Reproducible Evaluation Plans | 1.7 | Covered |
| FR16 | Reject post-exposure retries, best-of-N selection, repeated-until-success execution, and favorable attempt substitution. | Epic 1 — Create Reproducible Evaluation Plans | 1.7 | Covered |
| FR17 | Provision every attempt with a fresh worktree or equivalent isolated clone, workspace, session root, cache, staging root, and telemetry identity. | Epic 2 — Run Isolated Controlled Agent Trials | 2.2 | Covered |
| FR18 | Provision a unique `MEMRELAY_HOME`, graph, spool, socket or port, configuration, and worker process per attempt. | Epic 2 — Run Isolated Controlled Agent Trials | 2.2, 2.6 | Covered |
| FR19 | Run coding tasks through fresh local official GitHub Copilot SDK sessions authenticated by the current subscription. | Epic 2 — Run Isolated Controlled Agent Trials | 2.1, 2.4 | Covered |
| FR20 | Use Inspect as execution authority and orchestration control while making zero Inspect model-provider calls for task inference. | Epic 2 — Run Isolated Controlled Agent Trials | 2.4 | Covered |
| FR21 | Preserve Inspect `.eval`, native JSON export, native Copilot events and terminal status, patch, usage, limits, cancellation, and typed failures. | Epic 2 — Run Isolated Controlled Agent Trials | 2.4, 2.10 | Covered |
| FR22 | Archive native `list_models()`, run frozen qualification, and automatically lock `M0`, optional `M1/M2`, and judge models. | Epic 2 — Run Isolated Controlled Agent Trials | 2.1 | Covered |
| FR23 | Pause a stage when a locked model or capability changes; require a new protocol instead of substitution. | Epic 2 — Run Isolated Controlled Agent Trials | 2.1 | Covered |
| FR24 | Verify model, reasoning, context, prompts, tools, permissions, network, limits, runtime, workspace, built-in memory, stores, and retries are arm-identical except treatment. | Epic 2 — Run Isolated Controlled Agent Trials | 2.5 | Covered |
| FR25 | Evaluate the shipped product through a real isolated daemon and MCP path. | Epic 2 — Run Isolated Controlled Agent Trials | 2.6 | Covered |
| FR26 | Collect daemon, MCP, graph, spool, socket, health, tool, observation-path, and cleanup evidence without opening the daemon-owned graph. | Epic 2 — Run Isolated Controlled Agent Trials | 2.6 | Covered |
| FR27 | Evaluate the direct-engine upper bound through an isolated public `MemoryEngine` API and graph. | Epic 2 — Run Isolated Controlled Agent Trials | 2.7 | Covered |
| FR28 | Maintain separate assignments, protocols, endpoints, costs, analyses, reports, and claims for product and engine strata. | Epic 2 — Run Isolated Controlled Agent Trials | 2.7 | Covered |
| FR29 | Restore byte-identical immutable pre-treatment history to every controlled arm and verify parity. | Epic 2 — Run Isolated Controlled Agent Trials | 2.8 | Covered |
| FR30 | Assign dynamic-history sequences before episode one, retain arm-local state and attrition, prohibit crossover, and analyze at sequence level. | Epic 2 — Run Isolated Controlled Agent Trials | 2.9 | Covered |
| FR31 | Reject pooling of immutable-replay and dynamic-history outcomes. | Epic 2 — Run Isolated Controlled Agent Trials | 2.8, 2.9, 5.2 | Covered |
| FR32 | Support protocol-defined `N0`, `E0`, `YL`, `MI`, `TR`, `OR`, `AI`, and `WO` arms with appropriate parity. | Epic 2 — Run Isolated Controlled Agent Trials | 2.9 | Covered |
| FR33 | Run frozen benchmark-native tests and tamper/scope checks in a credential-free process over an immutable snapshot. | Epic 3 — Measure Correctness and Solution Quality | 3.1 | Covered |
| FR34 | Give every primary-analysis candidate at least three independent, fresh, blinded Copilot SDK judge assessments. | Epic 3 — Measure Correctness and Solution Quality | 3.3 | Covered |
| FR35 | Pin judge models, prompts, rubric, tools, controls, runtime, and presentation order; run calibration, agreement, drift, and bias checks. | Epic 3 — Measure Correctness and Solution Quality | 3.3, 3.4 | Covered |
| FR36 | Invoke a fresh blinded adjudicator only when frozen disagreement thresholds are crossed; preserve original judge records. | Epic 3 — Measure Correctness and Solution Quality | 3.5 | Covered |
| FR37 | Keep executable correctness authoritative for hard pass/fail and panel quality authoritative for its separate co-primary endpoint. | Epic 3 — Measure Correctness and Solution Quality | 3.6 | Covered |
| FR38 | Produce deterministic blinded evidence views that remove treatment leakage while preserving judgment evidence. | Epic 3 — Measure Correctness and Solution Quality | 3.2 | Covered |
| FR39 | Capture control, provisioning, Copilot, MCP, daemon, memory, framework, grader, judge, artifact, export, cost, and reconciliation telemetry. | Epic 4 — Produce Complete Auditable Evidence and Economics | 4.3 | Covered |
| FR40 | Emit distinct provider, credential-domain, service, resource, and cost identities for Copilot, framework OpenAI, and local resources. | Epic 4 — Produce Complete Auditable Evidence and Economics | 4.4, 4.6 | Covered |
| FR41 | Reconcile expected telemetry classes against native SDK, Inspect, memrelay, grading, artifact, cost, and ledger evidence. | Epic 4 — Produce Complete Auditable Evidence and Economics | 4.5 | Covered |
| FR42 | Store raw and derived artifacts in a SHA-256 content-addressed store with versioned manifests and verified reads. | Epic 4 — Produce Complete Auditable Evidence and Economics | 4.1 | Covered |
| FR43 | Require execution, SDK, telemetry, workspace, patch, treatment, grading, judging, cost, configuration, model, parity, cleanup, and transition evidence for terminal runs. | Epic 4 — Produce Complete Auditable Evidence and Economics | 4.2, 4.5 | Covered |
| FR44 | Include a run only after all evidence and hashes reconcile and no categorical blocker exists. | Epic 4 — Produce Complete Auditable Evidence and Economics | 4.5 | Covered |
| FR45 | Maintain separately queryable Copilot subscription, framework OpenAI, and local-resource cost ledgers. | Epic 4 — Produce Complete Auditable Evidence and Economics | 4.6 | Covered |
| FR46 | Record unsupported usage fields as `unavailable`, never zero, and prohibit incompatible token aggregation. | Epic 4 — Produce Complete Auditable Evidence and Economics | 4.6 | Covered |
| FR47 | Reprice monetary results from retained quantities and append-only price or invoice revisions. | Epic 4 — Produce Complete Auditable Evidence and Economics | 4.7 | Covered |
| FR48 | Materialize only reconciled terminal evidence into versioned Parquet and expose read-only DuckDB analysis. | Epic 5 — Analyze Effects and Bound Claims | 5.1, 5.2 | Covered |
| FR49 | Attach source and derivation hashes, protocol, population, endpoint, stratum, history mode, and gates to derived outputs. | Epic 5 — Analyze Effects and Bound Claims | 5.1, 5.2, 5.7 | Covered |
| FR50 | Implement frozen assignment-aligned estimators, ITT, clustering, Holm control, simultaneous intervals, power simulation, and claim thresholds. | Epic 5 — Analyze Effects and Bound Claims | 5.3, 5.4 | Covered |
| FR51 | Measure safety denominators, ascertainment, injected-positive sensitivity, exact upper bounds, and bounded claim language. | Epic 5 — Analyze Effects and Bound Claims | 5.5, 5.7 | Covered |
| FR52 | Support memory-necessity review, shortcut audits, contamination checks, canaries, holdouts, grader stability, and task dispositions. | Epic 1 — Create Reproducible Evaluation Plans | 1.4, 5.5 | Covered |
| FR53 | Support network-off analysis replay, grader reproduction, deterministic evidence replay, stochastic reruns, and independent replication. | Epic 5 — Analyze Effects and Bound Claims | 5.6 | Covered |
| FR54 | Enforce bootstrap/conformance, 32-run integration, 128-unit pilot, 512-unit primary, up-to-192-unit secondary, and gated 24-cluster stages. | Epic 6 — Operate Governed Experiment Stages | 6.1, 6.2, 6.3, 6.4, 6.5, 7.4 | Covered |
| FR55 | Refuse stage entry without the preceding immutable exit bundle and never auto-promote solely on process success. | Epic 6 — Operate Governed Experiment Stages | 6.1, 6.2, 6.3, 6.4, 6.5, 7.4 | Covered |
| FR56 | Stop new attempts at quota, cost, time, throttle, model, infrastructure, or evidence-loss limits while preserving active evidence. | Epic 6 — Operate Governed Experiment Stages | 6.6, 7.4 | Covered |
| FR57 | Atomically copy and hash-verify terminal-run evidence to an independent second local volume. | Epic 4 — Produce Complete Auditable Evidence and Economics | 4.8 | Covered |
| FR58 | Rebuild ledger-to-artifact reachability and complete a verified restore before paid pilots. | Epic 4 — Produce Complete Auditable Evidence and Economics | 4.8 | Covered |
| FR59 | Resolve, redact, canonicalize, hash, and retain effective configuration; reject in-place post-assignment changes. | Epic 1 — Create Reproducible Evaluation Plans | 1.5, 6.1 | Covered |
| FR60 | Scan environments, workspaces, prompts, logs, tools, traces, manifests, and artifacts for secret canaries. | Epic 2 — Run Isolated Controlled Agent Trials | 2.3, 2.10 | Covered |
| FR61 | Initially accept only synthetic or license-audited public data; reject private, personal, proprietary, credentialed, or unauthorized inputs. | Epic 1 — Create Reproducible Evaluation Plans | 1.4, 1.5 | Covered |
| FR62 | Keep cross-repository execution disabled until all identity, authorization, provenance, revocation, cache, migration, deletion, backup, and restore controls pass. | Epic 7 — Qualify Observation and Cross-Repository Scope | 7.3, 7.4 | Covered |
| FR63 | Test configured observation paths and require sentinel/reconciliation evidence before continuous-capture claims. | Epic 7 — Qualify Observation and Cross-Repository Scope | 7.1, 7.2 | Covered |
| FR64 | Prevent construction, component tests, deterministic fixtures, unreconciled trials, or engine results from being represented as product efficacy. | Epic 5 — Analyze Effects and Bound Claims | 1.8, 2.7, 5.7, 7.2 | Covered |
| FR65 | Generate local evidence-linked reports with intervals, diagnostics, Pareto surfaces, harm tails, gates, and bounded claims. | Epic 5 — Analyze Effects and Bound Claims | 5.7, 7.2 | Covered |
| FR66 | Emit a manifest containing input/output hashes, runtime lock, protocol ID, and typed terminal status for every CLI command. | Epic 1 — Create Reproducible Evaluation Plans | 1.3, 1.8, 6.1 | Covered |

### Missing Requirements

None. Every baseline functional requirement has substantive acceptance-criteria coverage.

No story cites an FR that is absent from the FR1–FR66 baseline.

### Coverage Statistics

- Total FRs: 66
- Covered FRs: 66
- Missing FRs: 0
- Coverage: 100%
