# Story 3.3: Run Three Independent Structured Judge Sessions

Status: ready-for-dev

## Story

As a quality researcher,
I want three fresh blinded judge assessments per primary-analysis candidate,
so that qualitative quality is measured as a separate co-primary endpoint.

## Acceptance Criteria

1. **Given** an eligible primary-analysis candidate and `model-lock.json`  
   **When** panel judging runs  
   **Then** three independent fresh Copilot SDK sessions use distinct eligible pinned judge models and families, excluding the task generator to the maximum available extent  
   **And** a homogeneous or partially homogeneous panel is explicitly labeled with its stronger frozen human-calibration and shared-bias requirements.
2. **Given** a judge session  
   **When** it evaluates the deterministic blinded view  
   **Then** it receives the same frozen rubric and read-only tools but no treatment assignment, task-agent transcript identity, cost data, provider credentials other than host Copilot auth, or other judge output  
   **And** candidate presentation order is randomized from a sealed seed.
3. **Given** a completed assessment  
   **When** its record is stored  
   **Then** it contains structured criterion scores, uncertainty, and artifact citations for uncovered requirement satisfaction, semantic appropriateness, maintainability, unnecessary complexity, repository fit, and evidence-supported confidence  
   **And** model ID, system prompt, rubric, tool schemas, decoding controls, order, and runtime are hash-pinned.
4. **Given** judge-session artifacts before Story 4.1  
   **When** they are written or resolved  
   **Then** Story 3.3 consumes the Story 1.1 `ArtifactStorePort` and `ArtifactManifest` schema `1.0.0`, using deterministic fake artifacts solely for unpaid conformance until Story 4.1 provides the durable filesystem CAS  
   **And** paid execution and study inclusion remain blocked until Story 4.1 passes conformance.

## Dependencies and Prerequisites

- **Authoritative direct graph dependencies:** Stories 2.1, 2.3, and 3.2, exactly as declared in `epics.md`. Consume the locked native model catalog/runtime, disposable credential-isolated process/session contract, and only the deterministic blinded view.
- The qualitative panel is co-primary with, but separate from, Story 3.1 executable authority. Story 3.4 qualifies panel reliability; Story 3.5 may append adjudication.
- Story 4.1 CAS qualification is mandatory before paid judging or study inclusion.
- Exact traceability: FR34, FR35; NFR11-NFR13, NFR37; AR3, AR20.

## Tasks / Subtasks

- [ ] Define frozen rubric, structured result schema, read-only tool policy, and panel schedule (AC: 1-3)
  - [ ] Encode all six named criteria, normalized scores, uncertainty, artifact citations, typed refusal/failure, and evidence hashes.
  - [ ] Freeze a treatment-neutral candidate-eligibility rule before outcomes. Gradeable failed patches remain candidates; missing/ungradeable evidence becomes an explicit unavailable panel outcome rather than an outcome-driven omission.
  - [ ] Include the prospectively supplied human-calibration, duplicate, and sentinel items in the sealed schedule, with exact item/session counts charged to stage caps; Story 3.4 computes from these immutable records and makes no provider calls.
- [ ] Select exactly three eligible judge slots from `model-lock.json` deterministically (AC: 1)
  - [ ] Maximize distinct pinned model IDs/families and task-generator separation; never invent, substitute, or silently reduce judges.
  - [ ] Label partial/homogeneous diversity and attach the prospectively frozen stronger calibration/shared-bias gates.
- [ ] Launch three independent fresh Copilot SDK sessions (AC: 1, 2)
  - [ ] Use one disposable judge process/session per assessment with only host Copilot authentication; exclude OpenAI keys/base URLs and unrelated credentials.
  - [ ] Prevent shared session state, other judge output, judge identities, assignment, unblinded evidence, task-agent transcript identity, and cost data.
- [ ] Randomize candidate order from a sealed seed and pin the complete judging envelope (AC: 2, 3)
  - [ ] Hash model/runtime, prompt, rubric, tools, controls, order, view, and protocol before calls.
- [ ] Bound provider use and preserve terminal evidence (AC: 1-4)
  - [ ] Exactly three authorized judge sessions per candidate; enforce per-session/stage token, tool, active-time, elapsed-time, and concurrency caps.
  - [ ] Key authorization by candidate/view/panel-protocol hashes; a repeated command returns the three retained records or a typed conflict and can never launch a fourth session.
  - [ ] No hidden retries, replacement, repeated-until-success, fallback provider, or extra “tie-break” call; failures remain evidence and block panel completion.
