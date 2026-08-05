---
name: unit-divergence review
type: architecture-review
lens: adversarial unit divergence
target: ARCHITECTURE-SPINE.md
cross_checked_against: IMPLEMENTATION-DESIGN.md
reviewer_role: independent reviewer gate
created: 2026-08-05
verdict: changes-requested
---

# Reviewer-Gate Review — Unit Divergence Lens

## Method

The spine was read as a stand-alone build substrate. For each governed concern
(shared-data shapes, owners, state-mutation paths, retries, credentials,
treatment assignment, evidence authority, telemetry, analysis) I constructed
**pairs of implementation units one level down**, where each unit obeys every
architecture decision (AD) *literally* yet the two units **build incompatibly**.
Each surviving pair is a hole: the spine underdetermines a decision that two
conforming engineers will resolve in mutually incompatible ways.

`IMPLEMENTATION-DESIGN.md` was consulted only to detect contradictions and to
confirm whether a gap is real. In almost every case below, the implementation
design *silently fills the gap* — which is itself evidence the spine does not
stand alone on that point, because a second engineer reading only the spine
would not reproduce the same choice.

No hard spine/impl contradiction was found. The findings are gaps in the spine,
several of which are load-bearing.

## Verdict

**Changes requested.** The spine is coherent and unusually disciplined, but it
leaves at least five build-incompatible degrees of freedom in load-bearing,
hash- and evidence-critical paths. These will produce components that pass their
own contract tests and still fail to compose. Tighten the ADs below (or add the
noted new ADs) before the spine is treated as frozen.

---

## Blocking Findings

### B1 — Canonicalization + hash algorithm is unpinned; every hash-addressed seam can diverge
**Concern:** shared-data shapes / evidence authority. **Severity: Blocking.**

The Consistency Conventions say hashes are "SHA-256 hex over declared canonical
bytes" and JSON is "canonicalized before identity hashing," but the spine never
names the canonicalization standard, number normalization, key ordering, Unicode
normalization form, or whitespace rules. AD-05, AD-10, AD-11 all depend on hash
identity but none pin the algorithm.

- **Unit A (catalog compiler):** canonicalizes with RFC 8785 / JCS, then SHA-256.
- **Unit B (ledger or evidence writer):** canonicalizes with
  `json.dumps(sort_keys=True, separators=(",",":"))`, then SHA-256.

Both "canonicalize before identity hashing" and both emit "canonical JSON," so
both literally conform. But JCS and `json.dumps` differ on float serialization,
integer/float boundary, non-ASCII escaping, and Unicode normalization — so the
**same logical content yields different digests** in different components.
Catalog hash, artifact IDs, parity hashes, and the reconciliation "matching
catalog/fixture/model/config/parity hashes" check will disagree across the exact
seams the architecture is built on. This is the single most systemic hole because
identity is hash-based end to end.

*Note:* IMPLEMENTATION-DESIGN §6.2 unilaterally chooses "RFC 8785 canonical JSON."
That choice belongs in the spine (a convention or a tightened AD-11), pinned once,
including number/Unicode/whitespace rules and what constitutes "declared canonical
bytes" for raw artifacts (raw on-disk bytes) versus derived JSON (re-serialized).

**Fix:** Add to conventions / AD-11: name the exact canonicalization standard
(RFC 8785), the number and Unicode normalization rules, and state that raw
artifacts are hashed as received bytes while derived records are hashed over the
canonical projection. Require one shared canonicalizer module used by every
component.

### B2 — Ledger writer ownership and concurrency model are undefined
**Concern:** owners / state-mutation paths. **Severity: Blocking.**

AD-04/AD-05 say the SQLite WAL ledger stores append-only transitions; AD-08 says
Inspect runs in the control process and launches one isolated worker per attempt
through a bounded concurrency pool. The spine never says **which process owns
writes to the ledger.**

- **Unit A:** only the control process writes the ledger; workers report
  transitions back over IPC.
- **Unit B:** each worker opens the SQLite file and appends its own
  `provisioned`/`running`/`exported` transitions directly (natural, since the
  worker is where those events happen).

