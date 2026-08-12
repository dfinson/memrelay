# Story 4.4: Separate Provider and Credential-Domain Identities

Status: done

## Story

As a cost and security reviewer,
I want provider, service, resource, credential, and cost identities kept distinct,
So that Copilot, framework OpenAI, and local resources cannot be conflated.

## Acceptance Criteria

1. **Given** Copilot and framework OpenAI activity  
   **When** spans and cost records are emitted  
   **Then** they use different service names, providers, credential domains, cost sources, resource identities, and ledgers  
   **And** local embeddings and resources never claim an external provider.
2. **Given** telemetry, manifest, or cost schema validation  
   **When** a Copilot subscription identity is paired with an OpenAI credential domain, or vice versa  
   **Then** validation fails with an authority-conflict reason  
   **And** the affected run is ineligible pending reconciliation.
3. **Given** credential-free Collector, grader, MCP, evidence, or analysis processes  
   **When** their environments and emitted resources are inspected  
   **Then** no provider credential is present  
   **And** credential values never appear in telemetry or manifests.

## Dependencies and Prerequisites

- **Authoritative direct graph dependencies:** Stories 2.3 and 4.3 only, exactly as declared in `epics.md`.
- Story 2.3 supplies process-specific environment allowlists; Story 4.3 supplies telemetry semantics and Collector integration.
- Consume Story 2.10 secret-canary scanning and Story 4.1 secret-safe manifest validation; do not create a parallel scanner.
- This story defines/validates identity authority. Story 4.6 later records separate quantities; Story 4.5 later decides immutable inclusion.
- Paid execution and inclusion remain blocked until durable CAS, sole-writer ledger, Collector, credential isolation, and identity conformance pass.
- Exact traceability: FR40; NFR12, NFR13, NFR16, NFR33; AR25.

## Tasks / Subtasks

- [x] Define a versioned identity vocabulary and compatibility matrix (AC: 1, 2)
  - [x] Distinguish `service_name`, provider, credential domain, cost source, resource identity, operation, and logical ledger.
  - [x] Define separate Copilot subscription, framework-internal OpenAI, and local/credential-free combinations.
  - [x] Reject unknown or cross-authority combinations with stable `authority_conflict` reasons; never coerce.
- [x] Apply identities to telemetry, manifests, and cost record contracts (AC: 1, 2)
  - [x] Copilot: SDK-specific service/resource, canonical provider, Copilot-subscription credential domain, and `copilot_subscription_usage`; preserve any native `github_copilot_sdk` labels as source fields rather than aliases for OpenAI.
  - [x] Framework: separate daemon/framework service/resource, canonical OpenAI provider, framework OpenAI credential domain, and `openai_api_metered`.
  - [x] Local embedding/CPU/memory/disk/Collector/storage: explicit local/no-credential identity and local-resource cost source, with no external credential/provider claim.
  - [x] Freeze canonical enum values and source-to-canonical aliases in the versioned compatibility matrix; do not let service name, provider, or model name infer another field.
- [x] Enforce process environment allowlists (AC: 3)
  - [x] Task-agent/judge processes receive only host Copilot authentication and no OpenAI key/base URL.
  - [x] Framework daemon receives only its configured OpenAI credential and no GitHub/Copilot token.
  - [x] Collector, grader, MCP thin client, evidence, analysis, and local resource processes receive neither.
- [x] Integrate secret-safe native evidence checks (AC: 3)
  - [x] Scan environment projections and emitted telemetry/manifests with Story 2.10 canaries.
  - [x] Persist only redacted finding type/location/hash; never serialize or echo a credential value.
- [x] Gate run eligibility on identity conflicts (AC: 2)
  - [x] Preserve all conflicting source refs and append a typed ineligibility fact through the control-owned ledger.
  - [x] Do not let favorable telemetry, cost, or aggregate outcomes waive the conflict.
- [x] Add matrix, boundary, canary, and negative tests (AC: 1-3)
  - [x] Cover every valid/invalid provider×credential×cost-source×resource combination and inherited environment path.

## Developer Context

Provider, credential, service/resource, and cost-source fields answer different questions and are never aliases. Native Copilot consumption is not an OpenAI API charge. Framework-internal OpenAI calls are not task-agent inference. Local resources do not inherit an external provider merely because they support a provider-backed run. Identity conflicts are evidence-integrity/security blockers and must retain source evidence for reconciliation.

The current shipped product keeps the MCP process credential-free and routes framework calls through the isolated daemon/engine process. Preserve that seam: do not inject the framework OpenAI key into the MCP client or task-agent process, and do not give the framework process GitHub/Copilot credentials.

### Architecture Compliance

