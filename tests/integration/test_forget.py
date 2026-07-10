"""E9-S1 (#58): hermetic ``forget`` over a REAL embedded Ladybug engine.

Same gate rig as ``test_engine_roundtrip`` — a deterministic in-process mock LLM
plus a real (or offline-fallback) embedder, temp Ladybug via ``tmp_path``. No
network, no API key, never a real ``~/.memrelay/graph.db``. These tests prove the
delete end-to-end: what is removed, what survives, and that ``--dry-run`` is a
no-op.

Note ordering matters here on purpose. graphiti feeds the last few episodes of a
namespace to the extractor as context, and the hermetic ``MockLLMClient`` scans
*all* message text (including that context) for its vocab. So an earlier episode's
entity can bleed into a later same-namespace episode's mentions. Noting the
``forget`` target **last** keeps its unique entity out of the earlier episode's
mentions — which mirrors how a real extractor scopes entities per episode and lets
us assert the production-representative outcome (unique entity of the forgotten
repo is cleaned up, shared entities are preserved).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from memrelay.config import load_config
from memrelay.engine.graphiti import MemoryEngine


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


async def _episode_descriptions(engine: MemoryEngine, namespace: str) -> list[str]:
    """Every Episodic node's ``source_description`` in ``namespace`` (group_id)."""
    records, _, _ = await engine._driver.execute_query(
        "MATCH (e:Episodic) RETURN e.group_id AS group_id, e.source_description AS sd"
    )
    return sorted(r["sd"] for r in records if r["group_id"] == namespace)


async def _entity_names(engine: MemoryEngine, namespace: str) -> set[str]:
    """Every Entity node name in ``namespace`` (group_id)."""
    records, _, _ = await engine._driver.execute_query(
        "MATCH (n:Entity) RETURN n.group_id AS group_id, n.name AS name"
    )
    return {r["name"] for r in records if r["group_id"] == namespace}


def _node_names(hits: dict) -> list[str]:
    return [(node.get("name") or "").lower() for node in hits["nodes"]]


def test_forget_repo_removes_that_repo_and_preserves_shared_entities(
    tmp_path, gate_embedder, mock_llm_factory
):
    """forget --repo A: A's episode + A-only entity gone; B and the shared entity survive."""

    namespace = "proj"

    async def scenario() -> None:
        cfg = _make_config(tmp_path)
        engine = await MemoryEngine.from_config(
            cfg,
            llm_client=mock_llm_factory(["Alpha", "Bravo", "Shared"]),
            embedder=gate_embedder,
        )
        try:
            # Note repo-b FIRST, the forget target repo-a LAST (see module docstring):
            # keeps "Alpha" mentioned only by repo-a's episode so it can be cleaned up,
            # while "Shared" — mentioned by both — is preserved.
            await engine.note(
                "Bravo service uses the Shared cache.",
                namespace=namespace,
                repo="owner/repo-b",
                source="copilot",
            )
            await engine.note(
                "Alpha service uses the Shared cache.",
                namespace=namespace,
                repo="Owner/Repo-A",  # stored mixed-case on purpose
                source="copilot",
            )

            assert await _entity_names(engine, namespace) == {"Alpha", "Bravo", "Shared"}
            assert await _episode_descriptions(engine, namespace) == [
                "repo=Owner/Repo-A agent=copilot",
                "repo=owner/repo-b agent=copilot",
            ]

            # --repo matches case-insensitively: stored "Owner/Repo-A", forget "owner/repo-a".
            deleted = await engine.forget(repo="owner/repo-a")
            assert deleted == 1

            # repo A's episode is gone; repo B's remains.
            assert await _episode_descriptions(engine, namespace) == [
                "repo=owner/repo-b agent=copilot"
            ]
            # A's unique entity removed; B's entity AND the shared entity preserved.
            assert await _entity_names(engine, namespace) == {"Bravo", "Shared"}

            # Recall reflects it: Alpha can no longer be found, Bravo still can.
            alpha_hits = await engine.search("Alpha service", namespace=namespace)
            assert all("alpha" not in name for name in _node_names(alpha_hits))
            bravo_hits = await engine.search("Bravo service", namespace=namespace)
            assert any("bravo" in name for name in _node_names(bravo_hits))
        finally:
            await engine.close()

    asyncio.run(scenario())


