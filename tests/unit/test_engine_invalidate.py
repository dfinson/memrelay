"""Unit tests for the file-refactor invalidation path (E9-S3 #60, SPEC §5.5).

Two layers are pinned here without any real graph:

* the pure ``source_description`` parsers ``_episode_files`` / ``_episode_sha`` that recover a
  file episode's touched paths and stamped commit sha (the inverse of the ``file=`` / ``sha=``
  tokens :meth:`MemoryEngine.note` writes), and the ``_invalidate_edges_query`` provider branch;
* the selection + gating logic of :meth:`MemoryEngine.invalidate_file_facts`, driven through a
  fake driver (recording every Cypher call) and a monkeypatched ``EntityEdge.get_by_group_ids``.

The real end-to-end supersession over LadybugDB (edges actually gaining ``expired_at`` while the
node stays recallable) lives in ``tests/integration/test_invalidate.py``; here we prove the
deterministic *decisions*: when it stays inert, which episodes count as stale, which edges are
superseded, and that only the two temporal fields are written — no deletes.

Driven with ``asyncio.run`` (the suite does not depend on pytest-asyncio).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from graphiti_core.driver.driver import GraphProvider
from graphiti_core.edges import EntityEdge
from graphiti_core.errors import GroupsEdgesNotFoundError

from memrelay.config import Config, IngestConfig
from memrelay.engine.graphiti import (
    MemoryEngine,
    _episode_files,
    _episode_sha,
    _invalidate_edges_query,
)

# --------------------------------------------------------------------------- parsers


def test_episode_files_recovers_every_file_token() -> None:
    # A composed episode may touch several files; all file= tokens are collected as a set,
    # order-independently, coexisting with the repo=/agent=/sha= tokens on the same line.
    sd = "repo=acme/app agent=copilot file=src/a.py file=src/b.py sha=deadbeef"
    assert _episode_files(sd) == frozenset({"src/a.py", "src/b.py"})


def test_episode_files_is_empty_without_tokens() -> None:
    # The provenance-less forms (bare repo, agent-only, sentinel, empty/None) carry no file
    # tokens, so nothing is ever mistaken for a file episode.
    assert _episode_files(None) == frozenset()
    assert _episode_files("") == frozenset()
    assert _episode_files("memrelay-note") == frozenset()
    assert _episode_files("repo=acme/app agent=copilot") == frozenset()


def test_episode_files_skips_empty_value() -> None:
    # A degenerate ``file=`` with no value contributes nothing (never an empty-string path).
    assert _episode_files("file= sha=abc") == frozenset()


def test_episode_sha_recovers_single_sha() -> None:
    # The lone sha= token is recovered regardless of position among the other tokens.
    assert _episode_sha("file=src/a.py sha=cafe123 repo=acme/app") == "cafe123"


def test_episode_sha_is_none_without_token() -> None:
    # No sha token (or empty value / no key=value at all) → None, so an un-stamped episode is
    # never treated as belonging to a refactor generation (and so is never superseded).
    assert _episode_sha(None) is None
    assert _episode_sha("file=src/a.py") is None
    assert _episode_sha("sha=") is None
    assert _episode_sha("memrelay-note") is None


def test_invalidate_edges_query_branches_on_provider() -> None:
    # LadybugDB/Kuzu keep the RELATES_TO fact on an intermediary RelatesToNode_; other
    # providers keep it on the relationship — the match shape differs accordingly. Both set
    # ONLY the two temporal fields and never delete.
    kuzu = _invalidate_edges_query(GraphProvider.KUZU)
    other = _invalidate_edges_query(GraphProvider.NEO4J)

    assert "RelatesToNode_" in kuzu
    assert "RelatesToNode_" not in other
    for query in (kuzu, other):
        assert "SET e.expired_at = $ref_time, e.invalid_at = $ref_time" in query
        assert "DELETE" not in query.upper()


# --------------------------------------------------------------------------- fakes


class _FakeDriver:
    """A graph driver stand-in that records every Cypher call and serves canned episodes.

    ``execute_query`` returns the configured Episodic rows for the episode scan and an empty
    result for the temporal SET write; every ``(cypher, kwargs)`` is captured so a test can
    assert *whether* and *how* the driver was touched (e.g. inert paths issue no query at all).
    """

    def __init__(self, provider: GraphProvider, episodes: list[dict[str, Any]]) -> None:
        self.provider = provider
        self._episodes = episodes
        self.queries: list[tuple[str, dict[str, Any]]] = []

    async def execute_query(self, cypher: str, **kwargs: Any) -> tuple[list[Any], Any, Any]:
        self.queries.append((cypher, kwargs))
        if "MATCH (e:Episodic)" in cypher:
            return (self._episodes, None, None)
        return ([], None, None)


class _FakeEdge:
    """The slice of ``EntityEdge`` the invalidation selection reads: uuid, expiry, episodes."""

    def __init__(
        self, uuid: str, *, expired_at: datetime | None, episodes: list[str] | None
    ) -> None:
        self.uuid = uuid
        self.expired_at = expired_at
        self.episodes = episodes


def _engine(driver: _FakeDriver, *, threshold: int) -> MemoryEngine:
    cfg = Config(ingest=IngestConfig(refactor_invalidation_lines=threshold))
    return MemoryEngine(graphiti=object(), driver=driver, cfg=cfg, search_timeout=5.0)


def _patch_edges(monkeypatch, edges: list[_FakeEdge] | Exception) -> None:
    """Replace the async classmethod ``EntityEdge.get_by_group_ids`` with a deterministic stub."""

    async def _fake(_driver: Any, _group_ids: list[str], **_kwargs: Any) -> list[_FakeEdge]:
        if isinstance(edges, Exception):
            raise edges
        return edges

    monkeypatch.setattr(EntityEdge, "get_by_group_ids", _fake)


# --------------------------------------------------------------------------- gating (inert)


def test_disabled_threshold_is_inert() -> None:
    # The zero-config default (threshold 0) returns 0 and issues NO graph query — the feature
    # is byte-identical-inert until a positive knob is set.
    driver = _FakeDriver(GraphProvider.KUZU, [])
    engine = _engine(driver, threshold=0)

    result = asyncio.run(
        engine.invalidate_file_facts("ns", "src/a.py", "sha2", change_magnitude=999)
    )

    assert result == 0
    assert driver.queries == [], "disabled knob must never touch the driver"


def test_sub_threshold_magnitude_is_inert() -> None:
    # A change below the threshold is not a "big refactor": 0, and again no graph query runs.
    driver = _FakeDriver(GraphProvider.KUZU, [])
    engine = _engine(driver, threshold=100)

    result = asyncio.run(
        engine.invalidate_file_facts("ns", "src/a.py", "sha2", change_magnitude=99)
    )

    assert result == 0
    assert driver.queries == []


def test_no_matching_stale_episodes_short_circuits(monkeypatch) -> None:
    # Threshold met, but no prior episode is both this file AND a *different* sha, so there is
    # nothing to supersede: the episode scan runs, but the edge load / write never do.
    episodes = [
        {"uuid": "same", "source_description": "file=src/a.py sha=sha2"},  # same sha
        {"uuid": "nosha", "source_description": "file=src/a.py"},  # no sha
        {"uuid": "other", "source_description": "file=src/b.py sha=sha1"},  # other file
    ]
    driver = _FakeDriver(GraphProvider.KUZU, episodes)
    engine = _engine(driver, threshold=50)

    def _boom(*_a: Any, **_k: Any) -> None:  # pragma: no cover - must never be reached
        raise AssertionError("edges must not be loaded when no episode is stale")

    monkeypatch.setattr(EntityEdge, "get_by_group_ids", _boom)

    result = asyncio.run(
        engine.invalidate_file_facts("ns", "src/a.py", "sha2", change_magnitude=200)
    )

    assert result == 0
    assert len(driver.queries) == 1, "only the episode scan ran; no SET write issued"
    assert "MATCH (e:Episodic)" in driver.queries[0][0]


# --------------------------------------------------------------------------- gating (fires)


def test_supersedes_only_valid_edges_of_the_stale_file(monkeypatch) -> None:
    # End-to-end selection: exactly one prior episode is stale for src/a.py (different sha), and
    # exactly one still-valid edge references it — that edge (and only it) is temporally closed.
    ref = datetime(2025, 1, 1, tzinfo=UTC)
    episodes = [
        # stale: this file, stamped at an OLDER sha -> the one true supersession target. The
        # repo=/agent= tokens share the line to prove file/sha parsing coexists with them.
        {"uuid": "ep-old-a", "source_description": "repo=acme agent=cp file=src/a.py sha=sha1"},
        {"uuid": "ep-same-a", "source_description": "file=src/a.py sha=sha2"},  # same sha -> keep
        {"uuid": "ep-nosha-a", "source_description": "file=src/a.py"},  # no sha -> keep
        {"uuid": "ep-old-b", "source_description": "file=src/b.py sha=sha1"},  # other file -> keep
        {"uuid": "ep-plain", "source_description": "memrelay-note"},  # non-file -> keep
    ]
    edges = [
        _FakeEdge("edge-A", expired_at=None, episodes=["ep-old-a", "unrelated"]),  # TARGET
        _FakeEdge("edge-A-expired", expired_at=ref, episodes=["ep-old-a"]),  # already expired
        _FakeEdge("edge-B", expired_at=None, episodes=["ep-old-b"]),  # unrelated episode
        _FakeEdge("edge-none", expired_at=None, episodes=None),  # no episodes
    ]
    driver = _FakeDriver(GraphProvider.KUZU, episodes)
    engine = _engine(driver, threshold=50)
    _patch_edges(monkeypatch, edges)

    result = asyncio.run(
        engine.invalidate_file_facts(
            "ns", "src/a.py", "sha2", change_magnitude=200, reference_time=ref
        )
    )

    assert result == 1, "exactly the one valid edge tied to the stale file episode is superseded"
    # queries: [episode scan, SET write]
    assert len(driver.queries) == 2
    set_cypher, set_kwargs = driver.queries[1]
    assert set_cypher == _invalidate_edges_query(GraphProvider.KUZU)
    assert set_kwargs["uuids"] == ["edge-A"]
    assert set_kwargs["ref_time"] == ref, "the caller's reference_time flows through verbatim"


def test_missing_edges_group_is_handled_gracefully(monkeypatch) -> None:
    # A namespace whose edges are not yet materialised raises GroupsEdgesNotFoundError from the
    # loader; the method swallows it and reports 0 rather than propagating.
    episodes = [{"uuid": "ep-old-a", "source_description": "file=src/a.py sha=sha1"}]
    driver = _FakeDriver(GraphProvider.KUZU, episodes)
    engine = _engine(driver, threshold=50)
    _patch_edges(monkeypatch, GroupsEdgesNotFoundError(["ns"]))

    result = asyncio.run(
        engine.invalidate_file_facts("ns", "src/a.py", "sha2", change_magnitude=200)
    )

    assert result == 0
    assert len(driver.queries) == 1, "episode scan ran; no SET write after the edge load failed"


def test_stale_episodes_but_no_valid_edges_writes_nothing(monkeypatch) -> None:
    # Stale episodes exist, but every candidate edge is already expired, so there is nothing to
    # close: return 0 and issue no SET write.
    ref = datetime(2025, 1, 1, tzinfo=UTC)
    episodes = [{"uuid": "ep-old-a", "source_description": "file=src/a.py sha=sha1"}]
    edges = [_FakeEdge("edge-A", expired_at=ref, episodes=["ep-old-a"])]  # already expired
    driver = _FakeDriver(GraphProvider.KUZU, episodes)
    engine = _engine(driver, threshold=50)
    _patch_edges(monkeypatch, edges)

    result = asyncio.run(
        engine.invalidate_file_facts("ns", "src/a.py", "sha2", change_magnitude=200)
    )

    assert result == 0
    assert len(driver.queries) == 1, "no SET write when no valid edge is targeted"
