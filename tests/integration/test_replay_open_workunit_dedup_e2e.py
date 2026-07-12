"""End-to-end regression for rt-ingest F1: the default replay path must not spool
partial-prefix snapshots of a still-open work-unit.

The daemon's default intake (``intake_source=replay``, :class:`RunObserveCapture`) re-replays
the whole, still-growing ``events.jsonl`` every poll. Before the fix, each pass ended with a
``pipeline.flush()`` that drained the currently-OPEN trailing work-unit into a spool row keyed
by ``_segment_id`` of the wire-ids buffered *so far* — a different partial-prefix key on every
poll as the work-unit accrued content::

    poll N:   buffer [α]        -> key hash(α)      -> episode P1
    poll N+1: buffer [α, β]     -> key hash(α, β)   -> episode P2
    poll N+2: turn.ended closes -> key hash(α, β)   -> episode C   (== P2 here)

P1/P2 carry distinct keys, so the spool's ``INSERT OR IGNORE`` cannot dedupe them against the
complete episode: one work-unit floods the graph with overlapping partial snapshots. The fix
defers the open trailing work-unit on intermediate replay passes (``final=False``); it is
captured exactly once — when a real boundary closes it (flushed by ``on_event`` with the stable
complete key) or when the terminal teardown pass drains it (``final=True``).

These tests drive the real :func:`run_observe` / :class:`RunObserveCapture` code path. The
downstream is a duck-typed :class:`DedupSpool` that mirrors the real spool's key-idempotent
``INSERT OR IGNORE`` exactly, so a partial-prefix flush under a novel key surfaces here as an
extra row — the F1 duplicate. The engine and shared conftests stay out of the loop; only the
committed ``copilot_fixture`` (read-only) is reused.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from memrelay.daemon.session_discovery import RunObserveCapture
from memrelay.ingest.graphiti_sink import run_observe
from memrelay.providers.base import SessionRef

SID = "fixture-session"


class DedupSpool:
    """Duck-typed spool mirroring session B's ``INSERT OR IGNORE`` on ``idempotency_key``.

    ``append`` is idempotent: a record whose key was already stored is ignored and returns
    ``False`` (no new row), exactly like the real spool. ``rows`` therefore holds one entry per
    DISTINCT key — the count the graph would actually receive — so a partial-prefix flush under
    a novel key shows up as an extra row.
    """

    def __init__(self) -> None:
        self.rows: list[Any] = []
        self._seen: set[str] = set()
        self.append_calls = 0

    def append(self, record: Any) -> bool:
        self.append_calls += 1
        key = record["idempotency_key"]
        if key in self._seen:
            return False
        self._seen.add(key)
        self.rows.append(record)
        return True

    @property
    def keys(self) -> set[str]:
        return {record["idempotency_key"] for record in self.rows}


def _write(path: Path, lines: list[str]) -> None:
    path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")


def _observe(path: Path, spool: DedupSpool, *, final: bool) -> None:
    asyncio.run(run_observe(path, SID, spool=spool, final=final))


def _replay_keys(path: Path) -> set[str]:
    """Full-file replay (``final=True``) → the set of COMPLETE work-unit keys."""
    spool = DedupSpool()
    _observe(path, spool, final=True)
    return spool.keys


# ── synthetic copilot wire events: one work-unit that accrues THREE distinct content ids ──────
def _ev(kind: str, seq: int, **data: Any) -> str:
    return json.dumps(
        {
            "type": kind,
            "data": data,
            "id": f"00000000-0000-4000-8000-{seq:012d}",
            "timestamp": "2026-06-29T17:47:20.282Z",
            "parentId": None,
        }
    )


def _synthetic_lines() -> list[str]:
    """A single work-unit that buffers 3 distinct user-message ids before a ``turn.ended`` close.

    Growing prefixes model a live session polled while this work-unit is still open::

        [:2] user α       -> partial key P1
        [:3] user α, β    -> partial key P2   (≠ P1)
        [:4] user α, β, γ -> partial key P3   (≠ P2)
        [:5] +turn.ended  -> COMPLETE key C   (== P3; the non-content boundary adds no id)

    Under the bug, draining every poll spools P1, P2, P3 — three overlapping snapshots of the
    ONE work-unit. The fix defers the open partial so only C is ever spooled.
    """
    return [
        _ev(
            "session.start",
            1,
            sessionId="00000000-0000-4000-8000-000000000000",
            version=1,
            context={"cwd": "[redacted]"},
        ),
        _ev("user.message", 2, content="alpha one"),
        _ev("user.message", 3, content="beta two"),
        _ev("user.message", 4, content="gamma three"),
        _ev("assistant.turn_end", 5, turnId="00000000-0000-4000-8000-000000000099"),
    ]


# ── the fix: intermediate replay passes defer the open work-unit ──────────────────────────────
def test_growing_open_workunit_defers_until_boundary_close(tmp_path: Path) -> None:
    """Two ``final=False`` passes over a growing OPEN work-unit spool nothing; the boundary
    close then yields exactly ONE episode — the complete key, no partial-prefix duplicates."""
    lines = _synthetic_lines()
    events = tmp_path / "events.jsonl"

    # The full (closed) work-unit → the single complete key we expect exactly once.
    _write(events, lines)
    complete = _replay_keys(events)
    assert len(complete) == 1

    spool = DedupSpool()

    # Intermediate replay passes while the work-unit is OPEN (grows α→β→γ). Each longer prefix
    # would, under the bug, flush a distinct partial-prefix key; deferred, they spool nothing.
    _write(events, lines[:3])  # α, β  (open)
    _observe(events, spool, final=False)
    _write(events, lines[:4])  # α, β, γ  (open, longer prefix)
    _observe(events, spool, final=False)
    assert spool.rows == []  # ← the fix: no partial-prefix snapshots while open

    # The boundary arrives; a normal (final=False) poll now sees turn.ended and flushes the
    # WHOLE work-unit via on_event — exactly one row, the complete key.
    _write(events, lines[:5])
    _observe(events, spool, final=False)
    assert spool.keys == complete
    assert len(spool.rows) == 1


# ── documents the bug the fix removes ─────────────────────────────────────────────────────────
def test_draining_every_pass_reproduces_partial_prefix_flood(tmp_path: Path) -> None:
    """Pin the pre-fix hazard: draining the open work-unit on every poll (``final=True``) spools
    a distinct partial-prefix snapshot per growth step — the flood ``final=False`` eliminates."""
    lines = _synthetic_lines()
    events = tmp_path / "events.jsonl"

    _write(events, lines)
    complete = _replay_keys(events)
    assert len(complete) == 1
    (complete_key,) = complete

    flood = DedupSpool()
    for cut in (2, 3, 4, 5):  # α ; α,β ; α,β,γ ; +turn.ended
        _write(events, lines[:cut])
        _observe(events, flood, final=True)

    # THREE rows for ONE work-unit: two novel partial-prefix snapshots (α ; α,β) PLUS the
    # complete key — none of the partials dedupe against the complete.
    assert len(flood.rows) == 3
    assert complete_key in flood.keys
    partials = flood.keys - complete
    assert len(partials) == 2  # the spurious overlapping snapshots F1 describes


# ── the terminal drain still captures a genuinely-open trailing work-unit ─────────────────────
def test_terminal_final_pass_drains_deferred_open_partial(tmp_path: Path) -> None:
    """At true end-of-session the still-open trailing work-unit is drained exactly once
    (``final=True``), and re-draining is idempotent — deferral never loses the trailing WU."""
    lines = _synthetic_lines()
    events = tmp_path / "events.jsonl"
    _write(events, lines[:4])  # α, β, γ — open, no boundary yet

    spool = DedupSpool()
    _observe(events, spool, final=False)  # cadence poll: deferred
    assert spool.rows == []

    _observe(events, spool, final=True)  # terminal teardown: drain the open partial once
    assert len(spool.rows) == 1

    _observe(events, spool, final=True)  # idempotent: same key, no new row
    assert len(spool.rows) == 1


# ── the real copilot adapter path exhibits — and the fix cures — F1 ───────────────────────────
def test_copilot_fixture_open_workunit_yields_single_episode(
    copilot_fixture: Path, tmp_path: Path
) -> None:
    """Over the committed copilot fixture, an open content-bearing work-unit (the user.message,
    before its tool.call.completed close) is deferred across polls and captured once at close."""
    lines = copilot_fixture.read_text(encoding="utf-8").splitlines()
    events = tmp_path / "events.jsonl"

    whole = _replay_keys(copilot_fixture)
    assert len(whole) == 3

    # WU1 closes on tool.execution_complete (fixture line 11). Its single complete key:
    _write(events, lines[:11])
    wu1 = _replay_keys(events)
    assert len(wu1) == 1 and wu1 <= whole
    (wu1_key,) = wu1

    # Sanity: draining an OPEN prefix (the pre-fix final=True) yields a partial key distinct
    # from every complete key — i.e. the fixture really exhibits F1 on its default path.
    probe = DedupSpool()
    _write(events, lines[:5])
    _observe(events, probe, final=True)
    assert probe.keys and probe.keys.isdisjoint(whole)  # a novel partial-prefix snapshot

    # The fix: two deferred polls while WU1 is open spool nothing…
    spool = DedupSpool()
    _write(events, lines[:5])
    _observe(events, spool, final=False)
    _write(events, lines[:8])
    _observe(events, spool, final=False)
    assert spool.rows == []
    # …then the poll that sees the tool.call.completed boundary flushes WU1 exactly once.
    _write(events, lines[:11])
    _observe(events, spool, final=False)
    assert spool.keys == {wu1_key}
    assert len(spool.rows) == 1


# ── the actual daemon loop: cadence polls defer, boundary poll flushes, stop() is idempotent ──
def test_run_observe_capture_defers_open_partial_across_polls(tmp_path: Path) -> None:
    """Drive the REAL RunObserveCapture over a growing file: cadence polls over the open
    work-unit defer (spool nothing), the poll that sees the boundary flushes exactly one
    complete episode, and the terminal stop() drain is idempotent — no partial-prefix dupes."""
    lines = _synthetic_lines()
    events = tmp_path / "events.jsonl"
    _write(events, lines[:3])  # α, β — open

    full = tmp_path / "full.jsonl"
    _write(full, lines)
    (complete_key,) = _replay_keys(full)

    spool = DedupSpool()
    snapshots: list[int] = []
    state = {"n": 0}
    done = asyncio.Event()

    async def grow_wait(interval: float, stop: asyncio.Event) -> None:
        # Injected cadence wait: record the rows visible AFTER poll n's observe, then grow the
        # file. No asserts here — an AssertionError would be swallowed by the loop's guard, so
        # the checks live in the test body against ``snapshots``.
        state["n"] += 1
        snapshots.append(len(spool.rows))
        if state["n"] == 1:
            _write(events, lines[:4])  # grow: +γ, still open
        elif state["n"] == 2:
            _write(events, lines[:5])  # grow: +turn.ended → WU closes
        elif state["n"] == 3:
            stop.set()
            done.set()
        else:
            await stop.wait()

    async def scenario() -> None:
        cap = RunObserveCapture(
            SessionRef(session_id=SID, agent_id="copilot", path=str(events)),
            spool=spool,
            provider=None,  # run_observe builds the real copilot provider
            config=None,  # …and loads the default config
            namespace_map=None,
            interval=2.0,
            wait=grow_wait,
        )
        cap.start()
        await asyncio.wait_for(done.wait(), timeout=10.0)
        await cap.stop()  # terminal final=True drain (idempotent here)

    asyncio.run(scenario())

    # poll 1 (open α,β) → 0 ; poll 2 (open α,β,γ) → 0 ; poll 3 (sees turn.ended) → 1 complete.
    assert snapshots == [0, 0, 1]
    assert spool.keys == {complete_key}
    assert len(spool.rows) == 1  # stop()'s drain added no partial-prefix duplicate
