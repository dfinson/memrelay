"""Unit tests for the provider registry + auto-detect (E12-S2).

These are **real** tests, not fakes: they resolve, detect, and construct the actual
:class:`~memrelay.providers.copilot.CopilotProvider` from a real ``~/.copilot``-style
layout under ``tmp_path`` (via the ``MEMRELAY_COPILOT_HOME`` env var the provider's
``from_home`` honors), and they assert the ABC contract that a *second* provider (the
later Claude Code session, #70) must satisfy.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from memrelay.providers import CopilotProvider
from memrelay.providers.base import AgentProvider, LLMStrategyHint
from memrelay.providers.registry import (
    DEFAULT_PROVIDER_ID,
    ProviderRegistry,
    get_registry,
)

# ── helpers: a complete + an incomplete provider for contract tests ──────────


class _CompleteProvider(AgentProvider):
    """A minimal but *complete* provider — proves the ABC can be satisfied by a 2nd agent."""

    id = "throwaway"

    @classmethod
    def from_home(cls, home: str | Path | None = None) -> _CompleteProvider:
        inst = cls()
        inst.home = home
        return inst

    def is_present(self) -> bool:
        return True

    def make_source(self, session_id=None, *, path=None):
        return None

    def make_adapter(self, session_id):
        return None

    def discover_sessions(self):
        return []

    def read_raw(self, ref):
        return iter(())

    def llm_strategy(self) -> LLMStrategyHint:
        return LLMStrategyHint("local")

    @property
    def mcp_config_path(self) -> Path:
        return Path("nonexistent")

    def mcp_server_entry(self, *, command="memrelay", args=("mcp",)):
        return {}

    def register(self, *, command="memrelay", args=("mcp",)) -> Path:
        return Path("nonexistent")


class _IncompleteProvider(AgentProvider):
    """Missing most abstractmethods — must be un-instantiable (contract enforcement)."""

    id = "incomplete"

    def is_present(self) -> bool:
        return True


def _make_copilot_home(root: Path, session_ids: tuple[str, ...] = ()) -> Path:
    """Build a real ``~/.copilot`` layout with ``session-state/<id>/events.jsonl``."""
    for sid in session_ids:
        d = root / "session-state" / sid
        d.mkdir(parents=True)
        (d / "events.jsonl").write_text("{}\n", encoding="utf-8")
    return root


# ── the default registry resolves the real CopilotProvider ───────────────────


def test_default_registry_has_copilot() -> None:
    registry = get_registry()
    assert DEFAULT_PROVIDER_ID == "copilot"
    assert "copilot" in registry.ids()


def test_create_returns_real_copilot_provider(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MEMRELAY_COPILOT_HOME", str(tmp_path / "copilot"))
    provider = get_registry().create("copilot")
    assert isinstance(provider, CopilotProvider)
    assert provider.id == "copilot"


def test_create_honors_explicit_home(tmp_path: Path) -> None:
    """``home=`` targets a real layout — proved by real ``discover_sessions``."""
    home = _make_copilot_home(tmp_path / ".copilot", ("aaa", "bbb"))
    provider = get_registry().create("copilot", home=str(home))
    refs = list(provider.discover_sessions())
    assert [r.session_id for r in refs] == ["aaa", "bbb"]
    assert all(r.agent_id == "copilot" for r in refs)


def test_create_unknown_id_raises() -> None:
    with pytest.raises(KeyError):
        get_registry().create("nope")


# ── auto-detect against a real filesystem layout ─────────────────────────────


def test_detect_finds_copilot_when_present(monkeypatch, tmp_path: Path) -> None:
    home = _make_copilot_home(tmp_path / "copilot", ("sess-1",))
    monkeypatch.setenv("MEMRELAY_COPILOT_HOME", str(home))

    detected = get_registry().detect()

    assert [p.id for p in detected] == ["copilot"]
    assert isinstance(detected[0], CopilotProvider)


def test_detect_empty_when_absent(monkeypatch, tmp_path: Path) -> None:
    """A home with no ``session-state`` dir must not be detected."""
    empty = tmp_path / "copilot"
    empty.mkdir()
    monkeypatch.setenv("MEMRELAY_COPILOT_HOME", str(empty))

    assert get_registry().detect() == []


def test_resolve_prefers_detected(monkeypatch, tmp_path: Path) -> None:
    home = _make_copilot_home(tmp_path / "copilot", ("sess-1",))
    monkeypatch.setenv("MEMRELAY_COPILOT_HOME", str(home))

    provider = get_registry().resolve()

    assert isinstance(provider, CopilotProvider)
    assert provider.is_present()


def test_resolve_falls_back_to_default_when_nothing_present(monkeypatch, tmp_path: Path) -> None:
    empty = tmp_path / "copilot"
    empty.mkdir()
    monkeypatch.setenv("MEMRELAY_COPILOT_HOME", str(empty))

    # Nothing is detected, so resolve() must still return the default provider.
    provider = get_registry().resolve()

    assert isinstance(provider, CopilotProvider)
    assert not provider.is_present()


def test_resolve_explicit_id_and_home(tmp_path: Path) -> None:
    home = _make_copilot_home(tmp_path / ".copilot", ("only",))
    provider = get_registry().resolve("copilot", home=str(home))
    assert [r.session_id for r in provider.discover_sessions()] == ["only"]


# ── LLM-strategy advertisement (responsibility 2) ────────────────────────────


def test_copilot_llm_strategy_hint(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MEMRELAY_COPILOT_HOME", str(tmp_path / "copilot"))
    hint = get_registry().create("copilot").llm_strategy()
    assert hint == LLMStrategyHint(strategy="borrow-host", host="copilot")


# ── ABC conformance: the cross-session contract is enforced ──────────────────


def test_abstract_base_cannot_instantiate() -> None:
    with pytest.raises(TypeError):
        AgentProvider()  # type: ignore[abstract]


def test_incomplete_provider_cannot_instantiate() -> None:
    """A subclass that omits abstractmethods raises at construction (contract guard)."""
    with pytest.raises(TypeError):
        _IncompleteProvider()  # type: ignore[abstract]


def test_copilot_is_subclass_of_agent_provider() -> None:
    assert issubclass(CopilotProvider, AgentProvider)
    assert isinstance(CopilotProvider(), AgentProvider)


# ── registration mechanism (how the Claude Code PR joins with no central edit) ─


def test_register_decorator_on_fresh_registry() -> None:
    """``@registry.register`` makes a brand-new provider resolvable — the seam #70 uses."""
    registry = ProviderRegistry()

    @registry.register
    class _Local(_CompleteProvider):
        id = "local-agent"

    assert "local-agent" in registry.ids()
    created = registry.create("local-agent")
    assert isinstance(created, _Local)


def test_register_returns_class_unchanged() -> None:
    """The decorator returns the class object unchanged (so ``@register`` is transparent)."""
    registry = ProviderRegistry()
    returned = registry.register(_CompleteProvider)
    assert returned is _CompleteProvider


def test_module_level_register_is_idempotent() -> None:
    """Re-registering the same id twice is harmless (keyed by ``cls.id``)."""
    registry = ProviderRegistry()
    registry.register(_CompleteProvider)
    registry.register(_CompleteProvider)
    assert registry.ids().count("throwaway") == 1


def test_register_requires_non_empty_id() -> None:
    registry = ProviderRegistry()

    class _NoId(_CompleteProvider):
        id = ""

    with pytest.raises(ValueError):
        registry.register(_NoId)
