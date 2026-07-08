"""Unit tests for the spool -> engine ingester loop (E4-S5 #37).

Uses a fake engine (records/raises on note) so the loop is exercised with no Kuzu
and no network; the real engine drain is covered by the integration test.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from memrelay.ingest.episode import EpisodeRecord
from memrelay.ingest.ingester import Ingester
from memrelay.ingest.spool import Spool


class FakeEngine:
    """Records successful notes; raises for any content in ``poison``."""

    def __init__(self, poison: set[str] | None = None) -> None:
        self.notes: list[tuple[str, str, str | None]] = []
        self._poison = poison or set()

    async def note(self, content: str, namespace: str, repo: str | None = None) -> str:
        if content in self._poison:
            raise RuntimeError(f"boom: {content}")
        self.notes.append((content, namespace, repo))
        return f"uuid-{len(self.notes)}"


def _record(content: str, key: str) -> dict:
    return EpisodeRecord.new(content, "proj-a", repo="memrelay", idempotency_key=key).to_dict()


def _seed(tmp_path: Path, contents: list[str]) -> Spool:
    spool = Spool(tmp_path / "spool" / "spool.db")
    for i, content in enumerate(contents):
        spool.append(_record(content, f"k{i}"))
    return spool


async def _drain(engine: FakeEngine, spool: Spool, *, timeout: float = 2.0) -> Ingester:
    """Run the ingester until the spool is fully consumed, then stop it."""
    ingester = Ingester(engine, spool, idle_sleep=0.01)
    stop = asyncio.Event()
    task = asyncio.create_task(ingester.run(stop))
    deadline = asyncio.get_running_loop().time() + timeout
    while spool.pending() > 0 and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.005)
    stop.set()
    await asyncio.wait_for(task, timeout=timeout)
    return ingester


def test_drains_all_records_in_order(tmp_path: Path) -> None:
    spool = _seed(tmp_path, ["a", "b", "c"])
    engine = FakeEngine()
    ingester = asyncio.run(_drain(engine, spool))

    assert [content for content, _, _ in engine.notes] == ["a", "b", "c"]
    assert engine.notes[0] == ("a", "proj-a", "memrelay"), "content/namespace/repo forwarded"
    assert spool.pending() == 0
    assert ingester.stats() == {"episodes_ingested": 3, "spool_pending": 0}
    spool.close()


def test_poison_record_is_skipped_and_loop_continues(tmp_path: Path) -> None:
    spool = _seed(tmp_path, ["a", "poison", "c"])
    engine = FakeEngine(poison={"poison"})
    ingester = asyncio.run(_drain(engine, spool))

    # The poison note raised, but the record ahead and behind it were still ingested.
    assert [content for content, _, _ in engine.notes] == ["a", "c"]
    # The poison seq was checkpointed too, so it neither wedges nor redelivers.
    assert spool.pending() == 0
    assert ingester.stats()["episodes_ingested"] == 2
    spool.close()


def test_stats_reports_live_backlog(tmp_path: Path) -> None:
    spool = _seed(tmp_path, ["a", "b"])
    engine = FakeEngine()
    ingester = Ingester(engine, spool)
    assert ingester.stats() == {"episodes_ingested": 0, "spool_pending": 2}
    spool.close()


def test_empty_spool_idles_until_stopped(tmp_path: Path) -> None:
    spool = Spool(tmp_path / "spool" / "spool.db")
    engine = FakeEngine()

    async def scenario() -> Ingester:
        ingester = Ingester(engine, spool, idle_sleep=0.01)
        stop = asyncio.Event()
        task = asyncio.create_task(ingester.run(stop))
        await asyncio.sleep(0.05)  # several idle cycles
        assert not task.done(), "loop must keep waiting on an empty spool"
        stop.set()
        await asyncio.wait_for(task, timeout=1.0)
        return ingester

    ingester = asyncio.run(scenario())
    assert engine.notes == []
    assert ingester.stats()["episodes_ingested"] == 0
    spool.close()


def test_stop_already_set_returns_immediately(tmp_path: Path) -> None:
    spool = _seed(tmp_path, ["a"])
    engine = FakeEngine()

    async def scenario() -> None:
        ingester = Ingester(engine, spool, idle_sleep=0.01)
        stop = asyncio.Event()
        stop.set()
        await asyncio.wait_for(ingester.run(stop), timeout=1.0)

    asyncio.run(scenario())
    assert engine.notes == [], "a pre-set stop must short-circuit before any note"
    spool.close()
