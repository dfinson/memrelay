"""Hermetic live-path test for the E12-S6 framework providers (#72, invariant E).

The conformance matrix and unit tests cover the *replay* branch of ``make_source`` (sync,
fixture-backed). This module exercises the **production** branch — the real, asynchronous
traceforge ``HttpPollSource`` / ``SSESource`` that ``make_source()`` builds from an opt-in
endpoint — end-to-end, but with the HTTP layer replaced by an in-memory
``httpx.MockTransport``. So one poll provider and one stream provider are driven from live
source → ``RawRecord`` → adapter → canonical ``SessionEvent``s **without touching the
network**.

Two deliberate mechanics:

* The traceforge sources allocate their own ``httpx.AsyncClient`` on ``__aenter__`` with no
  transport injection point, so we monkeypatch ``httpx.AsyncClient`` to inject a
  ``MockTransport`` (the sources look ``AsyncClient`` up on the ``httpx`` module at call time).
* These sources are ``async``, and this suite runs with ``PYTEST_DISABLE_PLUGIN_AUTOLOAD=1``
  (no ``pytest-asyncio``), so each test drives its coroutine with ``asyncio.run`` rather than
  an ``async def`` test.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from traceforge.sources import HttpPollSource, SSESource

from memrelay.providers._live_source import (
    LIVE_CONNECT_TIMEOUT_SECONDS,
    LIVE_HTTP_POLL_TIMEOUT_SECONDS,
    LIVE_SSE_MAX_RECONNECTS,
    LIVE_SSE_READ_TIMEOUT_SECONDS,
)
from memrelay.providers.crewai import CrewaiProvider
from memrelay.providers.langgraph import LangGraphProvider

# The exact framework payloads the mock endpoints serve (one JSON object per line), and the
# canonical kinds the provider's adapter must normalize them to — the same shapes the
# committed fixtures use, kept inline (as dicts + ``json.dumps``) so this test is
# self-contained.
CREWAI_RECORDS = [
    {"type": "crew_kickoff_started", "timestamp": "2026-01-01T00:00:00Z", "crew_name": "demo"},
    {
        "type": "agent_execution_started",
        "timestamp": "2026-01-01T00:00:01Z",
        "agent_id": "a1",
        "agent_role": "researcher",
        "task_name": "gather",
    },
    {
        "type": "tool_usage_started",
        "timestamp": "2026-01-01T00:00:02Z",
        "tool_name": "web_search",
        "event_id": "t1",
        "tool_args": {"q": "x"},
    },
    {
        "type": "tool_usage_finished",
        "timestamp": "2026-01-01T00:00:03Z",
        "tool_name": "web_search",
        "output": "ok",
    },
    {
        "type": "task_completed",
        "timestamp": "2026-01-01T00:00:04Z",
        "task_id": "k1",
        "task_name": "gather",
        "output": "done",
    },
    {
        "type": "crew_kickoff_completed",
        "timestamp": "2026-01-01T00:00:05Z",
        "crew_name": "demo",
        "output": "final",
    },
]
CREWAI_LINES = [json.dumps(record) for record in CREWAI_RECORDS]
CREWAI_KINDS = {
    "session.started",
    "agent.spawned",
    "tool.call.started",
    "tool.call.completed",
    "task.completed",
    "session.ended",
}

LANGGRAPH_RECORDS = [
    {"event": "on_chain_start", "run_id": "r1", "name": "graph", "data": {"input": {"q": "hi"}}},
    {
        "event": "on_chat_model_start",
        "run_id": "r2",
        "name": "model",
        "metadata": {"ls_model_name": "gpt-4o"},
    },
    {
        "event": "on_chat_model_end",
        "run_id": "r2",
        "metadata": {"ls_model_name": "gpt-4o"},
        "data": {
            "output": {
                "content": "hello",
                "usage_metadata": {"input_tokens": 5, "output_tokens": 3, "total_tokens": 8},
            }
        },
    },
    {"event": "on_tool_start", "run_id": "r3", "name": "search", "data": {"input": {"q": "x"}}},
    {"event": "on_tool_end", "run_id": "r3", "name": "search", "data": {"output": "result"}},
    {
        "event": "on_chain_end",
        "run_id": "r1",
        "name": "graph",
        "data": {"output": {"answer": "done"}},
    },
]
LANGGRAPH_LINES = [json.dumps(record) for record in LANGGRAPH_RECORDS]
LANGGRAPH_KINDS = {
    "workflow.started",
    "llm.call.started",
    "llm.call.completed",
    "tool.call.started",
    "tool.call.completed",
    "workflow.completed",
}


def _patch_transport(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    """Replace ``httpx.AsyncClient`` with one wired to an in-memory ``MockTransport``."""
    real_async_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)


def test_http_poll_live_path_yields_canonical_events(monkeypatch: pytest.MonkeyPatch) -> None:
    """crewai (http_poll): a mocked poll body flows through ``HttpPollSource`` → adapter."""
    body = "\n".join(CREWAI_LINES) + "\n"

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "http://crewai.local/trace"
        return httpx.Response(200, headers={"ETag": "v1"}, text=body)

    _patch_transport(monkeypatch, handler)

    provider = CrewaiProvider.from_home("http://crewai.local/trace")
    source = provider.make_source()
    assert isinstance(source, HttpPollSource)

    async def drive() -> str:
        # First poll returns the whole accumulated trace as a single record; break before
        # the source's inter-poll sleep so the test neither waits nor loops.
        async with source as opened:
            async for record in opened:
                return record.payload
        raise AssertionError("HttpPollSource yielded no record")

    payload = asyncio.run(drive())

    adapter = provider.make_adapter("live-crewai")
    events = []
    for line in payload.splitlines():
        if line.strip():
            events.extend(adapter.parse(line))
    assert {str(e.kind) for e in events} == CREWAI_KINDS
    assert all(e.session_id == "live-crewai" for e in events)


def test_sse_live_path_yields_canonical_events(monkeypatch: pytest.MonkeyPatch) -> None:
    """langgraph (sse): mocked ``text/event-stream`` events flow through ``SSESource`` → adapter."""
    stream_body = "".join(f"data: {line}\n\n" for line in LANGGRAPH_LINES)

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "http://langgraph.local/events"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=stream_body)

    _patch_transport(monkeypatch, handler)

    provider = LangGraphProvider.from_home("http://langgraph.local/events")
    source = provider.make_source()
    assert isinstance(source, SSESource)

    async def drive() -> list[str]:
        payloads: list[str] = []
        async with source as opened:
            async for record in opened:
                payloads.append(record.payload)
                if len(payloads) >= len(LANGGRAPH_LINES):
                    break
        return payloads

    payloads = asyncio.run(drive())
    assert len(payloads) == len(LANGGRAPH_LINES)

    adapter = provider.make_adapter("live-langgraph")
    events = [event for line in payloads for event in adapter.parse(line)]
    assert {str(e.kind) for e in events} == LANGGRAPH_KINDS
    assert all(e.session_id == "live-langgraph" for e in events)


# ── bounded-timeout coverage (rt-providers F1) ───────────────────────────────
# The live traceforge sources hardcode/inherit their httpx client timeout in ``__aenter__``
# (SSE: ``timeout=None`` → a black-hole endpoint reads forever). ``make_source()`` must instead
# build timeout-bounded subclasses. These tests assert the built source carries the bounded
# read+connect timeout — the pragmatic, network-free stand-in for "does not hang forever"
# (they genuinely fail on pre-fix code, where the SSE client is unbounded and the poll client
# uses httpx's 5 s default rather than the explicit bound).


def _entered_client_timeout(source) -> httpx.Timeout:
    """Enter ``source`` and return its httpx client's configured timeout, then release it.

    Entering allocates the client but issues **no** request (the traceforge sources only touch
    the network on iteration), so this stays fully hermetic — no sockets, no monkeypatch.
    """

    async def _run() -> httpx.Timeout:
        async with source as opened:
            return opened._client.timeout

    return asyncio.run(_run())


def test_sse_source_bounds_read_and_connect_timeout() -> None:
    """SSE (the F1 fix): the live ``SSESource`` must build its httpx client with a BOUNDED
    read+connect timeout, so a black-hole endpoint (TCP up, no bytes) cannot read forever.
    Pre-fix the client is ``timeout=None`` (unbounded) and the assertions below fail."""
    source = LangGraphProvider.from_home("http://langgraph.local/events").make_source()
    assert isinstance(source, SSESource)
    # Reconnect policy is intentionally unbounded — the read timeout, not a reconnect cap, is
    # what breaks the hang; pin it so a silent regression to a finite cap is caught.
    assert source.max_reconnects is LIVE_SSE_MAX_RECONNECTS
    timeout = _entered_client_timeout(source)
    assert timeout.read == LIVE_SSE_READ_TIMEOUT_SECONDS
    assert timeout.connect == LIVE_CONNECT_TIMEOUT_SECONDS


def test_http_poll_source_bounds_read_and_connect_timeout() -> None:
    """HTTP-poll (hardening): the live ``HttpPollSource`` must build its httpx client with an
    explicit bounded timeout. Pre-fix the client uses httpx's 5 s default, so asserting the
    explicit 30 s read bound fails until the fix sets it."""
    source = CrewaiProvider.from_home("http://crewai.local/trace").make_source()
    assert isinstance(source, HttpPollSource)
    timeout = _entered_client_timeout(source)
    assert timeout.read == LIVE_HTTP_POLL_TIMEOUT_SECONDS
    assert timeout.connect == LIVE_CONNECT_TIMEOUT_SECONDS
