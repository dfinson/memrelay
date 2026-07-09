# ADR 0001 — Graph backends: embedded LadybugDB default + cloud opt-ins; archived KuzuDB dropped

- **Status:** Accepted
- **Date:** 2026 (Epic E7 strategic de-risk)
- **Issue:** [#76](https://github.com/dfinson/memrelay/issues/76)
- **Supersedes:** the E4 decision to ship on embedded Kuzu (see `docs/e4-engine-notes.md` §6, which
  pinned `graphiti-core<0.30` and tracked the Kuzu deprecation here).

## Context

memrelay's memory engine (`MemoryEngine`) gets its whole value from **graphiti-core**: a bitemporal
fact model, LLM entity/edge extraction, entity dedup, contradiction invalidation, and RRF hybrid
retrieval. That is hard-to-replicate IP and we want to keep all of it.

graphiti's only *embedded* (in-process, zero-server) storage driver is **KuzuDB**. memrelay's
out-of-the-box (OOTB) promise is an embedded, zero-config, zero-key, no-server, cross-platform graph
that "just works" on `pip install memrelay`. Kuzu was what made that promise real.

**The problem:** KuzuDB is now **archived**. `kuzu==0.11.3` is terminal, and graphiti-core 0.29.2
deprecated its Kuzu driver (its `KuzuDriver.__init__` emits a `DeprecationWarning`). Shipping our
flagship OOTB default on an end-of-life, unmaintained native dependency is an unacceptable strategic
risk. graphiti's *other* backends (Neo4j, FalkorDB, Neptune) are servers — they break the embedded,
zero-config promise — so "just switch graphiti backend" is not, by itself, a fix for the default.

### Market gap (confirmed, 2026)

There is **no single pip-installable library** that offers *all three* of: (a) a truly **embedded**
graph (no server), (b) a **true bitemporal knowledge graph** (valid-time + transaction-time with
contradiction invalidation), and (c) **active maintenance**. graphiti supplies (b); its only embedded
storage (a) was Kuzu, which lost (c). Keeping (a)+(b) therefore requires restoring (c) at the storage
layer without giving up graphiti's brain.

## Decision

**Keep all of graphiti-core (the brain). Make the embedded default a maintained store — LadybugDB —
and expose graphiti's server backends as configurable opt-ins, all behind a formal memrelay `Backend`
seam + lazy registry. Drop archived KuzuDB entirely.**

memrelay is **pre-alpha with no real users**, so there is no back-compat or migration obligation:
`kuzu` is removed root-and-branch — no `KuzuBackend`, no `kuzu` dependency/extra, no `kuzu` in any
test or CI job. This is a clean break, not a deprecation.

### Embedded default — LadybugDB (`backend = "ladybug"`, OOTB)

**LadybugDB** (`pip install ladybug`, PyPI **0.18.0**, MIT, GitHub `LadybugDB/ladybug`) is the
**original Kuzu developers' maintained continuation** of the archived Kuzu codebase — Kuzu-API and
Cypher **drop-in compatible**. `import ladybug` exposes `Database` / `Connection` / `AsyncConnection`
identical to `kuzu`, runs Kuzu-dialect DDL/Cypher/FTS unchanged, and persists an embedded on-disk `.db`
with no server. This is the **only** backend smoke-tested end-to-end and the only one in the default
dependency set.

Key implementation choices for the embedded driver:

1. **Do NOT fork/patch/vendor graphiti-core.** We inject our own driver via `Graphiti(graph_driver=...)`
   (graphiti already supports driver injection). graphiti's brain runs untouched.

2. **Report `provider = GraphProvider.KUZU` from our Ladybug driver — deliberately.** `GraphProvider`
   is a closed enum, and graphiti's `search/search_utils.py` (and query helpers) have ~15+
   `driver.provider == GraphProvider.KUZU` branches emitting **Kuzu-dialect Cypher**, which Ladybug
   speaks identically. Reporting KUZU makes all of that reused verbatim. Intentional coupling,
   documented here so it is not "corrected" later.

3. **`LadybugDriver` is standalone (`class LadybugDriver(GraphDriver)`), NOT a subclass of graphiti's
   `KuzuDriver`.** graphiti's `graphiti_core.driver.kuzu_driver` does a hard `import kuzu` at module top,
   so subclassing would force importing the archived package. Instead we mirror that driver near-verbatim
   (Database/AsyncConnection construction, `execute_query`, `session`, `close`, `setup_schema`, copied
   `SCHEMA_QUERIES`) and reuse graphiti's **provider-agnostic** `graphiti_core.driver.kuzu.operations.*`
   classes — verified to carry no `import kuzu` (they only emit query strings).

4. **Three graphiti↔driver integration deltas**, applied **only** to the Ladybug (KUZU-provider) driver:
   - **Delta 1 (carried from E4):** set `driver._database = None` if unset (graphiti's `add_episode`
     compares `group_id` to `driver._database`, which the Kuzu/Ladybug driver never sets → `AttributeError`).
   - **Delta 2 (carried from E4, hardened here):** create graphiti's full-text indices at open time (the
     `CREATE_FTS_INDEX` DDL from `get_fulltext_indices(GraphProvider.KUZU)`), which graphiti 0.29.2 never
     wires into the driver, so search fails without them. Idempotent on re-open ("already exists" swallowed).
     This first requires Ladybug's **FTS extension** to be loaded, and the naive `INSTALL FTS; LOAD FTS;`
     path **fails on Linux CI** — so memrelay provisions the extension itself via a prefetch loader and only
     falls back to native `INSTALL FTS`. See "FTS extension provisioning" below.
   - **Delta 3 (a Ladybug/Kuzu parameter-strictness divergence found in this work):** graphiti's
     `KuzuDriver.execute_query` **strips `None`-valued parameters** and relies on Kuzu 0.11.3 treating a
     referenced-but-absent `$param` as NULL. **Ladybug 0.18.0 tightened this:** a query that references
     `$expired_at` while `expired_at` is missing from `parameters` raises `Parameter expired_at not found.`,
     but Ladybug binds Python `None` as SQL NULL. So our `LadybugDriver.execute_query` **keeps** None-valued
     params (bound as NULL — the same effective value as Kuzu's absent==NULL) and only drops graphiti's
     non-Cypher routing kwargs (`database_`, `routing_`). Caught by the real backend-smoke roundtrip.

### Cloud opt-ins — Neo4j / FalkorDB / Neptune (config-selected)

The three server backends are exposed as **thin adapters** over graphiti-core's **own native drivers**.
Each adapter reads `graph.connection`, constructs graphiti's driver, and returns it — nothing more:

- **`backend = "neo4j"`** → `graphiti_core.driver.neo4j_driver.Neo4jDriver(uri, user, password, database='neo4j')`.
- **`backend = "falkordb"`** → `graphiti_core.driver.falkordb_driver.FalkorDriver(host, port=6379, username, password, database='default_db')`.
- **`backend = "neptune"`** → `graphiti_core.driver.neptune_driver.NeptuneDriver(host, aoss_host, port=8182, aoss_port=443)`
  (`host` must be a `neptune-db://` / `neptune-graph://` endpoint; `aoss_host` is the OpenSearch endpoint).

These drivers are **pure graphiti** — they self-build their own indices/constraints — so they apply
**none** of the Ladybug/KUZU-provider deltas above. A cloud backend selected with a required
connection field missing **fails loud** with an actionable `ValueError` (mirroring #87's fail-loud
registry pattern) *before* importing any driver. They are **wiring-tested, not live-tested**: CI never
stands up a server; unit tests assert config→constructor arg mapping hermetically and that the real
graphiti driver modules import where the extras are installed.

### The `Backend` seam + lazy registry (its real justification: cloud opt-ins)

A `Backend` ABC (`id` + `async open_driver(cfg) -> GraphDriver`) with a **lazy** registry keyed on
`cfg.graph.backend` (`@register`, `DEFAULT_BACKEND_ID = "ladybug"`, `known_backends()`,
`resolve_backend()`). The engine's construction seam calls
`resolve_backend(cfg.graph.backend).open_driver(cfg)`; the swap lives **strictly below**
`MemoryEngine`'s frozen public async API + wire shapes, which are byte-identical.

**Why the registry is lazy** (unlike the eager provider registry #70): the embedded default pulls a
compiled native extension (`ladybug`), and **each cloud backend module hard-imports its own heavy
client stack at module top** — `falkordb`; `boto3` / `opensearch-py` / `langchain-aws` for Neptune —
that a default `pip install memrelay` never installs. `resolve_backend(id)` imports **only** the
selected backend's module, so an OOTB (Ladybug) install never needs any cloud client library, and the
static `id → module` map lets `known_backends()` answer without importing anything.

### Fork resolutions

- **D-1 (kuzu):** **Dropped entirely** (pre-alpha; no users). No `KuzuBackend`, no `kuzu` dependency,
  extra, marker, test, or CI job. The Backend seam is retained — its justification is now the cloud
  opt-ins above, not a kuzu fallback.
- **D-2 (migration):** **None, and moot.** memrelay is pre-alpha (no stores to migrate); independently,
  a Kuzu 0.11.3-created `graph.db` does **not** open under Ladybug 0.18.0
  (`RuntimeError: ... not a valid Lbug database file!`) — the storage magic diverged — so an in-place
  Kuzu→Ladybug migration was never viable anyway. Default `backend = "ladybug"` creates a fresh store.
- **D-3 (driver construction):** standalone `LadybugDriver` on the async path
  `ladybug.Database(path)` → `ladybug.AsyncConnection(db, max_concurrent_queries=1)` →
  `await client.execute(cypher, parameters=...)` → `.rows_as_dict()` — proven with a real on-disk roundtrip.
- **D-4 (Backend protocol shape):** minimal — `id` + `async open_driver(cfg) -> GraphDriver`. Everything
  the engine uses at runtime (`provider`, `execute_query`, `close`, `EntityNode.get_by_uuid`) already lives
  on the returned driver, so the seam needs nothing more.

## Empirical findings (reproduced against primary artifacts in this work)

- **Install / API parity:** `pip install ladybug` → 0.18.0 (MIT). `import ladybug` exposes
  `Database` / `Connection` / `AsyncConnection` identical to `kuzu`. Wheels span cp310–cp314 on Windows
  (amd64 **and arm64**), macOS (arm64 + x86_64), manylinux (x86_64 + aarch64), and **musllinux (Alpine)** —
  a **superset** of Kuzu's coverage, so OOTB install works across memrelay's `>=3.11,<3.14` matrix.
- **Real roundtrip:** Kuzu-dialect DDL (`CREATE NODE TABLE ... FLOAT[] ... TIMESTAMP ... valid_at/invalid_at`),
  `timestamp(...)`, `FLOAT[]` params, `group_id` filtering, and FTS (`INSTALL FTS;` / `LOAD FTS;` /
  `CREATE_FTS_INDEX`) all run unchanged; an embedded `.db` persists and reopens from disk with no server.
- **graphiti reuse is native-free:** importing `LadybugDriver` (and the `graphiti_core.driver.kuzu.operations.*`
  modules it reuses) does **not** import the `kuzu` package (`'kuzu' not in sys.modules`). A clean, kuzu-free
  install imports and runs fine.
- **graphiti-core does not force kuzu:** `graphiti-core==0.29.2` declares `kuzu>=0.11.3` **only** under its
  optional extras — **not** as a core dependency. Depending on `graphiti-core` (no extras) plus `ladybug`
  genuinely yields a kuzu-free OOTB install.
- **Cloud client footprint:** `neo4j>=5.26.0` is an **unconditional** graphiti-core dependency, so the
  Neo4j opt-in needs **no extra** (config + a running server suffice) and `graphiti_core.driver.neo4j_driver`
  always imports. FalkorDB and Neptune driver modules hard-import their client libs at module top, so those
  opt-ins ship as extras (`falkordb`; `boto3`/`langchain-aws`/`opensearch-py`) and are imported **lazily**
  inside `open_driver`. Importing the `graphiti_core.driver` package pulls only the (always-present) neo4j
  client — which is why the cloud wiring tests can inject fake driver modules without those libs.
- **Driver providers (verified, construction-free class attrs):** `Neo4jDriver.provider == GraphProvider.NEO4J`,
  `FalkorDriver.provider == GraphProvider.FALKORDB`, `NeptuneDriver.provider == GraphProvider.NEPTUNE`.

## FTS extension provisioning (Delta 2 on Linux CI)

Delta 2 needs Ladybug's **FTS extension** loaded before `CREATE_FTS_INDEX` will bind. Ladybug ships FTS as a
**downloadable** extension (never statically bundled): the native `INSTALL FTS;` fetches
`libfts.lbug_extension` from `extension.ladybugdb.com` over TLS, then `LOAD FTS;` loads it. This works locally
on Windows/macOS but **fails on the Linux CI runners** — the manylinux wheel's statically-linked OpenSSL
cannot verify the CDN's certificate chain, so `INSTALL FTS;` raises an SSL error, `QUERY_FTS_INDEX` stays
undefined, and graphiti's hybrid search breaks. This was caught by the backend-smoke job (all three Linux
matrix jobs red, while the same code passed locally on Windows).

**Fix — prefetch in Python, then `LOAD EXTENSION '<path>'`** (`engine/backends/_fts_extension.py`):

1. **Download the extension ourselves** with Python's `urllib` + the **`certifi`** CA bundle (added as an
   explicit dependency), into a cache dir (`MEMRELAY_EXTENSION_DIR`, else `~/.memrelay/extensions/ladybug-<ver>/<plat>/`),
   with an atomic write and a cache-hit fast path (fetched once). Two gotchas handled: (a) the CDN returns
   **HTTP 403** to the default `Python-urllib/x.y` User-Agent, so we send an ordinary UA; (b) the manylinux
   ABI tag is ambiguous, so we try platform candidates **`linux_amd64` then `linux_old_amd64`** (only the
   ABI-matching binary will load) — plus `win_amd64` / `osx_amd64` / `osx_arm64` for the other OSes.
