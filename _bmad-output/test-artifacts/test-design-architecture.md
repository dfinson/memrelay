---
workflowStatus: 'completed'
totalSteps: 5
stepsCompleted: ['step-01-detect-mode', 'step-02-load-context', 'step-03-risk-and-testability', 'step-04-coverage-plan', 'step-05-generate-output']
lastStep: 'step-05-generate-output'
nextStep: ''
lastSaved: '2026-08-05T09:03:41.511-04:00'
workflowType: 'testarch-test-design'
inputDocuments:
  - '_bmad/tea/config.yaml'
  - '_bmad-output/planning-artifacts/research/technical-evaluating-memrelay-collective-recall-for-coding-agent-reliability-cost-and-efficiency-research-2026-08-04.md'
  - 'SPEC.md'
  - 'docs/ARCHITECTURE.md'
  - 'docs/adr/0001-graph-backends.md'
  - 'docs/release-gate.md'
  - 'tests/eval/*'
  - 'tests/integration/test_release_gate_roundtrip.py'
  - '.github/workflows/ci.yml'
  - '.github/workflows/release.yml'
  - 'pyproject.toml'
---

# Test Design for Architecture: memrelay Collective-Recall Evaluation

**Purpose:** Architectural concerns, testability gaps, and NFR requirements for Architecture/Dev review. This is the contract for what must exist before evaluator test development and claims.  
**Date:** 2026-08-05  
**Author:** Davidfinson / TEA Master Test Architect  
**Status:** Architecture Review Pending — implementation and owner gates unresolved  
**Project:** dfinson-solid-happiness  
**PRD:** `SPEC.md`  
**Architecture/ADR:** `docs/ARCHITECTURE.md`; `docs/adr/0001-graph-backends.md`

## Executive Summary

**Scope:** Build a defensible evidence architecture for memrelay retrieval, downstream reliability, efficiency, cost, safety, longitudinal effects and bounded release claims without presuming benefit.

- Current architecture is headless and dependency-injectable, with durable spool and deterministic seams.
- `memrelay observe` remains one-shot; continuous observation is absent.
- `EV-FIXTURE-RETRIEVAL` and `EV-ROUNDTRIP-MCP` have separate bounded claims; neither proves whole trust, efficacy, safety, cost or continuous capture.
- `docs/release-gate.md` still overclaims and `release.yml` does not mechanically enforce the roundtrip before publish. Both are open blockers.
- Risk register: **36 total; all 36 score ≥6; 17 score-9**. QA companion contains the sole **161-row Markdown scenario design source**.
- The corrected runtime is local Inspect orchestration → custom Copilot SDK solver → owner-subscription Copilot model service; framework OpenAI calls, credentials, spans, and costs are separate. No executable catalog/model lock/schema/generator/CI exists.
- Cross-repository confirmation is blocked until authenticated caller/principal binding and full authorization/provenance/revocation/migration/deletion/restore evidence exists.

## Quick Guide

### 🚨 BLOCKERS — Team Must Decide or Implement

1. **DG-0 owner gates:** choose endpoint mode/primary, alpha/weights/power/margins, assignment/pilot branch, plausible simulation cells, population/history/scope, governance, economics, staffing and retry policy. No default is approved.
2. **Runtime and catalog architecture:** capture/hash native `list_models()`/capabilities; deterministically pin M0 and optional M1/M2; assert current-subscription auth; prohibit SDK BYOK/alternate task inference; implement pinned JSON Schema 2020-12, immutable catalog, hashes, mappings and CI.
3. **Credential/evidence control plane:** keep the OpenAI key/base URL only in the named framework process; fail closed against `borrow-host`; separate Copilot/framework provider spans and ledgers; preserve immutable assignments, reconciliation and data lock.
4. **Security/governance:** caller authentication, principal-role-group/purpose binding, confused-deputy resistance, cache invalidation, policy-version render recheck, revocation, migration, deletion and quarantined backup restore.
5. **Release implementation:** correct release-gate wording and make publish depend on the roundtrip test. Current repository state does neither.
6. **Cross-repository prohibition:** keep `R` scope disabled until DG-R implementation evidence passes.

### ⚠️ HIGH PRIORITY — Team Must Validate

1. Register exactly one endpoint family: reliability 3, efficiency 5, or dual 5; use exact randomization inference and procedure-compatible simultaneous bounds.
2. Freeze ITT outcomes and stage envelopes (32 integration, 128 pilot, 512 primary, ≤192 secondary, 24 gated cross-repository); balance quota/rate-limit exposure and prohibit favorable substitution.
3. Operationalize quality/harm, safety exposure/ascertainment/sensitivity/upper bounds, and pre-outcome MI/YL envelopes.
4. Preserve distinct cost/wall estimands and faithful null/harm/indeterminate/positive replication; require repeated positives for broad efficacy.
5. Validate and repair the committed Inspect/OTel/OpenInference/Parquet-DuckDB/local-CAS stack; use time-boxed temporary fallbacks with expiry/migration tests, not an open tooling bake-off.

### 📋 INFO ONLY — Solutions Provided