Both obey AD-04, AD-05, and AD-08. But with a bounded pool of N concurrent
workers, Unit B produces **N concurrent writers to one SQLite WAL** — lock
contention, `database is locked`, and no defined global ordering of appended
events. Unit A and Unit B also produce different transition-provenance and
different failure modes under crash. Components built to the two models cannot
share a ledger.

**Fix:** Add/extend an AD designating a single ledger writer (e.g., the control
process is sole ledger writer; workers emit transition intents that the control
process appends) and state the concurrency/serialization guarantee. Without this,
"append-only" does not constrain the write path.

### B3 — Evidence authority on terminal-status disagreement is unresolved
**Concern:** evidence authority. **Severity: Blocking.**

AD-04 declares "Inspect owns execution truth." AD-19 declares the official Copilot
SDK "performs all task inference," and AD-15 requires reconciling Inspect `.eval`,
Inspect JSON export, **and** native SDK terminal record. The spine never states
**who wins when they disagree** (e.g., SDK reports success but Inspect records a
timeout/cancellation, or vice versa).

- **Unit A (outcomes):** treats the Inspect `.eval` terminal status as
  authoritative (cites AD-04).
- **Unit B (outcomes):** treats the native SDK terminal record as authoritative
  (cites AD-19, since the SDK actually ran the inference).

Both conform. They classify the same run's outcome differently, producing
incompatible endpoint components and analysis. AD-15 lists these as co-required
evidence but assigns no precedence, so reconciliation cannot deterministically
resolve the conflict.

**Fix:** Tighten AD-04 or AD-15 to declare a precedence rule: on terminal-status
disagreement, Inspect is authoritative for execution outcome, or the run
fails closed to `evidence_incomplete`/excluded. Name the tie-breaker explicitly.

### B4 — Default classification of ambiguous exposure is missing (re-opens the AD-18 hole)
**Concern:** retries. **Severity: Blocking.**

AD-18 permits exactly one retry only for a *conclusively* pre-exposure
infrastructure failure, precisely to prevent favorable-outcome substitution. The
spine never states how to classify an **ambiguous** exposure (cannot conclusively
prove exposure did or did not occur).

- **Unit A:** treats ambiguous exposure as *exposed* → non-retryable (conservative,
  protects ITT).
- **Unit B:** treats ambiguous exposure as *pre-exposure* → eligible for the one
  authorized retry.

Both conform to AD-18's literal text ("only a pre-exposure infrastructure failure
may receive one retry"), because the spine never says which side of the line
"ambiguous" falls on. Unit B silently re-enables exactly the favorable-substitution
behavior AD-18 exists to prevent. IMPLEMENTATION-DESIGN §8.3 quietly adopts
"ambiguous exposure is treated as exposed" — that rule must live in the spine.

**Fix:** Add to AD-18 (or AD-11 lifecycle): "Exposure that cannot be conclusively
proven pre-exposure is classified as exposed and is not retryable."

### B5 — Run lifecycle vs. attempt terminal status is not modeled in the spine
**Concern:** state-mutation paths. **Severity: Blocking.**

AD-11 fixes one ordered lifecycle — `planned → assigned → provisioned → running →
exported → scored → reconciled → included|excluded` — and binds it to both "run"
and "attempt" IDs. But a failed attempt (timeout, agent failure, provider
unavailable) never reaches `exported`, so the ordered lifecycle has **no terminal
path for a failed attempt**, and the spine gives no separate attempt-status model.

- **Unit A:** applies the ordered lifecycle to the *run*, and models per-attempt
  terminal status (succeeded/agent_failed/timed_out/…) as a separate dimension.
- **Unit B:** applies the ordered lifecycle to *attempts*, and is forced to invent
  ad-hoc terminal transitions (e.g., an out-of-order `running → excluded`) that
  violate the fixed order.

Both claim AD-11 conformance; they produce incompatible ledger schemas and
incompatible reconciliation. IMPLEMENTATION-DESIGN §8.2 resolves this by splitting
run lifecycle from a 10-value attempt terminal classification — but that split is
absent from the spine, so a second engineer would not reproduce it.

