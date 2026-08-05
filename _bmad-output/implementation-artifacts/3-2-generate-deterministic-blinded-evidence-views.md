# Story 3.2: Generate Deterministic Blinded Evidence Views

Status: ready-for-dev

## Story

As a scoring operator,
I want versioned blinded views separated from immutable source evidence,
so that judges receive sufficient evidence without learning treatment.

## Acceptance Criteria

1. **Given** immutable source evidence  
   **When** a blinded view is generated  
   **Then** arm names, treatment codes, revealing memrelay paths, unnecessary provider fields, revealing tool/timing fields, and assignment records are removed or deterministically transformed  
   **And** judgment-relevant code, patch, requirements, tests allowed by policy, and artifact locations remain available.
2. **Given** identical source and blinding-policy hashes  
   **When** the view is regenerated  
   **Then** its bytes and SHA-256 are identical  
   **And** the access-separated unblinded source remains unchanged and linked by governed provenance.
3. **Given** blinded candidates and sentinel transformations  
   **When** leakage tests and a treatment-arm classifier run  
   **Then** any direct leakage fails scoring conformance  
   **And** confirmatory blinding requires the classifier's 95% upper AUC bound at most `0.60`.
4. **Given** blinded-view artifacts before Story 4.1  
   **When** they are written or resolved  
   **Then** Story 3.2 consumes the Story 1.1 `ArtifactStorePort` and `ArtifactManifest` schema `1.0.0`, using deterministic fake artifacts solely for unpaid conformance until Story 4.1 provides the durable filesystem CAS  
   **And** paid execution and study inclusion remain blocked until Story 4.1 passes conformance.

## Dependencies and Prerequisites

- **Authoritative direct graph dependencies:** Stories 2.10 and 3.1, exactly as declared in `epics.md`. Only immutable, secret-scanned source and frozen grader evidence may enter the transform.
- Stories 1.6 and 1.1 are inherited/transitive contract sources, not additional direct graph edges; they supply concealment policy, artifact ports, and fake-provenance eligibility.
- Story 3.3 consumes only the deterministic blinded view. Story 4.1 must qualify before paid use or inclusion.
- Exact traceability: FR38; NFR11, NFR16, NFR23, NFR36-NFR38; AR20.

## Tasks / Subtasks

- [ ] Define a versioned, hash-pinned blinding policy and view schema (AC: 1, 2)
  - [ ] Maintain explicit deny/transform/allow rules; default-deny assignment, treatment, provider, tool, path, and timing fields not needed for judgment.
- [ ] Build canonical deterministic blinded views from immutable source references (AC: 1, 2)
  - [ ] Retain judgment-relevant requirements, code, patch, policy-allowed tests, and stable blinded artifact locations.
  - [ ] Keep source immutable/access-separated and record source, policy, transform, and output hashes.
- [ ] Implement leakage conformance and randomized sentinel corpus (AC: 3)
  - [ ] Detect direct labels, aliases, paths, metadata, ordering, timing/tool/provider proxies, and sentinel transformations.
  - [ ] Freeze the sentinel corpus/generator, seed, feature projection, train/evaluation split, classifier algorithm, confidence-bound method, and all hashes before outcome access.
  - [ ] Run that preregistered treatment-arm classifier without outcome-informed feature tuning; require the frozen 95% upper AUC bound `<=0.60` (not mean/point AUC).
  - [ ] Fail scoring conformance on any direct leak or classifier-gate failure; do not waive or average this gate.
- [ ] Use fake artifact ports until Story 4.1 CAS qualification (AC: 4)
  - [ ] Mark artifacts `unpaid_conformance` and mechanically deny paid execution/study inclusion.
  - [ ] Preserve inherited fake-ledger/fake-telemetry ineligibility; Story 4.1 alone does not authorize a paid or included run.
- [ ] Add golden-byte, property, access-control, leakage, classifier, and corruption tests (AC: 1-4).

## Developer Context

The blinded view is a deterministic derived artifact, not a redacted replacement for source evidence. The unblinded source remains immutable and inaccessible to scoring/judge processes. Leakage control has two independent hard gates: zero direct leakage and the frozen arm-classifier 95% upper AUC at most `0.60`. Human arm guesses may be diagnostics but cannot replace the classifier or rescue a failed gate.

### Architecture and File Requirements

- Follow AD-05, AD-12, AD-13, AD-15, AD-17, and AD-22; scoring must have no assignment-resolution import or API.
- Expected paths:
  - **UPDATE:** `evaluation/src/memrelay_eval/domain/{entities,ports,policies,errors}.py`
  - **NEW/UPDATE:** `evaluation/src/memrelay_eval/scoring/blinding.py`
  - **UPDATE:** `evaluation/src/memrelay_eval/scoring/service.py`
  - **UPDATE:** `evaluation/src/memrelay_eval/evidence/{manifest,required}.py`
  - **NEW:** `evaluation/schemas/blinded-view.schema.json`
  - **NEW:** `evaluation/tests/{unit,contract,security}/scoring/`
- `evaluation/` is currently absent; preserve predecessor APIs when materialized. No product-source or root-manifest update is allowed.

### Library, Test, and Version Guardrails

- Python 3.11; RFC 8785 canonical JSON and lowercase SHA-256 through the one shared canonicalizer inherited from Epic 1. Do not create a scoring-local canonicalizer.
- Do not add an online classifier/tracker. The classifier is bounded, frozen, local, no-network, and fitted/evaluated only on preregistered splits with retained hashes.
- Test identical bytes/hashes, source immutability, access denial, every named leak class, sentinel/seed/split reproducibility, exact confidence-bound boundary behavior, adverse classifier bounds, and fake-CAS ineligibility.
- Changes to view schema, policy, feature set, split, threshold, sentinel set, or normalization require a new protocol version before outcomes.

### Preserved Behavior and Anti-Patterns

- Preserve raw/native evidence, source classification, redaction, opaque IDs, hard grader outcomes, and artifact lineage.
- Do not delete or mutate source, reveal treatment via filenames/order/timing/tools, tune leakage defenses after outcomes, expose assignment services to scoring, or accept mean AUC when its 95% upper bound exceeds `0.60`.
- Do not treat fake artifact references as durable CAS; do not make provider calls.

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` — Story 3.2; FR38; NFR11]
- [Source: `ARCHITECTURE-SPINE.md` — AD-12, AD-13, AD-15, AD-22]
- [Source: `IMPLEMENTATION-DESIGN.md` — §§6.7, 19.2-19.6, 22.1, 24.2-24.4]
- [Source: technical research — blinded transform, leak study, calibration/agreement/sensitivity guidance]
- [Source: `_bmad-output/test-artifacts/test-design-qa.md` — `BLIND-TRANSFORM`, `BLIND-GUESS`, `BLIND-LEAK-CLASSIFIER`, `BLIND-LEAKAGE-BOUND`]
- [Source: `_bmad-output/test-artifacts/test-design-architecture.md` — R-012 mitigation]
- [Source: predecessor Story 2.10 — immutable native evidence, redaction, fake artifact qualification]

## Dev Agent Record

### Agent Model Used

TBD by implementation agent

### Debug Log References

### Completion Notes List

- Ultimate context engine analysis completed - comprehensive developer guide created

### File List
