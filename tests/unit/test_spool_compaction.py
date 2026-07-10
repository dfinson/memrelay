"""Unit tests for the spool's compaction primitives (E3-S4 #33).

Covers :meth:`~memrelay.ingest.spool.Spool.pending_bytes` and the atomic
:meth:`~memrelay.ingest.spool.Spool.replace` that backs "summarize-in-place", with the
crash-safety properties that make compaction safe: transactional rollback on failure,
durability across reopen, a cursor that never moves, an inability to touch already-
checkpointed history, and preserved exactly-once drain semantics.
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


def test_pending_bytes_is_zero_when_empty_and_grows_with_backlog(tmp_path: Path) -> None:
    spool = Spool(_db(tmp_path))
    assert spool.pending_bytes() == 0, "no unprocessed rows → no backlog footprint"
    for i in range(50):
        spool.append(_record("x" * 500, f"k{i}"))
    assert spool.pending_bytes() > 0
    spool.close()


def test_pending_bytes_excludes_checkpointed_rows(tmp_path: Path) -> None:
    spool = Spool(_db(tmp_path))
    for i in range(4):
        spool.append(_record("x" * 500, f"k{i}"))
    full = spool.pending_bytes()
    spool.checkpoint(2)  # rows 1,2 become ingested history, out of the backlog
    assert spool.pending_bytes() < full, "checkpointed rows leave the backpressure backlog"
    spool.close()


def test_replace_swaps_oldest_for_summary(tmp_path: Path) -> None:
    spool = Spool(_db(tmp_path))
    for i in range(5):
        spool.append(_record(f"fact-{i}", f"k{i}"))

    summary = _record("summary of 0..2", "summary-key")
    spool.replace([1, 2, 3], [summary])

    remaining = spool.read_batch()
    contents = [rec["content"] for _, rec in remaining]
    assert contents == ["fact-3", "fact-4", "summary of 0..2"]
    # The summary landed at a fresh tail seq; the compacted originals are gone.
    assert [seq for seq, _ in remaining] == [4, 5, 6]
    spool.close()


def test_replace_reclaims_backlog_bytes(tmp_path: Path) -> None:
    spool = Spool(_db(tmp_path))
    seqs = []
    for i in range(200):
        spool.append(_record("y" * 400, f"k{i}"))
        seqs.append(i + 1)
    before = spool.pending_bytes()

    # Fold the oldest 200 large rows into one small summary.
    spool.replace(seqs, [_record("compacted", "summary-key")])

    after = spool.pending_bytes()
    assert after < before, "compaction must reduce the unprocessed backlog footprint"
    assert spool.pending() == 1
    spool.close()


def test_replace_is_atomic_on_failure(tmp_path: Path) -> None:
    """A failing insert rolls the whole replace back — originals survive intact."""
    spool = Spool(_db(tmp_path))
    for i in range(3):
        spool.append(_record(f"fact-{i}", f"k{i}"))
    before_rows = _row_count(spool)

    # Two summaries sharing one idempotency_key: the second INSERT violates UNIQUE, so the
    # transaction (including the DELETEs already issued) must roll back.
    dup_a = _record("summary a", "dup-key")
    dup_b = _record("summary b", "dup-key")
    with pytest.raises(sqlite3.IntegrityError):
        spool.replace([1, 2, 3], [dup_a, dup_b])

    assert _row_count(spool) == before_rows, "no rows deleted when the replace rolled back"
    assert [rec["content"] for _, rec in spool.read_batch()] == ["fact-0", "fact-1", "fact-2"]
    assert spool.pending() == 3
    spool.close()


def test_replace_never_moves_the_cursor(tmp_path: Path) -> None:
    spool = Spool(_db(tmp_path))
    for i in range(4):
        spool.append(_record(f"fact-{i}", f"k{i}"))
    spool.checkpoint(1)
    assert _cursor(spool) == 1

    spool.replace([2, 3], [_record("summary", "summary-key")])
    assert _cursor(spool) == 1, "compaction must never advance or rewind the durable cursor"
    spool.close()


def test_replace_cannot_delete_checkpointed_history(tmp_path: Path) -> None:
    """The ``seq > cursor`` guard: already-ingested rows are never removed, even if asked."""
    spool = Spool(_db(tmp_path))
    for i in range(4):
        spool.append(_record(f"fact-{i}", f"k{i}"))
    spool.checkpoint(2)  # seq 1,2 are now durable history (below the cursor)

    # Ask to delete 1..3; only seq 3 is past the cursor and may go.
    spool.replace([1, 2, 3], [_record("summary", "summary-key")])

    # Rows 1 and 2 must still be on disk (2 kept + seq 4 + the new summary = 4 rows).
    assert _row_count(spool) == 4
    # From the reader's view: only seq 4 and the summary remain pending.
    assert [rec["content"] for _, rec in spool.read_batch()] == ["fact-3", "summary"]
    spool.close()


def test_replace_survives_restart(tmp_path: Path) -> None:
    """After commit, a fresh Spool over the same file sees summaries, not originals."""
    db = _db(tmp_path)
    spool = Spool(db)
    for i in range(3):
        spool.append(_record(f"fact-{i}", f"k{i}"))
    spool.replace([1, 2, 3], [_record("durable summary", "summary-key")])
    spool.close()  # simulate a crash/restart after the compaction committed

    reopened = Spool(db)
    remaining = reopened.read_batch()
    assert [rec["content"] for _, rec in remaining] == ["durable summary"]
    assert reopened.pending() == 1
    reopened.close()


def test_exactly_once_preserved_after_compaction(tmp_path: Path) -> None:
    spool = Spool(_db(tmp_path))
    for i in range(5):
        spool.append(_record(f"fact-{i}", f"k{i}"))
    spool.replace([1, 2, 3], [_record("summary", "summary-key")])

    # Drain everything, checkpointing each surviving row exactly once.
    seen = []
    while True:
        batch = spool.read_batch()
        if not batch:
            break
        for seq, rec in batch:
            seen.append(rec["content"])
            spool.checkpoint(seq)
    assert seen == ["fact-3", "fact-4", "summary"]
    assert spool.pending() == 0
    assert spool.read_batch() == [], "no compacted original is ever redelivered"
    spool.close()


def test_replace_empty_is_a_noop(tmp_path: Path) -> None:
    spool = Spool(_db(tmp_path))
    spool.append(_record("only", "k0"))
    spool.replace([], [])
    assert spool.pending() == 1
    spool.close()