**Fix:** Tighten AD-11 to state that the ordered lifecycle applies to the *run*,
and that attempts carry a separate terminal classification (including failure
terminals) that links to the run. Name where a failed/retried attempt attaches.

---

## Non-Blocking Findings

### N1 — Copilot credential injection conflicts with per-attempt freshness
**Concern:** credentials. AD-06 requires a fresh cache root, agent session root,
and no shared writable state per attempt; AD-09 requires the worker to receive
"host Copilot authentication"; AD-17 restricts env vars to credential boundaries.
Copilot auth is typically host file/keychain state, not an env var. Unit A mounts
the host Copilot config read-only into the isolated session root; Unit B relies on
ambient shared host login under a shared config dir (violating AD-06 freshness).
The spine never says the Copilot credential is file-based, how it is injected, or
that it is exempt from the freshness rule. **Fix:** add a rule that Copilot
credential material is injected read-only and is the one explicit exception to
per-attempt root freshness; specify the injection surface.

### N2 — Telemetry correlation key and expected-span-class contract are unowned
**Concern:** telemetry. AD-14 layers OpenInference/GenAI/`memrelay.eval.*` and
AD-15 requires "required OTel trace classes," but the spine never (a) names the
correlation key that binds spans from the isolated worker, daemon, and grader to
an attempt, nor (b) says who owns the canonical list of required span classes.
Unit A relies on W3C trace-context propagation across closed process boundaries
(which the SDK/daemon may not carry); Unit B stamps every span with a
`memrelay.eval.attempt_id` resource attribute. Producers and reconciler built to
different keys cannot associate spans, so completeness checks silently pass or
fail. The required `memrelay.eval.*` fields exist only in the impl doc.
**Fix:** require the attempt correlation attribute on every span in AD-14, and
make the required span-class matrix a versioned catalog-owned contract.

### N3 — No enforceable mechanism prevents strata/history pooling
**Concern:** analysis. AD-03 and AD-07 declare results "never pooled," but the
spine gives only a policy, no mechanism. Unit A carries a `stratum_id`/`protocol_id`
on every analysis row plus a query-time guard; Unit B stores one Parquet dataset
per stratum with no guard — and an analyst can still `UNION` datasets in read-only
DuckDB. "Read-only" (AD-05) prevents mutation, not accidental cross-stratum
aggregation. IMPLEMENTATION-DESIGN §11.3 invents the `stratum_id` + validation
requirement; the spine should mandate it. **Fix:** require a stratum/protocol
discriminator column on every confirmatory table and a validation rule that
rejects unstratified aggregation.

### N4 — Cost-entry units and "unavailable" representation are unconstrained
**Concern:** analysis / shared-data shape. AD-16 lists the required fields but not
a controlled vocabulary for `unit`, nor a representation for unsupported fields.
Unit A emits `input_token` / `cpu_seconds` and `null`→"unavailable"; Unit B emits
`prompt_token` / `cpu_ms` and `0` for missing fields. Both conform to AD-16 but
cannot be repriced or compared, and Unit B's zeros are silently summable —
defeating AD-16's purpose. Impl §18 adds "unavailable, never zero," which belongs
in the spine. **Fix:** pin a controlled unit vocabulary per ledger and require a
non-numeric `unavailable` marker (never zero) for unsupported quantities in AD-16.

### N5 — Direct-engine → agent rendering parity is unspecified (cross-stratum comparability)
**Concern:** treatment assignment / evidence comparability. AD-03/AD-20 define the
engine stratum as excluding MCP rendering overhead, but the engine returns plain
dicts that *someone* must render into the agent's context. The spine sets no
rendering-parity contract, so Unit A renders retrieval as raw JSON and Unit B
renders it via an MCP-equivalent template. The two "engine upper bound" results
are not comparable to each other or to the product stratum, undermining the very
comparison the strata exist for. Impl §7.2/§11.2 says "freeze the
retrieval-to-agent rendering contract" — the requirement to freeze (and that it be
declared evidence) should be in the spine. **Fix:** add a rule that the
engine-stratum rendering contract is frozen, hashed, and recorded as parity
evidence.

