"""Daemon process lifecycle: single-instance lock, detached spawn, graceful stop.

Covers E6-S1 (start/stop, PID/lock, restart-safe) and the foreground runner used
by the detached ``memrelay _serve`` process. Liveness is determined by *probing
the endpoint* (a cheap ``health`` round-trip) rather than trusting a PID — a stale
``daemon.pid`` from a crashed process therefore reads as "not running" and start
is restart-safe.

Module-level ``spawn_detached`` / ``probe_health`` are the seams the CLI tests
monkeypatch so they never launch a real background process.
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from memrelay.config import Config, ensure_home
from memrelay.daemon import transport
from memrelay.daemon.protocol import SHUTDOWN, Backend
from memrelay.daemon.runtime import DaemonRuntime, IngesterFactory, default_ingester_factory
from memrelay.daemon.transport import resolve_endpoint

PID_FILENAME = "daemon.pid"

#: Default timeouts (seconds).
PROBE_TIMEOUT = 0.5
READY_TIMEOUT = 10.0
STOP_TIMEOUT = 5.0
POLL_INTERVAL = 0.1


class DaemonStartError(RuntimeError):
    """Raised when a spawned daemon does not become healthy in time."""


@dataclass(frozen=True)
class DaemonStatus:
    """Snapshot of daemon liveness for ``status`` / ``start`` reporting."""

    running: bool
    pid: int | None
    health: dict | None


# ─── PID file ────────────────────────────────────────────────────────────────


def pid_path(home: Path) -> Path:
    return home / PID_FILENAME


def read_pid(home: Path) -> int | None:
    try:
        return int(pid_path(home).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def write_pid(home: Path, pid: int) -> None:
    pid_path(home).write_text(str(pid), encoding="utf-8")


def clear_pid(home: Path) -> None:
    try:
        pid_path(home).unlink()
    except FileNotFoundError:
        pass


# ─── Endpoint probes (the authoritative liveness signal) ─────────────────────


async def _request(home: Path, message: dict, timeout: float) -> dict | None:
    """Send one request to the daemon and return its response, or ``None``.

    ``None`` means the daemon is unreachable (not running / not yet listening).
    """
    endpoint = resolve_endpoint(home)
    try:
        reader, writer = await transport.connect(endpoint, timeout=timeout)
    except ConnectionError:
        return None
    try:
        await transport.write_message(writer, message)
        return await asyncio.wait_for(transport.read_message(reader), timeout=timeout)
    except (TimeoutError, ConnectionError, ValueError, OSError):
        return None
    finally:
        writer.close()


def probe_health(home: Path, timeout: float = PROBE_TIMEOUT) -> dict | None:
    """Synchronously ask the daemon for ``health`` (``None`` if unreachable)."""
    return asyncio.run(_request(home, {"method": "health"}, timeout))


def is_running(home: Path, timeout: float = PROBE_TIMEOUT) -> bool:
    """True iff a daemon answers a health probe on this home's endpoint."""
    return probe_health(home, timeout) is not None


def _send_shutdown(home: Path, timeout: float) -> bool:
    """Ask a running daemon to shut down gracefully; True if it acknowledged."""
    reply = asyncio.run(_request(home, {"method": SHUTDOWN}, timeout))
    return bool(reply and reply.get("status") == "stopping")


# ─── Spawning the detached daemon ────────────────────────────────────────────


def spawn_detached(home: Path) -> int:
    """Launch ``memrelay _serve`` as a detached background process; return its PID.

    Uses ``python -m memrelay`` (not the console script) so it works even when the
    ``memrelay`` executable is not on PATH. ``MEMRELAY_HOME`` pins the child to the
    same home directory.
    """
    env = dict(os.environ)
    env["MEMRELAY_HOME"] = str(home)
    kwargs: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "env": env,
        "cwd": str(home),
    }
    if os.name == "nt":
        kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS  # type: ignore[attr-defined]
            | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen([sys.executable, "-m", "memrelay", "_serve"], **kwargs)
    return proc.pid


