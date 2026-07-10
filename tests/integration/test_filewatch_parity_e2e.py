"""End-to-end: the #11 live tail through a **real** traceforge ``FileWatchSource``.

Two founder-gated proofs, both deterministic (the loop is pumped until an episode count is
reached — never a wall-clock sleep — and the tail is stopped via an injected event):

* **AC4 parity (e2e).** Feeding the copilot fixture through ``run_tail`` over a REAL
  ``FileWatchSource`` (``start_at="beginning"`` drains history 0→EOF on the loop) composes the
  SAME episodes — identical ``idempotency_key``s — as the ``run_observe`` replay path.
* **Test F — real thread-bridge, on the loop (the anti-#8-offload proof).** A real foreign
  ``threading.Thread`` fires the watchdog handler's ``on_modified`` over a freshly appended
  line, exercising the genuine ``loop.call_soon_threadsafe(changed.set)`` bridge. We then
  assert every spool write ran on the asyncio LOOP thread, never the foreign thread — exactly
  the boundary the rejected #8 offload violated. The final ``session.ended`` line is delivered
  ONLY via that foreign-thread signal, so its episode's append is a direct on-loop proof.

Engine-free: a duck-typed recording spool is the whole downstream. The shared conftests are
off-limits to this lane, so the fixture-line helper is local (mirrors the sibling e2e tests).
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any

from traceforge.sources import FileWatchSource
from watchdog.events import FileModifiedEvent

from memrelay.ingest.graphiti_sink import run_observe, run_tail

SID = "fixture-session"


def _fixture_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


async def _pump_until(pred: Any, cap: int = 5000) -> None:
    """Yield the loop until ``pred()`` holds (deterministic; ``sleep(0)`` never waits on time)."""
    for _ in range(cap):
        if pred():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition not reached within pump cap")


class RecordingSpool:
    """Duck-typed spool that records each append **and the thread it ran on**."""

    def __init__(self) -> None:
        self.records: list[dict] = []
        self.append_threads: set[int] = set()

    def append(self, record: Any) -> bool:
        self.append_threads.add(threading.get_ident())
        self.records.append(record)
        return True


def _write_lines(path: Path, lines: list[str]) -> None:
    # Newline-TERMINATE every line (incl. the last): FileWatchSource holds back a trailing
    # partial line that lacks a newline, so an unterminated last line would never be yielded.
    path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")


def _append_lines(path: Path, lines: list[str]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        for line in lines:
            handle.write(f"{line}\n")


def _replay_keys(fixture: Path) -> set[str]:
    spool = RecordingSpool()
    asyncio.run(run_observe(fixture, SID, spool=spool))
    return {record["idempotency_key"] for record in spool.records}


def _first_workunit_boundary(lines: list[str], scratch: Path, whole_keys: set[str]) -> int:
    """Smallest prefix length that ends exactly at a COMPLETE work-unit boundary.

    "Complete" = the prefix's replay episodes are all WHOLE work-units — their keys are a
    non-empty subset of the full-file replay keys — NOT a truncated partial-WU segment that the
    trailing flush emits under a *novel* key. (Empirically the fixture's trailing flush over a
    mid-work-unit prefix yields exactly such a novel partial key, distinct from the complete-WU
    key: precisely the truncation hazard ``start_at="beginning"`` avoids.) This boundary is where
    the live tail flushes the work-unit DURING iteration (the open-work-unit buffer empties), so a
    ``start_at="beginning"`` tail over this prefix composes that WU whole, == replay.

    Computed from the fixture (not hard-coded) so the guard stays correct if the fixture changes.
    """
    probe_dir = scratch / "_probe"
    probe_dir.mkdir(exist_ok=True)
    probe = probe_dir / "events.jsonl"
    for count in range(1, len(lines) + 1):
        _write_lines(probe, lines[:count])
        spool = RecordingSpool()
        asyncio.run(run_observe(probe, SID, spool=spool))
        keys = {record["idempotency_key"] for record in spool.records}
        if keys and keys <= whole_keys:
            return count
    raise AssertionError("no complete work-unit boundary found in fixture")


# ── AC4 parity through a real FileWatchSource ───────────────────────────────────────────────
def test_filewatch_tail_parity_e2e(copilot_fixture: Path, tmp_path: Path) -> None:
    """AC4: the tail over a REAL FileWatchSource composes the SAME episodes as the replay."""
    lines = _fixture_lines(copilot_fixture)
    events = tmp_path / "events.jsonl"
    _write_lines(events, lines)

    ref_keys = _replay_keys(copilot_fixture)
    assert len(ref_keys) == 3

    spool = RecordingSpool()

    async def scenario() -> None:
        stop = asyncio.Event()
        src = FileWatchSource(str(events), "copilot", start_at="beginning")
        task = asyncio.create_task(run_tail(events, SID, spool=spool, tail_source=src, stop=stop))
        # start_at="beginning" drains history 0→EOF on the loop; wait for all three episodes.
        await _pump_until(lambda: len(spool.records) >= 3)
        stop.set()
        await task  # clean stop: select-based drain + observer stop/join + file close

    asyncio.run(scenario())
    assert {record["idempotency_key"] for record in spool.records} == ref_keys


# ── Test F: the real watchdog→loop thread bridge, proven on the loop thread ──────────────────
def test_filewatch_real_thread_bridge_runs_on_loop(copilot_fixture: Path, tmp_path: Path) -> None:
    """A real foreign thread fires ``on_modified``; every spool write runs on the LOOP thread."""
    lines = _fixture_lines(copilot_fixture)
    assert len(lines) == 14  # 13 history lines + the session.shutdown we bridge in last

    ref_keys = _replay_keys(copilot_fixture)
    assert len(ref_keys) == 3

    events = tmp_path / "events.jsonl"
    _write_lines(events, lines[:13])  # history up to (not incl.) session.shutdown

    spool = RecordingSpool()
    foreign_ident: dict[str, int] = {}

    async def scenario() -> None:
        loop_ident = threading.get_ident()
        stop = asyncio.Event()
        src = FileWatchSource(str(events), "copilot", start_at="beginning")
        task = asyncio.create_task(run_tail(events, SID, spool=spool, tail_source=src, stop=stop))
        # Source entered (handler exists) and the 13-line history drained → 2 work-unit
        # episodes. The summary is NOT here yet (it needs session.shutdown, line 14).
        await _pump_until(lambda: src._handler is not None and len(spool.records) >= 2)

        # Append the final line, then deliver it ONLY via a real foreign-thread fs event —
        # the genuine watchdog→loop bridge (call_soon_threadsafe), not the async iterator.
        _append_lines(events, [lines[13]])

        def fire() -> None:
            foreign_ident["value"] = threading.get_ident()
            # The handler's on_modified runs on THIS foreign thread; its only cross-thread act
            # is loop.call_soon_threadsafe(changed.set) — it reads no file, writes no spool.
            src._handler.on_modified(FileModifiedEvent(str(src.path)))

        thread = threading.Thread(target=fire)
        thread.start()
        thread.join()

        # The loop wakes, reads the appended line, and composes the summary — ON THE LOOP.
        await _pump_until(lambda: len(spool.records) >= 3)
        stop.set()
        await task

        # Sanity: the signal really did originate on a different (foreign) thread.
        assert foreign_ident["value"] != loop_ident

    asyncio.run(scenario())
    # The crux: every spool write happened on the asyncio loop thread (== this main thread,
    # since asyncio.run hosts the loop here) — never the foreign/watchdog thread.
    assert spool.append_threads == {threading.get_ident()}
    # …and the bridge delivered the real, correct episode set (parity through the bridge).
    assert {record["idempotency_key"] for record in spool.records} == ref_keys


# ── B2 mid-session guard (Amendment 1): start_at="beginning" composes whole pre-existing WUs ─
def test_filewatch_beginning_composes_preexisting_whole_workunit(
    copilot_fixture: Path, tmp_path: Path
) -> None:
    """A tail that starts AFTER the file already holds a complete work-unit composes it WHOLE.

    The founder-gated guard for ``start_at``: with ``start_at="beginning"`` the tail buffers the
    pre-existing work-unit from offset 0 and composes it whole → the SAME ``idempotency_key`` as
    replay, no truncated/novel-key segment. A ``start_at="end"`` tail would begin at EOF, read
    nothing from the static prefix, and miss the WU entirely (zero keys) — so the equality below
    would fail. This is the test that "would have caught start_at=end".
    """
    lines = _fixture_lines(copilot_fixture)

    # The full-file replay keys are all WHOLE work-units; the guard's boundary must land on one
    # of them (never a truncated partial-WU key).
    whole_keys = _replay_keys(copilot_fixture)
    assert len(whole_keys) == 3

    # The earliest prefix that already contains exactly one COMPLETE work-unit (computed, not
    # hard-coded) — i.e. the tail is "started AFTER the fixture already contains >=1 complete WU".
    boundary = _first_workunit_boundary(lines, tmp_path, whole_keys)
    prefix = lines[:boundary]

    events = tmp_path / "events.jsonl"
    _write_lines(events, prefix)
    ref_wu_keys = _replay_keys(events)  # replay over the identical pre-existing prefix
    assert ref_wu_keys and ref_wu_keys <= whole_keys  # a whole WU, not a truncated partial
    assert len(ref_wu_keys) == 1  # exactly one complete work-unit is present

    spool = RecordingSpool()

    async def scenario() -> None:
        stop = asyncio.Event()
        src = FileWatchSource(str(events), "copilot", start_at="beginning")
        task = asyncio.create_task(run_tail(events, SID, spool=spool, tail_source=src, stop=stop))
        # The pre-existing complete WU drains 0→EOF on the loop; wait for its single episode.
        await _pump_until(lambda: len(spool.records) >= 1)
        stop.set()
        await task

    asyncio.run(scenario())
    # WHOLE-work-unit segment id, identical to replay — never a truncated subset key.
    assert {record["idempotency_key"] for record in spool.records} == ref_wu_keys
