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
  - '.agents/skills/bmad-testarch-test-design/resources/tea-index.csv'
  - '.agents/skills/bmad-testarch-test-design/resources/knowledge/adr-quality-readiness-checklist.md'
  - '.agents/skills/bmad-testarch-test-design/resources/knowledge/nfr-criteria.md'
  - '.agents/skills/bmad-testarch-test-design/resources/knowledge/test-levels-framework.md'
  - '.agents/skills/bmad-testarch-test-design/resources/knowledge/test-priorities-matrix.md'
  - '.agents/skills/bmad-testarch-test-design/resources/knowledge/risk-governance.md'
  - '.agents/skills/bmad-testarch-test-design/resources/knowledge/probability-impact.md'
  - '.agents/skills/bmad-testarch-test-design/resources/knowledge/test-quality.md'
  - '.agents/skills/bmad-testarch-test-design/resources/knowledge/overview.md'
  - '.agents/skills/bmad-testarch-test-design/resources/knowledge/api-request.md'
  - '.agents/skills/bmad-testarch-test-design/resources/knowledge/auth-session.md'
  - '.agents/skills/bmad-testarch-test-design/resources/knowledge/recurse.md'
---

# Test Design Workflow Progress

## Step 1 — Mode and Prerequisites

- **Mode:** System-Level Mode, explicitly requested.
- **Prerequisites:** `SPEC.md`, `docs/ARCHITECTURE.md`, `docs/adr/0001-graph-backends.md`, and the latest corrected technical research are present.
- **Constraint:** regenerate managed TEA artifacts only. Source, tests, docs, research, `.gitignore`, and the BMAD installation remain read-only.
- **Runtime:** local Python 3.11 with `PYTHONUTF8=1`; `uv` is prohibited.

## Step 2 — Loaded Context

- Loaded the latest 2026-08-05 owner-corrected research, specification, current architecture, accepted graph-backend ADR, TEA configuration, required System-Level knowledge, and the backend API-only Playwright Utils profile.
- Config: backend + pytest + GitHub Actions; Playwright Utils enabled; Pact and browser automation disabled.
- Corrected execution plane: local Python Copilot SDK task agents under subscription auth; Inspect is orchestration only; OpenAI API is framework-internal only; local Collector/CAS/Parquet/DuckDB evidence plane.
- Extracted NFRs and unresolved thresholds for security, credential isolation, telemetry completeness, reliability, quota handling, cost provenance, performance, governance, reproducibility, and release controls.
- No prerequisite is missing. Owner-ratified scientific/economic parameters and implementation evidence remain genuine later gates, not invented defaults.

## Step 3 — Testability and Risk Assessment

- **Testability concerns:** no implemented Copilot SDK catalog lock/solver parity proof, subscription-auth assertion, fail-closed framework credential boundary, provider-identity telemetry contract, separated cost reconciliation, immutable scenario catalog, experiment control plane, or arm-balanced quota scheduler.
- **Strengths:** headless injectable product seams, durable spool, bounded deterministic fixtures, local evidence architecture, executable graders, and explicit fallback contracts.
- **Risk count:** 36 total; all 36 score ≥6; 17 score 9. Existing scientific, statistical, safety, governance, release, and reproducibility risks are preserved.
- Added atomic risks for catalog/model pinning; task-agent inference and subscription-auth boundary; OpenAI-key isolation/fail-closed framework routing; span-provider/cost provenance; and quota/rate-limit induced arm imbalance. Cost-ledger conflation is elevated to high risk.
- NFR thresholds without owner ratification remain **UNKNOWN**. Planned evidence covers security, credential isolation, provider identity, cost provenance, telemetry completeness, quota/reliability, arm parity, performance, governance, reproducibility, and release controls.
- Categorical security, governance, evidence-integrity, grading, and causal-validity failures remain zero tolerance. Final NFR verdict remains deferred to `nfr-assess`.

## Step 4 — Coverage and Execution Plan

