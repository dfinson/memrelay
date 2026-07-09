"""Behavioral regression for issue #94: memory_recall through the MCP stdio server.

On Windows, a ``memory_recall`` routed **through** the real ``memrelay mcp`` stdio
server used to hang forever (a ``git`` child in ``resolve_context`` inherited the
server's stdio stdin pipe; see ``src/memrelay/mcp/namespace.py`` and the fast
white-box guard in ``tests/unit/test_mcp_namespace.py``). A direct ``DaemonClient``
call was always fast, and the POSIX unix-socket path was unaffected.

This test drives the canonical reproduction path end to end: an in-process
:class:`StubBackend` daemon on a background thread (loopback on Windows, unix
socket on POSIX) plus the *real* ``sys.executable -m memrelay mcp`` server spawned
and spoken to as an agent would via an MCP :class:`ClientSession`. On the buggy
code the tool call never returns and the bounded timeout fails the test; with the
fix it returns promptly on every platform, giving Windows parity with POSIX.
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
from datetime import timedelta
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from memrelay.daemon.protocol import StubBackend
from memrelay.daemon.server import DaemonServer
from memrelay.daemon.transport import Endpoint, resolve_endpoint

pytestmark = pytest.mark.integration

#: The recall must come back well inside this; on buggy code it never returns and
#: this bound is what turns the hang into a fast, legible failure.
CALL_TIMEOUT = 15.0
#: Cold-starting the spawned server (importing memrelay) can be slow on CI/Windows.
INIT_TIMEOUT = 60.0
#: Hard ceiling for the whole exchange so a hang can never wedge the suite.
OVERALL_TIMEOUT = 120.0
#: Echoed by StubBackend into the result, so finding it proves a full round trip.
QUERY = "recall parity check #94"


def _serve_stub_daemon(home: Path, stop: threading.Event) -> None:
    """Run a StubBackend daemon (its own loop/thread) until ``stop`` is set."""

    async def main() -> None:
        server = DaemonServer(StubBackend(), resolve_endpoint(home))
        await server.start()
        try:
            while not stop.is_set():
                await asyncio.sleep(0.05)
        finally:
            await server.stop()

    asyncio.run(main())


def _await_listening(endpoint: Endpoint, timeout: float = 10.0) -> None:
    """Block until the daemon has published its socket/port file."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if endpoint.port_path.exists() or endpoint.socket_path.exists():
            return
        time.sleep(0.05)
    raise AssertionError("stub daemon did not start listening in time")


async def _recall_through_server(home: Path) -> None:
    env = dict(os.environ)
    env["MEMRELAY_HOME"] = str(home)  # point the spawned server at our stub daemon
    env["PYTHONUNBUFFERED"] = "1"
    env.setdefault("PYTHONIOENCODING", "utf-8")
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "memrelay", "mcp"],
        env=env,
        cwd=str(home),
    )
    async with (
        stdio_client(params) as (read, write),
        ClientSession(read, write) as session,
    ):
        await asyncio.wait_for(session.initialize(), timeout=INIT_TIMEOUT)
        try:
            result = await session.call_tool(
                "memory_recall",
                {"query": QUERY},
                read_timeout_seconds=timedelta(seconds=CALL_TIMEOUT),
            )
        except Exception as exc:  # a hang surfaces here as a bounded timeout error
            pytest.fail(
                f"memory_recall did not return through the MCP stdio server "
                f"({type(exc).__name__}: {exc}) — issue #94 regression"
            )

    assert not result.isError, f"tool reported an error: {result.content}"
    text = "".join(getattr(block, "text", "") for block in result.content)
    assert QUERY in text, "query did not round-trip agent -> daemon -> agent"


def test_memory_recall_roundtrips_through_stdio_server(tmp_path: Path) -> None:
    """A recall routed through the real stdio server returns promptly (issue #94)."""
    home = tmp_path / "home"
    home.mkdir()
    endpoint = resolve_endpoint(home)

    stop = threading.Event()
    daemon = threading.Thread(target=_serve_stub_daemon, args=(home, stop), daemon=True)
    daemon.start()
    try:
        _await_listening(endpoint)
        asyncio.run(asyncio.wait_for(_recall_through_server(home), timeout=OVERALL_TIMEOUT))
    finally:
        stop.set()
        daemon.join(timeout=5)
