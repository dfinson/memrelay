"""Unit tests for the spool's below-cursor history reclamation (E3 #112).

Covers :meth:`~memrelay.ingest.spool.Spool.retained_bytes` and the atomic
:meth:`~memrelay.ingest.spool.Spool.reclaim` that bounds the already-ingested history
``spool.db`` would otherwise grow forever. The properties that make reclamation safe are the
same ones #33's compaction guarantees for the *unprocessed* backlog, applied to the
*below-cursor* half: an oldest-first byte-budget prune, an inability to touch an un-ingested
(``seq > cursor``) row, transactional rollback on failure, durability across reopen, a cursor
that never moves, and preserved exactly-once drain — even when the row *at* the cursor is pruned.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from memrelay.ingest.episode import EpisodeRecord
from memrelay.ingest.spool import Spool


def _record(content: str, key: str, *, namespace: str = "proj-a") -> dict:
    return EpisodeRecord.new(content, namespace, repo="o/r", idempotency_key=key).to_dict()


def _db(tmp_path: Path) -> Path:
    return tmp_path / "spool" / "spool.db"


def _row_count(spool: Spool) -> int:
    """Total rows on disk (white-box: below-cursor rows are invisible to read_batch)."""
    return int(spool._conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0])


def _cursor(spool: Spool) -> int:
    return int(spool._conn.execute("SELECT seq FROM cursor WHERE id = 1").fetchone()[0])


def _below_cursor_seqs(spool: Spool) -> list[int]:
    cur = _cursor(spool)
    rows = spool._conn.execute(
        "SELECT seq FROM episodes WHERE seq <= ? ORDER BY seq", (cur,)
    ).fetchall()
    return [int(r[0]) for r in rows]


class _FailOnDelete:
    """A connection proxy that raises on the reclaim DELETE, to force a rollback path.

    Forwards every attribute (``commit`` / ``rollback`` / ``close`` / ...) to the real
    connection; only ``execute`` of a ``DELETE`` is intercepted, so :meth:`Spool.reclaim`'s
    byte-gate ``SELECT`` still runs and the failure lands exactly on the prune.
    """

    def __init__(self, real: sqlite3.Connection) -> None:
        self._real = real

    def execute(self, sql: str, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        if "DELETE" in sql:
            raise sqlite3.OperationalError("injected reclaim failure")
        return self._real.execute(sql, *args, **kwargs)

    def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
        return getattr(self._real, name)


# ─── retained_bytes: the below-cursor mirror of pending_bytes ────────────────


def test_retained_bytes_zero_when_empty(tmp_path: Path) -> None:
    spool = Spool(_db(tmp_path))
    assert spool.retained_bytes() == 0, "no rows → no retained history"
    spool.close()


def test_retained_bytes_counts_only_below_cursor(tmp_path: Path) -> None:
    spool = Spool(_db(tmp_path))
    for i in range(6):
        spool.append(_record("x" * 500, f"k{i}"))
    assert spool.retained_bytes() == 0, "nothing checkpointed yet → no history"
    spool.checkpoint(4)  # seq 1..4 become ingested history
    retained = spool.retained_bytes()
    assert retained > 0
    # It is exactly the complement of the backlog: history + backlog == whole table.
    whole = int(
        spool._conn.execute(
            "SELECT COALESCE(SUM(LENGTH(CAST(record AS BLOB))"
            " + LENGTH(CAST(idempotency_key AS BLOB))), 0) FROM episodes"
        ).fetchone()[0]
    )
    assert retained + spool.pending_bytes() == whole
    spool.close()


# ─── reclaim: oldest-first byte-budget prune ─────────────────────────────────


def test_reclaim_prunes_oldest_keeping_newest_within_budget(tmp_path: Path) -> None:
    spool = Spool(_db(tmp_path))
    for i in range(20):
        spool.append(_record("x" * 500, f"k{i}"))
    spool.checkpoint(20)  # all 20 rows are now below-cursor history
    full = spool.retained_bytes()
    budget = full // 2

    pruned = spool.reclaim(budget)

    assert pruned > 0
    assert spool.retained_bytes() <= budget, "history driven under the byte budget"
    # The survivors are the newest contiguous suffix; the oldest rows were dropped.
    survivors = _below_cursor_seqs(spool)
    assert survivors, "some recent history is kept"
    assert 20 in survivors, "the newest row survives"
    assert 1 not in survivors, "the oldest row is reclaimed"
    assert survivors == list(range(survivors[0], 21)), "kept rows are the newest suffix"
    assert pruned == 20 - len(survivors)
    spool.close()


def test_reclaim_never_deletes_uningested_rows(tmp_path: Path) -> None:
    """The ``seq <= cursor`` guard: an un-ingested (past-cursor) row is never removed."""
    spool = Spool(_db(tmp_path))
    for i in range(10):
        spool.append(_record("x" * 500, f"k{i}"))
    spool.checkpoint(5)  # seq 1..5 history, seq 6..10 still un-ingested
    backlog_before = spool.pending_bytes()

    pruned = spool.reclaim(1)  # a 1-byte budget wants to drop *all* history

    assert pruned == 5, "only the five below-cursor rows can go"
    assert _below_cursor_seqs(spool) == [], "all reclaimable history was pruned"
    # The un-ingested backlog is completely untouched: count, bytes, order, drain.
    assert spool.pending() == 5
    assert spool.pending_bytes() == backlog_before
    assert [seq for seq, _ in spool.read_batch()] == [6, 7, 8, 9, 10]
    assert _cursor(spool) == 5
    spool.close()


def test_reclaim_can_prune_the_row_at_the_cursor(tmp_path: Path) -> None:
    """A budget below a single row's size drops history entirely, cursor row included."""
    spool = Spool(_db(tmp_path))
    for i in range(5):
        spool.append(_record("x" * 500, f"k{i}"))
    spool.checkpoint(3)  # cursor == 3; the row *at* seq 3 is below-or-equal the cursor

    pruned = spool.reclaim(1)

    assert pruned == 3
    # The cursor value is a high-water mark, not a row reference — it is unmoved and drain
    # of the un-ingested tail is intact even though the row at seq==cursor was deleted.
    assert _cursor(spool) == 3
    assert [seq for seq, _ in spool.read_batch()] == [4, 5]
    spool.close()


