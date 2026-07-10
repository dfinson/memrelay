"""The three MCP tools exposed to the agent (SPEC §4.1, E7-S3/S4/S5).

``memory_recall`` / ``memory_detail`` / ``memory_note`` each resolve the caller's
namespace, forward to the daemon over :class:`~memrelay.mcp.client.DaemonClient`,
and shape the reply. They hold no state of their own — all memory lives in the
daemon, which serves the real :class:`~memrelay.engine.graphiti.MemoryEngine`.
"""

from __future__ import annotations

from collections.abc import Callable

from mcp.server.fastmcp import FastMCP

from memrelay.mcp.client import DaemonClient
from memrelay.mcp.format import format_as_map, format_detail

#: A zero-arg resolver returning ``(namespace, repo)`` for the current session. It is
#: built by :func:`memrelay.mcp.server.build_mcp_server` with the config
#: ``[namespaces.*]`` map already bound in, so the tools stay map-agnostic yet resolve
#: the same namespace the capture/observe path writes (#106).
ContextResolver = Callable[[], tuple[str, str | None]]


def register_tools(
    server: FastMCP, client: DaemonClient, context_resolver: ContextResolver
) -> None:
    """Register exactly the three memory tools on ``server``."""

    @server.tool()
    async def memory_recall(query: str, prefer_repo: str | None = None) -> str:
        """Retrieve relevant context from previous sessions.

        Returns a structured graph map + key facts, not flat text.
        """
        namespace, _repo = context_resolver()
        results = await client.search(query, namespace, prefer_repo)
        return format_as_map(results)

    @server.tool()
    async def memory_detail(node_uuid: str) -> str:
        """Drill into a specific entity surfaced by a previous recall."""
        namespace, _repo = context_resolver()
        result = await client.detail(node_uuid, namespace)
        return format_detail(result)

    @server.tool()
    async def memory_note(content: str) -> str:
        """Explicitly store a fact for future recall."""
        namespace, repo = context_resolver()
        await client.note(content, namespace, repo)
        return "Noted."
