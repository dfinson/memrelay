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
import logging
import os
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from memrelay import internal_sessions
from memrelay.daemon.session_discovery import (
    RunObserveCapture,
    SessionDiscoveryPoller,
    active_sessions,
    warn_on_self_observation,
)
from memrelay.providers.base import SessionRef


@pytest.fixture(autouse=True)
def _reset_internal_sessions() -> None:
    """Isolate the process-wide internal-session registry between tests."""
    internal_sessions.reset()
    yield
    internal_sessions.reset()


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


def test_active_sessions_excludes_internal_extraction_sessions(tmp_path: Path) -> None:
    """A session id registered by memrelay's own borrow-host extraction is never re-observed.

    This closes the self-observation loop (Part A): when the Copilot borrow-host path shells out
    to ``copilot --session-id <uuid> -p ...`` it registers ``<uuid>`` as internal. That id's
    ``events.jsonl`` is brand-new (well within the freshness window), so without the exclusion the
    poller would re-observe memrelay's own extraction call ~2s later and fan out exponentially.
    """
    internal_dir = tmp_path / "internal"
    external_dir = tmp_path / "external"
    internal_dir.mkdir()
    external_dir.mkdir()
    internal_events = internal_dir / "events.jsonl"
    external_events = external_dir / "events.jsonl"
    internal_events.write_text("{}\n", encoding="utf-8")
    external_events.write_text("{}\n", encoding="utf-8")

    # Both traces are fresh; only the internal one is memrelay's own extraction session.
    internal_sessions.register("extraction-uuid")

    refs = [
        _ref("extraction-uuid", str(internal_events)),
        _ref("external", str(external_events)),
    ]
    got = active_sessions(_StubProvider(refs), now=time.time(), freshness_s=30.0)

    # The registered extraction id is dropped even though its trace is fresh; the genuine
    # observed session is kept.
    assert [r.session_id for r in got] == ["external"]


def test_active_sessions_exclusion_is_a_noop_without_registration(tmp_path: Path) -> None:
    """The exclusion only touches registered ids, so ordinary sessions are unaffected."""
    events = tmp_path / "events.jsonl"
    events.write_text("{}\n", encoding="utf-8")
    refs = [_ref("ordinary", str(events))]

    got = active_sessions(_StubProvider(refs), now=time.time(), freshness_s=30.0)
    assert [r.session_id for r in got] == ["ordinary"]


def _cfg(strategy: str = "borrow-host", host: str = "copilot") -> Any:
    """A minimal stand-in exposing the two fields the guard reads (``config.llm.*``)."""
    return SimpleNamespace(llm=SimpleNamespace(strategy=strategy, host=host))


