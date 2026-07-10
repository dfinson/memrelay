"""Unit tests for :class:`memrelay.daemon.session_discovery.LiveTailCapture` (#11 lifecycle).

``LiveTailCapture`` composes #8's unchanged ``RunObserveCapture`` (the lossless replay
backstop) with a long-lived best-effort tail. These tests pin the *lifecycle* the founder
gated (Tests D + E), deterministically and with no wall clock:

* **Test D — stop drains + tears down.** ``start`` launches the replay backstop then the tail;
  ``stop`` signals the tail's select-based (un-cancelled) drain and awaits it — the tail's
  ``finally`` runs the source ``__aexit__`` (the real observer stop/join + file close, proxied
  here by the injected fake's ``exited`` flag), the trailing ``session.ended`` summary is
  flushed (nothing lost), and the retained replay backstop is stopped too. No task or handle
  is left behind.
* **Test E — LRU eviction tears down the evicted tail.** Driving the poller past
  ``max_sessions`` evicts the least-recently-active capture via ``capture.stop()``, tearing
  down its tail (task cleared, source ``__aexit__`` run) — no leaked task/observer/handle.

The tail source is an injected fake that replays the fixture lines then parks (mimicking a
real ``start_at="beginning"`` tail that has drained 0→EOF and now awaits appends); the replay
backstop is a recording stub. No real watchdog observer runs, so the whole module is a fast,
deterministic unit test.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from memrelay.daemon.session_discovery import LiveTailCapture, SessionDiscoveryPoller
from memrelay.providers.base import SessionRef

SID = "fixture-session"


def _fixture_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


async def _pump_until(pred: Any, cap: int = 2000) -> None:
    """Yield the loop until ``pred()`` holds (deterministic; ``sleep(0)`` never waits on time)."""
    for _ in range(cap):
        if pred():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition not reached within pump cap")


class RecordingSpool:
    """Duck-typed spool: keep every appended record so a test can assert the drain landed."""

    def __init__(self) -> None:
        self.records: list[dict] = []

    def append(self, record: Any) -> bool:
        self.records.append(record)
        return True


class LiveFakeWatch:
    """Injected ``FileWatchSource`` stand-in: replay ``lines``, then park like a live tail.

    After yielding the history it awaits a never-set event, mimicking a real
    ``start_at="beginning"`` tail that has drained 0→EOF and now waits for appends. Tracks
    ``exited`` so a test can prove the source ``__aexit__`` (observer stop/join + close) ran.
    """

    def __init__(self, lines: list[str]) -> None:
        self._lines = list(lines)
        self.entered = False
        self.exited = False
        self.parked = False

    async def __aenter__(self) -> LiveFakeWatch:
        self.entered = True
        return self

    async def __aexit__(self, *exc: object) -> bool:
        self.exited = True
        return False

    def __aiter__(self) -> Any:
        return self._gen()

    async def _gen(self) -> Any:
        for line in self._lines:
            yield SimpleNamespace(payload=line)
        # History drained; now tail: park until cancelled (no EOF exit), like the real source.
        self.parked = True
        await asyncio.Event().wait()


class FakeReplay:
    """Recording stand-in for the retained #8 ``RunObserveCapture`` backstop."""

    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True


def _capture(
    ref: SessionRef, spool: Any, src: LiveFakeWatch, replay: FakeReplay
) -> LiveTailCapture:
    return LiveTailCapture(
        ref,
        spool=spool,
        provider=None,  # → _prepare_observe defaults to the copilot provider
        config=None,  # → _prepare_observe defaults to load_config()
        tail_source_factory=lambda _r: src,
        replay_capture=replay,
    )


# ── Test D: stop drains the tail + tears everything down ────────────────────────────────────
def test_live_tail_capture_stop_drains_and_tears_down(copilot_fixture: Path) -> None:
    lines = _fixture_lines(copilot_fixture)
    spool = RecordingSpool()
    src = LiveFakeWatch(lines)
    replay = FakeReplay()
    ref = SessionRef(session_id=SID, agent_id="copilot", path=str(copilot_fixture))
    cap = _capture(ref, spool, src, replay)

    async def scenario() -> asyncio.Task:
        cap.start()
        assert replay.started  # the retained backstop is launched first (unchanged #8)
        # The tail drains the fixture history, then parks (a live tail awaiting appends).
        await _pump_until(lambda: src.parked)
        tail_task = cap._tail_task
        assert tail_task is not None and not tail_task.done()  # live, not leaked-and-gone
        await cap.stop()
        return tail_task

    tail_task = asyncio.run(scenario())
    # Clean teardown: the tail finished via the normal select-based stop (NOT cancellation),
    # so no half-written episode and no leaked task.
    assert tail_task.done() and not tail_task.cancelled()
    assert src.exited is True  # source __aexit__ ran: observer stop/join + file close
    assert replay.stopped is True  # the backstop's authoritative final drain ran too
    assert cap._tail_task is None  # no task handle left behind
    # Final drain: the trailing ``session.ended`` summary reached the spool — nothing lost.
    assert any("Session summary" in r["content"] for r in spool.records)
    assert len(spool.records) == 3


# ── Test E: LRU eviction stops the evicted session's live tail (no leak) ─────────────────────
def test_lru_eviction_tears_down_live_tail(copilot_fixture: Path) -> None:
    lines = _fixture_lines(copilot_fixture)
    made: list[tuple[str, LiveTailCapture, LiveFakeWatch]] = []

    def factory(ref: SessionRef) -> LiveTailCapture:
        src = LiveFakeWatch(lines)
        cap = _capture(ref, RecordingSpool(), src, FakeReplay())
        made.append((ref.session_id, cap, src))
        return cap

    active: dict[str, SessionRef] = {}

    def discover() -> list[SessionRef]:
        return list(active.values())

    # max_sessions=1 forces an eviction the moment a second session is active.
    poller = SessionDiscoveryPoller(discover=discover, capture_factory=factory, max_sessions=1)

    async def scenario() -> None:
        active["a"] = SessionRef(session_id="a", agent_id="copilot", path=str(copilot_fixture))
        await poller.poll_once()  # start capture "a"
        assert "a" in poller._captures

        # A second active session over the cap evicts the least-recently-active ("a").
        active["b"] = SessionRef(session_id="b", agent_id="copilot", path=str(copilot_fixture))
        await poller.poll_once()  # start "b", evict "a"

        # Eviction (not aclose) tore down "a"'s tail specifically.
        assert "a" not in poller._captures
        _sid, a_cap, a_src = made[0]
        assert a_cap._tail_task is None  # task cleared on stop
        assert a_src.exited is True  # source closed: observer stop/join + file close

        # aclose stops the survivor too — the poller ends holding nothing.
        await poller.aclose()
        assert not poller._captures

    asyncio.run(scenario())
    # Every capture ever made was torn down cleanly (task cleared + source exited) — no leak.
    assert len(made) == 2
    for _sid, cap, src in made:
        assert cap._tail_task is None
        assert src.exited is True
