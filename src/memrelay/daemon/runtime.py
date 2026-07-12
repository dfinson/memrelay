"""Daemon runtime: back the real MemoryEngine and host the spool→engine ingester.

``run_foreground`` (see :mod:`memrelay.daemon.lifecycle`) delegates here so the
same orchestration is drivable in-process by tests. Responsibilities:

* **Backend resolution** — build the async
  :class:`~memrelay.engine.graphiti.MemoryEngine` when none is injected, or use an
  explicitly injected backend *as-is* (the E4 test seam). Only a backend we built
  is closed on shutdown; an injected one is owned by its injector.
* **Ingester hosting** — build the spool→engine ingester via an injectable factory
  and run it as a background task that shares the daemon's single engine instance
  (the daemon is the sole writer). If the ingest seams are unavailable the daemon
  still starts and serves queries without one.
* **Session discovery** — optionally host a
  :class:`~memrelay.daemon.session_discovery.SessionDiscoveryPoller` (E1-S4 #8) via an
  injectable factory, so a live daemon captures every active session into the shared
  spool. Off unless a factory is supplied (``run_foreground`` wires the real one), so
  in-process tests never scan a real agent home.
* **Live health** — merge ``sessions_observed`` / ``active_sessions`` and the ingester's
  ``episodes_ingested`` / ``spool_pending`` counters into the backend's health so
  ``memrelay status`` reflects a live system, while keeping the stub health keys.

Shutdown order is listener → poller → ingester → engine: the server stops accepting
work, session capture stops, the ingester drains and exits, and only then is the Kuzu
write lock released.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from memrelay.daemon.protocol import Backend, JsonDict
from memrelay.daemon.server import DaemonServer

if TYPE_CHECKING:
    from memrelay.config import Config
    from memrelay.daemon.transport import Endpoint

logger = logging.getLogger(__name__)

#: Grace period (seconds) for the ingester to drain and exit before it is cancelled.
INGEST_STOP_TIMEOUT = 5.0

#: Grace period (seconds) for the session poller to stop its captures before cancel.
POLLER_STOP_TIMEOUT = 5.0


@runtime_checkable
class SupportsIngest(Protocol):
    """The slice of the frozen ``Ingester`` contract the daemon depends on."""

    async def run(self, stop: asyncio.Event) -> None: ...

    def stats(self) -> JsonDict: ...


#: Builds the ingester for a resolved engine, or returns ``None`` to run without one.
IngesterFactory = Callable[[Any, "Config"], "SupportsIngest | None"]


def default_ingester_factory(engine: Any, config: Config) -> SupportsIngest | None:
    """Build the real spool→engine ingester, or ``None`` if the seams aren't present.

    The import is lazy so the daemon (and this module's importers) never depend on
    Session B's ingest package being merged: until it is, the daemon simply runs
    without an ingester and still answers queries. The spool lives at
    ``<home>/spool/spool.db`` per the frozen Wave-3 contract.
    """
    try:
        from memrelay.ingest.ingester import Ingester
        from memrelay.ingest.spool import Spool
    except ImportError:
        logger.debug("ingest seams unavailable; daemon will run without an ingester")
        return None
    spool_dir = config.home_path / "spool"
    spool_dir.mkdir(parents=True, exist_ok=True)
    spool = Spool(spool_dir / "spool.db")
    return Ingester(
        engine,
        spool,
        max_bytes=config.ingest.spool_max_bytes,
        compaction_pct=config.ingest.spool_compaction_pct,
        retention_bytes=config.ingest.spool_retention_bytes,
    )


@runtime_checkable
class SupportsPoller(Protocol):
    """The slice of :class:`SessionDiscoveryPoller` the daemon depends on."""

    async def run(self, stop: asyncio.Event) -> None: ...

    def stats(self) -> JsonDict: ...

    async def aclose(self) -> None: ...


#: Builds the session poller for a resolved engine, or ``None`` to run without one.
PollerFactory = Callable[[Any, "Config"], "SupportsPoller | None"]


def default_poller_factory(engine: Any, config: Config) -> SupportsPoller | None:
    """Build the real session-discovery poller, or ``None`` if the seams aren't present.

    Lazy imports keep this module free of the ingest/provider packages until a live
    daemon actually asks for a poller (mirrors :func:`default_ingester_factory`). The
    poller writes discovered sessions into the *same* ``<home>/spool/spool.db`` the
    hosted ingester drains, so capture and ingest compose with no extra wiring. Degrades
    to ``None`` (the daemon still serves queries) if the seams or a provider are absent.
    """
    try:
        from memrelay.daemon.session_discovery import (
            LiveTailCapture,
            RunObserveCapture,
            SessionCapture,
            SessionDiscoveryPoller,
            active_sessions,
        )
        from memrelay.ingest.spool import Spool
        from memrelay.providers.base import SessionRef
        from memrelay.providers.registry import get_registry
    except ImportError:
        logger.debug("session-discovery seams unavailable; daemon will run without a poller")
        return None
    try:
        provider = get_registry().resolve()
    except Exception:  # noqa: BLE001 - provider resolution must never crash daemon startup
        logger.debug("no provider resolved; daemon will run without a poller", exc_info=True)
        return None

    spool_dir = config.home_path / "spool"
    spool_dir.mkdir(parents=True, exist_ok=True)
    spool = Spool(spool_dir / "spool.db")
    interval = config.ingest.session_poll_interval
    freshness = config.ingest.session_freshness_s
    namespace_map = config.namespaces.repo_map

    def discover() -> list[SessionRef]:
        return active_sessions(provider, now=time.time(), freshness_s=freshness)

    # E1-S2 #11: select the per-session capture by config. Default "replay" keeps #8's
    # RunObserveCapture verbatim (the shipping default); "file_watch" opts into the live
    # tail (LiveTailCapture) — the retained replay backstop + a real-time FileWatch tail,
    # both feeding the one idempotent spool. The tail is best-effort latency-only: it carries
    # no durable offset (start_at="beginning" + spool dedupe own losslessness), so there is no
    # per-session cursor state to wire here.
    intake_source = config.ingest.intake_source

    def capture_factory(ref: SessionRef) -> SessionCapture:
        if intake_source == "file_watch":
            return LiveTailCapture(
                ref,
                spool=spool,
                provider=provider,
                config=config,
                namespace_map=namespace_map,
                interval=interval,
            )
        return RunObserveCapture(
            ref,
            spool=spool,
            provider=provider,
            config=config,
            namespace_map=namespace_map,
            interval=interval,
        )

    return SessionDiscoveryPoller(
        discover=discover,
        capture_factory=capture_factory,
        poll_interval=interval,
        max_sessions=config.ingest.max_sessions,
    )


@dataclass
class _Counters:
    """Daemon-owned observation counters surfaced through health."""

    sessions_observed: int = 0


class LiveHealthBackend:
    """Wrap a :class:`Backend`, augmenting ``health`` with live daemon counters.

    ``search`` / ``detail`` / ``note`` pass straight through to the wrapped backend
    (the query answerer). ``health`` overlays the hosted poller's ``sessions_observed`` /
    ``active_sessions`` (falling back to the daemon-owned counter when no poller runs) and
    the ingester's ``episodes_ingested`` / ``spool_pending`` so ``memrelay status`` shows a
    live system, while preserving every other key the wrapped backend reports and
    guaranteeing the stub health keys are present.
    """

    def __init__(
        self,
        backend: Backend,
        counters: _Counters,
        ingester: SupportsIngest | None,
        poller: SupportsPoller | None = None,
    ) -> None:
        self._backend = backend
        self._counters = counters
        self._ingester = ingester
        self._poller = poller

    async def search(self, query: str, namespace: str, prefer_repo: str | None = None) -> JsonDict:
        return await self._backend.search(query, namespace, prefer_repo)

    async def detail(self, node_uuid: str, namespace: str) -> JsonDict:
        return await self._backend.detail(node_uuid, namespace)

    async def note(self, content: str, namespace: str, repo: str | None = None) -> str:
        return await self._backend.note(content, namespace, repo)

    async def health(self) -> JsonDict:
        report = dict(await self._backend.health())
        report.setdefault("status", "running")
        poll_stats = self._poller.stats() if self._poller is not None else {}
        report["sessions_observed"] = int(
            poll_stats.get("sessions_observed", self._counters.sessions_observed)
        )
        report["active_sessions"] = int(
            poll_stats.get("active_sessions", report.get("active_sessions", 0))
        )
        stats = self._ingester.stats() if self._ingester is not None else {}
        report["episodes_ingested"] = int(
            stats.get("episodes_ingested", report.get("episodes_ingested", 0))
        )
        report["spool_pending"] = int(stats.get("spool_pending", report.get("spool_pending", 0)))
        return report


class DaemonRuntime:
    """Owns the daemon's backend, server, and hosted ingester for one endpoint.

    Usable two ways, mirroring :class:`~memrelay.daemon.server.DaemonServer`:

    * **Foreground** — ``await serve()`` blocks until shutdown, then stops the
      ingester and closes the engine.
    * **In-process** (tests) — ``await start()`` to begin listening, drive queries
      over the socket, then ``request_shutdown()`` and await the ``serve()`` task.
    """

    def __init__(
        self,
        config: Config,
        endpoint: Endpoint,
        *,
        backend: Backend | None = None,
        ingester_factory: IngesterFactory = default_ingester_factory,
        poller_factory: PollerFactory | None = None,
    ) -> None:
        self._config = config
        self._endpoint = endpoint
        self._injected_backend = backend
        #: We only close a backend we built ourselves; injected ones stay the
        #: injector's responsibility (the E4 "used as-is, not rebuilt" seam).
        self._owns_backend = backend is None
        self._ingester_factory = ingester_factory
        #: Session discovery is opt-in: the default (None) keeps the in-process/test
        #: runtime from scanning a real agent home. ``run_foreground`` supplies the real
        #: factory, so only a live daemon captures sessions.
        self._poller_factory = poller_factory
        self._counters = _Counters()
        self._backend: Backend | None = None
        self._ingester: SupportsIngest | None = None
        self._poller: SupportsPoller | None = None
        self._server: DaemonServer | None = None
        self._stop = asyncio.Event()
        self._ingest_task: asyncio.Task[None] | None = None
        self._poll_task: asyncio.Task[None] | None = None

    @property
    def counters(self) -> _Counters:
        return self._counters

    @property
    def ingester(self) -> SupportsIngest | None:
        return self._ingester

    @property
    def poller(self) -> SupportsPoller | None:
        return self._poller

    @property
    def server(self) -> DaemonServer:
        if self._server is None:
            raise RuntimeError("DaemonRuntime.start() has not run yet")
        return self._server

    async def start(self) -> None:
        """Resolve the backend, host the ingester, and begin listening (idempotent)."""
        if self._server is not None:
            return
        if self._injected_backend is not None:
            self._backend = self._injected_backend
        else:
            self._backend = await self._build_engine()
        # The ingester writes through the daemon's single engine instance.
        self._ingester = self._ingester_factory(self._backend, self._config)
        # Session discovery (opt-in) captures active sessions into the shared spool.
        self._poller = (
            self._poller_factory(self._backend, self._config)
            if self._poller_factory is not None
            else None
        )
        wrapper = LiveHealthBackend(self._backend, self._counters, self._ingester, self._poller)
        self._server = DaemonServer(wrapper, self._endpoint)
        await self._server.start()
        if self._ingester is not None:
            self._ingest_task = asyncio.create_task(self._ingester.run(self._stop))
            # Fire-and-forget: without this callback a fatal exit of the ingester task would
            # be swallowed unretrieved, silently stopping ingest while the daemon serves on.
            self._ingest_task.add_done_callback(self._on_ingest_task_done)
        if self._poller is not None:
            self._poll_task = asyncio.create_task(self._poller.run(self._stop))

    async def _build_engine(self) -> Backend:
        # Imported lazily so importing this module never pulls in graphiti_core/kuzu.
        from memrelay.engine.graphiti import MemoryEngine

        return await MemoryEngine.from_config(self._config)

    def request_shutdown(self) -> None:
        """Signal the server and the ingester to stop (safe from a signal handler)."""
        if self._server is not None:
            self._server.request_shutdown()
        self._stop.set()

    async def serve(self) -> None:
        """Serve until shutdown is requested, then stop the ingester and close the engine."""
        await self.start()
        assert self._server is not None
        try:
            await self._server.run()
        finally:
            await self.aclose()

    async def aclose(self) -> None:
        """Stop the poller then the ingester (both bounded), then close the engine. Idempotent."""
        self._stop.set()
        poll_task, self._poll_task = self._poll_task, None
        if poll_task is not None or self._poller is not None:
            await self._stop_poller(poll_task)
        task, self._ingest_task = self._ingest_task, None
        if task is not None:
            await self._stop_ingester(task)
        if self._owns_backend and self._backend is not None:
            await self._close_backend(self._backend)
        self._backend = None

    async def _stop_poller(self, task: asyncio.Task[None] | None) -> None:
        """Await the poller's graceful exit (cancelling if it overruns), then close it.

        The poller's ``run`` loop stops every capture in its ``finally`` once ``stop`` is
        set; the explicit ``aclose`` is a belt-and-suspenders guarantee that captures are
        torn down even if the loop task never started or was cancelled mid-flight.
        """
        if task is not None:
            try:
                await asyncio.wait_for(task, timeout=POLLER_STOP_TIMEOUT)
            except (TimeoutError, asyncio.CancelledError):
                logger.debug(
                    "session poller did not stop within %.1fs; cancelled", POLLER_STOP_TIMEOUT
                )
            except Exception:  # noqa: BLE001 - a failing poller must not break shutdown
                logger.debug("session poller task errored during shutdown", exc_info=True)
        poller, self._poller = self._poller, None
        if poller is not None:
            try:
                await poller.aclose()
            except Exception:  # noqa: BLE001 - teardown must not break shutdown
                logger.debug("session poller aclose errored during shutdown", exc_info=True)

    @staticmethod
    async def _stop_ingester(task: asyncio.Task[None]) -> None:
        """Await the ingester's graceful exit, cancelling it if it overruns."""
        try:
            await asyncio.wait_for(task, timeout=INGEST_STOP_TIMEOUT)
        except (TimeoutError, asyncio.CancelledError):
            logger.debug("ingester did not stop within %.1fs; cancelled", INGEST_STOP_TIMEOUT)
        except Exception:  # noqa: BLE001 - a failing ingester must not break shutdown
            logger.debug("ingester task errored during shutdown", exc_info=True)

    @staticmethod
    def _on_ingest_task_done(task: asyncio.Task[None]) -> None:
        """Surface an unexpected death of the fire-and-forget ingester task (rt-ingest F2).

        :meth:`Ingester.run` is self-healing — it catches per-pass spool faults and retries —
        so the task should only ever finish by ``stop`` being set (normal return) or by being
        cancelled at shutdown. Any *other* exit means it died with an exception that, because
        the task is launched fire-and-forget, would otherwise be swallowed unretrieved: ingest
        would stop while the daemon keeps answering from stale memory. Log it at ERROR so it is
        at least visible; reading ``exception()`` here also clears asyncio's "Task exception
        was never retrieved" warning. Shutdown paths still await the task separately.
        """
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error("ingester task exited unexpectedly; ingest has stopped", exc_info=exc)

    @staticmethod
    async def _close_backend(backend: Backend) -> None:
        close = getattr(backend, "close", None)
        if close is None:
            return
        result = close()
        if inspect.isawaitable(result):
            await result
