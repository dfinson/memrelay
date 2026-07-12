"""Per-OS socket transport for the daemon query API (SPEC §2 "Process Communication").

The daemon and the MCP client must agree on *where* the daemon listens and *how*
to frame messages. Both concerns live here so there is exactly one place that
knows the platform rules.

Transport by platform (verified in ``docs/e6e7-skeleton-notes.md``):

* **POSIX (Linux/macOS — the CI path):** a Unix domain socket at
  ``~/.memrelay/daemon.sock`` via :func:`asyncio.start_unix_server`.
* **Windows:** asyncio has no Unix-socket support, so the daemon binds a
  ``127.0.0.1`` loopback port (``asyncio.start_server``) and records it in
  ``~/.memrelay/daemon.port`` for the client to read. Loopback-only.

Wire framing is newline-delimited JSON: one compact object per line.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: True on platforms without asyncio Unix-domain-socket support.
USE_LOOPBACK = sys.platform == "win32"

SOCKET_FILENAME = "daemon.sock"
PORT_FILENAME = "daemon.port"
LOOPBACK_HOST = "127.0.0.1"

#: Max framed-line size the daemon will buffer while reading one request. asyncio's
#: StreamReader defaults to 64 KiB (2**16), which silently caps a ``memory_note``
#: carrying a large diff/file; we raise it to a generous ceiling so legitimate notes
#: parse cleanly while an abusive/oversized frame is still bounded and reported.
MAX_LINE_BYTES = 4 * 1024 * 1024


class MessageTooLarge(ValueError):
    """A framed request line exceeded the transport read limit.

    Subclasses :class:`ValueError` so existing ``read_message`` consumers that
    broadly catch ``ValueError`` stay backward-compatible, while callers that want
    to answer distinctly (a ``payload_too_large`` envelope rather than
    ``bad_json``) can catch this precise type *first*.
    """


#: asyncio stream handler signature.
Handler = Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]]


@dataclass(frozen=True)
class Endpoint:
    """Resolved listen/connect address for one memrelay home directory."""

    home: Path
    use_loopback: bool = USE_LOOPBACK

    @property
    def socket_path(self) -> Path:
        """Unix-domain-socket path (POSIX transport)."""
        return self.home / SOCKET_FILENAME

    @property
    def port_path(self) -> Path:
        """File holding the bound loopback port (Windows transport)."""
        return self.home / PORT_FILENAME

    def describe(self) -> str:
        """Human-readable address for logs/errors."""
        return f"loopback:{self.port_path}" if self.use_loopback else str(self.socket_path)


def resolve_endpoint(home: str | os.PathLike[str]) -> Endpoint:
    """Resolve the daemon :class:`Endpoint` under a memrelay home directory."""
    return Endpoint(home=Path(home))


# ─── Framing ─────────────────────────────────────────────────────────────────


async def write_message(writer: asyncio.StreamWriter, obj: dict[str, Any]) -> None:
    """Encode ``obj`` as one compact JSON line and flush it."""
    writer.write((json.dumps(obj) + "\n").encode("utf-8"))
    await writer.drain()


async def read_message(reader: asyncio.StreamReader) -> dict[str, Any] | None:
    """Read one newline-delimited JSON object, or ``None`` at end of stream.

    Raises :class:`ValueError` on a malformed (non-JSON, or non-object) line so
    callers can answer with an error envelope rather than crash. A line longer
    than the reader's buffer limit raises :class:`MessageTooLarge` (a
    ``ValueError`` subclass) so callers can distinguish "too big" from "bad JSON".
    """
    try:
        line = await reader.readline()
    except ValueError as exc:
        # StreamReader.readline() collapses an asyncio.LimitOverrunError (a line
        # longer than the buffer limit) into a plain ValueError. Re-raise it as a
        # precise type so the server answers payload_too_large instead of
        # mislabeling an oversized-but-valid line as bad JSON.
        raise MessageTooLarge(str(exc)) from exc
    if not line:
        return None
    obj = json.loads(line.decode("utf-8"))
    if not isinstance(obj, dict):
        raise ValueError("request must be a JSON object")
    return obj


# ─── Listen / connect ────────────────────────────────────────────────────────


async def serve(
    endpoint: Endpoint, handler: Handler, *, limit: int = MAX_LINE_BYTES
) -> asyncio.AbstractServer:
    """Start a listener for ``endpoint`` and return the asyncio server.

    Callers are responsible for single-instance enforcement *before* calling
    this; a stale Unix socket file from a crashed daemon is removed first so the
    bind can succeed. On loopback, the chosen port is written to
    :attr:`Endpoint.port_path`. ``limit`` sets each connection's read-buffer
    ceiling (see :data:`MAX_LINE_BYTES`).
    """
    endpoint.home.mkdir(parents=True, exist_ok=True)

    if endpoint.use_loopback:
        server = await asyncio.start_server(handler, LOOPBACK_HOST, 0, limit=limit)
        port = server.sockets[0].getsockname()[1]
        _atomic_write(endpoint.port_path, str(port))
        return server

    # POSIX Unix domain socket. Remove a stale file so start_unix_server can bind.
    _unlink_quietly(endpoint.socket_path)
    server = await asyncio.start_unix_server(handler, path=str(endpoint.socket_path), limit=limit)
    try:
        os.chmod(endpoint.socket_path, 0o600)  # owner-only, defense in depth
    except OSError:
        pass
    return server


async def connect(
    endpoint: Endpoint, *, timeout: float
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Open a client connection to a running daemon, bounded by ``timeout``.

    Raises :class:`ConnectionError` if the daemon is not reachable (no socket /
    port file, refused connection, or the connect times out).
    """
    try:
        if endpoint.use_loopback:
            port = _read_port(endpoint.port_path)
            opener = asyncio.open_connection(LOOPBACK_HOST, port)
        else:
            if not endpoint.socket_path.exists():
                raise ConnectionError(f"daemon socket not found: {endpoint.socket_path}")
            opener = asyncio.open_unix_connection(str(endpoint.socket_path))
        return await asyncio.wait_for(opener, timeout=timeout)
    except (TimeoutError, OSError) as exc:
        raise ConnectionError(f"cannot reach daemon at {endpoint.describe()}: {exc}") from exc


def cleanup(endpoint: Endpoint) -> None:
    """Remove any endpoint artifacts left on disk (idempotent)."""
    _unlink_quietly(endpoint.socket_path)
    _unlink_quietly(endpoint.port_path)


# ─── helpers ─────────────────────────────────────────────────────────────────


def _read_port(port_path: Path) -> int:
    try:
        return int(port_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError) as exc:
        raise ConnectionError(f"daemon port file unusable: {port_path}: {exc}") from exc


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _unlink_quietly(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass
