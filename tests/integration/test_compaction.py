"""E9-S2 (#59): hermetic degradation-driven compaction over a REAL embedded Ladybug engine.

Same gate rig as ``test_forget`` / ``test_invalidate`` — a deterministic in-process mock LLM plus a
real (or offline-fallback) embedder, temp Ladybug via ``tmp_path``. No network, no API key, never a
real ``~/.memrelay/graph.db``. These tests prove the compaction pass end-to-end (SPEC §5.5): when a
namespace's oldest, lowest-reference-frequency episodes cross the activity-scaled degradation bar,
they are folded into ONE deterministic extractive summary and removed via the shared-entity-
preserving cascade — the graph shrinks, the gist still recalls, an entity a survivor still needs is
never orphaned, and structured before/after metrics report the reclaim (AC4). Off ⇒ byte-identical,
a re-run is a clean no-op (idempotent), and a busier namespace compacts more (AC1–AC3).

Ordering matters (as in ``test_forget``): graphiti feeds the last few episodes of a namespace to the
extractor as context and the ``MockLLMClient`` scans all of it, so an earlier episode's entity can
bleed forward into a later one's mentions. That bleed only makes shared-entity preservation *more*
likely, so the assertions here — the graph shrank, a survivor's entity lives, the summarized gist
still recalls — hold robustly regardless of exactly how the mock spreads entities.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from memrelay.config import load_config
from memrelay.engine.compaction import is_compaction_summary
from memrelay.engine.graphiti import MemoryEngine


def _make_config(tmp_path: Path, **compaction):
    graph_path = tmp_path / "graph.db"
    # environ={} + absolute overrides keep this fully isolated from the caller's real home,
    # MEMRELAY_* and XDG_* — we never touch a real graph.db. The compaction override arms (or, by
    # default, leaves off) the policy under test.
    cfg = load_config(
        environ={},
        home=str(tmp_path),
        graph={"path": str(graph_path), "backend": "ladybug"},
        compaction=compaction,
    )
    assert cfg.graph_path == graph_path.resolve()
    return cfg


async def _episodes(engine: MemoryEngine, namespace: str) -> list[dict]:
    """Every Episodic node's ``(content, source_description)`` in ``namespace`` (group_id)."""
    records, _, _ = await engine._driver.execute_query(
        "MATCH (e:Episodic) "
        "RETURN e.group_id AS group_id, e.content AS content, e.source_description AS sd"
    )
    return [{"content": r["content"], "sd": r["sd"]} for r in records if r["group_id"] == namespace]


async def _episode_contents(engine: MemoryEngine, namespace: str) -> set[str]:
    return {row["content"] for row in await _episodes(engine, namespace)}


async def _summary_count(engine: MemoryEngine, namespace: str) -> int:
    return sum(1 for row in await _episodes(engine, namespace) if is_compaction_summary(row["sd"]))


async def _entity_names(engine: MemoryEngine, namespace: str) -> set[str]:
    records, _, _ = await engine._driver.execute_query(
        "MATCH (n:Entity) RETURN n.group_id AS group_id, n.name AS name"
    )
    return {r["name"] for r in records if r["group_id"] == namespace}


def _node_names(hits: dict) -> list[str]:
    return [(node.get("name") or "").lower() for node in hits["nodes"]]


async def _seed(engine: MemoryEngine, namespace: str, facts: list[str]) -> None:
    """Note ``facts`` (oldest first) into ``namespace`` as ordinary agent memories."""
    for fact in facts:
        await engine.note(fact, namespace=namespace, repo="owner/app", source="copilot")


# Oldest-first. The last four (Echo/Foxtrot/Golf/Hotel) are the protected recent window when
# min_episodes=4; "Shared" is deliberately mentioned by a *surviving* episode so its entity must be
# preserved when the four oldest are compacted.
_PROJ_FACTS = [
    "Alpha module handles authentication.",  # oldest -> eligible
    "Bravo module handles billing.",  # eligible
    "Charlie module handles caching.",  # eligible
    "Delta module handles dashboards.",  # eligible
    "Echo module reads the Shared registry.",  # protected
    "Foxtrot module writes the Shared registry.",  # protected
    "Golf module handles graphing.",  # protected
    "Hotel module handles hosting.",  # newest -> protected
]