- [ ] Store immutable individual records through fake artifacts until CAS qualification (AC: 3, 4).
  - [ ] Preserve inherited fake-ledger/fake-telemetry ineligibility; CAS conformance alone does not authorize paid/study use.
- [ ] Add fake-runtime CI contracts and explicit paid integration tests (AC: 1-4).

## Developer Context

Three fresh, independent, blinded Copilot SDK qualitative judges are a co-primary authority for solution quality—not a convenience review and not a replacement for executable correctness. Each record is immutable and isolated. Panel completion requires all three authorized assessments. Diversity is maximized from observed qualified models; scarcity is reported and triggers stronger pre-frozen human calibration and shared-bias sensitivity rather than model invention or provider substitution.

### Architecture and File Requirements

- Follow AD-06, AD-08, AD-09, AD-12, AD-13, AD-17, AD-19, and AD-22.
- Expected paths:
  - **UPDATE:** `evaluation/src/memrelay_eval/domain/{entities,ports,policies,errors}.py`
  - **UPDATE:** `evaluation/src/memrelay_eval/scoring/service.py`
  - **NEW:** `evaluation/src/memrelay_eval/scoring/rubric.py`
  - **UPDATE:** `evaluation/src/memrelay_eval/adapters/copilot/{client,session}.py`
  - **UPDATE:** `evaluation/src/memrelay_eval/adapters/process/{launcher,environment}.py`
  - **NEW:** `evaluation/schemas/judge-record.schema.json`
  - **NEW:** `evaluation/tests/{unit,contract,integration,security}/judge/`
- `evaluation/` is absent now; extend predecessor ports without cross-adapter imports. `scoring/service.py` receives the existing domain-owned `AgentRuntimePort` and process port from CLI composition; keep all Copilot session launching in the Story 2.1 adapter rather than creating a sibling judge provider adapter or second route. Judge-specific rubric, scheduling, and response policy stay in `scoring`. Do not touch product sources/root dependencies.

### Library, Test, and Version Guardrails

- Python 3.11; `github-copilot-sdk==1.0.8` with wheel SHA-256 `7c3d868b73daa3a154ae6c1c5a2bd9301c47349c6b080f1ed77ba9027da3e0fa`; use SDK-bundled locked runtime.
- CI uses a fake `AgentRuntimePort` and zero paid calls. Real sessions require explicit approved invocation and bounded quotas.
- Test three-session freshness/isolation, selection reproducibility, sealed order, credential allowlists, no assignment/cross-output access, structured citations, cap enforcement, failure retention, and fake-CAS blocking.
- Model IDs, prompt, rubric, tool schemas, decoding controls, limits, order policy, or runtime changes require a new protocol version.

### Preserved Behavior and Anti-Patterns

- Preserve Story 2 model locks, subscription-only auth, one-worker isolation, native events/terminal evidence, and Story 3.2 blinding/access separation.
- Do not use Inspect model-provider calls, SDK BYOK, OpenAI keys, one session for multiple judges, unqualified models, mutable prompts, unbounded calls, hidden retries, or judge-to-judge context.
- Do not aggregate before retaining all original records; do not let quality scores override executable/categorical blockers; do not call fake artifacts CAS-qualified.

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` — Story 3.3; FR34-FR35]
- [Source: `ARCHITECTURE-SPINE.md` — AD-09, AD-13, AD-19, AD-22; Stack]
- [Source: `IMPLEMENTATION-DESIGN.md` — §§13, 15, 19.3-19.6, 22, 24]
- [Source: technical research — Copilot SDK authentication/session limits and blinded adjudication guidance]
- [Source: `_bmad-output/test-artifacts/test-design-qa.md` — `BLIND-CALIBRATE`, `BLIND-SENSITIVITY`, `OPS-QUOTA-RATE-LIMIT`, `OPS-STAGE-ENVELOPE`]
- [Source: predecessor Stories 2.1, 2.3, and 3.2 — model lock, process isolation, blinded-view contracts]

## Dev Agent Record

### Agent Model Used

TBD by implementation agent

### Debug Log References

### Completion Notes List

- Ultimate context engine analysis completed - comprehensive developer guide created

### File List
