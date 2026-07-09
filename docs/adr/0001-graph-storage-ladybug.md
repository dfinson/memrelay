# ADR 0001 — Graph storage: replace archived KuzuDB with LadybugDB behind a Backend seam

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
risk. graphiti's *other* backends (Neo4j, FalkorDB) are servers — they break the embedded, zero-config
promise — so "just switch graphiti backend" is not a fix.

### Market gap (confirmed, 2026)

There is **no single pip-installable library** that offers *all three* of: (a) a truly **embedded**
graph (no server), (b) a **true bitemporal knowledge graph** (valid-time + transaction-time with
contradiction invalidation), and (c) **active maintenance**. graphiti supplies (b); its only embedded
storage (a) was Kuzu, which lost (c). Keeping (a)+(b) therefore requires restoring (c) at the storage
layer without giving up graphiti's brain.

## Decision

**Keep all of graphiti-core (the brain); replace only its dead storage driver: KuzuDB → LadybugDB,
behind a formal memrelay `Backend` seam + registry.**

**LadybugDB** (`pip install ladybug`, PyPI **0.18.0**, MIT, GitHub `LadybugDB/ladybug`) is the
**original Kuzu developers' maintained continuation** of the archived Kuzu codebase — Kuzu-API and
Cypher **drop-in compatible**. `import ladybug` exposes `Database` / `Connection` / `AsyncConnection`
identical to `kuzu`, runs Kuzu-dialect DDL/Cypher/FTS unchanged, and persists an embedded on-disk `.db`
with no server.

Key implementation choices:

1. **Do NOT fork/patch/vendor graphiti-core.** We inject our own driver via `Graphiti(graph_driver=...)`
   (graphiti already supports driver injection — it is how memrelay wired Kuzu). graphiti's brain runs
   untouched.

2. **Report `provider = GraphProvider.KUZU` from our Ladybug driver — deliberately.** `GraphProvider`
   is a closed enum, and graphiti's `search/search_utils.py` (and query helpers) have ~15+
   `driver.provider == GraphProvider.KUZU` branches emitting **Kuzu-dialect Cypher**, which Ladybug
   speaks identically. Reporting KUZU makes all of that reused verbatim. This is intentional coupling,
   documented here so it is not "corrected" later.

3. **`LadybugDriver` is standalone (`class LadybugDriver(GraphDriver)`), NOT a subclass of graphiti's
   `KuzuDriver`.** graphiti's `graphiti_core.driver.kuzu_driver` does a hard `import kuzu` at module top,
   so subclassing would force importing the archived package. Instead we mirror that driver near-verbatim
   (Database/AsyncConnection construction, `execute_query`, `session`, `close`, `setup_schema`, copied
   `SCHEMA_QUERIES`) and reuse graphiti's **provider-agnostic** `graphiti_core.driver.kuzu.operations.*`
   classes — verified to carry no `import kuzu` (they only emit query strings).

