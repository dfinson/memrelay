"""Unit tests for the ingester's backpressure / disk-budget compaction (E3-S4 #33).

Fully hermetic: a fake spool whose ``pending_bytes`` we control, an **injected mock
summarizer** (so no real LLM is ever touched), and — for the end-to-end path — a real
:class:`~memrelay.ingest.spool.Spool` on ``tmp_path`` with a fake engine. Covers: the
feature is dormant by default and when under budget; over budget the oldest unprocessed
episodes are summarized in place via the injected seam and the backlog is bounded; the
config knob drives it; and compaction never breaks exactly-once drain.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from memrelay.config import load_config
from memrelay.ingest.episode import EpisodeRecord, to_row
from memrelay.ingest.ingester import Ingester
from memrelay.ingest.spool import Spool


def _record(content: str, key: str, *, namespace: str = "proj-a") -> dict:
    return EpisodeRecord.new(content, namespace, repo="o/r", idempotency_key=key).to_dict()


async def _no_wait(delay: float, stop: asyncio.Event) -> None:
    return None


class MockSummarizer:
    """Injected summarizer: records the batches it sees, returns one tiny summary each."""

    def __init__(self) -> None:
        self.seen_batches: list[list[dict]] = []
        self._n = 0

    def __call__(self, records: list[dict]) -> list[dict]:
        self.seen_batches.append(records)
        self._n += 1
        return [_record("tiny summary", f"summary-{self._n}")]


class FakeSpool:
    """A minimal in-memory spool that behaves correctly under compaction + drain."""

    def __init__(self, records: list[tuple[int, dict]]) -> None:
        self._rows: list[tuple[int, dict]] = list(records)
        self._next = max((s for s, _ in records), default=0) + 1
        self.replace_calls: list[tuple[list[int], list[dict]]] = []

    def pending(self) -> int:
        return len(self._rows)

    def pending_bytes(self) -> int:
        return sum(len(to_row(r)) for _, r in self._rows)

    def read_batch(self, max_n: int = 100) -> list[tuple[int, dict]]:
        return [(s, dict(r)) for s, r in self._rows[:max_n]]

    def checkpoint(self, seq: int) -> None:
        self._rows = [(s, r) for s, r in self._rows if s > seq]

    def replace(self, delete_seqs: list[int], insert_records: list[dict]) -> None:
        self.replace_calls.append((list(delete_seqs), insert_records))
        drop = set(delete_seqs)
        self._rows = [(s, r) for s, r in self._rows if s not in drop]
        for rec in insert_records:
            self._rows.append((self._next, rec))
            self._next += 1


def _big_backlog(n: int, *, namespace: str = "proj-a") -> FakeSpool:
    return FakeSpool([(i + 1, _record("z" * 500, f"k{i}", namespace=namespace)) for i in range(n)])


# ─── _maybe_compact: the trigger logic in isolation ──────────────────────────


def test_disabled_by_default_never_compacts() -> None:
    spool = _big_backlog(20)
    summarizer = MockSummarizer()
    ingester = Ingester(object(), spool, summarizer=summarizer)  # max_bytes defaults to 0
    assert ingester._maybe_compact(asyncio.Event()) is False
    assert summarizer.seen_batches == []
    assert spool.replace_calls == []


def test_under_budget_does_not_compact() -> None:
    spool = _big_backlog(5)
    summarizer = MockSummarizer()
    # A budget far above the backlog → never over the high-water mark.
    ingester = Ingester(object(), spool, max_bytes=10_000_000, summarizer=summarizer)
    assert ingester._maybe_compact(asyncio.Event()) is False
    assert summarizer.seen_batches == []
    assert spool.replace_calls == []


def test_over_budget_summarizes_oldest_via_injected_seam() -> None:
    spool = _big_backlog(6)
    before = spool.pending_bytes()
    summarizer = MockSummarizer()
    # Budget below the backlog so we are over the 90% high-water mark; batch_size folds
    # the whole backlog in one pass.
    ingester = Ingester(
        object(), spool, max_bytes=1000, compaction_pct=0.9, batch_size=100, summarizer=summarizer
    )

    assert ingester._maybe_compact(asyncio.Event()) is True
    # The MOCK seam was used (no real LLM), and it saw the oldest unprocessed rows in order.
    assert len(summarizer.seen_batches) == 1
    assert [r["idempotency_key"] for r in summarizer.seen_batches[0]] == [f"k{i}" for i in range(6)]
    # The oldest rows were swapped out atomically for the summary, and the backlog shrank.
    assert spool.replace_calls[0][0] == [1, 2, 3, 4, 5, 6]
    assert spool.pending_bytes() < before
    metrics = ingester.metrics()
    assert metrics["compactions"] == 1
    assert metrics["episodes_compacted"] == 6


def test_compacts_oldest_first_across_passes() -> None:
    spool = _big_backlog(6)
    summarizer = MockSummarizer()
    # Small batch so each pass takes only the oldest 3; a tiny budget keeps it over the
    # mark until both original batches are folded.
    ingester = Ingester(
        object(), spool, max_bytes=200, compaction_pct=0.9, batch_size=3, summarizer=summarizer
    )
    assert ingester._maybe_compact(asyncio.Event()) is True
    # Pass 1 saw the oldest three; pass 2 saw the next three.
    assert [r["idempotency_key"] for r in summarizer.seen_batches[0]] == ["k0", "k1", "k2"]
    assert [r["idempotency_key"] for r in summarizer.seen_batches[1]] == ["k3", "k4", "k5"]


def test_compaction_stops_when_back_under_budget() -> None:
    spool = _big_backlog(30)
    summarizer = MockSummarizer()
    # Budget large enough that folding the oldest 10 drops the backlog under the mark, so
    # not every row needs compacting.
    ingester = Ingester(
        object(), spool, max_bytes=6000, compaction_pct=0.9, batch_size=10, summarizer=summarizer
    )
    assert ingester._maybe_compact(asyncio.Event()) is True
    assert spool.pending_bytes() < 0.9 * 6000
    assert len(spool._rows) < 30  # some rows survived uncompacted


# ─── end-to-end through run() with a REAL spool ──────────────────────────────


class RecordingEngine:
    def __init__(self) -> None:
        self.notes: list[tuple[str, str]] = []

    async def note(self, content, namespace, repo=None, source=None):
        self.notes.append((content, namespace))
        return f"uuid-{len(self.notes)}"


async def _run_until_drained(ingester: Ingester, spool: Spool, *, timeout: float = 2.0) -> None:
    stop = asyncio.Event()
    task = asyncio.create_task(ingester.run(stop))
    deadline = asyncio.get_running_loop().time() + timeout
    while spool.pending() > 0 and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.01)
    stop.set()
    await asyncio.wait_for(task, timeout=1.0)


def test_run_compacts_over_budget_backlog_then_ingests_summary(tmp_path: Path) -> None:
    spool = Spool(tmp_path / "spool" / "spool.db")
    for i in range(10):
        spool.append(_record("z" * 300, f"k{i}"))
    over_budget = spool.pending_bytes()

    engine = RecordingEngine()
    ingester = Ingester(
        engine,
        spool,
        idle_sleep=0.01,
        backoff_wait=_no_wait,
        max_bytes=max(1, over_budget // 2),  # seeded backlog is well over budget
        compaction_pct=0.9,
    )
    asyncio.run(_run_until_drained(ingester, spool))

    metrics = ingester.metrics()
    assert metrics["compactions"] >= 1, "an over-budget backlog must trigger compaction"
    assert metrics["episodes_compacted"] >= 2
    # The summarized-in-place backlog was then ingested: the engine saw a compaction note.
    assert any("[memrelay compaction]" in content for content, _ in engine.notes)
    assert spool.pending() == 0  # everything drained, nothing wedged
    spool.close()


def test_config_budget_drives_compaction(tmp_path: Path) -> None:
    """The additive config knob flows into the Ingester and triggers compaction — proving
    'budget configurable' end-to-end without touching the off-limits daemon wiring."""
    cfg = load_config(
        environ={},
        ingest={"spool_max_bytes": 1500, "spool_compaction_pct": 0.9},
    )
    assert cfg.ingest.spool_max_bytes == 1500

    spool = Spool(tmp_path / "spool" / "spool.db")
    for i in range(12):
        spool.append(_record("z" * 300, f"k{i}"))

    engine = RecordingEngine()
    ingester = Ingester(
        engine,
        spool,
        idle_sleep=0.01,
        backoff_wait=_no_wait,
        max_bytes=cfg.ingest.spool_max_bytes,
        compaction_pct=cfg.ingest.spool_compaction_pct,
    )
    asyncio.run(_run_until_drained(ingester, spool))
    assert ingester.metrics()["compactions"] >= 1
    spool.close()


def test_zero_config_ingester_never_compacts(tmp_path: Path) -> None:
    """With the default (disabled) budget the compaction path is fully dormant."""
    spool = Spool(tmp_path / "spool" / "spool.db")
    for i in range(10):
        spool.append(_record("z" * 300, f"k{i}"))

    engine = RecordingEngine()
    ingester = Ingester(engine, spool, idle_sleep=0.01, backoff_wait=_no_wait)  # defaults
    asyncio.run(_run_until_drained(ingester, spool))

    metrics = ingester.metrics()
    assert metrics["compactions"] == 0
    assert metrics["episodes_compacted"] == 0
    # Every original was ingested verbatim — behaviour identical to pre-#33.
    assert metrics["episodes_ingested"] == 10
    assert not any("[memrelay compaction]" in content for content, _ in engine.notes)
    spool.close()
