"""Unit tests for the daemon's session discovery & multi-session management (E1-S4 #8).

Everything here is engine-free and deterministic: the poller is driven one ``poll_once``
tick at a time (or ``run`` with an injected wait) against a **fake** discovery source and
**fake** captures, so there is never a real 2s wall-clock sleep, no engine, and no network.
Only :class:`RunObserveCapture` touches the real (idempotent) ``run_observe``, and that is
exercised with ``run_observe`` monkeypatched to a recorder so the lifecycle — observe on a
cadence, a final drain on stop, and no leaked task — is asserted without the pipeline.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from pathlib import Path
from typing import Any

from memrelay.daemon.session_discovery import (
    RunObserveCapture,
    SessionDiscoveryPoller,
    active_sessions,
)
from memrelay.providers.base import SessionRef


def _ref(session_id: str, path: str | None = None) -> SessionRef:
    return SessionRef(session_id=session_id, agent_id="fake", path=path)


class _FakeCapture:
    """Records start/stop calls; stands in for a real per-session capture."""

    def __init__(self, ref: SessionRef) -> None:
        self.ref = ref
        self.starts = 0
        self.stops = 0

    def start(self) -> None:
        self.starts += 1

    async def stop(self) -> None:
        self.stops += 1


class _Factory:
    """A capture_factory that remembers every capture it builds, keyed by session id."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.made: dict[str, _FakeCapture] = {}

    def __call__(self, ref: SessionRef) -> _FakeCapture:
        self.calls.append(ref.session_id)
        capture = _FakeCapture(ref)
        self.made[ref.session_id] = capture
        return capture


class _StubProvider:
    """A provider exposing only ``discover_sessions`` (all :func:`active_sessions` needs)."""

    def __init__(self, refs: list[SessionRef]) -> None:
        self._refs = refs

    def discover_sessions(self) -> list[SessionRef]:
        return list(self._refs)


def test_new_active_session_starts_capture_and_counts() -> None:
    """A newly-active session gets a capture started and is counted."""

    async def scenario() -> tuple[_Factory, SessionDiscoveryPoller]:
        factory = _Factory()
        active = [_ref("s1")]
        poller = SessionDiscoveryPoller(discover=lambda: list(active), capture_factory=factory)
        await poller.poll_once()
        return factory, poller

    factory, poller = asyncio.run(scenario())
    assert factory.calls == ["s1"]
    assert factory.made["s1"].starts == 1
    assert poller.stats() == {"sessions_observed": 1, "active_sessions": 1}


def test_ended_session_is_stopped_cleanly() -> None:
    """When a session leaves the active set its capture is stopped exactly once."""

    async def scenario() -> tuple[_Factory, SessionDiscoveryPoller]:
        factory = _Factory()
        active = [_ref("s1")]
        poller = SessionDiscoveryPoller(discover=lambda: list(active), capture_factory=factory)
        await poller.poll_once()
        active.clear()  # s1 ends
        await poller.poll_once()
        return factory, poller

    factory, poller = asyncio.run(scenario())
    assert factory.made["s1"].stops == 1
    # sessions_observed is cumulative (a start counter); the live set is now empty.
    assert poller.stats() == {"sessions_observed": 1, "active_sessions": 0}


def test_already_captured_session_is_not_double_started() -> None:
    """Re-seeing an already-captured session is idempotent — no second capture."""

    async def scenario() -> tuple[_Factory, SessionDiscoveryPoller]:
        factory = _Factory()
        active = [_ref("s1")]
        poller = SessionDiscoveryPoller(discover=lambda: list(active), capture_factory=factory)
        await poller.poll_once()
        await poller.poll_once()  # identical active set
        return factory, poller

    factory, poller = asyncio.run(scenario())
    assert factory.calls == ["s1"]  # built once across both ticks
    assert factory.made["s1"].starts == 1
    assert poller.stats() == {"sessions_observed": 1, "active_sessions": 1}


