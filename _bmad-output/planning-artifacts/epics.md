---
stepsCompleted:
  - 1
  - 2
  - 3
  - 4
inputDocuments:
  - SPEC.md
  - _bmad-output/planning-artifacts/architecture/architecture-dfinson-solid-happiness-2026-08-05/ARCHITECTURE-SPINE.md
  - _bmad-output/planning-artifacts/architecture/architecture-dfinson-solid-happiness-2026-08-05/IMPLEMENTATION-DESIGN.md
  - _bmad-output/planning-artifacts/research/technical-evaluating-memrelay-collective-recall-for-coding-agent-reliability-cost-and-efficiency-research-2026-08-04.md
  - _bmad-output/test-artifacts/test-design-architecture.md
  - _bmad-output/test-artifacts/test-design-qa.md
  - _bmad-output/test-artifacts/test-design/dfinson-solid-happiness-handoff.md
---

# memrelay Evaluation Platform - Epic Breakdown

## Overview

This document provides the complete epic and story breakdown for the memrelay evaluation platform, decomposing the product specification, scientific evaluation design, test architecture, and finalized implementation architecture into implementable stories.

## Requirements Inventory

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

### NonFunctional Requirements

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

### Additional Requirements

AR1: Implement under `evaluation/` with its own `pyproject.toml`, `uv.lock`, schemas, catalog, collector config, tests, artifacts, and `memrelay-eval` CLI.

AR2: Use Python 3.13.

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

### UX Design Requirements

None. Evaluator v1 has no interactive UI; generated local reports are the only presentation surface.

### Input Coverage

| Input | Requirement coverage |
| --- | --- |
| `SPEC.md` | Product/API constraints, graph ownership, credential separation, observation behavior, data eligibility |
| `ARCHITECTURE-SPINE.md` | Architectural invariants, ownership, stack, stage and claim freezes |
| `IMPLEMENTATION-DESIGN.md` | Complete functional, non-functional, module, CLI, artifact, and acceptance contracts |
| Technical research report | Causal design, endpoints, costs, telemetry, statistics, claims, replication |
| `test-design-architecture.md` | Risks, gates, evidence, test levels, conformance |
| `test-design-qa.md` | Scenario coverage, failures, safety, reproducibility, governance |
| Evaluation handoff | Runtime boundaries, implementation sequencing, unresolved external prerequisites |

### Known External Prerequisites

- Native Copilot model IDs are materialized by the implemented catalog-lock workflow, not manually selected.
- Worktree/clone equivalence, Collector sufficiency, and second-volume restore are mandatory conformance proofs.
- Paid stages require current Copilot subscription access, framework OpenAI credentials, quota/cost authorization, eligible task assets, and role-separated review.
- Cross-repository execution remains prohibited until its governance gate exists and passes.

### FR Coverage Map

FR1: Epic 1 - Authoritative catalog source
FR2: Epic 1 - Schema and semantic validation
FR3: Epic 1 - Atomic scenarios
FR4: Epic 1 - Deterministic compilation
FR5: Epic 1 - Canonical identity
FR6: Epic 1 - Catalog evolution
FR7: Epic 1 - Fixture governance
FR8: Epic 1 - Traceability closure
FR9: Epic 1 - Experiment freeze
FR10: Epic 1 - Opaque identities
FR11: Epic 1 - Assignment concealment
FR12: Epic 1 - Run lifecycle
FR13: Epic 1 - Attempt terminal evidence
FR14: Epic 1 - Exposure tracking
FR15: Epic 1 - Restricted retry
FR16: Epic 1 - No favorable substitution
FR17: Epic 2 - Workspace isolation
FR18: Epic 2 - Treatment isolation
FR19: Epic 2 - Copilot task execution
FR20: Epic 2 - Inspect orchestration
FR21: Epic 2 - Native execution evidence
FR22: Epic 2 - Model discovery and locks
FR23: Epic 2 - Model drift handling
FR24: Epic 2 - Agent parity
FR25: Epic 2 - Product stratum
FR26: Epic 2 - Product evidence
FR27: Epic 2 - Engine stratum
FR28: Epic 2 - Stratum separation
FR29: Epic 2 - Controlled history
FR30: Epic 2 - Dynamic history
FR31: Epic 2 - History separation
FR32: Epic 2 - Protocol-defined arms
FR33: Epic 3 - Executable grading
FR34: Epic 3 - Agentic panel grading
FR35: Epic 3 - Judge controls
FR36: Epic 3 - Disagreement adjudication
FR37: Epic 3 - Outcome authority
FR38: Epic 3 - Blinded evidence views
FR39: Epic 4 - Telemetry capture
FR40: Epic 4 - Provider separation
FR41: Epic 4 - Telemetry reconciliation
FR42: Epic 4 - Immutable artifact storage
FR43: Epic 4 - Required evidence manifests
FR44: Epic 4 - Fail-closed inclusion
FR45: Epic 4 - Cost accounting
FR46: Epic 4 - Unknown usage
FR47: Epic 4 - Repricing
FR48: Epic 5 - Parquet and DuckDB analysis
FR49: Epic 5 - Analysis lineage
FR50: Epic 5 - Statistical enforcement
FR51: Epic 5 - Safety evidence
FR52: Epic 1 - Task eligibility
FR53: Epic 5 - Reproducibility
FR54: Epic 6 - Stage control
FR55: Epic 6 - Stage promotion
FR56: Epic 6 - Circuit breakers
FR57: Epic 4 - Evidence durability
FR58: Epic 4 - Restore
FR59: Epic 1 - Configuration evidence
FR60: Epic 2 - Credential scanning
FR61: Epic 1 - Data eligibility
FR62: Epic 7 - Cross-repository gate
FR63: Epic 7 - Observation sentinel
FR64: Epic 5 - Claim bounding
FR65: Epic 5 - Report generation
FR66: Epic 1 - Command manifests

## Epic List

### Epic 1: Create Reproducible Evaluation Plans

Evaluator engineers can author, validate, freeze, assign, and trace executable studies before any paid run.

**FRs covered:** FR1-FR16, FR52, FR59, FR61, FR66

### Epic 2: Run Isolated Controlled Agent Trials

Operators can execute treatment-neutral Copilot trials across product/engine strata and controlled/dynamic histories without contamination.

**FRs covered:** FR17-FR32, FR60

### Epic 3: Measure Correctness and Solution Quality

Researchers can obtain hard executable outcomes plus blinded, calibrated agentic-panel quality and disagreement adjudication.

**FRs covered:** FR33-FR38

### Epic 4: Produce Complete Auditable Evidence and Economics

Operators can reconcile telemetry, immutable artifacts, provider-separated costs, backup, restore, and inclusion evidence.

**FRs covered:** FR39-FR47, FR57-FR58

### Epic 5: Analyze Effects and Bound Claims

Researchers can run assignment-aligned analysis, safety measurement, replay, and evidence-linked claim reporting.

**FRs covered:** FR48-FR51, FR53, FR64-FR65

### Epic 6: Operate Governed Experiment Stages

Operators can enforce stage entry/exit bundles, budgets, circuit breakers, and promotion rules.

**FRs covered:** FR54-FR56

### Epic 7: Qualify Observation and Cross-Repository Scope

Release owners can validate current capture paths while keeping cross-repository execution mechanically disabled until governance passes.

**FRs covered:** FR62-FR63

## Epic 1: Create Reproducible Evaluation Plans

Evaluator engineers can author, validate, freeze, assign, and trace executable studies before any paid run.

### Story 1.1: Bootstrap the Evaluation Project and Domain Lifecycle

As an evaluator engineer,
I want a separate evaluation project with treatment-neutral domain identities and lifecycle rules,
So that I can plan studies without coupling evaluator dependencies or arm labels to the product.

**Acceptance Criteria:**

**Given** a Python 3.13 checkout of the repository
**When** I install and invoke the `evaluation/` project
**Then** `evaluation/pyproject.toml`, `uv.lock`, `src/memrelay_eval`, schemas, catalog, collector configuration, tests, and `memrelay-eval` CLI are available
**And** evaluator dependencies are absent from the memrelay wheel metadata.

**Given** experiment, protocol, scenario, task, history, assignment, run, attempt, artifact, evidence, endpoint, claim, cost-entry, and inclusion records
**When** domain IDs and records are created
**Then** their IDs are opaque and contain no arm or treatment label
**And** `domain` imports only the standard library and domain-owned types.

**Given** a run or attempt transition request
**When** it violates `planned -> assigned -> provisioned -> running -> exported -> scored -> reconciled -> included|excluded`
**Then** the domain rejects it with a typed error
**And** attempt terminal classifications remain separate immutable records using the frozen terminal vocabulary.

**Given** domain evidence, lifecycle records, or telemetry
**When** an early story stores an artifact or emits a state change or event
**Then** domain-owned `ArtifactRef` values address immutable SHA-256 content, `ArtifactManifest` validates against schema version `1.0.0`, `LedgerPort` appends authoritative lifecycle records without mutation, and `TelemetryPort` emits redacted structured observations without becoming lifecycle truth
**And** deterministic fake/in-memory `ArtifactStorePort`, `LedgerPort`, and `TelemetryPort` adapters are available to early stories.

**Given** those deterministic adapters
**When** they are used before durable adapters pass conformance
**Then** their outputs support unpaid conformance only
**And** they cannot make paid or study runs eligible for inclusion.

**Depends on:** None
**Requirements:** FR10, FR12, FR13; NFR1, NFR24, NFR28, NFR29, NFR35; AR1, AR2, AR14, AR15, AR28
**Produces:** Installable `evaluation/` project, domain records, state machines, typed failures, CLI composition root, domain-owned `ArtifactRef`, `ArtifactManifest` schema `1.0.0`, and domain-owned `LedgerPort`, `ArtifactStorePort`, `ExecutionAuthorityPort`, `AgentRuntimePort`, `TreatmentPort`, `WorkspacePort`, `AssignmentPort`, `GraderPort`, `TelemetryPort`, and `ReconciliationPort` protocols with deterministic fake/in-memory artifact, ledger, and telemetry adapters for unpaid conformance

