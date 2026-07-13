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
* ``PRAGMA busy_timeout`` (see :data:`_BUSY_TIMEOUT_MS`) — pinned explicitly so that when
  the daemon's two separate ``Spool`` connections over one ``spool.db`` (ingester + poller)
  contend, the loser waits rather than immediately raising ``database is locked``.
* ``INSERT OR IGNORE`` on the unique ``idempotency_key`` makes :meth:`append`
  idempotent: re-appending an already-seen episode is a silent no-op.

Concurrency: the connection is opened with ``check_same_thread=False`` and every
operation is guarded by a lock, so a single writer (``append``) and a single
reader (``read_batch`` / ``checkpoint``) may live on different threads (e.g. the
daemon's note handler vs. the ingester task) without corrupting the DB. Across
*connections* (the ingester and poller each open their own ``Spool`` on the same file)
the pinned ``busy_timeout`` absorbs the brief cross-connection write contention.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

from memrelay.ingest.episode import from_row, to_row

_CURSOR_ID = 1

#: Explicit SQLite busy-timeout (ms) pinned on the connection. The daemon runs the ingester
#: and the session poller on **separate** ``Spool`` connections over the same ``spool.db``
#: (see :mod:`memrelay.daemon.runtime`); when their small writes contend, the loser waits in
#: SQLite's busy handler for up to this long instead of immediately raising ``database is
#: locked``. CPython's ``sqlite3.connect(timeout=5.0)`` default already maps to
#: ``sqlite3_busy_timeout(5000)``, so this PINS that value explicitly — it does **not** *reduce*
#: the (already-bounded) contention; it regression-proofs it, so a future change to the connect
#: call (or a differently-opened connection) can no longer silently drop the timeout to 0.
#: Truly *eliminating* cross-connection contention would need a single shared ``Spool`` across
#: the ingester/poller factories (option b), but that global-connection lifecycle coupling was
#: deliberately declined as over-engineering for a fault the #147 non-fatal ingester guard
#: already makes benign. That guard stays the correctness backstop; this pin only trims
#: avoidable ``database is locked`` log-noise/retries under load.
_BUSY_TIMEOUT_MS = 5000


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
        # Pin the busy-timeout explicitly (see _BUSY_TIMEOUT_MS): PRAGMA values can't be
        # bound parameters and the interpolated value is an int constant, so it is safe.
        self._conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
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

    def pending_bytes(self) -> int:
        """Return the on-disk byte footprint of *unprocessed* episodes (E3-S4 #33).

        The companion to :meth:`pending` (which counts them): this sums the stored byte
        length of every row past the durable cursor (``seq > cursor``). That is exactly
        the **backpressure backlog** — the data that grows when the ingester falls behind
        and that "summarize-in-place" compaction can actually reclaim (compaction is
        forbidden from touching already-ingested history below the cursor). Measuring the
        controllable quantity, rather than the whole file, is what lets the ingester's
        budget loop converge: each compaction pass strictly shrinks this number.

        Already-checkpointed rows are excluded by design; their retention is the spool's
        append-only crash-safety invariant, a separate concern from this budget.
        """
        with self._lock:
            (total,) = self._conn.execute(
                "SELECT COALESCE(SUM("
                " LENGTH(CAST(record AS BLOB))"
                " + LENGTH(CAST(idempotency_key AS BLOB))), 0)"
                " FROM episodes WHERE seq > (SELECT seq FROM cursor WHERE id = ?)",
                (_CURSOR_ID,),
            ).fetchone()
        return int(total)

    def retained_bytes(self) -> int:
        """Return the on-disk byte footprint of *already-ingested* history (E3 #112).

        The below-cursor mirror of :meth:`pending_bytes`: it sums the stored byte length of
        every row at or before the durable cursor (``seq <= cursor``) — the append-only history
        that :meth:`checkpoint` only ever accumulates and that nothing else reclaims. That is
        precisely the quantity a retention policy must bound. Backpressure compaction (#33) caps
        the *unprocessed backlog* (``seq > cursor`` via :meth:`pending_bytes`); retention caps
        *this*, so the two together bound the whole file. :meth:`reclaim` drives this number down
        to a configured budget.
        """
        with self._lock:
            (total,) = self._conn.execute(
                "SELECT COALESCE(SUM("
                " LENGTH(CAST(record AS BLOB))"
                " + LENGTH(CAST(idempotency_key AS BLOB))), 0)"
                " FROM episodes WHERE seq <= (SELECT seq FROM cursor WHERE id = ?)",
                (_CURSOR_ID,),
            ).fetchone()
        return int(total)

    def replace(self, delete_seqs: list[int], insert_records: list[dict[str, Any]]) -> None:
        """Atomically drop ``delete_seqs`` and append ``insert_records`` (E3-S4 #33).

        The single crash-safe primitive behind "summarize-in-place": the ingester reads
        the oldest *unprocessed* rows, folds them into fewer/smaller summary records, and
        calls this to swap them in. Both halves run in **one transaction** — SQLite's
        implicit transaction spans the ``DELETE``\\ s and ``INSERT``\\ s and is finalized by
        a single :meth:`commit`, with a :meth:`rollback` on any error — so a crash (or a
        failing insert) leaves the spool in exactly one of two consistent states:

        * **before commit** → the originals are intact and simply re-drain; **after
          commit** → the summaries are present and the originals are gone.

        Crash-safety of the durable cursor is preserved because this never writes the
        cursor and only ever deletes rows **strictly past it** (a ``seq > cursor`` guard
        on every delete, so already-checkpointed history can never be removed even by a
        buggy caller). New summary rows are appended (fresh, monotonic ``seq``), keeping
        the append-only ordering contract; their deterministic ``idempotency_key`` makes
        a re-attempted compaction safe.
        """
        if not delete_seqs and not insert_records:
            return
        with self._lock:
            try:
                for seq in delete_seqs:
                    self._conn.execute(
                        "DELETE FROM episodes WHERE seq = ?"
                        " AND seq > (SELECT seq FROM cursor WHERE id = ?)",
                        (int(seq), _CURSOR_ID),
                    )
                for record in insert_records:
                    self._conn.execute(
                        "INSERT INTO episodes (idempotency_key, record) VALUES (?, ?)",
                        (record["idempotency_key"], to_row(record)),
                    )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def reclaim(self, max_retained_bytes: int) -> int:
        """Atomically prune the oldest below-cursor history down to a byte budget (E3 #112).

        The retention counterpart to :meth:`replace`. Already-ingested rows (``seq <= cursor``)
        are the spool's append-only history — :meth:`checkpoint` only accumulates them, so in a
        long-lived daemon they grow the file without bound. This reclaims the **oldest** of them,
        keeping the newest history whose running total fits ``max_retained_bytes`` and deleting the
        rest, so :meth:`retained_bytes` ends ``<= max_retained_bytes``. Returns the number of rows
        pruned.

        Two no-ops issue no ``DELETE`` at all: ``max_retained_bytes <= 0`` (retention disabled →
        keep all history, the byte-identical pre-#112 behaviour) and an already-under-budget spool.

        Crash-safety is preserved exactly as in :meth:`replace`:

        * **Below-cursor only.** The ``DELETE`` carries an explicit ``seq <= cursor`` guard — the
          inverse of :meth:`replace`'s ``seq > cursor`` guard — so an un-ingested (``seq > cursor``)
          row can never be removed, even by a buggy caller and even if the ranking sub-select were
          refactored. The un-ingested backlog and its ordering are untouched.
        * **Never writes the cursor.** The durable cursor is an integer high-water mark, not a
          foreign key into ``episodes``; :meth:`read_batch` / :meth:`pending` / :meth:`checkpoint`
          filter on its *value*, so deleting history (including the row at ``seq == cursor``) leaves
          every drain semantic intact — exactly-once delivery and cursor monotonicity both hold.
        * **Atomic.** The prune runs in one implicit transaction finalized by a single
          :meth:`commit`, with :meth:`rollback` on any error, so a crash leaves the spool either
          fully pre-prune or fully post-prune, never half-way.
        * **Cannot race the drain.** ``reclaim`` and :meth:`checkpoint` both hold ``self._lock`` and
          commit independently, so they serialize; the cursor is read *inside* this transaction, so
          a concurrent drain can only turn *more* rows into below-cursor history — never make a
          pruned row un-ingested.
        """
        if max_retained_bytes <= 0:
            return 0
        with self._lock:
            (retained,) = self._conn.execute(
                "SELECT COALESCE(SUM("
                " LENGTH(CAST(record AS BLOB))"
                " + LENGTH(CAST(idempotency_key AS BLOB))), 0)"
                " FROM episodes WHERE seq <= (SELECT seq FROM cursor WHERE id = ?)",
                (_CURSOR_ID,),
            ).fetchone()
            if int(retained) <= max_retained_bytes:
                return 0
            try:
                # Keep the newest below-cursor rows whose running byte total (newest→oldest)
                # stays within budget; delete the older remainder. The redundant outer
                # ``seq <= cursor`` conjunct is the load-bearing safety guard (see docstring):
                # it makes deleting an un-ingested row impossible independent of the CTE.
                deleted = self._conn.execute(
                    "DELETE FROM episodes WHERE seq IN ("
                    " SELECT seq FROM ("
                    "  SELECT seq, SUM("
                    "   LENGTH(CAST(record AS BLOB))"
                    "   + LENGTH(CAST(idempotency_key AS BLOB)))"
                    "   OVER (ORDER BY seq DESC) AS cum_bytes"
                    "  FROM episodes"
                    "  WHERE seq <= (SELECT seq FROM cursor WHERE id = ?)"
                    " ) WHERE cum_bytes > ?"
                    ") AND seq <= (SELECT seq FROM cursor WHERE id = ?)",
                    (_CURSOR_ID, max_retained_bytes, _CURSOR_ID),
                ).rowcount
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return int(deleted)

    def close(self) -> None:
        """Release the SQLite connection (and its WAL lock)."""
        with self._lock:
            self._conn.close()

    def __enter__(self) -> Spool:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
