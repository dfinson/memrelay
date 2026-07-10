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

import functools
from collections.abc import Mapping

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
    client: DaemonClient,
    *,
    namespace_map: Mapping[str, str] | None = None,
    context_resolver: ContextResolver | None = None,
) -> FastMCP:
    """Build the FastMCP server with the three memory tools registered.

    The tools resolve the caller's namespace through a *zero-arg* context resolver. By
    default that resolver is :func:`~memrelay.mcp.namespace.resolve_context` bound to
    ``namespace_map`` — the config ``[namespaces.*]`` repo→namespace map — so recall/note
    resolve the SAME namespace the capture/observe path writes (#106). An empty/``None``
    map derives the git-owner namespace, byte-identical to the zero-config path. An
    explicit ``context_resolver`` (a test seam) takes precedence and bypasses the map.
    """
    server = FastMCP(SERVER_NAME, instructions=SERVER_INSTRUCTIONS)
    resolver = context_resolver or functools.partial(resolve_context, namespace_map=namespace_map)
    register_tools(server, client, resolver)
    return server


def run_stdio(config: Config | None = None) -> None:
    """Serve the MCP tools over stdio until the agent closes the transport.

    Loads config (unless one is injected for tests via ``config``) and threads its
    ``[namespaces.*]`` map into the tools, so recall/note resolve the same namespace the
    observe path writes (#106). The ``config`` parameter is the injection seam:
    ``memrelay mcp`` calls this with no argument, which keeps loading config here.
    """
    cfg = config if config is not None else load_config()
    client = DaemonClient.for_home(cfg.home_path)
    server = build_mcp_server(client, namespace_map=cfg.namespaces.repo_map)
    server.run("stdio")
