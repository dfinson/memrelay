"""Unit tests for the ingester's below-cursor history reclamation wiring (E3 #112).

Fully hermetic: a spy spool for the trigger logic in isolation, and — for the end-to-end path
— a real :class:`~memrelay.ingest.spool.Spool` on ``tmp_path`` drained by a fake engine. Covers:
the feature is dormant by default (zero-config retains all ingested history, byte-identical to
pre-#112); a configured budget prunes the oldest below-cursor rows through the loop and bounds
retained history in steady state; the metrics advance; the un-ingested backlog / exactly-once
drain is never disturbed; and the additive config knob flows into the Ingester.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from memrelay.config import load_config
from memrelay.ingest.episode import EpisodeRecord
from memrelay.ingest.ingester import Ingester
from memrelay.ingest.spool import Spool


def _record(content: str, key: str, *, namespace: str = "proj-a") -> dict:
    return EpisodeRecord.new(content, namespace, repo="o/r", idempotency_key=key).to_dict()


async def _no_wait(delay: float, stop: asyncio.Event) -> None:
    return None


class ReclaimSpy:
    """A minimal spool exposing only ``reclaim``: records budgets, returns a fixed count."""

    def __init__(self, returns: int) -> None:
        self._returns = returns
        self.calls: list[int] = []

    def reclaim(self, max_retained_bytes: int) -> int:
        self.calls.append(max_retained_bytes)
        return self._returns


# ─── _maybe_reclaim: the trigger logic in isolation ──────────────────────────


def test_disabled_by_default_never_reclaims() -> None:
    spool = ReclaimSpy(returns=3)
    ingester = Ingester(object(), spool)  # retention_bytes defaults to 0
    assert ingester._maybe_reclaim() is False
    assert spool.calls == [], "a disabled retention budget never calls into the spool"
    assert ingester.metrics()["reclamations"] == 0


def test_over_budget_reclaims_and_counts() -> None:
    spool = ReclaimSpy(returns=7)
    ingester = Ingester(object(), spool, retention_bytes=1000)
    assert ingester._maybe_reclaim() is True
    assert spool.calls == [1000], "the configured budget is passed straight to Spool.reclaim"
    metrics = ingester.metrics()
    assert metrics["reclamations"] == 1
    assert metrics["episodes_reclaimed"] == 7


def test_under_budget_reclaim_is_not_counted() -> None:
    spool = ReclaimSpy(returns=0)  # spool reports nothing pruned (already under budget)
    ingester = Ingester(object(), spool, retention_bytes=1000)
    assert ingester._maybe_reclaim() is False
    assert spool.calls == [1000]
    metrics = ingester.metrics()
    assert metrics["reclamations"] == 0
    assert metrics["episodes_reclaimed"] == 0


# ─── end-to-end through run() with a REAL spool ──────────────────────────────


class RecordingEngine:
    def __init__(self) -> None:
        self.notes: list[tuple[str, str]] = []

    async def note(self, content, namespace, repo=None, source=None):
        self.notes.append((content, namespace))
        return f"uuid-{len(self.notes)}"


async def _run_until(ingester: Ingester, predicate, *, timeout: float = 3.0) -> None:
    stop = asyncio.Event()
    task = asyncio.create_task(ingester.run(stop))
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate() and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.01)
    stop.set()
    await asyncio.wait_for(task, timeout=1.0)


def test_run_bounds_below_cursor_history(tmp_path: Path) -> None:
    """The loop autonomously drains every row *and* keeps retained history under budget."""
    spool = Spool(tmp_path / "spool" / "spool.db")
    for i in range(20):
        spool.append(_record("z" * 500, f"k{i}"))
    full_backlog = spool.pending_bytes()
    budget = full_backlog // 5  # room for only a fraction of the ingested history

    engine = RecordingEngine()
    ingester = Ingester(
        engine,
        spool,
        idle_sleep=0.01,
        backoff_wait=_no_wait,
        retention_bytes=budget,
    )
    asyncio.run(
        _run_until(ingester, lambda: spool.pending() == 0 and spool.retained_bytes() <= budget)
    )

    # Every episode was ingested exactly once (nothing lost or wedged) ...
    assert len(engine.notes) == 20
    assert spool.pending() == 0
    # ... yet the below-cursor history is bounded, not the full 20-row footprint.
    assert spool.retained_bytes() <= budget
    assert spool.retained_bytes() < full_backlog
    metrics = ingester.metrics()
    assert metrics["reclamations"] >= 1, "an over-budget history must trigger reclamation"
    assert metrics["episodes_reclaimed"] >= 1
    spool.close()


def test_zero_config_run_retains_all_history(tmp_path: Path) -> None:
    """With the default (disabled) budget, reclamation is dormant and history is kept in full."""
    spool = Spool(tmp_path / "spool" / "spool.db")
    for i in range(10):
        spool.append(_record("z" * 500, f"k{i}"))

    engine = RecordingEngine()
    ingester = Ingester(engine, spool, idle_sleep=0.01, backoff_wait=_no_wait)  # defaults
    asyncio.run(_run_until(ingester, lambda: spool.pending() == 0))

    assert len(engine.notes) == 10
    assert spool.pending() == 0
    # No pruning: all ten ingested rows remain as below-cursor history (pre-#112 behaviour).
    whole = int(spool._conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0])
    assert whole == 10, "zero-config keeps every ingested row"
    assert spool.retained_bytes() > 0
    metrics = ingester.metrics()
    assert metrics["reclamations"] == 0
    assert metrics["episodes_reclaimed"] == 0
    spool.close()


def test_config_retention_drives_reclamation(tmp_path: Path) -> None:
    """The additive config knob flows into the Ingester and bounds retained history end-to-end,
    proving 'budget configurable' without touching the off-limits daemon wiring."""
    spool = Spool(tmp_path / "spool" / "spool.db")
    for i in range(12):
        spool.append(_record("z" * 500, f"k{i}"))
    budget = spool.pending_bytes() // 4

    cfg = load_config(environ={}, ingest={"spool_retention_bytes": budget})
    assert cfg.ingest.spool_retention_bytes == budget

    engine = RecordingEngine()
    ingester = Ingester(
        engine,
        spool,
        idle_sleep=0.01,
        backoff_wait=_no_wait,
        retention_bytes=cfg.ingest.spool_retention_bytes,
    )
    asyncio.run(
        _run_until(ingester, lambda: spool.pending() == 0 and spool.retained_bytes() <= budget)
    )
    assert spool.retained_bytes() <= budget
    assert ingester.metrics()["reclamations"] >= 1
    spool.close()


def test_zero_config_retention_defaults(tmp_path: Path) -> None:
    """A zero-config Config leaves retention disabled (0) — the byte-identical default."""
    cfg = load_config(environ={})
    assert cfg.ingest.spool_retention_bytes == 0