2. **Load it offline** with `LOAD EXTENSION '<local path>'` (the offline-load form; its binder only checks
   that the file exists, so any local path loads — no network, no native TLS).
3. **Belt & fallback:** we also set `SSL_CERT_FILE` (from `certifi`) when unset, and if every prefetch
   candidate fails we fall back to the native `INSTALL FTS; LOAD FTS;`. The prefetch path is tried **first**.

The loader is **injected** — `apply_graphiti_deltas(driver, load_fts_extension=load_ladybug_fts_extension)` —
so only `LadybugBackend` uses the hardened loader. This keeps the OOTB promise real on Linux CI (and
Alpine/musllinux): a clean install provisions FTS with no server and no manual CA setup. Covered by
`tests/unit/test_fts_extension.py` (native-free, no-network tests) and exercised for real by the
backend-smoke roundtrip in every CI matrix job.

## Consequences

**Positive**
- OOTB default is a **maintained**, embedded, zero-config graph again — the flagship promise is restored on
  a live dependency, cross-platform (a superset of Kuzu's platforms).
- graphiti's full brain (bitemporal + extraction + dedup + RRF) is **unchanged** — zero fork/patch/vendor.
- `MemoryEngine`'s public async API + wire shapes are **byte-identical** (only the construction seam and a
  couple of type hints changed).
