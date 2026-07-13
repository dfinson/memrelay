"""Unit tests for the offline compaction summarizer (E3-S4 #33).

Pure and hermetic: :func:`~memrelay.ingest.summarizer.default_summarizer` is deterministic
and offline, so these assert its grouping, bounding, provenance, and key-stability without
any engine, spool, or LLM.
"""

from __future__ import annotations

from memrelay.ingest.episode import EPISODE_FIELDS, EpisodeRecord
from memrelay.ingest.summarizer import (
    MAX_SUMMARY_CHARS,
    _summary_key,
    default_summarizer,
)


def _rec(content: str, key: str, *, namespace: str = "proj-a", repo: str | None = "o/r") -> dict:
    return EpisodeRecord.new(content, namespace, repo=repo, idempotency_key=key).to_dict()


def test_empty_input_yields_no_summaries() -> None:
    assert default_summarizer([]) == []


def test_single_namespace_folds_to_one_summary() -> None:
    records = [_rec(f"fact number {i}", f"k{i}") for i in range(5)]
    summaries = default_summarizer(records)

    assert len(summaries) == 1
    summary = summaries[0]
    assert summary["namespace"] == "proj-a"
    assert summary["repo"] == "o/r"
    assert summary["source"] == "compaction"
    assert summary["phase"] is None
    # It is a well-formed episode dict (every wire field present) so the ingester can note it.
    assert set(summary) == set(EPISODE_FIELDS)
    # The digest mentions how many episodes were folded in.
    assert "5 episode(s)" in summary["content"]


def test_groups_by_namespace_preserving_scope() -> None:
    records = [
        _rec("a", "k0", namespace="proj-a"),
        _rec("b", "k1", namespace="proj-b"),
        _rec("c", "k2", namespace="proj-a"),
    ]
    summaries = default_summarizer(records)

    by_ns = {s["namespace"]: s for s in summaries}
    assert set(by_ns) == {"proj-a", "proj-b"}
    assert "2 episode(s)" in by_ns["proj-a"]["content"]
    assert "1 episode(s)" in by_ns["proj-b"]["content"]
    # Output is ordered by namespace for stable, deterministic results.
    assert [s["namespace"] for s in summaries] == ["proj-a", "proj-b"]


def test_repo_kept_only_when_uniform() -> None:
    uniform = default_summarizer([_rec("a", "k0", repo="o/r"), _rec("b", "k1", repo="o/r")])
    assert uniform[0]["repo"] == "o/r"

    mixed = default_summarizer([_rec("a", "k0", repo="o/r"), _rec("b", "k1", repo="o/other")])
    assert mixed[0]["repo"] is None, "a mixed-repo group cannot claim a single repo"


def test_content_is_length_bounded() -> None:
    # Many large episodes must still collapse to a bounded summary — the disk win.
    records = [_rec("x" * 5000, f"k{i}") for i in range(50)]
    summary = default_summarizer(records)[0]
    assert len(summary["content"]) <= MAX_SUMMARY_CHARS + len(
        "[memrelay compaction] 50 episode(s): "
    )
    # The digest portion itself is clamped to MAX_SUMMARY_CHARS.
    digest = summary["content"].split(": ", 1)[1]
    assert len(digest) <= MAX_SUMMARY_CHARS
    # rt-episode F5: MAX_SUMMARY_CHARS bounds only the digest. The emitted content also carries a
    # fixed "[memrelay compaction] N episode(s): " frame, so with a saturated digest the content
    # deliberately exceeds MAX_SUMMARY_CHARS — the module docstring must not claim otherwise.
    assert len(summary["content"]) > MAX_SUMMARY_CHARS


def test_summary_key_is_deterministic_and_order_independent() -> None:
    records = [_rec("a", "k0"), _rec("b", "k1"), _rec("c", "k2")]
    first = default_summarizer(records)[0]["idempotency_key"]
    # Same members in a different order → identical key (the set of keys determines it).
    second = default_summarizer(list(reversed(records)))[0]["idempotency_key"]
    assert first == second
    assert first == _summary_key(["k0", "k1", "k2"])
    # A different member set → a different key.
    assert first != default_summarizer([_rec("a", "k0"), _rec("b", "k1")])[0]["idempotency_key"]


def test_ts_is_the_latest_member() -> None:
    records = [
        EpisodeRecord.new("a", "proj-a", idempotency_key="k0", ts="2024-01-01T00:00:00").to_dict(),
        EpisodeRecord.new("b", "proj-a", idempotency_key="k1", ts="2024-06-01T00:00:00").to_dict(),
    ]
    assert default_summarizer(records)[0]["ts"] == "2024-06-01T00:00:00"


def test_summary_is_smaller_than_its_inputs() -> None:
    # The whole point: the serialized summary must be far smaller than the originals.
    from memrelay.ingest.episode import to_row

    records = [_rec("y" * 2000, f"k{i}") for i in range(20)]
    summaries = default_summarizer(records)
    original_bytes = sum(len(to_row(r)) for r in records)
    summary_bytes = sum(len(to_row(s)) for s in summaries)
    assert summary_bytes < original_bytes
