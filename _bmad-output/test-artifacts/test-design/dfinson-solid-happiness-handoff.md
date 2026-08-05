---
title: 'TEA Test Design → BMAD Handoff Document'
version: '1.0'
workflowType: 'testarch-test-design-handoff'
inputDocuments:
  - '_bmad-output/test-artifacts/test-design-architecture.md'
  - '_bmad-output/test-artifacts/test-design-qa.md'
  - '_bmad-output/test-artifacts/test-design-progress.md'
  - '_bmad-output/planning-artifacts/research/technical-evaluating-memrelay-collective-recall-for-coding-agent-reliability-cost-and-efficiency-research-2026-08-04.md'
sourceWorkflow: 'testarch-test-design'
generatedBy: 'TEA Master Test Architect'
generatedAt: '2026-08-05T09:03:41.511-04:00'
projectName: 'dfinson-solid-happiness'
---

# TEA → BMAD Integration Handoff

## Purpose

Translate the System-Level TEA design into epics and atomic stories without treating design coverage as implementation completion. The QA document owns the sole 161-row Markdown scenario design source; this handoff references its generated design-time mappings and must not become a second catalog.

## TEA Artifacts Inventory

| Artifact | Path | BMAD integration point |
|---|---|---|
| Architecture contract | `_bmad-output/test-artifacts/test-design-architecture.md` | ASRs, testability blockers, risks and NFR requirements |
| QA design/source | `_bmad-output/test-artifacts/test-design-qa.md` | Sole Markdown scenario design source and generated risk/requirement projections |
| Workflow progress | `_bmad-output/test-artifacts/test-design-progress.md` | Mode, inputs, counts, checklist and unresolved gates |
| Latest research | `_bmad-output/planning-artifacts/research/technical-evaluating-memrelay-collective-recall-for-coding-agent-reliability-cost-and-efficiency-research-2026-08-04.md` | Normative runtime, endpoint, analysis, safety, catalog and integration contracts |

**Critical status:** no schema-validated executable catalog, fixture/expected-evidence hash inventory, traceability generator or CI validator exists. Stories must create them before evaluator/harness work. Design mappings are not execution evidence.

## Epic-Level Integration Guidance

### Recommended epics

1. **Runtime, owner protocol and governance lock:** native Copilot SDK catalog/capabilities, deterministic M0/M1/M2 pinning, subscription-auth boundary, framework OpenAI credential/model/endpoint, then endpoints, estimator, thresholds, population, economics, operations and authorization parameters.
2. **Catalog, credential and evidence baseline:** model and scenario catalogs; JSON Schema 2020-12; hashes/mappings; minimal agent environment; fail-closed framework route; separate provider spans and cost ledgers; JSONL manifests and CI divergence validation.
3. **Runner, arms and graders:** isolated workers, ordinary MCP treatment path, pre-outcome MI/YL envelopes, frozen graders, complete ITT attempt lineage and no favorable substitution.
4. **Security/governance:** caller authentication, principal/role/group/purpose binding, confused-deputy and TOCTOU defenses, cache invalidation, revocation, migration, deletion and quarantined restore-before-expiry.
5. **Statistics and pilot:** quality/harm endpoints, 3/5/5 families, exact inference, clustering/correction/compatible intervals, DG-4 readiness, DG-5 completion, enumerable simulation and DG-6 registration.
6. **Committed-stack integration:** validate/repair Inspect/custom Copilot SDK solver, local OTel, OpenInference, Parquet/DuckDB and local CAS; bounded temporary fallbacks carry expiry and migration tests; generated local HTML is the baseline viewer.
7. **Trials and replication:** bounded session, replay/dynamic longitudinal and faithful null/harm/indeterminate/positive replication; repeated positive evidence for broader efficacy.
8. **Operations and release:** safety exposure/bounds, continuous sentinel, bounded evidence claims, documentation correction and mechanical `release.yml` publish dependency.

### Risk references

