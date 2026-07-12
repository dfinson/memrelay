"""Per-agent unit tests for the E12-S5 provider expansion (#71).

Mirrors ``test_claude_provider.py`` across the ten agents added in E12-S5, proving each
satisfies the frozen :class:`AgentProvider` ABC, rides entirely on its installed traceforge
mapping (+ preprocessor), replays its synthetic fixture to the exact canonical event kinds,
resolves its home (env → default), and either **serves** MCP via a non-destructive JSON merge
(cline, amazonq, opencode) or is **ingest-only** (the other seven raise ``NotImplementedError``
from the three serving hooks).

The registry-driven conformance matrix (``tests/integration/test_agent_conformance.py``) already
asserts the generic shape for every provider; these tests add the per-agent specifics that the
matrix intentionally does not encode.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from memrelay.providers.aider import DEFAULT_AIDER_HOME, AiderProvider
from memrelay.providers.amazon_q import DEFAULT_AMAZONQ_HOME, AmazonQProvider
from memrelay.providers.antigravity import DEFAULT_ANTIGRAVITY_HOME, AntigravityProvider
from memrelay.providers.base import AgentProvider, LLMStrategyHint
from memrelay.providers.cline import DEFAULT_CLINE_HOME, ClineProvider
from memrelay.providers.codex import DEFAULT_CODEX_HOME, CodexProvider
from memrelay.providers.continue_dev import DEFAULT_CONTINUE_HOME, ContinueProvider
from memrelay.providers.goose import DEFAULT_GOOSE_HOME, GooseProvider
from memrelay.providers.opencode import DEFAULT_OPENCODE_HOME, OpenCodeProvider
from memrelay.providers.openhands import DEFAULT_OPENHANDS_HOME, OpenHandsProvider
from memrelay.providers.registry import get_registry
from memrelay.providers.swe_agent import DEFAULT_SWEAGENT_HOME, SweAgentProvider

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


@dataclass(frozen=True)
class Spec:
    """Everything the parametrized tests need to know about one agent."""

    id: str
    provider_cls: type[AgentProvider]
    home_env: str
    default_home: str
    #: exact canonical event kinds the committed fixture replays to.
    expected_kinds: set[str]
    #: ("dir"|"file", relpath under home) whose existence makes ``is_present`` true.
    present_marker: tuple[str, str]
    #: MCP-serving agents only: (relpath tuple under home, container key). Empty ⇒ ingest-only.
    mcp_relpath: tuple[str, ...] = ()
    mcp_container: str = ""


SPECS: list[Spec] = [
    Spec(
        id="codex",
        provider_cls=CodexProvider,
        home_env="MEMRELAY_CODEX_HOME",
        default_home=DEFAULT_CODEX_HOME,
        expected_kinds={
            "message.user",
            "tool.call.started",
            "tool.call.completed",
            "message.assistant",
        },
        present_marker=("dir", "sessions"),
    ),
    Spec(
        id="continue",
        provider_cls=ContinueProvider,
        home_env="MEMRELAY_CONTINUE_HOME",
        default_home=DEFAULT_CONTINUE_HOME,
        expected_kinds={
            "message.user",
            "message.assistant",
            "tool.call.started",
            "tool.call.completed",
        },
        present_marker=("dir", "sessions"),
    ),
    Spec(
        id="cline",
        provider_cls=ClineProvider,
        home_env="MEMRELAY_CLINE_HOME",
        default_home=DEFAULT_CLINE_HOME,
        expected_kinds={
            "session.started",
            "message.assistant",
            "permission.requested",
            "session.ended",
        },
        present_marker=("dir", "tasks"),
        mcp_relpath=("settings", "cline_mcp_settings.json"),
        mcp_container="mcpServers",
    ),
    Spec(
        id="aider",
        provider_cls=AiderProvider,
        home_env="MEMRELAY_AIDER_HOME",
        default_home=DEFAULT_AIDER_HOME,
        expected_kinds={
            "session.started",
            "llm.call.started",
            "llm.call.completed",
            "session.ended",
        },
        present_marker=("file", "analytics.jsonl"),
    ),
    Spec(
        id="amazonq",
        provider_cls=AmazonQProvider,
        home_env="MEMRELAY_AMAZONQ_HOME",
        default_home=DEFAULT_AMAZONQ_HOME,
        expected_kinds={
            "message.user",
            "message.assistant",
            "tool.call.started",
            "tool.call.completed",
        },
        present_marker=("dir", ""),
        mcp_relpath=("mcp.json",),
        mcp_container="mcpServers",
    ),
    Spec(
        id="goose",
        provider_cls=GooseProvider,
        home_env="MEMRELAY_GOOSE_HOME",
        default_home=DEFAULT_GOOSE_HOME,
        expected_kinds={
            "message.user",
            "message.assistant",
            "tool.call.started",
            "tool.call.completed",
        },
        present_marker=("dir", "sessions"),
    ),
    Spec(
        id="opencode",
        provider_cls=OpenCodeProvider,
        home_env="MEMRELAY_OPENCODE_HOME",
        default_home=DEFAULT_OPENCODE_HOME,
        expected_kinds={
            "session.started",
            "message.user",
            "message.assistant",
            "tool.call.completed",
        },
        present_marker=("dir", ""),
        mcp_relpath=("opencode.json",),
        mcp_container="mcp",
    ),
    Spec(
        id="openhands",
        provider_cls=OpenHandsProvider,
        home_env="MEMRELAY_OPENHANDS_HOME",
        default_home=DEFAULT_OPENHANDS_HOME,
        expected_kinds={
            "message.user",
            "message.assistant",
            "command.started",
            "command.completed",
        },
        present_marker=("dir", "sessions"),
    ),
    Spec(
        id="sweagent",
        provider_cls=SweAgentProvider,
        home_env="MEMRELAY_SWEAGENT_HOME",
        default_home=DEFAULT_SWEAGENT_HOME,
        expected_kinds={
            "message.system",
            "message.user",
            "message.assistant",
            "tool.output",
        },
        present_marker=("dir", ""),
    ),
    Spec(
        id="antigravity",
        provider_cls=AntigravityProvider,
        home_env="MEMRELAY_ANTIGRAVITY_HOME",
        default_home=DEFAULT_ANTIGRAVITY_HOME,
        expected_kinds={
            "message.user",
            "message.assistant",
            "reasoning.started",
            "tool.call.started",
            "task.completed",
        },
        present_marker=("dir", "sessions"),
    ),
]

SERVING_SPECS = [s for s in SPECS if s.mcp_relpath]
INGEST_ONLY_SPECS = [s for s in SPECS if not s.mcp_relpath]


def _id(spec: Spec) -> str:
    return spec.id


def _fixture(spec: Spec) -> Path:
    return FIXTURES / f"{spec.id}_session.jsonl"


def _make_present(spec: Spec, home: Path) -> None:
    kind, rel = spec.present_marker
    target = home / rel if rel else home
    if kind == "dir":
        target.mkdir(parents=True, exist_ok=True)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{}\n", encoding="utf-8")


# ── mapping + adapter + fixture replay ───────────────────────────────────────


@pytest.mark.parametrize("spec", SPECS, ids=_id)
def test_make_adapter_scopes_to_session_id(spec: Spec) -> None:
    """Every event the fixture replays is stamped with the adapter's session id."""
    provider = spec.provider_cls()
    adapter = provider.make_adapter("sess-scope")
    events = [
        event for line in provider.make_source(path=_fixture(spec)) for event in adapter.parse(line)
    ]
    assert events, f"{spec.id} fixture produced no events"
    assert all(e.session_id == "sess-scope" for e in events)