- Python 3.13 stdlib/pytest/JSONL/hash-manifest fallbacks remain available while committed integrations are validated and repaired.
- QA companion owns the one Markdown design-source representation plus design-time risk/requirement projections; architecture, handoff and progress reference it rather than duplicate it.
- Embedded Ladybug remains default; cloud backends are wiring-tested only.
- Execution mode is sequential until product arm/history isolation is proven.

## For Architects and Devs — Open Topics

### Risk Assessment

**Total:** 36; high 36; medium 0; low 0. Probability/impact are 1–3 and score=P×I. Scores never average away categorical blockers.

#### High-Priority Risks (Score ≥6)

| Risk | Category | Description | P | I | Score | Mitigation | Owner | Timeline |
|---|---|---|---:|---:|---:|---|---|---|
| R-001 | BUS | Tasks do not require memory or expose shortcuts, creating construct-invalid uplift/null results. | 3 | 3 | 9 | Independent rubric, shortcut audit, oracle pilot, natural-task replication. | Task curation lead | Before pilot |
| R-002 | DATA | Shared or treatment-generated histories contaminate controls or induce carryover/post-treatment bias. | 3 | 3 | 9 | Regime-specific whole-history assignment, immutable replay, ITT, no crossover. | Statistical lead | Protocol lock |
| R-003 | DATA | Assignment, experimental, observation, and analysis units are conflated; effective N is overstated. | 3 | 3 | 9 | Materialize parent IDs; analyze and resample at independently assigned units. | Statistical lead | Protocol lock |
| R-004 | BUS | Scope increments compare unrelated histories/tasks rather than exact nested `S`, `S+P`, `S+P+A`, `S+P+A+R`. | 2 | 3 | 6 | Frozen nested content and assignment at highest interference level. | Evaluation lead | Before pilot |
| R-005 | SEC | Unauthorized cross-agent/user/repository or secret disclosure occurs. | 2 | 3 | 6 | Isolation, per-record authorization, canaries, denial scenarios, incident kill switch. One confirmed event blocks. | Security lead | Before any real data |
| R-006 | DATA | Owner-level grouping is mistaken for cross-repository authorization. | 3 | 3 | 9 | Keep `R` disabled until provenance, per-read policy, revocation, migration, and deletion conformance pass. | Privacy/legal lead | Separate program |
| R-007 | DATA | Revocation/deletion leaves graph, embedding, cache, export, viewer, backup, or key copies. | 3 | 3 | 9 | Field registry, deletion bundle, tombstones, rebuild, receipts, negative retrieval, independent sign-off. | Privacy engineering lead | Before governed trial |
| R-008 | DATA | Missing/mutable assignments, outcomes, hashes, or lineage invalidate evidence. | 2 | 3 | 6 | Append-only manifests, signatures/hashes, expected-artifact reconciliation, 100% primary fields. | Data/observability lead | Stdlib baseline |
| R-009 | OPS | Telemetry loss/duplication/out-of-order delivery creates false completeness. | 3 | 3 | 9 | Expected-record matrix and fault injection; raw JSONL fallback; missingness bounds. | Data/observability lead | Spike/L0 |
| R-010 | BUS | Grader, protected tests, image, dependencies, or retry policy vary by arm or drift. | 2 | 3 | 6 | Frozen grader contract, baseline/gold repeats, tamper hashes, drift quarantine. | Benchmark lead | Before enrollment |
| R-011 | DATA | Public/model contamination or benchmark leakage makes success noncausal. | 2 | 3 | 6 | Cutoff provenance, duplicate/semantic/canary checks, isolated holdouts. | Contamination lead | Before enrollment |
| R-012 | BUS | Blinded views leak arm and subjective adjudication becomes treatment-aware. | 3 | 2 | 6 | Deterministic transform, leak classifier/human guesses, calibration, double labels, sensitivity analyses. | Adjudication lead | Pilot |
| R-013 | BUS | Endpoint multiplicity, family-cardinality drift, incompatible intervals, or ad hoc sequential peeking inflates false-positive claims. | 2 | 3 | 6 | Register exactly one 3/5/5 mode family, closed/graphical weighted-Bonferroni procedure, compatible simultaneous bounds, named BH families, and fixed-N default or simulated spending. | Statistical lead | Registration |
| R-014 | BUS | Fixed “pair count” underpowers clustered/longitudinal trials. | 3 | 3 | 9 | ≥10,000 simulations/cell using blinded nuisance inputs and exact estimator/assignment. | Statistical lead | After pilot |
| R-015 | PERF | Latency/cost apparent gains are artifacts of failures, missing records, or unequal budgets. | 2 | 3 | 6 | ITT including zero-cost failures, arm parity, three cost estimands, bounds. | FinOps + statistical leads | Pilot/analysis |
| R-016 | OPS | Copilot subscription consumption, framework OpenAI metered spend, local resources, price/invoice/FX, or allocation are conflated or drift. | 2 | 3 | 6 | Separate provider ledgers/provenance, labelled marginal components, versioned prices/invoices and sensitivities. | FinOps + data leads | Before economic analysis |
| R-017 | TECH | A committed framework integration changes behavior, loses raw evidence, locks data, or blocks deletion. | 2 | 3 | 6 | Validate the pinned integration, repair within its time box, then activate an expiry-controlled fallback. | Platform lead | Integration spike |
| R-018 | TECH | Reproducibility classes are conflated; deterministic replay is presented as trajectory replication. | 3 | 3 | 9 | Apply separate analysis, grader, evidence replay, stochastic, and independent replication contracts. | Reproducibility lead | All phases |
| R-019 | OPS | Provider/model/version/time drift is pooled silently and invalidates comparability. | 3 | 2 | 6 | Pin/record versions, stratify batches, trigger dependent reruns, bound claim population. | Operations lead | Trial release |
| R-020 | BUS | Matched-irrelevant or oracle arms leak instructions/solutions or differ in format/token/latency. | 2 | 3 | 6 | Independent certification, injection review, no hidden solution, frozen envelopes/yokes. | Evaluation lead | Before pilot |
| R-021 | OPS | Current one-shot observation and zero counter are overclaimed as continuous capture. | 3 | 3 | 9 | Explicit release wording plus separate continuous-observation implementation and sentinel. | Product + release leads | Immediate |
| R-022 | BUS | Deterministic fixture is overclaimed as the complete trust/release contract. | 3 | 3 | 9 | Rename evidence to “deterministic pipeline wiring fixture passed”; gate higher claims separately. | Release lead | Immediate |
| R-023 | OPS | Staffing, legal/privacy approvals, provider access, compute, or independent review are underplanned. | 3 | 2 | 6 | Workstream ranges, dependency map, owners, budget, and 15–40 week baseline critical path plus external waits. | Program lead | Before commitment |
| R-024 | SEC | Prompt injection/poisoned/stale/contradictory memory causes unsafe code or evidence manipulation. | 2 | 3 | 6 | Atomic attack scenarios, provenance/validity checks, executable grading, zero-tolerance high-severity path. | Security/red-team lead | L0/L1 |
| R-025 | DATA | Assignment, estimator, weighting, clustering, correction, or interval construction diverges from the registered design. | 2 | 3 | 6 | Exact randomization inference; equal-task/equal-sequence weights; assignment-unit clustering; CR2/wild-bootstrap sensitivity; compatible simultaneous intervals. | Statistical lead | Protocol lock/registration |
| R-026 | DATA | Retry, re-grade, or multiple-attempt handling substitutes a favorable outcome into ITT. | 2 | 3 | 6 | Freeze terminal outcomes; permit at most one arm-blind pre-exposure infrastructure replacement; retain lineage; prohibit post-exposure and best-of-N substitution. | Evaluation + data leads | Protocol lock |
| R-027 | SEC | Zero observed safety events are called safe despite sparse exposure, incomplete ascertainment, or weak detectors. | 2 | 3 | 6 | Gate-specific exposure denominators, coverage and injected-positive sensitivity intervals, adjusted exact upper bounds, and bounded language. | Safety lead | Before pilot |
| R-028 | DATA | Markdown scenarios or hand-edited mappings diverge across architecture, QA, handoff, progress, and future execution. | 3 | 3 | 9 | One schema-validated immutable JSON catalog; deterministic generated mappings; fixture/evidence hashes; CI byte-identity and referential-closure checks. | TEA + data leads | Before harness implementation |
| R-029 | SEC | Cross-repository caller spoofing, role/group confusion, stale authorization caches, policy-version TOCTOU, or confused-deputy behavior discloses records. | 3 | 3 | 9 | Cryptographic caller authentication, principal-role-group binding, purpose-bound delegation, read/render recheck, cache invalidation, revocation and migration scenarios. | Security + privacy leads | DG-R separate program |
| R-030 | DATA | A backup restored after revocation but before expiry makes revoked data readable again. | 2 | 3 | 6 | Restore into quarantine; apply current tombstones, authorization and policy version before indexing/rendering; negative retrieval and audit evidence. | Privacy + operations leads | DG-R separate program |
| R-031 | OPS | Release documentation overclaims a fixture and publish proceeds without the roundtrip gate. | 3 | 3 | 9 | Correct `docs/release-gate.md`; make `release.yml` invoke/depend on the roundtrip before publish; keep both as explicit unresolved implementation blockers until repository changes exist. | Release lead | Immediate |
| R-032 | TECH | Model names are invented, capabilities ignored, or a frozen model silently substituted. | 3 | 3 | 9 | Hash native catalog/capabilities; deterministic arm-blind selection; exact pin; pause/version on change. | Platform + evaluation leads | Experiment start |
| R-033 | SEC | Task-agent inference escapes the Copilot SDK/current-subscription boundary through SDK BYOK, Inspect provider routing, or another client. | 3 | 3 | 9 | Assert signed-in subscription path; reject custom providers/BYOK; audit zero Inspect provider calls. | Platform + security leads | DG-2/DG-3 |
| R-034 | SEC | Framework OpenAI credential/endpoint leaks into agent, MCP, workspace, prompts, tools, traces, artifacts, or logs. | 3 | 3 | 9 | Minimal child environment, canary scans, framework-only injection and fail-closed concrete-client preflight. | Security + platform leads | DG-2/DG-3 |
| R-035 | DATA | Copilot agent and framework OpenAI spans/ledgers share provider identity or cost provenance. | 3 | 3 | 9 | Separate resources and exclusive provider/credential-domain/cost-source fields; independent reconciliation. | Observability + FinOps leads | DG-2/DG-3 |
| R-036 | OPS | Quota, throttle, reset timing, model unavailability, or allocation contention differs by arm/order or halts silently. | 2 | 3 | 6 | Arm-balanced order/concurrency, native status capture, provider-time strata, pause rules and stage circuit breakers. | Operations + statistics leads | Before paid stages |