### Story 1.2: Author and Validate the Executable Catalog

As an evaluator author,
I want one versioned YAML catalog validated structurally and semantically,
So that only atomic, closed, reviewable scenarios can become executable tasks.

**Acceptance Criteria:**

**Given** `evaluation/catalog/catalog.yaml`
**When** catalog validation runs
**Then** it is the sole hand-authored execution source and is validated with pinned JSON Schema 2020-12 plus semantic and referential-closure rules
**And** production code never parses the TEA Markdown scenario table as execution truth.

**Given** a scenario
**When** it has zero or multiple injected conditions, procedures, or objective verdicts, duplicate or treatment-revealing IDs, or unresolved references
**Then** validation fails with source-located typed diagnostics
**And** no generated execution artifact is written.

**Given** a catalog change relative to its lock
**When** the change is breaking, additive, or content-only
**Then** validation requires respectively a major, minor, or patch catalog-version increment
**And** an insufficient or unrelated version movement fails closed.

**Depends on:** Story 1.1
**Requirements:** FR1, FR2, FR3, FR6; NFR20, NFR24, NFR36-NFR38; AR16, AR37
**Produces:** Catalog schema `1.0.0`, YAML loader, semantic validator, version policy, and diagnostics

### Story 1.3: Compile Canonical Tasks and Catalog Locks

As an evaluator engineer,
I want valid catalog YAML compiled deterministically into canonical execution inputs,
So that every downstream plan is bound to byte-identical, hash-addressed inputs.

**Acceptance Criteria:**

**Given** the same valid catalog bytes and referenced inputs
**When** `memrelay-eval compile-catalog` runs repeatedly
**Then** one shared RFC 8785 canonicalizer emits byte-identical task inputs, opaque assignment inputs, fixture manifests, and traceability maps
**And** every identity uses SHA-256 over canonical JSON with the digest field omitted during digest calculation.

**Given** generated output or `catalog-lock.json`
**When** a byte is manually changed or an independent package attempts a second identity canonicalizer
**Then** CI detects and rejects the mismatch
**And** catalog code imports neither Inspect nor the Copilot SDK and makes no live provider call.

**Given** a successful or failed compile command
**When** it terminates
**Then** it writes a command manifest containing input and output hashes, runtime-lock reference, protocol ID, and typed terminal status
**And** failure leaves no artifact that can be mistaken for a valid lock.

**Depends on:** Story 1.2
**Requirements:** FR4, FR5, FR66; NFR20, NFR23-NFR25, NFR35-NFR38; AR16, AR37, AR44
**Produces:** Shared canonicalizer, compiler, canonical task inputs, opaque assignment inputs, and `catalog-lock.json`

### Story 1.4: Govern Fixtures, Traceability, and Task Eligibility

As a study reviewer,
I want every fixture and priority scenario linked to governed evidence and claims,
So that unauthorized data and untraceable tasks cannot enter a study.

**Acceptance Criteria:**

**Given** a fixture reference
**When** it is compiled
**Then** its opaque ID, relative path, SHA-256, media type, revision, license, provenance, extraction path, classification, and redistribution policy are verified
**And** a missing, escaping, changed, or unauthorized fixture fails compilation.

**Given** a P0 or P1 scenario
**When** traceability is validated
**Then** valid risk, gate, endpoint, expected-evidence, and claim IDs resolve with no orphan
**And** the generated traceability map retains source locations and hashes.

**Given** an initial-study task or history
**When** eligibility is evaluated
**Then** only synthetic or license-audited public data are accepted
**And** private histories, personal data, proprietary repositories, real credentials, and unauthorized inputs are rejected with a task disposition.

**Given** any candidate task or history governed by FR52
**When** study eligibility is reviewed
**Then** a memory-necessity review and shortcut audit show that the intended memory capability, rather than identifiers, formatting artifacts, repository clues, or other shortcuts, is necessary for success
**And** contamination and canary checks, development/pilot/confirmatory holdout separation, and baseline and gold-grader stability checks must pass before an immutable eligible or rejected task disposition is recorded with its evidence and reasons.

**Depends on:** Story 1.3
**Requirements:** FR7, FR8, FR52, FR61; NFR15, NFR37, NFR40; AR16, AR23, AR45
**Produces:** Verified fixture manifests, closed traceability maps, eligibility policy, and task dispositions

### Story 1.5: Freeze Effective Configuration and Enrollment Inputs

As a study operator,
I want all experimental inputs and effective configuration frozen before enrollment,
So that post-assignment changes cannot silently alter the protocol.

**Acceptance Criteria:**

**Given** CLI arguments, frozen protocol/stage values, evaluator configuration, and safe defaults
**When** effective configuration is resolved
**Then** precedence is exactly CLI, protocol/stage, evaluator file, then safe defaults
**And** environment variables are accepted only as credentials for their named target process.

**Given** a pre-enrollment plan
**When** it is sealed
**Then** catalog, protocol, environment fingerprint, native model catalog, assignment algorithm, seed commitment, blocks, ordered inputs, and price tables are canonicalized and hash-frozen
**And** credential values are replaced by structured redaction markers while variable names and target processes are retained.

**Given** an assignment has been created
**When** any frozen version, endpoint, threshold, stage rule, model selection, or configuration value changes
**Then** in-place mutation is rejected
**And** a new protocol or attempt with a new immutable configuration artifact is required.

**Depends on:** Story 1.3
**Requirements:** FR9, FR59, FR61; NFR15-NFR17, NFR26, NFR37, NFR43; AR26, AR48
**Produces:** Redacted effective-configuration artifact, environment fingerprint, enrollment freeze, and parity hash inputs

### Story 1.6: Conceal Assignment and Record Exposure

As an experiment controller,
I want opaque assignment resolved only inside provisioning with explicit exposure evidence,
So that operators, graders, and ordinary artifacts cannot infer treatment.

**Acceptance Criteria:**

**Given** a frozen enrollment plan
**When** assignment runs
**Then** it seals the algorithm version, seed commitment, blocks, ordered input hashes, and assignment-plan hash
**And** ordinary manifests contain only opaque experiment, run, and assignment IDs.

**Given** an attempt specification outside the provisioning boundary
**When** it is inspected, logged, exported, or passed to scoring
**Then** it contains no human-readable arm label or assignment resolution
**And** only the provisioning authority can resolve the treatment.

**Given** provisioning and execution events
**When** exposure is classified
**Then** assignment resolution, memory provisioning, task delivery, inference, treatment access, first monotonic exposure time, and supporting evidence are recorded
**And** missing or ambiguous exposure evidence is classified as exposed.

**Given** assignment lifecycle or telemetry before Stories 4.2 and 4.3
**When** it is emitted
**Then** it flows through the Story 1.1 domain-owned `LedgerPort` and `TelemetryPort` using deterministic fakes for unpaid conformance
**And** durable study execution and inclusion remain blocked until the concrete sole-writer SQLite ledger and Collector adapters pass conformance.

**Depends on:** Story 1.4, Story 1.5
**Requirements:** FR11, FR14; NFR2, NFR16, NFR29, NFR36; AR17, AR19, AR20
**Produces:** Concealed assignment service, provisioning-only resolver, exposure record, and redacted ordinary view

### Story 1.7: Enforce Attempt Terminal and Retry Policy

As an experiment controller,
I want immutable attempt outcomes and a single narrowly authorized retry path,
So that failures remain in ITT and favorable substitution is impossible.

**Acceptance Criteria:**

**Given** an attempt reaches any terminal condition
**When** its terminal record is appended
**Then** the classification and partial evidence are immutable and linked to the original run
**And** crashes, timeouts, provider failures, quota exhaustion, grading failure, cancellation, and evidence incompleteness remain observable outcomes.

**Given** a conclusively pre-exposure infrastructure failure and a protocol that authorizes retry
**When** retry is requested
**Then** exactly one new attempt ID with fresh isolation is linked to the same assignment
**And** the original attempt and its evidence remain unchanged.

**Given** ambiguous or post-exposure failure, an existing retry, or a request for best-of-N, repeated-until-success, or favorable substitution
**When** retry or inclusion is requested
**Then** it is rejected with a typed reason
**And** Inspect, SDK, memrelay, and grader internal retries are separately bounded and recorded.

**Given** attempt lifecycle or telemetry before Stories 4.2 and 4.3
**When** it is emitted
**Then** it flows through the Story 1.1 domain-owned `LedgerPort` and `TelemetryPort` using deterministic fakes for unpaid conformance
**And** durable study execution and inclusion remain blocked until the concrete sole-writer SQLite ledger and Collector adapters pass conformance.

**Depends on:** Story 1.1
**Requirements:** FR13, FR15, FR16; NFR1, NFR2, NFR6, NFR29; AR28, AR48
**Produces:** Attempt terminal policy, retry authorizer, lineage records, and favorable-substitution guards

### Story 1.8: Produce an Offline Catalog-to-Manifest Dry Run

As an evaluator engineer,
I want non-interactive fake-provider planning and dry-run commands,
So that CI proves a usable study plan without paid calls or efficacy claims.

**Acceptance Criteria:**

**Given** the synthetic catalog and fake Copilot, OpenAI, and memrelay ports
**When** the offline dry run executes
**Then** it validates, freezes, assigns opaquely, and emits a deterministic planned-run manifest without network or provider credentials
**And** no Copilot or OpenAI request is made.

**Given** any `memrelay-eval` planning command
**When** it succeeds, fails, or is interrupted
**Then** it is non-interactive and emits input hashes, output hashes, runtime lock, protocol ID, and typed terminal status
**And** logs omit prompts, code, repository names, usernames, credentials, treatment labels, and provider payloads by default.