- Follow AD-03, AD-09, AD-14, AD-15, AD-16, AD-17, AD-24.
- Reuse `memrelay.eval.genai-map/1.0.0` and Story 4.3 semantic schema; version any vocabulary/compatibility change.

### Frozen Version Requirements

- Preserve exact runtime/provider locks from architecture; no BYOK or alternate provider may enter the Copilot task-agent path.
- Environment fingerprints record policy/config hashes and non-secret platform facts; never credential values.

### File Structure Requirements

- **UPDATE:** `evaluation/src/memrelay_eval/domain/{entities,errors,policies}.py`
- **UPDATE:** `evaluation/src/memrelay_eval/adapters/process/environment.py`
- **UPDATE:** `evaluation/src/memrelay_eval/adapters/telemetry/{otel,semantics,reconcile}.py`
- **UPDATE:** `evaluation/src/memrelay_eval/evidence/{manifest,costs,required}.py`
- **NEW:** `evaluation/schemas/provider-identity.schema.json`
- **NEW:** `evaluation/tests/contract/identity/test_authority_matrix.py`
- **NEW:** `evaluation/tests/security/test_provider_credential_boundaries.py`
- **READ ONLY:** provider clients, secret values, future price/repricing/analysis implementations.

### Existing Behavior to Preserve

- Preserve minimal child environments, secret canary coverage, treatment concealment, native evidence, and process isolation from Epic 2.
- Preserve Story 4.3 opaque correlation and prohibited telemetry fields.
- Preserve all source authority disagreements; never rename fields to make incompatible records appear compatible.

### Testing Requirements

- Table/property tests cover complete valid/invalid identity combinations and stable typed errors.
- Launch process-role fixtures and inspect actual child environments plus emitted resource projections.
- Canary tests cover inherited environment, config, exception, manifest, log, span, baggage, and serialized forms without echoing matched values.
- `TEL-PROVIDER-IDENTITY`, `COST-PROVIDER-LEDGERS`, `SECRET-OPENAI-ISOLATION`, and `BLOCK-EVIDENCE/SECURITY` remain fail-closed.

### Anti-Patterns

- Do not use one generic `openai` provider for both paths, infer credential domain from model name, or let one span carry both cost sources.
- Do not label subscription-included Copilot use as zero cash/economic cost or merge token units.
- Do not place secrets in hashes intended for low-entropy recovery, resource attributes, manifests, errors, or tests.
- Do not implement quantity normalization, price arithmetic, inclusion reconciliation, Parquet, or reporting here.

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` — Epic 4 / Story 4.4; FR40]
- [Source: `ARCHITECTURE-SPINE.md` — AD-09, AD-14, AD-16, AD-17]
- [Source: `IMPLEMENTATION-DESIGN.md` — §§15, 17.4, 18]
- [Source: technical research — corrected trust boundaries and non-conflated provider ledgers]
- [Source: `_bmad-output/test-artifacts/test-design-qa.md` — R-034, R-035; `TEL-PROVIDER-IDENTITY`, `COST-PROVIDER-LEDGERS`]
- [Source: predecessor Stories 2.3, 2.10 and Story 4.3 — environment, secret, and telemetry contracts]

## Dev Agent Record

### Agent Model Used

GPT-5.6 Terra (gpt-5.6-terra)

### Debug Log References

- `py -3.13 -m pytest evaluation\tests -q`
- `py -3.13 -m pytest -q`
- `py -3.13 -m ruff check evaluation\src evaluation\tests`
- `py -3.13 -m ruff format --check evaluation\src evaluation\tests`
- `uv lock --project evaluation --check`
- `git diff --check`

### Completion Notes List

- Added a frozen provider-identity matrix with exact source labels, service/resource identity,
  credential domain, cost source, operation, and separate logical ledgers.
- Versioned telemetry evidence to 1.1.0 and bind each span class to its owning provider or
  local-resource authority; invalid aliases and substitutions fail closed.
- Added cost/identity evidence contracts, secret-safe environment projections, and a
  control-owned append-only authority-conflict ledger fact.
- Preserved Story 4.3 telemetry drop behavior while emitting direct-engine outcome spans on
  failed, timed-out, and cancelled boundaries.

### File List

- `evaluation/{collector/semantic-map.yaml,schemas/{provider-identity,telemetry-evidence}.schema.json}`
- `evaluation/src/memrelay_eval/{domain/{identity,errors,intents,states}.py,evidence/costs.py}`
- `evaluation/src/memrelay_eval/{adapters/{fakes,memrelay/engine,process/environment,telemetry/semantics}.py,ledger/{repository,schema}.py,orchestration/inspect.py}`
- `evaluation/tests/{contract/identity/test_authority_matrix.py,security/test_provider_credential_boundaries.py}`
