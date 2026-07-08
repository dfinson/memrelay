"""Unit tests for the Copilot provider wiring (source/mapping resolution)."""

from __future__ import annotations

from pathlib import Path

from memrelay.providers import CopilotProvider, SessionRef
from memrelay.providers.copilot import CANONICAL_MAPPING, FALLBACK_MAPPING, mapping_path


def test_mapping_paths_resolve_to_real_files() -> None:
    """The packaged traceforge mappings resolve to on-disk YAML files."""
    for name in (CANONICAL_MAPPING, FALLBACK_MAPPING):
        resolved = Path(mapping_path(name))
        assert resolved.is_file(), f"mapping not found: {resolved}"
        assert resolved.suffix == ".yaml"


def test_make_adapter_scopes_to_session_id() -> None:
    adapter = CopilotProvider().make_adapter("sess-123")
    # A minimal session.start record must map with the injected session_id.
    line = '{"type": "session.start", "id": "x", "timestamp": "2026-01-01T00:00:00Z", "data": {}}'
    events = list(adapter.parse(line))
    assert len(events) == 1
    assert events[0].session_id == "sess-123"
    assert str(events[0].kind) == "session.started"


def test_discover_sessions(tmp_path: Path) -> None:
    home = tmp_path / ".copilot"
    for sid in ("aaa", "bbb"):
        d = home / "session-state" / sid
        d.mkdir(parents=True)
        (d / "events.jsonl").write_text("{}\n", encoding="utf-8")
    # A dir without events.jsonl must be skipped.
    (home / "session-state" / "empty").mkdir(parents=True)

    provider = CopilotProvider(copilot_home=home)
    refs = list(provider.discover_sessions())
    assert [r.session_id for r in refs] == ["aaa", "bbb"]
    assert all(isinstance(r, SessionRef) and r.agent_id == "copilot" for r in refs)


def test_read_raw_yields_nonblank_lines(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    events.write_text('{"a": 1}\n\n  \n{"b": 2}\n', encoding="utf-8")
    ref = SessionRef(session_id="s", agent_id="copilot", path=str(events))
    lines = list(CopilotProvider().read_raw(ref))
    assert lines == ['{"a": 1}', '{"b": 2}']
