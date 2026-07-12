"""Unit tests for the three MCP tools (E7-S3/S4/S5) driven via ``call_tool``.

Each test wires the real FastMCP server to the real DaemonClient against an
in-process stub daemon, then calls a tool exactly as an agent would.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from memrelay.daemon.protocol import StubBackend
from memrelay.daemon.server import DaemonServer
from memrelay.daemon.transport import resolve_endpoint
from memrelay.mcp.client import DaemonClient
from memrelay.mcp.server import build_mcp_server


def _tool_text(result: object) -> str:
    """Normalize FastMCP.call_tool's return across SDK versions to the text body.

    1.25 returns ``(content_blocks, structured)``; older/newer may return just the
    blocks. Either way the first block carries the tool's string result.
    """
    blocks = result[0] if isinstance(result, tuple) else result
    return blocks[0].text


async def _with_tools(tmp_path: Path, use) -> object:
    endpoint = resolve_endpoint(tmp_path)
    server = DaemonServer(StubBackend(), endpoint)
    await server.start()
    try:
        client = DaemonClient(endpoint, timeout=5.0)
        mcp = build_mcp_server(client, context_resolver=lambda: ("dfinson", "owner/repo"))
        return await use(mcp)
    finally:
        await server.stop()


def test_exactly_three_tools_registered(tmp_path: Path) -> None:
    async def use(mcp) -> list[str]:
        return sorted(tool.name for tool in await mcp.list_tools())

    names = asyncio.run(_with_tools(tmp_path, use))
    assert names == ["memory_detail", "memory_note", "memory_recall"]


def test_memory_recall_returns_formatted_string(tmp_path: Path) -> None:
    async def use(mcp) -> str:
        return _tool_text(await mcp.call_tool("memory_recall", {"query": "auth system"}))

    text = asyncio.run(_with_tools(tmp_path, use))
    assert "Memory Map" in text
    assert "auth system" in text  # query round-tripped agent -> daemon -> agent


def test_memory_detail_surfaces_requested_node(tmp_path: Path) -> None:
    async def use(mcp) -> str:
        return _tool_text(await mcp.call_tool("memory_detail", {"node_uuid": "xyz-1"}))

    text = asyncio.run(_with_tools(tmp_path, use))
    assert "xyz-1" in text


def test_memory_note_returns_noted(tmp_path: Path) -> None:
    async def use(mcp) -> str:
        return _tool_text(await mcp.call_tool("memory_note", {"content": "remember me"}))

    text = asyncio.run(_with_tools(tmp_path, use))
    assert text == "Noted."


class _NoteSpyClient:
    """Duck-typed :class:`~memrelay.mcp.client.DaemonClient` that records ``note`` args.

    The note-size guard lives in the tool itself, so these tests need no daemon: a spy
    proves the tool short-circuits an oversized note *before* the client is touched, and
    still forwards a normal note verbatim.
    """

    def __init__(self) -> None:
        self.note_calls: list[tuple[str, str, str | None]] = []

    async def note(self, content: str, namespace: str, repo: str | None = None) -> str:
        self.note_calls.append((content, namespace, repo))
        return "ok"


def test_memory_note_rejects_oversized_content_without_calling_daemon() -> None:
    from memrelay.mcp.tools import MAX_NOTE_BYTES

    client = _NoteSpyClient()
    mcp = build_mcp_server(client, context_resolver=lambda: ("dfinson", "owner/repo"))
    oversized = "x" * (MAX_NOTE_BYTES + 1)

    text = _tool_text(asyncio.run(mcp.call_tool("memory_note", {"content": oversized})))

    assert "exceeds" in text  # actionable guard message, not an opaque transport error
    assert client.note_calls == []  # guard short-circuited before the daemon


def test_memory_note_normal_content_still_reaches_daemon() -> None:
    client = _NoteSpyClient()
    mcp = build_mcp_server(client, context_resolver=lambda: ("dfinson", "owner/repo"))

    text = _tool_text(asyncio.run(mcp.call_tool("memory_note", {"content": "small note"})))

    assert text == "Noted."
    assert client.note_calls == [("small note", "dfinson", "owner/repo")]