- Risk register: 36 total; all 36 score ≥6; 17 score-9.
- Security, governance, evidence-integrity, grading and causal-validity violations are categorical. One confirmed event blocks; no P1 aggregate authorizes them.
- Cross-repository work remains prohibited until DG-R evidence exists.

### Quality gates

- Every applicable P0 and gate-dependent P1 must produce expected evidence against one immutable catalog version/hash; informational P1 ≥95% cannot override a failed dependency.
- 100% assignment/outcome/lineage primary fields and raw-to-report reconciliation.
- Every committed integration validates or enters bounded repair before an expiry-controlled temporary fallback; both primary and fallback paths satisfy canonical contracts.
- Enforce the staged envelope: 32 integration runs, 128-unit pilot, 512-unit expanded primary, at most 192 secondary units, and 24 later cross-repository clusters only after DG-R.
- Release text must map to population, estimand, history, scope, endpoint/evidence/claim and passed gate.

## Story-Level Integration Guidance

### P0/P1 scenarios → acceptance criteria

Stories must copy atomic rows from the QA design source by stable ID and preserve exactly one injected condition/procedure/objective verdict. At minimum:

- `MODEL-*`, `AUTH-*`, `SECRET-*`, `FRAMEWORK-*`: catalog snapshot/pinning, subscription auth, no SDK BYOK, framework-only OpenAI key and fail-closed routing.
- `CATALOG-*`: schema, canonical hash, fixture/evidence hashes, generated mappings and CI no-divergence.
- `TEL-PROVIDER-IDENTITY`, `COST-PROVIDER-LEDGERS`, `OPS-QUOTA-RATE-LIMIT`, `OPS-STAGE-ENVELOPE`, `ARM-PARITY`: provider identity, separated costs, quotas, staged caps and identical arms.
- `STAT-MODE-*`, `STAT-ESTIMATOR-*`, `STAT-CLUSTER-CORRECTION`, `STAT-INTERVAL-COMPAT`: exact endpoint/analysis contract.
- `ITT-*`: one terminal condition per story and no favorable outcome replacement.
- `PILOT-READINESS` and `PILOT-COMPLETION`: distinct evidence and transition gates.
- `SAFETY-*`: exposures, ascertainment, injected sensitivity, adjusted upper bound and bounded language.
- `GOV-CALLER-AUTH`, `GOV-PRINCIPAL-BIND`, `GOV-CONFUSED-DEPUTY`, `GOV-AUTH-CACHE`, `GOV-POLICY-TOCTOU`, `GOV-BACKUP-RESTORE-REVOKED`.
- `EV-FIXTURE-RETRIEVAL` and `EV-ROUNDTRIP-MCP`: separate IDs and claims.
- `RELEASE-DOC-WORDING` and `RELEASE-CI-ENFORCEMENT`: explicit open blockers, not completed stories.
- `REPRO-FAITHFUL-*` and `REPRO-REPEATED-POSITIVE`: distinguish replication of any conclusion from broad efficacy.

### Machine-readable catalog acceptance criteria

The implementation story is not complete until the native SDK catalog and capability projection are hashed, deterministic M0/M1/M2 selection reproduces, and UTF-8 scenario JSON validates against a pinned JSON Schema draft 2020-12 and records `schema_version`, monotonic `catalog_version`, stable IDs and RFC 8785 `content_sha256`; every fixture/evidence digest resolves; generated mappings are referentially closed and byte-identical on regeneration; breaking/additive/content changes follow major/minor/patch policy; release CI rejects divergence across architecture, QA, handoff and progress projections. Do not hand-author mapping rows.

### Future UI identifiers

N/A. The current product/evaluator is backend/headless. Do not create UI `data-testid` requirements. The Playwright TypeScript sample in QA Appendix A is future-only and not readiness evidence.

## Risk-to-Story Mapping