# ─── start / stop / status ───────────────────────────────────────────────────


def start_daemon(
    config: Config,
    *,
    ready_timeout: float = READY_TIMEOUT,
    poll_interval: float = POLL_INTERVAL,
) -> DaemonStatus:
    """Start the daemon if not already running; wait until it answers health.

    Returns the resulting :class:`DaemonStatus`; ``running`` is always true on
    success. Raises :class:`DaemonStartError` if the spawned process never
    becomes healthy within ``ready_timeout``.
    """
    home = ensure_home(config)
    existing = probe_health(home)
    if existing is not None:
        return DaemonStatus(running=True, pid=read_pid(home), health=existing)

    # Clear any stale lock/endpoint from a previous crash before (re)starting.
    clear_pid(home)
    transport.cleanup(resolve_endpoint(home))

    pid = spawn_detached(home)
    write_pid(home, pid)

    deadline = time.monotonic() + ready_timeout
    while time.monotonic() < deadline:
        health = probe_health(home)
        if health is not None:
            return DaemonStatus(running=True, pid=pid, health=health)
        time.sleep(poll_interval)

    raise DaemonStartError("daemon did not become healthy within the timeout")


def stop_daemon(
    config: Config,
    *,
    timeout: float = STOP_TIMEOUT,
    poll_interval: float = POLL_INTERVAL,
) -> bool:
    """Stop a running daemon gracefully. Returns False if none was running.

    Sends the ``__shutdown__`` control message, waits for the endpoint to go
    quiet, then falls back to terminating the recorded PID. Always leaves the
    lock file and endpoint artifacts cleaned up (restart-safe).
    """
    home = config.home_path
    endpoint = resolve_endpoint(home)
    if not is_running(home):
        clear_pid(home)
        transport.cleanup(endpoint)
        return False

    _send_shutdown(home, timeout)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_running(home):
            break
        time.sleep(poll_interval)

    if is_running(home):  # graceful path timed out — force it
        pid = read_pid(home)
        if pid is not None:
            _terminate(pid)

    clear_pid(home)
    transport.cleanup(endpoint)
    return True


def status(config: Config) -> DaemonStatus:
    """Report current daemon liveness + health metrics."""
    home = config.home_path
    health = probe_health(home)
    return DaemonStatus(running=health is not None, pid=read_pid(home), health=health)


def _terminate(pid: int) -> None:
    """Best-effort process termination (cross-platform)."""
    try:
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        pass


# ─── Foreground runner (the detached process body) ───────────────────────────


def run_foreground(
    config: Config,
    backend: Backend | None = None,
    *,
    ingester_factory: IngesterFactory = default_ingester_factory,
) -> None:
    """Run the daemon in the foreground until shutdown (used by ``memrelay _serve``).

    Builds the real async :class:`~memrelay.engine.graphiti.MemoryEngine` (the E4
    backend) unless a ``backend`` is injected for tests, hosts the spool→engine
    ingester as a background task sharing that single engine, and closes the engine
    it built on the way out. An injected ``backend`` is used as-is (never rebuilt or
    closed); the ``ingester_factory`` seam lets tests host a fake ingester.

    Installs best-effort SIGTERM/SIGINT handlers for graceful stop where the
    platform supports them (POSIX); on Windows, ``memrelay stop`` drives shutdown
    over the socket instead.
    """
    ensure_home(config)
    endpoint = resolve_endpoint(config.home_path)
    runtime = DaemonRuntime(config, endpoint, backend=backend, ingester_factory=ingester_factory)

    async def _main() -> None:
        await runtime.start()
        loop = asyncio.get_running_loop()
        for signame in ("SIGTERM", "SIGINT"):
            sig = getattr(signal, signame, None)
            if sig is None:
                continue
            try:
                loop.add_signal_handler(sig, runtime.request_shutdown)
            except (NotImplementedError, RuntimeError):
                pass  # not supported on this platform/loop (e.g. Windows)
        await runtime.serve()

    asyncio.run(_main())
