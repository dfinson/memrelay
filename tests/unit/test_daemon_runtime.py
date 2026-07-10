"""Unit tests for the daemon runtime: engine/ingester hosting + live health.

Everything here is engine-free (no graphiti / kuzu): a ``StubBackend`` or a tiny
recording backend stands in, and a fake ingester/spool prove the daemon hosts the
ingester over the real socket, merges live counters into ``health``, shuts down
cleanly, and leaves an injected backend untouched — so this tier stays green
regardless of Session B (the real ``Ingester`` / ``Spool``).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from memrelay.config import load_config
from memrelay.daemon import transport
from memrelay.daemon.protocol import StubBackend
from memrelay.daemon.runtime import (
    DaemonRuntime,
    default_ingester_factory,
    default_poller_factory,
)
from memrelay.daemon.transport import Endpoint, resolve_endpoint


async def _roundtrip(endpoint: Endpoint, message: dict) -> dict | None:
    reader, writer = await transport.connect(endpoint, timeout=5.0)
    try:
        await transport.write_message(writer, message)
        return await transport.read_message(reader)
    finally:
        writer.close()


def _config(tmp_path: Path):
    # environ={} + absolute home keep this isolated from the caller's real env.
    return load_config(environ={}, home=str(tmp_path))


class _CloseTrackingStub(StubBackend):
    """A StubBackend that records whether the runtime closed it."""

    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _RecordingBackend:
    """Minimal ``Backend`` that remembers noted content so ``search`` can echo it."""

    def __init__(self) -> None:
        self.notes: list[str] = []

    async def search(self, query: str, namespace: str, prefer_repo: str | None = None) -> dict:
        hits = [text for text in self.notes if query.lower() in text.lower()]
        nodes = [{"uuid": f"n{i}", "name": text, "summary": text} for i, text in enumerate(hits)]
        return {"nodes": nodes, "edges": [], "scores": [1.0] * len(nodes)}

    async def detail(self, node_uuid: str, namespace: str) -> dict:
        return {
            "node": {"uuid": node_uuid, "name": node_uuid, "summary": ""},
            "connected_edges": [],
            "episodes": [],
        }

    async def note(self, content: str, namespace: str, repo: str | None = None) -> str:
        self.notes.append(content)
        return f"ep-{len(self.notes)}"

    async def health(self) -> dict:
        return {"status": "running"}


class _FakeSpool:
    def __init__(self, items: list[dict]) -> None:
        self._items = list(items)
        self._checkpointed = 0

    def read_batch(self, limit: int | None = None) -> list[dict]:
        return list(self._items)

    def checkpoint(self, count: int) -> None:
        self._checkpointed += count

    def pending(self) -> int:
        return max(0, len(self._items) - self._checkpointed)


class _FakeIngester:
    """Drains a ``_FakeSpool`` into the shared backend, then idles until ``stop``."""

    def __init__(
        self, backend: _RecordingBackend, spool: _FakeSpool, namespace: str = "ns"
    ) -> None:
        self._backend = backend
        self._spool = spool
        self._namespace = namespace
        self._ingested = 0
        self.drained = asyncio.Event()

    async def run(self, stop: asyncio.Event) -> None:
        for item in self._spool.read_batch():
            await self._backend.note(item["content"], self._namespace, item.get("repo"))
            self._ingested += 1
        self._spool.checkpoint(self._ingested)
        self.drained.set()
        await stop.wait()

    def stats(self) -> dict:
        return {"episodes_ingested": self._ingested, "spool_pending": self._spool.pending()}


def test_injected_stub_backend_serves_and_is_not_closed(tmp_path: Path) -> None:
    """An injected backend answers over the socket and is used as-is (never closed)."""
    endpoint = resolve_endpoint(tmp_path)

    async def scenario() -> tuple:
        stub = _CloseTrackingStub()
        runtime = DaemonRuntime(
            _config(tmp_path), endpoint, backend=stub, ingester_factory=lambda engine, cfg: None
        )
        await runtime.start()
        serve_task = asyncio.create_task(runtime.serve())
        try:
            health = await _roundtrip(endpoint, {"method": "health"})
            search = await _roundtrip(
                endpoint, {"method": "search", "query": "auth", "namespace": "ns"}
            )
            note = await _roundtrip(endpoint, {"method": "note", "content": "c", "namespace": "ns"})
            detail = await _roundtrip(
                endpoint, {"method": "detail", "node_uuid": "n1", "namespace": "ns"}
            )
        finally:
            runtime.request_shutdown()
            await asyncio.wait_for(serve_task, timeout=5.0)
        return stub, health, search, note, detail

    stub, health, search, note, detail = asyncio.run(scenario())
    # Stub shapes flow through the health-augmenting wrapper unchanged.
    assert set(search) == {"nodes", "edges", "scores"}
    assert note == {"status": "ok"}
    assert detail["node"]["uuid"] == "n1"
    # Health keeps the stub health keys even with no ingester hosted.
    assert health["status"] == "running"
    for key in ("sessions_observed", "episodes_ingested", "spool_pending"):
        assert key in health
    # Injected backend is the injector's to close — the runtime must not.
    assert stub.closed is False


def test_hosted_ingester_drains_into_shared_backend_and_health_reflects_it(tmp_path: Path) -> None:
    """The hosted ingester writes through the same backend; health shows live counters."""
    endpoint = resolve_endpoint(tmp_path)

    async def scenario() -> tuple:
        backend = _RecordingBackend()
        spool = _FakeSpool([{"content": "spooled fact about Redis"}, {"content": "second note"}])
        created: dict = {}

        def factory(engine, cfg):
            # The runtime must hand the ingester its resolved backend (shared instance).
            assert engine is backend
            ingester = _FakeIngester(engine, spool)
            created["ingester"] = ingester
            return ingester

        runtime = DaemonRuntime(
            _config(tmp_path), endpoint, backend=backend, ingester_factory=factory
        )
        await runtime.start()
        serve_task = asyncio.create_task(runtime.serve())
        try:
            await asyncio.wait_for(created["ingester"].drained.wait(), timeout=5.0)
            search = await _roundtrip(
                endpoint, {"method": "search", "query": "Redis", "namespace": "ns"}
            )
            health = await _roundtrip(endpoint, {"method": "health"})
        finally:
            runtime.request_shutdown()
            await asyncio.wait_for(serve_task, timeout=5.0)
        return backend, search, health

    backend, search, health = asyncio.run(scenario())
    # The spooled fact reached the engine only via the hosted ingester.
    assert any("Redis" in node["name"] for node in search["nodes"])
    assert backend.notes == ["spooled fact about Redis", "second note"]
    # Live counters surface through health.
    assert health["episodes_ingested"] == 2
    assert health["spool_pending"] == 0
    assert health["sessions_observed"] == 0


def test_default_ingester_factory_wires_real_seams_or_degrades(tmp_path: Path) -> None:
    """The default factory builds the real spool→engine ingester at the canonical
    ``<home>/spool/spool.db`` path when Session B's seams are present, and degrades
    to no ingester when they are not — so the daemon stays start-able either way."""
    cfg = _config(tmp_path)
    try:
        import memrelay.ingest.ingester  # noqa: F401
        import memrelay.ingest.spool  # noqa: F401
    except ImportError:
        assert default_ingester_factory(object(), cfg) is None
        return

    ingester = default_ingester_factory(object(), cfg)
    assert ingester is not None, "seams present: the default factory must host an ingester"
    # It is the real hosted contract (run + stats) over a freshly-created spool db.
    assert callable(ingester.run) and callable(ingester.stats)
    assert (cfg.home_path / "spool" / "spool.db").exists()
    assert ingester.stats() == {"episodes_ingested": 0, "spool_pending": 0}


def test_default_ingester_factory_threads_disk_budget(tmp_path: Path) -> None:
    """The factory forwards the ingest disk-budget config into the hosted Ingester
    (E3-S4 #33); the zero-config default leaves compaction dormant (``max_bytes == 0``),
    so the default daemon path stays byte-identical to pre-#33."""
    try:
        import memrelay.ingest.ingester  # noqa: F401
        import memrelay.ingest.spool  # noqa: F401
    except ImportError:
        return  # ingest seams absent → factory degrades to None; nothing to thread

    budgeted = default_ingester_factory(
        object(),
        load_config(
            environ={},
            home=str(tmp_path / "budgeted"),
            ingest={"spool_max_bytes": 4096, "spool_compaction_pct": 0.5},
        ),
    )
    assert budgeted is not None
    assert budgeted._max_bytes == 4096
    assert budgeted._compaction_pct == 0.5

    default = default_ingester_factory(
        object(), load_config(environ={}, home=str(tmp_path / "default"))
    )
    assert default is not None
    assert default._max_bytes == 0  # dormant: zero-config daemon path unchanged


class _FakePoller:
    """A hosted session poller: reports live counters, idles until stop, tracks aclose."""

    def __init__(self, *, sessions_observed: int = 3, active_sessions: int = 2) -> None:
        self._sessions_observed = sessions_observed
        self._active_sessions = active_sessions
        self.ran = asyncio.Event()
        self.closed = False

    async def run(self, stop: asyncio.Event) -> None:
        self.ran.set()
        await stop.wait()

    def stats(self) -> dict:
        return {
            "sessions_observed": self._sessions_observed,
            "active_sessions": self._active_sessions,
        }

    async def aclose(self) -> None:
        self.closed = True


def test_default_runtime_hosts_no_poller(tmp_path: Path) -> None:
    """Session discovery is opt-in: the default runtime hosts no poller and reports zeros.

    This is the safety default the in-process/test tier relies on — without it a plain
    ``DaemonRuntime`` would scan a real agent home."""
    endpoint = resolve_endpoint(tmp_path)

    async def scenario() -> tuple:
        runtime = DaemonRuntime(
            _config(tmp_path),
            endpoint,
            backend=StubBackend(),
            ingester_factory=lambda engine, cfg: None,
        )
        await runtime.start()
        serve_task = asyncio.create_task(runtime.serve())
        try:
            health = await _roundtrip(endpoint, {"method": "health"})
            poller = runtime.poller
        finally:
            runtime.request_shutdown()
            await asyncio.wait_for(serve_task, timeout=5.0)
        return poller, health

    poller, health = asyncio.run(scenario())
    assert poller is None  # no factory supplied → discovery is off
    assert health["sessions_observed"] == 0
    assert health["active_sessions"] == 0


def test_hosted_poller_surfaces_session_counters_and_is_closed(tmp_path: Path) -> None:
    """An injected poller is handed the resolved backend, feeds health, and is closed on stop."""
    endpoint = resolve_endpoint(tmp_path)

    async def scenario() -> tuple:
        backend = _RecordingBackend()
        created: dict = {}

        def poller_factory(engine, cfg):
            assert engine is backend  # the poller shares the daemon's resolved backend
            poller = _FakePoller(sessions_observed=3, active_sessions=2)
            created["poller"] = poller
            return poller

        runtime = DaemonRuntime(
            _config(tmp_path),
            endpoint,
            backend=backend,
            ingester_factory=lambda engine, cfg: None,
            poller_factory=poller_factory,
        )
        await runtime.start()
        serve_task = asyncio.create_task(runtime.serve())
        try:
            await asyncio.wait_for(created["poller"].ran.wait(), timeout=5.0)
            health = await _roundtrip(endpoint, {"method": "health"})
        finally:
            runtime.request_shutdown()
            await asyncio.wait_for(serve_task, timeout=5.0)
        return created["poller"], health

    poller, health = asyncio.run(scenario())
    # The poller's live counters surface through the health-augmenting wrapper.
    assert health["sessions_observed"] == 3
    assert health["active_sessions"] == 2
    # Shutdown tore the poller down cleanly.
    assert poller.closed is True


def test_default_poller_factory_wires_real_poller(tmp_path: Path) -> None:
    """The default factory builds the real poller over the shared spool, without polling.

    Merely *building* the poller opens the canonical ``<home>/spool/spool.db`` (same file
    the ingester drains) and returns the frozen ``run``/``stats``/``aclose`` contract; no
    session is discovered until ``run`` is driven, so this stays hermetic."""
    cfg = _config(tmp_path)
    poller = default_poller_factory(object(), cfg)
    assert poller is not None
    assert callable(poller.run) and callable(poller.stats) and callable(poller.aclose)
    assert poller.stats() == {"sessions_observed": 0, "active_sessions": 0}
    assert (cfg.home_path / "spool" / "spool.db").exists()