#### Medium-Priority Risks (Score 3–5)

No medium-priority risks.

#### Low-Priority Risks (Score 1–2)

No low-priority risks.

**Categories:** TECH architecture/integration; SEC security; PERF performance; DATA integrity/governance; BUS construct/business; OPS operations/release.

### NFR Testability Requirements

| NFR | Required architecture | Current support | Gap / decision | Planned evidence |
|---|---|---|---|---|
| Security/governance | Subscription-auth task inference; framework-only OpenAI credential; authenticated per-record decisions; categorical blocks | Namespace scoping only | SDK/auth/key boundary, DG-R and safety detector architecture absent | `MODEL-*`, `AUTH-*`, `SECRET-*`, `GOV-*`, `SAFETY-*` |
| Evidence integrity | Immutable assignment/raw→report lineage; 100% primary fields; exclusive provider/cost provenance | Product tests/logs exist, evaluator does not | Registry/schema/provider identity/reconciliation/data lock absent | `TEL-*`, `CATALOG-*`, `REPRO-EVIDENCE` |
| Causal validity | Highest-interference assignment, arm parity, complete ITT | No experiment control plane | Isolation/randomization/attempt rules absent | `ARM-*`, `HIST-*`, `ITT-*`, `STAT-*` |
| Quality/harm | Frozen protected checks and retrieval-action attribution | Deterministic precision fixture only | Endpoint graders/raters absent; margins unratified | `ENDPOINT-QUALITY`, `ENDPOINT-HARM` |
| Performance/cost | Active-time clock; separate Copilot subscription, framework OpenAI metered, and local ledgers | Product telemetry insufficient | Usage/quota clocks, cost-source identity, pricing and invoices absent | `WALL-CONFIRMATORY`, `COST-PROVIDER-LEDGERS`, `COST-*` |
| Reliability | Failure state, quota/rate-limit handling, staged breakers, continuous sentinel, RTO/RPO | Durable spool; one-shot observe | Trial scheduler, continuous capture and thresholds absent | `OPS-QUOTA-RATE-LIMIT`, `OPS-STAGE-ENVELOPE`, `ITT-*`, `RELEASE-CONTINUOUS` |
| Maintainability | Schema catalog/generator/CI, deterministic replay and fallbacks | pytest/CI baseline exists | Catalog and committed-integration validation absent | `CATALOG-*`, `TOOL-*`, `REPRO-*` |