def test_lru_bound_evicts_oldest_over_max_sessions() -> None:
    """The active-capture set is bounded by ``max_sessions``; the oldest is evicted."""

    async def scenario() -> tuple[_Factory, SessionDiscoveryPoller]:
        factory = _Factory()
        active = [_ref("s1"), _ref("s2"), _ref("s3")]
        poller = SessionDiscoveryPoller(
            discover=lambda: list(active), capture_factory=factory, max_sessions=2
        )
        await poller.poll_once()
        return factory, poller

    factory, poller = asyncio.run(scenario())
    # All three are seen/started, then the least-recently-active is stopped down to the cap.
    assert factory.calls == ["s1", "s2", "s3"]
    assert factory.made["s1"].stops == 1  # oldest → evicted + stopped
    assert factory.made["s2"].stops == 0
    assert factory.made["s3"].stops == 0
    assert poller.stats() == {"sessions_observed": 3, "active_sessions": 2}


def test_max_sessions_none_disables_the_bound() -> None:
    """``max_sessions=None`` tracks every active session (no eviction)."""

    async def scenario() -> tuple[_Factory, SessionDiscoveryPoller]:
        factory = _Factory()
        active = [_ref(f"s{i}") for i in range(5)]
        poller = SessionDiscoveryPoller(
            discover=lambda: list(active), capture_factory=factory, max_sessions=None
        )
        await poller.poll_once()
        return factory, poller

    factory, poller = asyncio.run(scenario())
    assert all(cap.stops == 0 for cap in factory.made.values())
    assert poller.stats() == {"sessions_observed": 5, "active_sessions": 5}


def test_run_loop_uses_injected_wait_and_never_sleeps() -> None:
    """``run`` polls via the injected wait (no wall clock) and tears down on ``stop``."""

    async def scenario() -> tuple[_Factory, SessionDiscoveryPoller, list[float]]:
        factory = _Factory()
        active = [_ref("s1")]
        waits: list[float] = []
        stop = asyncio.Event()

        async def fake_wait(interval: float, ev: asyncio.Event) -> None:
            waits.append(interval)
            if len(waits) >= 2:  # break the loop after two polls
                ev.set()

        poller = SessionDiscoveryPoller(
            discover=lambda: list(active),
            capture_factory=factory,
            poll_interval=2.0,
            wait=fake_wait,
        )
        # The timeout is a safety net: if the poller ever slept for real this would trip.
        await asyncio.wait_for(poller.run(stop), timeout=5.0)
        return factory, poller, waits

    factory, poller, waits = asyncio.run(scenario())
    assert waits == [2.0, 2.0]  # used the injected wait both times
    assert factory.made["s1"].starts == 1
    assert factory.made["s1"].stops == 1  # run()'s finally aclose stops it
    assert poller.stats()["active_sessions"] == 0


def test_aclose_stops_every_live_capture() -> None:
    """``aclose`` cleanly stops all tracked captures (no leaks)."""

    async def scenario() -> tuple[_Factory, SessionDiscoveryPoller]:
        factory = _Factory()
        active = [_ref("s1"), _ref("s2")]
        poller = SessionDiscoveryPoller(discover=lambda: list(active), capture_factory=factory)
        await poller.poll_once()
        await poller.aclose()
        return factory, poller

    factory, poller = asyncio.run(scenario())
    assert factory.made["s1"].stops == 1
    assert factory.made["s2"].stops == 1
    assert poller.stats()["active_sessions"] == 0


def test_discovery_failure_keeps_existing_captures() -> None:
    """A flaky discovery poll is swallowed; live captures are not torn down."""

    async def scenario() -> tuple[_Factory, SessionDiscoveryPoller]:
        factory = _Factory()
        state: dict[str, Any] = {"fail": False, "active": [_ref("s1")]}

        def discover() -> list[SessionRef]:
            if state["fail"]:
                raise RuntimeError("session store unavailable")
            return list(state["active"])

        poller = SessionDiscoveryPoller(discover=discover, capture_factory=factory)
        await poller.poll_once()  # starts s1
        state["fail"] = True
        await poller.poll_once()  # discovery raises → must swallow and keep s1
        return factory, poller

    factory, poller = asyncio.run(scenario())
    assert factory.made["s1"].starts == 1
    assert factory.made["s1"].stops == 0  # not stopped by a failed poll
    assert poller.stats()["active_sessions"] == 1