def test_warn_on_self_observation_fires_on_the_circular_copilot_config(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The guard warns exactly once when the observed provider == the borrowed host (Part B)."""
    with caplog.at_level(logging.WARNING, logger="memrelay.daemon.session_discovery"):
        warned = warn_on_self_observation("copilot", _cfg(strategy="borrow-host", host="copilot"))

    assert warned is True
    records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(records) == 1
    # Actionable: names the observed provider/host and the quota cost.
    assert "copilot" in records[0].getMessage().lower()


def test_warn_on_self_observation_silent_for_byo_key_and_local(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Non-borrow-host strategies never shell out to the observed agent → no warning."""
    with caplog.at_level(logging.WARNING, logger="memrelay.daemon.session_discovery"):
        byo = warn_on_self_observation("copilot", _cfg(strategy="byo-key", host="copilot"))
        local = warn_on_self_observation("copilot", _cfg(strategy="local", host="copilot"))

    assert byo is False
    assert local is False
    assert [r for r in caplog.records if r.levelno == logging.WARNING] == []


def test_warn_on_self_observation_silent_when_host_differs_from_provider(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Borrowing a *different* agent than the one observed is not a loop → no warning."""
    with caplog.at_level(logging.WARNING, logger="memrelay.daemon.session_discovery"):
        # Observed provider is copilot but the borrowed host is claude (writes to ~/.claude).
        other_host = warn_on_self_observation(
            "copilot", _cfg(strategy="borrow-host", host="claude")
        )
        # Observed provider is claude but the borrowed host is copilot.
        other_provider = warn_on_self_observation(
            "claude", _cfg(strategy="borrow-host", host="copilot")
        )

    assert other_host is False
    assert other_provider is False
    assert [r for r in caplog.records if r.levelno == logging.WARNING] == []


def test_run_observe_capture_observes_on_cadence_and_final_drains(monkeypatch: Any) -> None:
    """The live capture replays via ``run_observe`` on a cadence, drains once on stop, no leak."""
    import memrelay.ingest.graphiti_sink as graphiti_sink

    calls: list[str] = []
    observed = asyncio.Event()

    async def fake_run_observe(path: Any, session_id: str, **kwargs: Any) -> None:
        calls.append(session_id)
        observed.set()

    monkeypatch.setattr(graphiti_sink, "run_observe", fake_run_observe)

    async def scenario() -> tuple[list[str], asyncio.Task[None] | None, RunObserveCapture]:
        async def parking_wait(interval: float, stop: asyncio.Event) -> None:
            # Park the loop after its first observe until stop is set, so the cadence is
            # fully deterministic (exactly one loop pass before we stop it).
            await stop.wait()

        capture = RunObserveCapture(
            _ref("s1", "C:/nope/events.jsonl"),
            spool=object(),
            provider=object(),
            config=None,
            namespace_map=None,
            interval=2.0,
            wait=parking_wait,
        )
        capture.start()
        task = capture._task
        await asyncio.wait_for(observed.wait(), timeout=5.0)  # one loop observe happened
        await capture.stop()  # sets stop, cancels + awaits the loop, then final-drains
        return calls, task, capture

    seen, task, capture = asyncio.run(scenario())
    # One observe in the loop + one final drain on stop.
    assert seen == ["s1", "s1"]
    assert task is not None and task.done()  # loop task finished — nothing leaked
    assert capture._task is None  # handle released


def test_poller_restart_keeps_each_capture_terminal_drain(monkeypatch: Any) -> None:
    """A stopped session may restart, but each capture still issues one authoritative drain."""
    import memrelay.ingest.graphiti_sink as graphiti_sink

    calls: list[tuple[str, bool]] = []
    observed = asyncio.Event()

    async def fake_run_observe(path: Any, session_id: str, **kwargs: Any) -> None:
        del path
        calls.append((session_id, bool(kwargs["final"])))
        if not kwargs["final"]:
            observed.set()

    monkeypatch.setattr(graphiti_sink, "run_observe", fake_run_observe)

    async def scenario() -> None:
        active = [_ref("sentinel_00000000000000000000000000000001", "C:/synthetic/events.jsonl")]

        async def parking_wait(_interval: float, stop: asyncio.Event) -> None:
            await stop.wait()

        def capture_factory(ref: SessionRef) -> RunObserveCapture:
            return RunObserveCapture(
                ref,
                spool=object(),
                provider=object(),
                config=None,
                namespace_map=None,
                wait=parking_wait,
            )

        poller = SessionDiscoveryPoller(
            discover=lambda: list(active), capture_factory=capture_factory
        )
        await poller.poll_once()
        await asyncio.wait_for(observed.wait(), timeout=5.0)
        active.clear()
        await poller.poll_once()

        observed.clear()
        active.append(
            _ref("sentinel_00000000000000000000000000000001", "C:/synthetic/events.jsonl")
        )
        await poller.poll_once()
        await asyncio.wait_for(observed.wait(), timeout=5.0)
        await poller.aclose()

    asyncio.run(scenario())

    assert calls == [
        ("sentinel_00000000000000000000000000000001", False),
        ("sentinel_00000000000000000000000000000001", True),
        ("sentinel_00000000000000000000000000000001", False),
        ("sentinel_00000000000000000000000000000001", True),
    ]
