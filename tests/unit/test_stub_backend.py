"""Unit tests for the StubBackend + dispatch seam (E6-S3, backend contract)."""

from __future__ import annotations

import asyncio

from memrelay.daemon.protocol import StubBackend, dispatch, error_response


def test_search_shape_and_query_echo() -> None:
    result = asyncio.run(StubBackend().search("auth", "dfinson", "owner/repo"))
    assert set(result) == {"nodes", "edges", "scores"}
    assert len(result["nodes"]) == len(result["scores"])
    # The query flows into the payload (never as an extra top-level key).
    assert any("auth" in node["summary"] for node in result["nodes"])
    assert result["edges"][0]["source_node_uuid"] == result["nodes"][0]["uuid"]


def test_detail_shape_and_uuid_echo() -> None:
    result = asyncio.run(StubBackend().detail("abc-123", "ns"))
    assert set(result) == {"node", "connected_edges", "episodes"}
    assert result["node"]["uuid"] == "abc-123"
    assert "abc-123" in result["episodes"][0]["content"]


def test_note_returns_a_status_string() -> None:
    status = asyncio.run(StubBackend().note("a fact", "ns", "owner/repo"))
    assert isinstance(status, str)
    assert status


def test_health_metrics_shape() -> None:
    health = asyncio.run(StubBackend().health())
    assert health["status"] == "running"
    for key in ("sessions_observed", "episodes_ingested", "spool_pending"):
        assert isinstance(health[key], int)


def test_dispatch_unknown_method_is_error() -> None:
    result = asyncio.run(dispatch(StubBackend(), {"method": "bogus"}))
    assert result["error"]["type"] == "unknown_method"


def test_dispatch_missing_field_is_bad_request() -> None:
    # search requires 'query'; omitting it must not raise, just error out.
    result = asyncio.run(dispatch(StubBackend(), {"method": "search", "namespace": "ns"}))
    assert result["error"]["type"] == "bad_request"


def test_dispatch_note_wraps_status() -> None:
    result = asyncio.run(
        dispatch(StubBackend(), {"method": "note", "content": "x", "namespace": "ns"})
    )
    assert result == {"status": "ok"}


def test_dispatch_survives_backend_exception() -> None:
    class Boom(StubBackend):
        async def health(self) -> dict:
            raise RuntimeError("kaboom")

    result = asyncio.run(dispatch(Boom(), {"method": "health"}))
    assert result["error"]["type"] == "backend_error"
    assert "kaboom" in result["error"]["message"]


def test_error_response_envelope() -> None:
    assert error_response("t", "m") == {"error": {"type": "t", "message": "m"}}