def test_active_sessions_filters_by_events_mtime(tmp_path: Path) -> None:
    """The production ``discover`` keeps only sessions whose trace is within the window."""
    fresh_dir = tmp_path / "fresh"
    stale_dir = tmp_path / "stale"
    fresh_dir.mkdir()
    stale_dir.mkdir()
    fresh_events = fresh_dir / "events.jsonl"
    stale_events = stale_dir / "events.jsonl"
    fresh_events.write_text("{}\n", encoding="utf-8")
    stale_events.write_text("{}\n", encoding="utf-8")
    # Backdate the stale trace far outside the freshness window.
    old = time.time() - 10_000
    os.utime(stale_events, (old, old))

    refs = [
        _ref("fresh", str(fresh_events)),
        _ref("stale", str(stale_events)),
        _ref("nopath", None),
        _ref("missing", str(tmp_path / "gone" / "events.jsonl")),
    ]
    got = active_sessions(_StubProvider(refs), now=time.time(), freshness_s=30.0)
    assert [r.session_id for r in got] == ["fresh"]


def test_run_observe_capture_observes_on_cadence_and_final_drains(monkeypatch: Any) -> None:
    """The live capture replays via ``run_observe`` on a cadence, drains once on stop, no leak."""
    import memrelay.ingest.graphiti_sink as graphiti_sink

    calls: list[str] = []

    async def fake_run_observe(path: Any, session_id: str, **kwargs: Any) -> None:
        # The capture offloads each pass onto a worker thread; a list append is safe there and
        # is published to the loop when the ``to_thread`` future resolves.
        calls.append(session_id)

    monkeypatch.setattr(graphiti_sink, "run_observe", fake_run_observe)

    async def scenario() -> tuple[list[str], asyncio.Task[None] | None, RunObserveCapture]:
        observed = asyncio.Event()

        async def signal_then_park(interval: float, stop: asyncio.Event) -> None:
            # Runs on the loop right after the (offloaded) observe returns: fire once so the
            # test resumes deterministically, then park until stop — exactly one loop pass.
            observed.set()
            await stop.wait()

        capture = RunObserveCapture(
            _ref("s1", "C:/nope/events.jsonl"),
            spool=object(),
            provider=object(),
            config=None,
            namespace_map=None,
            interval=2.0,
            wait=signal_then_park,
        )
        capture.start()
        task = capture._task
        await asyncio.wait_for(observed.wait(), timeout=5.0)  # one loop observe happened
        await capture.stop()  # sets stop, awaits the loop (no cancel), then final-drains
        return calls, task, capture

    seen, task, capture = asyncio.run(scenario())
    # One observe in the loop + one final drain on stop.
    assert seen == ["s1", "s1"]
    assert task is not None and task.done()  # loop task finished — nothing leaked
    assert capture._task is None  # handle released


def test_observe_runs_off_the_event_loop_thread(monkeypatch: Any) -> None:
    """Each observe pass executes on a worker thread, never inline on the daemon loop.

    ``run_observe`` is a synchronous full-file replay; awaiting it inline would block the
    daemon's event loop — starving the global ingester drain and the socket listener — for the
    whole pass. This proves the capture offloads it: every pass runs on a thread other than the
    loop's, so however long a replay takes it cannot stall the loop. A regression to an inline
    ``await run_observe(...)`` makes the observed thread id equal the loop's and trips this
    assertion cleanly (without hanging the suite).
    """
    import memrelay.ingest.graphiti_sink as graphiti_sink

    observe_tids: list[int] = []

    async def fake_run_observe(path: Any, session_id: str, **kwargs: Any) -> None:
        observe_tids.append(threading.get_ident())

    monkeypatch.setattr(graphiti_sink, "run_observe", fake_run_observe)

    async def scenario() -> tuple[int, list[int]]:
        observed = asyncio.Event()

        async def signal_then_park(interval: float, stop: asyncio.Event) -> None:
            observed.set()
            await stop.wait()

        capture = RunObserveCapture(
            _ref("s1", "C:/nope/events.jsonl"),
            spool=object(),
            provider=object(),
            config=None,
            namespace_map=None,
            wait=signal_then_park,
        )
        capture.start()
        await asyncio.wait_for(observed.wait(), timeout=5.0)
        await capture.stop()
        return threading.get_ident(), observe_tids

    loop_tid, tids = asyncio.run(scenario())
    assert tids, "run_observe never executed"
    # Every pass (loop pass + final drain) ran on a thread other than the daemon loop's.
    assert all(tid != loop_tid for tid in tids)
