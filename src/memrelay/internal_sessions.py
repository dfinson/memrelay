"""Process-wide registry of memrelay's *own* borrow-host extraction session ids.

When the zero-key ``borrow-host`` strategy borrows the Copilot model, each
:meth:`~memrelay.engine.llm.borrow_host.CopilotHostProcess.complete` shells out to the real
``copilot`` CLI, and **every** ``copilot -p`` invocation creates a fresh
``~/.copilot/session-state/<uuid>/events.jsonl``. On a Copilot box the daemon's
:class:`~memrelay.daemon.session_discovery.SessionDiscoveryPoller` observes exactly that tree,
so without a mitigation each extraction call would be re-observed ~2s later and re-extracted —
an exponential self-observation feedback loop that burns Copilot quota and pollutes the graph
with memrelay's own extraction prompts.

This module is that mitigation's shared seam. The Copilot host path generates the extraction
session's id itself (``copilot --session-id <uuid> ...``) and :func:`register`\\s it here
*before* the CLI runs; the daemon's discovery predicate
(:func:`~memrelay.daemon.session_discovery.active_sessions`) then skips any id
:func:`is_internal` reports, so memrelay never observes a session it created.

Design:

* **A tiny leaf module.** It imports only the standard library (no memrelay ``engine`` /
  ``providers`` / ``daemon`` imports), so the engine-layer producer and the daemon-layer
  consumer can both import it with no risk of an import cycle.
* **In-memory, not persisted.** The producer (extraction, inline in the daemon process) and the
  consumer (the poller, same process) share this set within one daemon process, so a plain
  module-level set suffices. Persistence would buy nothing: a *prior* process's extraction
  ``events.jsonl`` are already older than ``ingest.session_freshness_s`` by the time a new
  daemon finishes its (slower) engine build and first poll, so they never re-qualify as
  "active" — there is no cross-restart id to remember. A :class:`threading.Lock` guards the set
  so registration and membership checks are safe even if a host ever drives extraction off the
  main loop thread.
"""

from __future__ import annotations

import threading

_lock = threading.Lock()
_internal_ids: set[str] = set()


def register(session_id: str) -> None:
    """Record ``session_id`` as one of memrelay's own extraction sessions.

    Idempotent. Callers MUST register *before* the session's ``events.jsonl`` can exist (the
    Copilot host path registers before spawning ``copilot``), so the poller can never observe
    the file in the window before the id is known.
    """
    with _lock:
        _internal_ids.add(session_id)


def is_internal(session_id: str) -> bool:
    """Return ``True`` iff ``session_id`` was created by memrelay's own extraction."""
    with _lock:
        return session_id in _internal_ids


def snapshot() -> frozenset[str]:
    """Return an immutable copy of the currently-registered ids (for tests/diagnostics)."""
    with _lock:
        return frozenset(_internal_ids)


def reset() -> None:
    """Clear the registry. Test-only seam; never call from production code."""
    with _lock:
        _internal_ids.clear()
