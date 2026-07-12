"""Daemon query-API server: accept loop, dispatch, graceful shutdown (E6-S3/S4).

:class:`DaemonServer` wraps the :mod:`~memrelay.daemon.transport` listener and the
:func:`~memrelay.daemon.protocol.dispatch` router. It is deliberately usable two
ways:

* **In-process** (tests, and the injection seam): ``await server.start()`` →
  clients connect → ``await server.stop()``.
* **Foreground** (the detached ``memrelay _serve`` process): ``await server.run()``
  blocks until a graceful shutdown is requested — either the ``__shutdown__``
  control message over the socket (what ``memrelay stop`` sends) or
  :meth:`request_shutdown` (what a signal handler calls).

Graceful shutdown closes the listener and removes the on-disk socket / port file
so no orphaned endpoint or lock is left behind.
"""

from __future__ import annotations

import asyncio
import contextlib

from memrelay.daemon import transport
from memrelay.daemon.protocol import SHUTDOWN, Backend, dispatch, error_response
from memrelay.daemon.transport import Endpoint

#: How long one connection may sit without sending a complete request line before
#: the server reclaims it. Bounds the per-connection coroutine + read buffer so a
#: stalled (or hostile) client cannot pin daemon resources indefinitely.
IDLE_TIMEOUT = 30.0


class DaemonServer:
    """Serves the JSON query API for one :class:`Backend` over one endpoint."""

    def __init__(
        self,
        backend: Backend,
        endpoint: Endpoint,
        *,
        idle_timeout: float = IDLE_TIMEOUT,
        read_limit: int = transport.MAX_LINE_BYTES,
    ) -> None:
        self._backend = backend
        self._endpoint = endpoint
        self._idle_timeout = idle_timeout
        self._read_limit = read_limit
        self._server: asyncio.AbstractServer | None = None
        self._shutdown: asyncio.Event | None = None

    @property
    def endpoint(self) -> Endpoint:
        return self._endpoint

    async def start(self) -> None:
        """Begin listening. Idempotent within a single event loop."""
        if self._server is not None:
            return
        self._shutdown = asyncio.Event()
        self._server = await transport.serve(self._endpoint, self._handle, limit=self._read_limit)

    async def run(self) -> None:
        """Serve until a graceful shutdown is requested, then clean up."""
        await self.start()
        assert self._shutdown is not None
        try:
            await self._shutdown.wait()
        finally:
            await self.stop()

    def request_shutdown(self) -> None:
        """Ask :meth:`run` to stop (safe to call from a signal handler)."""
        if self._shutdown is not None:
            self._shutdown.set()

    async def stop(self) -> None:
        """Close the listener and remove endpoint artifacts. Idempotent."""
        server, self._server = self._server, None
        if server is not None:
            server.close()
            with contextlib.suppress(Exception):
                await server.wait_closed()
        transport.cleanup(self._endpoint)

    # ── connection handling ──────────────────────────────────────────────────

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Serve one client connection: framed request → response, until closed."""
        try:
            while True:
                try:
                    request = await asyncio.wait_for(
                        transport.read_message(reader), timeout=self._idle_timeout
                    )
                except TimeoutError:
                    break  # idle client — reclaim the coroutine + read buffer (Bug B)
                except transport.MessageTooLarge as exc:
                    # Precise handling (must precede the ValueError clause, since
                    # MessageTooLarge subclasses ValueError): a too-large frame
                    # leaves the stream mid-line, so report and reset the (short-
                    # lived) connection rather than mislabel it as bad JSON.
                    await transport.write_message(
                        writer, error_response("payload_too_large", str(exc))
                    )
                    break
                except ValueError as exc:
                    await transport.write_message(writer, error_response("bad_json", str(exc)))
                    continue
                if request is None:
                    break  # client hung up

                if request.get("method") == SHUTDOWN:
                    await transport.write_message(writer, {"status": "stopping"})
                    self.request_shutdown()
                    break

                response = await dispatch(self._backend, request)
                await transport.write_message(writer, response)
        except (ConnectionResetError, BrokenPipeError):
            pass  # client vanished mid-exchange — nothing to do
        finally:
            with contextlib.suppress(Exception):
                writer.close()
                await writer.wait_closed()
