"""Unit tests for the MCP-side DaemonClient (E7-S2): round-trip, timeout, reconnect."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from memrelay.daemon import transport
from memrelay.daemon.protocol import StubBackend
from memrelay.daemon.server import DaemonServer
from memrelay.daemon.transport import Endpoint, resolve_endpoint
from memrelay.mcp.client import DaemonClient, DaemonError


def test_client_roundtrips_all_methods(tmp_path: Path) -> None:
    endpoint = resolve_endpoint(tmp_path)

    async def scenario() -> tuple:
        server = DaemonServer(StubBackend(), endpoint)
        await server.start()
        try:
            client = DaemonClient(endpoint, timeout=5.0)
            search = await client.search("auth", "ns")
            detail = await client.detail("n1", "ns")
            note = await client.note("c", "ns")
            health = await client.health()
            return search, detail, note, health
        finally:
            await server.stop()

    search, detail, note, health = asyncio.run(scenario())
    assert search["nodes"][0]["uuid"] == "stub-node-1"
    assert detail["node"]["uuid"] == "n1"
    assert note == "ok"
    assert health["sessions_observed"] == 0


def test_client_raises_daemon_error_on_backend_failure(tmp_path: Path) -> None:
    class Boom(StubBackend):
        async def search(self, query: str, namespace: str, prefer_repo: str | None = None) -> dict:
            raise RuntimeError("kaboom")

    endpoint = resolve_endpoint(tmp_path)

    async def scenario() -> dict:
        server = DaemonServer(Boom(), endpoint)
        await server.start()
        try:
            return await DaemonClient(endpoint, timeout=5.0).search("x", "ns")
        finally:
            await server.stop()

    with pytest.raises(DaemonError) as exc_info:
        asyncio.run(scenario())
    assert "backend_error" in str(exc_info.value)


def test_client_unreachable_raises(tmp_path: Path) -> None:
    # No server was ever started on this endpoint.
    client = DaemonClient(resolve_endpoint(tmp_path), timeout=0.3, retries=0)
    with pytest.raises(DaemonError):
        asyncio.run(client.health())


def test_client_times_out_on_silent_daemon(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Simulate a daemon that accepts the connection but never sends a reply line:
    # readline() hangs, so the client's asyncio.wait_for must fire and surface a
    # DaemonError. (Faking the connection keeps this deterministic and avoids a
    # real never-replying socket whose teardown can wedge on Windows.)
    class _HangingReader:
        async def readline(self) -> bytes:
            await asyncio.Event().wait()  # never returns
            return b""  # pragma: no cover

    class _NoopWriter:
        def write(self, data: bytes) -> None: ...

        async def drain(self) -> None: ...

        def close(self) -> None: ...

    async def fake_connect(endpoint: Endpoint, *, timeout: float):
        return _HangingReader(), _NoopWriter()

    monkeypatch.setattr(transport, "connect", fake_connect)
    client = DaemonClient(resolve_endpoint(tmp_path), timeout=0.2, retries=0)
    with pytest.raises(DaemonError):
        asyncio.run(client.health())


def test_client_reconnects_after_transient_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    endpoint = resolve_endpoint(tmp_path)

    async def scenario() -> tuple[dict, int]:
        server = DaemonServer(StubBackend(), endpoint)
        await server.start()
        real_connect = transport.connect
        calls = {"n": 0}

        async def flaky_connect(ep: Endpoint, *, timeout: float):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ConnectionError("transient blip")
            return await real_connect(ep, timeout=timeout)

        monkeypatch.setattr(transport, "connect", flaky_connect)
        try:
            client = DaemonClient(endpoint, timeout=5.0, retries=1)
            health = await client.health()
            return health, calls["n"]
        finally:
            await server.stop()

    health, attempts = asyncio.run(scenario())
    assert health["status"] == "running"
    assert attempts == 2  # first connect failed, retry succeeded
