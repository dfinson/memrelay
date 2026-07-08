# E4 — Graphiti Memory Engine De-risk Notes

**Verdict: GO.** The riskiest assumption behind Epic E4 — that `graphiti-core` can run
memrelay's store + recall on an **embedded Kuzu** graph, **key-less by default** — holds
on the *actually installed* `graphiti-core==0.29.2` + `kuzu==0.11.3`. A live, hermetic
**note → recall roundtrip** works: `add_episode` extracts entities/edges with a
deterministic in-process mock LLM + real `fastembed`, and `search_(...RRF)` recalls them
by a *semantic* query — no network, temp Kuzu dir.

Per the same rule E0 used, this epic is written against the **installed** API, not
against `SPEC.md` (which is illustrative and predates the install). The install differs
from the SPEC in six concrete places (§3) and ships **two graphiti↔Kuzu bugs** that the
engine works around (§4). The Kuzu deprecation is now pinned + tracked (§6).

---

## 1. What was verified, and how

- **Environment:** Windows, CPython 3.12.10, clean venv, `pip install -e ".[dev]"` clean.
  Key deps: `graphiti-core 0.29.2`, `kuzu 0.11.3`, `fastembed 0.8.0` (+ transitive
  `neo4j 6.2.0`, a hint that graphiti's *default* backend is Neo4j).
- **Inspection first:** `inspect.signature` / `dir` / reading the installed package for
  every symbol below — evidence scripts (session artifacts, not committed):
  `files/inspect_graphiti.py`, `files/roundtrip_smoke.py`.
- **fastembed works offline after one download:** `TextEmbedding("BAAI/bge-small-en-v1.5")`
  auto-downloads a quantized ONNX build (`qdrant/bge-small-en-v1.5-onnx-q`, ~5 files, ~5s),
  embeds to **384-dim float32**. E0 already confirmed the wheels install on 3.11–3.13.
- **The gate** ([`tests/integration/test_engine_roundtrip.py`](../tests/integration/test_engine_roundtrip.py))
  proves note → recall + namespace isolation, hermetically, on temp Kuzu.

## 2. Real graphiti-core 0.29.2 API surface memrelay uses

```python
from graphiti_core import Graphiti
from graphiti_core.driver.kuzu_driver import KuzuDriver
from graphiti_core.nodes import EntityNode, EpisodeType          # message|json|text|fact_triple
from graphiti_core.llm_client.client import LLMClient, ModelSize # small|medium
from graphiti_core.llm_client.config import LLMConfig
from graphiti_core.embedder.client import EmbedderClient
from graphiti_core.cross_encoder.client import CrossEncoderClient
from graphiti_core.search.search_config_recipes import COMBINED_HYBRID_SEARCH_RRF

# Construction — ALL THREE clients must be injected or graphiti defaults to OpenAI:
g = Graphiti(graph_driver=KuzuDriver(db=path, max_concurrent_queries=1),
             llm_client=..., embedder=..., cross_encoder=...)

# Verified signatures:
# add_episode(name, episode_body, source_description, reference_time,
#             source=EpisodeType.message, group_id=None, ...) -> AddEpisodeResults
#     .episode (EpisodicNode), .nodes, .edges, .episodic_edges, .communities, ...
# search_(query, config, group_ids=None, center_node_uuid=None, ...) -> SearchResults
#     .nodes / .edges / .episodes  (+ *_reranker_scores)
# LLMClient._generate_response(self, messages: list[Message],
#     response_model: type[BaseModel] | None = None,
#     max_tokens: int = 16384, model_size: ModelSize = ModelSize.medium) -> dict
# EmbedderClient.create(input_data) -> list[float]; create_batch(list[str]) -> list[list[float]]
# CrossEncoderClient.rank(query: str, passages: list[str]) -> list[tuple[str, float]]
# EntityNode.get_by_uuid(driver, uuid) -> EntityNode   (used by detail())
```

## 3. API deltas vs SPEC (SPEC is illustrative / pre-install)

| # | Topic | SPEC | Reality (0.29.2) |
|---|-------|------|------------------|
| 1 | Graphiti ctor | implicit Kuzu, no clients | Must pass `graph_driver=KuzuDriver(db=...)` **and** `llm_client` + `embedder` + `cross_encoder`, or graphiti silently defaults to `OpenAIClient`/`OpenAIEmbedder`/`OpenAIRerankerClient` (all need `OPENAI_API_KEY`). |
| 2 | `group_id` + Kuzu | per-namespace group_id | **BUG (§4.1):** `add_episode` reads `self.driver._database`, which `KuzuDriver` never sets → `AttributeError`. |
| 3 | full-text indexes | assumed present | **BUG (§4.2):** the FTS indexes are never created; `build_indices_and_constraints()` is a Kuzu no-op. |
| 4 | `_generate_response` args | `messages: list[dict]`, `model_size: str` | `messages: list[Message]` (pydantic role/content), `model_size: ModelSize` enum |
| 5 | `detail()` node fetch | `driver.entity_node_ops.get_by_uuid(uuid)` | `EntityNode.get_by_uuid(driver, uuid)` classmethod |
| 6 | recall recipe | `..._CROSS_ENCODER` | cross-encoder recipes → `OpenAIRerankerClient` (needs key). Use **RRF** recipes (`COMBINED/NODE/EDGE_HYBRID_SEARCH_RRF`) for key-less recall; semantic recall is carried by **cosine over embeddings**. |

## 4. The two graphiti↔Kuzu bugs the engine works around

Both are encapsulated in [`engine/kuzu_backend.py`](../src/memrelay/engine/kuzu_backend.py).

**4.1 `driver._database` (`AttributeError`).** `Graphiti.add_episode` runs
`if group_id != self.driver._database: self.driver = self.driver.clone(database=group_id)`
— a Neo4j multi-database concept. `KuzuDriver` never sets `_database`, and its `clone()`
is a no-op returning `self`. **Fix:** set `driver._database = None`. The "clone per
group_id" branch becomes a safe no-op and `group_id` correctly degrades to an in-database
property filter over a single Kuzu file — exactly SPEC §5.1's "group_id = namespace".
(Verified by the namespace-isolation test: a note in `proj-a` is invisible to `proj-b`.)

**4.2 Full-text indexes are never created.** `Graphiti.build_indices_and_constraints()`
delegates to `KuzuDriver.build_indices_and_constraints()`, which is a **no-op**;
`setup_schema()` (run in the driver ctor) creates only node/rel tables. The code that
actually issues `CREATE_FTS_INDEX` (`KuzuGraphMaintenanceOperations`) is **not wired**
into the driver in 0.29.2, so `add_episode`/search hit
`Binder exception: ... index edge_name_and_fact`. **Fix:** at open time run
`INSTALL FTS; LOAD FTS;` then the four DDLs from `get_fulltext_indices(GraphProvider.KUZU)`.
Idempotent on re-open (the "already exists" error is swallowed). Note Kuzu FTS is a
**static snapshot** — fresh writes aren't BM25-searchable until the index is recreated;
fine for recall (cosine carries it), a follow-up for high-volume ingest.

## 5. Engine shape (this PR — `src/memrelay/engine/**` + one additive `config.py` change)

- `kuzu_backend.py` — `open_kuzu_driver()`: the two §4 workarounds, READ_WRITE once.
- `embedder.py` — `LocalEmbedder(EmbedderClient)` over fastembed `bge-small-en-v1.5`
  (384-dim), cache `~/.memrelay/models`; CPU embed offloaded to a thread.
- `llm/strategy.py` — `LLMStrategy` seam + `select_llm_client(cfg)`: picks
  `borrow-host | byo-key | local` from `cfg.llm.strategy` with a fallback chain.
- `llm/borrow_host.py` — `BorrowHostLLMClient`: schema-in-prompt + robust JSON parse +
  retries; host call isolated behind the fakeable `HostProcess` protocol.
  `CopilotHostProcess` (real subprocess) is **best-effort, non-gating**.
- `llm/byo_key.py` — `ByoKeyLLMClient`: lazy wrapper over graphiti's `OpenAIClient`
  (native JSON); **never reads the key or hits the network at construction** (CI-safe).
- `llm/local.py` — stub raising `NotImplementedError` (E4-S7 / #64, deferred).
- `graphiti.py` — `MemoryEngine` with the shared async contract
  (`note` / `search` / `detail` / `health` + `from_config` + `close`), returning plain
  serializable dicts/strings **shaped to the merged daemon wire schema** in
  `src/memrelay/mcp/format.py` (so the daemon's `StubBackend` → `MemoryEngine` swap is
  one line, no adapter): `search()` → `{"nodes":[{uuid,name,summary}], "edges":[{uuid,
  name,source_node_uuid,target_node_uuid,fact}], "scores":[…]}` with `scores` aligned
  position-for-position with `nodes` (`format_as_map` gates on non-empty `nodes` and pairs
  `scores[i]` with `nodes[i]`); `detail()` → `{"node":{…}|None, "connected_edges":[…],
  "episodes":[…]}` (unknown uuid → `node=None`, which `format_detail` renders as
  "Entity not found."). The gate imports the *real* `format_as_map` / `format_detail` and
  asserts they render the engine's live output, permanently pinning the seam against drift.
  Injects a key-less `PassthroughCrossEncoder` (RRF never reranks, but it stops graphiti
  defaulting to the OpenAI reranker).
- `config.py` (additive): optional `provider` / `api_key_env` / `model` on `LLMConfig`
  and `api_key_env` on `EmbeddingsConfig`, needed to *configure* byo-key. Defaults `None`,
  so the key-less path is unchanged.

## 6. Kuzu is deprecated in graphiti-core 0.29.2 — now pinned + tracked

`KuzuDriver.__init__` emits a `DeprecationWarning`: *"The Kuzu backend is deprecated and
will be removed in a future release … Migrate to Neo4j or FalkorDB."* graphiti-core does
**not** upper-bound Kuzu (`Requires-Dist: kuzu>=0.11.3`), so nothing stops a future
`pip install` from resolving a graphiti release that drops Kuzu and breaking the epic.

**Decision (manager-approved): proceed on Kuzu now** — it is SPEC's zero-server default, it
works today, and the backend seam (`kuzu_backend.py`) keeps any future swap contained.
Under a **scoped exception** to the "engine doesn't touch `pyproject.toml`" rule (these two
dependency lines only — no other wave-2 session edits `pyproject.toml`, so zero conflict),
this PR pins:

- **`graphiti-core>=0.29,<0.30`** — for reproducible CI, mirroring E0's
  `traceforge-toolkit>=0.1,<0.2` precedent. This is the determinism lever.
- **`kuzu>=0.11.3`** — floor aligned to graphiti-core 0.29.2's *own* requirement
  (`Requires-Dist: kuzu>=0.11.3`); the previous `kuzu>=0.4` sat *below* graphiti's floor and
  was misleading. Intentionally **not** upper-bounded — capping graphiti-core already gates
  the API surface, and over-constraining Kuzu buys little.

The upstream deprecation itself is tracked for the E0 / packaging owners as **issue #76**
(*"graphiti-core deprecated the embedded Kuzu backend — revisit default-backend strategy
before GA"*); if Kuzu support is ever actually removed, the Neo4j/FalkorDB migration
decision lands there and `kuzu_backend.py` is the single file it would touch. **Not a
blocker for E4.**

## 7. The gate (SPEC §12 Step 2)

`tests/integration/test_engine_roundtrip.py` notes a fact → recalls it by a semantic query
→ asserts it comes back, using a deterministic `MockLLMClient` (implements the real
`LLMClient` ABC, returns schema-conformant JSON keyed by `response_model.__name__`) + the
**real** `LocalEmbedder`, with a deterministic offline **fallback embedder**
(hashed bag-of-words, 384-dim) if the model download is unavailable in CI. Temp Kuzu via
`tmp_path`; never a real `~/.memrelay/graph.db`. Runs on Linux 3.11 / 3.12 / 3.13.
