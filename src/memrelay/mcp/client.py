"""Thin daemon client used by the MCP server (E7-S2, SPEC §2 / §8 ``mcp/client.py``).

The MCP server is stateless and never touches the graph directly — it reaches the
daemon **only** through this client over the local socket (the single-writer
invariant, SPEC §6.5). One request opens one short-lived connection; failures are
retried with a brief backoff (the daemon may be briefly unavailable, e.g. mid
start-up), and every method is bounded by a timeout so a wedged daemon can never
hang the agent.

This module depends on :mod:`memrelay.daemon.transport` for the shared wire
framing/endpoint rules — and on nothing else in the daemon package. It imports
neither the graph engine nor the daemon's server internals.
"""

from __future__ import annotations

import asyncio
import os

from memrelay.daemon import transport
from memrelay.daemon.transport import Endpoint, resolve_endpoint

DEFAULT_TIMEOUT = 5.0
DEFAULT_RETRIES = 1
RETRY_BACKOFF = 0.05


class DaemonError(RuntimeError):
    """A daemon request failed (unreachable, timed out, or returned an error)."""


class DaemonClient:
    """Async client for the daemon JSON query API."""

    def __init__(
        self,
        endpoint: Endpoint,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
    ) -> None:
        self._endpoint = endpoint
        self._timeout = timeout
        self._retries = retries

    @classmethod
    def for_home(cls, home: str | os.PathLike[str], **kwargs) -> DaemonClient:
        """Build a client for the daemon under a memrelay home directory."""
        return cls(resolve_endpoint(home), **kwargs)

    @property
    def endpoint(self) -> Endpoint:
        return self._endpoint

    # ── public methods (mirror the Backend Protocol) ─────────────────────────

    async def search(self, query: str, namespace: str, prefer_repo: str | None = None) -> dict:
        return await self._call(
            {
                "method": "search",
                "query": query,
                "namespace": namespace,
                "prefer_repo": prefer_repo,
            }
        )

    async def detail(self, node_uuid: str, namespace: str) -> dict:
        return await self._call(
            {"method": "detail", "node_uuid": node_uuid, "namespace": namespace}
        )

    async def note(self, content: str, namespace: str, repo: str | None = None) -> str:
        response = await self._call(
            {"method": "note", "content": content, "namespace": namespace, "repo": repo}
        )
        return str(response.get("status", "ok"))

    async def health(self) -> dict:
        return await self._call({"method": "health"})

    # ── transport ────────────────────────────────────────────────────────────

    async def _call(self, message: dict) -> dict:
        """Send one request with timeout + reconnect; raise :class:`DaemonError`."""
        last_error: Exception | None = None
        for attempt in range(self._retries + 1):
            try:
                return await self._round_trip(message)
            except (TimeoutError, ConnectionError) as exc:
                last_error = exc
                if attempt < self._retries:
                    await asyncio.sleep(RETRY_BACKOFF * (attempt + 1))
        raise DaemonError(f"daemon unreachable at {self._endpoint.describe()}: {last_error}")

    async def _round_trip(self, message: dict) -> dict:
        reader, writer = await transport.connect(self._endpoint, timeout=self._timeout)
        try:
            await transport.write_message(writer, message)
            response = await asyncio.wait_for(transport.read_message(reader), timeout=self._timeout)
        finally:
            writer.close()

        if response is None:
            raise DaemonError("daemon closed the connection without responding")
        if "error" in response:
            err = response["error"]
            raise DaemonError(f"{err.get('type', 'error')}: {err.get('message', '')}")
        return response
