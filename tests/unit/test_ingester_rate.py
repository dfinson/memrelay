"""Unit tests for the ingester's rate-management behaviours (E3-S5 #32).

Covers exponential backoff + retry, the no-data-loss guarantee across a mid-backoff
crash, poison handling (malformed rows and retry-exhausted rows), batch/idle vs. size
flush triggers, and the metrics surface. All hermetic: a fake engine whose ``note`` we
script, a real SQLite :class:`~memrelay.ingest.spool.Spool` on ``tmp_path``, and an
**injected no-op backoff wait** so retries never actually sleep.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from pathlib import Path

from memrelay.ingest.episode import EpisodeRecord
from memrelay.ingest.ingester import Ingester
from memrelay.ingest.spool import Spool


class FlakyEngine:
    """Fake engine: fail N times per content then succeed; ``always_fail`` never succeeds."""

    def __init__(
        self,
        *,
        fail_times: dict[str, int] | None = None,
        always_fail: set[str] | None = None,
    ) -> None:
        self.notes: list[tuple[str, str, str | None, str | None]] = []
        self._fail_times = dict(fail_times or {})
        self._always_fail = set(always_fail or ())
        self.attempts: dict[str, int] = defaultdict(int)

    async def note(
        self,
        content: str,
        namespace: str,
        repo: str | None = None,
        source: str | None = None,
    ) -> str:
        self.attempts[content] += 1
        if content in self._always_fail:
            raise RuntimeError(f"always boom: {content}")
        remaining = self._fail_times.get(content, 0)
        if remaining > 0:
            self._fail_times[content] = remaining - 1
            raise RuntimeError(f"transient boom: {content}")
        self.notes.append((content, namespace, repo, source))
        return f"uuid-{len(self.notes)}"


class RecordingWait:
    """An injected backoff wait: records each delay, never sleeps, optionally 'crashes'.

    When ``stop_after`` is set, it fires the stop event once that many waits have
    happened — simulating a shutdown/crash mid-backoff so the record is left
    un-checkpointed.
    """

    def __init__(self, *, stop_after: int | None = None) -> None:
        self.delays: list[float] = []
        self._stop_after = stop_after

    async def __call__(self, delay: float, stop: asyncio.Event) -> None:
        self.delays.append(delay)
        if self._stop_after is not None and len(self.delays) >= self._stop_after:
            stop.set()


async def _no_wait(delay: float, stop: asyncio.Event) -> None:
    """A backoff wait that never sleeps (keeps retry-heavy tests instant)."""
    return None


def _ceiling_rng() -> float:
    """Full jitter pinned to the ceiling so recorded delays are exactly base*2**attempt."""
    return 1.0


def _record(content: str, key: str) -> dict:
    return EpisodeRecord.new(content, "proj-a", repo="memrelay", idempotency_key=key).to_dict()


def _seed(tmp_path: Path, items: list[tuple[str, str]]) -> Spool:
    spool = Spool(tmp_path / "spool" / "spool.db")
    for content, key in items:
        spool.append(_record(content, key))
    return spool


async def _run_until_drained(ingester: Ingester, spool: Spool, *, timeout: float = 2.0) -> None:
    """Run the ingester until the spool is fully consumed, then stop it."""
    stop = asyncio.Event()
    task = asyncio.create_task(ingester.run(stop))
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while spool.pending() > 0 and loop.time() < deadline:
        await asyncio.sleep(0.005)
    stop.set()
    await asyncio.wait_for(task, timeout=timeout)


# ---------------------------------------------------------------- backoff + retry


def test_transient_failure_is_retried_then_succeeds_without_loss_or_dup(tmp_path: Path) -> None:
    spool = _seed(tmp_path, [("a", "k0")])
    engine = FlakyEngine(fail_times={"a": 3})
    wait = RecordingWait()
    ingester = Ingester(
        engine,
        spool,
        idle_sleep=0.01,
        backoff_base=0.5,
        backoff_cap=30.0,
        rng=_ceiling_rng,
        backoff_wait=wait,
    )
    asyncio.run(_run_until_drained(ingester, spool))

    assert engine.attempts["a"] == 4, "3 transient failures then one success"
    assert [n[0] for n in engine.notes] == ["a"], "noted exactly once — no duplicate"
    assert spool.pending() == 0, "checkpointed only after the note finally succeeded"
    metrics = ingester.metrics()
    assert metrics["retries"] == 3
    assert metrics["notes_failed"] == 3
    assert metrics["notes_attempted"] == 4
    assert metrics["episodes_ingested"] == 1
    assert metrics["poison_skipped"] == 0
    # Full jitter pinned to the ceiling → exponential schedule is exact.
    assert wait.delays == [0.5, 1.0, 2.0]
    spool.close()


def test_retry_forever_mode_rides_out_a_long_outage(tmp_path: Path) -> None:
    # max_retries=None must never drop on unavailability, even past the default bound of 5.
    spool = _seed(tmp_path, [("a", "k0")])
    engine = FlakyEngine(fail_times={"a": 20})
    ingester = Ingester(
        engine, spool, idle_sleep=0.01, max_retries=None, rng=_ceiling_rng, backoff_wait=_no_wait
    )
    asyncio.run(_run_until_drained(ingester, spool, timeout=3.0))

    assert [n[0] for n in engine.notes] == ["a"], "recovered after 20 failures, noted once"
    assert spool.pending() == 0
    assert ingester.metrics()["retries"] == 20
    assert ingester.metrics()["poison_skipped"] == 0
    spool.close()


def test_default_backoff_wait_returns_immediately_when_stopped(tmp_path: Path) -> None:
    # The real (uninjected) interruptible wait must short-circuit on a set stop event,
    # so a long backoff can never delay shutdown.
    spool = Spool(tmp_path / "spool" / "spool.db")
    ingester = Ingester(FlakyEngine(), spool)

    async def scenario() -> None:
        stop = asyncio.Event()
        stop.set()
        await asyncio.wait_for(ingester._backoff_wait(100.0, stop), timeout=1.0)

    asyncio.run(scenario())
    spool.close()


# ---------------------------------------------------------------- no data loss on crash


def test_crash_mid_backoff_leaves_record_for_redrain(tmp_path: Path) -> None:
    db = tmp_path / "spool" / "spool.db"
    spool = Spool(db)
    spool.append(_record("a", "k0"))

    # First run: engine is down; we 'crash' (fire stop) during the first backoff.
    engine1 = FlakyEngine(always_fail={"a"})
    wait = RecordingWait(stop_after=1)
    ing1 = Ingester(engine1, spool, idle_sleep=0.01, rng=_ceiling_rng, backoff_wait=wait)
    asyncio.run(asyncio.wait_for(ing1.run(asyncio.Event()), timeout=2.0))

    assert engine1.notes == [], "nothing succeeded"
    assert spool.pending() == 1, "row left un-checkpointed — not lost"
    assert ing1.metrics()["episodes_ingested"] == 0
    assert ing1.metrics()["poison_skipped"] == 0, "interrupted, not dropped as poison"

    # Restart with a healthy engine: the SAME row drains to success, exactly once.
    engine2 = FlakyEngine()
    ing2 = Ingester(engine2, spool, idle_sleep=0.01, backoff_wait=_no_wait)
    asyncio.run(_run_until_drained(ing2, spool))

    assert [n[0] for n in engine2.notes] == ["a"], "re-drained after the crash, noted once"
    assert spool.pending() == 0
    spool.close()


# ---------------------------------------------------------------- poison handling


def test_engine_failure_past_max_retries_is_dropped_as_poison(tmp_path: Path) -> None:
    spool = _seed(tmp_path, [("a", "k0"), ("bad", "k1"), ("c", "k2")])
    engine = FlakyEngine(always_fail={"bad"})
    ingester = Ingester(
        engine, spool, idle_sleep=0.01, max_retries=5, rng=_ceiling_rng, backoff_wait=_no_wait
    )
    asyncio.run(_run_until_drained(ingester, spool))

    assert [n[0] for n in engine.notes] == ["a", "c"], "bad dropped; neighbours ingested"
    assert spool.pending() == 0, "poison seq checkpointed so it can't wedge the queue"
    assert engine.attempts["bad"] == 6, "1 initial attempt + 5 retries"
    metrics = ingester.metrics()
    assert metrics["poison_skipped"] == 1
    assert metrics["episodes_ingested"] == 2
    assert metrics["retries"] == 5
    spool.close()


def test_malformed_record_is_dropped_without_retry(tmp_path: Path) -> None:
    spool = Spool(tmp_path / "spool" / "spool.db")
    # A row missing 'content' — the extraction seam must drop it without ever retrying.
    spool.append({"idempotency_key": "k0", "namespace": "proj-a"})
    spool.append(_record("ok", "k1"))
    engine = FlakyEngine()
    wait = RecordingWait()
    ingester = Ingester(engine, spool, idle_sleep=0.01, backoff_wait=wait)
    asyncio.run(_run_until_drained(ingester, spool))

    assert [n[0] for n in engine.notes] == ["ok"], "good neighbour still ingested"
    assert spool.pending() == 0
    metrics = ingester.metrics()
    assert metrics["poison_skipped"] == 1
    assert metrics["notes_attempted"] == 1, "malformed row never reached engine.note"
    assert wait.delays == [], "malformed row never triggered a backoff"
    spool.close()


# ---------------------------------------------------------------- batch / idle / size triggers


def test_arrivals_present_together_drain_in_a_single_flush(tmp_path: Path) -> None:
    spool = _seed(tmp_path, [("a", "k0"), ("b", "k1"), ("c", "k2")])
    engine = FlakyEngine()
    ingester = Ingester(engine, spool, idle_sleep=0.01, batch_size=100, backoff_wait=_no_wait)
    asyncio.run(_run_until_drained(ingester, spool))

    assert [n[0] for n in engine.notes] == ["a", "b", "c"], "seq order preserved"
    assert spool.pending() == 0
    assert ingester.metrics()["batches_drained"] == 1, "coalesced into one idle flush"
    spool.close()


def test_size_trigger_flushes_without_waiting_for_idle(tmp_path: Path) -> None:
    # idle_sleep is huge: if we relied on the idle timer we'd time out. Reaching
    # batch_size must force an immediate flush during an 'active' session.
    spool = _seed(tmp_path, [("a", "k0"), ("b", "k1"), ("c", "k2")])
    engine = FlakyEngine()
    ingester = Ingester(engine, spool, idle_sleep=5.0, batch_size=3, backoff_wait=_no_wait)
    asyncio.run(_run_until_drained(ingester, spool, timeout=2.0))

    assert [n[0] for n in engine.notes] == ["a", "b", "c"]
    assert spool.pending() == 0
    spool.close()


def test_idle_trigger_flushes_a_partial_batch(tmp_path: Path) -> None:
    spool = _seed(tmp_path, [("a", "k0")])  # single record, well below batch_size
    engine = FlakyEngine()
    ingester = Ingester(
        engine, spool, idle_sleep=0.01, batch_size=100, idle_flush_cycles=1, backoff_wait=_no_wait
    )
    asyncio.run(_run_until_drained(ingester, spool))

    assert [n[0] for n in engine.notes] == ["a"]
    assert spool.pending() == 0
    assert ingester.metrics()["batches_drained"] >= 1
    spool.close()


def test_many_records_drain_in_seq_order_without_dup(tmp_path: Path) -> None:
    contents = [f"m{i}" for i in range(50)]
    spool = _seed(tmp_path, [(c, f"k{i}") for i, c in enumerate(contents)])
    engine = FlakyEngine(fail_times={"m10": 2, "m20": 1})  # a couple of transient blips
    ingester = Ingester(
        engine, spool, idle_sleep=0.01, batch_size=100, rng=_ceiling_rng, backoff_wait=_no_wait
    )
    asyncio.run(_run_until_drained(ingester, spool, timeout=3.0))

    assert [n[0] for n in engine.notes] == contents, "exact seq order, no dup, no drop"
    assert spool.pending() == 0
    assert ingester.metrics()["episodes_ingested"] == 50
    assert ingester.metrics()["retries"] == 3
    spool.close()


# ---------------------------------------------------------------- metrics surface


def test_stats_shape_is_frozen_and_metrics_are_separate(tmp_path: Path) -> None:
    spool = _seed(tmp_path, [("a", "k0")])
    engine = FlakyEngine()
    ingester = Ingester(engine, spool, idle_sleep=0.01, backoff_wait=_no_wait)
    asyncio.run(_run_until_drained(ingester, spool))

    # stats() keeps its exact two-key shape (the daemon health contract).
    assert ingester.stats() == {"episodes_ingested": 1, "spool_pending": 0}
    # metrics() is the richer, separate surface.
    metrics = ingester.metrics()
    assert set(metrics) == {
        "episodes_ingested",
        "notes_attempted",
        "notes_failed",
        "retries",
        "poison_skipped",
        "batches_drained",
        "backoff_sleep_seconds",
        "compactions",
        "episodes_compacted",
    }
    assert metrics["episodes_ingested"] == 1
    spool.close()
