"""Unit tests for ``CopilotProvider.make_filewatch_source`` (#11 live-tail intake).

The factory only *constructs* a traceforge ``FileWatchSource`` — it does not enter it, so
no watchdog observer thread is started here. These tests assert path resolution (explicit
path vs ``session_id`` under the Copilot home), the ``start_at="beginning"`` default
(drain-history-then-tail), and the guard when neither is given — mirroring the replay
``make_source`` contract so the two intakes stay symmetric.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from traceforge.sources import FileWatchSource

from memrelay.providers.copilot import EVENTS_FILENAME, CopilotProvider


def test_make_filewatch_source_with_path_defaults_to_beginning(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    events.write_text('{"a": 1}\n', encoding="utf-8")

    provider = CopilotProvider()
    source = provider.make_filewatch_source(path=events)

    assert isinstance(source, FileWatchSource)
    assert source.path == events.resolve()
    # Provenance name is the real agent id (== make_source's), not a hardcoded literal.
    assert source.name == provider.id
    # #11 RULING 1 (A1): default start_at="beginning" so the tail drains history 0→EOF once
    # then tails appends — one continuous replay-then-tail source.
    assert source.start_at == "beginning"


def test_make_filewatch_source_with_session_id_resolves_under_home(tmp_path: Path) -> None:
    home = tmp_path / ".copilot"
    session_dir = home / "session-state" / "sess-x"
    session_dir.mkdir(parents=True)
    (session_dir / EVENTS_FILENAME).write_text('{"x": 1}\n', encoding="utf-8")

    source = CopilotProvider(copilot_home=home).make_filewatch_source("sess-x")

    assert isinstance(source, FileWatchSource)
    assert source.path == (session_dir / EVENTS_FILENAME).resolve()
    assert source.start_at == "beginning"


def test_make_filewatch_source_honours_start_at_override(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    events.write_text("", encoding="utf-8")

    source = CopilotProvider().make_filewatch_source(path=events, start_at="end")

    assert source.start_at == "end"


def test_make_filewatch_source_requires_session_or_path() -> None:
    with pytest.raises(ValueError, match="session_id or an explicit path"):
        CopilotProvider().make_filewatch_source()