| Risk | Cat. | P×I | Recommended epic/story | Scenario source | Level |
|---|---|---:|---|---|---|
| R-001 | BUS | 9 | Corpus, graders, contamination and blinding | `PILOT-READINESS`, `PILOT-COMPLETION`, `MEM-VALID`, `TASK-NECESSITY`, `TASK-SHORTCUT` (+2) | Component, Analysis, E2E |
| R-002 | DATA | 9 | Protocol, assignment, statistics and ITT | `ARM-PARITY`, `HIST-CARRYOVER`, `HIST-POSTTX`, `STAT-ESTIMATOR-SEQUENCE`, `BLOCK-CAUSAL` (+4) | Component, Unit/Analysis, E2E |
| R-003 | DATA | 9 | Protocol, assignment, statistics and ITT | `HIST-POSTTX`, `STAT-ESTIMATOR-SESSION`, `STAT-ESTIMATOR-SEQUENCE`, `STAT-CLUSTER-CORRECTION`, `STAT-POWER-SIM` (+3) | Unit/Analysis, Analysis, Component |
| R-004 | BUS | 6 | Corpus, graders, contamination and blinding | `BLOCK-CAUSAL`, `ARM-N0`, `ARM-TR`, `SCOPE-S`, `SCOPE-P` (+2) | Component, E2E, API/Integration |
| R-005 | SEC | 6 | Security, governance and cross-repository controls | `SCOPE-R-DENY`, `MEM-POISON`, `GRADE-TAMPER`, `GOV-XREPO-RECORD`, `GOV-CALLER-AUTH` (+5) | E2E, API/Integration, Component |
| R-006 | DATA | 9 | Security, governance and cross-repository controls | `SCOPE-R-DENY`, `GOV-FIELD-REGISTRY`, `GOV-XREPO-RECORD`, `GOV-MIGRATE`, `GOV-EXISTING-GRAPH` (+2) | E2E, Component |
| R-007 | DATA | 9 | Security, governance and cross-repository controls | `GOV-FIELD-REGISTRY`, `GOV-AUTH-CACHE`, `GOV-WITHDRAW`, `GOV-REVOKE`, `GOV-BACKUP-RESTORE-REVOKED` (+13) | Component, E2E, API/Integration |
| R-008 | DATA | 6 | Evidence schema, catalog, reconciliation and reproducibility | `ITT-MISSING-PRIMARY`, `TEL-PRIMARY`, `TEL-RETRIEVAL`, `TEL-WRITE`, `TEL-EXECUTABLE` (+9) | Component, E2E, API/Integration |
| R-009 | OPS | 9 | Evidence schema, catalog, reconciliation and reproducibility | `TEL-PRIMARY`, `TEL-RETRIEVAL`, `TEL-WRITE`, `TEL-FAILURE`, `TEL-DROP` (+10) | E2E, API/Integration, Component |
| R-010 | BUS | 6 | Corpus, graders, contamination and blinding | `GRADE-BASELINE-STABILITY`, `GRADE-GOLD-STABILITY`, `GRADE-DEPENDENCY`, `GRADE-NETWORK-ALLOWED`, `GRADE-NETWORK-FORBIDDEN` (+11) | E2E, Component, Unit/Analysis |
| R-011 | DATA | 6 | Corpus, graders, contamination and blinding | `CONTAM-CANARY`, `CONTAM-HOLDOUT`, `OPS-PROTOCOL-FREEZE`, `TASK-SHORTCUT`, `TASK-NATURAL` (+2) | E2E, API/Integration, Component |
| R-012 | BUS | 6 | Program staffing and independent replication | `BLIND-TRANSFORM`, `BLIND-GUESS`, `BLIND-LEAK-CLASSIFIER`, `BLIND-CALIBRATE`, `BLIND-SENSITIVITY` (+2) | Unit/Component, Component, Analysis |
| R-013 | BUS | 6 | Protocol, assignment, statistics and ITT | `STAT-MODE-RELIABILITY`, `STAT-MODE-EFFICIENCY`, `STAT-MODE-DUAL`, `STAT-MULTIPLICITY`, `STAT-INTERVAL-COMPAT` (+6) | Unit/Analysis, API/Integration, Component |
| R-014 | BUS | 9 | Protocol, assignment, statistics and ITT | `PILOT-READINESS`, `PILOT-COMPLETION`, `STAT-POWER-SIM`, `STAT-SEQUENTIAL` | Component, Analysis |
| R-015 | PERF | 6 | Cost, wall-time and FinOps | `ARM-PARITY`, `ITT-TIMEOUT`, `ARM-YL`, `TEL-MODEL`, `TEL-MISSINGNESS` (+3) | Component, E2E, API/Integration |
| R-016 | OPS | 6 | Cost, wall-time and FinOps | `COST-PROVIDER-LEDGERS`, `COST-MARGINAL`, `COST-FULLY-LOADED`, `COST-STUDY`, `COST-LATE-INVOICE` | Analysis, Component/Analysis |
| R-017 | TECH | 6 | Component falsification and fallbacks | `GOV-VIEWER-PURGE`, `TOOL-CAS-DELETE`, `TOOL-INSPECT-EVAL`, `TOOL-INSPECT-CONFORMANCE`, `TOOL-OTEL-EVAL` (+8) | E2E, Component |
| R-018 | TECH | 9 | Evidence schema, catalog, reconciliation and reproducibility | `REPRO-ANALYSIS`, `REPRO-GRADER`, `REPRO-EVIDENCE`, `REPRO-STOCHASTIC`, `REPRO-FAITHFUL-NULL` (+4) | Analysis, E2E, E2E/Analysis |
| R-019 | OPS | 6 | Operations, sentinels and release enforcement | `OPS-PROTOCOL-FREEZE`, `REPRO-STOCHASTIC` | Component, E2E |
| R-020 | BUS | 6 | Program staffing and independent replication | `ARM-MI-ENVELOPE`, `ARM-PARITY`, `ARM-E0`, `ARM-YL`, `ARM-MI` (+3) | Analysis, Component, E2E |
| R-021 | OPS | 9 | Operations, sentinels and release enforcement | `EV-ROUNDTRIP-MCP`, `RELEASE-CONTINUOUS` | E2E |
| R-022 | BUS | 9 | Operations, sentinels and release enforcement | `EV-FIXTURE-RETRIEVAL`, `EV-ROUNDTRIP-MCP`, `RELEASE-DOC-WORDING` | Integration, E2E, Component |
| R-023 | OPS | 6 | Program staffing and independent replication | `COST-FULLY-LOADED`, `COST-STUDY`, `REPRO-FAITHFUL-NULL`, `REPRO-FAITHFUL-HARM`, `REPRO-FAITHFUL-INDETERMINATE` (+3) | Analysis, E2E/Analysis, Component |
| R-024 | SEC | 6 | Security, governance and cross-repository controls | `MEM-STALE`, `MEM-SUPERSEDED`, `MEM-CONTRADICTORY`, `MEM-POISON`, `ENDPOINT-HARM` (+3) | API/Integration, E2E, Analysis |
| R-025 | DATA | 6 | Protocol, assignment, statistics and ITT | `ARM-MI-ENVELOPE`, `STAT-MODE-RELIABILITY`, `STAT-MODE-EFFICIENCY`, `STAT-MODE-DUAL`, `ENDPOINT-QUALITY` (+8) | Analysis, Unit/Analysis |
| R-026 | DATA | 6 | Protocol, assignment, statistics and ITT | `GRADE-IMAGE-PRESTART`, `GRADE-IMAGE-POSTSTART`, `ITT-AGENT-FAILURE`, `ITT-TIMEOUT`, `ITT-GRADER-FAILURE` (+4) | Component, Unit/Analysis |
| R-027 | SEC | 6 | Security, governance and cross-repository controls | `SAFETY-EXPOSURE`, `SAFETY-ASCERTAINMENT`, `SAFETY-SENSITIVITY`, `SAFETY-UPPER-BOUND`, `SAFETY-LANGUAGE` | Unit/Analysis, Component, E2E |
| R-028 | DATA | 9 | Evidence schema, catalog, reconciliation and reproducibility | `PILOT-READINESS`, `CATALOG-SCHEMA`, `CATALOG-HASHES`, `CATALOG-MAPPINGS`, `CATALOG-CI` | Component |
| R-029 | SEC | 9 | Security, governance and cross-repository controls | `GOV-CALLER-AUTH`, `GOV-PRINCIPAL-BIND`, `GOV-CONFUSED-DEPUTY`, `GOV-AUTH-CACHE`, `GOV-POLICY-TOCTOU` | API/Integration, E2E |
| R-030 | DATA | 6 | Security, governance and cross-repository controls | `GOV-BACKUP-RESTORE-REVOKED` | E2E |
| R-031 | OPS | 9 | Operations, sentinels and release enforcement | `SAFETY-LANGUAGE`, `EV-FIXTURE-RETRIEVAL`, `EV-ROUNDTRIP-MCP`, `RELEASE-DOC-WORDING`, `RELEASE-CI-ENFORCEMENT` | Component, Integration, E2E |
| R-032 | TECH | 9 | Runtime/model catalog and pinning | `MODEL-CATALOG-SNAPSHOT`, `MODEL-SELECTION-PIN`, `MODEL-UNAVAILABLE-PAUSE`, `ARM-PARITY`, `PILOT-READINESS` | API/Integration, Unit/Analysis, Component |
| R-033 | SEC | 9 | Subscription-auth task-agent boundary | `AUTH-COPILOT-SUBSCRIPTION`, `AUTH-SDK-BYOK-DENY`, `FRAMEWORK-ROUTE-FAIL-CLOSED`, `ARM-PARITY` | API/Integration, Component |
| R-034 | SEC | 9 | Framework OpenAI credential isolation | `SECRET-OPENAI-ISOLATION`, `FRAMEWORK-ROUTE-FAIL-CLOSED`, `ARM-PARITY`, `BLOCK-SECURITY` | E2E, API/Integration, Component |
| R-035 | DATA | 9 | Provider telemetry and cost provenance | `TEL-PROVIDER-IDENTITY`, `COST-PROVIDER-LEDGERS`, `TEL-PRIMARY`, `BLOCK-EVIDENCE` | Component, Analysis, E2E |
| R-036 | OPS | 6 | Quota/rate-limit and staged envelope | `OPS-QUOTA-RATE-LIMIT`, `OPS-STAGE-ENVELOPE`, `ARM-PARITY`, `TEL-MODEL`, `ITT-TIMEOUT` | E2E, Component, API/Integration |

