"""A crash-safe, idempotent durable spool over SQLite (E3-S1 #29, E3-S2 #30, E3-S3 #31).

The spool is the durable buffer between the observation/producer side and the
Graphiti :class:`~memrelay.engine.graphiti.MemoryEngine`. It is an **append-only**
log of episode rows plus a single **durable cursor** marking how far a reader has
consumed. That split is what makes ingest crash-safe: episodes are committed the
moment they are appended, and the cursor only advances once a reader has durably
handled a row — so a crash at any point resumes exactly where it left off, never
losing an episode and never re-delivering one that was already checkpointed.

Design (validated against SQLite 3.49 — see the PR body for the delta writeup):

* ``episodes(seq INTEGER PRIMARY KEY, idempotency_key TEXT UNIQUE, record TEXT)`` —
  ``seq`` is the SQLite rowid; because the table is append-only (rows are never
  deleted) it is strictly monotonic, so it doubles as the ordering/cursor key.
* ``cursor(id=1, seq)`` — one row holding the last checkpointed ``seq``.
* ``PRAGMA journal_mode=WAL`` + ``synchronous=NORMAL`` — durable across process /
  OS crash (only a hard power-loss window remains, acceptable for an ingest spool)
  while keeping the one-writer/one-reader path cheap.
* ``INSERT OR IGNORE`` on the unique ``idempotency_key`` makes :meth:`append`
  idempotent: re-appending an already-seen episode is a silent no-op.

Concurrency: the connection is opened with ``check_same_thread=False`` and every
operation is guarded by a lock, so a single writer (``append``) and a single
reader (``read_batch`` / ``checkpoint``) may live on different threads (e.g. the
daemon's note handler vs. the ingester task) without corrupting the DB.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

from memrelay.ingest.episode import from_row, to_row

_CURSOR_ID = 1


class Spool:
    """An append-only, idempotent, crash-safe episode queue backed by SQLite.

    Args:
        db_path: file to open (created along with parent dirs if absent). The
            canonical location is ``cfg.home_path / "spool" / "spool.db"``; tests
            point it at a ``tmp_path``.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS episodes ("
                " seq INTEGER PRIMARY KEY,"
                " idempotency_key TEXT UNIQUE NOT NULL,"
                " record TEXT NOT NULL)"
            )
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS cursor ("
                " id INTEGER PRIMARY KEY CHECK (id = 1),"
                " seq INTEGER NOT NULL)"
            )
            # Seed the cursor at 0 exactly once; harmless on every later open.
            self._conn.execute(
                "INSERT OR IGNORE INTO cursor (id, seq) VALUES (?, 0)", (_CURSOR_ID,)
            )
            self._conn.commit()

    def append(self, record: dict[str, Any]) -> None:
        """Durably append an episode; a duplicate ``idempotency_key`` is ignored.

        Idempotent by construction: re-appending the same logical episode (same
        ``idempotency_key``) inserts nothing and does not raise, so producers may
        retry freely after a crash without creating duplicates.
        """
        key = record["idempotency_key"]
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO episodes (idempotency_key, record) VALUES (?, ?)",
                (key, to_row(record)),
            )
            self._conn.commit()

    def read_batch(self, max_n: int = 100) -> list[tuple[int, dict[str, Any]]]:
        """Return up to ``max_n`` ``(seq, record)`` pairs past the durable cursor.

        Rows come back in ascending ``seq`` order and stop at the current
        checkpoint, so a reader can process them and :meth:`checkpoint` each ``seq``
        in turn. Nothing is consumed until :meth:`checkpoint` is called — a crash
        mid-batch simply re-reads the un-checkpointed tail.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT seq, record FROM episodes"
                " WHERE seq > (SELECT seq FROM cursor WHERE id = ?)"
                " ORDER BY seq ASC LIMIT ?",
                (_CURSOR_ID, max_n),
            ).fetchall()
        return [(int(seq), from_row(blob)) for seq, blob in rows]

    def checkpoint(self, seq: int) -> None:
        """Durably advance the cursor to ``seq`` (monotonic; never moves backward).

        Only advances when ``seq`` is greater than the stored cursor, so a stale or
        out-of-order checkpoint is a safe no-op. The write is committed before
        returning, which is what guarantees a consumed episode is never redelivered
        after a restart.
        """
        with self._lock:
            self._conn.execute(
                "UPDATE cursor SET seq = ? WHERE id = ? AND seq < ?",
                (seq, _CURSOR_ID, seq),
            )
            self._conn.commit()

    def pending(self) -> int:
        """Count episodes appended but not yet checkpointed."""
        with self._lock:
            (count,) = self._conn.execute(
                "SELECT COUNT(*) FROM episodes WHERE seq > (SELECT seq FROM cursor WHERE id = ?)",
                (_CURSOR_ID,),
            ).fetchone()
        return int(count)

    def close(self) -> None:
        """Release the SQLite connection (and its WAL lock)."""
        with self._lock:
            self._conn.close()

    def __enter__(self) -> Spool:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
