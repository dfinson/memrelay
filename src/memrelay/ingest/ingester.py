"""The spool → Graphiti ingester: drain the durable spool into memory (E4-S5 #37).

:class:`Ingester` is the long-lived reader half of the ingest path. It repeatedly
pulls a batch of episodes off the :class:`~memrelay.ingest.spool.Spool`, notes each
one into the injected memory engine, and checkpoints it — turning durable-but-inert
spool rows into recallable graph memory.

Rate management (E3-S5 #32) shapes *when* and *how hard* the loop drains:

* **Batch during an active session, drain on idle.** While new rows keep arriving the
  loop *accumulates* them (a session actively producing memory) instead of paying the
  expensive per-episode ``engine.note`` immediately; it flushes the backlog once
  arrivals go quiet (idle) or the backlog reaches ``batch_size`` (so a long busy
  session can never grow the spool without bound). "Idle" is inferred **locally** from
  the spool: because the producer only *appends* and only this loop *checkpoints*,
  ``pending()`` is monotonic between drains, so a poll where it did not grow means no
  new rows arrived — no daemon hook required.
* **Exponential backoff when the engine is unavailable.** A failing ``engine.note`` is
  assumed transient (the LLM/engine is momentarily down) and retried with exponential
  backoff + full jitter (see :mod:`memrelay.ingest.backoff`), capped and interruptible
  on the daemon's stop event. The record is **not** checkpointed until it truly
  succeeds, so a crash (or shutdown) mid-backoff simply re-drains it on restart — **no
  data loss** (the spool's durable cursor guarantees this).
* **Poison tolerance (preserved).** Two failures are *not* transient and must never
  wedge the queue: a **malformed record** (missing ``content``/``namespace``) is dropped
  at the extraction seam without any retry (backoff can't fix a bug), and a record whose
  ``engine.note`` keeps failing past ``max_retries`` is finally dropped too. Both are
  logged loudly, counted, and checkpointed so one bad episode can never stall ingest for
  every episode behind it.
* **Backpressure & disk budget (E3-S4 #33).** Before ingesting, the loop checks the
  spool against a configurable byte budget; while it is over the high-water mark it
  summarizes the **oldest unprocessed** episodes *in place* (via an injectable, offline
  ``summarizer`` and the spool's atomic ``replace``) so the spool cannot grow the disk
  without bound while the engine is behind. Disabled by default (``max_bytes=0``), so the
  zero-config path is unchanged.
* **Metrics.** :meth:`metrics` exposes in-process counters (attempts, failures, retries,
  poison drops, backoff seconds, flushes, compactions) for observability; :meth:`stats`
  keeps its frozen two-key shape for the daemon health report.

The engine and spool are injected and only duck-typed (``await engine.note(...)``,
``spool.read_batch/checkpoint/pending/pending_bytes/replace``), and the backoff wait and
summarizer are injectable, so the loop unit-tests against fakes with no Kuzu, no network,
and no real sleeping.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from typing import Any, Protocol

from memrelay.ingest.backoff import (
    DEFAULT_BASE_DELAY,
    DEFAULT_MAX_DELAY,
    next_delay,
)
from memrelay.ingest.summarizer import Summarizer, default_summarizer

logger = logging.getLogger(__name__)

#: Prefix that carries an episode's derived phase into the noted content. Kept short
#: and deterministic so it embeds/FTS-indexes as ordinary episode text and shows up in
#: ``memory_recall`` output. See :func:`_content_with_phase`.
_PHASE_PREFIX = "Phase: "

#: How many transient ``engine.note`` failures to ride out with backoff before a record
#: is treated as poison and dropped. ``None`` means retry forever (capped delay) — never
#: dropping on unavailability, at the cost of wedging behind a truly-poison record.
DEFAULT_MAX_RETRIES: int | None = 5

#: Consecutive polls with no new rows before a held batch is flushed ("drain on idle").
#: One poll (≈ ``idle_sleep``) keeps drain latency sub-second while still coalescing a
#: burst of arrivals into a single flush.
DEFAULT_IDLE_FLUSH_CYCLES = 1

#: Max compaction passes per poll when over the disk budget (E3-S4 #33). Each pass folds
#: the oldest ``batch_size`` unprocessed rows into summaries; the cap bounds how long a
#: single poll can spend compacting so the drain loop stays responsive, and it backstops
#: any pathological "made no progress" case (the loop also bails the moment used-bytes
#: stops falling). Several passes let one poll claw a badly-over-budget spool back down.
DEFAULT_MAX_COMPACTION_PASSES = 64


def _content_with_phase(content: str, phase: str | None) -> str:
    """Fold an episode's derived phase into its noted content (E2-S6 #98).

    The graph seam (``engine.note`` → ``add_episode(episode_body=content)``) only
    surfaces free-text ``content`` in recall, and both ``engine.note`` and the MCP
    tool signatures are frozen — so phase reaches the graph as *queryable context* by
    prepending a compact ``Phase: <phase>`` header to the content. When ``phase`` is
    ``None`` (the default, phase-off) the content is returned **unchanged**, byte for
    byte identical to the pre-#98 behaviour, so nothing shifts on the default path.
    """
    if not phase:
        return content
    return f"{_PHASE_PREFIX}{phase}\n\n{content}"


class _Engine(Protocol):
    async def note(
        self,
        content: str,
        namespace: str,
        repo: str | None = None,
        source: str | None = None,
        *,
        last_commit_sha: str | None = None,
        file_change_lines: dict[str, int] | None = None,
    ) -> str: ...


class _Spool(Protocol):
    def read_batch(self, max_n: int = 100) -> list[tuple[int, dict[str, Any]]]: ...

    def checkpoint(self, seq: int) -> None: ...

    def pending(self) -> int: ...

    def pending_bytes(self) -> int: ...

    def retained_bytes(self) -> int: ...

    def replace(self, delete_seqs: list[int], insert_records: list[dict[str, Any]]) -> None: ...

    def reclaim(self, max_retained_bytes: int) -> int: ...


#: An injectable interruptible wait: sleep ``delay`` seconds unless ``stop`` fires first.
BackoffWait = Callable[[float, asyncio.Event], Awaitable[None]]


@dataclass
class IngestMetrics:
    """In-process ingest counters for observability (E3-S5 #32).

    Cumulative over the life of the process. Exposed via :meth:`Ingester.metrics`;
    kept separate from :meth:`Ingester.stats` (whose two-key shape is frozen for the
    daemon health report). Surfacing these through ``memrelay status`` is a deferred
    follow-up (it would touch the CLI seam owned by another lane).
    """

    #: Episodes successfully noted into the engine and checkpointed.
    episodes_ingested: int = 0
    #: Individual ``engine.note`` calls made (an episode can account for several).
    notes_attempted: int = 0
    #: ``engine.note`` calls that raised.
    notes_failed: int = 0
    #: Backoff retries scheduled after a transient failure.
    retries: int = 0
    #: Records dropped as poison (malformed, or still failing past ``max_retries``).
    poison_skipped: int = 0
    #: Idle/size-triggered flush passes over the spool backlog.
    batches_drained: int = 0
    #: Cumulative seconds of backoff wait scheduled (before interruption).
    backoff_sleep_seconds: float = 0.0
    #: Over-budget compaction passes run (E3-S4 #33): each folds the oldest unprocessed
    #: batch into summaries in place to keep the spool under its disk budget.
    compactions: int = 0
    #: Original episodes folded away by compaction (the rows the summaries replaced).
    episodes_compacted: int = 0
    #: Below-cursor history reclamation passes run (E3 #112): each prunes the oldest
    #: already-ingested rows to keep retained history under its byte budget.
    reclamations: int = 0
    #: Already-ingested rows pruned by reclamation (below-cursor history dropped).
    episodes_reclaimed: int = 0


class Ingester:
    """Drain a :class:`~memrelay.ingest.spool.Spool` into a memory engine.

    Args:
        engine: object exposing ``async note(content, namespace, repo=None, source=None,
            *, last_commit_sha=None, file_change_lines=None)`` (the merged
            :class:`~memrelay.engine.graphiti.MemoryEngine`).
        spool: object exposing ``read_batch`` / ``checkpoint`` / ``pending``.
        idle_sleep: seconds to wait (interruptibly) between polls when there is nothing
            to drain, or while accumulating a not-yet-full batch.
        batch_size: max episodes per :meth:`Spool.read_batch`, and the backlog size that
            force-flushes an actively-growing session so the spool stays bounded.
        idle_flush_cycles: consecutive no-new-row polls before a held batch is drained.
        max_retries: transient-failure retries before a record is dropped as poison;
            ``None`` retries forever with capped delay.
        backoff_base / backoff_cap: exponential-backoff base and ceiling (seconds).
        rng: jitter source for backoff (injectable for deterministic tests).
        backoff_wait: injectable interruptible wait; defaults to an ``asyncio.wait_for``
            on ``stop`` so a real deployment sleeps but tests need not.
        max_bytes: spool disk budget in bytes (E3-S4 #33). ``0`` (default) disables
            compaction, so the zero-config path is byte-identical to pre-#33.
        compaction_pct: high-water fraction of ``max_bytes`` at which the oldest
            unprocessed episodes are summarized in place before ingest.
        summarizer: injectable seam folding the oldest episodes into fewer/smaller ones;
            the default (:func:`~memrelay.ingest.summarizer.default_summarizer`) is pure
            and offline so tests never touch a real LLM.
        retention_bytes: below-cursor history budget in bytes (E3 #112). ``0`` (default)
            disables reclamation, so already-ingested rows are kept forever exactly as
            before; a positive value caps retained history, pruning the oldest below-cursor
            rows in place (via the spool's atomic ``reclaim``) so ``spool.db`` stays bounded
            in steady state. Independent of ``max_bytes`` (which bounds only the unprocessed
            backlog).
    """

    def __init__(
        self,
        engine: _Engine,
        spool: _Spool,
        *,
        idle_sleep: float = 0.5,
        batch_size: int = 100,
        idle_flush_cycles: int = DEFAULT_IDLE_FLUSH_CYCLES,
        max_retries: int | None = DEFAULT_MAX_RETRIES,
        backoff_base: float = DEFAULT_BASE_DELAY,
        backoff_cap: float = DEFAULT_MAX_DELAY,
        rng: Callable[[], float] = random.random,
        backoff_wait: BackoffWait | None = None,
        max_bytes: int = 0,
        compaction_pct: float = 0.9,
        summarizer: Summarizer = default_summarizer,
        retention_bytes: int = 0,
    ) -> None:
        self._engine = engine
        self._spool = spool
        self._idle_sleep = idle_sleep
        self._batch_size = batch_size
        self._idle_flush_cycles = max(1, idle_flush_cycles)
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._backoff_cap = backoff_cap
        self._rng = rng
        self._injected_backoff_wait = backoff_wait
        self._max_bytes = max_bytes
        self._compaction_pct = compaction_pct
        self._summarizer = summarizer
        self._retention_bytes = retention_bytes
        self._metrics = IngestMetrics()

    async def run(self, stop: asyncio.Event) -> None:
        """Loop until ``stop`` is set: accumulate arrivals, then flush on idle or size.

        Never raises for a bad record (see the module docstring); the only way out is
        ``stop`` being set. Safe to launch as a background task and cancel via
        ``stop.set()``.
        """
        last_pending = 0
        idle_polls = 0
        while not stop.is_set():
            # Disk watchdog first, so the oldest unprocessed episodes are summarized in
            # place *before* ingest whenever the spool is over its budget (E3-S4 #33).
            if self._maybe_compact(stop):
                # Compaction lowered pending() without a checkpoint, which would fool the
                # active-session detector below (it infers "new arrivals" from a rising
                # pending). Re-baseline so a compaction is never misread as a new burst.
                last_pending = self._spool.pending()
            # Retention watchdog: prune below-cursor history back under its byte budget
            # (E3 #112). It touches only already-ingested rows, so it never changes pending()
            # and cannot disturb the active-session detector — no re-baseline needed.
            self._maybe_reclaim()
            pending = self._spool.pending()
            if pending == 0:
                # Nothing buffered: reset the idle window and wait for the next arrival.
                last_pending = 0
                idle_polls = 0
                await self._idle(stop)
                continue
            if pending > last_pending:
                # New rows since the last poll → the session is active. Accumulate,
                # unless the backlog has hit the flush ceiling.
                last_pending = pending
                idle_polls = 0
                if pending >= self._batch_size:
                    await self._drain(stop)
                    last_pending = self._spool.pending()
                else:
                    await self._idle(stop)
                continue
            # No new rows this poll: once arrivals have been quiet long enough, flush.
            idle_polls += 1
            if idle_polls >= self._idle_flush_cycles:
                await self._drain(stop)
                last_pending = self._spool.pending()
                idle_polls = 0
            else:
                await self._idle(stop)

    async def _drain(self, stop: asyncio.Event) -> None:
        """Flush the whole pending backlog in ascending ``seq``, checkpointing each row.

        Reads in ``batch_size`` chunks until the spool is drained (or ``stop`` fires),
        so a large accumulated batch is fully cleared in one idle window. Ordering and
        at-most-once delivery are the spool's cursor semantics — batching changes only
        *when* we drain, never the order or duplication of rows.
        """
        self._metrics.batches_drained += 1
        while not stop.is_set():
            batch = self._spool.read_batch(self._batch_size)
            if not batch:
                return
            for seq, record in batch:
                await self._ingest_one(seq, record, stop)
                if stop.is_set():
                    return

    def _maybe_compact(self, stop: asyncio.Event) -> bool:
        """Summarize the oldest unprocessed episodes while over the disk budget (#33).

        Returns ``True`` if it compacted at least once. Disabled (an immediate no-op)
        when ``max_bytes <= 0``, which is the default — so the whole feature is dormant
        and behaviour is byte-identical unless a budget is configured.

        While the spool's :meth:`~memrelay.ingest.spool.Spool.pending_bytes` is at or
        above ``compaction_pct`` of the budget, it repeatedly takes the oldest
        ``batch_size`` unprocessed rows, folds them through the injected ``summarizer``
        into fewer, size-bounded records, and swaps them in via one atomic
        :meth:`~memrelay.ingest.spool.Spool.replace`. It stops as soon as the backlog is
        back under budget, when there is nothing left worth compressing (``< 2`` rows),
        when a pass fails to reclaim any bytes (no-progress guard), or after
        :data:`DEFAULT_MAX_COMPACTION_PASSES` — so it can never spin. It never writes the
        cursor and only ever touches rows past it, so the spool's no-loss / no-dup /
        monotonic-cursor guarantees hold across a crash mid-compaction.
        """
        if self._max_bytes <= 0:
            return False
        threshold = self._compaction_pct * self._max_bytes
        compacted = False
        for _ in range(DEFAULT_MAX_COMPACTION_PASSES):
            if stop.is_set():
                break
            used = self._spool.pending_bytes()
            if used < threshold:
                break
            batch = self._spool.read_batch(self._batch_size)
            if len(batch) < 2:
                # 0 or 1 pending rows cannot be compressed into anything smaller.
                break
            seqs = [seq for seq, _ in batch]
            records = [record for _, record in batch]
            summaries = self._summarizer(records)
            self._spool.replace(seqs, summaries)
            self._metrics.compactions += 1
            self._metrics.episodes_compacted += len(records)
            compacted = True
            if self._spool.pending_bytes() >= used:
                # A pass that reclaimed nothing (e.g. a budget below one summary's size):
                # bail rather than loop uselessly toward the pass cap.
                break
        return compacted

    def _maybe_reclaim(self) -> bool:
        """Prune below-cursor history back under the retention byte budget (E3 #112).

        Returns ``True`` if it pruned at least one already-ingested row. Disabled (an immediate
        no-op) when ``retention_bytes <= 0``, which is the default — so retention is dormant and
        the full ingested history is kept, byte-identical to pre-#112, unless a budget is set.

        Delegates to the spool's atomic :meth:`~memrelay.ingest.spool.Spool.reclaim`, which
        prunes the oldest ``seq <= cursor`` rows (keeping the newest history within budget) in one
        transaction. It never writes the cursor and only ever touches already-checkpointed rows,
        so exactly-once drain and the durable-monotonic cursor invariant hold across a crash
        mid-reclaim. Because it changes only below-cursor rows,
        :meth:`~memrelay.ingest.spool.Spool.pending` is unaffected — so, unlike compaction, this
        needs no re-baseline of the active-session detector.
        """
        if self._retention_bytes <= 0:
            return False
        pruned = self._spool.reclaim(self._retention_bytes)
        if pruned <= 0:
            return False
        self._metrics.reclamations += 1
        self._metrics.episodes_reclaimed += pruned
        return True

    async def _ingest_one(self, seq: int, record: dict[str, Any], stop: asyncio.Event) -> None:
        """Note one record with backoff; checkpoint only once it is truly handled.

        A malformed record is dropped (checkpointed) with no retry. A transient
        ``engine.note`` failure is retried with exponential backoff; the row is
        checkpointed on success, or dropped as poison once retries are exhausted. If
        ``stop`` fires mid-backoff the row is left un-checkpointed so it re-drains on the
        next run — the no-data-loss guarantee.
        """
        try:
            content = _content_with_phase(record["content"], record.get("phase"))
            namespace = record["namespace"]
        except Exception:
            # Malformed row: retrying can't conjure a missing field. Drop it, but loudly.
            logger.exception(
                "ingester: dropping malformed record seq=%s key=%s",
                seq,
                record.get("idempotency_key"),
            )
            self._metrics.poison_skipped += 1
            self._spool.checkpoint(seq)
            return

        repo = record.get("repo")
        source = record.get("source")
        # E9-S3 #60: forward file-refactor provenance so a big-refactor episode invalidates
        # its file's prior facts. Pure passthrough of two spool-carried provenance fields
        # (both ``None`` unless ``ingest.refactor_invalidation_lines`` is enabled), kept out
        # of ``content`` / the idempotency key so the zero-config note path is unchanged.
        last_commit_sha = record.get("last_commit_sha")
        file_change_lines = record.get("file_change_lines")
        attempt = 0
        while True:
            self._metrics.notes_attempted += 1
            try:
                await self._engine.note(
                    content,
                    namespace,
                    repo,
                    source=source,
                    last_commit_sha=last_commit_sha,
                    file_change_lines=file_change_lines,
                )
            except Exception as exc:
                self._metrics.notes_failed += 1
                if self._max_retries is not None and attempt >= self._max_retries:
                    # Not transient after all: drop as poison so it can't wedge the queue.
                    logger.exception(
                        "ingester: dropping record after %s retries seq=%s key=%s",
                        attempt,
                        seq,
                        record.get("idempotency_key"),
                    )
                    self._metrics.poison_skipped += 1
                    self._spool.checkpoint(seq)
                    return
                delay = next_delay(
                    attempt, base=self._backoff_base, cap=self._backoff_cap, rng=self._rng
                )
                self._metrics.retries += 1
                self._metrics.backoff_sleep_seconds += delay
                logger.warning(
                    "ingester: engine.note failed (attempt %s), backing off %.3fs seq=%s: %s",
                    attempt + 1,
                    delay,
                    seq,
                    exc,
                )
                await self._backoff_wait(delay, stop)
                if stop.is_set():
                    # Shutdown/crash mid-backoff: leave the row un-checkpointed → re-drain.
                    return
                attempt += 1
                continue
            # Success: the row is durably in the graph, so advance the cursor past it.
            self._metrics.episodes_ingested += 1
            self._spool.checkpoint(seq)
            return

    async def _idle(self, stop: asyncio.Event) -> None:
        """Wait ``idle_sleep`` seconds, returning early if ``stop`` is set."""
        try:
            await asyncio.wait_for(stop.wait(), timeout=self._idle_sleep)
        except TimeoutError:
            pass

    async def _backoff_wait(self, delay: float, stop: asyncio.Event) -> None:
        """Wait ``delay`` seconds before a retry, returning early if ``stop`` is set.

        Uses the injected wait when provided (tests supply a no-op recorder); otherwise
        mirrors :meth:`_idle` — an ``asyncio.wait_for`` on ``stop`` so a real backoff is
        both bounded and promptly interruptible at shutdown.
        """
        if self._injected_backoff_wait is not None:
            await self._injected_backoff_wait(delay, stop)
            return
        try:
            await asyncio.wait_for(stop.wait(), timeout=delay)
        except TimeoutError:
            pass

    def stats(self) -> dict[str, int]:
        """Return ingest counters for the daemon health report (frozen two-key shape).

        ``episodes_ingested`` counts successful notes this process; ``spool_pending``
        is the live backlog. The daemon (session A) folds these into ``health()``.
        Richer observability lives on :meth:`metrics` so this contract stays stable.
        """
        return {
            "episodes_ingested": self._metrics.episodes_ingested,
            "spool_pending": self._spool.pending(),
        }

    def metrics(self) -> dict[str, Any]:
        """Return a snapshot of the in-process rate-management counters (E3-S5 #32)."""
        return asdict(self._metrics)
