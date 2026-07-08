"""Unit tests for ``CopilotProvider.make_source`` / :class:`CopilotSource` (replay)."""

from __future__ import annotations

from pathlib import Path

import pytest

from memrelay.providers.copilot import EVENTS_FILENAME, CopilotProvider, CopilotSource


def test_make_source_with_path_yields_nonblank_lines(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    events.write_text('{"a": 1}\n\n  \n{"b": 2}\n', encoding="utf-8")

    source = CopilotProvider().make_source(path=events)

    assert isinstance(source, CopilotSource)
    assert list(source) == ['{"a": 1}', '{"b": 2}']


def test_make_source_with_session_id_resolves_under_home(tmp_path: Path) -> None:
    home = tmp_path / ".copilot"
    session_dir = home / "session-state" / "sess-x"
    session_dir.mkdir(parents=True)
    (session_dir / EVENTS_FILENAME).write_text('{"x": 1}\n', encoding="utf-8")

    source = CopilotProvider(copilot_home=home).make_source("sess-x")

    assert list(source) == ['{"x": 1}']


def test_make_source_is_replayable(tmp_path: Path) -> None:
    """Iterating twice re-reads the file — the replay source is not single-shot."""
    events = tmp_path / "events.jsonl"
    events.write_text('{"a": 1}\n{"b": 2}\n', encoding="utf-8")

    source = CopilotProvider().make_source(path=events)

    assert list(source) == ['{"a": 1}', '{"b": 2}']
    assert list(source) == ['{"a": 1}', '{"b": 2}']


def test_make_source_requires_session_or_path() -> None:
    with pytest.raises(ValueError, match="session_id or an explicit path"):
        CopilotProvider().make_source()