The full risk-to-scenario and requirement-to-scenario projections are generated from the QA Markdown design source. Repository generation automation remains required work; this document does not claim the executable mapping ran.

## Owner-Only Decision Stories

| Parameter | Options | Consequence |
|---|---|---|
| Endpoint mode | reliability 3 / efficiency 5 / dual 5 | Changes primary, guards, multiplicity and N |
| Efficiency primary/component | cost / wall / intersection | Intersection is stricter and typically increases N |
| Statistical policy | alpha, weights, power, FDR, fixed vs 50/75/100 sequential | Changes bounds, simulation and budget |
| Assignment/pilot | independent blocked vs paired; baseline-only vs escrow | Estimator and grid branch must match; no silent default |
| Plausible simulation cells | owner-named finite allowlist | Broader set increases worst-case N; excluded cells remain stress tests |
| Population/history/scope | product/model/tasks; replay/dynamic; session/cross-session | Bounds claim scope; cross-agent requires promotion; cross-repo remains blocked |
| Economics | currency/region/tax/discount/price/FX/volume/amortization/value/budget | Versions cost analysis and decision threshold |
| Operations | retry 0/1, envelope tolerances/substitution, telemetry ceiling, availability/RTO/RPO, staffing | Changes eligibility, missingness and elapsed plan |
| Governance | real/private permission, legal basis, consent, region, retention/deletion, managed services | Determines eligible evidence; no approval is implied |
| Committed integrations | validate / bounded repair / temporary fallback | Fallback requires expiry, migration test and canonical-contract parity |

