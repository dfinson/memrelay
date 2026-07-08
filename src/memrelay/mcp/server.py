"""MCP stdio server lifecycle (SPEC §4.1, E7-S1, ``mcp/server.py``).

``memrelay mcp`` calls :func:`run_stdio`, which builds a :class:`FastMCP` server
registering exactly the three memory tools and serves it over stdio — the
transport every MCP-capable agent (Copilot CLI, Claude Code, …) spawns. The
server is stateless: it owns a :class:`~memrelay.mcp.client.DaemonClient` and
forwards every call to the daemon.

Nothing here may write to stdout: on stdio transport, stdout *is* the MCP
protocol channel.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from memrelay.config import Config, load_config
from memrelay.mcp.client import DaemonClient
from memrelay.mcp.namespace import resolve_context
from memrelay.mcp.tools import ContextResolver, register_tools

SERVER_NAME = "memrelay"
SERVER_INSTRUCTIONS = (
    "Persistent cross-session memory for coding agents. Use memory_recall to fetch "
    "relevant context from previous sessions, memory_detail to expand an entity, and "
    "memory_note to store a durable fact."
)


def build_mcp_server(
    client: DaemonClient, *, context_resolver: ContextResolver | None = None
) -> FastMCP:
    """Build the FastMCP server with the three memory tools registered."""
    server = FastMCP(SERVER_NAME, instructions=SERVER_INSTRUCTIONS)
    register_tools(server, client, context_resolver or resolve_context)
    return server


def run_stdio(config: Config | None = None) -> None:
    """Serve the MCP tools over stdio until the agent closes the transport."""
    cfg = config or load_config()
    client = DaemonClient.for_home(cfg.home_path)
    server = build_mcp_server(client)
    server.run("stdio")