@pytest.mark.parametrize("spec", SPECS, ids=_id)
def test_fixture_replays_to_expected_canonical_kinds(spec: Spec) -> None:
    """The committed synthetic fixture yields exactly this agent's expected canonical kinds.

    This runs entirely through the provider's own ``make_source`` + ``make_adapter`` (so the
    mapping's declared preprocessor is exercised), a stronger assertion than the conformance
    matrix's generic "≥3 canonical events" floor.
    """
    provider = spec.provider_cls()
    adapter = provider.make_adapter(f"fx-{spec.id}")
    kinds = {
        str(event.kind)
        for line in provider.make_source(path=_fixture(spec))
        for event in adapter.parse(line)
    }
    assert kinds == spec.expected_kinds


# ── construction / detection ─────────────────────────────────────────────────


@pytest.mark.parametrize("spec", SPECS, ids=_id)
def test_from_home_honors_env(spec: Spec, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv(spec.home_env, str(tmp_path / "envhome"))
    provider = spec.provider_cls.from_home()
    assert getattr(provider, f"{spec.id}_home") == tmp_path / "envhome"


@pytest.mark.parametrize("spec", SPECS, ids=_id)
def test_from_home_explicit_overrides_env(spec: Spec, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv(spec.home_env, str(tmp_path / "envhome"))
    provider = spec.provider_cls.from_home(str(tmp_path / "explicit"))
    assert getattr(provider, f"{spec.id}_home") == tmp_path / "explicit"


@pytest.mark.parametrize("spec", SPECS, ids=_id)
def test_from_home_defaults_when_env_absent(spec: Spec, monkeypatch) -> None:
    monkeypatch.delenv(spec.home_env, raising=False)
    provider = spec.provider_cls.from_home()
    assert getattr(provider, f"{spec.id}_home") == Path(spec.default_home).expanduser()


@pytest.mark.parametrize("spec", SPECS, ids=_id)
def test_is_present_true_with_marker_false_when_empty(spec: Spec, tmp_path: Path) -> None:
    absent = spec.provider_cls.from_home(str(tmp_path / "absent"))
    assert absent.is_present() is False

    home = tmp_path / "present"
    home.mkdir()
    _make_present(spec, home)
    assert spec.provider_cls.from_home(str(home)).is_present() is True


# ── LLM strategy advertisement (metadata only) ───────────────────────────────


@pytest.mark.parametrize("spec", SPECS, ids=_id)
def test_llm_strategy_is_byo_key(spec: Spec) -> None:
    """All E12-S5 agents advertise byo-key (no memrelay host-borrow path exists for them)."""
    assert spec.provider_cls().llm_strategy() == LLMStrategyHint(strategy="byo-key", host=None)


# ── ABC conformance + self-registration ──────────────────────────────────────


@pytest.mark.parametrize("spec", SPECS, ids=_id)
def test_provider_satisfies_abc(spec: Spec) -> None:
    assert isinstance(spec.provider_cls(), AgentProvider)


@pytest.mark.parametrize("spec", SPECS, ids=_id)
def test_provider_self_registers_without_central_edit(spec: Spec) -> None:
    """The pkgutil sweep discovers each new module — no edit to any central list."""
    registry = get_registry()
    assert spec.id in registry.ids()
    assert isinstance(registry.create(spec.id), spec.provider_cls)


# ── serving agents: non-destructive JSON merge (cline / amazonq / opencode) ──


@pytest.mark.parametrize("spec", SERVING_SPECS, ids=_id)
def test_mcp_config_path_is_under_home(spec: Spec, tmp_path: Path) -> None:
    provider = spec.provider_cls.from_home(str(tmp_path))
    assert provider.mcp_config_path == tmp_path.joinpath(*spec.mcp_relpath)


@pytest.mark.parametrize("spec", SERVING_SPECS, ids=_id)
def test_register_creates_config_with_memrelay_entry(spec: Spec, tmp_path: Path) -> None:
    provider = spec.provider_cls.from_home(str(tmp_path))
    path = provider.register()
    assert path == tmp_path.joinpath(*spec.mcp_relpath)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "memrelay" in data[spec.mcp_container]


@pytest.mark.parametrize("spec", SERVING_SPECS, ids=_id)
def test_register_preserves_existing_keys_and_servers(spec: Spec, tmp_path: Path) -> None:
    path = tmp_path.joinpath(*spec.mcp_relpath)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"unrelated": "keep-me", spec.mcp_container: {"other": {"command": "x"}}}),
        encoding="utf-8",
    )
    spec.provider_cls.from_home(str(tmp_path)).register()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["unrelated"] == "keep-me"  # sibling top-level key preserved
    assert data[spec.mcp_container]["other"] == {"command": "x"}  # sibling server kept
    assert "memrelay" in data[spec.mcp_container]  # ours merged in