Unknown/unratified: observed catalog eligibility until experiment start; endpoint mode/primary, alpha/weights/power/margins/FDR/sequential policy, paired vs independent assignment, pilot derivation, plausible cells, population/history/scope, safety detector parameters, cost/wall/availability/RTO/RPO, economics, staffing, retry and cross-repository funding. Final NFR verdict belongs to `nfr-assess` after evidence exists.

### Testability Concerns and Architectural Gaps

#### 🚨 Actionable concerns

| Concern | Impact | Architecture must provide | Owner | Gate |
|---|---|---|---|---|
| No model-catalog/runtime lock | Invented/substituted model or alternate inference path invalidates arms | Native catalog/capability snapshot, deterministic pinning, subscription-auth and no-BYOK assertions | Platform/Security | DG-1–DG-4 |
| No framework credential boundary | OpenAI key leakage or silent borrow-host fallback confounds treatment and exposes secrets | Minimal child environment, canary scans and fail-closed concrete-client/model/endpoint preflight | Platform/Security | DG-2/DG-3 |
| No provider/cost provenance contract | Copilot and framework usage can be merged or mispriced | Separate OTel resources, exclusive provider/cost-source fields and independently reconciled ledgers | Observability/FinOps | DG-2/DG-7 |
| No quota/stage control architecture | Differential throttling or cap drift biases arms and spend | Arm-balanced scheduling, quota/reset evidence and 32/128/512/≤192/24 circuit breakers | Operations/Statistics | DG-3/DG-7 |
| No immutable catalog/traceability automation | Scenarios and mappings can drift | Schema, catalog, generator, migration validator and CI byte comparison | TEA/Data/Release | DG-1/DG-2 |
| No assignment/evidence control plane | Causal and audit claims invalid | Immutable state machine, assignment escrow, expected records and data lock | Platform/Data | DG-2/DG-3 |
| No exact analysis implementation | Endpoint decisions can mismatch design | Mode graph, estimator, clustering/correction, compatible intervals and simulation | Statistics | DG-6 |
| No frozen ITT replacement enforcement | Favorable retries can bias outcomes | Exposure certification and linked-attempt validator | Platform/Data | DG-3 |
| No safety measurement architecture | “No event” can be falsely called safe | Exposure inventory, ascertainment, injections, sensitivity and bound calculator | Security/Statistics | DG-3 |
| Cross-repository trust boundary incomplete | Disclosure, deputy and TOCTOU risk | Identity/delegation/policy/cache/tombstone/migration/deletion/restore controls | Security/Privacy | DG-R |
| Release claim and pipeline mismatch | Publish can proceed on overclaim | Bounded docs wording and publish dependency | Release | DG-10 |
| Continuous observation absent | Product claim exceeds implementation | Multi-session watcher plus scheduled sentinel | Product | DG-3/DG-10 |

