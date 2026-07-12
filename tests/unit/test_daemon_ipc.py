"""Unit tests for the in-process daemon IPC layer (E6-S3/S4).

Every scenario runs a real :class:`DaemonServer` on a temp endpoint (a Unix domain
socket on POSIX — the CI path — or loopback on Windows) and talks to it with the
shared transport framing.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from memrelay.daemon import transport
from memrelay.daemon.protocol import StubBackend
from memrelay.daemon.server import DaemonServer
from memrelay.daemon.transport import Endpoint, resolve_endpoint

#: Response payload size for the daemon→client read-limit tests: 200 KiB comfortably
#: exceeds asyncio's default 64 KiB (2**16) StreamReader buffer while staying well
#: under MAX_LINE_BYTES (4 MiB), so it is a legitimate large-but-valid reply.
_BIG_RESPONSE_BYTES = 200 * 1024


class _BigResponseBackend(StubBackend):
    """A backend whose ``search`` returns a payload far larger than asyncio's
    default 64 KiB client reader buffer — a realistic large result set — driven by
    a *small* request, so tests exercise the RESPONSE read path in isolation.
    """

    async def search(self, query: str, namespace: str, prefer_repo: str | None = None) -> dict:
        node = {
            "uuid": "big-node",
            "name": "big stub result",
            "summary": "y" * _BIG_RESPONSE_BYTES,
            "agent": "copilot",
        }
        return {"nodes": [node], "edges": [], "scores": [1.0]}


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


def test_large_note_roundtrips_ok(tmp_path: Path) -> None:
    # A note whose JSON line far exceeds asyncio's default 64 KiB StreamReader
    # buffer must now round-trip cleanly — the regression proof for the 64 KiB
    # cliff that used to mislabel a big valid note as bad_json.
    endpoint = resolve_endpoint(tmp_path)
    big = "x" * (200 * 1024)  # 200 KiB of content → line well over the old 64 KiB cap

    async def scenario() -> dict | None:
        return await _serve_and(
            endpoint,
            lambda: _roundtrip(endpoint, {"method": "note", "content": big, "namespace": "ns"}),
        )

    result = asyncio.run(scenario())
    assert result == {"status": "ok"}


def test_oversize_frame_returns_payload_too_large(tmp_path: Path) -> None:
    # With a small read limit, an over-limit request line gets a clean
    # payload_too_large envelope — not bad_json, and not a dropped/hung connection.
    endpoint = resolve_endpoint(tmp_path)
    oversize = "x" * 8192  # comfortably past the 4 KiB read_limit below

    async def scenario() -> dict | None:
        server = DaemonServer(StubBackend(), endpoint, read_limit=4096)
        await server.start()
        try:
            return await _roundtrip(
                endpoint, {"method": "note", "content": oversize, "namespace": "ns"}
            )
        finally:
            await server.stop()

    result = asyncio.run(scenario())
    assert result["error"]["type"] == "payload_too_large"


def test_idle_connection_is_closed_without_crashing(tmp_path: Path) -> None:
    # A client that connects but never sends a full line is reclaimed after the
    # idle timeout; the server neither crashes nor stops serving other clients.
    endpoint = resolve_endpoint(tmp_path)

    async def scenario() -> tuple:
        server = DaemonServer(StubBackend(), endpoint, idle_timeout=0.2)
        await server.start()
        try:
            # Stalled connection: connect and send nothing at all.
            reader, writer = await transport.connect(endpoint, timeout=5.0)
            try:
                # The server closes it after idle_timeout, so our read hits EOF.
                idle_eof = await asyncio.wait_for(transport.read_message(reader), timeout=5.0)
            finally:
                writer.close()
            # A fresh connection is still served normally after the idle reclaim.
            health = await _roundtrip(endpoint, {"method": "health"})
            return idle_eof, health
        finally:
            await server.stop()

    idle_eof, health = asyncio.run(scenario())
    assert idle_eof is None  # server closed the idle connection cleanly (EOF)
    assert health["status"] == "running"


def test_large_response_roundtrips_ok(tmp_path: Path) -> None:
    # Symmetric partner of test_large_note_roundtrips_ok, on the RESPONSE side: a
    # *small* request whose *reply* far exceeds asyncio's default 64 KiB client
    # StreamReader buffer must round-trip cleanly. The daemon frames the big reply
    # fine; before connect() raised the client's read limit, the client's
    # readline() overran on the 64 KiB cap and broke the round-trip. Proves the
    # invariant: a reply the daemon can produce, the client can read.
    endpoint = resolve_endpoint(tmp_path)

    async def scenario() -> dict | None:
        server = DaemonServer(_BigResponseBackend(), endpoint)
        await server.start()
        try:
            return await _roundtrip(endpoint, {"method": "search", "query": "q", "namespace": "ns"})
        finally:
            await server.stop()

    result = asyncio.run(scenario())
    assert set(result) == {"nodes", "edges", "scores"}
    assert len(result["nodes"][0]["summary"]) == _BIG_RESPONSE_BYTES


def test_oversize_response_raises_symmetric_error(tmp_path: Path) -> None:
    # A reply larger than the *client's* read limit must fail with the same clear,
    # named error the server raises on an over-limit request (MessageTooLarge) —
    # never an opaque asyncio overrun. We shrink only the client's limit (the
    # daemon still frames the ~200 KiB reply fine) to force the over-limit path
    # deterministically, keeping the failure symmetric with the request side.
    endpoint = resolve_endpoint(tmp_path)

    async def scenario() -> None:
        server = DaemonServer(_BigResponseBackend(), endpoint)
        await server.start()
        try:
            reader, writer = await transport.connect(endpoint, timeout=5.0, limit=4096)
            try:
                await transport.write_message(
                    writer, {"method": "search", "query": "q", "namespace": "ns"}
                )
                with pytest.raises(transport.MessageTooLarge):
                    await transport.read_message(reader)
            finally:
                writer.close()
        finally:
            await server.stop()

    asyncio.run(scenario())
