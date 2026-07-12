"""E9-S3 (#60): hermetic file-refactor invalidation over a REAL embedded Ladybug engine.

Same gate rig as ``test_forget`` — a deterministic in-process mock LLM plus a real (or
offline-fallback) embedder, temp Ladybug via ``tmp_path``. No network, no API key, never a real
``~/.memrelay/graph.db``. These tests prove the staleness path end-to-end (SPEC §5.5): when a big
refactor is observed for a file, the prior file facts are **temporally superseded** — the entity
edges gain ``expired_at`` — while nothing is deleted, other files / namespaces / non-file notes
are untouched, and the superseded node stays recallable.

The mock's ``EdgeDuplicate`` returns no contradictions and no duplicates, so graphiti's own
LLM-driven contradiction is inert and edges are never merged across episodes — our explicit
:meth:`MemoryEngine.invalidate_file_facts` (reached through :meth:`note`'s provenance channel) is
the *only* mechanism that closes an edge here. Edges are matched to their originating episode by
uuid (via ``EntityEdge.episodes``), so assertions are robust to the mock's cross-episode entity
bleed: which entities an edge connects does not matter, only which episode it belongs to.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from graphiti_core.edges import EntityEdge

from memrelay.config import load_config
from memrelay.engine.graphiti import MemoryEngine


def _make_config(tmp_path: Path, *, refactor_lines: int):
    graph_path = tmp_path / "graph.db"
    # environ={} + absolute overrides keep this fully isolated from the caller's real home,
    # MEMRELAY_* and XDG_* — we never touch a real graph.db. The ingest override arms (or, at 0,
    # disables) the invalidation threshold under test.
    cfg = load_config(
        environ={},
        home=str(tmp_path),
        graph={"path": str(graph_path), "backend": "ladybug"},
        ingest={"refactor_invalidation_lines": refactor_lines},
    )
    assert cfg.graph_path == graph_path.resolve()
    assert cfg.ingest.refactor_invalidation_lines == refactor_lines
    return cfg


async def _edges_for_episode(
    engine: MemoryEngine, namespace: str, episode_uuid: str, *, with_embeddings: bool = False
) -> list:
    """Every entity edge in ``namespace`` whose ``episodes`` references ``episode_uuid``.

    Loaded through graphiti's own ``EntityEdge`` accessor — the same one the engine's
    invalidation path reads — so ``expired_at`` reflects exactly what was written. Pass
    ``with_embeddings=True`` to also hydrate ``fact_embedding`` (off by default in the loader),
    which lets a test prove the temporal SET left the fact's embedding untouched.
    """
    edges = await EntityEdge.get_by_group_ids(
        engine._driver, [namespace], with_embeddings=with_embeddings
    )
    return [edge for edge in edges if episode_uuid in (edge.episodes or [])]


async def _episode_shas(engine: MemoryEngine, namespace: str) -> list[str]:
    """Every Episodic node's ``source_description`` in ``namespace`` (proves no node deletion)."""
    records, _, _ = await engine._driver.execute_query(
        "MATCH (e:Episodic) RETURN e.group_id AS group_id, e.source_description AS sd"
    )
    return sorted(r["sd"] for r in records if r["group_id"] == namespace)


async def _entity_names(engine: MemoryEngine, namespace: str) -> set[str]:
    records, _, _ = await engine._driver.execute_query(
        "MATCH (n:Entity) RETURN n.group_id AS group_id, n.name AS name"
    )
    return {r["name"] for r in records if r["group_id"] == namespace}


def _node_names(hits: dict) -> list[str]:
    return [(node.get("name") or "").lower() for node in hits["nodes"]]