- The default `pip install memrelay` never pulls the archived `kuzu` package **nor** any cloud client lib.
- The `Backend` seam gives operators three production-grade server backends (Neo4j/FalkorDB/Neptune) as
  config-only opt-ins, and gives us a clean, tested place to add future storage backends.

**Negative / trade-offs**
- We deliberately report `provider = GraphProvider.KUZU` for a non-Kuzu store — intentional coupling to
  graphiti's Kuzu-dialect branches (documented above so it is understood, not accidental).
- The `LadybugDriver` mirrors graphiti's `KuzuDriver` by copy, so upstream `KuzuDriver` changes must be
  re-mirrored. Low risk: the Kuzu driver is deprecated/frozen upstream, and the copied surface is small.
- Ladybug's FTS extension is **fetched once over the network on first run** (then cached on disk and loaded
  offline). Fully offline first-run installs must pre-seed the cache dir (`MEMRELAY_EXTENSION_DIR`).
- The cloud opt-ins are **wiring-tested only** — CI proves config→driver arg mapping and that the driver
  modules import, but not a live end-to-end roundtrip against a real Neo4j/FalkorDB/Neptune server.

## Alternatives considered

- **Stay on archived Kuzu.** Rejected: end-of-life native dependency under our flagship default is the exact
  strategic risk #76 exists to remove.
- **Make a cloud server the default.** Rejected: Neo4j/FalkorDB/Neptune are servers; they break the embedded,
  zero-config, no-server OOTB promise. They belong as opt-ins, which is exactly what they now are.
- **Drop graphiti; hand-roll storage on DuckDB + `sqlite-vec`.** Rejected **now**, but explicitly preserved as
  a **future embedded escape hatch reachable by adding one `Backend` module**: DuckDB (embedded, maintained,
  columnar, cross-platform) plus a vector index (`sqlite-vec`/`vss`) could back a hand-rolled bitemporal fact
  store if Ladybug ever stalls. The cost is that we would then own the bitemporal fact model, extraction
  orchestration, dedup, contradiction invalidation, and RRF ourselves — i.e. re-implement graphiti's brain.
  The `Backend` seam is designed so this can be added without touching `MemoryEngine` or graphiti, which makes
  "keep graphiti + Ladybug now, hand-roll later if forced" the lowest-risk path.
