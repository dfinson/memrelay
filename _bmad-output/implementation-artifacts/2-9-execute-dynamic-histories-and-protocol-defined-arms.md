# Story 2.9: Execute Dynamic Histories and Protocol-Defined Arms

Status: ready-for-dev

## Story

As an experiment controller,
I want whole dynamic sequences assigned before episode one across approved arms,
So that evolving state is measured without crossover or unit-of-analysis errors.

## Acceptance Criteria

1. **Given** a dynamic-history protocol  
   **When** a sequence is enrolled  
   **Then** the entire sequence is assigned before episode one into fresh arm-local state  
   **And** the sequence/history is the experimental, resampling, and analysis unit.
2. **Given** episodes update memory or an attempt fails or attrits  
   **When** later episodes execute  
   **Then** state and lineage remain arm-local, failures and attrition remain assigned outcomes, and crossover or treatment-history reuse in controls is prohibited  
   **And** only total-policy sequence estimands are permitted.
3. **Given** protocol-defined `N0`, `E0`, `YL`, `MI`, `TR`, `OR`, `AI`, and `WO` arms  
   **When** a selected arm is provisioned  
   **Then** its frozen tool, permission, budget, accounting, treatment-access, and control-parity contract is enforced  
   **And** unsupported arms fail before exposure rather than being substituted.

## Dependencies and Prerequisites

- Authoritative direct dependencies are Stories 1.6, 1.7, and 2.4. Story 2.8 defines the separate immutable controlled regime; Stories 2.6-2.7 define product/direct strata.

## Tasks / Subtasks

- [ ] Define dynamic sequence and episode lineage records (AC: 1, 2)
  - [ ] Assign the full ordered sequence before episode one with opaque arm IDs and frozen allocation seed lineage.
  - [ ] Carry one arm, graph/root lineage, episode order, and prior-terminal references through all episodes.
- [ ] Implement within-sequence graph evolution, attrition retention, and isolation (AC: 1, 2)
  - [ ] Reuse state only inside the assigned sequence; prohibit every cross-sequence/arm/workspace/cache/output path.
  - [ ] Preserve terminal/failure evidence without favorable episode replacement.
- [ ] Instantiate all approved arm contracts solely from protocol + assignment (AC: 3)
  - [ ] Support protocol-defined no-memory or other frozen control behavior without hardcoded treatment semantics.
  - [ ] Keep executor inputs opaque and arm-neutral.
  - [ ] Enforce the frozen interventions: `N0` unavailable tool with equivalent startup/budget; `E0` visible tool with canonical immediate zero-item reads and discarded writes; `YL` same accounting with a pre-outcome frozen latency-yoked empty response; `MI` pre-outcome matched-format/source/token/latency certified-irrelevant evidence; `TR` production pipeline; `OR` curated minimum-necessary evidence without patch/protected answers; `AI` production pipeline with relevant candidates replaced by certified irrelevant candidates; `WO` writes enabled with reads unavailable.
- [ ] Enforce total-policy sequence estimands and regime identity/non-pooling (AC: 2; Architecture: AD-07)
- [ ] Add allocation, carryover, contamination, retry, and analysis-boundary tests (AC: 1-3)

## Developer Context

Dynamic assignment is at whole-sequence granularity. Episode-level randomization would contaminate treatment and is forbidden. The graph may evolve only within its assigned sequence. Controlled and dynamic protocols answer different questions and maintain distinct protocol, assignment, endpoint, cost, analysis, report, and claim identities. This is independent of the product versus direct-engine stratum dimension.

### Architecture Compliance

- Follow AD-03, AD-05, AD-07, AD-10, AD-11, AD-12, AD-13, AD-16, AD-20, AD-24.
- Allocation resolves opaque arm IDs only; the executor receives no human-readable arm meaning.
- Treatment-aware persistence appears only at authority boundaries, never generic caches/workspaces/tools.
- Story 2.4 Inspect execution remains authoritative for every episode. Treatment access or any executed episode is exposed; an exposed sequence cannot be retried or restarted from episode one.
- No cross-arm or cross-sequence reuse; no pooling across regimes or strata.
- Fake `ArtifactStorePort`, `LedgerPort`, and `TelemetryPort` implementations remain unpaid-only; durable/study provenance requires Story 4.1 CAS, Story 4.2 ledger, and Story 4.3 Collector conformance.

### Library and Version Requirements

- Python 3.11; use Story 1 deterministic assignment/canonicalization primitives.
- Preserve Copilot SDK `1.0.8`, Inspect `0.3.252`, and Stories 2.6-2.7 framework configuration.
- No alternate randomization, cache, workflow, or memory framework.

### Expected File Paths

- **UPDATE:** `evaluation/src/memrelay_eval/domain/{entities,ids,policies}.py`
- **UPDATE:** `evaluation/src/memrelay_eval/orchestration/{history,assignment,attempt,stages}.py`
- **UPDATE:** `evaluation/src/memrelay_eval/adapters/fakes.py`
- **NEW:** `evaluation/tests/unit/orchestration/test_dynamic_assignment.py`
- **NEW:** `evaluation/tests/contract/history/test_sequence_isolation.py`
- **NEW:** `evaluation/tests/unit/test_regime_non_pooling.py`

### Existing Behavior to Preserve

- Story 1 concealed deterministic assignment, frozen block membership, blocked staged-task skipping, and terminal-attempt accounting.
- Controlled checkpoints remain immutable and cannot receive dynamic writes.
- Product/framework and direct-engine identities remain distinct inside each regime.
- This story schedules against domain treatment ports. Do not require or modify concrete product/engine adapters: Stories 2.6/2.7 are contextual strata, not formal graph dependencies.

### Testing Requirements

- Deterministic repeat tests show full sequence assignment before episode one and identical arm across episodes.
- Adversarial probes for cross-arm/sequence graph, root, cache, output, and workspace reuse.
- Control construction tests prove protocol/assignment authority and no hardcoded label leakage.
- Retry truth table covers early unexposed infrastructure failure versus any exposed episode; no sequence best-of-N.
- Analysis/export schemas reject ordinary pooling across regime or stratum; CI uses no provider calls.

### Anti-Patterns

- Do not randomize each episode, switch arms mid-sequence, or seed from mutable timestamps.
- Do not share a graph/cache between control and treatment or between sequences.
- Do not infer control behavior from filenames/labels in generic execution code.
- Do not pool controlled/dynamic estimates, retry post-exposure, or run unbounded paid calls.

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` — “Story 2.9”]
- [Source: `ARCHITECTURE-SPINE.md` — AD-03, AD-05, AD-07, AD-10-AD-13, AD-20, AD-24]
- [Source: `IMPLEMENTATION-DESIGN.md` — §§5, 10.1-10.2, 12.2, 20.2, 21.1-21.3]
- [Source: technical research — “Reproducible arms and ablations”; “Matched-irrelevant and yoke envelope freeze”]
- [Source: `test-design-qa.md` — `HIST-CARRYOVER`, assignment and no-substitution scenarios]

## Dev Agent Record

### Agent Model Used

TBD by implementation agent

### Debug Log References

### Completion Notes List

- Ultimate context engine analysis completed - comprehensive developer guide created

### File List
