"""Unit tests for the crash-safe SQLite spool (E3-S1 #29, E3-S2 #30, E3-S3 #31)."""

from __future__ import annotations

from pathlib import Path

from memrelay.ingest.episode import EpisodeRecord
from memrelay.ingest.spool import Spool


def _record(content: str, key: str) -> dict:
    return EpisodeRecord.new(content, "proj-a", repo="memrelay", idempotency_key=key).to_dict()


def _db(tmp_path: Path) -> Path:
    return tmp_path / "spool" / "spool.db"


def test_append_increments_pending(tmp_path: Path) -> None:
    spool = Spool(_db(tmp_path))
    assert spool.pending() == 0
    spool.append(_record("a", "k1"))
    spool.append(_record("b", "k2"))
    assert spool.pending() == 2
    spool.close()


def test_creates_parent_directory(tmp_path: Path) -> None:
    db = _db(tmp_path)
    assert not db.parent.exists()
    Spool(db).close()
    assert db.exists()


def test_read_batch_is_ordered_and_limited(tmp_path: Path) -> None:
    spool = Spool(_db(tmp_path))
    for i in range(5):
        spool.append(_record(f"fact-{i}", f"k{i}"))

    batch = spool.read_batch(3)
    assert [seq for seq, _ in batch] == [1, 2, 3], "must be seq-ordered and limited"
    assert [rec["content"] for _, rec in batch] == ["fact-0", "fact-1", "fact-2"]
    spool.close()


def test_checkpoint_advances_the_cursor(tmp_path: Path) -> None:
    spool = Spool(_db(tmp_path))
    for i in range(3):
        spool.append(_record(f"fact-{i}", f"k{i}"))

    spool.checkpoint(2)
    assert spool.pending() == 1
    remaining = spool.read_batch()
    assert [seq for seq, _ in remaining] == [3]
    spool.close()


def test_duplicate_append_is_a_noop(tmp_path: Path) -> None:
    spool = Spool(_db(tmp_path))
    spool.append(_record("original", "dup-key"))
    spool.append(_record("different body, same key", "dup-key"))
    assert spool.pending() == 1, "same idempotency_key must not add a second row"

    batch = spool.read_batch()
    assert batch[0][1]["content"] == "original", "the first write wins; the dup is ignored"
    spool.close()


def test_checkpoint_is_monotonic(tmp_path: Path) -> None:
    spool = Spool(_db(tmp_path))
    for i in range(3):
        spool.append(_record(f"fact-{i}", f"k{i}"))

    spool.checkpoint(2)
    spool.checkpoint(1)  # stale/backwards -> must be ignored
    assert spool.pending() == 1
    assert [seq for seq, _ in spool.read_batch()] == [3]
    spool.close()


def test_cursor_survives_restart(tmp_path: Path) -> None:
    """The crash-safety guarantee: a checkpointed cursor persists across reopen."""
    db = _db(tmp_path)
    spool = Spool(db)
    for i in range(3):
        spool.append(_record(f"fact-{i}", f"k{i}"))
    spool.checkpoint(2)
    spool.close()  # simulate a crash: drop the process/connection entirely

    reopened = Spool(db)  # a fresh Spool over the same file
    assert reopened.pending() == 1, "cursor must survive the restart"
    remaining = reopened.read_batch()
    assert [seq for seq, _ in remaining] == [3], "already-consumed rows must not redeliver"
    assert remaining[0][1]["content"] == "fact-2"

    reopened.checkpoint(3)
    assert reopened.pending() == 0
    assert reopened.read_batch() == []
    reopened.close()


def test_appended_rows_survive_restart(tmp_path: Path) -> None:
    db = _db(tmp_path)
    spool = Spool(db)
    spool.append(_record("durable", "k1"))
    spool.close()

    reopened = Spool(db)
    assert reopened.pending() == 1
    assert reopened.read_batch()[0][1]["content"] == "durable"
    reopened.close()


def test_context_manager_closes(tmp_path: Path) -> None:
    with Spool(_db(tmp_path)) as spool:
        spool.append(_record("x", "k1"))
        assert spool.pending() == 1
