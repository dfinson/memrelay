"""Integration test: the spool drains into a REAL memory engine and recalls (E4-S5 #37).

Seeds N episode records into the durable :class:`~memrelay.ingest.spool.Spool`, runs
:class:`~memrelay.ingest.ingester.Ingester` to drain them into a real
:class:`~memrelay.engine.graphiti.MemoryEngine` (embedded Ladybug on ``tmp_path``, the
deterministic mock LLM + real/offline embedder from ``conftest.py``), and asserts a
noted fact comes back via a *semantic* ``engine.search``. Fully hermetic: no network,
no API key, never a real ``~/.memrelay``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from memrelay.config import load_config
from memrelay.engine.graphiti import MemoryEngine
from memrelay.ingest.episode import EpisodeRecord
from memrelay.ingest.ingester import Ingester
from memrelay.ingest.spool import Spool

NAMESPACE = "proj-a"
RECALL_QUERY = "which graph database does memrelay use for memory"
FACTS = [
    "memrelay stores its persistent agent memory in an embedded Ladybug graph database.",
    "memrelay runs a single-writer observation daemon.",
    "the Ladybug database file lives under the memrelay home directory.",
]


def _make_config(tmp_path: Path):
    graph_path = tmp_path / "graph.db"
    # environ={} + absolute overrides keep this isolated from the caller's real
    # home, MEMRELAY_* and XDG_* — we never touch a real graph.db.
    cfg = load_config(
        environ={},
        home=str(tmp_path),
        graph={"path": str(graph_path), "backend": "ladybug"},
    )
    assert cfg.graph_path == graph_path.resolve()
    return cfg


async def _drain(engine: MemoryEngine, spool: Spool, *, timeout: float = 120.0) -> Ingester:
    """Run the ingester until the spool is fully consumed, then stop it."""
    ingester = Ingester(engine, spool, idle_sleep=0.02)
    stop = asyncio.Event()
    task = asyncio.create_task(ingester.run(stop))
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while spool.pending() > 0 and loop.time() < deadline:
        await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(task, timeout=timeout)
    return ingester


@pytest.mark.integration
def test_spool_drains_into_engine_and_recalls(tmp_path, gate_embedder, mock_llm_factory) -> None:
    async def scenario() -> None:
        cfg = _make_config(tmp_path)
        engine = await MemoryEngine.from_config(
            cfg,
            llm_client=mock_llm_factory(["memrelay", "Ladybug"]),
            embedder=gate_embedder,
        )
        try:
            spool = Spool(tmp_path / "spool" / "spool.db")
            for index, fact in enumerate(FACTS):
                spool.append(
                    EpisodeRecord.new(
                        fact,
                        NAMESPACE,
                        repo="memrelay",
                        source="integration",
                        session_id="s1",
                        event_id=f"e{index}",
                    ).to_dict()
                )
            assert spool.pending() == len(FACTS)

            ingester = await _drain(engine, spool)

            # Every seeded episode was ingested and the spool fully drained.
            assert ingester.stats()["episodes_ingested"] == len(FACTS)
            assert ingester.stats()["spool_pending"] == 0
            assert spool.pending() == 0

            # The drained facts are now recallable by a semantic query.
            results = await engine.search(RECALL_QUERY, namespace=NAMESPACE)
            assert set(results) == {"nodes", "edges", "scores"}, f"bad shape: {results!r}"
            assert results["nodes"], "recall returned no nodes after draining the spool"
            blob = " ".join(
                f"{node.get('name') or ''} {node.get('summary') or ''}" for node in results["nodes"]
            )
            blob += " " + " ".join(
                f"{edge.get('name') or ''} {edge.get('fact') or ''}" for edge in results["edges"]
            )
            assert "ladybug" in blob.lower(), (
                f"expected the drained fact to be recalled: {results!r}"
            )

            spool.close()
        finally:
            await engine.close()

    asyncio.run(scenario())