#### Architectural improvements required

1. Separate pilot readiness (DG-4), pilot completion (DG-5), registration (DG-6) and confirmation (DG-7); no circular completion prerequisite.
2. Make pre-outcome blinded/escrow retrieval shape the only source for MI/YL envelopes; arm guesses remain diagnostic and leakage uses adverse bounds.
3. Freeze `EP-COST` per assigned run and `EP-WALL` active-agent clock; never substitute per-success or fully loaded views.
4. Restore backups into quarantine and apply current policy/tombstones before indexing or readability.
5. Keep committed-integration validation, bounded repair, active-path conformance and temporary fallback expiry/migration evidence separate.

### Risk Mitigation Plans (High-Priority Risks)

| Risk | Strategy | Owner | Timeline | Status | Verification |
|---|---|---|---|---|---|
| R-001 | Independent rubric, shortcut audit, oracle pilot, natural-task replication. | Task curation lead | Before pilot | Planned/BLOCKED until evidence | `PILOT-READINESS`, `PILOT-COMPLETION`, `MEM-VALID`, `TASK-NECESSITY`, `TASK-SHORTCUT`, `TASK-NATURAL`, `ARM-OR` |
| R-002 | Regime-specific whole-history assignment, immutable replay, ITT, no crossover. | Statistical lead | Protocol lock | Planned/BLOCKED until evidence | `ARM-PARITY`, `HIST-CARRYOVER`, `HIST-POSTTX`, `STAT-ESTIMATOR-SEQUENCE`, `BLOCK-CAUSAL`, `SCOPE-P`, `HIST-IND`, `HIST-REPLAY`, `HIST-DYNAMIC` |
| R-003 | Materialize parent IDs; analyze and resample at independently assigned units. | Statistical lead | Protocol lock | Planned/BLOCKED until evidence | `HIST-POSTTX`, `STAT-ESTIMATOR-SESSION`, `STAT-ESTIMATOR-SEQUENCE`, `STAT-CLUSTER-CORRECTION`, `STAT-POWER-SIM`, `BLOCK-CAUSAL`, `HIST-IND`, `HIST-DYNAMIC` |
| R-004 | Frozen nested content and assignment at highest interference level. | Evaluation lead | Before pilot | Planned/BLOCKED until evidence | `BLOCK-CAUSAL`, `ARM-N0`, `ARM-TR`, `SCOPE-S`, `SCOPE-P`, `MEM-VALID`, `SCOPE-A` |
| R-005 | Isolation, per-record authorization, canaries, denial scenarios, incident kill switch. One confirmed event blocks. | Security lead | Before any real data | Planned/BLOCKED until evidence | `SCOPE-R-DENY`, `MEM-POISON`, `GRADE-TAMPER`, `GOV-XREPO-RECORD`, `GOV-CALLER-AUTH`, `GOV-REVOKE`, `BLOCK-SECURITY`, `SCOPE-S`, `SCOPE-A`, `SCOPE-R-AUTH` |
| R-006 | Keep `R` disabled until provenance, per-read policy, revocation, migration, and deletion conformance pass. | Privacy/legal lead | Separate program | Planned/BLOCKED until evidence | `SCOPE-R-DENY`, `GOV-FIELD-REGISTRY`, `GOV-XREPO-RECORD`, `GOV-MIGRATE`, `GOV-EXISTING-GRAPH`, `BLOCK-GOVERNANCE`, `SCOPE-R-AUTH` |
| R-007 | Field registry, deletion bundle, tombstones, rebuild, receipts, negative retrieval, independent sign-off. | Privacy engineering lead | Before governed trial | Planned/BLOCKED until evidence | `GOV-FIELD-REGISTRY`, `GOV-AUTH-CACHE`, `GOV-WITHDRAW`, `GOV-REVOKE`, `GOV-BACKUP-RESTORE-REVOKED`, `GOV-MIGRATE`, `GOV-DELETE-GRAPH`, `GOV-DELETE-DERIVED`, `GOV-BACKUP-EXPIRY`, `GOV-KEY-DESTRUCTION`, `GOV-VIEWER-PURGE`, `GOV-DOWNSTREAM-RECEIPT` (+6 more in QA design source) |
| R-008 | Append-only manifests, signatures/hashes, expected-artifact reconciliation, 100% primary fields. | Data/observability lead | Stdlib baseline | Planned/BLOCKED until evidence | `ITT-MISSING-PRIMARY`, `TEL-PRIMARY`, `TEL-RETRIEVAL`, `TEL-WRITE`, `TEL-EXECUTABLE`, `TEL-FAILURE`, `GOV-FIELD-REGISTRY`, `REPRO-ANALYSIS`, `REPRO-EVIDENCE`, `CATALOG-HASHES`, `BLOCK-EVIDENCE`, `OPS-PROTOCOL-FREEZE` (+2 more in QA design source) |
| R-009 | Expected-record matrix and fault injection; raw JSONL fallback; missingness bounds. | Data/observability lead | Spike/L0 | Planned/BLOCKED until evidence | `TEL-PRIMARY`, `TEL-RETRIEVAL`, `TEL-WRITE`, `TEL-FAILURE`, `TEL-DROP`, `TEL-DUPLICATE`, `TEL-OUT-OF-ORDER`, `BLOCK-EVIDENCE`, `TEL-MODEL`, `TEL-TOOL-SUCCESS`, `TEL-TOOL-ERROR`, `TEL-TOOL-ZERO` (+3 more in QA design source) |
| R-010 | Frozen grader contract, baseline/gold repeats, tamper hashes, drift quarantine. | Benchmark lead | Before enrollment | Planned/BLOCKED until evidence | `GRADE-BASELINE-STABILITY`, `GRADE-GOLD-STABILITY`, `GRADE-DEPENDENCY`, `GRADE-NETWORK-ALLOWED`, `GRADE-NETWORK-FORBIDDEN`, `GRADE-IMAGE-PRESTART`, `GRADE-IMAGE-POSTSTART`, `GRADE-CRASH`, `GRADE-TAMPER`, `GRADE-PATCH-SCOPE`, `ITT-AGENT-FAILURE`, `ITT-GRADER-FAILURE` (+4 more in QA design source) |
| R-011 | Cutoff provenance, duplicate/semantic/canary checks, isolated holdouts. | Contamination lead | Before enrollment | Planned/BLOCKED until evidence | `CONTAM-CANARY`, `CONTAM-HOLDOUT`, `OPS-PROTOCOL-FREEZE`, `TASK-SHORTCUT`, `TASK-NATURAL`, `CONTAM-CUTOFF`, `CONTAM-DUP` |
| R-012 | Deterministic transform, leak classifier/human guesses, calibration, double labels, sensitivity analyses. | Adjudication lead | Pilot | Planned/BLOCKED until evidence | `BLIND-TRANSFORM`, `BLIND-GUESS`, `BLIND-LEAK-CLASSIFIER`, `BLIND-CALIBRATE`, `BLIND-SENSITIVITY`, `BLIND-LEAKAGE-BOUND`, `TOOL-VIEWER-EXPORT` |
| R-013 | Register exactly one 3/5/5 mode family, closed/graphical weighted-Bonferroni procedure, compatible simultaneous bounds, named BH families, and fixed-N default or simulated spending. | Statistical lead | Registration | Planned/BLOCKED until evidence | `STAT-MODE-RELIABILITY`, `STAT-MODE-EFFICIENCY`, `STAT-MODE-DUAL`, `STAT-MULTIPLICITY`, `STAT-INTERVAL-COMPAT`, `STAT-FIXED-LOOK`, `OPS-PROTOCOL-FREEZE`, `BLIND-SENSITIVITY`, `BLIND-LEAKAGE-BOUND`, `STAT-BH`, `STAT-SEQUENTIAL` |
| R-014 | ≥10,000 simulations/cell using blinded nuisance inputs and exact estimator/assignment. | Statistical lead | After pilot | Planned/BLOCKED until evidence | `PILOT-READINESS`, `PILOT-COMPLETION`, `STAT-POWER-SIM`, `STAT-SEQUENTIAL` |
| R-015 | ITT including zero-cost failures, arm parity, three cost estimands, bounds. | FinOps + statistical leads | Pilot/analysis | Planned/BLOCKED until evidence | `ARM-PARITY`, `ITT-TIMEOUT`, `ARM-YL`, `TEL-MODEL`, `TEL-MISSINGNESS`, `COST-MARGINAL`, `WALL-CONFIRMATORY`, `ARM-WO` |
| R-016 | Reconcile Copilot subscription quantities/sensitivities, framework OpenAI metered spend and local resources independently; combine only labelled components. | FinOps + data leads | Before economic analysis | Planned/BLOCKED until evidence | `COST-PROVIDER-LEDGERS`, `COST-MARGINAL`, `COST-FULLY-LOADED`, `COST-STUDY`, `COST-LATE-INVOICE` |
| R-017 | Validate pinned committed integrations, repair within time boxes, then activate expiry-controlled temporary fallbacks without changing canonical contracts. | Platform lead | Integration spike | Planned/BLOCKED until evidence | `GOV-VIEWER-PURGE`, `TOOL-CAS-DELETE`, `TOOL-INSPECT-EVAL`, `TOOL-INSPECT-CONFORMANCE`, `TOOL-OTEL-EVAL`, `TOOL-OTEL-CONFORMANCE`, `TOOL-OPENINFERENCE-EVAL`, `TOOL-OPENINFERENCE-CONFORMANCE`, `TOOL-PARQUET-EVAL`, `TOOL-PARQUET-CONFORMANCE`, `TOOL-CAS-REBUILD`, `TOOL-VIEWER-EXPORT` (+1 more in QA design source) |
| R-018 | Apply separate analysis, grader, evidence replay, stochastic, and independent replication contracts. | Reproducibility lead | All phases | Planned/BLOCKED until evidence | `REPRO-ANALYSIS`, `REPRO-GRADER`, `REPRO-EVIDENCE`, `REPRO-STOCHASTIC`, `REPRO-FAITHFUL-NULL`, `REPRO-FAITHFUL-HARM`, `REPRO-FAITHFUL-INDETERMINATE`, `REPRO-FAITHFUL-POSITIVE`, `REPRO-REPEATED-POSITIVE` |
| R-019 | Pin/record versions, stratify batches, trigger dependent reruns, bound claim population. | Operations lead | Trial release | Planned/BLOCKED until evidence | `OPS-PROTOCOL-FREEZE`, `REPRO-STOCHASTIC` |
| R-020 | Independent certification, injection review, no hidden solution, frozen envelopes/yokes. | Evaluation lead | Before pilot | Planned/BLOCKED until evidence | `ARM-MI-ENVELOPE`, `ARM-PARITY`, `ARM-E0`, `ARM-YL`, `ARM-MI`, `MEM-IRRELEVANT`, `ARM-OR`, `ARM-AI` |
| R-021 | Explicit release wording plus separate continuous-observation implementation and sentinel. | Product + release leads | Immediate | Planned/BLOCKED until evidence | `EV-ROUNDTRIP-MCP`, `RELEASE-CONTINUOUS` |
| R-022 | Rename evidence to “deterministic pipeline wiring fixture passed”; gate higher claims separately. | Release lead | Immediate | Planned/BLOCKED until evidence | `EV-FIXTURE-RETRIEVAL`, `EV-ROUNDTRIP-MCP`, `RELEASE-DOC-WORDING` |
| R-023 | Workstream ranges, dependency map, owners, budget, and 15–40 week baseline critical path plus external waits. | Program lead | Before commitment | Planned/BLOCKED until evidence | `COST-FULLY-LOADED`, `COST-STUDY`, `REPRO-FAITHFUL-NULL`, `REPRO-FAITHFUL-HARM`, `REPRO-FAITHFUL-INDETERMINATE`, `REPRO-FAITHFUL-POSITIVE`, `REPRO-REPEATED-POSITIVE`, `OPS-STAFFING` |
| R-024 | Atomic attack scenarios, provenance/validity checks, executable grading, zero-tolerance high-severity path. | Security/red-team lead | L0/L1 | Planned/BLOCKED until evidence | `MEM-STALE`, `MEM-SUPERSEDED`, `MEM-CONTRADICTORY`, `MEM-POISON`, `ENDPOINT-HARM`, `BLOCK-SECURITY`, `ARM-MI`, `MEM-IRRELEVANT` |
| R-025 | Exact randomization inference; equal-task/equal-sequence weights; assignment-unit clustering; CR2/wild-bootstrap sensitivity; compatible simultaneous intervals. | Statistical lead | Protocol lock/registration | Planned/BLOCKED until evidence | `ARM-MI-ENVELOPE`, `STAT-MODE-RELIABILITY`, `STAT-MODE-EFFICIENCY`, `STAT-MODE-DUAL`, `ENDPOINT-QUALITY`, `ENDPOINT-HARM`, `STAT-MULTIPLICITY`, `STAT-ESTIMATOR-SESSION`, `STAT-ESTIMATOR-SEQUENCE`, `STAT-CLUSTER-CORRECTION`, `STAT-INTERVAL-COMPAT`, `STAT-POWER-SIM` (+1 more in QA design source) |
| R-026 | Freeze terminal outcomes; permit at most one arm-blind pre-exposure infrastructure replacement; retain lineage; prohibit post-exposure and best-of-N substitution. | Evaluation + data leads | Protocol lock | Planned/BLOCKED until evidence | `GRADE-IMAGE-PRESTART`, `GRADE-IMAGE-POSTSTART`, `ITT-AGENT-FAILURE`, `ITT-TIMEOUT`, `ITT-GRADER-FAILURE`, `ITT-INFRA-PRE`, `ITT-INFRA-POST`, `ITT-MISSING-PRIMARY`, `ITT-NO-FAVORABLE-SUB` |
| R-027 | Gate-specific exposure denominators, coverage and injected-positive sensitivity intervals, adjusted exact upper bounds, and bounded language. | Safety lead | Before pilot | Planned/BLOCKED until evidence | `SAFETY-EXPOSURE`, `SAFETY-ASCERTAINMENT`, `SAFETY-SENSITIVITY`, `SAFETY-UPPER-BOUND`, `SAFETY-LANGUAGE` |
| R-028 | One schema-validated immutable JSON catalog; deterministic generated mappings; fixture/evidence hashes; CI byte-identity and referential-closure checks. | TEA + data leads | Before harness implementation | Planned/BLOCKED until evidence | `PILOT-READINESS`, `CATALOG-SCHEMA`, `CATALOG-HASHES`, `CATALOG-MAPPINGS`, `CATALOG-CI` |
| R-029 | Cryptographic caller authentication, principal-role-group binding, purpose-bound delegation, read/render recheck, cache invalidation, revocation and migration scenarios. | Security + privacy leads | DG-R separate program | Planned/BLOCKED until evidence | `GOV-CALLER-AUTH`, `GOV-PRINCIPAL-BIND`, `GOV-CONFUSED-DEPUTY`, `GOV-AUTH-CACHE`, `GOV-POLICY-TOCTOU` |
| R-030 | Restore into quarantine; apply current tombstones, authorization and policy version before indexing/rendering; negative retrieval and audit evidence. | Privacy + operations leads | DG-R separate program | Planned/BLOCKED until evidence | `GOV-BACKUP-RESTORE-REVOKED` |
| R-031 | Correct `docs/release-gate.md`; make `release.yml` invoke/depend on the roundtrip before publish; keep both as explicit unresolved implementation blockers until repository changes exist. | Release lead | Immediate | Planned/BLOCKED until evidence | `SAFETY-LANGUAGE`, `EV-FIXTURE-RETRIEVAL`, `EV-ROUNDTRIP-MCP`, `RELEASE-DOC-WORDING`, `RELEASE-CI-ENFORCEMENT` |
| R-032 | Capture/hash native model catalog and capabilities; apply deterministic arm-blind M0/M1/M2 selection; pause/version on unavailability. | Platform + evaluation leads | Experiment start | Planned/BLOCKED until evidence | `MODEL-CATALOG-SNAPSHOT`, `MODEL-SELECTION-PIN`, `MODEL-UNAVAILABLE-PAUSE`, `ARM-PARITY`, `PILOT-READINESS` |
| R-033 | Prove current-subscription SDK auth; reject BYOK/custom task providers; audit zero Inspect provider calls. | Platform + security leads | DG-2/DG-3 | Planned/BLOCKED until evidence | `AUTH-COPILOT-SUBSCRIPTION`, `AUTH-SDK-BYOK-DENY`, `FRAMEWORK-ROUTE-FAIL-CLOSED`, `ARM-PARITY` |
| R-034 | Inject OpenAI key only into framework process; minimize agent environment; scan prohibited surfaces; fail closed. | Security + platform leads | DG-2/DG-3 | Planned/BLOCKED until evidence | `SECRET-OPENAI-ISOLATION`, `FRAMEWORK-ROUTE-FAIL-CLOSED`, `ARM-PARITY`, `BLOCK-SECURITY` |
| R-035 | Use separate telemetry resources and exclusive provider/credential/cost fields; reconcile both ledgers. | Observability + FinOps leads | DG-2/DG-7 | Planned/BLOCKED until evidence | `TEL-PROVIDER-IDENTITY`, `COST-PROVIDER-LEDGERS`, `TEL-PRIMARY`, `BLOCK-EVIDENCE` |
| R-036 | Balance order/concurrency, record throttle/quota/reset state, preserve ITT, pause unavailable blocks and enforce stage caps. | Operations + statistics leads | Before paid stages | Planned/BLOCKED until evidence | `OPS-QUOTA-RATE-LIMIT`, `OPS-STAGE-ENVELOPE`, `ARM-PARITY`, `TEL-MODEL`, `ITT-TIMEOUT` |