4. **Three graphiti↔driver integration deltas carry over / were discovered**, because Ladybug *is* Kuzu
   with a couple of tightened behaviours:
   - **Delta 1 (carried from E4):** set `driver._database = None` if unset (graphiti's `add_episode`
     compares `group_id` to `driver._database`, which the Kuzu/Ladybug driver never sets → `AttributeError`).
   - **Delta 2 (carried from E4, hardened here):** create graphiti's full-text indices at open time (the
     `CREATE_FTS_INDEX` DDL from `get_fulltext_indices(GraphProvider.KUZU)`), which graphiti 0.29.2 never
     wires into the driver, so search fails without them. Idempotent on re-open ("already exists" swallowed).
     This first requires Ladybug's **FTS extension** to be loaded, and the naive `INSTALL FTS; LOAD FTS;`
     path **fails on Linux CI** — so memrelay provisions the extension itself via a prefetch loader and only
     falls back to native `INSTALL FTS`. See "FTS extension provisioning (Delta 2 on Linux CI)" below.
   - **Delta 3 (new — a Ladybug/Kuzu parameter-strictness divergence found in this work):** graphiti's
     `KuzuDriver.execute_query` **strips `None`-valued parameters** and relies on Kuzu 0.11.3 treating a
     referenced-but-absent `$param` as NULL. **Ladybug 0.18.0 tightened this:** a query that references
     `$expired_at` while `expired_at` is missing from `parameters` raises `Parameter expired_at not found.`,
     but Ladybug happily binds Python `None` as SQL NULL. So our `LadybugDriver.execute_query` **keeps**
     None-valued params (bound as NULL — the same effective value as Kuzu's absent==NULL) and only drops
     graphiti's non-Cypher routing kwargs (`database_`, `routing_`). Ladybug tolerates extra/unused params,
     so passing the full set through is safe. This is the one place `LadybugDriver` deliberately deviates
     from the verbatim `KuzuDriver` mirror; it was caught by the real (non-mocked) backend-smoke roundtrip.

5. **A `Backend` seam + lazy registry** (mirrors the provider registry #70 and host-process registry #87):
   a `Backend` ABC (`id` + `async open_driver(cfg) -> GraphDriver`), a registry keyed on
   `cfg.graph.backend` with `@register`, `DEFAULT_BACKEND_ID = "ladybug"`, and `resolve_backend()`. The
   engine's construction seam calls `resolve_backend(cfg.graph.backend).open_driver(cfg)` instead of a
   hard `open_kuzu_driver(...)`. The swap lives **strictly below** `MemoryEngine`'s frozen public async
   API + wire shapes, which are byte-identical.

### Fork resolutions

- **D-1 (Kuzu fallback):** **Keep `KuzuBackend` registered** as a back-compat fallback, but **move `kuzu`
  out of the default dependencies** into an optional extra `[project.optional-dependencies].kuzu`. OOTB
  `pip install memrelay` pulls **Ladybug only**; the archived package is never installed unless a user
  opts into the `kuzu` extra and pins `backend = "kuzu"`.
- **D-2 (migration):** **No in-place migration.** A Kuzu 0.11.3-created `graph.db` does **not** open under
  Ladybug 0.18.0 (`RuntimeError: ... not a valid Lbug database file!`) — the storage magic diverged. Default
  `backend = "ladybug"` creates a **fresh** store; `"kuzu"` is still accepted (routes to `KuzuBackend`) so
  users with an existing Kuzu graph can keep reading it. memrelay is pre-release (no real users), so a fresh
  store is acceptable.
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
- **graphiti reuse is native-free:** importing `LadybugDriver` (and the 13 `graphiti_core.driver.kuzu.operations.*`
  modules it reuses) does **not** import the `kuzu` package (`'kuzu' not in sys.modules`). So a clean,
  kuzu-free install imports and runs fine.
- **graphiti-core does not force kuzu:** `graphiti-core==0.29.2` declares `kuzu>=0.11.3` **only** under its
  optional `kuzu` / `dev` extras — **not** as a core dependency. So depending on `graphiti-core` (no extras)
  plus `ladybug` genuinely yields a kuzu-free OOTB install.
- **Mutual exclusivity (drives the test/CI strategy):** `kuzu` and `ladybug` share one compiled pybind11
  extension and **cannot both load in a single process**, in either order (kuzu-first breaks Ladybug's C-API
  load; ladybug-first makes kuzu raise `generic_type: type "Database" is already registered`). This is a
  non-issue in production (one backend per process) but forces: (a) a **lazy** registry that imports only the
  selected backend's native lib, and (b) a test suite that loads **only one** native backend per process —
  so the Kuzu fallback is `@pytest.mark.kuzu` and runs in its **own** CI job (`pytest -m kuzu`) with the
  `kuzu` extra, while the main matrix runs Ladybug only.

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
   with an atomic write and a cache-hit fast path (~14.5 MB, fetched once). Two gotchas handled: (a) the CDN
   returns **HTTP 403** to the default `Python-urllib/x.y` User-Agent, so we send an ordinary UA; (b) the
   manylinux ABI tag is ambiguous, so we try platform candidates **`linux_amd64` then `linux_old_amd64`**
   (only the ABI-matching binary will load) — plus `win_amd64` / `osx_amd64` / `osx_arm64` for the other OSes.
2. **Load it offline** with `LOAD EXTENSION '<local path>'` (the authoritative offline-load form; its binder
   only checks that the file exists, so any local path loads — no network, no native TLS).
3. **Belt & fallback:** we also set `SSL_CERT_FILE` (from `certifi`) when unset, and if every prefetch
   candidate fails we fall back to the native `INSTALL FTS; LOAD FTS;`. The prefetch path is tried **first**
   because it bypasses the broken native downloader entirely and is the most robust.

The loader is **injected** — `apply_graphiti_deltas(driver, load_fts_extension=load_ladybug_fts_extension)` —
so `LadybugBackend` gets the hardened loader while `KuzuBackend` keeps the plain native one. This keeps the
OOTB promise real on Linux CI (and Alpine/musllinux): a clean install provisions FTS with no server and no
manual CA setup. Covered by `tests/unit/test_fts_extension.py` (16 native-free, no-network tests) and
exercised for real by the backend-smoke roundtrip in every CI matrix job.

## Consequences

**Positive**
- OOTB default is a **maintained**, embedded, zero-config graph again — the flagship promise is restored on
  a live dependency, cross-platform (a superset of Kuzu's platforms).
- graphiti's full brain (bitemporal + extraction + dedup + RRF) is **unchanged** — zero fork/patch/vendor.
- `MemoryEngine`'s public async API + wire shapes are **byte-identical** (only the construction seam and a
  couple of type hints changed).
- The default `pip install memrelay` no longer pulls the archived `kuzu` package.
- The `Backend` seam gives us a clean, tested place to add future storage backends without touching the
  engine or graphiti.

**Negative / trade-offs**
- We deliberately report `provider = GraphProvider.KUZU` for a non-Kuzu store — intentional coupling to
  graphiti's Kuzu-dialect branches (documented above so it is understood, not accidental).
- No in-place Kuzu→Ladybug data migration (incompatible storage magic); acceptable pre-release.
- The `LadybugDriver` mirrors graphiti's `KuzuDriver` by copy, so upstream `KuzuDriver` changes must be
  re-mirrored. Low risk: the Kuzu driver is deprecated/frozen upstream, and the copied surface is small.
- `kuzu` and `ladybug` cannot coexist in one process — a permanent constraint on how the fallback is tested.
- Ladybug's FTS extension is **fetched once over the network on first run** (then cached on disk and loaded
  offline). Fully offline first-run installs must pre-seed the cache dir (`MEMRELAY_EXTENSION_DIR`). The
  fetch is small (~14.5 MB), cached thereafter, and independent of Ladybug's own broken native downloader.

## Alternatives considered

- **Stay on archived Kuzu.** Rejected: end-of-life native dependency under our flagship default is the exact
  strategic risk #76 exists to remove.
- **Switch graphiti to Neo4j / FalkorDB.** Rejected: both are servers; they break the embedded, zero-config,
  no-server OOTB promise.
- **Drop graphiti; hand-roll storage on DuckDB + `sqlite-vec`.** Rejected **now**, but explicitly preserved as
  the **escape hatch behind this Backend seam**: DuckDB (embedded, maintained, columnar, cross-platform) plus a
  vector index (`sqlite-vec`/`vss`) could back a hand-rolled bitemporal fact store if Ladybug ever stalls.
  The cost is that we would then own the bitemporal fact model, extraction orchestration, dedup, contradiction
  invalidation, and RRF ourselves — i.e. re-implement graphiti's brain. The `Backend` seam is designed so this
  can be added as another `Backend` implementation without touching `MemoryEngine` or graphiti, which makes
  "keep graphiti + Ladybug now, hand-roll later if forced" the lowest-risk path.
