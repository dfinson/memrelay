"""THE OOTB GUARANTEE (#76): a real note -> recall roundtrip on the DEFAULT backend.

This is the out-of-the-box promise made executable: with **no** ``backend`` set in
config, a clean install must store and recall on an embedded, on-disk graph with
zero config, zero keys, and no server — cross-platform. It proves the Backend seam
resolves ``cfg.graph.backend`` to LadybugDB by default and that graphiti's full brain
(bitemporal facts + LLM extraction + dedup + RRF hybrid retrieval) works end-to-end
over it.

Only the *storage* is real (embedded Ladybug in ``tmp_path``); the LLM is the
deterministic in-process mock and the embedder is the real/offline-fallback one, both
from ``conftest.py`` — so there is no network, no API key, and never a real
``~/.memrelay/graph.db``.

Deliberately **unmarked** (unlike the ``integration``-marked daemon/ingest tests): the
OOTB guarantee must run in *every* CI matrix job (py3.11/3.12/3.13), even one invoked
with ``-m "not integration"``. It is the sibling of ``test_engine_roundtrip.py`` but
pins the *default* resolution specifically.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from memrelay.config import load_config
from memrelay.engine.backends import DEFAULT_BACKEND_ID
from memrelay.engine.graphiti import MemoryEngine
from memrelay.mcp.format import format_as_map

NAMESPACE = "proj-ootb"
FACT = "memrelay stores its persistent agent memory in an embedded Ladybug graph database."
RECALL_QUERY = "which graph database does memrelay use for memory"


def _default_config(tmp_path: Path):
    """Hermetic config that sets **only** the graph path — backend is left to default.

    ``environ={}`` + an absolute ``home``/``path`` isolate this from the caller's real
    home, ``MEMRELAY_*`` and ``XDG_*``. Crucially we do **not** pass ``backend``: the
    engine must resolve the OOTB default (Ladybug) on its own.
    """
    graph_path = tmp_path / "graph.db"
    cfg = load_config(environ={}, home=str(tmp_path), graph={"path": str(graph_path)})
    # The default really is Ladybug (guards against a silent default flip regressing OOTB).
    assert cfg.graph.backend == DEFAULT_BACKEND_ID == "ladybug"
    assert cfg.graph_path == graph_path.resolve()
    return cfg


def test_default_backend_note_recall_roundtrip(tmp_path, gate_embedder, mock_llm_factory) -> None:
    async def scenario() -> None:
        cfg = _default_config(tmp_path)
        engine = await MemoryEngine.from_config(
            cfg,
            llm_client=mock_llm_factory(["memrelay", "Ladybug"]),
            embedder=gate_embedder,
        )
        try:
            # store
            note_id = await engine.note(FACT, namespace=NAMESPACE, repo="memrelay")
            assert isinstance(note_id, str) and note_id, "note() must return an id"

            # recall by a *semantic* query -> the frozen {nodes, edges, scores} wire shape
            results = await engine.search(RECALL_QUERY, namespace=NAMESPACE)
            assert set(results) == {"nodes", "edges", "scores"}, f"bad shape: {results!r}"
            assert results["nodes"], "recall returned no nodes"
            assert len(results["scores"]) == len(results["nodes"]), "scores must align with nodes"
            blob = " ".join(
                f"{n.get('name') or ''} {n.get('summary') or ''}" for n in results["nodes"]
            )
            blob += " " + " ".join(
                f"{e.get('name') or ''} {e.get('fact') or ''}" for e in results["edges"]
            )
            assert "ladybug" in blob.lower(), f"expected the noted fact to be recalled: {results!r}"

            # the daemon formatter renders the same shape (pins the seam)
            rendered = format_as_map(results)
            assert rendered != "No relevant memories found.", "formatter saw an empty map"
            assert "ladybug" in rendered.lower(), f"formatter dropped the fact:\n{rendered}"

            # detail() on a hit returns the daemon detail schema
            node_hit = results["nodes"][0]
            detail = await engine.detail(node_hit["uuid"], namespace=NAMESPACE)
            assert set(detail) == {"node", "connected_edges", "episodes"}
            assert detail["node"] is not None and detail["node"]["uuid"] == node_hit["uuid"]

            # detail() on an unknown uuid degrades gracefully (no exception)
            missing = await engine.detail(
                "00000000-0000-0000-0000-000000000000", namespace=NAMESPACE
            )
            assert missing["node"] is None

            # health reports the default backend the seam actually resolved
            health = await engine.health()
            assert health["status"] == "ok"
            assert health["backend"] == "ladybug"
        finally:
            await engine.close()

    asyncio.run(scenario())
