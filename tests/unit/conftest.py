"""Fixtures for the E6/E7 daemon + MCP unit tests (kept out of the root conftest).

Provides a hermetic CLI environment (``MEMRELAY_HOME`` / ``MEMRELAY_COPILOT_HOME``
pinned under ``tmp_path``, real ``~`` never touched) and an in-process
:class:`ThreadDaemon` so the ``start``/``status``/``stop`` CLI path can be tested
against a real socket without spawning a subprocess.
"""

from __future__ import annotations

import asyncio
import os
import threading
from pathlib import Path

import pytest

from memrelay.daemon.protocol import StubBackend
from memrelay.daemon.server import DaemonServer
from memrelay.daemon.transport import Endpoint


class ThreadDaemon:
    """Run a ``DaemonServer(StubBackend)`` in a background thread on its own loop.

    ``start()`` then ``wait_listening()`` blocks until the listener is bound, so a
    monkeypatched ``spawn_detached`` can return only once the daemon is reachable —
    exactly what :func:`memrelay.daemon.lifecycle.start_daemon` polls for.
    """

    def __init__(self, endpoint: Endpoint) -> None:
        self._endpoint = endpoint
        self._ready = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server: DaemonServer | None = None
        self._thread = threading.Thread(target=self._run, name="thread-daemon", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def wait_listening(self, timeout: float = 5.0) -> None:
        if not self._ready.wait(timeout):
            raise TimeoutError("in-process daemon did not start listening in time")

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        server = DaemonServer(StubBackend(), self._endpoint)
        self._server = server
        loop.run_until_complete(server.start())
        self._ready.set()
        try:
            loop.run_until_complete(server.run())  # start() is idempotent; waits for shutdown
        finally:
            loop.close()

    def stop(self) -> None:
        """Request a graceful shutdown from outside the loop and join the thread."""
        loop, server = self._loop, self._server
        if loop is not None and server is not None:
            try:
                loop.call_soon_threadsafe(server.request_shutdown)
            except RuntimeError:
                pass  # loop already stopped/closed
        self._thread.join(timeout=5.0)


@pytest.fixture
def cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Pin memrelay + Copilot homes under ``tmp_path``; clear inherited overrides."""
    for key in list(os.environ):
        if key.startswith(("MEMRELAY_", "XDG_")):
            monkeypatch.delenv(key, raising=False)
    user_home = tmp_path / "userhome"
    home = tmp_path / "mem"
    copilot = tmp_path / "copilot"
    monkeypatch.setenv("HOME", str(user_home))
    monkeypatch.setenv("USERPROFILE", str(user_home))
    monkeypatch.setenv("MEMRELAY_HOME", str(home))
    monkeypatch.setenv("MEMRELAY_COPILOT_HOME", str(copilot))
    return home, copilot


@pytest.fixture
def fake_daemon_spawn(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Patch ``lifecycle.spawn_detached`` to launch an in-process :class:`ThreadDaemon`.

    Yields a state dict (``count``, ``daemons``) for assertions; all spawned
    daemons are shut down and joined on teardown.
    """
    from memrelay.daemon import lifecycle
    from memrelay.daemon.transport import resolve_endpoint

    state: dict = {"count": 0, "daemons": []}

    def fake_spawn(home: Path) -> int:
        state["count"] += 1
        daemon = ThreadDaemon(resolve_endpoint(home))
        daemon.start()
        daemon.wait_listening()
        state["daemons"].append(daemon)
        return 424242

    monkeypatch.setattr(lifecycle, "spawn_detached", fake_spawn)
    yield state
    for daemon in state["daemons"]:
        daemon.stop()
