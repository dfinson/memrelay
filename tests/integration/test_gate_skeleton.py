"""THE GATE (SPEC §12 Step 1): agent -> MCP server -> daemon socket -> stub -> agent.

Hermetic, end-to-end proof of the process architecture on a temp endpoint (a Unix
domain socket on the Linux CI path). It starts an in-process daemon with the
``StubBackend``, wires the *real* :class:`DaemonClient` into a *real* FastMCP
server, and drives all three MCP tools plus a direct ``health`` round-trip exactly
as an agent would — asserting the agent-visible response flowed all the way back.

When the Epic E4 ``MemoryEngine`` replaces ``StubBackend`` (a one-line swap in the
daemon), this same gate exercises the real graph.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from memrelay.daemon.protocol import StubBackend
from memrelay.daemon.server import DaemonServer
from memrelay.daemon.transport import resolve_endpoint
from memrelay.mcp.client import DaemonClient
from memrelay.mcp.server import build_mcp_server

pytestmark = pytest.mark.integration


def _tool_text(result: object) -> str:
    blocks = result[0] if isinstance(result, tuple) else result
    return blocks[0].text


async def _run_gate(tmp_path: Path) -> tuple[str, str, str, dict]:
    endpoint = resolve_endpoint(tmp_path)
    daemon = DaemonServer(StubBackend(), endpoint)
    await daemon.start()
    try:
        client = DaemonClient(endpoint, timeout=5.0)
        mcp = build_mcp_server(client, context_resolver=lambda: ("dfinson", "dfinson/memrelay"))
        recall = _tool_text(await mcp.call_tool("memory_recall", {"query": "auth flow"}))
        detail = _tool_text(await mcp.call_tool("memory_detail", {"node_uuid": "stub-node-1"}))
        note = _tool_text(await mcp.call_tool("memory_note", {"content": "we use JWT"}))
        health = await client.health()
        return recall, detail, note, health
    finally:
        await daemon.stop()


def test_gate_agent_through_mcp_and_daemon(tmp_path: Path) -> None:
    recall, detail, note, health = asyncio.run(_run_gate(tmp_path))

    # memory_recall: the query flowed to the daemon and returned formatted for the agent.
    assert "Memory Map" in recall
    assert "auth flow" in recall

    # memory_detail: the requested node id round-tripped through the daemon.
    assert "stub-node-1" in detail

    # memory_note: fixed acknowledgement per the tool contract.
    assert note == "Noted."

    # health (E6-S5): cheap metrics surfaced for `memrelay status`.
    assert health["status"] == "running"
    assert {"sessions_observed", "episodes_ingested", "spool_pending"} <= set(health)
