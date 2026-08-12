# Story 3.5: Adjudicate Frozen Disagreement Thresholds

Status: done

## Story

As a scoring operator,
I want a fresh blinded adjudicator invoked only for material disagreement,
so that disputes are resolved transparently without replacing original judgments.

## Acceptance Criteria

1. **Given** three immutable judge records  
   **When** no prospectively frozen criterion-level disagreement threshold is crossed  
   **Then** no adjudicator session runs  
   **And** the threshold evaluation is retained as evidence.
2. **Given** a frozen disagreement threshold is crossed  
   **When** adjudication starts  
   **Then** a fresh blinded session receives candidate evidence and anonymized rationales but no treatment labels or judge identities  
   **And** it resolves each disputed criterion with artifact citations.
3. **Given** an adjudication result  
   **When** panel evidence is finalized  
   **Then** original scores and rationales remain immutable and the adjudication is appended as a separate record  
   **And** adjudication cannot override executable failure or any categorical blocker.

## Dependencies and Prerequisites

- **Authoritative direct graph dependency:** Story 3.3 only, exactly as declared in `epics.md`. Consume exactly its three immutable judge records and the transitively available Story 3.2 deterministic blinded candidate view.
- Disagreement formulas/thresholds, adjudicator eligibility, prompt/rubric/tools, limits, and failure policy are sealed before outcome access.
- Story 3.4 panel reliability is independent: adjudication cannot repair failed agreement, calibration, drift, sentinel, leakage, or shared-bias gates.
- Exact traceability: FR36; NFR12, NFR29, NFR37, NFR45; AR20.

## Tasks / Subtasks

- [x] Define frozen criterion-level disagreement evaluation and evidence record (AC: 1, 2)
  - [x] Evaluate every criterion deterministically against the sealed threshold and retain both crossed and non-crossed decisions.
  - [x] If any formula, threshold, eligibility rule, or required input is absent/unsealed, make zero provider calls and block finalization with typed evidence; never infer a threshold from judge outputs.
- [x] Implement the no-call branch as a tested provider-use invariant (AC: 1)
  - [x] Prove zero session/provider invocation when no criterion crosses.
- [x] Launch one fresh blinded adjudicator only when required (AC: 2)
  - [x] Select a qualified pinned model from `model-lock.json` under the frozen rule; if no eligible model exists, retain blocked/unavailable evidence without substitution. Create a new Copilot SDK session/process with only host auth and no assignment, judge identity, unblinded evidence, cost, OpenAI credential, or unrelated provider access.
  - [x] Supply only disputed criteria, deterministic blinded candidate evidence, and anonymized immutable rationales; require structured resolution, uncertainty, and artifact citations.
- [x] Append adjudication without mutation or replacement (AC: 3)
  - [x] Link original records by hash, retain their bytes/scores/rationales, and record `not_triggered`, `completed`, or `failed/blocked` status plus adjudicator runtime/prompt/rubric/tools/controls/order hashes when a call occurs.
- [x] Enforce bounded provider use and hard authority (AC: 1-3)
  - [x] At most one authorized fresh adjudicator session per candidate/view/panel/threshold-protocol hash tuple; an idempotent replay returns retained evidence or a typed conflict and never launches again.
  - [x] Enforce token/tool/time/concurrency caps and no hidden retry/fallback/repeated-until-consensus.
  - [x] Preserve failure evidence; block panel finalization as frozen rather than substituting. Never override executable failure or categorical blockers.
- [x] Add fake-runtime, boundary, privacy, immutability, failure, cap, and authority tests (AC: 1-3).
  - [x] Keep every fake-runtime/artifact/ledger/telemetry result ineligible for paid or study use until the corresponding Epic 4 adapters and reconciliation qualify.

## Developer Context

Adjudication is exceptional append-only evidence, not a fourth ordinary vote, reliability repair, or best-of-N selection. The fresh adjudicator sees anonymized rationales only for disputed criteria and cannot identify treatment or judges. The no-threshold branch must be provably call-free. Original judge records remain immutable regardless of the adjudicated result.

### Architecture and File Requirements