- **Atomic Markdown design source:** 161 rows: 104 P0 blockers, 52 P1 confirmatory, 5 P2 exploratory, 0 P3.
- Added atomic scenarios for catalog snapshot, deterministic model selection/pinning, frozen-model unavailability, Copilot subscription authentication, SDK BYOK prohibition, OpenAI-key isolation, fail-closed framework routing, telemetry provider identity, provider-ledger reconciliation, quota/rate-limit handling, and staged-envelope enforcement.
- Corrected `ARM-PARITY`, `TEL-MODEL`, `COST-MARGINAL`, pilot gates, execution stages, staffing dependencies, and requirement/risk projections to use the corrected runtime.
- The Markdown table remains a design source, not an executable catalog. JSON Schema/catalog/version/hash/fixture/evidence hashes/generated mappings/CI remain required implementation work and are BLOCKED/TODO.
- Execution remains PR / nightly / weekly. Paid stages are catalog/conformance, 32 integration runs, 128-unit pilot, 512-unit expanded primary, at most 192 secondary units, and 24 later cross-repository clusters after DG-R.
- Quality gates: 100% P0, ≥95% P1 only after every categorical blocker passes; no aggregate percentage can waive security, governance, evidence-integrity, grading, or causal-validity failure.
- Effort is 44–117 person-weeks (1,760–4,680 hours) including replication, with about 15–40 elapsed critical-path weeks plus UNKNOWN external waits.

## Step 5 — Outputs and Validation

- **Execution mode:** Sequential. Config requested `auto`; nested delegation is unavailable to this sub-agent and product isolation is not proven.
- **Architecture:** `_bmad-output/test-artifacts/test-design-architecture.md`
- **QA/atomic design source:** `_bmad-output/test-artifacts/test-design-qa.md`
- **Handoff:** `_bmad-output/test-artifacts/test-design/dfinson-solid-happiness-handoff.md`
- **Progress:** `_bmad-output/test-artifacts/test-design-progress.md`
- Regenerated architecture, risk/NFR mappings, 161-row atomic design source, generated requirement/risk projections, phase gates, staffing, and handoff from the corrected research.
- Installed-checklist result: **CONCERNS / BLOCKED FOR IMPLEMENTATION**, not unconditional PASS. Document structure, atomic IDs, risk math, ownership, ranges, execution strategy, cross-document counts, and corrected runtime terminology validate; implementation evidence does not exist.
- No source, tests, docs, research, `.gitignore`, workflows, configuration, or BMAD installation was changed. No browser/session/temp artifact was created.

Checklist exceptions are explicit: architecture is 239 lines rather than the ~150–200 target because all 36 risks and mitigation rows are retained; P0 exceeds the generic <10% heuristic because categorical atomic blockers cannot be demoted; the estimate uses the research-mandated cross-functional workstream basis rather than inventing an additive QA-only total; stakeholder exclusion approval, named staffing, team review, executable coverage, and final NFR evidence remain open.

### Remaining genuine gates

1. Experiment-start SDK catalog/capability snapshot and deterministic M0/M1/M2 qualification/pinning.
2. Current-subscription auth proof, SDK BYOK/alternate inference rejection, and framework-only OpenAI credential isolation with fail-closed routing.
3. Separate provider-span identities and independently reconciled Copilot subscription/framework OpenAI/local-resource ledgers.
4. Executable scenario catalog/schema/hashes/generator/CI; assignment/evidence/analysis/safety control plane; frozen tasks/graders/blinding.
5. Owner statistical, economic, governance, staffing, retry, availability/RTO/RPO, and threshold parameters.
6. Quota/rate-limit capacity for the 32/128/512/≤192 staged envelope.
7. Continuous observation, bounded release wording, and mechanical publish dependency.
8. DG-R caller/authz/provenance/revocation/migration/deletion/cache/TOCTOU/restore controls before the 24-unit cross-repository phase.