**Given** construction, fixtures, component tests, or the dry-run output
**When** a reportable status is generated
**Then** it is labeled implementation or conformance evidence only
**And** it cannot be represented as product efficacy, safety, economic value, or release fitness.

**Depends on:** Story 1.4, Story 1.6, Story 1.7
**Requirements:** FR64, FR66; NFR16, NFR24, NFR25, NFR38; AR35, AR37, AR43-AR46
**Produces:** Offline dry-run workflow, fake ports, per-command manifests, and bounded evidence labels

## Epic 2: Run Isolated Controlled Agent Trials

Operators can execute treatment-neutral Copilot trials across product/engine strata and controlled/dynamic histories without contamination.

### Story 2.1: Lock the Copilot Runtime and Qualified Models

As a trial operator,
I want the official Copilot SDK runtime and eligible models discovered and hash-locked,
So that trials pause rather than silently substitute a changed execution substrate.

**Acceptance Criteria:**

**Given** `memrelay-eval bootstrap --backup-root <second-volume-path>`
**When** Copilot bootstrap runs
**Then** it accepts only `github-copilot-sdk==1.0.8` wheel `github_copilot_sdk-1.0.8-py3-none-any.whl` with SHA-256 `7c3d868b73daa3a154ae6c1c5a2bd9301c47349c6b080f1ed77ba9027da3e0fa`
**And** `python -m copilot download-runtime` runs once, records runtime version, binary SHA-256, transport, auth mode, and non-secret subscription identity in `runtime-lock.json`, then requires `COPILOT_SKIP_CLI_DOWNLOAD=1`.

**Given** the complete native `CopilotClient.list_models()` response
**When** `memrelay-eval lock-models` runs the frozen eight-task nonstudy qualification
**Then** it filters required capabilities; ranks by executable passes, protected-check fraction, active wall time, then native ID; selects `M0`, optional distinct-family `M1`, and qualifying low-credit `M2`; and maximizes judge-model diversity
**And** it freezes IDs, capabilities, reasoning, context, qualification evidence, and runtime in `model-lock.json`.

**Given** a missing or changed runtime, locked model, or required capability
**When** a stage starts
**Then** the stage pauses with a typed conformance failure
**And** no download, model substitution, public-name inference, or manual preference occurs.

**Depends on:** Story 1.1
**Requirements:** FR19, FR22, FR23; NFR25, NFR26, NFR37, NFR41; AR3-AR5, AR35, AR36
**Produces:** Verified SDK/runtime installation, native model archive, qualification evidence, `runtime-lock.json`, and `model-lock.json`

### Story 2.2: Provision Isolated Workspace Providers

As a trial operator,
I want one fresh workspace with unique roots and state per attempt,
So that concurrent trials cannot contaminate one another.

**Acceptance Criteria:**

**Given** an attempt starts through worktree or isolated-clone provisioning
**When** its workspace is created
**Then** workspace, agent session, cache, staging, telemetry identity, `MEMRELAY_HOME`, graph, spool, socket or port, and configuration roots are unique
**And** both providers satisfy the same isolation contract with no shared writable state.

**Given** attempt completion, failure, timeout, or cancellation
**When** workspace cleanup runs as compensating work
**Then** cache, graph ownership, workspace, and provider cleanup evidence is recorded
**And** attempt-local roots and state cannot be reused by another attempt.

**Depends on:** Story 1.1
**Requirements:** FR17, FR18; NFR27, NFR42; AR29
**Produces:** Worktree and isolated-clone adapters, unique attempt roots and state, workspace cleanup records, and equivalence isolation conformance tests

### Story 2.3: Launch Disposable Credential-Isolated Processes

As a trial operator,
I want disposable processes launched from minimal credential allowlists,
So that process boundaries cannot leak provider credentials or state across attempts.

**Acceptance Criteria:**

**Given** Inspect control, Copilot worker, memrelay daemon, MCP client, grader, judge, Collector, and analysis processes
**When** each is launched from a minimal allowlist
**Then** only Copilot worker/judges receive host Copilot auth, only the daemon receives its OpenAI key, and every other process receives no provider credential
**And** OpenAI variables never enter agent/MCP processes and GitHub/Copilot credentials never enter framework processes.

**Given** disposable process environments containing synthetic credential canaries
**When** process-boundary conformance runs
**Then** each process can observe only canaries authorized for its credential domain
**And** any canary crossing a prohibited process boundary fails conformance with preserved non-secret evidence.

**Given** process completion, failure, timeout, or cancellation
**When** process cleanup runs as compensating work
**Then** worker, socket, and process cleanup evidence is recorded
**And** the disposable worker is never reused across attempts.

**Depends on:** Story 2.2
**Requirements:** FR60; NFR12-NFR18; AR24, AR25, AR29
**Produces:** Disposable process launcher, minimal credential allowlists, process-boundary canaries, process cleanup records, and credential-isolation conformance tests

### Story 2.4: Execute Copilot Through Inspect Authority

As a trial operator,
I want Inspect to orchestrate a direct official Copilot SDK custom agent,
So that execution limits and truth remain native without an alternate inference route.

**Acceptance Criteria:**

**Given** Inspect AI `0.3.252`
**When** a task executes
**Then** the adapter uses the official `@agent` surface or only the pinned release's documented `agent_bridge()`
**And** the custom agent calls the official Copilot SDK directly with zero Inspect model-provider calls and no OpenAI-compatible Copilot endpoint.

**Given** a fresh task-agent session
**When** Inspect schedules, cancels, times out, or terminates it
**Then** the exact task metadata, limits, model controls, native terminal state, event references, patch references, usage, cancellation, and typed failure return to Inspect
**And** partial evidence survives every terminal path.

**Given** Inspect `.eval`, native JSON export, and the SDK terminal record
**When** their terminal statuses are compared
**Then** Inspect is execution authority and the SDK record is mandatory corroboration
**And** disagreement blocks reconciliation rather than selecting a favorable source.

**Given** execution lifecycle or telemetry before Stories 4.2 and 4.3
**When** it is emitted
**Then** it flows through the Story 1.1 domain-owned `LedgerPort` and `TelemetryPort` using deterministic fakes for unpaid conformance
**And** durable study execution and inclusion remain blocked until the concrete sole-writer SQLite ledger and Collector adapters pass conformance.

**Depends on:** Story 1.3, Story 1.6, Story 1.7, Story 2.1, Story 2.3
**Requirements:** FR19, FR20, FR21; NFR1, NFR12, NFR18, NFR35; AR5, AR19
**Produces:** Inspect custom-agent adapter, direct SDK session adapter, cancellation handling, `.eval`, JSON, event, patch, usage, and terminal references

### Story 2.5: Verify Agent and Environment Parity

As an experiment controller,
I want arm-neutral parity hashes before task exposure,
So that treatment content or access is the only permitted difference.

**Acceptance Criteria:**

**Given** a provisioned attempt
**When** parity is computed
**Then** it hashes SDK/runtime versions, exact model ID, reasoning, context, prompt bytes, tool schemas, permissions, network, limits, timeout, workspace layout, built-in memory, cross-session store, retry policy, effective configuration, and host fingerprint
**And** only treatment content or access may differ.

**Given** paired arms in a block
**When** their parity records disagree before exposure
**Then** execution fails as pre-exposure infrastructure failure with evidence
**And** no task is delivered or inference started.

**Given** OS/build, CPU, memory, storage class, power mode, runtime, limits, network, or background-load policy changes
**When** the environment fingerprint is checked
**Then** the changed fingerprint creates a separate environment stratum
**And** it is never silently pooled with the prior fingerprint.

**Depends on:** Story 1.5, Story 2.2, Story 2.4
**Requirements:** FR24; NFR4, NFR5, NFR26, NFR43; AR29, AR48
**Produces:** Agent-parity record, environment fingerprint, blocking comparison, and environment-stratum rule

### Story 2.6: Run the Shipped Daemon and MCP Product Stratum

As a product evaluator,
I want trials to use the shipped daemon and MCP path in isolated state,
So that measured outcomes include the actual product lifecycle and transport overhead.

**Acceptance Criteria:**

**Given** a product-stratum attempt
**When** treatment is provisioned
**Then** an isolated `MEMRELAY_HOME` and pinned configuration start a real daemon, verify `health`, and expose only shipped `memory_recall`, `memory_detail`, and `memory_note` through isolated MCP
**And** evaluator code never opens the daemon-owned graph directly.

**Given** framework inference is configured
**When** preflight runs
**Then** it requires direct OpenAI `byo-key`, exact model `gpt-4.1-mini-2025-04-14`, pinned base URL/client, daemon-only key, and rejects `borrow-host`, LiteLLM, local, or any unexpected fallback
**And** local embeddings use digest-pinned `BAAI/bge-small-en-v1.5` with no embedding API calls.

**Given** product execution terminates
**When** state is collected
**Then** daemon, MCP, graph, spool, socket/loopback, health, tool success/error/zero-result, observation-path, process, and cleanup evidence is preserved
**And** controls maintain equivalent tool visibility, schemas, permissions, budgets, and accounting.

**Given** product lifecycle or telemetry before Stories 4.2 and 4.3
**When** it is emitted
**Then** it flows through the Story 1.1 domain-owned `LedgerPort` and `TelemetryPort` using deterministic fakes for unpaid conformance
**And** durable study execution and inclusion remain blocked until the concrete sole-writer SQLite ledger and Collector adapters pass conformance.

**Depends on:** Story 2.4, Story 2.5
**Requirements:** FR25, FR26; NFR12, NFR13, NFR27, NFR35; AR10-AR13, AR22
**Produces:** Product treatment adapter, MCP controls, framework preflight, isolated product evidence, and cleanup contract

### Story 2.7: Run the Direct-Engine Upper-Bound Stratum

As a product researcher,
I want a separately governed direct-engine treatment,
So that I can estimate an upper bound without presenting it as shipped-product efficacy.

**Acceptance Criteria:**

