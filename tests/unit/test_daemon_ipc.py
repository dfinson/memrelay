"""Unit tests for the in-process daemon IPC layer (E6-S3/S4).

Every scenario runs a real :class:`DaemonServer` on a temp endpoint (a Unix domain
socket on POSIX — the CI path — or loopback on Windows) and talks to it with the
shared transport framing.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from memrelay.daemon import transport
from memrelay.daemon.protocol import StubBackend
from memrelay.daemon.server import DaemonServer
from memrelay.daemon.transport import Endpoint, resolve_endpoint


async def _roundtrip(endpoint: Endpoint, message: dict) -> dict | None:
    reader, writer = await transport.connect(endpoint, timeout=5.0)
    try:
        await transport.write_message(writer, message)
        return await transport.read_message(reader)
    finally:
        writer.close()


async def _serve_and(endpoint: Endpoint, body) -> object:
    server = DaemonServer(StubBackend(), endpoint)
    await server.start()
    try:
        return await body()
    finally:
        await server.stop()


def test_all_methods_roundtrip(tmp_path: Path) -> None:
    endpoint = resolve_endpoint(tmp_path)

    async def scenario() -> tuple:
        async def body() -> tuple:
            search = await _roundtrip(
                endpoint, {"method": "search", "query": "auth", "namespace": "ns"}
            )
            detail = await _roundtrip(
                endpoint, {"method": "detail", "node_uuid": "n1", "namespace": "ns"}
            )
            note = await _roundtrip(endpoint, {"method": "note", "content": "c", "namespace": "ns"})
            health = await _roundtrip(endpoint, {"method": "health"})
            return search, detail, note, health

        return await _serve_and(endpoint, body)

    search, detail, note, health = asyncio.run(scenario())
    assert set(search) == {"nodes", "edges", "scores"}
    assert detail["node"]["uuid"] == "n1"
    assert note == {"status": "ok"}
    assert health["status"] == "running"


def test_unknown_method_returns_error(tmp_path: Path) -> None:
    endpoint = resolve_endpoint(tmp_path)

    async def scenario() -> dict | None:
        return await _serve_and(endpoint, lambda: _roundtrip(endpoint, {"method": "nope"}))

    result = asyncio.run(scenario())
    assert result["error"]["type"] == "unknown_method"


def test_malformed_json_is_handled(tmp_path: Path) -> None:
    endpoint = resolve_endpoint(tmp_path)

    async def scenario() -> dict | None:
        server = DaemonServer(StubBackend(), endpoint)
        await server.start()
        try:
            reader, writer = await transport.connect(endpoint, timeout=5.0)
            try:
                writer.write(b"this is not json\n")
                await writer.drain()
                return await transport.read_message(reader)
            finally:
                writer.close()
        finally:
            await server.stop()

    result = asyncio.run(scenario())
    assert result["error"]["type"] == "bad_json"


def test_multiple_requests_on_one_connection(tmp_path: Path) -> None:
    endpoint = resolve_endpoint(tmp_path)

    async def scenario() -> list:
        server = DaemonServer(StubBackend(), endpoint)
        await server.start()
        try:
            reader, writer = await transport.connect(endpoint, timeout=5.0)
            try:
                replies = []
                for _ in range(3):
                    await transport.write_message(writer, {"method": "health"})
                    replies.append(await transport.read_message(reader))
                return replies
            finally:
                writer.close()
        finally:
            await server.stop()

    replies = asyncio.run(scenario())
    assert len(replies) == 3
    assert all(r["status"] == "running" for r in replies)


def test_endpoint_artifacts_removed_on_stop(tmp_path: Path) -> None:
    endpoint = resolve_endpoint(tmp_path)

    async def scenario() -> None:
        server = DaemonServer(StubBackend(), endpoint)
        await server.start()
        await server.stop()

    asyncio.run(scenario())
    assert not endpoint.socket_path.exists()
    assert not endpoint.port_path.exists()


def test_shutdown_control_message_stops_run(tmp_path: Path) -> None:
    endpoint = resolve_endpoint(tmp_path)

    async def scenario() -> dict | None:
        server = DaemonServer(StubBackend(), endpoint)
        run_task = asyncio.create_task(server.run())
        # Give the listener a moment to come up, then send __shutdown__.
        for _ in range(50):
            if endpoint.socket_path.exists() or endpoint.port_path.exists():
                break
            await asyncio.sleep(0.02)
        reply = await _roundtrip(endpoint, {"method": "__shutdown__"})
        await asyncio.wait_for(run_task, timeout=5.0)
        return reply

    reply = asyncio.run(scenario())
    assert reply == {"status": "stopping"}