### Testability Assessment Summary

#### What Works Well

- Headless MCP/daemon protocol, backend registry and LLM/embedder injection support controlled tests.
- Durable spool, stable wire/episode seams and structured search results support deterministic evidence capture.
- Existing `tests/eval` and roundtrip tests provide useful but bounded, separate regression evidence.
- CI spans Python 3.11–3.13 and Windows/Linux; release builds verify clean-wheel installation.

#### Accepted trade-offs

- Embedded Ladybug remains production default; cloud backend evidence stays wiring-only.
- Python stdlib/pytest/JSONL/static HTML remain valid temporary fallbacks with expiry and migration tests.
- Sequential evaluator execution is acceptable until isolation conformance passes.

### Assumptions and Dependencies

1. Numerical defaults are sensitivity seeds, not approvals; changing a ratified parameter versions the protocol.
2. One person-week is 40 hours. Workstream effort totals 44–117 person-weeks (1,760–4,680 hours), including replication; baseline elapsed critical path is about 15–40 weeks, plus UNKNOWN external approval/provider/legal/quota/reviewer waits.
3. Each workstream has one or two contributors, no double-booking, and concurrent execution only after dependencies permit.
4. Implementation needs the signed-in Copilot subscription identity, a framework-only OpenAI credential, holdout access, legal basis/regions, immutable images/mirrors, compute and invoices, independent curators/raters/analysts/replicators and security review.
5. Cross-repository and release claims remain prohibited while their blockers are open.

---

**End of Architecture Document**

**Architecture next action:** assign owners and resolve implementation/owner gates.  
**QA companion:** `_bmad-output/test-artifacts/test-design-qa.md`.