def test_reclaim_disabled_is_a_noop(tmp_path: Path) -> None:
    spool = Spool(_db(tmp_path))
    for i in range(4):
        spool.append(_record("x" * 500, f"k{i}"))
    spool.checkpoint(4)
    before = _row_count(spool)

    assert spool.reclaim(0) == 0, "0 == disabled → keep all history"
    assert spool.reclaim(-100) == 0, "negative budget is also disabled"
    assert _row_count(spool) == before, "no history removed when reclamation is disabled"
    spool.close()


def test_reclaim_under_budget_is_a_noop(tmp_path: Path) -> None:
    spool = Spool(_db(tmp_path))
    for i in range(4):
        spool.append(_record("x" * 500, f"k{i}"))
    spool.checkpoint(4)
    full = spool.retained_bytes()
    before = _row_count(spool)

    assert spool.reclaim(full + 10_000) == 0, "already under budget → nothing to prune"
    assert _row_count(spool) == before
    assert spool.retained_bytes() == full
    spool.close()


def test_reclaim_empty_spool_is_a_noop(tmp_path: Path) -> None:
    spool = Spool(_db(tmp_path))
    assert spool.reclaim(1000) == 0
    spool.close()


# ─── crash-safety: atomicity, cursor invariance, durability, exactly-once ────


def test_reclaim_is_atomic_on_failure(tmp_path: Path) -> None:
    """A failing prune rolls back and re-raises — history and cursor survive intact."""
    spool = Spool(_db(tmp_path))
    for i in range(6):
        spool.append(_record("x" * 500, f"k{i}"))
    spool.checkpoint(6)
    before_rows = _row_count(spool)
    before_retained = spool.retained_bytes()

    real = spool._conn
    spool._conn = _FailOnDelete(real)  # type: ignore[assignment]
    try:
        with pytest.raises(sqlite3.OperationalError):
            spool.reclaim(1)  # over budget → attempts the DELETE, which is intercepted
    finally:
        spool._conn = real

    assert _row_count(spool) == before_rows, "no rows deleted when the prune rolled back"
    assert spool.retained_bytes() == before_retained
    assert _cursor(spool) == 6, "the durable cursor is untouched by a failed reclaim"
    spool.close()


def test_reclaim_never_moves_the_cursor(tmp_path: Path) -> None:
    spool = Spool(_db(tmp_path))
    for i in range(8):
        spool.append(_record("x" * 500, f"k{i}"))
    spool.checkpoint(6)
    assert _cursor(spool) == 6

    spool.reclaim(1)  # prune all below-cursor history
    assert _cursor(spool) == 6, "reclamation must never advance or rewind the durable cursor"
    spool.close()


def test_reclaim_survives_restart(tmp_path: Path) -> None:
    """After commit, a fresh Spool over the same file sees the pruned history."""
    db = _db(tmp_path)
    spool = Spool(db)
    for i in range(20):
        spool.append(_record("x" * 500, f"k{i}"))
    spool.checkpoint(20)
    budget = spool.retained_bytes() // 4
    pruned = spool.reclaim(budget)
    survivors = _below_cursor_seqs(spool)
    spool.close()  # simulate a crash/restart after the reclaim committed

    reopened = Spool(db)
    assert _below_cursor_seqs(reopened) == survivors, "the prune is durable across reopen"
    assert reopened.retained_bytes() <= budget
    assert pruned == 20 - len(survivors)
    reopened.close()


def test_exactly_once_drain_preserved_across_reclaim_and_reopen(tmp_path: Path) -> None:
    """Reclaiming ingested history never redelivers a pruned row nor loses an un-ingested one."""
    db = _db(tmp_path)
    spool = Spool(db)
    for i in range(10):
        spool.append(_record(f"fact-{i}", f"k{i}"))

    # Drain-checkpoint the first six exactly once (as the ingester would).
    seen: list[str] = []
    for seq, rec in spool.read_batch(6):
        seen.append(rec["content"])
        spool.checkpoint(seq)
    assert _cursor(spool) == 6

    # Aggressively reclaim: prune *all* below-cursor history, including the row at the cursor.
    assert spool.reclaim(1) == 6
    assert _below_cursor_seqs(spool) == []

    # Crash/restart, then drain the remainder.
    spool.close()
    reopened = Spool(db)
    while True:
        batch = reopened.read_batch()
        if not batch:
            break
        for seq, rec in batch:
            seen.append(rec["content"])
            reopened.checkpoint(seq)

    # Every episode delivered exactly once, in order; no pruned row was ever redelivered.
    assert seen == [f"fact-{i}" for i in range(10)]
    assert reopened.pending() == 0
    assert reopened.read_batch() == []
    assert _cursor(reopened) == 10, "cursor advanced monotonically to the last row"
    reopened.close()
