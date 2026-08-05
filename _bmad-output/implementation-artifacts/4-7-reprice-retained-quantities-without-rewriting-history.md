# Story 4.7: Reprice Retained Quantities Without Rewriting History

Status: ready-for-dev

## Story

As an economics researcher,
I want monetary outcomes repriced from immutable quantities and append-only revisions,
So that price updates do not alter observed usage.

## Acceptance Criteria

1. **Given** framework OpenAI quantities  
   **When** the initial frozen price table is applied  
   **Then** `gpt-4.1-mini-2025-04-14` prices per million tokens are input `$0.40`, cached input `$0.10`, and output `$1.60`  
   **And** the dated price-table artifact and hash are linked to every derived amount.
2. **Given** a new price or invoice revision  
   **When** repricing runs  
   **Then** a new append-only price/invoice record and derived monetary view are created from retained quantities  
   **And** prior quantities, prices, estimates, and results remain unchanged.
3. **Given** a report requests monetary results  
   **When** amounts are selected  
   **Then** estimated, subscription-normalized, and invoice-reconciled values are labeled distinctly  
   **And** Copilot and framework token quantities are never collapsed into one model-cost quantity.

## Dependencies and Prerequisites

- **Authoritative direct graph dependency:** Story 4.6 only, exactly as declared in `epics.md`.
- Story 4.6 supplies immutable quantity ledgers, canonical units, measurement states, and source authority.
- Stories 4.1/4.2 provide append-only CAS/link persistence; Story 4.4 identity authority prevents cross-provider repricing.
- Story 4.5 inclusion consumes complete cost evidence or permitted unavailable values, but pricing revisions cannot rewrite its source evidence.
- No report or effect estimate is created here; Epic 5 consumes labeled immutable monetary views.
- Exact traceability: FR47; NFR33, NFR34, NFR37; AR11.

## Tasks / Subtasks

- [ ] Define dated, versioned price/invoice artifacts (AC: 1, 2)
  - [ ] Include provider/product/model, effective interval, billing region, currency, unit/scale, tax/discount/credit treatment, source authority, observation/retrieval time, schema version, and content hash.
  - [ ] Store the frozen initial framework table exactly: `gpt-4.1-mini-2025-04-14`, USD per 1M tokens, input `0.40`, cached input `0.10`, output `1.60`.
- [ ] Implement deterministic repricing from retained quantities (AC: 1, 2)
  - [ ] Resolve compatible unit/provider/model/effective interval only; reject ambiguity, overlap, missing scale, or authority conflict.
  - [ ] Parse rates and quantities as exact decimal values; compute `quantity / 1_000_000 * rate` for the frozen token table, retain unrounded derivation precision, and round only under a versioned output/presentation policy. Never use binary float for money.
  - [ ] Link every amount to quantity entry hashes, price/invoice artifact hash, conversion-table hash, derivation version, and environment/protocol context.
- [ ] Implement append-only revisions (AC: 2)
  - [ ] New price, invoice, FX, tax/discount, plan/allowance, or allocation data creates a linked revision and new monetary view.
  - [ ] Preserve all prior estimates, subscription-normalized sensitivities, invoices, reconciliation lag/status, and results.
  - [ ] Make reruns idempotent for identical canonical inputs.
- [ ] Enforce labeled monetary authority (AC: 3)
  - [ ] Distinguish estimated, shadow/sensitivity, subscription-normalized, incremental cash, framework API metered, invoice-reconciled, local variable, fully loaded, and study-cost categories where present.
  - [ ] Reject “actual cash” unless invoice/additional-usage evidence supports it.
  - [ ] Never collapse Copilot and framework tokens or rewrite source quantities into a common token.
  - [ ] Produce Copilot subscription sensitivities only under explicit dated allowance/plan inputs and named published, included-allowance, overage, and `0.5x/1x/2x` shadow-price scenarios; none is invoice cash without invoice evidence.