def test_big_refactor_supersedes_only_that_files_prior_facts(
    tmp_path, gate_embedder, mock_llm_factory
):
    """A big refactor on file A expires A's prior edge only — B, a plain note, and another
    namespace are untouched; nothing is deleted and A's node stays recallable."""

    ns = "proj"
    other_ns = "other"

    async def scenario() -> None:
        cfg = _make_config(tmp_path, refactor_lines=50)
        # Vocab is ordered latest-episode-entities-first. The mock extractor scans each episode
        # PLUS its graphiti context (prior same-namespace episodes) and emits one edge over the
        # two earliest-in-vocab entities it finds; ordering this way makes each episode's *own*
        # (distinct) entity pair win, so edges never collapse across episodes despite the bleed.
        vocab = ["Echo", "Foxtrot", "Charlie", "Bravo", "Delta", "Alpha", "Shared"]
        engine = await MemoryEngine.from_config(
            cfg,
            llm_client=mock_llm_factory(vocab),
            embedder=gate_embedder,
        )
        try:
            # Seed file A @ sha1 (magnitude 0 → the seed itself never invalidates anything).
            # First in its namespace → no context bleed → edge Alpha→Shared, unique to this episode.
            ep_a = await engine.note(
                "Alpha service uses the Shared cache.",
                namespace=ns,
                repo="owner/app",
                source="copilot",
                last_commit_sha="sha1",
                file_change_lines={"src/a.py": 0},
            )
            # Seed file B @ sha1 — a different file, must survive A's refactor. Edge Bravo→Delta.
            ep_b = await engine.note(
                "Bravo service uses the Delta queue.",
                namespace=ns,
                repo="owner/app",
                source="copilot",
                last_commit_sha="sha1",
                file_change_lines={"src/b.py": 0},
            )
            # A plain (non-file) note — no provenance, must never be touched.
            ep_plain = await engine.note(
                "Charlie service uses the Delta queue.",
                namespace=ns,
                repo="owner/app",
                source="copilot",
            )
            # Same file path A @ sha1 in a *different* namespace — group scoping must protect it.
            ep_other = await engine.note(
                "Alpha service uses the Shared cache.",
                namespace=other_ns,
                repo="owner/app",
                source="copilot",
                last_commit_sha="sha1",
                file_change_lines={"src/a.py": 0},
            )

            # Every seeded episode produced at least one edge, all still valid pre-refactor.
            a_edges = await _edges_for_episode(engine, ns, ep_a)
            b_edges = await _edges_for_episode(engine, ns, ep_b)
            plain_edges = await _edges_for_episode(engine, ns, ep_plain)
            other_edges = await _edges_for_episode(engine, other_ns, ep_other)
            assert a_edges and all(e.expired_at is None for e in a_edges)
            assert b_edges and all(e.expired_at is None for e in b_edges)
            assert all(e.expired_at is None for e in plain_edges)
            assert other_edges and all(e.expired_at is None for e in other_edges)

            entities_before = await _entity_names(engine, ns)
            shas_before = await _episode_shas(engine, ns)

            # THE REFACTOR: file A observed again at sha2 with a churn of 200 lines (≥ threshold).
            # A distinct entity pair (Echo→Foxtrot) models the file's facts genuinely changing, so
            # the new fact is its own edge. note() invalidates A's PRIOR facts *before* adding this
            # new episode, so the incoming fact never catches its own invalidation.
            ep_a2 = await engine.note(
                "Echo service uses the Foxtrot queue after a big rewrite.",
                namespace=ns,
                repo="owner/app",
                source="copilot",
                last_commit_sha="sha2",
                file_change_lines={"src/a.py": 200},
            )

            # A's prior edge(s) are now temporally superseded ...
            a_edges_after = await _edges_for_episode(engine, ns, ep_a)
            assert a_edges_after and all(e.expired_at is not None for e in a_edges_after)
            assert all(e.invalid_at is not None for e in a_edges_after)
            # ... but the fact itself is NOT deleted — embedding + episodes are left intact
            # (the engine sets only the two temporal fields, by uuid).
            a_edges_embedded = await _edges_for_episode(engine, ns, ep_a, with_embeddings=True)
            for edge in a_edges_embedded:
                assert edge.fact_embedding is not None
                assert ep_a in (edge.episodes or [])

            # The newly-observed post-refactor fact is valid (it is the current truth).
            a2_edges = await _edges_for_episode(engine, ns, ep_a2)
            assert a2_edges and all(e.expired_at is None for e in a2_edges)

            # File B, the plain note, and the other namespace are all untouched.
            assert all(e.expired_at is None for e in await _edges_for_episode(engine, ns, ep_b))
            assert all(e.expired_at is None for e in await _edges_for_episode(engine, ns, ep_plain))
            assert all(
                e.expired_at is None for e in await _edges_for_episode(engine, other_ns, ep_other)
            )

            # No node was deleted: the prior episode and every entity survive (the refactor only
            # ADDS its new episode on top).
            assert set(shas_before).issubset(set(await _episode_shas(engine, ns)))
            assert "repo=owner/app agent=copilot file=src/a.py sha=sha1" in shas_before
            assert entities_before.issubset(await _entity_names(engine, ns))

            # Recall still surfaces the (superseded) node — supersession is temporal, not removal.
            hits = await engine.search("Alpha service", namespace=ns)
            assert any("alpha" in name for name in _node_names(hits))
        finally:
            await engine.close()

    asyncio.run(scenario())


