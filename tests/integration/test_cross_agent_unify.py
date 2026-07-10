"""Headline cross-agent unification at the ENGINE level (E5-S4 / #65).

This is the engine-level proof of issue #65's user story: memory formed while driving one
agent is recalled while driving another, *within the same namespace*. Two facts are noted
directly through ``MemoryEngine.note`` into ONE namespace — one tagged ``agent=copilot`` (with
a repo, so the combined ``repo=<r> agent=<a>`` provenance form is exercised) and one tagged
``agent=claude`` (agent-only form) — into a single embedded-Ladybug graph. Then:

* **AC 1** — each stored episode carries its own parseable ``source_description`` provenance.
* **AC 2 / AC 4** — a namespace-scoped recall (no agent argument) surfaces *both* agents'
  facts: a decision made in agent A is visible in agent B, because both live in one namespace
  graph and recall is never partitioned by agent.
* **AC 3** — the optional, default-off ``prefer_agent`` soft boost re-ranks by agent
  provenance without ever adding or dropping a result. The agent tag is a soft signal only —
  never a hard filter (SPEC §5.3) — so recall is never narrowed to a single agent; every
  agent's memories always remain recallable in the namespace.
* The default recall path is **byte-identical** whether or not the (``None``) ``prefer_agent``
  knob is passed.

The pipeline-level cross-agent story (two real providers → one derived namespace) is proved
separately by ``test_cross_agent_recall.py`` (#70); this test isolates the *engine* contract.

Assertions match on entity **names** (structured), never on free-text ``summary``/``fact``
blobs. That is deliberate: graphiti folds an entity's connected edge facts into its ``summary``
(e.g. the shared ``Zephyr`` node's summary textually mentions ``Quasar`` via the bridging
``Zephyr uses Quasar`` fact), so a substring scan of summaries would conflate distinct
entities. Name-level checks assert on the actual entity identities a recall returns — without
that cross-talk.

Same-namespace extraction "bleed" (documented in ``test_forget.py``): graphiti feeds the
earlier episode to the later one's extractor as context, so the mock extractor also mentions
the earlier fact's entity in the later (claude) episode. Noting copilot **first** therefore
makes claude's ``Quasar`` entity the cleanly claude-only one (copilot's episode, noted first,
never saw it), which gives the ``prefer_agent`` boost a non-preferred node to rank below the
preferred agent's; the two-directional ranking rigor lives in
``tests/unit/test_agent_provenance.py``.

Fully hermetic: deterministic mock LLM + real/offline embedder from ``conftest.py``, embedded
Ladybug on ``tmp_path``. No network, no API key, never a real ``~/.memrelay``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from memrelay.config import load_config
from memrelay.engine.graphiti import MemoryEngine

NAMESPACE = "acme"
COPILOT_REPO = "acme/widgets"

# Each fact carries a unique invented term so recall proves *which* agent contributed it, plus
# the shared "widget" anchor tying them to the same namespace. Invented terms have no semantic
# neighbours, so recall is deterministic under both the real fastembed model and the offline
# hashing fallback (mirrors the proven approach in test_cross_agent_recall.py).
COPILOT_FACT = "The widget service authentication is handled by the Zephyr token module."
CLAUDE_FACT = "The widget service health checks are monitored by the Quasar watchdog daemon."
VOCAB = ["Zephyr", "Quasar", "widget"]
COPILOT_QUERY = "Zephyr token module used for authentication"
CLAUDE_QUERY = "Quasar watchdog daemon that monitors health"


def _make_config(tmp_path: Path):
    graph_path = tmp_path / "graph.db"
    # environ={} + absolute overrides keep this fully isolated from the caller's real home,
    # MEMRELAY_* and XDG_* — we never touch a real graph.db.
    return load_config(
        environ={},
        home=str(tmp_path),
        graph={"path": str(graph_path), "backend": "ladybug"},
    )


def _names(results: dict) -> set[str]:
    """Lower-cased set of the entity names a recall returned (its structured node identities)."""
    assert set(results) == {"nodes", "edges", "scores"}, f"bad shape: {results!r}"
    assert len(results["scores"]) == len(results["nodes"]), "scores must align with nodes"
    return {(node.get("name") or "").lower() for node in results["nodes"]}


async def _source_descriptions(engine: MemoryEngine, namespace: str) -> set[str]:
    """Every Episodic node's ``source_description`` in ``namespace`` (read straight off graph)."""
    records, _, _ = await engine._driver.execute_query(
        "MATCH (e:Episodic) RETURN e.group_id AS group_id, e.source_description AS sd"
    )
    return {r["sd"] for r in records if r["group_id"] == namespace}