**Given** an engine-stratum attempt
**When** it is provisioned
**Then** `await MemoryEngine.from_config(...)` receives its own configuration and graph and only public async `note`, `search`, `detail`, `health`, and `close` methods are used
**And** it never opens a live or daemon-owned product graph.

**Given** the engine adapter receives external results
**When** they cross the adapter boundary
**Then** plain dictionaries are converted into domain-owned records
**And** engine construction, health, note, search, detail, close, graph, and rendering-contract evidence is retained.

**Given** product and engine results
**When** protocol, assignment, endpoint, cost, analysis, report, or claim identity is created
**Then** each stratum has a separate identity and aggregation without an explicit stratified operation fails
**And** every engine claim is labeled `engine upper bound`, never product efficacy.

**Depends on:** Story 2.4, Story 2.5
**Requirements:** FR27, FR28; NFR5, NFR26, NFR35, NFR36; AR23, AR45
**Produces:** Direct-engine adapter, separate graph and records, stratum identities, and upper-bound claim guard

### Story 2.8: Restore Controlled Immutable Histories

As an experiment controller,
I want byte-identical pre-treatment histories restored to every controlled arm,
So that controlled-effect comparisons start from the same immutable evidence.

**Acceptance Criteria:**

**Given** a controlled history
**When** its bundle is built before assignment exposure
**Then** immutable CAS references preserve ordered episodes, actors, scopes, revisions, provenance, validity windows, expected graph inputs, protocol ID, and content SHA-256
**And** no treatment-generated content enters the source bundle.

**Given** the same controlled bundle is restored to different arms
**When** provisioning completes
**Then** restore manifests and parity hashes prove byte-identical inputs
**And** any mismatch blocks exposure.

**Given** probe-time writes or a query combining history modes
**When** protocol and analysis rules are enforced
**Then** writes are disabled, discarded, or separately recorded exactly as frozen and only controlled-effect estimands are allowed
**And** controlled and dynamic outcomes cannot be pooled.

**Given** controlled-history artifacts before Story 4.1
**When** they are written or resolved
**Then** Story 2.8 consumes the Story 1.1 `ArtifactStorePort` and `ArtifactManifest` schema `1.0.0`, using deterministic fake artifacts solely for unpaid conformance until Story 4.1 provides the durable filesystem CAS
**And** paid execution and study inclusion remain blocked until Story 4.1 passes conformance.

**Depends on:** Story 1.4, Story 1.6, Story 2.2, Story 2.5
**Requirements:** FR29, FR31; NFR4, NFR5, NFR30, NFR37; AR19
**Produces:** Controlled-history bundle, restore provider, parity manifest, and history-mode separation rule

### Story 2.9: Execute Dynamic Histories and Protocol-Defined Arms

As an experiment controller,
I want whole dynamic sequences assigned before episode one across approved arms,
So that evolving state is measured without crossover or unit-of-analysis errors.

**Acceptance Criteria:**

**Given** a dynamic-history protocol
**When** a sequence is enrolled
**Then** the entire sequence is assigned before episode one into fresh arm-local state
**And** the sequence/history is the experimental, resampling, and analysis unit.

**Given** episodes update memory or an attempt fails or attrits
**When** later episodes execute
**Then** state and lineage remain arm-local, failures and attrition remain assigned outcomes, and crossover or treatment-history reuse in controls is prohibited
**And** only total-policy sequence estimands are permitted.

**Given** protocol-defined `N0`, `E0`, `YL`, `MI`, `TR`, `OR`, `AI`, and `WO` arms
**When** a selected arm is provisioned
**Then** its frozen tool, permission, budget, accounting, treatment-access, and control-parity contract is enforced
**And** unsupported arms fail before exposure rather than being substituted.

**Depends on:** Story 1.6, Story 1.7, Story 2.4
**Requirements:** FR30, FR31, FR32; NFR4-NFR6, NFR26, NFR42; AR19
**Produces:** Dynamic-sequence scheduler, arm contracts, lineage and attrition records, and total-policy estimand metadata

### Story 2.10: Preserve Native Evidence and Scan Secret Boundaries

As an evidence operator,
I want every attempt export and surface scanned against native evidence and credential canaries,
So that contaminated or incomplete trials cannot proceed to grading.

**Acceptance Criteria:**

**Given** a running or terminal attempt
**When** native evidence is exported
**Then** Inspect `.eval` and JSON, SDK events and terminal status, patch, usage, limits, cancellation, typed failures, monotonic active-agent time, and separate provisioning/queue/backoff/cleanup times are retained by artifact reference
**And** capped and failed runs are not dropped.

**Given** environments, workspaces, prompts, logs, tools, traces, manifests, configuration, or artifacts
**When** secret-canary scanning runs
**Then** OpenAI, GitHub, Copilot, and synthetic canaries are checked against process-specific prohibitions
**And** a confirmed credential leak blocks the stage and preserves evidence without echoing the secret.

**Given** raw telemetry or logs
**When** they are emitted
**Then** prompts, code, repository names, usernames, credentials, treatment labels, and provider payloads are omitted by default
**And** treatment labels never appear in agent-visible resources, baggage, prompts, or logs.

**Given** native evidence artifacts before Story 4.1
**When** they are written or resolved
**Then** Story 2.10 consumes the Story 1.1 `ArtifactStorePort` and `ArtifactManifest` schema `1.0.0`, using deterministic fake artifacts solely for unpaid conformance until Story 4.1 provides the durable filesystem CAS
**And** paid execution and study inclusion remain blocked until Story 4.1 passes conformance.

**Given** evidence lifecycle or telemetry before Stories 4.2 and 4.3
**When** it is emitted
**Then** it flows through the Story 1.1 domain-owned `LedgerPort` and `TelemetryPort` using deterministic fakes for unpaid conformance
**And** durable study execution and inclusion remain blocked until the concrete sole-writer SQLite ledger and Collector adapters pass conformance.

**Depends on:** Story 2.3, Story 2.4
**Requirements:** FR21, FR60; NFR1, NFR14, NFR16, NFR18, NFR19, NFR30; AR24, AR29
**Produces:** Native execution-evidence export, time accounting, secret scanner, redaction tests, and categorical leak blocker

## Epic 3: Measure Correctness and Solution Quality

Researchers can obtain hard executable outcomes plus blinded, calibrated agentic-panel quality and disagreement adjudication.

### Story 3.1: Freeze Snapshots and Run Deterministic Graders

As a benchmark maintainer,
I want credential-free deterministic grading over immutable terminal snapshots,
So that executable correctness is reproducible and cannot be influenced by treatment or live services.

**Acceptance Criteria:**

**Given** a terminal attempt from Story 2.10
**When** the workspace is frozen
**Then** baseline revision, terminal revision, patch, files, and canonical timestamp/path-normalized snapshot are immutable SHA-256 artifacts
**And** later workspace mutation cannot change grader inputs.

**Given** a frozen grader contract
**When** the grader process starts
**Then** benchmark-native and hidden tests, dependencies, tamper checks, scope checks, network policy, grader version, and hashes are pinned
**And** the process receives opaque IDs but no assignment, provider credentials, or unrestricted network.

**Given** repeated grading of identical snapshot and contract hashes
**When** results are compared
**Then** binary and test outcomes match exactly and continuous scores match within `1e-6`, excluding timing
**And** executable results, objective components, terminal status, and artifact references are preserved.

**Given** snapshot or grader artifacts before Story 4.1
**When** they are written or resolved
**Then** Story 3.1 consumes the Story 1.1 `ArtifactStorePort` and `ArtifactManifest` schema `1.0.0`, using deterministic fake artifacts solely for unpaid conformance until Story 4.1 provides the durable filesystem CAS
**And** paid execution and study inclusion remain blocked until Story 4.1 passes conformance.

**Depends on:** Story 2.2, Story 2.10
**Requirements:** FR33; NFR13, NFR22, NFR30, NFR37, NFR38; AR20, AR29
**Produces:** Immutable workspace snapshots, grader contracts, credential-free grader adapter, and deterministic result artifacts

### Story 3.2: Generate Deterministic Blinded Evidence Views

As a scoring operator,
I want versioned blinded views separated from immutable source evidence,
So that judges receive sufficient evidence without learning treatment.

**Acceptance Criteria:**

**Given** immutable source evidence
**When** a blinded view is generated
**Then** arm names, treatment codes, revealing memrelay paths, unnecessary provider fields, revealing tool/timing fields, and assignment records are removed or deterministically transformed
**And** judgment-relevant code, patch, requirements, tests allowed by policy, and artifact locations remain available.

**Given** identical source and blinding-policy hashes
**When** the view is regenerated
**Then** its bytes and SHA-256 are identical
**And** the access-separated unblinded source remains unchanged and linked by governed provenance.

**Given** blinded candidates and sentinel transformations
**When** leakage tests and a treatment-arm classifier run
**Then** any direct leakage fails scoring conformance
**And** confirmatory blinding requires the classifier's 95% upper AUC bound at most `0.60`.

**Given** blinded-view artifacts before Story 4.1
**When** they are written or resolved
**Then** Story 3.2 consumes the Story 1.1 `ArtifactStorePort` and `ArtifactManifest` schema `1.0.0`, using deterministic fake artifacts solely for unpaid conformance until Story 4.1 provides the durable filesystem CAS
**And** paid execution and study inclusion remain blocked until Story 4.1 passes conformance.

**Depends on:** Story 2.10, Story 3.1
**Requirements:** FR38; NFR11, NFR16, NFR23, NFR36-NFR38; AR20
**Produces:** Blinding policy, deterministic blinded-view builder, access separation, leakage suite, and arm-classifier evidence

### Story 3.3: Run Three Independent Structured Judge Sessions

As a quality researcher,
I want three fresh blinded judge assessments per primary-analysis candidate,
So that qualitative quality is measured as a separate co-primary endpoint.

**Acceptance Criteria:**

