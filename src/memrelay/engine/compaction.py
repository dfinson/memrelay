"""Deterministic, offline policy for degradation-driven graph compaction (E9-S2 #59, SPEC §5.5).

As a namespace accumulates old, rarely-referenced episodes, ``memory_recall`` must scan and rank
ever more low-signal nodes, degrading recall latency and precision. A compaction *pass* folds a
namespace's **oldest, lowest-reference-frequency** episodes into **one deterministic extractive
summary** and removes the originals via the shared-entity-preserving cascade (#58), so the graph
shrinks while the gist stays recallable. Busier namespaces compact more aggressively.

The degradation trigger (:func:`is_degraded`) is a **deterministic, graph-derived proxy** for
§5.5's recall latency/precision degradation: the fraction of a namespace that is stale, low-value
mass (old + low-reference-frequency episodes). It is deliberately **not** a real wall-clock latency
or precision measurement — that would be non-deterministic (timing-flaky), would run on the recall
hot path, and could not be exercised in a hermetic offline test. The proxy is deterministic,
hermetic, and knob-driven; :func:`degradation_fraction` exposes it so a caller can report the
before/after effect.

This module is the **pure decision + summary-construction seam** of that pass. Like
:mod:`memrelay.ingest.summarizer` (the #33 spool summarizer this mirrors) it is deliberately
**offline and stdlib-only** — no asyncio, no engine, no graph, no network, no LLM/ML, no API key —
so the policy is trivially unit-testable and the extractive summary is **byte-identical across
re-runs**. All graph I/O lives in :meth:`memrelay.engine.graphiti.MemoryEngine.compact`, which
drives these helpers.

Determinism guarantees:

* :func:`summary_key` hashes the **sorted** victim uuids, so the summary identity depends only on
  the *set* of compacted episodes — a crash-retried pass produces the identical key.
* :func:`build_summary_content` is a whitespace-normalized, per-episode-capped, length-clamped
  extractive digest (mirrors :func:`memrelay.ingest.summarizer._digest`) — no model, no randomness.
* :func:`select_eligible` totally orders episodes by ``(valid_at, uuid)`` and
  :func:`is_degraded` uses integer ``ceil`` arithmetic — same inputs always yield the same decision.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Any

#: Upper bound (characters) on a compaction summary's digest. The digest is clamped to this, so a
#: summary's size is independent of how large the compacted episodes were (mirrors
#: :data:`memrelay.ingest.summarizer.MAX_SUMMARY_CHARS`).
MAX_SUMMARY_CHARS = 512

#: Per-episode excerpt cap folded into the digest, so one huge episode cannot crowd out every other
#: episode's contribution before the overall :data:`MAX_SUMMARY_CHARS` clamp.
_PER_EPISODE_CHARS = 120

#: Marker prefixing a summary episode's ``content`` so a compacted episode is recognizable in
#: recall.
_SUMMARY_PREFIX = "[memrelay compaction]"

#: The ``source_description`` marker stamped on a compaction summary episode. Distinct, greppable,
#: and deliberately **inert** to the ``repo=`` / ``agent=`` parsers in
#: :mod:`memrelay.engine.graphiti` (it carries no ``repo=``/``agent=`` token), so a summary is never
#: mistaken for a repo or agent memory. The engine excludes episodes carrying this marker from the
#: working set, which is what keeps a re-run from re-compacting prior summaries.
COMPACTION_MARKER = "memrelay-compaction"


@dataclass(frozen=True)
class EpisodeStat:
    """The compaction-relevant slice of one ``Episodic`` node.

    ``ref_count`` is the episode's **reference frequency**: the number of entity edges (facts) it
    produced, i.e. ``len(EpisodicNode.entity_edges)`` (populated by ``add_episode`` and persisted on
    the node). ``valid_at`` is the episode's event time, used only for relative oldest-first
    ordering.
    """

    uuid: str
    valid_at: Any
    ref_count: int
    content: str


def compaction_source_description(key: str) -> str:
    """Return the ``source_description`` to stamp on the summary episode for victim set ``key``."""
    return f"{COMPACTION_MARKER} key={key}"


def is_compaction_summary(source_description: str | None) -> bool:
    """Return ``True`` if ``source_description`` marks a compaction summary episode."""
    return (source_description or "").startswith(COMPACTION_MARKER)


def summary_key(victim_uuids: list[str]) -> str:
    """Return a deterministic, order-independent key identifying a summary of ``victim_uuids``.

    The *set* of compacted uuids determines the key (uuids are sorted before hashing), so a
    re-attempted compaction of the same episodes — e.g. after a crash between adding the summary and
    removing the originals — produces the identical key and never a duplicate summary.
    """
    hasher = hashlib.sha256()
    hasher.update(b"memrelay-graph-compaction\x00")
    for uuid in sorted(victim_uuids):
        hasher.update(uuid.encode("utf-8"))
        hasher.update(b"\x00")
    return hasher.hexdigest()


def build_digest(contents: list[str]) -> str:
    """Fold episode contents into one whitespace-normalized, length-bounded string."""
    parts: list[str] = []
    for content in contents:
        collapsed = " ".join(str(content).split())
        if collapsed:
            parts.append(collapsed[:_PER_EPISODE_CHARS])
    joined = " | ".join(parts)
    if len(joined) > MAX_SUMMARY_CHARS:
        joined = joined[: MAX_SUMMARY_CHARS - 3].rstrip() + "..."
    return joined


def build_summary_content(contents: list[str]) -> str:
    """Return the summary episode body: marker + episode count + bounded extractive digest."""
    return f"{_SUMMARY_PREFIX} {len(contents)} episode(s): {build_digest(contents)}"


def _age_key(stat: EpisodeStat) -> tuple[Any, str]:
    # Oldest first by valid_at; uuid breaks ties for a total, deterministic order.
    return (stat.valid_at, stat.uuid)


def select_eligible(
    stats: list[EpisodeStat],
    *,
    low_reference_max: int,
    protected_recent: int,
) -> list[EpisodeStat]:
    """Return the *oldest, lowest-reference-frequency* episodes eligible for compaction.

    Episodes are ordered oldest→newest by ``(valid_at, uuid)``; the newest ``protected_recent`` are
    dropped (the protected hot working set — a freshly-noted episode that has not yet accrued edges
    is never treated as stale low-value mass); of the remainder, those whose reference frequency is
    ``<= low_reference_max`` are returned, oldest-first. Deterministic total order.
    """
    ordered = sorted(stats, key=_age_key)
    protected = max(protected_recent, 0)
    keep = max(0, len(ordered) - protected)
    older = ordered[:keep]
    return [s for s in older if s.ref_count <= low_reference_max]


def is_degraded(
    eligible_count: int,
    episode_count: int,
    *,
    degradation_ratio: float,
    min_episodes: int,
) -> bool:
    """Return ``True`` when a namespace is degraded enough to warrant a compaction pass.

    The trigger is **activity-scaled, not a fixed count** (SPEC §5.5): a namespace is degraded only
    when it holds at least ``min_episodes`` episodes *and* its eligible (old + low-frequency)
    episodes number at least ``ceil(degradation_ratio * episode_count)``. Because the bar scales
    with namespace size, a busier namespace needs proportionally more stale mass to trigger — and,
    once triggered, has more episodes to compact (SPEC §5.5 "busier namespaces compact more
    aggressively").
    """
    if episode_count < min_episodes:
        return False
    bar = math.ceil(degradation_ratio * episode_count)
    return eligible_count >= bar


def degradation_fraction(eligible_count: int, episode_count: int) -> float:
    """Return the stale-low-value-mass fraction of a namespace's working set.

    This is the deterministic, graph-derived **proxy** for SPEC §5.5's recall latency/precision
    degradation — ``eligible_count / episode_count`` (0.0 for an empty working set) — that
    :func:`is_degraded` thresholds against ``degradation_ratio``. Exposed separately so a caller can
    report it **before vs. after** a pass and make the reclaim measurable (AC4), not merely
    asserted. It is NOT a wall-clock latency or precision measurement (see the module docstring).
    """
    if episode_count <= 0:
        return 0.0
    return round(eligible_count / episode_count, 6)