@pytest.mark.parametrize("spec", SERVING_SPECS, ids=_id)
def test_register_is_idempotent(spec: Spec, tmp_path: Path) -> None:
    provider = spec.provider_cls.from_home(str(tmp_path))
    provider.register()
    first = provider.mcp_config_path.read_text(encoding="utf-8")
    provider.register()
    assert provider.mcp_config_path.read_text(encoding="utf-8") == first


@pytest.mark.parametrize("spec", SERVING_SPECS, ids=_id)
def test_register_refuses_to_clobber_malformed_json(spec: Spec, tmp_path: Path) -> None:
    path = tmp_path.joinpath(*spec.mcp_relpath)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        spec.provider_cls.from_home(str(tmp_path)).register()
    assert path.read_text(encoding="utf-8") == "{not valid json"  # left untouched


@pytest.mark.parametrize("spec", SERVING_SPECS, ids=_id)
def test_mcp_server_entry_spawns_memrelay(spec: Spec) -> None:
    entry = spec.provider_cls().mcp_server_entry()
    # command may be a bare string or (opencode) the head of an argv array.
    assert "memrelay" in json.dumps(entry)


# ── ingest-only agents: the three serving hooks refuse (SPEC §2.1) ───────────


@pytest.mark.parametrize("spec", INGEST_ONLY_SPECS, ids=_id)
def test_ingest_only_serving_hooks_raise(spec: Spec) -> None:
    provider = spec.provider_cls()
    with pytest.raises(NotImplementedError):
        _ = provider.mcp_config_path
    with pytest.raises(NotImplementedError):
        provider.mcp_server_entry()
    with pytest.raises(NotImplementedError):
        provider.register()