**Given** an eligible primary-analysis candidate and `model-lock.json`
**When** panel judging runs
**Then** three independent fresh Copilot SDK sessions use distinct eligible pinned judge models and families, excluding the task generator to the maximum available extent
**And** a homogeneous or partially homogeneous panel is explicitly labeled with its stronger frozen human-calibration and shared-bias requirements.

**Given** a judge session
**When** it evaluates the deterministic blinded view
**Then** it receives the same frozen rubric and read-only tools but no treatment assignment, task-agent transcript identity, cost data, provider credentials other than host Copilot auth, or other judge output
**And** candidate presentation order is randomized from a sealed seed.

**Given** a completed assessment
**When** its record is stored
**Then** it contains structured criterion scores, uncertainty, and artifact citations for uncovered requirement satisfaction, semantic appropriateness, maintainability, unnecessary complexity, repository fit, and evidence-supported confidence
**And** model ID, system prompt, rubric, tool schemas, decoding controls, order, and runtime are hash-pinned.

**Given** judge-session artifacts before Story 4.1
**When** they are written or resolved
**Then** Story 3.3 consumes the Story 1.1 `ArtifactStorePort` and `ArtifactManifest` schema `1.0.0`, using deterministic fake artifacts solely for unpaid conformance until Story 4.1 provides the durable filesystem CAS
**And** paid execution and study inclusion remain blocked until Story 4.1 passes conformance.

**Depends on:** Story 2.1, Story 2.3, Story 3.2
**Requirements:** FR34, FR35; NFR11-NFR13, NFR37; AR3, AR20
**Produces:** Judge-session adapter, frozen rubric and tools, randomized panel schedule, and three immutable judge records per candidate

### Story 3.4: Calibrate and Gate Panel Reliability

As a research lead,
I want prospectively frozen calibration, agreement, drift, and bias checks,
So that unreliable panel scores cannot support qualitative claims.

**Acceptance Criteria:**

**Given** the frozen panel protocol
**When** panel qualification and each stage run
**Then** human-labeled calibration items, duplicate items, sentinels, per-criterion agreement, drift, leave-one-judge-out, and generator-versus-judge-family sensitivity are computed
**And** calibration items and thresholds cannot be changed after outcome access.

**Given** panel results
**When** reliability gates are evaluated
**Then** weighted kappa or ICC must be at least `0.70`, human-calibration MAE at most `0.10`, and blinded arm-classifier 95% upper AUC bound at most `0.60`
**And** failure blocks confirmatory qualitative claims without changing executable outcomes.

**Given** a homogeneous or partially homogeneous panel
**When** its quality is assessed
**Then** the prospectively frozen stronger human-calibration threshold and shared-bias sensitivity analysis are applied
**And** missing diversity is reported rather than filled with an unqualified model.

**Depends on:** Story 3.3
**Requirements:** FR35; NFR11, NFR26, NFR37, NFR45; AR20, AR31, AR48
**Produces:** Calibration set contract, agreement and drift metrics, sensitivity analyses, and panel gate decision

### Story 3.5: Adjudicate Frozen Disagreement Thresholds

As a scoring operator,
I want a fresh blinded adjudicator invoked only for material disagreement,
So that disputes are resolved transparently without replacing original judgments.

**Acceptance Criteria:**

**Given** three immutable judge records
**When** no prospectively frozen criterion-level disagreement threshold is crossed
**Then** no adjudicator session runs
**And** the threshold evaluation is retained as evidence.

**Given** a frozen disagreement threshold is crossed
**When** adjudication starts
**Then** a fresh blinded session receives candidate evidence and anonymized rationales but no treatment labels or judge identities
**And** it resolves each disputed criterion with artifact citations.

**Given** an adjudication result
**When** panel evidence is finalized
**Then** original scores and rationales remain immutable and the adjudication is appended as a separate record
**And** adjudication cannot override executable failure or any categorical blocker.

**Depends on:** Story 3.3
**Requirements:** FR36; NFR12, NFR29, NFR37, NFR45; AR20
**Produces:** Disagreement detector, fresh adjudicator adapter, immutable adjudication record, and non-replacement enforcement

### Story 3.6: Normalize Outcomes Under Separate Authorities

As a research lead,
I want explicit outcome authority and normalized endpoint records,
So that hard correctness, qualitative quality, and categorical blockers cannot substitute for one another.

**Acceptance Criteria:**

**Given** deterministic grader, panel, calibration, and adjudication records
**When** outcome normalization runs
**Then** executable correctness is authoritative for hard pass/fail and panel quality is authoritative only for its separate qualitative co-primary endpoint
**And** endpoint records retain scorer, rubric, grader, snapshot, and evidence hashes.

**Given** failed tests, security violations, governance failures, evidence-integrity failures, grading failures, or causal-validity failures
**When** panel scores are favorable
**Then** those scores cannot reverse the categorical or executable outcome
**And** the conflicting evidence is preserved for reconciliation.

**Given** a scorer attempts assignment access or cross-adapter import
**When** architecture and contract tests run
**Then** the access or import fails
**And** scoring remains dependent on domain ports and blinded evidence only.

**Depends on:** Story 3.1, Story 3.4, Story 3.5
**Requirements:** FR37; NFR14, NFR36, NFR45; AR20
**Produces:** Normalized hard and qualitative endpoints, authority policy, categorical override record, and scoring boundary tests

## Epic 4: Produce Complete Auditable Evidence and Economics

Operators can reconcile telemetry, immutable artifacts, provider-separated costs, backup, restore, and inclusion evidence.

### Story 4.1: Store Immutable Artifacts and Versioned Manifests

As an evidence operator,
I want a SHA-256 content-addressed store with verified manifests,
So that every raw or derived artifact is immutable and corruption is detectable.

**Acceptance Criteria:**

**Given** artifact bytes and metadata
**When** the filesystem CAS stores them
**Then** identity is lowercase SHA-256, bytes reside under `blobs/sha256/<prefix>/<remainder>`, and a versioned manifest records opaque IDs, attempt, kind, size, media type, producer/version, classification, secret flag, sources, and retention policy
**And** paths are convenience indexes, never identity.

**Given** any write, read, or copy
**When** hash verification fails or manifest authority conflicts
**Then** the operation fails closed with preserved corruption evidence
**And** the artifact cannot be linked for inclusion.

**Given** intact manifests and blobs
**When** indexes are deleted and rebuilt
**Then** experiment, run, attempt, and evidence reachability is reproduced
**And** artifacts remain retained until every linked claim is formally retired.

**Depends on:** Story 1.1
**Requirements:** FR42; NFR23, NFR29, NFR30, NFR37, NFR40; AR27
**Produces:** Filesystem CAS, artifact schema `1.0.0`, verified reads/copies, rebuild tooling, and corruption tests

### Story 4.2: Integrate the Sole-Writer Append-Only Ledger

As an execution controller,
I want one SQLite WAL writer to append lifecycle, evidence, and inclusion records,
So that workers cannot corrupt operational truth or place large evidence in the ledger.

**Acceptance Criteria:**

**Given** disposable workers executing attempts
**When** they need a lifecycle change or artifact link
**Then** they emit typed transition intents and never open SQLite directly
**And** the Inspect control process validates and appends experiment, run, attempt, transition, artifact-link, and inclusion records as sole writer.

**Given** an existing ledger record
**When** repository APIs request update or deletion
**Then** the operation is unavailable or rejected
**And** crash/reopen preserves exact append history and retry lineage.

**Given** prompts, patches, traces, grader bodies, Inspect events, or other large evidence
**When** persistence occurs
**Then** only CAS references and digests enter SQLite
**And** DuckDB and analysis code cannot mutate or use SQLite as analysis state.

**Depends on:** Story 1.1
**Requirements:** FR12, FR13, FR43; NFR1, NFR29, NFR31; AR17, AR18, AR28
**Produces:** SQLite WAL repository, transition-intent channel, sole-writer control integration, artifact links, and append-only migration tests

### Story 4.3: Capture Versioned Telemetry Semantics and Classes

As an observability engineer,
I want local telemetry with a frozen semantic map and required span registry,
So that all execution layers can be reconciled without leaking sensitive payloads.

**Acceptance Criteria:**

**Given** evaluator bootstrap
**When** telemetry components are verified
**Then** OpenTelemetry SDK/exporters are exactly `1.44.0`, `otelcol-contrib` is Windows amd64 `0.158.0` archive `otelcol-contrib_0.158.0_windows_amd64.tar.gz` SHA-256 `4314abde3c8acc67af58bb8d7611aa991fd80abe4a412695167f956d9fff3005`
**And** OpenInference is `0.1.31`, OpenAI instrumentation `0.1.53` only after pinned-client compatibility, and OTel GenAI Development fields pass through `memrelay.eval.genai-map/1.0.0`.

**Given** control, provisioning, Copilot, MCP, daemon, memory, framework, grader, judge, artifact, export, cost, reconciliation, and cleanup activity
**When** spans are emitted
**Then** required span classes and `memrelay.eval.*` IDs, stratum, history mode, provider, credential domain, cost source, evidence class, exposure state, and failure code are present
**And** closed process boundaries use opaque correlation and span links rather than fabricated parentage.

**Given** Collector shutdown, drop, duplicate, out-of-order, or partial-success faults
**When** telemetry conformance runs
**Then** every injected fault is detected and recorded
**And** one Collector per invocation is accepted only if export, shutdown, and reconciliation proofs pass.

**Depends on:** Story 2.10
**Requirements:** FR39; NFR16, NFR23, NFR30, NFR38; AR6-AR8, AR29
**Produces:** Collector config, semantic map, instrumentation adapters, required span registry, and telemetry fault suite

### Story 4.4: Separate Provider and Credential-Domain Identities

As a cost and security reviewer,
I want provider, service, resource, credential, and cost identities kept distinct,
So that Copilot, framework OpenAI, and local resources cannot be conflated.