- Follow AD-09, AD-12, AD-13, AD-15, AD-17, AD-19, and AD-22.
- Expected paths:
  - **UPDATE:** `evaluation/src/memrelay_eval/domain/{entities,policies,errors}.py`
  - **NEW:** `evaluation/src/memrelay_eval/scoring/adjudication.py`
  - **UPDATE:** `evaluation/src/memrelay_eval/scoring/service.py`
  - **UPDATE:** `evaluation/src/memrelay_eval/adapters/copilot/{client,session}.py`
  - **NEW:** `evaluation/schemas/adjudication-record.schema.json`
  - **NEW:** `evaluation/tests/{unit,contract,integration,security}/judge/test_adjudication.py`
- `evaluation/` is absent now; reuse Story 3.3's domain runtime/process ports through CLI composition. Keep adjudicator prompting, input minimization, and response policy in `scoring/adjudication.py`; keep Copilot launch mechanics in the existing Copilot adapter. No sibling-adapter import, alternate provider path, product change, or root change is allowed.

### Library, Test, and Version Guardrails

- Python 3.13 and the Story 3.3 locked `github-copilot-sdk==1.0.8` runtime/session boundary.
- CI uses a fake agent runtime with exact call-count assertions; paid execution is explicit and capped.
- Test missing/unsealed protocol, below/equal/above threshold, multiple disputed criteria in one bounded session, zero-call branch, unavailable model, anonymization, immutable originals, adjudicator crash/timeout, artifact citations, exact call caps, and blocker non-overridability.
- Threshold, model-selection, prompt, rubric, tools, limits, or failure-policy changes require a new protocol version.

### Preserved Behavior and Anti-Patterns

- Preserve blinding, model qualification, original judge evidence, panel reliability decisions, executable authority, and categorical blockers.
- Do not adjudicate when no threshold crosses, reveal judge IDs, run per-criterion sessions, mutate original scores, retry until favorable, use another provider, or interpret adjudication as permission to waive a gate.
- Do not claim fake artifacts are CAS-qualified; inherited Story 4.1 paid/inclusion block remains.

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` — Story 3.5; FR36]
- [Source: `ARCHITECTURE-SPINE.md` — AD-09, AD-12, AD-13, AD-15]
- [Source: `IMPLEMENTATION-DESIGN.md` — §§19.3-19.6, 22, 24]
- [Source: technical research — blinded adjudication and immutable evidence guidance]
- [Source: `_bmad-output/test-artifacts/test-design-architecture.md` — R-012, R-026]
- [Source: predecessor Stories 3.2-3.4 — view, judge-record, and reliability contracts]

## Dev Agent Record

### Agent Model Used

GitHub Copilot CLI (GPT-5.6)

### Debug Log References

- `py -3.13 -m pytest evaluation\tests\unit\judge\test_adjudication.py evaluation\tests\contract\judge\test_adjudication.py evaluation\tests\security\judge\test_adjudication.py evaluation\tests\integration\judge\test_adjudication.py -q` (10 passed)
- `py -3.13 -m pytest evaluation\tests\unit\judge evaluation\tests\contract\judge evaluation\tests\security\judge evaluation\tests\integration\judge evaluation\tests\architecture\test_process_boundary.py -q` (30 passed)
- `py -3.13 -m pytest evaluation\tests -q` (1141 passed, 46 skipped; two unrelated catalog-publication cases passed on immediate targeted retry)
- `py -3.13 -m ruff check` and `py -3.13 -m ruff format --check` on all Story 3.5 source and test paths (passed)
### Completion Notes List

- Ultimate context engine analysis completed - comprehensive developer guide created
- Implemented frozen per-criterion spread evaluation, append-only adjudication records,
  one-session authorization/replay binding, hard authority gates, and bounded failure retention.
- Enforced stage-wide caps before any provider call and pinned the deterministic anonymous
  rationale order used in each adjudication request.
- Added schema and focused unit, contract, integration, and privacy tests using unpaid fake evidence.

### File List

- `_bmad-output/implementation-artifacts/3-5-adjudicate-frozen-disagreement-thresholds.md`
- `evaluation/schemas/adjudication-record.schema.json`
- `evaluation/src/memrelay_eval/adapters/copilot/judge_worker.py`
- `evaluation/src/memrelay_eval/adapters/process/judge.py`
- `evaluation/src/memrelay_eval/domain/errors.py`
- `evaluation/src/memrelay_eval/scoring/adjudication.py`
- `evaluation/src/memrelay_eval/scoring/rubric.py`
- `evaluation/tests/**/__init__.py`
- `evaluation/tests/{unit,contract,integration,security}/judge/test_adjudication.py`
