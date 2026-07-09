# Retrieval quality eval (precision@k) — E11-S4 / #21

An offline, deterministic **precision@k** harness on seeded synthetic sessions. It measures
memrelay's recall quality over the real `MemoryEngine.search` path and backs a **CI regression
gate**, so a change that quietly degrades ranking fails CI instead of shipping.

## What precision@k here actually measures

For each labeled gold query we run the **real** `engine.search(query, namespace)` and check
whether the query's relevant nodes land in the top-k of the **structured** result
(`{"nodes": [...], ...}` — never the human-readable rendering). We report, macro-averaged over
all gold queries:

- `p@1`, `p@3`, `p@5` — `|relevant ∩ top-k| / k`; and
- `hit@1`, `hit@3`, `hit@5` — whether any relevant node is in the top-k.

Node identity is the entity **name**, never the random per-run UUID.

This guards **memrelay's retrieval/ranking wiring**: the `COMBINED_HYBRID_SEARCH_RRF` recipe,
RRF fusion of the BM25 full-text and vector-similarity channels, `group_ids` namespace
filtering, and the `prefer_repo` boost. It is deliberately **not** a measurement of the
production embedding model's semantic quality or of real-LLM extraction quality — those drift
across model/library versions and need network, which would make a flaky gate. To stay a
*stable regression* gate we substitute deterministic doubles for exactly those two layers:

- **extraction** → an in-process mock LLM (fixed, schema-conformant responses);
- **embeddings** → a fixed hashed bag-of-words offline embedder (`_embedder.py`), injected via
  `MemoryEngine.from_config(embedder=...)`, which bypasses fastembed entirely.

Everything else is the real shipped engine (embedded Ladybug graph, real `note` → `search`).

## Why the baseline is stable run-to-run and machine-to-machine

- Fixed seed → byte-identical synthetic sessions + query set (`_generator.py`; asserted).
- Deterministic mock LLM → identical entity/edge extraction.
- Harness-owned deterministic embedder → identical vectors (no fastembed/network variance).
- Deterministic ingestion order + fixed query set → identical graph and identical search inputs.
- Distinctive, invented gold vocabulary → relevant nodes rank with a clear score gap (no
  boundary ties), and `test_precision_at_k.py` runs the whole eval twice to assert identical
  metrics.

## Files

| File | Purpose |
| --- | --- |
| `_generator.py` | Seeded synthetic-session + labeled-gold-query generator (stdlib). |
| `_embedder.py` | Deterministic offline embedder (hashed bag-of-words, 384-d). |
| `_precision.py` | precision@k / hit@k arithmetic (stdlib). |
| `_harness.py` | Builds the real engine with the deterministic doubles, notes facts, searches, computes metrics. |
| `baseline.json` | Checked-in baseline (measured metrics + margin + reproducibility metadata). |
| `generate_baseline.py` | `--write` regenerates the baseline; `--check` enforces the gate. |
| `test_precision_at_k.py` | The gate + determinism/byte-stability tests, also run in the normal suite. |

## Commands

Run from a memrelay checkout with `memrelay` importable. In the shared dev env, put this
worktree's `src` on `PYTHONPATH` (never `pip install -e .`):

```powershell
$env:PYTHONPATH = (Resolve-Path .\src).Path

python tests/eval/generate_baseline.py --check   # enforce the regression gate (CI does this)
python tests/eval/generate_baseline.py --write    # regenerate baseline.json after an intended change
python -m pytest tests/eval -q                     # the same gate + determinism tests via pytest
```

## CI gate

The `retrieval-eval` job in `.github/workflows/ci.yml` runs
`python tests/eval/generate_baseline.py --check` (offline, no keys) and fails if any enforced
metric drops below `baseline - margin`. The pytest above enforces the same threshold inside the
normal test suite.

## Tuning / regenerating

If you intentionally change ranking (or the corpus knobs in `_harness.py`:
`DEFAULT_SEED` / `DEFAULT_N_TOPICS` / `DEFAULT_FACTS_PER_TOPIC`), re-run `--write` and commit the
updated `baseline.json` in the same PR, with a note on why the numbers moved.