_PROJ_VOCAB = [
    "Alpha",
    "Bravo",
    "Charlie",
    "Delta",
    "Echo",
    "Foxtrot",
    "Golf",
    "Hotel",
    "Shared",
    "registry",
]


def test_degraded_namespace_compacts_and_preserves_gist(tmp_path, gate_embedder, mock_llm_factory):
    """A degraded namespace folds its oldest low-value episodes into one summary and removes them:
    the graph shrinks, a shared entity survives, the gist still recalls, a sibling namespace is
    untouched, before/after metrics are reported, and an immediate re-run is a clean no-op."""

    ns = "proj"
    other = "other"

    async def scenario() -> None:
        cfg = _make_config(
            tmp_path, enabled=True, min_episodes=4, protect_recent=4, degradation_ratio=0.5
        )
        engine = await MemoryEngine.from_config(
            cfg, llm_client=mock_llm_factory(_PROJ_VOCAB), embedder=gate_embedder
        )
        try:
            await _seed(engine, ns, _PROJ_FACTS)
            # A sibling namespace that stays below the activity floor (must never be touched).
            await _seed(engine, other, ["Zulu module handles zoning."])

            before_contents = await _episode_contents(engine, ns)
            assert len(before_contents) == 8
            assert await _summary_count(engine, ns) == 0

            # --- dry run: reports what WOULD compact, writes nothing -----------------------------
            dry = await engine.compact(ns, dry_run=True)
            assert dry["dry_run"] is True
            assert dry["namespaces"][ns]["triggered"] is True
            assert dry["namespaces"][ns]["eligible"] == 4  # newest 4 protected, oldest 4 eligible
            assert dry["namespaces"][ns]["episodes_compacted"] == 0
            assert await _episode_contents(engine, ns) == before_contents  # unchanged
            assert await _summary_count(engine, ns) == 0

            # --- the real pass -------------------------------------------------------------------
            result = await engine.compact(ns)
            metrics = result["namespaces"][ns]
            assert result["enabled"] is True
            assert metrics["triggered"] is True
            assert metrics["eligible"] == 4
            assert metrics["episodes_compacted"] == 4
            assert metrics["summaries_added"] == 1
            assert result["episodes_compacted"] == 4 and result["summaries_added"] == 1

            # before/after metrics (AC4): the graph shrank 8 -> 5 (4 removed, 1 summary added).
            assert metrics["episodes_before"] == 8
            assert metrics["episodes_after"] == 5
            assert metrics["entities_after"] <= metrics["entities_before"]
            assert metrics["edges_after"] <= metrics["edges_before"]
            # The degradation proxy (eligible/episodes) is measurably reclaimed: 4/8 -> 0.0 once the
            # stale mass is folded away (the surviving 4 are all inside the protected window).
            assert metrics["degradation_fraction_before"] == 0.5
            assert metrics["degradation_fraction_after"] == 0.0

            # Exactly one summary episode now exists; the four oldest originals are gone; the four
            # protected originals survive.
            assert await _summary_count(engine, ns) == 1
            contents_after = await _episode_contents(engine, ns)
            assert _PROJ_FACTS[0] not in contents_after
            assert _PROJ_FACTS[3] not in contents_after
            for survivor in _PROJ_FACTS[4:]:
                assert survivor in contents_after

            # Shared-entity preservation: "Shared" is referenced by a surviving episode, so the
            # cascade must never have orphaned it.
            assert "Shared" in await _entity_names(engine, ns)

            # Gist recall survives: a term that lived only in a compacted episode is still findable,
            # because the extractive summary carried it.
            alpha_hits = await engine.search("Alpha module", namespace=ns)
            assert any("alpha" in name for name in _node_names(alpha_hits))

            # The sibling namespace was never considered (below min_episodes) and is intact.
            assert result["namespaces"].get(other) is None
            assert await _episode_contents(engine, other) == {"Zulu module handles zoning."}

            # --- idempotent re-run: nothing left to compact -> clean no-op -----------------------
            rerun = await engine.compact(ns)
            assert rerun["namespaces"][ns]["episodes_compacted"] == 0
            assert rerun["namespaces"][ns]["summaries_added"] == 0
            assert await _summary_count(engine, ns) == 1  # no duplicate summary
            assert await _episode_contents(engine, ns) == contents_after  # stable
        finally:
            await engine.close()

    asyncio.run(scenario())


