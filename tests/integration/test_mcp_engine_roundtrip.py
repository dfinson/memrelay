"""The REAL-graph gate: agent MCP tools -> daemon socket -> real ``MemoryEngine`` (#18).

``test_gate_skeleton.py`` drives the *real* MCP tool surface (``build_mcp_server`` +
``DaemonClient``) end-to-end, but only against the :class:`StubBackend`; its own docstring
promises that *"when the Epic E4 ``MemoryEngine`` replaces ``StubBackend`` … this same gate
exercises the real graph"* — yet that real-graph gate is never actually run. Conversely
``test_daemon_engine.py`` reaches the real engine over the socket, but through the **raw**
``transport`` client, bypassing the three MCP tools and the ``mcp.format`` renderer the agent
actually sees.

This module closes that gap: the exact wiring of the gate skeleton with ``StubBackend``
swapped for a real, hermetic :class:`MemoryEngine` (deterministic mock LLM + the real/offline
embedder from ``conftest.py``, temp Ladybug on ``tmp_path``). It proves the whole agent-facing
component roundtrip — ``memory_note`` -> ``DaemonClient`` -> daemon socket -> real engine ->
real graph, then ``memory_recall`` -> ``format_as_map`` back to the agent, then the
``recall -> copy uuid -> memory_detail`` drill-down loop — and that namespace isolation holds
across that entire path. No network, no API key, never a real ``~/.memrelay``.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from memrelay.config import load_config
from memrelay.daemon.server import DaemonServer
from memrelay.daemon.transport import resolve_endpoint
from memrelay.engine.graphiti import MemoryEngine
from memrelay.mcp.client import DaemonClient
from memrelay.mcp.server import build_mcp_server

pytestmark = pytest.mark.integration

NAMESPACE = "proj-a"
OTHER_NAMESPACE = "proj-b"
FACT = "memrelay stores its persistent agent memory in an embedded Ladybug graph database."
RECALL_QUERY = "which graph database does memrelay use for memory"
#: Entities the deterministic mock LLM "extracts" from the fact above.
VOCAB = ["memrelay", "Ladybug"]

#: A backtick-wrapped entity uuid as ``format_as_map`` emits it on each ``### Entities`` line
#: (``- **name** `<uuid>` …``). Matching only the backtick-wrapped form isolates real drill-down
#: handles (returned nodes) from the un-quoted edge endpoints and the ``<uuid>`` hint placeholder.
_ENTITY_UUID = re.compile(
    r"`([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})`", re.IGNORECASE
)


def _tool_text(result: object) -> str:
    """Extract the text of a FastMCP ``call_tool`` result (tuple or block list)."""
    blocks = result[0] if isinstance(result, tuple) else result
    return blocks[0].text


def _make_config(tmp_path: Path):
    """Hermetic config: temp home + embedded Ladybug, isolated from the real env.

    ``environ={}`` + absolute ``home``/``path`` keep this off the caller's real home,
    ``MEMRELAY_*`` and ``XDG_*`` — we never touch a real ``graph.db``.
    """
    graph_path = tmp_path / "graph.db"
    cfg = load_config(
        environ={}, home=str(tmp_path), graph={"path": str(graph_path), "backend": "ladybug"}
    )
    assert cfg.graph_path == graph_path.resolve()
    return cfg


def test_mcp_tools_note_recall_detail_through_daemon_real_engine(
    tmp_path: Path, gate_embedder, mock_llm_factory
) -> None:
    """note -> recall -> detail through the MCP tools, served by the REAL engine.

    Every hop is the production seam an agent uses: the FastMCP tool -> ``DaemonClient`` ->
    the daemon socket -> the real ``MemoryEngine`` -> the ``mcp.format`` renderer. The stub
    sentinels are asserted *absent* so a silent fallback to ``StubBackend`` could never pass.
    """

    async def scenario() -> None:
        cfg = _make_config(tmp_path)
        endpoint = resolve_endpoint(tmp_path)
        engine = await MemoryEngine.from_config(
            cfg, llm_client=mock_llm_factory(VOCAB), embedder=gate_embedder
        )
        daemon = DaemonServer(engine, endpoint)
        await daemon.start()
        try:
            client = DaemonClient(endpoint, timeout=10.0)
            mcp = build_mcp_server(client, context_resolver=lambda: (NAMESPACE, "memrelay"))

            # 1) note the fact through the real MCP tool (crosses client -> socket -> engine).
            note = _tool_text(await mcp.call_tool("memory_note", {"content": FACT}))
            assert note == "Noted.", f"memory_note contract broke: {note!r}"

            # 2) recall it through the real MCP tool: the agent gets the real graph, rendered
            #    by the real formatter — NOT the stub's canned map.
            recall = _tool_text(await mcp.call_tool("memory_recall", {"query": RECALL_QUERY}))
            assert "## Memory Map" in recall, f"recall was not the rendered map:\n{recall}"
            assert "ladybug" in recall.lower(), f"noted fact not recalled through MCP:\n{recall}"
            assert "stub-node-1" not in recall and "stub result for" not in recall, (
                f"served the StubBackend, not the real engine:\n{recall}"
            )

            # 3) the realistic drill-down loop: pull an entity uuid straight out of the rendered
            #    recall (the handle the agent copies) and feed it back to memory_detail.
            handles = _ENTITY_UUID.findall(recall)
            assert handles, f"recall exposed no drill-down uuid handle:\n{recall}"
            detail = _tool_text(await mcp.call_tool("memory_detail", {"node_uuid": handles[0]}))
            assert detail != "Entity not found.", f"detail lost the recalled node {handles[0]!r}"
            assert handles[0] in detail, f"detail did not render the requested node:\n{detail}"

            # 4) health over the same socket reports the REAL engine (status "ok" + the Ladybug
            #    backend), which the stub's constant {"status": "running", …} can never satisfy.
            health = await client.health()
            assert health["status"] == "ok", f"real-engine health not ok: {health!r}"
            assert health["backend"] == "ladybug", f"unexpected backend: {health!r}"
        finally:
            await daemon.stop()
            # We built the engine (injected as the daemon's backend), so we release the
            # Ladybug lock here — the daemon must not have closed an engine it does not own.
            await engine.close()

    asyncio.run(scenario())


def test_mcp_recall_is_namespace_isolated_through_real_engine(
    tmp_path: Path, gate_embedder, mock_llm_factory
) -> None:
    """A fact noted in namespace A is invisible to a namespace-B recall over the FULL path.

    ``test_engine_roundtrip`` pins namespace isolation at the engine level; this pins it
    through the whole agent-facing MCP -> daemon -> engine chain, using two MCP servers that
    differ only in the namespace their context resolver returns (same client, same daemon,
    same engine).
    """

    async def scenario() -> None:
        cfg = _make_config(tmp_path)
        endpoint = resolve_endpoint(tmp_path)
        engine = await MemoryEngine.from_config(
            cfg, llm_client=mock_llm_factory(VOCAB), embedder=gate_embedder
        )
        daemon = DaemonServer(engine, endpoint)
        await daemon.start()
        try:
            client = DaemonClient(endpoint, timeout=10.0)
            mcp_a = build_mcp_server(client, context_resolver=lambda: (NAMESPACE, "memrelay"))
            mcp_b = build_mcp_server(client, context_resolver=lambda: (OTHER_NAMESPACE, "memrelay"))

            # Write into namespace A through the MCP tool, and confirm A can recall it.
            assert _tool_text(await mcp_a.call_tool("memory_note", {"content": FACT})) == "Noted."
            recall_a = _tool_text(await mcp_a.call_tool("memory_recall", {"query": RECALL_QUERY}))
            assert "ladybug" in recall_a.lower(), f"namespace A lost its own note:\n{recall_a}"

            # Namespace B recalls the SAME query and must see nothing — the daemon/engine filter
            # by group_id, so the MCP tool renders the formatter's empty-result text verbatim.
            recall_b = _tool_text(await mcp_b.call_tool("memory_recall", {"query": RECALL_QUERY}))
            assert recall_b == "No relevant memories found.", f"namespace leak into B:\n{recall_b}"
        finally:
            await daemon.stop()
            await engine.close()

    asyncio.run(scenario())