async def _entity_agents(engine: MemoryEngine, namespace: str) -> dict[str, set[str]]:
    """Independently resolve result-entity uuid -> {agents} via MENTIONS (verify, don't reuse).

    Parses the ``agent=`` token inline rather than importing the engine's parser, so this is an
    independent check of the engine's filter/boost, not a tautology.
    """
    records, _, _ = await engine._driver.execute_query(
        "MATCH (ep:Episodic)-[:MENTIONS]->(n:Entity) WHERE n.group_id = $ns "
        "RETURN n.uuid AS uuid, ep.source_description AS sd",
        ns=namespace,
    )
    out: dict[str, set[str]] = {}
    for record in records:
        for token in (record.get("sd") or "").split(" "):
            key, sep, value = token.partition("=")
            if sep and key == "agent" and value.strip():
                out.setdefault(record["uuid"], set()).add(value.strip().lower())
    return out


def _partition_ok(node_uuids: list[str], agents: dict[str, set[str]], preferred: str) -> bool:
    """True if no ``preferred``-agent node appears after a node lacking that agent.

    This is the exact contract of the stable ``prefer_agent`` boost and is a correctness
    invariant over *any* returned set (it can never falsely fail), so asserting it end-to-end is
    non-flaky regardless of how many nodes the embedder happens to retrieve.
    """
    seen_missing = False
    for uuid in node_uuids:
        has_agent = preferred in agents.get(uuid, set())
        if has_agent and seen_missing:
            return False
        if not has_agent:
            seen_missing = True
    return True


@pytest.mark.integration
def test_cross_agent_unification_engine_level(tmp_path, gate_embedder, mock_llm_factory) -> None:
    async def scenario() -> None:
        cfg = _make_config(tmp_path)
        engine = await MemoryEngine.from_config(
            cfg,
            llm_client=mock_llm_factory(VOCAB),
            embedder=gate_embedder,
        )
        try:
            # Copilot notes FIRST (with a repo -> combined provenance form); Claude SECOND
            # (agent-only form). Order makes Claude's "Quasar" the agent-exclusive entity.
            await engine.note(
                COPILOT_FACT, namespace=NAMESPACE, repo=COPILOT_REPO, source="copilot"
            )
            await engine.note(CLAUDE_FACT, namespace=NAMESPACE, repo=None, source="claude")

            # --- AC 1: each episode carries its own parseable provenance (both encoding forms),
            # and both agents' episodes coexist in the ONE namespace.
            assert await _source_descriptions(engine, NAMESPACE) == {
                "repo=acme/widgets agent=copilot",
                "agent=claude",
            }

            # --- AC 2 / AC 4: ONE namespace, recall (no agent arg) surfaces BOTH agents' facts.
            # Copilot's Zephyr entity and Claude's Quasar entity are each recalled from the same
            # namespace graph — a decision made in one agent is visible from the other.
            assert "zephyr" in _names(await engine.search(COPILOT_QUERY, namespace=NAMESPACE))
            assert "quasar" in _names(await engine.search(CLAUDE_QUERY, namespace=NAMESPACE))

            # --- Default path is byte-identical whether or not the (None) prefer_agent knob is
            # passed (structural guarantee: the boost block is skipped when prefer_agent is None).
            assert await engine.search(CLAUDE_QUERY, namespace=NAMESPACE) == await engine.search(
                CLAUDE_QUERY, namespace=NAMESPACE, prefer_agent=None
            )

            # --- AC 3 (soft boost): prefer_agent re-ranks by real agent provenance and never
            # adds/drops results; every preferred-agent node precedes the non-preferred ones.
            agents = await _entity_agents(engine, NAMESPACE)
            base = await engine.search(CLAUDE_QUERY, namespace=NAMESPACE)
            base_uuids = {n["uuid"] for n in base["nodes"]}
            for preferred in ("claude", "copilot"):
                boosted = await engine.search(
                    CLAUDE_QUERY, namespace=NAMESPACE, prefer_agent=preferred
                )
                boosted_uuids = [n["uuid"] for n in boosted["nodes"]]
                assert set(boosted_uuids) == base_uuids, "boost must be a permutation, not a filter"
                assert len(boosted["scores"]) == len(boosted["nodes"]), "scores stay node-aligned"
                assert _partition_ok(boosted_uuids, agents, preferred), (
                    f"prefer_agent={preferred!r} did not float that agent's nodes: {boosted_uuids}"
                )
        finally:
            await engine.close()

    asyncio.run(scenario())
