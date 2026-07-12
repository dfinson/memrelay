"""Session discovery & multi-session management for the daemon (E1-S4 #8).

The daemon hosts one global spool→engine :class:`~memrelay.ingest.ingester.Ingester`
(the *reader* half). This module adds the *writer* half for a live, multi-session
daemon: a :class:`SessionDiscoveryPoller` that, on a ~2s cadence, asks a provider which
sessions are **active** and keeps one per-session capture running for each, starting new
ones and stopping ended ones cleanly.

Design (composition, not a rewrite):

* **Discovery** is an injected ``discover`` callable returning the currently-active
  :class:`~memrelay.providers.base.SessionRef`s. The production default,
  :func:`active_sessions`, filters ``provider.discover_sessions()`` (SPEC §3.1) to the
  sessions whose ``events.jsonl`` was touched within a freshness window.
* **Per-session capture** is a :class:`SessionCapture`; the production
  :class:`RunObserveCapture` periodically replays a session through the existing,
  idempotent :func:`~memrelay.ingest.graphiti_sink.run_observe` into the shared spool
  (re-reading a growing file appends only new episodes — the spool's unique
  ``idempotency_key`` guarantees exactly-once). Efficient live tailing with a durable
  read-offset is an explicit deferred optimization (see ``run_observe``'s docstring / #11);
  re-observe-on-cadence is correct today, just not maximal.
* **Concurrency bound.** The tracked captures live in an ``OrderedDict`` recency map; when
  it exceeds ``max_sessions`` the least-recently-active capture is stopped and evicted —
  the same LRU-with-cold-restart semantics (and the same default cap value) as TraceForge's
  ``EventPipeline`` per-session eviction (:data:`_DEFAULT_MAX_SESSIONS`), reused rather than
  reinvented. Realistic concurrent workloads never approach the cap; it is a safety bound.

Everything here is engine-free and driven by injected seams (``discover``, the capture
factory, and an interruptible ``wait``), so the poller unit-tests deterministically with a
fake discovery source and hand-driven ``poll_once`` ticks — no real 2s sleeps, no engine,
no network. Only the production :class:`RunObserveCapture` touches ``run_observe``.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Iterable
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from memrelay.providers.base import SessionRef

if TYPE_CHECKING:
    from memrelay.config import Config

logger = logging.getLogger(__name__)

#: Default cap on concurrently-tracked session captures. Sourced from TraceForge's own
#: per-session LRU default so memrelay leans on the same bound rather than inventing one;
#: falls back to a literal if the private symbol ever moves.
try:  # pragma: no cover - trivial import shim
    from traceforge.pipeline import _DEFAULT_MAX_SESSIONS as _DEFAULT_MAX_SESSIONS
except Exception:  # noqa: BLE001 - never let a traceforge internal break import
    _DEFAULT_MAX_SESSIONS = 4096

#: Default poll cadence (seconds). SPEC §3.1: session creation is infrequent and the check
#: is a cheap directory listing + stat, so ~2s is ample.
DEFAULT_POLL_INTERVAL = 2.0

#: Default freshness window (seconds): a session whose ``events.jsonl`` was modified within
#: this window counts as *active*. Generous relative to the poll cadence so a session that
#: is merely between turns is not misread as ended and torn down prematurely.
DEFAULT_FRESHNESS_S = 30.0

#: An interruptible wait: sleep ``timeout`` seconds, returning early when ``stop`` is set.
#: Injectable so tests never sleep on a wall clock (mirrors the ingester's ``backoff_wait``).
PollWait = Callable[[float, asyncio.Event], Awaitable[None]]

#: Builds the per-session capture for a discovered ref (injectable for deterministic tests).
CaptureFactory = Callable[[SessionRef], "SessionCapture"]

#: Returns the sessions considered active *right now* (injectable; default reads the provider).
DiscoverFn = Callable[[], Iterable[SessionRef]]


@runtime_checkable
class SessionCapture(Protocol):
    """One session's ingestion, as owned by the poller.

    ``start`` launches the capture (non-blocking); ``stop`` tears it down cleanly —
    cancelling any task, releasing handles, with no leaked resources.
    """

    def start(self) -> None: ...

    async def stop(self) -> None: ...


def active_sessions(
    provider: Any, *, now: float, freshness_s: float = DEFAULT_FRESHNESS_S
) -> list[SessionRef]:
    """Return the provider's sessions whose trace was touched within ``freshness_s``.

    The "active" predicate for :func:`SessionDiscoveryPoller`'s production ``discover``:
    ``provider.discover_sessions()`` (SPEC §3.1) enumerates *every* session with a trace,
    so we keep only those whose ``events.jsonl`` mtime is within the freshness window of
    ``now`` (a caller-supplied clock reading, so the whole thing stays deterministic). A
    ref with no path, or whose file has vanished, is treated as not-active.
    """
    fresh: list[SessionRef] = []
    for ref in provider.discover_sessions():
        if ref.path is None:
            continue
        try:
            mtime = os.path.getmtime(ref.path)
        except OSError:
            continue
        if now - mtime <= freshness_s:
            fresh.append(ref)
    return fresh


class RunObserveCapture:
    """Live per-session capture: replay a session into the shared spool on a cadence.

    Launches one asyncio task that loops :func:`~memrelay.ingest.graphiti_sink.run_observe`
    over the session's trace every ``interval`` seconds. Each pass re-reads the (growing)
    ``events.jsonl`` and appends only new episodes — idempotent by the spool's unique
    ``idempotency_key`` — so the task is safe to run repeatedly and safe to cancel at any
    point. A failed pass is logged and never crashes the daemon; on :meth:`stop` a final
    pass drains the trailing work-unit and the ``session.ended`` summary before the task is
    cancelled and awaited, so nothing is lost and nothing leaks.
    """

    def __init__(
        self,
        ref: SessionRef,
        *,
        spool: Any,
        provider: Any,
        config: Config,
        namespace_map: Any = None,
        interval: float = DEFAULT_POLL_INTERVAL,
        wait: PollWait | None = None,
    ) -> None:
        self._ref = ref
        self._spool = spool
        self._provider = provider
        self._config = config
        self._namespace_map = namespace_map
        self._interval = interval
        self._injected_wait = wait
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        """Launch the capture loop (idempotent)."""
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        try:
            while not self._stop.is_set():
                await self._observe_once()
                await self._wait()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a capture must never crash the poller/daemon
            logger.debug("session %s: capture loop errored", self._ref.session_id, exc_info=True)

    async def _observe_once(self) -> None:
        from memrelay.ingest.graphiti_sink import run_observe

        try:
            await run_observe(
                self._ref.path,
                self._ref.session_id,
                spool=self._spool,
                provider=self._provider,
                config=self._config,
                namespace_map=self._namespace_map,
            )
        except Exception:  # noqa: BLE001 - one bad pass must not stop live capture
            logger.warning("session %s: observe pass failed", self._ref.session_id, exc_info=True)

    async def stop(self) -> None:
        """Stop the loop, then do one final drain pass; leaves no task or handle behind."""
        self._stop.set()
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        # Final pass *after* the loop is fully stopped: capture the trailing work-unit and
        # the session.ended summary. Idempotent, so a race with the cancelled loop is safe.
        await self._observe_once()

    async def _wait(self) -> None:
        if self._injected_wait is not None:
            await self._injected_wait(self._interval, self._stop)
            return
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
        except TimeoutError:
            pass


class LiveTailCapture:
    """Per-session capture (#11) = #8's replay backstop **plus** a long-lived FileWatch tail.

    Composes an *unchanged* :class:`RunObserveCapture` (the lossless backstop: periodic
    whole-file :func:`~memrelay.ingest.graphiti_sink.run_observe` replay) with a best-effort,
    real-time tail (:func:`~memrelay.ingest.graphiti_sink.run_tail`, ``start_at="beginning"``,
    streamed until stopped). Both feed the **same** idempotent spool, deduped on
    ``idempotency_key`` — so the tail is a pure latency optimization and needs no independent
    losslessness or crash-durable offset: correctness is owned by the replay + spool dedupe,
    latency by the tail.

    Lifecycle mirrors :class:`RunObserveCapture` exactly (the poller drives both identically):
    ``start`` launches the replay loop then the tail task; ``stop`` signals the tail's normal
    (un-cancelled) select-based drain and awaits it — its ``finally`` runs the source
    ``__aexit__`` (watchdog observer stop + join, file close) so nothing leaks — then runs the
    replay's authoritative final drain. On LRU eviction / ``aclose`` the poller calls
    :meth:`stop`, tearing down both.
    """

    def __init__(
        self,
        ref: SessionRef,
        *,
        spool: Any,
        provider: Any,
        config: Config,
        namespace_map: Any = None,
        interval: float = DEFAULT_POLL_INTERVAL,
        wait: PollWait | None = None,
        tail_source_factory: Callable[[SessionRef], Any] | None = None,
        replay_capture: RunObserveCapture | None = None,
    ) -> None:
        self._ref = ref
        self._spool = spool
        self._provider = provider
        self._config = config
        self._namespace_map = namespace_map
        self._tail_source_factory = tail_source_factory
        #: The retained #8 backstop — injected for tests, else built with the same wiring.
        self._replay = replay_capture or RunObserveCapture(
            ref,
            spool=spool,
            provider=provider,
            config=config,
            namespace_map=namespace_map,
            interval=interval,
            wait=wait,
        )
        self._stop = asyncio.Event()
        self._tail_task: asyncio.Task[None] | None = None

    def start(self) -> None:
        """Launch the replay backstop then the live tail (idempotent)."""
        self._replay.start()
        if self._tail_task is None:
            self._tail_task = asyncio.create_task(self._run_tail())

    async def _run_tail(self) -> None:
        from memrelay.ingest.graphiti_sink import run_tail

        tail_source = (
            self._tail_source_factory(self._ref) if self._tail_source_factory is not None else None
        )
        try:
            await run_tail(
                self._ref.path,
                self._ref.session_id,
                spool=self._spool,
                provider=self._provider,
                config=self._config,
                namespace_map=self._namespace_map,
                stop=self._stop,
                tail_source=tail_source,
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a tail must never crash the poller/daemon
            logger.warning("session %s: live tail failed", self._ref.session_id, exc_info=True)

    async def stop(self) -> None:
        """Stop the tail (clean drain), then the replay backstop; leaves no task/handle."""
        # (1) Signal the tail's select-based stop and await its normal-context drain. A cancel
        #     fallback covers a wedged task, but the select means stop.set() alone suffices.
        self._stop.set()
        task, self._tail_task = self._tail_task, None
        if task is not None:
            try:
                await task
            except asyncio.CancelledError:
                pass
        # (2) The replay backstop's authoritative final drain (#8, unchanged): captures the
        #     trailing work-unit + session.ended summary, lossless regardless of the tail.
        await self._replay.stop()


class SessionDiscoveryPoller:
    """Detect active sessions on a cadence and keep one capture running per session.

    On each :meth:`poll_once`: stop captures whose session is no longer active (clean end),
    start a capture for each newly-active session, refresh recency for still-active ones,
    and evict the least-recently-active captures beyond ``max_sessions``. :meth:`run` drives
    :meth:`poll_once` on a loop with an interruptible wait; :meth:`aclose` stops everything.

    Args:
        discover: returns the currently-active :class:`SessionRef`s (injected; the daemon
            wires :func:`active_sessions` over the resolved provider).
        capture_factory: builds a :class:`SessionCapture` for a ref (injected so tests use a
            fake and the poller logic needs no engine).
        poll_interval: seconds between polls when running via :meth:`run`.
        max_sessions: cap on concurrently-tracked captures; ``None`` disables the bound.
            Defaults to TraceForge's :data:`_DEFAULT_MAX_SESSIONS`.
        wait: injectable interruptible wait so :meth:`run` never sleeps on a wall clock.
    """

    def __init__(
        self,
        *,
        discover: DiscoverFn,
        capture_factory: CaptureFactory,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        max_sessions: int | None = _DEFAULT_MAX_SESSIONS,
        wait: PollWait | None = None,
    ) -> None:
        self._discover = discover
        self._capture_factory = capture_factory
        self._poll_interval = poll_interval
        self._max_sessions = max_sessions
        self._injected_wait = wait
        #: session_id → capture, ordered least-recently-active first (the eviction victim).
        self._captures: OrderedDict[str, SessionCapture] = OrderedDict()
        self._sessions_started = 0

    async def run(self, stop: asyncio.Event) -> None:
        """Poll until ``stop`` is set, then stop every capture cleanly.

        Safe to launch as a background task and cancel via ``stop.set()``; a failing poll is
        logged and never breaks the loop.
        """
        try:
            while not stop.is_set():
                await self.poll_once()
                await self._wait(stop)
        finally:
            await self.aclose()

    async def poll_once(self) -> None:
        """Run one discovery tick: stop ended, start new, refresh recency, enforce the cap."""
        try:
            active = OrderedDict((ref.session_id, ref) for ref in self._discover())
        except Exception:  # noqa: BLE001 - a flaky discovery source must not break the loop
            logger.warning(
                "session discovery failed this poll; keeping current captures", exc_info=True
            )
            return

        # (1) Sessions that vanished from the active set have ended — stop them cleanly.
        for session_id in list(self._captures):
            if session_id not in active:
                await self._stop_capture(session_id)

        # (2) Start captures for newly-active sessions; refresh recency for the rest so a
        #     still-active session is never the eviction victim over a freshly-started one.
        for session_id, ref in active.items():
            existing = self._captures.get(session_id)
            if existing is not None:
                self._captures.move_to_end(session_id)  # idempotent: already capturing
                continue
            capture = self._capture_factory(ref)
            capture.start()
            self._captures[session_id] = capture
            self._sessions_started += 1
            logger.info("session %s: started ingestion", session_id)

        # (3) Bound concurrency (reuse TraceForge's LRU semantics; see module docstring).
        await self._evict_over_cap()

    async def _evict_over_cap(self) -> None:
        if self._max_sessions is None:
            return
        while len(self._captures) > self._max_sessions:
            victim = next(iter(self._captures))  # least-recently-active
            logger.info("session %s: evicted (over max_sessions=%s)", victim, self._max_sessions)
            await self._stop_capture(victim)

    async def _stop_capture(self, session_id: str) -> None:
        capture = self._captures.pop(session_id, None)
        if capture is None:
            return
        try:
            await capture.stop()
        except Exception:  # noqa: BLE001 - a failing teardown must not wedge the poller
            logger.debug("session %s: capture stop errored", session_id, exc_info=True)
        else:
            logger.info("session %s: stopped ingestion", session_id)

    async def aclose(self) -> None:
        """Stop every tracked capture cleanly (idempotent)."""
        for session_id in list(self._captures):
            await self._stop_capture(session_id)

    async def _wait(self, stop: asyncio.Event) -> None:
        if self._injected_wait is not None:
            await self._injected_wait(self._poll_interval, stop)
            return
        try:
            await asyncio.wait_for(stop.wait(), timeout=self._poll_interval)
        except TimeoutError:
            pass

    def stats(self) -> dict[str, int]:
        """Counters for the daemon health report: cumulative starts + current active count."""
        return {
            "sessions_observed": self._sessions_started,
            "active_sessions": len(self._captures),
        }
