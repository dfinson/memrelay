"""The daemon serving the REAL MemoryEngine end-to-end (E4 behind E6/E7).

Hermetic: an offline ``MemoryEngine`` (deterministic mock LLM + gate embedder,
temp Ladybug) is injected through the daemon's ``backend=`` seam. Two ingest paths are
covered: a fake ingester/spool drives the socket note→recall→drain ordering
deterministically, and Session B's real ``Spool`` → ``Ingester`` is hosted via the
default factory to prove the merged spool→engine path. No network, no API key, no
real ``~/.memrelay``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from memrelay.config import load_config
from memrelay.daemon import transport
from memrelay.daemon.protocol import StubBackend
from memrelay.daemon.runtime import DaemonRuntime
from memrelay.daemon.transport import Endpoint, resolve_endpoint
from memrelay.engine.graphiti import MemoryEngine
from memrelay.ingest.episode import EpisodeRecord
from memrelay.ingest.spool import Spool

pytestmark = pytest.mark.integration

NAMESPACE = "proj-a"
SOCKET_FACT = "memrelay stores persistent agent memory in an embedded Ladybug graph database."
SOCKET_QUERY = "which graph database backs memrelay memory"
SPOOL_FACT = "memrelay caches its hottest lookups in Redis for speed."
SPOOL_QUERY = "what does memrelay use for caching"
#: Entities the deterministic mock LLM "extracts". Order matters: the mock emits a
#: single ``vocab[0] uses vocab[1]`` edge per episode, and graphiti feeds the first
#: episode back as context into the second — so "Redis" must precede "Ladybug" for the
#: spooled Redis episode to yield a distinct "memrelay uses Redis" edge (subject is
#: always the shared "memrelay"). See the ingester-drain assertion below.
VOCAB = ["memrelay", "Redis", "Ladybug"]
#: A single fact exercised only through the real spool→ingester path (one episode,
#: so ``["memrelay", "Ladybug"]`` has no cross-episode contamination to order around).
REAL_SPOOL_FACT = (
    "memrelay keeps its persistent agent memory in an embedded Ladybug graph database."
)
REAL_SPOOL_QUERY = "which graph database stores memrelay memory"


async def _roundtrip(endpoint: Endpoint, message: dict) -> dict | None:
    reader, writer = await transport.connect(endpoint, timeout=5.0)
    try:
        await transport.write_message(writer, message)
        return await transport.read_message(reader)
    finally:
        writer.close()


def _mentions(result: dict | None, needle: str) -> bool:
    assert result is not None and set(result) == {"nodes", "edges", "scores"}, (
        f"bad shape: {result!r}"
    )
    blob = " ".join(f"{n.get('name') or ''} {n.get('summary') or ''}" for n in result["nodes"])
    blob += " " + " ".join(f"{e.get('name') or ''} {e.get('fact') or ''}" for e in result["edges"])
    return needle in blob.lower()


def _make_config(tmp_path: Path):
    graph_path = tmp_path / "graph.db"
    cfg = load_config(
        environ={}, home=str(tmp_path), graph={"path": str(graph_path), "backend": "ladybug"}
    )
    assert cfg.graph_path == graph_path.resolve()
    return cfg


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
    """Drains a spool into the shared engine once ``release`` is set, then idles.

    ``release`` lets the test order the socket write before the ingester's write so
    the two writers never touch the one Ladybug connection concurrently — keeping the
    end-to-end assertion deterministic.
    """

    def __init__(self, engine: MemoryEngine, spool: _FakeSpool, release: asyncio.Event) -> None:
        self._engine = engine
        self._spool = spool
        self._release = release
        self._ingested = 0
        self.drained = asyncio.Event()

    async def run(self, stop: asyncio.Event) -> None:
        await self._release.wait()
        for item in self._spool.read_batch():
            await self._engine.note(item["content"], NAMESPACE, item.get("repo"))
            self._ingested += 1
        self._spool.checkpoint(self._ingested)
        self.drained.set()
        await stop.wait()

    def stats(self) -> dict:
        return {"episodes_ingested": self._ingested, "spool_pending": self._spool.pending()}

    def metrics(self) -> dict:
        # The daemon reads metrics() in health(); this fake reports no ingest failures.
        return {
            "episodes_ingested": self._ingested,
            "notes_failed": 0,
            "poison_skipped": 0,
        }


def test_daemon_serves_real_engine_and_hosts_ingester(
    tmp_path: Path, gate_embedder, mock_llm_factory
) -> None:
    async def scenario() -> None:
        cfg = _make_config(tmp_path)
        endpoint = resolve_endpoint(tmp_path)
        engine = await MemoryEngine.from_config(
            cfg, llm_client=mock_llm_factory(VOCAB), embedder=gate_embedder
        )
        spool = _FakeSpool([{"content": SPOOL_FACT, "repo": "memrelay"}])
        release = asyncio.Event()
        created: dict = {}

        def factory(shared_engine, config):
            # The runtime must give the ingester the same engine it serves from.
            assert shared_engine is engine
            ingester = _FakeIngester(shared_engine, spool, release)
            created["ingester"] = ingester
            return ingester

        runtime = DaemonRuntime(cfg, endpoint, backend=engine, ingester_factory=factory)
        await runtime.start()
        serve_task = asyncio.create_task(runtime.serve())
        try:
            # 1) note a fact over the socket, then recall it over the socket.
            note_reply = await _roundtrip(
                endpoint,
                {
                    "method": "note",
                    "content": SOCKET_FACT,
                    "namespace": NAMESPACE,
                    "repo": "memrelay",
                },
            )
            assert note_reply is not None and note_reply.get("status")
            assert _mentions(
                await _roundtrip(
                    endpoint, {"method": "search", "query": SOCKET_QUERY, "namespace": NAMESPACE}
                ),
                "ladybug",
            )

            # 2) release the hosted ingester; it drains the spool into the SAME engine.
            release.set()
            await asyncio.wait_for(created["ingester"].drained.wait(), timeout=30.0)
            assert _mentions(
                await _roundtrip(
                    endpoint, {"method": "search", "query": SPOOL_QUERY, "namespace": NAMESPACE}
                ),
                "redis",
            )

            # 3) health surfaces the live counters (stub keys preserved).
            health = await _roundtrip(endpoint, {"method": "health"})
            assert health is not None
            assert health["episodes_ingested"] >= 1
            assert health["spool_pending"] == 0
            assert "sessions_observed" in health
        finally:
            runtime.request_shutdown()
            await asyncio.wait_for(serve_task, timeout=10.0)
        # We built the engine, so the runtime (given an injected backend) must NOT
        # have closed it — we own it and release the Ladybug lock here.
        await engine.close()

    asyncio.run(scenario())


def test_stub_backend_still_injectable_end_to_end(tmp_path: Path) -> None:
    """The frozen StubBackend remains a drop-in backend for the daemon runtime."""
    endpoint = resolve_endpoint(tmp_path)

    async def scenario() -> dict | None:
        cfg = load_config(environ={}, home=str(tmp_path))
        runtime = DaemonRuntime(
            cfg, endpoint, backend=StubBackend(), ingester_factory=lambda engine, config: None
        )
        await runtime.start()
        serve_task = asyncio.create_task(runtime.serve())
        try:
            return await _roundtrip(endpoint, {"method": "health"})
        finally:
            runtime.request_shutdown()
            await asyncio.wait_for(serve_task, timeout=5.0)

    health = asyncio.run(scenario())
    assert health is not None and health["status"] == "running"
    for key in ("sessions_observed", "episodes_ingested", "spool_pending"):
        assert key in health


def test_daemon_hosts_real_ingester_draining_real_spool(
    tmp_path: Path, gate_embedder, mock_llm_factory
) -> None:
    """The DEFAULT factory wires Session B's real ``Spool`` + ``Ingester`` (post-merge).

    Seeds the durable spool at its canonical ``<home>/spool/spool.db`` path, injects
    an offline engine, and lets the daemon's hosted *real* ingester drain the spool
    into that engine so the fact becomes recallable over the socket — the merged E4
    engine + #37 ingester, proven without any fake. Drain progress is observed
    in-process via ``ingester.stats()`` (SQLite/counter only, never Ladybug), so the
    socket recall happens after the single writer is quiescent — no engine contention.
    """

    async def scenario() -> None:
        cfg = _make_config(tmp_path)
        endpoint = resolve_endpoint(tmp_path)
        # Seed the real spool at exactly the path the default factory will open, then
        # close our writer so the hosted ingester is the only spool reader.
        with Spool(cfg.home_path / "spool" / "spool.db") as seed:
            seed.append(
                EpisodeRecord.new(REAL_SPOOL_FACT, namespace=NAMESPACE, repo="memrelay").to_dict()
            )
            assert seed.pending() == 1

        engine = await MemoryEngine.from_config(
            cfg, llm_client=mock_llm_factory(["memrelay", "Ladybug"]), embedder=gate_embedder
        )
        # No ingester_factory override → the real default factory builds B's Spool +
        # Ingester against the seeded db and shares this one engine instance.
        runtime = DaemonRuntime(cfg, endpoint, backend=engine)
        await runtime.start()
        assert runtime.ingester is not None, "default factory must host the real ingester"
        serve_task = asyncio.create_task(runtime.serve())
        try:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + 30.0
            while loop.time() < deadline:
                stats = runtime.ingester.stats()
                if stats["episodes_ingested"] >= 1 and stats["spool_pending"] == 0:
                    break
                await asyncio.sleep(0.05)
            else:
                raise AssertionError(f"real ingester did not drain the spool: {stats!r}")

            # The spooled fact reached the engine only via the hosted real ingester.
            assert _mentions(
                await _roundtrip(
                    endpoint,
                    {"method": "search", "query": REAL_SPOOL_QUERY, "namespace": NAMESPACE},
                ),
                "ladybug",
            )
            health = await _roundtrip(endpoint, {"method": "health"})
            assert health is not None
            assert health["episodes_ingested"] >= 1
            assert health["spool_pending"] == 0
            assert "sessions_observed" in health
        finally:
            runtime.request_shutdown()
            await asyncio.wait_for(serve_task, timeout=10.0)
        # We built the engine (injected as backend), so we release the Ladybug lock here;
        # the runtime must not have closed it.
        await engine.close()

    asyncio.run(scenario())