**Acceptance Criteria:**

**Given** Copilot and framework OpenAI activity
**When** spans and cost records are emitted
**Then** they use different service names, providers, credential domains, cost sources, resource identities, and ledgers
**And** local embeddings and resources never claim an external provider.

**Given** telemetry, manifest, or cost schema validation
**When** a Copilot subscription identity is paired with an OpenAI credential domain, or vice versa
**Then** validation fails with an authority-conflict reason
**And** the affected run is ineligible pending reconciliation.

**Given** credential-free Collector, grader, MCP, evidence, or analysis processes
**When** their environments and emitted resources are inspected
**Then** no provider credential is present
**And** credential values never appear in telemetry or manifests.

**Depends on:** Story 2.3, Story 4.3
**Requirements:** FR40; NFR12, NFR13, NFR16, NFR33; AR25
**Produces:** Provider identity vocabulary, credential-domain validators, resource separation tests, and authority-conflict evidence

### Story 4.5: Reconcile Required Evidence and Decide Inclusion

As an evidence operator,
I want expected evidence reconciled against independent native authorities,
So that only complete, unconflicted terminal runs enter analysis.

**Acceptance Criteria:**

**Given** a terminal run
**When** reconciliation executes
**Then** expected telemetry classes are compared with SDK events, Inspect records, memrelay logs, grader/judge records, artifact writes, cost records, and ledger transitions
**And** OTLP delivery alone never proves completeness.

**Given** the required-evidence matrix
**When** `memrelay-eval reconcile --stage <stage>` runs
**Then** it verifies assignment/lifecycle, `.eval`, JSON, SDK, telemetry, workspace/patch, treatment, grading, panel/calibration/adjudication, costs or explicit unavailable values, configuration, model/parity, cleanup, transitions, and every hash
**And** it appends exactly one immutable included or excluded decision with reconciliation hash and typed reason.

**Given** missing primary evidence, corruption, credential leak, unauthorized disclosure, contamination, hidden-test tamper, grading conflict, or causal-validity conflict
**When** inclusion is evaluated
**Then** it fails closed and blocks the applicable run or stage
**And** no aggregate score can override the categorical blocker.

**Depends on:** Story 3.6, Story 4.1, Story 4.2, Story 4.4
**Requirements:** FR41, FR43, FR44; NFR14, NFR30-NFR32, NFR45; AR29, AR40
**Produces:** Required-evidence matrix, cross-authority reconciler, reconciliation report, and immutable inclusion decisions

### Story 4.6: Record Separate Quantity and Cost Ledgers

As an economics researcher,
I want separately queryable usage and local-resource ledgers,
So that unsupported fields and unlike quantities are not mistaken for zero or combined.

**Acceptance Criteria:**

**Given** Copilot subscription usage
**When** a cost entry is written
**Then** exposed input, cached input, output, reasoning, AI-credit, tool-call, quota, throttle, and reset quantities retain source and measurement status
**And** subscription allowance and incremental cash remain distinct.

**Given** framework OpenAI or local resource usage
**When** entries are recorded
**Then** OpenAI model/token/tool/service-tier/region/API-cost quantities and local CPU/memory/disk/process/Collector/storage quantities use separate logical ledgers
**And** every entry identifies attempt, provider, credential domain, source, canonical unit, price-table version, currency, measurement, and observation time.

**Given** a provider does not expose a usage field or two records use incompatible units
**When** normalization runs
**Then** the field is `unavailable`, never zero, and incompatible tokens are not aggregated
**And** conversion occurs only through a versioned conversion table.

**Depends on:** Story 2.10, Story 4.4
**Requirements:** FR45, FR46; NFR33, NFR34, NFR37; AR27
**Produces:** Versioned cost schema, three logical quantity ledgers, canonical unit vocabulary, and unavailable-value enforcement

### Story 4.7: Reprice Retained Quantities Without Rewriting History

As an economics researcher,
I want monetary outcomes repriced from immutable quantities and append-only revisions,
So that price updates do not alter observed usage.

**Acceptance Criteria:**

**Given** framework OpenAI quantities
**When** the initial frozen price table is applied
**Then** `gpt-4.1-mini-2025-04-14` prices per million tokens are input `$0.40`, cached input `$0.10`, and output `$1.60`
**And** the dated price-table artifact and hash are linked to every derived amount.

**Given** a new price or invoice revision
**When** repricing runs
**Then** a new append-only price/invoice record and derived monetary view are created from retained quantities
**And** prior quantities, prices, estimates, and results remain unchanged.

**Given** a report requests monetary results
**When** amounts are selected
**Then** estimated, subscription-normalized, and invoice-reconciled values are labeled distinctly
**And** Copilot and framework token quantities are never collapsed into one model-cost quantity.

**Depends on:** Story 4.6
**Requirements:** FR47; NFR33, NFR34, NFR37; AR11
**Produces:** Price-table artifacts, append-only revision model, repricing service, and labeled monetary views

### Story 4.8: Back Up and Restore Terminal Evidence

As an evidence custodian,
I want terminal evidence atomically copied to an independent local volume and restored,
So that paid trials meet durability and reachability requirements.

**Acceptance Criteria:**

**Given** `memrelay-eval bootstrap --backup-root <second-volume-path>`
**When** backup conformance runs
**Then** the path is verified on a different local volume and terminal ledger snapshots, manifests, `.eval`, JSON, and newly referenced CAS blobs are atomically copied and hash-verified
**And** same-volume, corrupt, or incomplete roots fail conformance without fallback.

**Given** a terminal run
**When** backup completes
**Then** the evidence RPO is at most the active in-flight attempt
**And** the backup receipt is linked to the run's required evidence.

**Given** only the backup root and documented restore inputs
**When** a restore drill runs
**Then** ledger-to-artifact reachability and verified reads are reconstructed within the 24-hour RTO
**And** any failed atomic-copy, index-rebuild, hash, or restore proof blocks paid pilots.

**Depends on:** Story 4.1, Story 4.2, Story 4.5
**Requirements:** FR57, FR58; NFR3, NFR30, NFR39, NFR40; AR25, AR29, AR35, AR47
**Produces:** Second-volume backup service, atomic receipts, index rebuild, verified restore drill, and durability conformance result

## Epic 5: Analyze Effects and Bound Claims

Researchers can run assignment-aligned analysis, safety measurement, replay, and evidence-linked claim reporting.

### Story 5.1: Materialize Reconciled Terminal Parquet

As a data engineer,
I want versioned Arrow schemas and Parquet datasets built only from reconciled terminal evidence,
So that confirmatory analysis has a typed immutable input boundary.

**Acceptance Criteria:**

**Given** reconciled included and excluded terminal records from Story 4.5
**When** materialization runs
**Then** PyArrow is exactly `25.0.0` and versioned schemas preserve IDs, assignment and analysis units, stratum, history mode, outcomes, failures, attrition, zero costs, unavailable values, evidence, and inclusion status
**And** no unreconciled operational row enters confirmatory tables.

**Given** two independent readers
**When** they read a dataset version
**Then** rows, Arrow types, nulls, units, and ordering keys agree exactly
**And** source manifest hashes, schema hash, protocol, population, endpoint, stratum, history mode, and materialization hash are attached.

**Given** rematerialization from identical reconciled inputs
**When** category and numeric outputs are compared
**Then** categories and counts match exactly and numeric values match within `1e-10` absolute or `1e-8` relative
**And** changed inputs produce a new dataset version rather than mutation.

**Depends on:** Story 4.5
**Requirements:** FR48, FR49; NFR6, NFR21, NFR30, NFR31, NFR37; AR9, AR21
**Produces:** Versioned Arrow schemas, reconciled terminal Parquet datasets, lineage manifests, and roundtrip contracts

### Story 5.2: Expose Read-Only DuckDB Analysis and Derivation Lineage

As a researcher,
I want read-only DuckDB queries over versioned Parquet,
So that analyses are reproducible and cannot mutate operational evidence.

**Acceptance Criteria:**

**Given** DuckDB exactly `1.5.5`
**When** `memrelay-eval analyze --stage <stage>` opens data
**Then** it reads only reconciled Parquet through a read-only connection
**And** no path exists to mutate SQLite, CAS, source Parquet, or operational records.

**Given** a query that combines product/engine strata, controlled/dynamic histories, model strata, or changed environment fingerprints
**When** no explicit valid stratified operation is declared
**Then** schema/query validation rejects it
**And** the rejection records the conflicting dimensions.

**Given** a derived table, diagnostic, or figure
**When** it is emitted
**Then** source table hashes, SQL or derivation hash, protocol, population, endpoint, stratum, history mode, units, and gate IDs are recorded
**And** deterministic figures reproduce exact hashes.

**Depends on:** Story 5.1
**Requirements:** FR48, FR49; NFR5, NFR21, NFR26, NFR31; AR9, AR21, AR41
**Produces:** Read-only DuckDB adapter, stratification guards, derivation manifests, and deterministic table/figure outputs

### Story 5.3: Estimate Assignment-Aligned ITT Effects

As a causal researcher,
I want frozen estimators aligned to the assigned interference unit,
So that failures, attrition, and clustered treatment remain valid study outcomes.

**Acceptance Criteria:**

**Given** a frozen protocol and analysis dataset
**When** an estimator is selected
**Then** assignment, experimental, resampling, clustering, and analysis units match the highest treatment-interference level
**And** controlled histories use controlled-effect estimands while dynamic histories use sequence-level total-policy estimands.

**Given** every assigned unit
**When** ITT tables are built
**Then** failures, attrition, timeouts, provider outcomes, zero costs, and unavailable evidence remain represented according to the frozen policy
**And** no per-protocol or complete-case replacement is silently used.

**Given** bounded concurrency, run order, host fingerprint, quota, throttle, or provider contention
**When** balance and sensitivity diagnostics run
**Then** arm balance and applicable strata are reported
**And** changed fingerprints and model roles are analyzed separately.

