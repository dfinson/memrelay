"""Unit tests for the internal-extraction-session registry (borrow-host loop fix, Part A).

The registry is the shared seam that lets the Copilot borrow-host producer tell the daemon's
discovery consumer "this session id is mine — do not observe it." These tests pin its small,
thread-safe contract (register / is_internal / snapshot / reset) in isolation, so the exclusion
in :func:`memrelay.daemon.session_discovery.active_sessions` and the argv threading in
:class:`memrelay.engine.llm.borrow_host.CopilotHostProcess` can rely on it deterministically.
"""

from __future__ import annotations

import threading

import pytest

from memrelay import internal_sessions


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    """The registry is process-wide module state; isolate every test from the others."""
    internal_sessions.reset()
    yield
    internal_sessions.reset()


def test_unregistered_id_is_not_internal() -> None:
    assert internal_sessions.is_internal("never-seen") is False
    assert internal_sessions.snapshot() == frozenset()


def test_register_marks_id_internal() -> None:
    internal_sessions.register("abc")

    assert internal_sessions.is_internal("abc") is True
    assert internal_sessions.is_internal("other") is False
    assert internal_sessions.snapshot() == frozenset({"abc"})


def test_register_is_idempotent() -> None:
    internal_sessions.register("dupe")
    internal_sessions.register("dupe")

    assert internal_sessions.snapshot() == frozenset({"dupe"})


def test_register_accumulates_distinct_ids() -> None:
    internal_sessions.register("one")
    internal_sessions.register("two")

    assert internal_sessions.snapshot() == frozenset({"one", "two"})
    assert internal_sessions.is_internal("one")
    assert internal_sessions.is_internal("two")


def test_snapshot_is_an_immutable_copy() -> None:
    internal_sessions.register("first")
    snap = internal_sessions.snapshot()

    # Mutating the registry after the fact must not change an earlier snapshot.
    internal_sessions.register("second")
    assert snap == frozenset({"first"})
    assert isinstance(snap, frozenset)


def test_reset_clears_all_ids() -> None:
    internal_sessions.register("gone")
    assert internal_sessions.is_internal("gone")

    internal_sessions.reset()

    assert internal_sessions.is_internal("gone") is False
    assert internal_sessions.snapshot() == frozenset()


def test_registration_is_thread_safe() -> None:
    """Concurrent registrations from many threads never lose an id (the lock holds)."""
    ids = [f"sid-{i}" for i in range(200)]

    barrier = threading.Barrier(len(ids))

    def _register(session_id: str) -> None:
        barrier.wait()
        internal_sessions.register(session_id)

    threads = [threading.Thread(target=_register, args=(sid,)) for sid in ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert internal_sessions.snapshot() == frozenset(ids)