## Staffing and External Dependencies

One person-week = 40 hours. Program effort is 44–117 person-weeks (1,760–4,680 hours), including 4–12 person-weeks replication; pre-replication subtotal is 40–105 person-weeks. Includes design, implementation, review, tests, docs and rework; excludes external waits, compute queues and unrelated meetings. One or two contributors per concurrent workstream, no double-booking. Baseline critical path is about 15–40 calendar weeks; external approval/provider/legal/quota/reviewer waits remain additional UNKNOWN delays. These three-point ranges reflect adapter parity, task yield, pilot-derived N and access uncertainty.

## Recommended BMAD → TEA Workflow Sequence

1. Create epics/stories from this handoff, keeping owner parameters open.
2. Implement catalog/schema/generator/CI and stdlib evidence baseline.
3. Validate and repair each committed integration; activate its expiry-controlled temporary fallback only after the time box.
4. Run TEA ATDD explicitly for atomic P0/P1 stories; implementation follows failing tests.
5. Complete L0/L1 security/governance/grading/evidence conformance.
6. Pass DG-4 readiness, execute blinded pilot, then pass DG-5 completion.
7. Simulate/register at DG-6; run confirmation/longitudinal/replication only after approval.
8. Run TEA Trace and `nfr-assess` with implementation evidence.