- [ ] Expose a query/service contract for downstream immutable views (AC: 3)
  - [ ] Select by explicit price/revision/scenario identity; no mutable “latest” default in confirmatory paths.
  - [ ] Bind any confirmatory selection before enrollment. A later price/invoice/FX revision creates a new labeled economic view or analysis version and never changes run inclusion or the historical selected view.
- [ ] Add golden arithmetic, revision, authority, idempotency, and failure tests (AC: 1-3).

## Developer Context

Repricing is a pure derivation over immutable quantities. Quantity authority never changes when economics change. A late invoice is new evidence, not a correction-in-place. Copilot allowance/shadow price and OpenAI invoice cost remain separate labeled components. Downstream atomic Parquet publication may materialize selected views only after reconciliation; read-only DuckDB may query them but cannot reprice operational evidence or mutate revisions.

### Architecture Compliance

- Follow AD-05, AD-15, AD-16, AD-17, AD-22.
- Use Python 3.11 stdlib `decimal.Decimal`, canonical JSON, lowercase SHA-256, explicit currency and per-million scale.

### Frozen Version Requirements

- Initial framework model/rates are frozen exactly as stated; new rates create new artifacts and protocol/analysis version where required.
- Version/hash price tables, conversion tables, invoice projections, derivations, and selection policy.

### File Structure Requirements

- **UPDATE:** `evaluation/src/memrelay_eval/domain/{entities,errors,policies}.py`
- **UPDATE:** `evaluation/src/memrelay_eval/evidence/costs.py`
- **NEW:** `evaluation/src/memrelay_eval/evidence/pricing.py`
- **NEW/UPDATE:** `evaluation/schemas/{price-table,monetary-view}.schema.json`
- **NEW:** `evaluation/catalog/prices/framework-openai-initial.json`
- **NEW:** `evaluation/tests/unit/costs/test_repricing.py`
- **NEW:** `evaluation/tests/contract/costs/test_price_revisions.py`
- **NEW:** `evaluation/tests/golden/costs/`
- **READ ONLY:** Story 4.6 quantities, future analysis/report implementations.

### Existing Behavior to Preserve

- Preserve every original quantity, unavailable/zero distinction, provider/credential/cost-source identity, measurement status, and native evidence ref.
- Preserve failed/zero-cost runs and separate active/queue/provisioning/cleanup time quantities.
- Preserve prior derived views after every late invoice, revised price, or new sensitivity scenario.

### Testing Requirements

- Golden vectors cover all three frozen framework rates, mixed token categories, per-million scaling, zero, unavailable, and rounding boundaries.
- Revision tests prove old artifact/view hashes and bytes unchanged and new lineage complete.
- Negative tests reject provider/model mismatch, cross-ledger aggregation, overlapping tables, missing effective interval, unsupported currency conversion, unlabeled estimate, and invoice claim without evidence.
- Cover cache-write or other quantities with no frozen compatible rate as explicit unavailable/not-applicable pricing, never as free or silently folded into input tokens.
- No-network CI uses committed/hash-pinned price artifacts; never scrape live pricing during tests or execution.

### Anti-Patterns

- Do not mutate quantities, overwrite price rows, use floating point, silently choose latest pricing, or backfill old results.
- Do not call subscription-included Copilot usage zero economic cost or actual invoice cost.
- Do not merge provider tokens into one model quantity or hide study/fully-loaded cost inside marginal cost.
- Do not implement estimators, report selection, live price fetching, Parquet publication, or DuckDB queries here.

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` — Epic 4 / Story 4.7; FR47]
- [Source: `ARCHITECTURE-SPINE.md` — AD-16; frozen stack pricing]
- [Source: `IMPLEMENTATION-DESIGN.md` — §§18, 24.1]
- [Source: technical research — price-independent sensitivity and late-invoice versioning]
- [Source: `_bmad-output/test-artifacts/test-design-qa.md` — R-016; `COST-*`]
- [Source: predecessor Story 4.6 — immutable source quantities and unit vocabulary]

## Dev Agent Record

### Agent Model Used

TBD by implementation agent

### Debug Log References

### Completion Notes List

- Ultimate context engine analysis completed - comprehensive developer guide created

### File List
