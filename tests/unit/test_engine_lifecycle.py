"""Unit tests for ``engine/graphiti.py`` lifecycle + selection helpers.

These are the small, graph-free pieces of the memory engine that the end-to-end
integration tests exercise only on their happy path:

* :class:`PassthroughCrossEncoder` — the key-less no-op reranker's order-preserving contract.
* :meth:`MemoryEngine.health` — the **error** branch (a failing graph probe is surfaced, not
  raised) alongside the ok shape.
* :meth:`MemoryEngine.close` — driver-shape tolerance (no ``close`` / sync ``close`` / async
  ``close``).
* :func:`build_embedder` — config-driven provider selection (unknown → ``ValueError``; the
  ``local`` default wires the fastembed cache dir; ``openai`` routes to the byo-key builder),
  with fastembed stubbed so no model downloads.

They are driven with ``asyncio.run`` (the suite does not depend on pytest-asyncio).
"""

from __future__ import annotations

import asyncio
import sys
import types

import pytest

from memrelay.config import Config, EmbeddingsConfig
from memrelay.engine.embedder import LocalEmbedder
from memrelay.engine.graphiti import (
    MemoryEngine,
    PassthroughCrossEncoder,
    build_embedder,
)

# --- PassthroughCrossEncoder --------------------------------------------------


def test_passthrough_preserves_order_with_monotonic_scores() -> None:
    """RRF never invokes ``rank``; when something does, order is kept and scores decay."""
    passages = ["alpha", "beta", "gamma"]
    ranked = asyncio.run(PassthroughCrossEncoder().rank("q", passages))

    assert [passage for passage, _ in ranked] == passages  # input order preserved
    scores = [score for _, score in ranked]
    assert scores[0] == 1.0
    assert scores == sorted(scores, reverse=True)  # monotonically decreasing
    assert len(set(scores)) == len(scores)  # strictly decreasing -> stable, unique ranks


def test_passthrough_empty_passages_is_empty() -> None:
    assert asyncio.run(PassthroughCrossEncoder().rank("q", [])) == []


# --- MemoryEngine.health ------------------------------------------------------


class _OkDriver:
    async def execute_query(self, *args: object, **kwargs: object) -> tuple:
        return ([], None, None)


class _BoomDriver:
    async def execute_query(self, *args: object, **kwargs: object) -> tuple:
        raise RuntimeError("graph down")


def test_health_ok_shape_and_no_error_key() -> None:
    cfg = Config()
    engine = MemoryEngine(graphiti=object(), driver=_OkDriver(), cfg=cfg)

    report = asyncio.run(engine.health())

    assert report["status"] == "ok"
    assert "error" not in report  # the ok path never adds an error field
    # Config is reported verbatim (asserted against the same cfg, not hard-coded defaults).
    assert report["backend"] == cfg.graph.backend
    assert report["graph_path"] == str(cfg.graph_path)
    assert report["llm_strategy"] == cfg.llm.strategy
    assert report["embeddings_provider"] == cfg.embeddings.provider
    assert report["embeddings_model"] == cfg.embeddings.model


def test_health_surfaces_probe_failure_as_error_not_raise() -> None:
    engine = MemoryEngine(graphiti=object(), driver=_BoomDriver(), cfg=Config())

    report = asyncio.run(engine.health())

    assert report["status"] == "error"
    assert "graph down" in report["error"]  # the driver exception text is reported


# --- MemoryEngine.close -------------------------------------------------------


def test_close_is_noop_when_driver_has_no_close() -> None:
    # A driver without a ``close`` attribute must be tolerated silently (getattr -> None).
    engine = MemoryEngine(graphiti=object(), driver=object(), cfg=Config())
    asyncio.run(engine.close())  # must not raise


def test_close_invokes_sync_close() -> None:
    calls: list[str] = []

    class _SyncClose:
        def close(self) -> None:  # non-awaitable result -> not awaited
            calls.append("sync")

    engine = MemoryEngine(graphiti=object(), driver=_SyncClose(), cfg=Config())
    asyncio.run(engine.close())

    assert calls == ["sync"]


def test_close_awaits_async_close() -> None:
    calls: list[str] = []

    class _AsyncClose:
        async def close(self) -> None:  # awaitable result -> awaited
            calls.append("async")

    engine = MemoryEngine(graphiti=object(), driver=_AsyncClose(), cfg=Config())
    asyncio.run(engine.close())

    assert calls == ["async"]


# --- build_embedder: config-driven provider selection -------------------------


def test_build_embedder_unknown_provider_raises() -> None:
    cfg = Config(embeddings=EmbeddingsConfig(provider="bogus"))
    with pytest.raises(ValueError, match="unknown embeddings provider"):
        build_embedder(cfg)


def test_build_embedder_local_wires_fastembed_cache_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ``local`` default builds a fastembed-backed ``LocalEmbedder`` at ``home/models``.

    The whole ``fastembed`` module is swapped for a stub in ``sys.modules`` (the real one is
    unimportable here — it needs ``requests``, absent in this env), so constructing the embedder
    triggers **no** model download. We only assert the selection + cache-dir wiring.
    """
    captured: dict[str, object] = {}

    class _FakeTextEmbedding:
        def __init__(self, model_name: str, cache_dir: str | None = None) -> None:
            captured["model_name"] = model_name
            captured["cache_dir"] = cache_dir

    fake_fastembed = types.ModuleType("fastembed")
    fake_fastembed.TextEmbedding = _FakeTextEmbedding  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fastembed", fake_fastembed)

    cfg = Config(embeddings=EmbeddingsConfig(provider="local", model="test-model"))
    embedder = build_embedder(cfg)

    assert isinstance(embedder, LocalEmbedder)
    assert embedder.model_name == "test-model"
    # cache_dir is wired to <home>/models (per SPEC §6.3), passed through to fastembed.
    assert captured["model_name"] == "test-model"
    assert captured["cache_dir"] == str(cfg.home_path / "models")


def test_build_embedder_openai_routes_to_byo_key_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    """``provider="openai"`` delegates to the byo-key builder (no OpenAI client constructed here).

    The builder is stubbed so this asserts only the selection/routing — the real
    ``build_openai_embedder`` (network + key env) is the byo-key lane's concern, not the
    selector's.
    """
    sentinel = object()
    seen: dict[str, object] = {}

    def _fake_build_openai_embedder(cfg: Config) -> object:
        seen["cfg"] = cfg
        return sentinel

    monkeypatch.setattr(
        "memrelay.engine.llm.byo_key.build_openai_embedder",
        _fake_build_openai_embedder,
    )

    cfg = Config(embeddings=EmbeddingsConfig(provider="openai"))
    result = build_embedder(cfg)

    assert result is sentinel  # routed to the byo-key builder...
    assert seen["cfg"] is cfg  # ...and handed the same config