**Depends on:** Story 5.1
**Requirements:** FR50; NFR4-NFR6, NFR18, NFR41-NFR43; AR32, AR33
**Produces:** Frozen estimator registry, ITT tables, cluster/sequence resampling, balance diagnostics, and estimand metadata

### Story 5.4: Enforce Multiplicity, Power, and Claim Thresholds

As a research lead,
I want frozen multiplicity, power, interval, and gate calculations,
So that outcomes cannot weaken confirmatory standards.

**Acceptance Criteria:**

**Given** confirmatory claim families
**When** inference runs
**Then** familywise alpha is `0.05` with Holm control, simultaneous intervals are emitted, and target power is `0.80`
**And** a 512-unit primary with frozen simulated power below `0.80` remains estimation-only without silent enrollment expansion.

**Given** reliability or qualitative benefit claims
**When** gates are evaluated
**Then** the point difference must be at least `+0.05` and the simultaneous 95% lower bound must exceed `0`
**And** qualitative results use the `[0,1]` blinded panel scale and require the panel gate.

**Given** no-regression, cost, or active-wall-time claims
**When** gates are evaluated
**Then** no-regression uses a one-sided 97.5% lower bound above `-0.02`; superiority requires a ratio at most `0.90`, simultaneous 95% upper bound below `1.0`, and reliability/quality lower bounds above `-0.02`
**And** thresholds frozen before the first study trial cannot be weakened after pilot or primary outcomes.

**Depends on:** Story 5.3
**Requirements:** FR50; NFR7-NFR11, NFR18, NFR26, NFR44; AR32, AR48
**Produces:** Holm controller, simultaneous intervals, power simulator, frozen claim gates, and estimation-only decisions

### Story 5.5: Measure Safety, Necessity, and Contamination

As a safety researcher,
I want frozen safety denominators and adversarial audits,
So that rare harms, shortcuts, and contamination are not hidden by aggregate benefit.

**Acceptance Criteria:**

**Given** included and assigned populations
**When** safety analysis runs
**Then** denominators, ascertainment coverage, harm tails, injected-positive sensitivity, and exact upper bounds are reported
**And** missing ascertainment cannot be represented as absence of harm.

**Given** candidate tasks and histories
**When** memory-necessity review, shortcut audit, contamination checks, canaries, holdouts, grader stability, and task dispositions run
**Then** each result links to immutable evidence and frozen policy
**And** compromised tasks are excluded with a categorical reason rather than selectively repaired.

**Given** any confirmed credential leak, unauthorized use, treatment contamination, hidden-test tamper, high-severity poisoning, hash mismatch, or authority conflict
**When** aggregate performance is favorable
**Then** the categorical failure overrides aggregate performance for the affected stage or claim
**And** evidence is preserved for bounded reporting.

**Depends on:** Story 5.1
**Requirements:** FR51, FR52; NFR14, NFR15, NFR32, NFR44, NFR45; AR30-AR32
**Produces:** Safety tables, ascertainment and sensitivity metrics, audit dispositions, harm tails, and categorical gate decisions

### Story 5.6: Reproduce Analysis, Grading, and Evidence Offline

As an independent reviewer,
I want network-off deterministic replay and clearly separated stochastic reruns,
So that published tables and evidence can be independently verified.

**Acceptance Criteria:**

**Given** a retained evidence bundle
**When** network-off replay runs
**Then** analysis categories/counts reproduce exactly, numeric values meet `1e-10` absolute or `1e-8` relative tolerance, and figure hashes match exactly
**And** no provider credential or network call is available.

**Given** grader and normalized evidence inputs
**When** they are replayed
**Then** grader binary/test outcomes match exactly, continuous scores meet `1e-6`, and canonical timestamp/path-normalized evidence hashes match
**And** mismatches identify the source and derivation hashes.

**Given** a requested stochastic rerun or independent replication
**When** it executes
**Then** it receives a new protocol/run identity and is reported separately from deterministic reproduction
**And** it cannot overwrite or backfill the original confirmatory result.

**Depends on:** Story 4.8, Story 5.2, Story 5.4, Story 5.5
**Requirements:** FR53; NFR13, NFR21-NFR25, NFR37; AR41, AR44
**Produces:** Network-off replay command, reproduction comparison, stochastic-rerun protocol, and replication evidence

### Story 5.7: Generate Evidence-Linked Reports and Bounded Claims

As a release decision-maker,
I want local reports that bind every conclusion to its tested scope and evidence,
So that null, harmful, indeterminate, and positive results are communicated without overclaiming.

**Acceptance Criteria:**

**Given** completed stage analysis
**When** `memrelay-eval report --stage <stage>` runs
**Then** the generated local report includes intervals, diagnostics, Pareto surfaces, harm tails, costs, active and non-active time, panel metrics, gates, and claim decisions
**And** every table, figure, and claim links protocol, population, model, endpoint, stratum, history regime, source hashes, derivation hash, and evidence/gate IDs.

**Given** a proposed efficacy, safety, economic, or release claim
**When** claim bounding runs
**Then** construction, component tests, deterministic fixtures, unreconciled trials, engine upper bounds, and pilot outcomes cannot be represented as shipped-product confirmatory efficacy
**And** null, harmful, indeterminate, and positive conclusions use frozen language.

**Given** release fitness evaluation
**When** gates are applied
**Then** at least one reliability, qualitative, cost, or wall-time benefit passes, every non-target primary outcome passes non-inferiority, and every categorical gate passes
**And** the claim is limited to the tested population, model, stratum, protocol, and history regime.

**Depends on:** Story 4.7, Story 5.2, Story 5.4, Story 5.5
**Requirements:** FR64, FR65; NFR8-NFR10, NFR33, NFR34, NFR40, NFR44, NFR45; AR42, AR46
**Produces:** Evidence-linked local report, bounded claim registry, Pareto/harm diagnostics, and release-fitness decision

## Epic 6: Operate Governed Experiment Stages

Operators can enforce stage entry/exit bundles, budgets, circuit breakers, and promotion rules.

### Story 6.1: Seal Stage Bundles and Enforce the Stage CLI

As a study operator,
I want immutable stage entry and exit bundles enforced by non-interactive commands,
So that no stage can promote itself merely because its processes completed.

**Acceptance Criteria:**

**Given** a stage configuration
**When** its entry bundle is sealed
**Then** exact catalog, protocol, SDK, runtime, model, environment, grader, judge, telemetry, price, limit, and preceding-exit hashes are recorded
**And** post-seal changes require a new protocol or stage ID.

**Given** `memrelay-eval run --stage integration|pilot|primary|secondary|cross-repo`
**When** the preceding immutable exit bundle is absent, corrupt, rejected, or incomplete
**Then** entry is refused with a typed status before enrollment
**And** no automatic fallback topology or stage promotion occurs.

**Given** any bootstrap, lock, compile, conformance, run, reconcile, analyze, or report command
**When** it terminates
**Then** it is non-interactive and writes a manifest with input/output hashes, runtime lock, protocol ID, and typed terminal status
**And** paid execution requires explicit operator invocation or approved scheduling.

**Depends on:** Story 1.8, Story 2.1, Story 2.10, Story 3.6, Story 4.5, Story 5.7
**Requirements:** FR54, FR55, FR66; NFR24-NFR26, NFR37; AR38-AR42, AR48
**Produces:** Stage-bundle schema, stage transition policy, CLI entry guards, and per-command manifests

### Story 6.2: Gate Bootstrap and Conformance

As a study operator,
I want a complete bootstrap/conformance gate before enrollment,
So that paid trials cannot start on an unqualified evaluator substrate.

**Acceptance Criteria:**

**Given** a clean Python 3.13 evaluator environment and valid Copilot subscription authentication
**When** `memrelay-eval bootstrap` and `memrelay-eval conformance` run
**Then** runtime, catalog, credential, workspace/clone isolation, grader, judge, telemetry, CAS, backup/restore, and reconciliation contracts are tested
**And** `runtime-lock.json` and `conformance-report.json` bind every proof and version/hash from prior stories.

**Given** fake providers and the synthetic catalog
**When** unpaid CI conformance runs
**Then** one catalog-to-report path completes with no Copilot or OpenAI call
**And** schema, state machine, concealment, isolation, blinding, telemetry faults, CAS corruption, reconciliation, repricing, Parquet, and no-network tests pass.

**Given** any failed proof
**When** enrollment is requested
**Then** all study enrollment is blocked until repaired conformance is rerun
**And** successful construction is labeled conformance, not efficacy evidence.

**Depends on:** Story 2.2, Story 2.3, Story 3.6, Story 4.1, Story 4.3, Story 4.5, Story 4.8, Story 5.6, Story 6.1
**Requirements:** FR54, FR55; NFR3, NFR25, NFR27, NFR38, NFR39; AR29, AR35, AR38, AR43, AR44, AR47
**Produces:** Bootstrap bundle, `conformance-report.json`, unpaid vertical-slice proof, and enrollment blocker

### Story 6.3: Run and Exit the 32-Run Integration Stage

As a study operator,
I want the fixed integration envelope enforced and reconciled,
So that infrastructure failures are discovered before pilot outcomes exist.

**Acceptance Criteria:**

**Given** an accepted conformance bundle hash
**When** integration starts
**Then** exactly 32 runs are planned as 8 synthetic scenarios across `YL` and `TR` with 2 repeats
**And** run order and concurrency are bounded, arm-balanced, and recorded.

**Given** all integration attempts terminate
**When** the exit gate runs
**Then** at least 30 of 32 attempts are infrastructure-complete, every terminal attempt has complete reconciled evidence, and categorical blockers are zero
**And** the immutable exit bundle includes run, reconciliation, backup, parity, cost, and fault summaries.

**Given** fewer than 30 infrastructure-complete attempts, incomplete terminal evidence, or any categorical blocker
**When** integration closes
**Then** the stage fails and the entire 32-run stage must be rerun under a new stage ID after repair
**And** no favorable subset advances.