### N6 — Workspace isolation "equivalence contract" is undefined and the assumption is optimistic
**Concern:** owners / state-mutation. AD-06 plus the workspace ASSUMPTION claim
worktree and clone providers "prove equivalent isolation," but the observable
isolation contract is never defined — and the assumption is technically shaky:
a git worktree **shares the origin's object database and part of the ref/config
namespace** via the common dir, whereas an isolated clone does not. Unit A (clone)
and Unit B (worktree) therefore expose different isolation guarantees for ref/
object/config writes. **Fix:** define the observable isolation contract the
`WorkspacePort` must satisfy (independent object store, refs, config, index,
working tree) and require the equivalence conformance test named in the assumption
before either backend is trusted.

### N7 — Assignment reproducibility and mapping-as-evidence are unspecified
**Concern:** treatment assignment. AD-11/AD-12 conceal arm codes but say nothing
about how assignment is generated or whether the assignment mapping is durable
evidence. Unit A uses a frozen, recorded seed (reproducible, auditable); Unit B
uses `os.urandom` and retains only the opaque assignment IDs (non-reproducible).
Both conceal treatment and conform. Non-reproducible assignment breaks audit and
re-analysis and is not captured by the parity/effective-config hashes. **Fix:**
require the assignment procedure to be deterministic from a recorded seed and the
seed + arm mapping to be retained as access-separated evidence.

---

## Suggestions

- **S1 — Parquet dataset versioning granularity.** AD-05 says "versioned Parquet
  tables" but not whether reconciled runs *append* to a dataset version or produce
  immutable per-batch snapshots. Two teams will pick append vs. snapshot and
  produce incompatible analysis-table lifecycles. Consider stating the versioning
  and immutability granularity for Parquet datasets.
- **S2 — `measurement` vs field-level `unavailable`.** AD-16 offers only
  "metered or estimated," while impl adds per-field "unavailable." Clarify that
  `measurement` is per-entry and `unavailable` is per-field so the two vocabularies
  do not collide.

## What is solid

- Strict hexagonal dependency direction (AD-01) and the domain/adapter import ban
  are unambiguous and testable.
- Credential-domain separation by process (AD-09) with the explicit prohibited
  columns in impl §15.2 is strong once N1 is closed.
- Strata separation and no-pooling *intent* (AD-03/AD-07) is clear; only the
  enforcement mechanism (N3/N5) is missing.
- Append-only, no-inferred-success reconciliation (AD-05/AD-15) is a good
  fail-closed backbone; the gaps are precedence (B3) and completeness ownership
  (N2), not the philosophy.

---

## Summary Table

| ID | Concern | Divergent pair | Severity |
| --- | --- | --- | --- |
| B1 | Shared-data / hashing | JCS vs `json.dumps` canonicalization → different digests | Blocking |
| B2 | Owners / state | Control-only writer vs per-worker SQLite writers | Blocking |
| B3 | Evidence authority | Inspect-authoritative vs SDK-authoritative terminal status | Blocking |
| B4 | Retries | Ambiguous exposure = exposed vs pre-exposure (retryable) | Blocking |
| B5 | State-mutation | Lifecycle on run vs on attempt; no failure terminal | Blocking |
| N1 | Credentials | Read-only injected auth vs shared ambient host login | Non-Blocking |
| N2 | Telemetry | Trace-context propagation vs attempt_id stamping; unowned span-class set | Non-Blocking |
| N3 | Analysis | stratum_id + guard vs per-file datasets union-able in DuckDB | Non-Blocking |
| N4 | Cost / shape | Divergent unit vocab; zero vs unavailable for missing fields | Non-Blocking |
| N5 | Treatment comparability | Divergent engine→agent rendering; no parity freeze | Non-Blocking |
| N6 | Isolation | Clone vs worktree (shared object store) unequal isolation | Non-Blocking |
| N7 | Assignment | Seeded/reproducible+recorded vs urandom/non-reproducible | Non-Blocking |
| S1 | Analysis | Parquet append vs snapshot versioning | Suggestion |
| S2 | Cost | `measurement` vs field-level `unavailable` overlap | Suggestion |
