"""Regression test for the ingester run-loop's spool-error guard (rt-ingest F2).

``Ingester.run`` must survive a transient *spool* failure the same way it already survives a
failing ``engine.note``: log it and keep draining, never let the exception kill the
background task and stop ingest permanently and silently.

Historically only ``engine.note`` was guarded; every spool op (``pending`` / ``read_batch`` /
``checkpoint`` / ``pending_bytes`` / ``replace`` / ``reclaim``) was unguarded, so a single
raise — a disk/IO/corruption fault, or a ``sqlite3.OperationalError: database is locked`` when
the poller's and ingester's separate connections contend past SQLite's busy timeout —
propagated out of ``run`` and killed the fire-and-forget task (launched with a bare
``asyncio.create_task`` and no done-callback, so the exception was swallowed unretrieved).

This test injects a spool whose ``read_batch`` raises exactly once mid-``run``, then asserts
the loop logs the error, does **not** die, and drains the record on a subsequent pass. All
hermetic: a real SQLite :class:`~memrelay.ingest.spool.Spool` on ``tmp_path``, a trivial
recording engine, and an injected no-op backoff wait so nothing actually sleeps.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from memrelay.ingest.episode import EpisodeRecord
from memrelay.ingest.ingester import Ingester
from memrelay.ingest.spool import Spool


class RecordingEngine:
    """Fake engine that records every successful note and never fails."""

    def __init__(self) -> None:
        self.notes: list[tuple[str, str]] = []

    async def note(
        self,
        content: str,
        namespace: str,
        repo: str | None = None,
        source: str | None = None,
        **_: object,
    ) -> str:
        self.notes.append((content, namespace))
        return f"uuid-{len(self.notes)}"


class RaiseOnceSpool:
    """Wrap a real :class:`Spool`; the named op raises ``exc`` once, then delegates verbatim.

    Everything else passes straight through, so the run-loop meets a genuine, transient spool
    fault at exactly one call site — the shape of a disk/IO hiccup or a ``database is locked``
    timeout — with completely normal behaviour before and after.
    """

    def __init__(self, inner: Spool, *, fail_op: str, exc: BaseException) -> None:
        self._inner = inner
        self._fail_op = fail_op
        self._exc = exc
        self.raised = 0

    def _guard(self, op: str) -> None:
        if op == self._fail_op and self.raised == 0:
            self.raised += 1
            raise self._exc

    def read_batch(self, max_n: int = 100) -> list[tuple[int, dict[str, Any]]]:
        self._guard("read_batch")
        return self._inner.read_batch(max_n)

    def checkpoint(self, seq: int) -> None:
        self._guard("checkpoint")
        self._inner.checkpoint(seq)

    def pending(self) -> int:
        self._guard("pending")
        return self._inner.pending()

    def pending_bytes(self) -> int:
        self._guard("pending_bytes")
        return self._inner.pending_bytes()

    def retained_bytes(self) -> int:
        return self._inner.retained_bytes()

    def replace(self, delete_seqs: list[int], insert_records: list[dict[str, Any]]) -> None:
        self._guard("replace")
        self._inner.replace(delete_seqs, insert_records)

    def reclaim(self, max_retained_bytes: int) -> int:
        self._guard("reclaim")
        return self._inner.reclaim(max_retained_bytes)


async def _no_wait(delay: float, stop: asyncio.Event) -> None:
    """A backoff wait that never sleeps (unused here — the engine never fails — but injected)."""
    return None


def _record(content: str, key: str) -> dict:
    return EpisodeRecord.new(content, "proj-a", repo="memrelay", idempotency_key=key).to_dict()


async def _run_until_drained(ingester: Ingester, spool: RaiseOnceSpool, *, timeout: float) -> None:
    """Run the ingester until the spool is drained (or timeout), then stop and await it.

    The task is *awaited* after ``stop`` — so if the run-loop died with an exception (the
    unfixed bug) it is re-raised here and fails the test rather than passing silently. A
    ``task.done()`` early-out keeps the failing case fast instead of spinning to the deadline.
    """
    stop = asyncio.Event()
    task = asyncio.create_task(ingester.run(stop))
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while spool.pending() > 0 and loop.time() < deadline:
        if task.done():
            break
        await asyncio.sleep(0.005)
    stop.set()
    await asyncio.wait_for(task, timeout=timeout)


def test_run_survives_transient_spool_error_and_drains_next_pass(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    inner = Spool(tmp_path / "spool" / "spool.db")
    inner.append(_record("a", "k0"))
    # read_batch blows up once with the exact fault the unguarded loop turned fatal.
    spool = RaiseOnceSpool(
        inner, fail_op="read_batch", exc=sqlite3.OperationalError("database is locked")
    )
    engine = RecordingEngine()
    ingester = Ingester(engine, spool, idle_sleep=0.01, backoff_wait=_no_wait)

    with caplog.at_level(logging.ERROR, logger="memrelay.ingest.ingester"):
        asyncio.run(_run_until_drained(ingester, spool, timeout=2.0))

    assert spool.raised == 1, "the injected spool fault fired exactly once"
    # Survived: the record drained on a later pass, noted exactly once, cursor advanced.
    assert [content for content, _ in engine.notes] == ["a"], "drained after the spool error"
    assert inner.pending() == 0, "checkpointed — the loop kept going after the fault"
    assert ingester.metrics()["episodes_ingested"] == 1
    # Logged loudly rather than dying silently.
    assert any(
        record.levelno == logging.ERROR and "loop" in record.getMessage().lower()
        for record in caplog.records
    ), "the transient spool error was logged at ERROR"

    inner.close()


def test_run_survives_a_checkpoint_error_mid_drain(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # A different unguarded op (checkpoint, called after a successful note) must also be
    # survivable. The row re-drains on a later pass; at-least-once means the engine may see
    # it twice, which is fine — the point is the loop lives and the spool eventually clears.
    inner = Spool(tmp_path / "spool" / "spool.db")
    inner.append(_record("a", "k0"))
    spool = RaiseOnceSpool(
        inner, fail_op="checkpoint", exc=sqlite3.OperationalError("disk I/O error")
    )
    engine = RecordingEngine()
    ingester = Ingester(engine, spool, idle_sleep=0.01, backoff_wait=_no_wait)

    with caplog.at_level(logging.ERROR, logger="memrelay.ingest.ingester"):
        asyncio.run(_run_until_drained(ingester, spool, timeout=2.0))

    assert spool.raised == 1, "the injected checkpoint fault fired exactly once"
    assert inner.pending() == 0, "the row was ultimately checkpointed — the loop survived"
    assert engine.notes, "the record was noted (at least once)"
    assert any(record.levelno == logging.ERROR for record in caplog.records), (
        "the transient spool error was logged at ERROR"
    )

    inner.close()
