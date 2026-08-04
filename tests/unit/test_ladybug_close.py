"""Unit tests for ``LadybugDriver.close()`` releasing the on-disk graph lock (#154).

The default Ladybug backend acquires a file-level ``READ_WRITE`` lock on ``graph.db`` in
``LadybugDriver.__init__`` (mirroring ``LadybugBackend.open_driver``). ``close()`` used to be
an inherited kuzu no-op that relied on GC to drop that lock, so an explicit ``close()`` did
not deterministically release it. These tests prove the fixed ``close()`` releases the lock
*now* — the key proof being that a second open of the same ``graph.db`` succeeds in the same
process (which fails against the pre-fix no-op ``close()``).

Async is driven with ``asyncio.run`` (the suite runs under ``PYTEST_DISABLE_PLUGIN_AUTOLOAD``
so there is no ``pytest-asyncio``), and each test gets its own ``graph.db`` under ``tmp_path``.
"""

from __future__ import annotations

import asyncio

import pytest

from memrelay.config import load_config
from memrelay.engine.backends import ladybug_backend
from memrelay.engine.backends.ladybug_backend import LadybugBackend
from memrelay.engine.backends.ladybug_driver import LadybugDriver
from memrelay.engine.graphiti import _close_driver_quietly


def _open_driver(tmp_path) -> LadybugDriver:
    """Open a real Ladybug driver on a temp ``graph.db`` (acquires the file lock)."""
    return LadybugDriver(db=str(tmp_path / "graph.db"), max_concurrent_queries=1)


def test_close_releases_lock_after_failed_construction(tmp_path) -> None:
    """A second in-process open of the same ``graph.db`` succeeds after ``close()``.

    Reproduces ``MemoryEngine.from_config``'s construction-failure path: ``open_driver`` has
    acquired the lock and a later step raises, so the driver is run through the real
    ``_close_driver_quietly`` cleanup. With the fixed ``close()`` the lock is released now, so
    re-opening the same graph in the same process works. Against the pre-fix no-op ``close()``
    the re-open below raises ``RuntimeError: Could not set lock on file`` — this is the
    counterfactual that makes the test meaningful.
    """
    graph = tmp_path / "graph.db"
    driver = LadybugDriver(db=str(graph), max_concurrent_queries=1)

    # Simulate "construction failed after open_driver": from_config's best-effort cleanup.
    asyncio.run(_close_driver_quietly(driver))

    # The proof: re-open the SAME graph.db in the SAME process and use it.
    reopened = LadybugDriver(db=str(graph), max_concurrent_queries=1)
    try:
        rows, _, _ = asyncio.run(reopened.execute_query("RETURN 1 AS one"))
        assert rows == [{"one": 1}]
    finally:
        asyncio.run(reopened.close())


def test_normal_open_use_close(tmp_path) -> None:
    """A clean open -> use -> close cycle still works and leaves the driver closed."""
    driver = _open_driver(tmp_path)

    async def _use_and_close() -> list[dict[str, object]]:
        rows, _, _ = await driver.execute_query("RETURN 1 AS one")
        await driver.close()
        return rows

    rows = asyncio.run(_use_and_close())

    assert rows == [{"one": 1}]
    # No operation is attempted on the driver after close(); we only inspect closed state.
    assert driver._closed is True
    assert driver.db.is_closed is True


def test_double_close_is_safe(tmp_path) -> None:
    """Calling ``close()`` twice is a safe no-op (no double-close / use-after-close hazard)."""
    driver = _open_driver(tmp_path)

    async def _close_twice() -> None:
        await driver.close()
        await driver.close()  # must not raise on the AsyncConnection + Database pair

    asyncio.run(_close_twice())

    assert driver._closed is True
    assert driver.db.is_closed is True


def test_backend_closes_driver_when_delta_setup_fails(tmp_path, monkeypatch) -> None:
    graph = tmp_path / "graph.db"
    cfg = load_config(
        environ={},
        home=str(tmp_path),
        graph={"backend": "ladybug", "path": str(graph)},
    )

    async def fail_deltas(*args, **kwargs) -> None:
        raise RuntimeError("simulated FTS setup failure")

    monkeypatch.setattr(ladybug_backend, "apply_graphiti_deltas", fail_deltas)

    with pytest.raises(RuntimeError, match="simulated FTS setup failure"):
        asyncio.run(LadybugBackend().open_driver(cfg))

    reopened = LadybugDriver(db=str(graph), max_concurrent_queries=1)
    asyncio.run(reopened.close())


def test_backend_close_failure_does_not_mask_the_setup_error(tmp_path, monkeypatch, caplog) -> None:
    """A failing cleanup must not replace the error that caused the cleanup.

    ``open_driver`` closes the driver on a delta-setup failure to release the
    engine's file-level READ_WRITE lock. If ``close()`` itself raises, the original
    diagnostic — the only one that explains *why* startup failed — would be lost.
    """
    import logging

    cfg = load_config(
        environ={},
        home=str(tmp_path),
        graph={"backend": "ladybug", "path": str(tmp_path / "graph.db")},
    )

    async def fail_deltas(*args, **kwargs) -> None:
        raise RuntimeError("simulated FTS setup failure")

    async def fail_close(self) -> None:
        raise RuntimeError("close blew up too")

    monkeypatch.setattr(ladybug_backend, "apply_graphiti_deltas", fail_deltas)
    monkeypatch.setattr(LadybugDriver, "close", fail_close)

    with (
        caplog.at_level(logging.WARNING),
        pytest.raises(RuntimeError, match="simulated FTS setup failure"),
    ):
        asyncio.run(LadybugBackend().open_driver(cfg))

    assert any("failed to close Ladybug driver" in r.getMessage() for r in caplog.records)