def test_forget_namespace_clears_only_that_namespace(tmp_path, gate_embedder, mock_llm_factory):
    """forget --namespace X: X is emptied entirely; a sibling namespace Y is untouched."""

    async def scenario() -> None:
        cfg = _make_config(tmp_path)
        engine = await MemoryEngine.from_config(
            cfg,
            llm_client=mock_llm_factory(["Xray", "Yankee"]),
            embedder=gate_embedder,
        )
        try:
            await engine.note(
                "Xray subsystem powers the dashboard.",
                namespace="ns-x",
                repo="owner/x",
                source="copilot",
            )
            await engine.note(
                "Yankee subsystem powers the dashboard.",
                namespace="ns-y",
                repo="owner/y",
                source="copilot",
            )
            assert await _entity_names(engine, "ns-x") == {"Xray"}
            assert await _entity_names(engine, "ns-y") == {"Yankee"}

            deleted = await engine.forget(namespace="ns-x")
            assert deleted == 1

            # ns-x is fully cleared (no episodes, no entities); ns-y is intact.
            assert await _episode_descriptions(engine, "ns-x") == []
            assert await _entity_names(engine, "ns-x") == set()
            assert await _episode_descriptions(engine, "ns-y") == ["repo=owner/y agent=copilot"]
            assert await _entity_names(engine, "ns-y") == {"Yankee"}

            # Recall confirms it: ns-x empty, ns-y still finds its memory.
            x_hits = await engine.search("Xray subsystem", namespace="ns-x")
            assert x_hits["nodes"] == []
            y_hits = await engine.search("Yankee subsystem", namespace="ns-y")
            assert any("yankee" in name for name in _node_names(y_hits))
        finally:
            await engine.close()

    asyncio.run(scenario())


def test_forget_dry_run_reports_without_deleting(tmp_path, gate_embedder, mock_llm_factory):
    """--dry-run returns the blast-radius count for repo and namespace but deletes nothing."""

    namespace = "proj"

    async def scenario() -> None:
        cfg = _make_config(tmp_path)
        engine = await MemoryEngine.from_config(
            cfg,
            llm_client=mock_llm_factory(["Alpha", "Bravo"]),
            embedder=gate_embedder,
        )
        try:
            await engine.note(
                "Alpha runs here.", namespace=namespace, repo="owner/a", source="copilot"
            )
            await engine.note(
                "Bravo runs here.", namespace=namespace, repo="owner/b", source="copilot"
            )

            # dry-run by repo: reports the single matching episode, deletes nothing.
            assert await engine.forget(repo="owner/a", dry_run=True) == 1
            assert len(await _episode_descriptions(engine, namespace)) == 2

            # dry-run by namespace: reports both episodes, still deletes nothing.
            assert await engine.forget(namespace=namespace, dry_run=True) == 2
            assert len(await _episode_descriptions(engine, namespace)) == 2

            # A real delete afterwards still works (proves dry-run left the graph usable).
            assert await engine.forget(repo="owner/a") == 1
            assert await _episode_descriptions(engine, namespace) == ["repo=owner/b agent=copilot"]
        finally:
            await engine.close()

    asyncio.run(scenario())


def test_forget_requires_exactly_one_target(tmp_path, gate_embedder, mock_llm_factory):
    """The engine API rejects neither-or-both, mirroring the CLI usage guard."""

    async def scenario() -> None:
        cfg = _make_config(tmp_path)
        engine = await MemoryEngine.from_config(
            cfg, llm_client=mock_llm_factory([]), embedder=gate_embedder
        )
        try:
            with pytest.raises(ValueError):
                await engine.forget()
            with pytest.raises(ValueError):
                await engine.forget(repo="owner/a", namespace="ns")
        finally:
            await engine.close()

    asyncio.run(scenario())
