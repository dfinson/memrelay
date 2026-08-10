# Story 4.6: Record Separate Quantity and Cost Ledgers

Status: review

## Story

As an economics researcher,
I want separately queryable usage and local-resource ledgers,
So that unsupported fields and unlike quantities are not mistaken for zero or combined.

## Acceptance Criteria

1. **Given** Copilot subscription usage  
   **When** a cost entry is written  
   **Then** exposed input, cached input, output, reasoning, AI-credit, tool-call, quota, throttle, and reset quantities retain source and measurement status  
   **And** subscription allowance and incremental cash remain distinct.
2. **Given** framework OpenAI or local resource usage  
   **When** entries are recorded  
   **Then** OpenAI model/token/tool/service-tier/region/API-cost quantities and local CPU/memory/disk/process/Collector/storage quantities use separate logical ledgers  
   **And** every entry identifies attempt, provider, credential domain, source, canonical unit, price-table version, currency, measurement, and observation time.
3. **Given** a provider does not expose a usage field or two records use incompatible units  
   **When** normalization runs  
   **Then** the field is `unavailable`, never zero, and incompatible tokens are not aggregated  
   **And** conversion occurs only through a versioned conversion table.

## Dependencies and Prerequisites

- **Authoritative direct graph dependencies:** Stories 2.10 and 4.4 only, exactly as declared in `epics.md`.
- Story 2.10 supplies native usage/evidence preservation; Story 4.4 supplies provider/credential/cost-source identity authority.
- Story 4.1 stores native quantity, price-table, conversion-table, and derived record artifacts; Story 4.2 appends only refs/digests.
- Story 4.5 requires complete quantities or explicit permitted `unavailable` values before inclusion.
- This story records quantities and their provenance. Story 4.7 derives/revises monetary views without rewriting them.
- Exact traceability: FR45, FR46; NFR33, NFR34, NFR37; AR27.

## Tasks / Subtasks

- [x] Define versioned common cost/quantity entry schema (AC: 1, 2)
  - [x] Require opaque cost-entry/attempt IDs, provider, credential domain, cost source, source authority/ref, quantity or explicit unavailable status, canonical unit, price-table version/ref, currency where applicable, measurement status, and UTC observation time.
  - [x] Distinguish `metered`, `estimated`, `subscription_normalized`, `invoice_reconciled`, and `unavailable`; never encode status through null/zero alone.
  - [x] Require explicit `not_applicable` authority where a raw quantity has no currency or price table yet; never invent a price-table/currency value to satisfy shape.
- [x] Implement three separately queryable logical ledgers (AC: 1, 2)
  - [x] Copilot subscription quantities: requests/model calls, exposed input/cached-input/cache-write/output/reasoning tokens, AI credits or legacy units, tool calls, provider latency, throttles, quota rejections, allowance at start/end, reset, plan, and billing period where native evidence exposes them.
  - [x] Framework OpenAI: exact operation/model, input/cached/output tokens, tools/requests, service tier, region, retries, dated price/invoice authority, and actual API cost where supported; never include coding-agent inference.
  - [x] Local resources: CPU/process seconds, active/queue/provisioning/backoff/cleanup wall clocks, peak and byte-second memory, disk byte/second measures, Collector/telemetry overhead, storage reads/writes, and network operations as frozen units permit.
- [x] Define canonical unit vocabulary and conversion tables (AC: 3)
  - [x] Include at least `input_token`, `cached_input_token`, `output_token`, `reasoning_token`, `ai_credit`, `tool_call`, `request`, `usd`, `cpu_second`, `byte_second`, `disk_byte`, and `wall_second`.
  - [x] Reject cross-provider token addition and incompatible dimensions; conversions require a versioned/hash-linked table and exact source/target unit.
  - [x] Add any needed cache-write, provider-latency, disk-time, storage-operation, network, GB-month, or billing-period units by versioning the vocabulary; do not overload an existing unit.
- [x] Preserve source-of-truth and missingness (AC: 1-3)
  - [x] Keep native provider records authoritative for exposed quantities; normalized rows are derived projections with source hashes.
  - [x] Record unsupported/non-exposed fields as explicit `unavailable`, not zero; record observed zero only when instrumentation was active and authority supports it.
  - [x] Keep subscription allowance, shadow/normalized value, incremental cash, API invoice cost, local resource value, fully loaded cost, and study cost conceptually distinct.
- [x] Append immutable quantity artifacts/links (AC: 1, 2)
  - [x] Publish canonical records through CAS and typed ledger intents; retries/late sources create new linked records, never updates.
- [x] Add schema, arithmetic, authority, unit, missingness, and reconciliation tests (AC: 1-3).

These are three logical evidence ledgers published as immutable artifacts and referenced by the evaluator ledger. They are not three writable SQLite databases and do not weaken Story 4.2's single control-owned writer.

## Developer Context

Quantities are the durable economic source of truth. Currency is a derived view. Copilot subscription consumption, framework OpenAI metered API usage, and local resources have distinct authorities and units. A subscription-included request is neither an OpenAI invoice charge nor economically “free.” Every assigned run—including failures, timeouts, and actual zero-cost outcomes—retains quantity status. Environment fingerprints and provider-time strata remain linked so local contention or quota state is not mistaken for treatment cost.