## Phase Transition Quality Gates

| From | To | Gate criteria |
|---|---|---|
| Design | Epic/story creation | Risks, open owner parameters, ASRs and 161-row QA design source referenced; no implementation PASS claim |
| Epic/story | Catalog/evidence baseline | `CATALOG-*` stories accepted; schema/version/hash/fallback requirements preserved |
| Baseline | Committed-stack validation | Canonical fallback passes; each pinned integration validates or enters bounded repair |
| Committed-stack validation | L0/L1 conformance | Active primary/fallback paths pass identical canonical contracts and migration/expiry checks |
| L0/L1 | Pilot Readiness (DG-4) | Model/scenario catalog hashes, pinned M0, auth/key boundaries, arm parity, tasks/graders/views, telemetry, ledgers, quota/stage stopping ready |
| Pilot | Confirmation (DG-5) | Pilot complete while blind; nuisance/feasibility outputs locked; no efficacy decoding/tuning |
| DG-5 | Registration (DG-6) | Enumerable simulations, N/allocation, mode family, analysis code and owner choices locked |
| Registration | Primary trial (DG-7) | Every dependency and categorical gate passes |
| Primary | Longitudinal (DG-8) | Session claim bounded; replay/dynamic protocols separate |
| Trial | Replication (DG-9) | Conclusion class preserved; broader efficacy requires repeated positives |
| Evidence | Release (DG-10) | Bounded claim map; docs wording fixed; publish mechanically depends on roundtrip; categorical blockers zero |
| Any phase | Cross-repository (DG-R) | Caller authentication, provenance, revocation, migration, deletion, cache/TOCTOU and backup-restore implementation evidence all pass |

## Open Implementation Blockers

- Executable catalog/schema/generator/mapping/CI: absent.
- Copilot SDK solver/catalog pinning, subscription-auth proof, OpenAI-key isolation, provider-span identity, separated ledgers and quota/stage controls: absent.
- Evaluator assignment/evidence/analysis/safety control plane: absent.
- Continuous observation: absent.
- Release-gate wording correction: absent.
- `release.yml` roundtrip dependency: absent.
- Cross-repository principal authentication and lifecycle controls: absent.

Therefore requirements 1–20 and the revised findings are covered in design, but are **not closed as repository implementation**.
