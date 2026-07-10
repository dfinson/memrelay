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
* **Live health** — merge ``sessions_observed`` and the ingester's
  ``episodes_ingested`` / ``spool_pending`` counters into the backend's health so
  ``memrelay status`` reflects a live system, while keeping the stub health keys.

Shutdown order is listener → ingester → engine: the server stops accepting work,
the ingester drains and exits, and only then is the Kuzu write lock released.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
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
    )


@dataclass
class _Counters:
    """Daemon-owned observation counters surfaced through health."""

    sessions_observed: int = 0


class LiveHealthBackend:
    """Wrap a :class:`Backend`, augmenting ``health`` with live daemon counters.

    ``search`` / ``detail`` / ``note`` pass straight through to the wrapped backend
    (the query answerer). ``health`` overlays the daemon-owned ``sessions_observed``
    counter and the hosted ingester's ``episodes_ingested`` / ``spool_pending`` so
    ``memrelay status`` shows a live system, while preserving every other key the
    wrapped backend reports and guaranteeing the stub health keys are present.
    """

    def __init__(
        self, backend: Backend, counters: _Counters, ingester: SupportsIngest | None
    ) -> None:
        self._backend = backend
        self._counters = counters
        self._ingester = ingester

    async def search(self, query: str, namespace: str, prefer_repo: str | None = None) -> JsonDict:
        return await self._backend.search(query, namespace, prefer_repo)

    async def detail(self, node_uuid: str, namespace: str) -> JsonDict:
        return await self._backend.detail(node_uuid, namespace)

    async def note(self, content: str, namespace: str, repo: str | None = None) -> str:
        return await self._backend.note(content, namespace, repo)

    async def health(self) -> JsonDict:
        report = dict(await self._backend.health())
        report.setdefault("status", "running")
        report["sessions_observed"] = self._counters.sessions_observed
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
    ) -> None:
        self._config = config
        self._endpoint = endpoint
        self._injected_backend = backend
        #: We only close a backend we built ourselves; injected ones stay the
        #: injector's responsibility (the E4 "used as-is, not rebuilt" seam).
        self._owns_backend = backend is None
        self._ingester_factory = ingester_factory
        self._counters = _Counters()
        self._backend: Backend | None = None
        self._ingester: SupportsIngest | None = None
        self._server: DaemonServer | None = None
        self._stop = asyncio.Event()
        self._ingest_task: asyncio.Task[None] | None = None

    @property
    def counters(self) -> _Counters:
        return self._counters

    @property
    def ingester(self) -> SupportsIngest | None:
        return self._ingester

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
        wrapper = LiveHealthBackend(self._backend, self._counters, self._ingester)
        self._server = DaemonServer(wrapper, self._endpoint)
        await self._server.start()
        if self._ingester is not None:
            self._ingest_task = asyncio.create_task(self._ingester.run(self._stop))

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
        """Stop the ingester (bounded) then close the engine we built. Idempotent."""
        self._stop.set()
        task, self._ingest_task = self._ingest_task, None
        if task is not None:
            await self._stop_ingester(task)
        if self._owns_backend and self._backend is not None:
            await self._close_backend(self._backend)
        self._backend = None

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
    async def _close_backend(backend: Backend) -> None:
        close = getattr(backend, "close", None)
        if close is None:
            return
        result = close()
        if inspect.isawaitable(result):
            await result
