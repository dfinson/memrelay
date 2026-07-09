"""THE E4 GATE (SPEC §12 Step 2): a hermetic note -> recall roundtrip.

Notes a fact into an embedded Ladybug graph via ``MemoryEngine`` using a
deterministic in-process mock LLM + a real (or offline-fallback) embedder, then
recalls it by a *semantic* query and asserts it comes back. No network, no API
key, temp Ladybug via ``tmp_path`` — never a real ``~/.memrelay/graph.db``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from memrelay.config import load_config
from memrelay.engine.graphiti import MemoryEngine
from memrelay.mcp.format import format_as_map, format_detail

NAMESPACE = "proj-a"
FACT = "memrelay stores its persistent agent memory in an embedded Ladybug graph database."
RECALL_QUERY = "which graph database does memrelay use for memory"


def _make_config(tmp_path: Path):
    graph_path = tmp_path / "graph.db"
    # environ={} + absolute overrides keep this fully isolated from the caller's
    # real home, MEMRELAY_* and XDG_* — we never touch a real graph.db.
    cfg = load_config(
        environ={},
        home=str(tmp_path),
        graph={"path": str(graph_path), "backend": "ladybug"},
    )
    assert cfg.graph_path == graph_path.resolve()
    return cfg


def test_note_recall_roundtrip(tmp_path, gate_embedder, mock_llm_factory):
    async def scenario() -> None:
        cfg = _make_config(tmp_path)
        engine = await MemoryEngine.from_config(
            cfg,
            llm_client=mock_llm_factory(["memrelay", "Ladybug"]),
            embedder=gate_embedder,
        )
        try:
            note_id = await engine.note(FACT, namespace=NAMESPACE, repo="memrelay")
            assert isinstance(note_id, str) and note_id, "note() must return an id"

            results = await engine.search(RECALL_QUERY, namespace=NAMESPACE)
            # Wire schema the daemon consumes via memrelay.mcp.format: a dict of
            # nodes / edges / node-aligned scores, NOT a flat list.
            assert set(results) == {"nodes", "edges", "scores"}, f"bad shape: {results!r}"
            assert results["nodes"], "recall returned no nodes"
            assert len(results["scores"]) == len(results["nodes"]), "scores must align with nodes"
            blob = " ".join(
                f"{node.get('name') or ''} {node.get('summary') or ''}" for node in results["nodes"]
            )
            blob += " " + " ".join(
                f"{edge.get('name') or ''} {edge.get('fact') or ''}" for edge in results["edges"]
            )
            blob = blob.lower()
            assert "ladybug" in blob, f"expected the noted fact to be recalled, got: {results!r}"

            # The real daemon formatter must render this engine output verbatim —
            # importing it here pins the seam so the shapes can never silently drift.
            rendered = format_as_map(results)
            assert rendered != "No relevant memories found.", "formatter saw an empty map"
            assert "ladybug" in rendered.lower(), f"formatter dropped the fact:\n{rendered}"

            # detail() on a recalled node returns the daemon detail schema.
            node_hit = results["nodes"][0]
            detail = await engine.detail(node_hit["uuid"], namespace=NAMESPACE)
            assert set(detail) == {"node", "connected_edges", "episodes"}
            assert detail["node"] is not None
            assert detail["node"]["uuid"] == node_hit["uuid"]
            assert detail["node"]["name"]
            assert detail["node"]["name"] in format_detail(detail)

            # detail() on an unknown uuid degrades gracefully: node=None, and the
            # formatter renders its not-found text (no exception).
            missing = await engine.detail(
                "00000000-0000-0000-0000-000000000000", namespace=NAMESPACE
            )
            assert missing["node"] is None
            assert format_detail(missing) == "Entity not found."

            health = await engine.health()
            assert health["status"] == "ok"
            assert health["backend"] == "ladybug"
        finally:
            await engine.close()

    asyncio.run(scenario())


def test_namespace_isolation(tmp_path, gate_embedder, mock_llm_factory):
    """A note in one namespace must not leak into another (group_id filtering)."""

    async def scenario() -> None:
        cfg = _make_config(tmp_path)
        engine = await MemoryEngine.from_config(
            cfg,
            llm_client=mock_llm_factory(["memrelay", "Ladybug"]),
            embedder=gate_embedder,
        )
        try:
            await engine.note(FACT, namespace=NAMESPACE, repo="memrelay")
            other = await engine.search(RECALL_QUERY, namespace="proj-b")
            assert other["nodes"] == [], f"namespace leak: proj-b saw {other!r}"
            assert other["edges"] == [], f"namespace leak: proj-b saw {other!r}"
            # An empty recall must render the formatter's no-results text.
            assert format_as_map(other) == "No relevant memories found."
        finally:
            await engine.close()

    asyncio.run(scenario())