def test_sub_threshold_change_does_not_invalidate(tmp_path, gate_embedder, mock_llm_factory):
    """A change below the threshold is not a big refactor: A's prior edge stays valid."""

    ns = "proj"

    async def scenario() -> None:
        cfg = _make_config(tmp_path, refactor_lines=100)
        engine = await MemoryEngine.from_config(
            cfg,
            llm_client=mock_llm_factory(["Alpha", "Shared"]),
            embedder=gate_embedder,
        )
        try:
            ep_a = await engine.note(
                "Alpha service uses the Shared cache.",
                namespace=ns,
                repo="owner/app",
                source="copilot",
                last_commit_sha="sha1",
                file_change_lines={"src/a.py": 0},
            )
            assert all(e.expired_at is None for e in await _edges_for_episode(engine, ns, ep_a))

            # Re-observe file A at sha2 but with only 40 changed lines — below the 100 threshold.
            await engine.note(
                "Alpha service uses the Shared cache, lightly tweaked.",
                namespace=ns,
                repo="owner/app",
                source="copilot",
                last_commit_sha="sha2",
                file_change_lines={"src/a.py": 40},
            )

            # The prior edge is NOT superseded — the change was too small to count as a refactor.
            a_edges = await _edges_for_episode(engine, ns, ep_a)
            assert a_edges and all(e.expired_at is None for e in a_edges)
        finally:
            await engine.close()

    asyncio.run(scenario())


def test_disabled_knob_never_invalidates(tmp_path, gate_embedder, mock_llm_factory):
    """With ``refactor_invalidation_lines = 0`` (the zero-config default) even a huge refactor is
    inert: the provenance is still stamped, but no prior fact is ever superseded."""

    ns = "proj"

    async def scenario() -> None:
        cfg = _make_config(tmp_path, refactor_lines=0)
        engine = await MemoryEngine.from_config(
            cfg,
            llm_client=mock_llm_factory(["Alpha", "Shared"]),
            embedder=gate_embedder,
        )
        try:
            ep_a = await engine.note(
                "Alpha service uses the Shared cache.",
                namespace=ns,
                repo="owner/app",
                source="copilot",
                last_commit_sha="sha1",
                file_change_lines={"src/a.py": 0},
            )
            # A massive change, but the knob is off → invalidation is disabled entirely.
            await engine.note(
                "Alpha service uses the Shared cache after a total rewrite.",
                namespace=ns,
                repo="owner/app",
                source="copilot",
                last_commit_sha="sha2",
                file_change_lines={"src/a.py": 9999},
            )

            a_edges = await _edges_for_episode(engine, ns, ep_a)
            assert a_edges and all(e.expired_at is None for e in a_edges)
        finally:
            await engine.close()

    asyncio.run(scenario())
