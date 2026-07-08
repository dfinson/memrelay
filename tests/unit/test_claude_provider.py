"""Unit tests for the Claude Code provider (#70).

Mirrors ``test_providers.py`` (the Copilot conformance suite) against the second provider,
proving the frozen :class:`AgentProvider` ABC is satisfied and that ingestion rides entirely
on the installed traceforge ``claude.yaml`` mapping + ``claude`` preprocessor.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from memrelay.providers.base import AgentProvider, LLMStrategyHint, SessionRef
from memrelay.providers.claude_code import (
    CLAUDE_MAPPING,
    DEFAULT_CLAUDE_HOME,
    ClaudeCodeProvider,
    mapping_path,
)
from memrelay.providers.registry import get_registry

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "claude_session.jsonl"


def _make_projects(home: Path, layout: dict[str, list[str]]) -> None:
    """Build a two-level ``projects/<enc-cwd>/<uuid>.jsonl`` tree under ``home``."""
    for project, sessions in layout.items():
        pdir = home / "projects" / project
        pdir.mkdir(parents=True, exist_ok=True)
        for sid in sessions:
            (pdir / f"{sid}.jsonl").write_text("{}\n", encoding="utf-8")


# ── mapping + adapter ────────────────────────────────────────────────────────


def test_mapping_path_resolves_to_real_file() -> None:
    """The packaged traceforge ``claude.yaml`` resolves to an on-disk YAML file."""
    resolved = Path(mapping_path(CLAUDE_MAPPING))
    assert resolved.is_file(), f"mapping not found: {resolved}"
    assert resolved.suffix == ".yaml"


def test_make_adapter_scopes_to_session_id_and_applies_preprocessor() -> None:
    """A raw Claude assistant record maps (via the auto-applied ``claude`` preprocessor)."""
    adapter = ClaudeCodeProvider().make_adapter("sess-123")
    # An assistant message with a tool_use block flattens to a tool.call.started event,
    # which only happens if the mapping's declared preprocessor ran inside ``parse``.
    line = json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "hi"},
                    {"type": "tool_use", "id": "t1", "name": "read_file", "input": {"path": "a"}},
                ]
            },
        }
    )
    events = list(adapter.parse(line))
    kinds = {str(e.kind) for e in events}
    assert all(e.session_id == "sess-123" for e in events)
    assert "message.assistant" in kinds
    assert "tool.call.started" in kinds


def test_fixture_replays_to_tool_and_message_events() -> None:
    """The committed synthetic fixture yields the expected canonical event kinds."""
    provider = ClaudeCodeProvider()
    adapter = provider.make_adapter("fx")
    kinds: set[str] = set()
    for line in provider.make_source(path=FIXTURE):
        for event in adapter.parse(line):
            kinds.add(str(event.kind))
    assert {
        "message.user",
        "message.assistant",
        "tool.call.started",
        "tool.call.completed",
    } <= kinds


# ── discovery (two-level projects/<enc-cwd>/<uuid>.jsonl) ─────────────────────


def test_discover_sessions_recurses_project_dirs(tmp_path: Path) -> None:
    home = tmp_path / ".claude"
    _make_projects(
        home,
        {
            "C--Users-me-proj-a": ["aaa", "bbb"],
            "C--Users-me-proj-b": ["ccc"],
        },
    )
    provider = ClaudeCodeProvider(claude_home=home)
    refs = list(provider.discover_sessions())
    assert [r.session_id for r in refs] == ["aaa", "bbb", "ccc"]
    assert all(isinstance(r, SessionRef) and r.agent_id == "claude" for r in refs)
    assert all(r.path and Path(r.path).is_file() for r in refs)


def test_discover_sessions_empty_when_no_projects(tmp_path: Path) -> None:
    assert list(ClaudeCodeProvider(claude_home=tmp_path / ".claude").discover_sessions()) == []


def test_read_raw_yields_nonblank_lines(tmp_path: Path) -> None:
    log = tmp_path / "s.jsonl"
    log.write_text('{"a": 1}\n\n  \n{"b": 2}\n', encoding="utf-8")
    ref = SessionRef(session_id="s", agent_id="claude", path=str(log))
    assert list(ClaudeCodeProvider().read_raw(ref)) == ['{"a": 1}', '{"b": 2}']


def test_make_source_resolves_session_id_by_scanning_projects(tmp_path: Path) -> None:
    home = tmp_path / ".claude"
    _make_projects(home, {"proj-x": ["target"]})
    provider = ClaudeCodeProvider(claude_home=home)
    source = provider.make_source("target")
    assert Path(source.path).name == "target.jsonl"


def test_make_source_requires_session_or_path() -> None:
    with pytest.raises(ValueError):
        ClaudeCodeProvider().make_source()


# ── construction / detection ─────────────────────────────────────────────────


def test_from_home_honors_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MEMRELAY_CLAUDE_HOME", str(tmp_path / "envhome"))
    assert ClaudeCodeProvider.from_home().claude_home == tmp_path / "envhome"


def test_from_home_explicit_overrides_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MEMRELAY_CLAUDE_HOME", str(tmp_path / "envhome"))
    provider = ClaudeCodeProvider.from_home(str(tmp_path / "explicit"))
    assert provider.claude_home == tmp_path / "explicit"


def test_from_home_defaults_to_dot_claude(monkeypatch) -> None:
    monkeypatch.delenv("MEMRELAY_CLAUDE_HOME", raising=False)
    assert ClaudeCodeProvider.from_home().claude_home == Path(DEFAULT_CLAUDE_HOME).expanduser()


def test_is_present_true_when_projects_exists(tmp_path: Path) -> None:
    (tmp_path / "projects").mkdir()
    assert ClaudeCodeProvider(claude_home=tmp_path).is_present() is True


def test_is_present_false_when_absent(tmp_path: Path) -> None:
    assert ClaudeCodeProvider(claude_home=tmp_path).is_present() is False


# ── LLM strategy advertisement (metadata only) ───────────────────────────────


def test_llm_strategy_advertises_borrow_host_claude() -> None:
    assert ClaudeCodeProvider().llm_strategy() == LLMStrategyHint(
        strategy="borrow-host", host="claude"
    )


# ── MCP registration (non-destructive merge into ~/.claude.json) ─────────────


def test_mcp_server_entry_is_stdio() -> None:
    entry = ClaudeCodeProvider().mcp_server_entry()
    assert entry["type"] == "stdio"  # asymmetry vs Copilot's "local" is intentional
    assert entry["command"] == "memrelay"
    assert entry["args"] == ["mcp"]


def test_register_creates_config_with_memrelay_entry(tmp_path: Path) -> None:
    provider = ClaudeCodeProvider(claude_home=tmp_path)
    path = provider.register()
    assert path == tmp_path / ".claude.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["mcpServers"]["memrelay"]["type"] == "stdio"


def test_register_preserves_existing_keys_and_servers(tmp_path: Path) -> None:
    """Merge must not clobber the large live ``~/.claude.json`` state document."""
    path = tmp_path / ".claude.json"
    path.write_text(
        json.dumps(
            {
                "userID": "keep-me",
                "projects": {"/some/proj": {"mcpServers": {}}},
                "mcpServers": {"other": {"type": "stdio", "command": "x"}},
            }
        ),
        encoding="utf-8",
    )
    ClaudeCodeProvider(claude_home=tmp_path).register()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["userID"] == "keep-me"  # unrelated top-level key preserved
    assert data["projects"] == {"/some/proj": {"mcpServers": {}}}  # projects map preserved
    assert data["mcpServers"]["other"] == {"type": "stdio", "command": "x"}  # sibling server kept
    assert data["mcpServers"]["memrelay"]["command"] == "memrelay"  # ours merged in


def test_register_is_idempotent(tmp_path: Path) -> None:
    provider = ClaudeCodeProvider(claude_home=tmp_path)
    provider.register()
    first = (tmp_path / ".claude.json").read_text(encoding="utf-8")
    provider.register()
    assert (tmp_path / ".claude.json").read_text(encoding="utf-8") == first


def test_register_refuses_to_clobber_malformed_json(tmp_path: Path) -> None:
    path = tmp_path / ".claude.json"
    path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        ClaudeCodeProvider(claude_home=tmp_path).register()
    assert path.read_text(encoding="utf-8") == "{not valid json"  # left untouched


# ── ABC conformance + self-registration ──────────────────────────────────────


def test_provider_satisfies_agent_provider_abc() -> None:
    assert isinstance(ClaudeCodeProvider(), AgentProvider)


def test_provider_self_registers_with_no_central_edit() -> None:
    """The pkgutil sweep discovers ``claude_code.py`` — no edit to any central list."""
    registry = get_registry()
    assert "claude" in registry.ids()
    assert isinstance(registry.create("claude"), ClaudeCodeProvider)
