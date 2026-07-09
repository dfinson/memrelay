"""Kuzu fallback proof (#76, fork D-1): the archived backend still works when selected.

memrelay keeps ``KuzuBackend`` registered so anyone with an existing Kuzu ``graph.db``
can still open it by pinning ``backend = "kuzu"``. This test proves that fallback is a
real, working note -> recall roundtrip on the **native Kuzu** driver — not just a
registry entry.

It is marked ``@pytest.mark.kuzu`` and therefore **deselected from the default suite**:
Kuzu and Ladybug share one compiled pybind11 extension and cannot both load in a single
Python process (verified in #76), and the default suite already loads Ladybug. A
dedicated CI job installs the optional ``kuzu`` extra and runs ``pytest -m kuzu`` in its
own process. Requires the ``kuzu`` package (the ``kuzu`` extra); it is skipped cleanly
if that archived, optional dependency is not installed.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from memrelay.config import load_config
from memrelay.engine.graphiti import MemoryEngine
from memrelay.mcp.format import format_as_map

pytestmark = pytest.mark.kuzu

NAMESPACE = "proj-kuzu"
FACT = "memrelay stores its persistent agent memory in an embedded Kuzu graph database."
RECALL_QUERY = "which graph database does memrelay use for memory"


def _kuzu_config(tmp_path: Path):
    graph_path = tmp_path / "graph.db"
    cfg = load_config(
        environ={},
        home=str(tmp_path),
        graph={"path": str(graph_path), "backend": "kuzu"},
    )
    assert cfg.graph.backend == "kuzu"
    assert cfg.graph_path == graph_path.resolve()
    return cfg


def test_kuzu_fallback_note_recall_roundtrip(tmp_path, gate_embedder, mock_llm_factory) -> None:
    # Skip cleanly (rather than error) when the optional, archived kuzu extra is absent.
    # This import is kept INSIDE the test — never at module top — so merely *collecting*
    # this file (which pytest does even under ``-m "not kuzu"``) never loads the kuzu
    # native extension, which would otherwise collide with Ladybug in the main suite.
    pytest.importorskip("kuzu", reason="the optional `kuzu` fallback extra is not installed")

    async def scenario() -> None:
        cfg = _kuzu_config(tmp_path)
        engine = await MemoryEngine.from_config(
            cfg,
            llm_client=mock_llm_factory(["memrelay", "Kuzu"]),
            embedder=gate_embedder,
        )
        try:
            note_id = await engine.note(FACT, namespace=NAMESPACE, repo="memrelay")
            assert isinstance(note_id, str) and note_id

            results = await engine.search(RECALL_QUERY, namespace=NAMESPACE)
            assert set(results) == {"nodes", "edges", "scores"}, f"bad shape: {results!r}"
            assert results["nodes"], "recall returned no nodes"
            blob = " ".join(
                f"{n.get('name') or ''} {n.get('summary') or ''}" for n in results["nodes"]
            )
            blob += " " + " ".join(
                f"{e.get('name') or ''} {e.get('fact') or ''}" for e in results["edges"]
            )
            assert "kuzu" in blob.lower(), f"expected the noted fact to be recalled: {results!r}"

            rendered = format_as_map(results)
            assert rendered != "No relevant memories found."

            health = await engine.health()
            assert health["status"] == "ok"
            assert health["backend"] == "kuzu"
        finally:
            await engine.close()

    asyncio.run(scenario())
