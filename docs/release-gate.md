# The bounded memrelay pipeline-seam release gate

`EV-ROUNDTRIP-MCP` is a single hermetic end-to-end fixture test supporting only
`CL-PIPELINE-SEAM`:

> `tests/integration/test_release_gate_roundtrip.py::test_release_gate_fixture_session_recalled_through_mcp`

A human runs it before cutting a release. A passing result supports the named synthetic fixture's
capture-to-MCP pipeline seam against the real embedded graph. It does not establish product
efficacy, safety, economics, production completeness, generalization, or cross-repository fitness.
A failing result means this bounded seam is not supported for the tested fixture.

## What the fixture supports

The fixture drives one synthetic **session** through the same product components used for capture,
ingest, and MCP recall, joining the two halves that ship on `main` but that no other test exercises
together:

1. **Capture.** A raw Copilot `events.jsonl` session → `run_observe` → the durable `Spool` at
   `<home>/spool/spool.db`. The namespace is **derived** from the session's git remote (the same
   resolution `memory_recall` uses), not hard-coded.
2. **Ingest.** The daemon's own `default_ingester_factory` **independently** recomputes that spool
   path and drains the episode into a real `MemoryEngine` backed by embedded **Ladybug**.
3. **Recall through the agent surface.** The ingested memory is retrieved **through the daemon +
   MCP `memory_recall` tool** — `DaemonClient` → daemon socket → real engine → the `mcp.format`
   renderer — i.e. the literal seam the agent calls, not a direct `engine.search`.

It then asserts, behaviorally (not smoke):

- the ingested session fact is present in the rendered **`## Memory Map`**;
- the `StubBackend` sentinels are **absent** (a silent fallback to the stub could never pass);
- `health` over the same socket reports `status == "ok"` and `backend == "ladybug"` (the real
  embedded graph answered);
- the fact is **scoped to the observe-derived namespace** — a foreign namespace recalls nothing.

Because capture and ingest resolve the spool path and namespace *independently*, a path or
record-shape drift surfaces here as an empty recall — the class of silent-no-recall bug a
`FakeSpool`/`StubBackend` test cannot catch.

## How to run it (headless, no keys)

The gate is fully hermetic and **keyless** — that is the "runs headless" guarantee. It needs no
API key, no network, and no external database. A deterministic in-process mock LLM stands in for
extraction, embeddings come from the real fastembed model (or a deterministic offline fallback when
the model cannot be downloaded), and the graph is embedded Ladybug on a temp dir; it never touches a
real `~/.memrelay`.

From a fresh checkout:

```bash
pip install -e ".[dev]"
python -m pytest tests/integration/test_release_gate_roundtrip.py -q
```

Expected: `1 passed`. No environment variables, secrets, or services are required.

## What a failure means

A red gate means the tested fixture's capture → spool → ingest → namespace derivation → daemon
transport → MCP renderer path is not supported. Triage from the failing assertion:

| Failing assertion | Most likely culprit |
| --- | --- |
| `result.appended` / `spool.pending()` | the observe/capture side or spool write |
| `episodes_ingested == 0` | spool-path or record-shape drift between capture and the ingester |
| empty / wrong `## Memory Map` | ingestion, recall, or the `mcp.format` renderer |
| `StubBackend` sentinel present | the daemon silently served the stub, not the real engine |
| `backend != "ladybug"` | the embedded graph backend did not come up |

Fix the regression — do not weaken the gate — before relying on this fixture evidence. See
[`RELEASING.md`](../RELEASING.md) for the surrounding release procedure.
