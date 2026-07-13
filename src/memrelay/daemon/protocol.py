"""Daemon backend contract + stub, and the JSON query-API dispatch (SPEC §2, §4.2).

This module defines the **seam** between the daemon and whatever answers its
queries. Today that is :class:`StubBackend` (canned data, no graph); in a later
wave the Epic E4 ``MemoryEngine`` implements the exact same :class:`Backend`
Protocol and is injected in its place — a one-line swap in the daemon.

The daemon is the **sole owner** of graph state (SPEC §6.5): the MCP server never
touches a backend directly, it reaches one only through the socket. The wire
schema below is the single source of truth for that socket.

Wire schema (newline-delimited JSON, one object per line)::

    # search   → {"nodes": [...], "edges": [...], "scores": [...]}
    {"method": "search", "query": "auth", "namespace": "dfinson", "prefer_repo": "o/r"}
    # detail   → {"node": {...}, "connected_edges": [...], "episodes": [...]}
    {"method": "detail", "node_uuid": "abc-123", "namespace": "dfinson"}
    # note     → {"status": "<backend string>"}
    {"method": "note", "content": "JWT now", "namespace": "dfinson", "repo": "o/r"}
    # health   → {"status": "running", "sessions_observed": N, ...}
    {"method": "health"}

Any response may instead be an **error envelope**
``{"error": {"type": "...", "message": "..."}}``; clients treat the presence of
``error`` as failure. Success payloads keep the SPEC-shaped keys exactly, so the
real engine's responses are wire-compatible with the stub's.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

#: A JSON-serializable object payload crossing the socket.
JsonDict = dict[str, Any]

#: Data methods the daemon answers. ``__shutdown__`` is a control message handled
#: by the server itself (see :mod:`memrelay.daemon.server`), not a backend call.
METHODS = frozenset({"search", "detail", "note", "health"})
SHUTDOWN = "__shutdown__"


@runtime_checkable
class Backend(Protocol):
    """What the daemon queries to answer the MCP tools (the E4 injection seam).

    Exactly these four async methods — the Epic E4 ``MemoryEngine`` implements
    them verbatim, so any object satisfying this Protocol drops into the daemon.
    All returns must be JSON-serializable (they cross the socket unchanged).
    """

    async def search(self, query: str, namespace: str, prefer_repo: str | None = None) -> JsonDict:
        """Return ``{"nodes", "edges", "scores"}`` relevant to ``query``."""
        ...

    async def detail(self, node_uuid: str, namespace: str) -> JsonDict:
        """Return ``{"node", "connected_edges", "episodes"}`` for one node."""
        ...

    async def note(self, content: str, namespace: str, repo: str | None = None) -> str:
        """Persist an explicit fact; return a short status/identifier string."""
        ...

    async def health(self) -> JsonDict:
        """Return cheap liveness/metrics (see :meth:`StubBackend.health`)."""
        ...


class StubBackend:
    """Canned :class:`Backend` for the walking skeleton — no graph, no I/O.

    Returns deterministic, SPEC-shaped payloads that echo the incoming ``query`` /
    ``node_uuid`` / ``namespace`` **inside the data** (never as extra top-level
    keys), so end-to-end tests can prove a value flowed agent→daemon→agent while
    the response stays wire-compatible with the real engine.
    """

    async def search(self, query: str, namespace: str, prefer_repo: str | None = None) -> JsonDict:
        repo = prefer_repo or "stub/repo"
        node = {
            "uuid": "stub-node-1",
            "name": f"stub result for {query!r}",
            "summary": f"canned node for query {query!r} in namespace {namespace!r}",
            "repo": repo,
            "agent": "copilot",
        }
        related = {
            "uuid": "stub-node-2",
            "name": "related stub entity",
            "summary": "a second canned node so edges have a target",
            "repo": repo,
            "agent": "copilot",
        }
        edge = {
            "uuid": "stub-edge-1",
            "name": "RELATED_TO",
            "source_node_uuid": "stub-node-1",
            "target_node_uuid": "stub-node-2",
            "fact": f"{query!r} is related to a stub entity",
        }
        return {"nodes": [node, related], "edges": [edge], "scores": [1.0, 0.5]}

    async def detail(self, node_uuid: str, namespace: str) -> JsonDict:
        node = {
            "uuid": node_uuid,
            "name": f"stub node {node_uuid}",
            "summary": f"canned detail for {node_uuid!r} in namespace {namespace!r}",
        }
        edge = {
            "uuid": "stub-edge-1",
            "name": "RELATED_TO",
            "source_node_uuid": node_uuid,
            "target_node_uuid": "stub-node-2",
            "fact": "canned connected edge",
        }
        episode = {
            "uuid": "stub-episode-1",
            "name": "stub_episode",
            "content": f"canned episode mentioning {node_uuid}",
        }
        return {"node": node, "connected_edges": [edge], "episodes": [episode]}

    async def note(self, content: str, namespace: str, repo: str | None = None) -> str:
        # Real engine returns an episode id; the stub just acknowledges receipt.
        return "ok"

    async def health(self) -> JsonDict:
        # Cheap, constant metrics for now (SPEC §2 / E6-S5). The real daemon will
        # surface live observation counters here; the stub reports a quiet system.
        return {
            "status": "running",
            "sessions_observed": 0,
            "episodes_ingested": 0,
            "spool_pending": 0,
            "notes_failed": 0,
            "poison_skipped": 0,
        }


def error_response(error_type: str, message: str) -> JsonDict:
    """Build the standard error envelope."""
    return {"error": {"type": error_type, "message": message}}


async def dispatch(backend: Backend, request: Mapping[str, Any]) -> JsonDict:
    """Route one parsed request object to ``backend`` and shape the response.

    Never raises: unknown methods, missing fields, and backend exceptions all map
    to an :func:`error_response`, so a bad request can't take the listener down.
    ``__shutdown__`` is intentionally *not* handled here — it is a server-level
    control message, not a backend query.
    """
    method = request.get("method")
    if method not in METHODS:
        return error_response("unknown_method", f"unknown method: {method!r}")
    try:
        if method == "search":
            return await backend.search(
                query=request["query"],
                namespace=request["namespace"],
                prefer_repo=request.get("prefer_repo"),
            )
        if method == "detail":
            return await backend.detail(
                node_uuid=request["node_uuid"], namespace=request["namespace"]
            )
        if method == "note":
            status = await backend.note(
                content=request["content"],
                namespace=request["namespace"],
                repo=request.get("repo"),
            )
            return {"status": status}
        # method == "health"
        return await backend.health()
    except KeyError as missing:
        return error_response("bad_request", f"missing required field: {missing.args[0]}")
    except Exception as exc:  # noqa: BLE001 - the socket boundary must never leak
        return error_response("backend_error", f"{type(exc).__name__}: {exc}")