**Depends on:** Story 6.2
**Requirements:** FR54, FR55; NFR3, NFR14, NFR19, NFR30, NFR32, NFR42; AR30
**Produces:** 32-run integration plan, balanced schedule, immutable exit bundle, and whole-stage rerun decision

### Story 6.4: Run and Gate the 128-Unit Blinded Pilot

As a research lead,
I want a fixed blinded pilot that estimates operating characteristics without entering confirmation,
So that variance and panel defects are learned without weakening thresholds.

**Acceptance Criteria:**

**Given** an accepted integration exit bundle
**When** the pilot starts
**Then** exactly 128 assigned units across 16 tasks use frozen assignments, holdouts, blinding, panel, evidence, budget, and analysis contracts
**And** pilot data are permanently marked non-confirmatory.

**Given** pilot completion
**When** exit gates run
**Then** overall evidence completeness is at least 98%, panel and blinding gates pass, and variance, ICC, attrition, harm, and frozen power simulation are published
**And** pilot outcomes cannot weaken thresholds or enter the primary confirmatory estimate.

**Given** evidence completeness below 98% or a failed panel, blinding, security, governance, grading, evidence, or causal gate
**When** pilot closes
**Then** repair requires a fresh 128-unit pilot under a new stage ID
**And** no subset or regraded favorable result advances.

**Depends on:** Story 3.4, Story 5.4, Story 6.1
**Requirements:** FR54, FR55; NFR7, NFR11, NFR14, NFR26, NFR32, NFR44; AR31, AR48
**Produces:** 128-unit pilot plan, variance/power publication, panel/blinding gate evidence, and pilot exit bundle

### Story 6.5: Run Primary and Secondary Model Stages

As a research lead,
I want fixed primary and secondary envelopes analyzed as separate model strata,
So that confirmatory and generalization claims obey their planned populations.

**Acceptance Criteria:**

**Given** accepted pilot gates and locked primary protocol/holdout hashes
**When** the primary stage runs
**Then** exactly 512 assigned units across 32 tasks are retained in complete ITT
**And** exit requires simultaneous intervals, harm tails, Pareto surface, panel and safety results, and explicit claim-gate decisions.

**Given** primary evidence is reconciled and qualified `M1` or `M2` roles exist
**When** secondary generalization runs
**Then** it enrolls 96 units per available role, never more than 192 total, with each role a separate model stratum
**And** an unavailable role is recorded and never substituted.

**Given** a primary categorical blocker or frozen power below `0.80`
**When** stage conclusions are issued
**Then** the affected claim family stops or the result is estimation-only, respectively
**And** enrollment does not silently expand and secondary evidence cannot repair the primary claim.

**Depends on:** Story 2.1, Story 5.4, Story 5.5, Story 6.1
**Requirements:** FR54, FR55; NFR4-NFR11, NFR14, NFR26, NFR44, NFR45; AR32, AR33
**Produces:** 512-unit primary bundle, up-to-192-unit secondary bundles, separate model-stratum analyses, and claim decisions

### Story 6.6: Stop New Attempts with Evidence-Preserving Circuit Breakers

As a study operator,
I want frozen resource and integrity circuit breakers,
So that overruns stop enrollment without erasing active-attempt evidence.

**Acceptance Criteria:**

**Given** per-run and stage token, tool, Copilot AI-credit, framework input/output/USD, active-time, elapsed-time, quota, throttle, model, infrastructure-failure, and evidence-loss limits
**When** any limit is reached
**Then** no new attempt starts and a typed circuit-breaker record is appended
**And** attempts already started are allowed to terminate or cancel under policy while preserving partial evidence.

**Given** active attempts during a stop
**When** terminal handling runs
**Then** each attempt remains in ITT with immutable terminal classification, costs, exposure state, and available evidence
**And** capped runs are never dropped or replaced.

**Given** quota, throttle, unavailable models, provider contention, or repeated infrastructure failure
**When** the stage is summarized
**Then** these are observable outcomes or strata with arm-balanced order/concurrency diagnostics
**And** no threshold or provider fallback is selected automatically.

**Depends on:** Story 1.7, Story 4.3, Story 4.6, Story 6.1
**Requirements:** FR56; NFR1, NFR19, NFR26, NFR41, NFR42; AR19, AR47
**Produces:** Circuit-breaker service, frozen envelopes, stop records, active-attempt drain/cancel handling, and contention diagnostics

## Epic 7: Qualify Observation and Cross-Repository Scope

Release owners can validate current capture paths while keeping cross-repository execution mechanically disabled until governance passes.

### Story 7.1: Prove Observation Paths with Sentinels and Reconciliation

As a release owner,
I want sentinel events traced through each configured observation path,
So that continuous-capture claims reflect current source behavior rather than stale documentation.

**Acceptance Criteria:**

**Given** current `SessionDiscoveryPoller`, `RunObserveCapture`, and `LiveTailCapture` implementations
**When** observation conformance runs for the configured poll, replay, and live-tail modes
**Then** unique non-secret sentinels traverse discovery, capture, spool/graph evidence, telemetry, manifest, and reconciliation boundaries
**And** path-specific delivery, ordering, duplicate, gap, restart, and terminal-flush results are retained.

**Given** a missing, duplicated, reordered, delayed, or unreconciled sentinel
**When** observation qualification closes
**Then** the affected path fails with a typed reason
**And** no continuous-capture completeness claim is emitted.

**Given** a configured observation mode
**When** its source implementation or semantic mapping changes
**Then** a new conformance hash and protocol version are required
**And** prior sentinel evidence remains bound to its original implementation.

**Depends on:** Story 2.6, Story 4.3, Story 4.5, Story 6.1
**Requirements:** FR63; NFR1, NFR23, NFR26, NFR30, NFR38; AR13, AR29, AR48
**Produces:** Observation sentinel suite, per-path reconciliation report, capture completeness decision, and implementation hash

### Story 7.2: Bind Release Evidence to Qualified Observation Scope

As a release owner,
I want observation and regression evidence mapped to narrowly bounded release statements,
So that product fixtures and sentinels cannot be promoted into efficacy claims.

**Acceptance Criteria:**

**Given** passed observation sentinel evidence and existing retrieval or release-roundtrip fixtures
**When** release evidence mapping runs
**Then** each artifact is labeled by exact product path, configuration, version, observation mode, evidence class, and supported claim
**And** fixtures remain bounded product regression evidence, not downstream efficacy evidence.

**Given** a proposed continuous-capture or release statement
**When** the supporting mode lacks passed sentinel and reconciliation evidence
**Then** the statement is rejected or explicitly marked unqualified
**And** favorable evidence from another path or mode cannot substitute.

**Given** a passed bounded release statement
**When** a local report is generated
**Then** it links sentinel, configuration, source, reconciliation, protocol, and gate hashes
**And** it does not imply safety, economics, generalization, or cross-repository fitness beyond tested evidence.

**Depends on:** Story 5.7, Story 7.1
**Requirements:** FR63, FR64, FR65; NFR26, NFR40, NFR44, NFR45; AR42, AR46
**Produces:** Release-evidence map, bounded observation claims, unsupported-claim rejection, and report links

### Story 7.3: Deny Cross-Repository Execution by Default

As a governance owner,
I want cross-repository planning and execution mechanically disabled,
So that no private or unauthorized repository is accessed before the complete governance gate passes.

**Acceptance Criteria:**

**Given** evaluator v1 installation, ordinary configuration, or a stage request
**When** a repository differs from the authorized task repository or `--stage cross-repo` is requested
**Then** execution is denied before repository discovery, clone, cache lookup, assignment, or task exposure
**And** no environment flag, safe default, or operator convenience option can bypass the gate.

**Given** a denied request
**When** evidence is recorded
**Then** only treatment-neutral request identity, authorization decision, policy version, and typed reason are retained
**And** no repository content, name, credential, or private metadata enters telemetry or artifacts.

**Given** CI and non-cross-repository stages
**When** architecture tests run
**Then** cross-repository adapters are unreachable unless a verified governance qualification artifact is supplied
**And** revocation immediately returns the system to deny-by-default.

**Depends on:** Story 1.1
**Requirements:** FR62; NFR15-NFR17, NFR24, NFR36; AR34, AR45
**Produces:** Cross-repository deny policy, pre-discovery guard, revocation behavior, and architecture enforcement tests

### Story 7.4: Qualify Governance Contracts for the 24-Cluster Stage

As a governance owner,
I want every DG-R control proven and bound to a cross-repository stage bundle,
So that later cluster trials can run only with complete authorization and lifecycle evidence.

**Acceptance Criteria:**

**Given** primary-stage completion
**When** cross-repository qualification is requested
**Then** identity, authorization, repository provenance, revocation, cache isolation, migration, deletion, backup, restore, data classification, and audit contracts must each pass
**And** every proof is versioned, hash-pinned, repository-scoped, time-bounded, and linked in one immutable DG-R bundle.

**Given** a complete and current DG-R bundle
**When** `memrelay-eval run --stage cross-repo` evaluates entry
**Then** it permits exactly the frozen 24-cluster envelope with repository-level assignment, experimental, resampling, and analysis units
**And** cluster-level ITT retains authorization and revocation evidence.

**Given** any missing, expired, revoked, conflicting, or failed governance proof
**When** entry or an active stage is evaluated
**Then** the entire cross-repository stage is disabled and new work stops
**And** active evidence is preserved, no fallback repository is selected, and no partial aggregate claim is released.

**Depends on:** Story 4.8, Story 6.1, Story 6.5, Story 6.6, Story 7.3
**Requirements:** FR54, FR55, FR56, FR62; NFR4, NFR14-NFR17, NFR30, NFR39, NFR45; AR29, AR34, AR47
**Produces:** DG-R qualification contracts, immutable governance bundle, 24-cluster stage gate, revocation stop, and cluster-level ITT metadata
