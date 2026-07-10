"""Hermetic ingestion-robustness soak for the live ``events.jsonl`` observe path (E1-S7 #12).

Issue #12's acceptance criteria are worded for a file-*tailing* / SQLite reader, but the
actually-wired ingestion path is ``memrelay observe`` → :func:`run_observe`, which replays a
session's ``events.jsonl`` line-by-line (``CopilotProvider`` source + traceforge adapter) into
the durable :class:`~memrelay.ingest.spool.Spool`. This soak exercises the four robustness
properties end-to-end against temp files + a real *local* SQLite spool — **no network, no API
keys, no embedders**:

* **P1 — malformed / partial / corrupt records are skipped *and counted*.** A record that is
  not valid JSON (or not a JSON object) is tallied in ``result.malformed`` and dropped; it never
  raises. NB: traceforge's adapter already *silently* swallows unparseable input (its ``parse``
  contract is "never raise"), so :func:`run_observe` detects malformed records itself to keep a
  visible count — that is what these assertions pin down.
* **P2 — truncation / replacement recovery.** A mid-read source failure (the events file is
  truncated/replaced/unreadable while iterating) is caught, tallied in ``result.source_errors``,
  and recovered by flushing partial progress and ending the pass cleanly. Cross-run truncation
  then replacement is recovered because every observe re-opens and re-reads the current file.
  (VACUUM and ROWID-RESET from the SQLite-worded ACs have no JSONL analog and are intentionally
  not simulated.)
* **P3 — exactly-once resume across a restart.** Guaranteed at the episode level by the spool's
  unique ``idempotency_key``: truncating then restoring the file and re-observing from a fresh
  spool connection (a "restart") appends **zero** duplicate episodes. A durable *source*
  read-offset is a deferred perf optimization, not required for correctness (see
  :func:`run_observe`).
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest

from memrelay.ingest.graphiti_sink import run_observe
from memrelay.ingest.spool import Spool
from memrelay.providers.copilot import CopilotProvider

pytestmark = pytest.mark.integration


def _observe(events: Path, session_id: str, spool: Spool, *, cwd: str, provider: object = None):
    """Drive the async :func:`run_observe` synchronously with session B's real spool helpers.

    ``cwd`` points at a non-repo temp dir so namespace resolution takes its documented
    fallback (no git remote) — the counts/idempotency under test don't depend on it.
    """
    return asyncio.run(run_observe(events, session_id, spool=spool, cwd=cwd, provider=provider))


def _fixture_lines(fixture: Path) -> list[str]:
    return [line for line in fixture.read_text(encoding="utf-8").splitlines() if line.strip()]


class _FailingSource:
    """A replay source that yields every line, then raises on the next read (P2).

    Mimics the events file becoming unreadable (truncated/replaced) mid-iteration: the
    reader consumes the records currently present, then the underlying read fails.
    """

    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    def __iter__(self) -> Iterator[str]:
        yield from self._lines
        raise OSError("simulated truncation/replacement during read")


class _MidReadFailureProvider(CopilotProvider):
    """Real Copilot adapter (``copilot.yaml``), but a source that fails partway through."""

    def __init__(self, lines: list[str]) -> None:
        super().__init__()
        self._lines = lines

    def make_source(
        self, session_id: str | None = None, *, path: str | Path | None = None
    ) -> _FailingSource:
        return _FailingSource(self._lines)


# ── P1: malformed / partial / corrupt records ──────────────────────────────────────────────
def test_malformed_records_are_skipped_counted_and_never_crash(
    tmp_path: Path, copilot_fixture: Path
) -> None:
    valid = _fixture_lines(copilot_fixture)
    malformed = [
        "this is not json at all",  # invalid JSON (bare text)
        '{"type": "message.user", "data": {',  # truncated / partial JSON object
        '"a bare json string"',  # valid JSON, but not an object
        "1234567890",  # valid JSON number, not an object
    ]
    # Interleave the corrupt records around the valid stream. They must be dropped *before*
    # the pipeline, so the valid event sequence — and thus the composed episodes — is
    # byte-identical to a clean run.
    body = [malformed[0], malformed[1], *valid, malformed[2], malformed[3]]
    events = tmp_path / "events.jsonl"
    events.write_text("\n".join(body) + "\n", encoding="utf-8")

    spool_db = tmp_path / "spool" / "spool.db"
    with Spool(spool_db) as spool:
        result = _observe(events, "soak-malformed", spool, cwd=str(tmp_path))

        # Every corrupt record is tallied and skipped; ingestion never raises.
        assert result.malformed == 4
        assert result.source_errors == 0
        # The valid stream still composes its 3 episodes (2 work-units + a summary).
        assert result.appended == 3
        assert spool.pending() == 3


def test_all_malformed_input_ingests_nothing_without_crashing(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    events.write_text("garbage\n{not json}\n[\n42\n", encoding="utf-8")

    spool_db = tmp_path / "spool" / "spool.db"
    with Spool(spool_db) as spool:
        result = _observe(events, "soak-all-bad", spool, cwd=str(tmp_path))

        assert result.malformed == 4
        assert result.parsed == 0
        assert result.appended == 0
        assert result.source_errors == 0
        assert spool.pending() == 0


# ── P2 + P3: truncation / replacement recovery with exactly-once resume ─────────────────────
def test_truncate_then_replace_recovers_and_stays_exactly_once(
    tmp_path: Path, copilot_fixture: Path
) -> None:
    content = copilot_fixture.read_text(encoding="utf-8")
    events = tmp_path / "events.jsonl"
    events.write_text(content, encoding="utf-8")
    spool_db = tmp_path / "spool" / "spool.db"

    # (1) First pass ingests the 3 composed episodes.
    with Spool(spool_db) as spool:
        first = _observe(events, "soak-restart", spool, cwd=str(tmp_path))
        assert first.appended == 3
        assert spool.pending() == 3
        first_keys = [record["idempotency_key"] for _, record in spool.read_batch()]

    # (2) File truncated to zero bytes (rotation/truncation). A fresh "restart" must not crash
    # and must not lose already-ingested episodes.
    events.write_text("", encoding="utf-8")
    with Spool(spool_db) as spool:
        empty = _observe(events, "soak-restart", spool, cwd=str(tmp_path))
        assert empty.parsed == 0
        assert empty.malformed == 0
        assert empty.source_errors == 0
        assert empty.appended == 0
        assert spool.pending() == 3  # nothing lost across the truncation

    # (3) File replaced with the original content again (swapped back in). Re-observing across
    # the restart re-reads the whole file, but the durable unique ``idempotency_key`` dedupes to
    # ZERO new rows — exactly-once resume.
    events.write_text(content, encoding="utf-8")
    with Spool(spool_db) as spool:
        again = _observe(events, "soak-restart", spool, cwd=str(tmp_path))
        assert again.appended == 3  # composed again from the file...
        assert spool.pending() == 3  # ...but deduped to zero new durable rows
        assert [record["idempotency_key"] for _, record in spool.read_batch()] == first_keys


# ── P2: a source error raised mid-read is caught, counted, and recovered ────────────────────
def test_source_read_error_is_counted_and_progress_preserved(
    tmp_path: Path, copilot_fixture: Path
) -> None:
    lines = _fixture_lines(copilot_fixture)
    events = tmp_path / "events.jsonl"
    events.write_text(copilot_fixture.read_text(encoding="utf-8"), encoding="utf-8")
    # The source yields every valid record, then its next read raises OSError. The failure
    # position is irrelevant to the code path (the whole read loop is guarded); consuming the
    # records first keeps the composed-episode count deterministic while still driving recovery.
    provider = _MidReadFailureProvider(lines)

    spool_db = tmp_path / "spool" / "spool.db"
    with Spool(spool_db) as spool:
        result = _observe(events, "soak-oserr", spool, cwd=str(tmp_path), provider=provider)

        # The mid-read OSError is detected + counted, not propagated (no crash).
        assert result.source_errors == 1
        # Progress read before the failure is flushed to the durable spool.
        assert result.appended == 3
        assert spool.pending() == 3
