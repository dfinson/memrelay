"""The spool → Graphiti ingester: drain the durable spool into memory (E4-S5 #37).

:class:`Ingester` is the long-lived reader half of the ingest path. It repeatedly
pulls a batch of episodes off the :class:`~memrelay.ingest.spool.Spool`, notes each
one into the injected memory engine, and checkpoints it — turning durable-but-inert
spool rows into recallable graph memory.

Two robustness properties matter for a background loop the daemon never babysits:

* **Poison tolerance.** A single record that makes ``engine.note`` raise is logged
  and *skipped* — its ``seq`` is still checkpointed so it can never wedge the cursor
  or be retried forever, and the loop keeps going. One bad episode must not stall
  ingest for every episode behind it.
* **Idle backoff.** When the spool is empty the loop waits (interruptibly) instead
  of spinning, and wakes immediately when ``stop`` is set for a prompt shutdown.

The engine and spool are injected and only duck-typed (``await engine.note(...)``,
``spool.read_batch/checkpoint/pending``), so the loop unit-tests against fakes with
no Kuzu and no network.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class _Engine(Protocol):
    async def note(self, content: str, namespace: str, repo: str | None = None) -> str: ...


class _Spool(Protocol):
    def read_batch(self, max_n: int = 100) -> list[tuple[int, dict[str, Any]]]: ...

    def checkpoint(self, seq: int) -> None: ...

    def pending(self) -> int: ...


class Ingester:
    """Drain a :class:`~memrelay.ingest.spool.Spool` into a memory engine.

    Args:
        engine: object exposing ``async note(content, namespace, repo=None)``
            (the merged :class:`~memrelay.engine.graphiti.MemoryEngine`).
        spool: object exposing ``read_batch`` / ``checkpoint`` / ``pending``.
        idle_sleep: seconds to wait when the spool is empty before re-polling.
        batch_size: max episodes fetched per :meth:`Spool.read_batch`.
    """

    def __init__(
        self,
        engine: _Engine,
        spool: _Spool,
        *,
        idle_sleep: float = 0.5,
        batch_size: int = 100,
    ) -> None:
        self._engine = engine
        self._spool = spool
        self._idle_sleep = idle_sleep
        self._batch_size = batch_size
        self._episodes_ingested = 0

    async def run(self, stop: asyncio.Event) -> None:
        """Loop until ``stop`` is set: read a batch, note + checkpoint each record.

        Never raises for a bad record (see the module docstring); the only way out
        is ``stop`` being set. Safe to launch as a background task and cancel via
        ``stop.set()``.
        """
        while not stop.is_set():
            batch = self._spool.read_batch(self._batch_size)
            if not batch:
                await self._idle(stop)
                continue
            for seq, record in batch:
                await self._ingest_one(seq, record)
                if stop.is_set():
                    break

    async def _ingest_one(self, seq: int, record: dict[str, Any]) -> None:
        """Note one record, then checkpoint it — even if noting failed (skip poison)."""
        try:
            await self._engine.note(record["content"], record["namespace"], record.get("repo"))
            self._episodes_ingested += 1
        except Exception:
            logger.exception(
                "ingester: skipping poison record seq=%s key=%s",
                seq,
                record.get("idempotency_key"),
            )
        finally:
            # Always advance the cursor: a poisoned row must not be retried forever.
            self._spool.checkpoint(seq)

    async def _idle(self, stop: asyncio.Event) -> None:
        """Wait ``idle_sleep`` seconds, returning early if ``stop`` is set."""
        try:
            await asyncio.wait_for(stop.wait(), timeout=self._idle_sleep)
        except TimeoutError:
            pass

    def stats(self) -> dict[str, int]:
        """Return ingest counters for the daemon health report.

        ``episodes_ingested`` counts successful notes this process; ``spool_pending``
        is the live backlog. The daemon (session A) folds these into ``health()``.
        """
        return {
            "episodes_ingested": self._episodes_ingested,
            "spool_pending": self._spool.pending(),
        }
