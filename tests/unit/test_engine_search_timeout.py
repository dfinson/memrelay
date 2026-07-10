"""Unit tests for the recall latency guard (E8-S4 AC2, SPEC §4.3).

``MemoryEngine.search`` wraps its single Graphiti ``search_`` call in
:func:`asyncio.wait_for` bounded by an injectable ``search_timeout``. When the graph query
overruns, recall must degrade to an empty-but-valid payload rather than hang or raise, so a slow
backend never wedges the agent. These pin that contract without any wall-clock wait: the "slow"
search blocks on an :class:`asyncio.Event` that is never set, and ``search_timeout=0`` makes
``wait_for`` cancel it on the first loop turn (deterministic, no sleeps). A companion test proves
the guard is transparent on the happy path — a search that returns in time is unaffected.

Driven with ``asyncio.run`` (the suite does not depend on pytest-asyncio).
"""

from __future__ import annotations

import asyncio
from typing import Any

from memrelay.config import Config
from memrelay.engine.graphiti import MemoryEngine
from memrelay.mcp.format import format_as_map

_EMPTY_RESULT = {"nodes": [], "edges": [], "scores": []}


class _BlockingGraphiti:
    """A Graphiti stand-in whose ``search_`` blocks forever on a gate we never open."""

    def __init__(self) -> None:
        self.gate = asyncio.Event()  # never set -> the coroutine can only end via cancellation

    async def search_(self, **_kwargs: Any) -> Any:
        await self.gate.wait()  # blocks until the timeout cancels it; never returns
        raise AssertionError("unreachable")  # pragma: no cover


class _Node:
    def __init__(self, uuid: str, name: str, summary: str | None = None) -> None:
        self.uuid = uuid
        self.name = name
        self.summary = summary


class _Results:
    def __init__(self) -> None:
        self.nodes = [_Node("u0", "N0", "top fact")]
        self.node_reranker_scores = [0.9]
        self.edges: list[Any] = []


class _OkGraphiti:
    """A Graphiti stand-in whose ``search_`` returns promptly with a normal result object."""

    async def search_(self, **_kwargs: Any) -> _Results:
        return _Results()


def test_search_timeout_returns_empty_valid_map_without_raising() -> None:
    # (AC2) a graph query that overruns the budget yields an empty-but-valid payload -- no hang,
    # no raise -- which the formatter renders as the ordinary not-found map.
    graphiti = _BlockingGraphiti()
    engine = MemoryEngine(graphiti=graphiti, driver=object(), cfg=Config(), search_timeout=0)

    result = asyncio.run(engine.search("any query", "ns"))

    assert result == _EMPTY_RESULT  # empty-but-valid wire schema (nodes/edges/scores aligned)
    assert format_as_map(result) == "No relevant memories found."  # renders as a valid empty map


def test_search_timeout_empty_result_survives_every_recall_arg() -> None:
    # (AC2) the graceful-empty path is independent of the soft prefer_* re-rank knobs: a timeout
    # returns the same empty payload whether or not repo/agent preferences are supplied.
    graphiti = _BlockingGraphiti()
    engine = MemoryEngine(graphiti=graphiti, driver=object(), cfg=Config(), search_timeout=0)

    result = asyncio.run(engine.search("q", "ns", prefer_repo="acme/app", prefer_agent="claude"))

    assert result == _EMPTY_RESULT


def test_search_within_budget_is_unaffected_by_the_guard() -> None:
    # (AC2 no-regress) a search that completes within the timeout is transparent: the guard does
    # not alter the success payload, so the retrieval ranking downstream stays byte-identical.
    engine = MemoryEngine(graphiti=_OkGraphiti(), driver=object(), cfg=Config(), search_timeout=5.0)

    result = asyncio.run(engine.search("q", "ns"))

    assert result == {
        "nodes": [{"uuid": "u0", "name": "N0", "summary": "top fact"}],
        "edges": [],
        "scores": [0.9],
    }
    assert format_as_map(result).startswith("## Memory Map")  # a real, non-empty map