def test_disabled_compaction_is_byte_identical(tmp_path, gate_embedder, mock_llm_factory):
    """With ``enabled=False`` (the zero-config default) a compact() call is an inert no-op: the
    episode set, the entity set, and a recall are all identical before and after, and no summary is
    ever created."""

    ns = "proj"

    async def scenario() -> None:
        cfg = _make_config(tmp_path, enabled=False, min_episodes=4)
        engine = await MemoryEngine.from_config(
            cfg, llm_client=mock_llm_factory(_PROJ_VOCAB), embedder=gate_embedder
        )
        try:
            await _seed(engine, ns, _PROJ_FACTS)

            contents_before = await _episode_contents(engine, ns)
            entities_before = await _entity_names(engine, ns)
            recall_before = _node_names(await engine.search("Alpha module", namespace=ns))

            result = await engine.compact(ns)
            assert result == {
                "enabled": False,
                "dry_run": False,
                "namespaces": {},
                "episodes_compacted": 0,
                "summaries_added": 0,
            }

            # Nothing changed: same episodes (no summary), same entities, same recall.
            assert await _episode_contents(engine, ns) == contents_before
            assert await _summary_count(engine, ns) == 0
            assert await _entity_names(engine, ns) == entities_before
            assert _node_names(await engine.search("Alpha module", namespace=ns)) == recall_before
        finally:
            await engine.close()

    asyncio.run(scenario())


def test_busier_namespace_compacts_more(tmp_path, gate_embedder, mock_llm_factory):
    """Two degraded namespaces of different activity: the busier one (more episodes) compacts
    strictly more, the graph self-regulating by namespace size (AC3)."""

    async def scenario() -> None:
        cfg = _make_config(
            tmp_path, enabled=True, min_episodes=4, protect_recent=4, degradation_ratio=0.5
        )
        vocab = [f"E{i}" for i in range(16)]
        engine = await MemoryEngine.from_config(
            cfg, llm_client=mock_llm_factory(vocab), embedder=gate_embedder
        )
        try:
            # Busy: 16 episodes -> protect newest 4 -> 12 eligible (bar ceil(0.5*16)=8, 12>=8).
            await _seed(engine, "busy", [f"E{i} module note number {i}." for i in range(16)])
            # Quiet: 8 episodes -> protect newest 4 -> 4 eligible (bar ceil(0.5*8)=4, 4>=4).
            await _seed(engine, "quiet", [f"E{i} module note number {i}." for i in range(8)])

            result = await engine.compact()  # sweep every namespace

            busy = result["namespaces"]["busy"]
            quiet = result["namespaces"]["quiet"]
            assert busy["episodes_compacted"] == 12
            assert quiet["episodes_compacted"] == 4
            assert busy["episodes_compacted"] > quiet["episodes_compacted"], "busier compacts more"

            # before/after metrics per namespace (AC4): each shrank by its own compacted count.
            assert busy["episodes_before"] == 16 and busy["episodes_after"] == 16 - 12 + 1
            assert quiet["episodes_before"] == 8 and quiet["episodes_after"] == 8 - 4 + 1
            # The busier namespace starts more degraded by the proxy (12/16 vs 4/8) and both are
            # fully reclaimed to 0.0 — the effect is measured, not merely asserted (AC4).
            assert busy["degradation_fraction_before"] == 0.75
            assert quiet["degradation_fraction_before"] == 0.5
            assert busy["degradation_fraction_after"] == 0.0
            assert quiet["degradation_fraction_after"] == 0.0
            assert busy["degradation_fraction_before"] > quiet["degradation_fraction_before"]
            assert result["episodes_compacted"] == 16 and result["summaries_added"] == 2
            assert await _summary_count(engine, "busy") == 1
            assert await _summary_count(engine, "quiet") == 1
        finally:
            await engine.close()

    asyncio.run(scenario())