### Architecture Compliance

- Follow AD-03, AD-05, AD-15, AD-16, AD-17, AD-18, AD-24.
- Cost schema/version, unit vocabulary, conversions, price tables, and derivations are hash-pinned. Any semantic change requires a new version.

### Frozen Version Requirements

- Framework price seed is Story 4.7 scope; this story may reference price-table versions but must not silently apply current web prices.
- Preserve active-agent wall time from first agent action after workspace-ready to terminal on the control monotonic clock. Provider latency inside an active call counts; scheduler queue, rate-limit backoff, provisioning, and cleanup remain separate.

### File Structure Requirements

- **UPDATE:** `evaluation/src/memrelay_eval/domain/{entities,errors,policies}.py`
- **NEW/UPDATE:** `evaluation/src/memrelay_eval/evidence/{costs,manifest,required}.py`
- **UPDATE:** relevant provider/telemetry adapters to emit domain quantity records; no cross-adapter imports.
- **NEW/UPDATE:** `evaluation/schemas/cost-ledger.schema.json`
- **NEW:** `evaluation/schemas/cost-unit-conversion.schema.json`
- **NEW:** `evaluation/tests/contract/costs/test_cost_schema_units.py`
- **NEW:** `evaluation/tests/integration/costs/test_provider_ledgers.py`
- **NEW:** `evaluation/tests/fault/costs/test_missing_conflicting_usage.py`
- **READ ONLY:** future repricing, Parquet, DuckDB, estimator, and report code.

### Existing Behavior to Preserve

- Preserve raw provider usage/limits/cancellation/failure evidence and separate provider identities from Stories 2.10/4.4.
- Preserve explicit unavailable versus observed zero, failures in ITT, monotonic timing, and all late/contradictory records.
- Preserve secret-safe manifests: invoice/account/credential values not required by schema remain governed artifacts, not telemetry attributes.

### Testing Requirements

- Property tests reject incompatible dimension aggregation, cross-provider token sums, invalid currency/unit/status combinations, and conversions without a frozen table.
- Golden tests reconcile normalized projections to native SDK/OpenAI/local evidence by hash.
- Cover exposed zero, unsupported field, missing required field, conflicting authorities, late usage, retry lineage, timeout, quota/throttle/reset, and local counter rollover.
- `COST-PROVIDER-LEDGERS` must reconcile each source independently before any later labeled marginal-cost composition.

### Anti-Patterns

- Do not use `None`, omitted, or zero interchangeably; do not guess unsupported provider fields.
- Do not merge Copilot and OpenAI tokens, infer cash from token counts without a versioned table, or call subscription-normalized values invoice cost.
- Do not rewrite quantity rows when prices/invoices change.
- Do not implement repricing, economic estimators, Parquet/DuckDB analytics, or reports here.

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` — Epic 4 / Story 4.6; FR45, FR46]
- [Source: `ARCHITECTURE-SPINE.md` — AD-16, AD-24]
- [Source: `IMPLEMENTATION-DESIGN.md` — §§17.4, 18, 20, 22]
- [Source: technical research — distinct cost estimands, source provenance, frozen wall/cost contract]
- [Source: `_bmad-output/test-artifacts/test-design-qa.md` — R-015, R-016, R-035; `COST-PROVIDER-LEDGERS`]
- [Source: predecessor Stories 2.10 and 4.4 — native usage and identity separation]

## Dev Agent Record

### Agent Model Used

GPT-5.6 Terra (gpt-5.6-terra)

### Debug Log References

- `py -3.13 -m pytest evaluation\tests -q` - 1046 passed, 4 skipped.
- `py -3.13 -m pytest -q` - 1303 passed, 4 skipped.
- `py -3.13 -m ruff check evaluation\src evaluation\tests` - passed.
- `py -3.13 -m ruff format --check evaluation\src evaluation\tests` - passed.
- `uv lock --project evaluation --check` - passed.
- `git diff --check` - passed.

### Completion Notes List

- Ultimate context engine analysis completed - comprehensive developer guide created
- Added immutable raw quantity records with frozen units, explicit unavailable values, price and
  conversion authority, source hashes, observation time, and retry lineage.
- Added three separately queryable cost-ledger links that retain only typed CAS references in the
  control-owned SQLite writer; no worker-side SQLite path was added.
- Added exact native-field normalization, conversion guards, conflict rejection, secret-safe source
  validation, telemetry/resource identity separation, and replay/concurrency coverage.

### File List

- `_bmad-output/implementation-artifacts/{4-6-record-separate-quantity-and-cost-ledgers.md,sprint-status.yaml}`
- `evaluation/{schemas/{cost-ledger,cost-unit-conversion}.schema.json}`
- `evaluation/src/memrelay_eval/{domain/{entities,intents,ports,states}.py,evidence/costs.py}`
- `evaluation/src/memrelay_eval/{adapters/fakes.py,ledger/{repository,schema}.py}`
- `evaluation/tests/{contract/{costs/test_cost_schema_units.py,identity/test_authority_matrix.py,ledger/test_repository.py},integration/costs/test_provider_ledgers.py,fault/costs/test_missing_conflicting_usage.py}`
