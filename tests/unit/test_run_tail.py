"""Unit tests for :func:`memrelay.ingest.graphiti_sink.run_tail` — the #11 live tail.

Everything here is deterministic and wall-clock-free: the source is an injected
``FakeFileWatch`` (a scripted async-CM that yields one record per fixture line, each release
optionally gated by a test-controlled ``asyncio.Event``) and the spool is a duck-typed
``DedupeSpool`` that mirrors the real spool's ``idempotency_key`` UNIQUE (INSERT OR IGNORE).
No real watchdog observer is started and no real filesystem-event timing is relied upon.

The tail carries **no durable offset** — it is best-effort latency only; correctness (no
loss / no dup) is owned by the periodic ``run_observe`` replay backstop + the spool's
idempotency dedupe, so these tests inject no cursor.

Proofs (the founder-gated set):
* **Parity (AC4):** the tail path composes byte-identical episodes to the ``run_observe``
  replay path on the copilot fixture — same shared ``_push_line``/pipeline backbone.
* **Per-append loop-yield:** the tail suspends the loop between appends (it streams one
  record at a time and stays responsive), asserted via injected gating, not timing.
* **Stop mid-stream:** a stop signal breaks the read in a normal context, the trailing
  partial is flushed (no lost events), and the source ``__aexit__`` runs (clean teardown).
* **Tail + replay coexist exactly-once:** the same fixture through both paths yields one
  spool row per episode (idempotency dedupe).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from memrelay.ingest.graphiti_sink import run_observe, run_tail

SID = "fixture-session"


# ── injected seams (duck types; keep the tests independent of session B) ──────────────────
class DedupeSpool:
    """Duck type for the real ``Spool``: dedupe on ``idempotency_key`` (INSERT OR IGNORE)."""

    def __init__(self) -> None:
        self.records: list[dict] = []
        self.ignored = 0
        self._keys: set[str] = set()

    def append(self, record: dict) -> None:
        key = record["idempotency_key"]
        if key in self._keys:
            self.ignored += 1
            return
        self._keys.add(key)
        self.records.append(record)

    @property
    def keys(self) -> list[str]:
        return [r["idempotency_key"] for r in self.records]


def _fake_idem(session_id: str | None, event_id: str | None, content: str) -> str:
    return f"K|{session_id}|{event_id}|{content}"


def _fake_factory(**fields: object) -> dict:
    return dict(fields)


class FakeFileWatch:
    """Injected stand-in for traceforge ``FileWatchSource``: scripted, optionally gated.

    Async context manager + async iterator yielding one ``payload``-bearing record per line.
    When ``gates`` is given, record ``i`` is withheld until ``gates[i]`` is set — deterministic
    per-append scheduling with no wall clock and no real fs-event delivery. Tracks entry/exit
    (``__aexit__`` stands in for the real observer stop/join + file close) and how many records
    have been yielded so tests can assert append-by-append streaming.
    """

    def __init__(self, lines: list[str], *, gates: list[asyncio.Event] | None = None) -> None:
        self._lines = list(lines)
        self._gates = gates
        self.entered = False
        self.exited = False
        self.yielded = 0

    async def __aenter__(self) -> FakeFileWatch:
        self.entered = True
        return self

    async def __aexit__(self, *exc: object) -> bool:
        self.exited = True
        return False

    def __aiter__(self) -> Any:
        return self._gen()

    async def _gen(self) -> Any:
        for i, line in enumerate(self._lines):
            if self._gates is not None:
                await self._gates[i].wait()
            self.yielded += 1
            yield SimpleNamespace(payload=line)


def _fixture_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


async def _pump_until(pred: Any, cap: int = 2000) -> None:
    """Yield the loop until ``pred()`` holds (or a safety cap trips).

    Deterministic and wall-clock-free: ``asyncio.sleep(0)`` just reschedules, so this drains
    ready work without ever waiting on time. It returns the instant the condition is met, so
    a gated invariant (e.g. "record i+1 can't be consumed until gate[i+1] fires") is what
    actually bounds progress, not the pump count.
    """
    for _ in range(cap):
        if pred():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition not reached within pump cap")


def _replay(spool: Any, fixture: Path) -> Any:
    return asyncio.run(
        run_observe(
            fixture,
            SID,
            spool=spool,
            idempotency_fn=_fake_idem,
            record_factory=_fake_factory,
        )
    )


def _tail(spool: Any, fixture: Path, lines: list[str]) -> Any:
    return asyncio.run(
        run_tail(
            fixture,
            SID,
            spool=spool,
            tail_source=FakeFileWatch(lines),
            idempotency_fn=_fake_idem,
            record_factory=_fake_factory,
        )
    )


# ── AC4 parity ────────────────────────────────────────────────────────────────────────────
def test_run_tail_parity_with_run_observe(copilot_fixture: Path) -> None:
    """AC4: the tail composes the SAME episodes (content + keys) as the replay path."""
    lines = _fixture_lines(copilot_fixture)

    replay_spool = DedupeSpool()
    replay_result = _replay(replay_spool, copilot_fixture)

    tail_spool = DedupeSpool()
    tail_result = _tail(tail_spool, copilot_fixture, lines)

    # Same composed episodes, same order, same idempotency keys — byte-identical records.
    assert tail_result.appended == replay_result.appended == 3
    assert tail_spool.records == replay_spool.records
    assert tail_result.parsed == replay_result.parsed


# ── per-append loop-yield ──────────────────────────────────────────────────────────────────
def test_run_tail_yields_loop_between_appends(copilot_fixture: Path) -> None:
    """The tail streams one record at a time and yields the loop between appends.

    With every record gated, the tail cannot consume record i+1 until gate[i+1] fires — proof
    it suspends the loop per append (unlike a non-yielding whole-file read, which would drain
    all records at once). A concurrent probe advancing while the tail is parked shows the loop
    stays responsive. Fully deterministic: driven by ``asyncio.Event`` gates, never a sleep.
    """
    lines = _fixture_lines(copilot_fixture)
    gates = [asyncio.Event() for _ in lines]
    fw = FakeFileWatch(lines, gates=gates)

    async def scenario() -> Any:
        stop = asyncio.Event()
        ticks = 0

        async def probe() -> None:
            nonlocal ticks
            while not stop.is_set():
                ticks += 1
                await asyncio.sleep(0)

        probe_task = asyncio.create_task(probe())
        task = asyncio.create_task(
            run_tail(
                copilot_fixture,
                SID,
                spool=DedupeSpool(),
                tail_source=fw,
                stop=stop,
                idempotency_fn=_fake_idem,
                record_factory=_fake_factory,
            )
        )

        # No gate released → the tail parks on record 0; it consumes nothing.
        for _ in range(20):
            await asyncio.sleep(0)
        assert fw.yielded == 0
        assert not task.done()
        base = ticks

        # Release exactly one record. The tail consumes it, pushes it, then parks on record 1
        # — it does NOT drain the rest, no matter how long the loop runs.
        gates[0].set()
        await _pump_until(lambda: fw.yielded == 1)  # the released record was taken…
        assert not task.done()  # …and the tail suspended again, awaiting the next append
        # It stays parked: pumping the loop further cannot advance past the ungated record 1.
        for _ in range(50):
            await asyncio.sleep(0)
        assert fw.yielded == 1  # per-append: still exactly one record consumed
        assert not task.done()
        assert ticks > base  # loop stayed responsive while the tail was parked

        # Drain to completion.
        for gate in gates[1:]:
            gate.set()
        result = await task
        stop.set()
        await probe_task
        return result

    result = asyncio.run(scenario())
    assert result.appended == 3


# ── stop mid-stream drains + tears down the source ─────────────────────────────────────────
def test_run_tail_stop_midstream_drains_and_closes(copilot_fixture: Path) -> None:
    """A mid-stream stop breaks the read in a normal context, flushes the partial, and exits.

    The stop lands at the select ``await`` (never mid ``spool.append``), so the ``finally``
    flush drains the buffered partial (no lost trailing events) and the source ``__aexit__``
    runs (the real observer stop/join + file close) — nothing leaks.
    """
    lines = _fixture_lines(copilot_fixture)
    gates = [asyncio.Event() for _ in lines]
    fw = FakeFileWatch(lines, gates=gates)
    spool = DedupeSpool()

    async def scenario() -> Any:
        stop = asyncio.Event()
        task = asyncio.create_task(
            run_tail(
                copilot_fixture,
                SID,
                spool=spool,
                tail_source=fw,
                stop=stop,
                idempotency_fn=_fake_idem,
                record_factory=_fake_factory,
            )
        )
        # Release a partial prefix (an open work-unit), let it stream, then stop mid-stream.
        for gate in gates[:6]:
            gate.set()
        await _pump_until(lambda: fw.yielded == 6)
        assert not task.done()

        stop.set()
        result = await task  # completes cleanly — no hang, no cancellation escaping
        return result

    result = asyncio.run(scenario())
    assert fw.exited is True  # source torn down (observer stop/join + close)
    assert result.parsed >= 1  # the released events were processed
    # The buffered partial was flushed on stop → best-effort trailing drain, nothing lost.
    assert result.appended >= 1
    assert fw.yielded == 6  # stop halted the stream — the rest was never consumed


# ── tail + replay coexist exactly-once ─────────────────────────────────────────────────────
def test_tail_and_replay_coexist_exactly_once(copilot_fixture: Path) -> None:
    """The same fixture through BOTH paths → one spool row per episode (idempotency dedupe)."""
    lines = _fixture_lines(copilot_fixture)
    spool = DedupeSpool()

    _replay(spool, copilot_fixture)
    after_replay = len(spool.records)
    assert after_replay == 3

    # Tail the identical fixture into the SAME spool: every episode collides with a replay row.
    _tail(spool, copilot_fixture, lines)

    assert len(spool.records) == after_replay == 3  # no net new rows
    assert spool.ignored >= 3  # each tail episode was deduped against the replay
