"""Unit tests for degradation-driven graph compaction (E9-S2 #59, SPEC §5.5).

Three layers are pinned here without any real graph:

* the **pure policy + summary helpers** in :mod:`memrelay.engine.compaction` — deterministic
  selection of the oldest/lowest-frequency episodes, the activity-scaled degradation trigger, and
  the order-independent summary key + bounded extractive digest;
* the engine's ``entity_edges`` reference-frequency counter ``_entity_edge_count`` (tolerant of the
  Kuzu ``STRING[]`` list and the Neptune joined string), and that a summary's marker is **inert** to
  the ``repo=`` / ``agent=`` parsers;
* the **gating** of :meth:`MemoryEngine.compact`, driven through a fake driver (recording every
  Cypher call) and a fake graphiti (recording ``add_episode`` / ``remove_episode``): off ⇒ zero
  queries, not-degraded ⇒ no writes, ``dry_run`` ⇒ reads only, and a fired pass adds exactly one
  summary then removes exactly the eligible originals.

The real end-to-end pass over LadybugDB (the graph actually shrinking, the gist still recalling,
shared entities surviving) lives in ``tests/integration/test_compaction.py``; here we prove the
deterministic *decisions*. Driven with ``asyncio.run`` (the suite needs no pytest-asyncio).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from graphiti_core.driver.driver import GraphProvider
from graphiti_core.edges import EntityEdge
from graphiti_core.errors import GroupsEdgesNotFoundError

from memrelay.config import CompactionConfig, Config
from memrelay.engine import compaction
from memrelay.engine.compaction import (
    MAX_SUMMARY_CHARS,
    EpisodeStat,
    build_digest,
    build_summary_content,
    compaction_source_description,
    degradation_fraction,
    is_compaction_summary,
    is_degraded,
    select_eligible,
    summary_key,
)
from memrelay.engine.graphiti import (
    MemoryEngine,
    _entity_edge_count,
    _episode_agent,
    _episode_repo,
)

# --------------------------------------------------------------------------- summary key


def test_summary_key_is_order_independent() -> None:
    # The *set* of compacted uuids determines the key, so a crash-retried pass (which may re-list
    # the victims in any order) yields the identical key and never a duplicate summary.
    assert summary_key(["b", "a", "c"]) == summary_key(["a", "b", "c"])
    assert summary_key(["a", "a", "b"]) == summary_key(["a", "b", "a"])


def test_summary_key_is_set_sensitive() -> None:
    # A different victim set is a different summary.
    assert summary_key(["a", "b"]) != summary_key(["a", "c"])
    assert summary_key(["a", "b"]) != summary_key(["a", "b", "c"])


def test_summary_key_is_hex_sha256() -> None:
    key = summary_key(["a", "b"])
    assert len(key) == 64 and all(c in "0123456789abcdef" for c in key)


# --------------------------------------------------------------------------- digest / content


def test_build_digest_is_whitespace_normalized() -> None:
    # Extractive, not generative: content is folded verbatim with runs of whitespace collapsed.
    assert build_digest(["  hello   world \n"]) == "hello world"
    assert build_digest(["a", "  ", "b"]) == "a | b"  # blank episode contributes nothing


def test_build_digest_is_bounded() -> None:
    # No matter how large the compacted episodes were, the digest is clamped — the property that
    # lets compaction reclaim space.
    digest = build_digest([("x" * 1000) for _ in range(20)])
    assert len(digest) <= MAX_SUMMARY_CHARS


def test_build_summary_content_carries_marker_and_count() -> None:
    content = build_summary_content(["first fact", "second fact"])
    assert content.startswith("[memrelay compaction] 2 episode(s):")
    assert "first fact" in content


def test_build_summary_content_is_deterministic() -> None:
    # Same inputs ⇒ byte-identical output (no model, no randomness) ⇒ idempotent re-runs.
    contents = ["alpha", "beta", "gamma"]
    assert build_summary_content(contents) == build_summary_content(contents)


# --------------------------------------------------------------------------- marker inertness


def test_compaction_marker_round_trips() -> None:
    sd = compaction_source_description("deadbeef")
    assert is_compaction_summary(sd)
    assert not is_compaction_summary("repo=acme/app agent=copilot")
    assert not is_compaction_summary("memrelay-note")
    assert not is_compaction_summary(None)


def test_compaction_marker_is_inert_to_repo_and_agent_parsers() -> None:
    # A summary's source_description carries no repo=/agent= token, so it is never mistaken for a
    # repo or agent memory by forget --repo / prefer-agent.
    sd = compaction_source_description("cafef00d")
    assert _episode_repo(sd) is None
    assert _episode_agent(sd) is None


# --------------------------------------------------------------------------- selection


def _stat(uuid: str, valid_at: int, ref_count: int) -> EpisodeStat:
    return EpisodeStat(uuid=uuid, valid_at=valid_at, ref_count=ref_count, content=uuid)


def test_select_eligible_picks_oldest_low_frequency() -> None:
    # Oldest→newest by valid_at; protect the newest 2; of the rest keep ref_count <= 1.
    stats = [
        _stat("u1", 1, 0),  # oldest, low-ref -> eligible
        _stat("u2", 2, 5),  # old but high-ref -> preserved
        _stat("u3", 3, 1),  # old, low-ref -> eligible
        _stat("u4", 4, 0),  # protected (newest 2)
        _stat("u5", 5, 0),  # protected (newest 2)
    ]
    eligible = select_eligible(stats, low_reference_max=1, protected_recent=2)
    assert [s.uuid for s in eligible] == ["u1", "u3"]


def test_select_eligible_protects_recency_window() -> None:
    # A fresh low-ref episode inside the protected window is never selected, even though it is
    # low-frequency — recency, not just frequency, guards the hot working set.
    stats = [_stat(f"u{i}", i, 0) for i in range(1, 5)]  # all low-ref
    # protect the newest 4 of 4 -> nothing eligible
    assert select_eligible(stats, low_reference_max=1, protected_recent=4) == []


def test_select_eligible_is_order_independent_and_stable() -> None:
    # Shuffled input yields the same deterministic oldest-first result (total order via valid_at).
    stats = [_stat("u1", 1, 0), _stat("u2", 2, 0), _stat("u3", 3, 0)]
    forward = select_eligible(stats, low_reference_max=1, protected_recent=0)
    backward = select_eligible(list(reversed(stats)), low_reference_max=1, protected_recent=0)
    assert [s.uuid for s in forward] == [s.uuid for s in backward] == ["u1", "u2", "u3"]


def test_select_eligible_breaks_ties_by_uuid() -> None:
    # Equal valid_at -> uuid breaks the tie for a fully deterministic order.
    stats = [_stat("b", 1, 0), _stat("a", 1, 0), _stat("c", 1, 0)]
    eligible = select_eligible(stats, low_reference_max=1, protected_recent=0)
    assert [s.uuid for s in eligible] == ["a", "b", "c"]


def test_select_eligible_handles_datetime_valid_at() -> None:
    # Real episodes carry datetime valid_at; oldest-first ordering must hold for those too.
    old = EpisodeStat("old", datetime(2024, 1, 1, tzinfo=UTC), 0, "old")
    new = EpisodeStat("new", datetime(2025, 1, 1, tzinfo=UTC), 0, "new")
    eligible = select_eligible([new, old], low_reference_max=1, protected_recent=0)
    assert [s.uuid for s in eligible] == ["old", "new"]


# --------------------------------------------------------------------------- trigger


def test_is_degraded_requires_activity_floor() -> None:
    # Below min_episodes a namespace is too quiet to compact, no matter how stale.
    assert is_degraded(3, 3, degradation_ratio=0.5, min_episodes=4) is False


def test_is_degraded_uses_ceil_of_ratio() -> None:
    # bar = ceil(0.5 * 8) = 4.
    assert is_degraded(4, 8, degradation_ratio=0.5, min_episodes=4) is True
    assert is_degraded(3, 8, degradation_ratio=0.5, min_episodes=4) is False
    # bar = ceil(0.5 * 5) = 3.
    assert is_degraded(3, 5, degradation_ratio=0.5, min_episodes=4) is True
    assert is_degraded(2, 5, degradation_ratio=0.5, min_episodes=4) is False


def test_busier_namespace_has_more_eligible_at_same_saturation() -> None:
    # Both namespaces are fully low-ref; protecting the newest `protect_recent` leaves the busier
    # one with strictly more eligible episodes — the mechanism behind "busier compacts more".
    busy = [_stat(f"b{i}", i, 0) for i in range(1, 17)]  # E=16
    quiet = [_stat(f"q{i}", i, 0) for i in range(1, 9)]  # E=8
    busy_eligible = select_eligible(busy, low_reference_max=1, protected_recent=4)
    quiet_eligible = select_eligible(quiet, low_reference_max=1, protected_recent=4)
    assert len(busy_eligible) == 12
    assert len(quiet_eligible) == 4
    assert len(busy_eligible) > len(quiet_eligible)
    assert is_degraded(len(busy_eligible), 16, degradation_ratio=0.5, min_episodes=4)
    assert is_degraded(len(quiet_eligible), 8, degradation_ratio=0.5, min_episodes=4)


def test_protect_recent_shields_independently_of_the_activity_floor() -> None:
    # min_episodes (the floor) and protect_recent (the window) are independent knobs: widening the
    # protection window shrinks the eligible set while the floor stays put (manager ruling Q2).
    stats = [_stat(f"u{i}", i, 0) for i in range(1, 9)]  # E=8, all low-ref
    # A wide window of 6 shields the newest 6 -> only 2 eligible, below the ceil(0.5*8)=4 bar, so
    # the pass would NOT fire ...
    eligible_wide = select_eligible(stats, low_reference_max=1, protected_recent=6)
    assert len(eligible_wide) == 2
    assert is_degraded(len(eligible_wide), 8, degradation_ratio=0.5, min_episodes=4) is False
    # ... whereas a narrower window of 4 leaves 4 eligible and the pass fires (same floor both).
    eligible_narrow = select_eligible(stats, low_reference_max=1, protected_recent=4)
    assert len(eligible_narrow) == 4
    assert is_degraded(len(eligible_narrow), 8, degradation_ratio=0.5, min_episodes=4) is True


def test_degradation_fraction_is_eligible_over_episodes() -> None:
    # The deterministic graph-derived proxy is_degraded thresholds and compact() reports
    # before/after. An empty working set is 0.0, never a divide-by-zero.
    assert degradation_fraction(4, 8) == 0.5
    assert degradation_fraction(3, 8) == 0.375
    assert degradation_fraction(16, 16) == 1.0
    assert degradation_fraction(0, 5) == 0.0
    assert degradation_fraction(0, 0) == 0.0
    assert degradation_fraction(1, 0) == 0.0


# --------------------------------------------------------------------------- ref-count parsing


def test_entity_edge_count_counts_kuzu_list() -> None:
    # LadybugDB/Kuzu store entity_edges as a native STRING[] -> the driver returns a Python list.
    assert _entity_edge_count(["e1", "e2", "e3"]) == 3
    assert _entity_edge_count([]) == 0


def test_entity_edge_count_counts_neptune_joined_string() -> None:
    # Neptune joins the uuids with '|' -> a length count, never the string length.
    assert _entity_edge_count("e1|e2|e3") == 3
    assert _entity_edge_count("e1") == 1
    assert _entity_edge_count("") == 0


def test_entity_edge_count_handles_none() -> None:
    assert _entity_edge_count(None) == 0


# --------------------------------------------------------------------------- fakes


class _FakeGraphiti:
    """Records the summary added and the originals removed, standing in for graphiti-core."""

    def __init__(self) -> None:
        self.added: list[dict[str, Any]] = []
        self.removed: list[str] = []

    async def add_episode(self, **kwargs: Any) -> None:
        self.added.append(kwargs)

    async def remove_episode(self, uuid: str) -> None:
        self.removed.append(uuid)


class _FakeDriver:
    """A graph driver stand-in that records every Cypher call and serves canned episode rows.

    Routes by query shape: the namespace-discovery ``DISTINCT`` scan, the per-namespace episode
    scan (uuid/valid_at/content/source_description/entity_edges), and the ``count(e)`` /
    ``count(n)`` measurements. Every ``(cypher, kwargs)`` is captured so a test can assert *whether*
    the driver was touched (e.g. the disabled path issues no query at all).
    """

    def __init__(
        self,
        provider: GraphProvider,
        rows_by_ns: dict[str, list[dict[str, Any]]],
        *,
        entity_count: int = 0,
    ) -> None:
        self.provider = provider
        self._rows_by_ns = rows_by_ns
        self.entity_count = entity_count
        self.queries: list[tuple[str, dict[str, Any]]] = []

    async def execute_query(self, cypher: str, **kwargs: Any) -> tuple[list[Any], Any, Any]:
        self.queries.append((cypher, kwargs))
        if "DISTINCT e.group_id" in cypher:
            return ([{"group_id": ns} for ns in sorted(self._rows_by_ns)], None, None)
        if "count(e) AS episode_count" in cypher:
            rows = self._rows_by_ns.get(kwargs.get("group_id"), [])
            return ([{"episode_count": len(rows)}], None, None)
        if "count(n) AS entity_count" in cypher:
            return ([{"entity_count": self.entity_count}], None, None)
        if "MATCH (e:Episodic)" in cypher and "entity_edges" in cypher:
            return (self._rows_by_ns.get(kwargs.get("group_id"), []), None, None)
        return ([], None, None)


def _row(uuid: str, valid_at: int, ref_count: int, *, summary: bool = False) -> dict[str, Any]:
    sd = compaction_source_description(uuid) if summary else "memrelay-note"
    return {
        "uuid": uuid,
        "valid_at": valid_at,
        "content": f"content of {uuid}",
        "source_description": sd,
        "entity_edges": ["edge"] * ref_count,
    }


def _engine(driver: _FakeDriver, graphiti: Any, cfg: CompactionConfig) -> MemoryEngine:
    return MemoryEngine(
        graphiti=graphiti, driver=driver, cfg=Config(compaction=cfg), search_timeout=5.0
    )


def _patch_edges(monkeypatch, edges: list[Any] | Exception) -> None:
    async def _fake(_driver: Any, _group_ids: list[str], **_kwargs: Any) -> list[Any]:
        if isinstance(edges, Exception):
            raise edges
        return edges

    monkeypatch.setattr(EntityEdge, "get_by_group_ids", _fake)


# --------------------------------------------------------------------------- config knobs


def test_compaction_config_defaults_are_all_off() -> None:
    # Opt-in / default-off: the five knobs ship in the inert position, so compact() is a
    # byte-identical no-op until a caller explicitly enables it.
    cfg = CompactionConfig()
    assert cfg.enabled is False
    assert cfg.low_reference_max == 1
    assert cfg.degradation_ratio == 0.5
    assert cfg.min_episodes == 8
    assert cfg.protect_recent == 4
    # Config always carries a compaction section, defaulted off.
    assert Config().compaction == CompactionConfig()


def test_compaction_config_knobs_are_independent() -> None:
    # The activity floor (min_episodes) and the protected-recency window (protect_recent) are
    # separate knobs (manager ruling Q2): setting one must not move the other.
    cfg = CompactionConfig(
        enabled=True,
        low_reference_max=2,
        degradation_ratio=0.25,
        min_episodes=10,
        protect_recent=3,
    )
    assert cfg.enabled is True
    assert cfg.low_reference_max == 2
    assert cfg.degradation_ratio == 0.25
    assert cfg.min_episodes == 10
    assert cfg.protect_recent == 3


# --------------------------------------------------------------------------- gating (off)


def test_disabled_compaction_is_inert() -> None:
    # The default (enabled=False) issues NO graph query and reports zeroed metrics — the
    # byte-identical-off guarantee.
    driver = _FakeDriver(GraphProvider.KUZU, {"ns": [_row("u", 1, 0) for _ in range(1)]})
    graphiti = _FakeGraphiti()
    engine = _engine(driver, graphiti, CompactionConfig(enabled=False))

    result = asyncio.run(engine.compact("ns"))

    assert result == {
        "enabled": False,
        "dry_run": False,
        "namespaces": {},
        "episodes_compacted": 0,
        "summaries_added": 0,
    }
    assert driver.queries == [], "disabled compaction must never touch the driver"
    assert graphiti.added == [] and graphiti.removed == []


# --------------------------------------------------------------------------- gating (no-op)


def test_not_degraded_namespace_writes_nothing(monkeypatch) -> None:
    # Enough episodes, but not enough stale low-value mass to cross the bar: the pass reads and
    # measures, but adds no summary and removes nothing.
    rows = {"ns": [_row(f"u{i}", i, 0) for i in range(1, 5)]}  # E=4, all protected by min_episodes
    driver = _FakeDriver(GraphProvider.KUZU, rows)
    graphiti = _FakeGraphiti()
    engine = _engine(driver, graphiti, CompactionConfig(enabled=True, min_episodes=4))
    _patch_edges(monkeypatch, [])

    result = asyncio.run(engine.compact("ns"))

    assert result["episodes_compacted"] == 0
    assert result["namespaces"]["ns"]["triggered"] is False
    assert graphiti.added == [] and graphiti.removed == []


def test_dry_run_reads_but_never_writes(monkeypatch) -> None:
    # A degraded namespace under dry_run reports what WOULD compact (eligible) but writes nothing.
    rows = {"ns": [_row(f"u{i}", i, 0) for i in range(1, 9)]}  # E=8, all low-ref
    driver = _FakeDriver(GraphProvider.KUZU, rows)
    graphiti = _FakeGraphiti()
    engine = _engine(driver, graphiti, CompactionConfig(enabled=True, min_episodes=4))
    _patch_edges(monkeypatch, [])

    result = asyncio.run(engine.compact("ns", dry_run=True))

    ns_metrics = result["namespaces"]["ns"]
    assert result["dry_run"] is True
    assert ns_metrics["triggered"] is True
    assert ns_metrics["eligible"] == 4  # newest 4 protected, oldest 4 eligible
    assert ns_metrics["episodes_compacted"] == 0
    assert ns_metrics["degradation_fraction_before"] == 0.5  # eligible 4 / E 8
    assert ns_metrics["degradation_fraction_after"] == 0.5  # dry_run leaves the graph untouched
    assert graphiti.added == [] and graphiti.removed == []


# --------------------------------------------------------------------------- gating (fires)


def test_fired_pass_adds_one_summary_then_removes_eligible(monkeypatch) -> None:
    # A degraded namespace: exactly one summary is added, then exactly the eligible (oldest,
    # low-ref) originals are removed via the cascade. High-ref and protected-recent episodes stay.
    rows = {
        "ns": [
            _row("old1", 1, 0),  # eligible
            _row("old2", 2, 0),  # eligible
            _row("old3", 3, 0),  # eligible
            _row("busy", 4, 9),  # high-ref -> preserved
            _row("hot1", 5, 0),  # protected (newest min_episodes=4)
            _row("hot2", 6, 0),  # protected
            _row("hot3", 7, 0),  # protected
            _row("hot4", 8, 0),  # protected
        ]
    }
    driver = _FakeDriver(GraphProvider.KUZU, rows)
    graphiti = _FakeGraphiti()
    # ratio 0.3 -> bar = ceil(0.3 * 8) = 3, so the 3 eligible originals trigger a pass while the
    # high-ref "busy" and the protected hot set stay out of it.
    engine = _engine(
        driver, graphiti, CompactionConfig(enabled=True, min_episodes=4, degradation_ratio=0.3)
    )
    _patch_edges(monkeypatch, [])

    result = asyncio.run(engine.compact("ns"))

    ns_metrics = result["namespaces"]["ns"]
    assert ns_metrics["triggered"] is True
    assert ns_metrics["eligible"] == 3
    assert ns_metrics["summaries_added"] == 1
    assert ns_metrics["episodes_compacted"] == 3
    assert result["episodes_compacted"] == 3 and result["summaries_added"] == 1
    assert ns_metrics["degradation_fraction_before"] == 0.375  # eligible 3 / E 8
    # The FakeDriver is stateless, so the re-read sees the same rows; the real fraction DROP after
    # removal is proven end-to-end in tests/integration/test_compaction.py.
    assert isinstance(ns_metrics["degradation_fraction_after"], float)
    # exactly one summary added, carrying the compaction marker over the eligible set
    assert len(graphiti.added) == 1
    assert is_compaction_summary(graphiti.added[0]["source_description"])
    assert graphiti.added[0]["group_id"] == "ns"
    # exactly the eligible originals removed (high-ref "busy" and the protected hot set survive)
    assert graphiti.removed == ["old1", "old2", "old3"]


def test_fired_pass_summary_key_matches_victims(monkeypatch) -> None:
    # The summary's source_description is deterministically keyed off the victim uuid set, so a
    # crash-retry (which recomputes the same key) can detect and skip a duplicate.
    rows = {"ns": [_row(f"u{i}", i, 0) for i in range(1, 9)]}  # E=8, oldest 4 eligible
    driver = _FakeDriver(GraphProvider.KUZU, rows)
    graphiti = _FakeGraphiti()
    engine = _engine(driver, graphiti, CompactionConfig(enabled=True, min_episodes=4))
    _patch_edges(monkeypatch, [])

    asyncio.run(engine.compact("ns"))

    victims = graphiti.removed
    expected_sd = compaction_source_description(summary_key(victims))
    assert graphiti.added[0]["source_description"] == expected_sd


def test_existing_summary_is_not_recreated(monkeypatch) -> None:
    # Crash-idempotency: if a summary for the exact victim set already exists (added before a crash
    # that rolled back the removals), a re-run removes the originals but does NOT add a 2nd summary.
    victims = ["u1", "u2", "u3", "u4"]
    existing_key = summary_key(victims)
    rows = {
        "ns": [_row(f"u{i}", i, 0) for i in range(1, 9)]
        + [
            {
                "uuid": "existing-summary",
                "valid_at": 99,
                "content": "prior summary",
                "source_description": compaction_source_description(existing_key),
                "entity_edges": [],
            }
        ]
    }
    driver = _FakeDriver(GraphProvider.KUZU, rows)
    graphiti = _FakeGraphiti()
    engine = _engine(driver, graphiti, CompactionConfig(enabled=True, min_episodes=4))
    _patch_edges(monkeypatch, [])

    result = asyncio.run(engine.compact("ns"))

    assert result["namespaces"]["ns"]["summaries_added"] == 0
    assert graphiti.added == [], "no duplicate summary for an already-summarized victim set"
    assert graphiti.removed == victims, "the originals are still removed"


def test_summaries_are_excluded_from_the_working_set(monkeypatch) -> None:
    # A pre-existing summary episode is never itself treated as a compaction candidate, so a
    # steady state cannot re-compact its own summaries (no thrash).
    rows = {
        "ns": [
            _row("s", 1, 0, summary=True),  # a summary -> excluded
            _row("hot1", 2, 0),
            _row("hot2", 3, 0),
        ]
    }
    driver = _FakeDriver(GraphProvider.KUZU, rows)
    graphiti = _FakeGraphiti()
    # min_episodes=4 but only 2 non-summary episodes -> below the activity floor -> no-op.
    engine = _engine(driver, graphiti, CompactionConfig(enabled=True, min_episodes=4))
    _patch_edges(monkeypatch, [])

    result = asyncio.run(engine.compact("ns"))

    assert result["namespaces"]["ns"]["triggered"] is False
    assert graphiti.removed == []


def test_compact_all_namespaces_sweeps_each(monkeypatch) -> None:
    # namespace=None discovers every group_id and compacts each independently; the busier namespace
    # compacts strictly more (activity-scaled), and no namespace's episodes cross into another.
    rows = {
        "busy": [_row(f"b{i}", i, 0) for i in range(1, 17)],  # E=16 -> 12 eligible
        "quiet": [_row(f"q{i}", i, 0) for i in range(1, 9)],  # E=8 -> 4 eligible
    }
    driver = _FakeDriver(GraphProvider.KUZU, rows)
    graphiti = _FakeGraphiti()
    engine = _engine(driver, graphiti, CompactionConfig(enabled=True, min_episodes=4))
    _patch_edges(monkeypatch, [])

    result = asyncio.run(engine.compact())

    busy = result["namespaces"]["busy"]["episodes_compacted"]
    quiet = result["namespaces"]["quiet"]["episodes_compacted"]
    assert busy == 12 and quiet == 4
    assert busy > quiet, "busier namespace compacts more"
    assert result["episodes_compacted"] == 16
    # each namespace removed only its own episodes (no cross-namespace bleed)
    assert sum(u.startswith("b") for u in graphiti.removed) == 12
    assert sum(u.startswith("q") for u in graphiti.removed) == 4


def test_missing_edges_group_is_handled_gracefully(monkeypatch) -> None:
    # A namespace whose edges are not yet materialised raises GroupsEdgesNotFoundError from the
    # loader; the edge-count metric swallows it and reports 0 rather than propagating.
    rows = {"ns": [_row(f"u{i}", i, 0) for i in range(1, 9)]}
    driver = _FakeDriver(GraphProvider.KUZU, rows)
    graphiti = _FakeGraphiti()
    engine = _engine(driver, graphiti, CompactionConfig(enabled=True, min_episodes=4))
    _patch_edges(monkeypatch, GroupsEdgesNotFoundError(["ns"]))

    result = asyncio.run(engine.compact("ns"))

    assert result["namespaces"]["ns"]["edges_before"] == 0
    assert result["namespaces"]["ns"]["edges_after"] == 0
    assert result["namespaces"]["ns"]["episodes_compacted"] == 4


def test_module_exposes_marker_constant() -> None:
    # The marker is a module constant so the engine and tests agree on one spelling.
    assert compaction.COMPACTION_MARKER == "memrelay-compaction"
