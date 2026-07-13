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


@pytest.fixture(autouse=True)
def _isolate_agent_homes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Pin non-Copilot agent homes to non-existent dirs (deterministic auto-detect).

    Claude Code (#70) and the ten E12-S5 providers each self-register and detect via
    their real on-disk homes, so without this these copilot-centric registry tests would
    leak whatever agents are installed on a dev machine (CI runs clean). Pinning each
    ``MEMRELAY_<AGENT>_HOME`` at an empty path keeps ``detect``/``resolve`` copilot-only,
    without touching the frozen base/registry/copilot source.
    """
    monkeypatch.setenv("MEMRELAY_CLAUDE_HOME", str(tmp_path / "_no_claude_home"))
    # E12-S5: the ten new providers also auto-detect via their real homes; pin each
    # away so ``detect()``/``resolve()`` stay copilot-only on a dev box with some installed.
    for env_var in (
        "MEMRELAY_CODEX_HOME",
        "MEMRELAY_CONTINUE_HOME",
        "MEMRELAY_CLINE_HOME",
        "MEMRELAY_AIDER_HOME",
        "MEMRELAY_AMAZONQ_HOME",
        "MEMRELAY_GOOSE_HOME",
        "MEMRELAY_OPENCODE_HOME",
        "MEMRELAY_OPENHANDS_HOME",
        "MEMRELAY_SWEAGENT_HOME",
        "MEMRELAY_ANTIGRAVITY_HOME",
    ):
        monkeypatch.setenv(env_var, str(tmp_path / f"_no_home_{env_var.lower()}"))


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


# ── reference-preferred auto-detect (#171: no alphabetical-first surprise) ────
#
# These are hermetic: a throwaway ``ProviderRegistry`` + fake providers with fixed
# ``is_present()`` results, so the outcome never depends on which agents are installed on
# the test machine (the founder's box has BOTH ~/.aws/amazonq and ~/.copilot, which is what
# exposed the bug live).


def _fixed_presence_provider(agent_id: str, *, present: bool) -> type[_CompleteProvider]:
    """A complete provider with a fixed ``id`` + ``is_present()`` for detection tests."""

    class _Fixed(_CompleteProvider):
        id = agent_id

        def is_present(self) -> bool:
            return present

    return _Fixed


def test_resolve_prefers_reference_over_alphabetically_earlier_agent() -> None:
    """The reference provider wins over an alphabetically-earlier detected agent (#171).

    ``detect()`` iterates ``sorted(ids)``, so ``"amazonq"`` is detected *before* ``"copilot"``;
    the old ``resolve()`` returned ``detected[0]`` and thus picked amazonq. Now copilot wins.
    (Reverting the precedence change flips this assertion back to ``"amazonq"`` — the
    counterfactual guard.)
    """
    registry = ProviderRegistry()
    registry.register(_fixed_presence_provider("amazonq", present=True))
    registry.register(_fixed_presence_provider(DEFAULT_PROVIDER_ID, present=True))

    # amazonq really is detected first (alphabetical) ...
    assert [p.id for p in registry.detect()] == ["amazonq", DEFAULT_PROVIDER_ID]
    # ... but the reference provider is the one resolve() selects.
    assert registry.resolve().id == DEFAULT_PROVIDER_ID


def test_resolve_picks_sole_present_agent_when_reference_absent() -> None:
    """No regression: with the reference absent, the sole present agent still wins.

    This is the guard against a naive "always copilot" fix — precedence must fall through to
    the deterministic first-detected provider when the reference isn't present.
    """
    registry = ProviderRegistry()
    registry.register(_fixed_presence_provider("amazonq", present=True))
    registry.register(_fixed_presence_provider(DEFAULT_PROVIDER_ID, present=False))

    assert registry.resolve().id == "amazonq"


def test_resolve_returns_first_detected_when_reference_absent_and_multiple_present() -> None:
    """Safety case: reference-preference must NOT become "force copilot/DEFAULT".

    With copilot absent but several other agents present, ``resolve()`` returns the
    deterministic first-detected provider (``sorted`` ⇒ ``detected[0]``), never the DEFAULT.
    This pins that "prefer copilot when present" did not regress into "always DEFAULT".
    """
    registry = ProviderRegistry()
    registry.register(_fixed_presence_provider("amazonq", present=True))
    registry.register(_fixed_presence_provider("codex", present=True))
    registry.register(_fixed_presence_provider(DEFAULT_PROVIDER_ID, present=False))

    assert [p.id for p in registry.detect()] == ["amazonq", "codex"]
    resolved = registry.resolve()
    assert resolved.id == "amazonq"
    assert resolved.id != DEFAULT_PROVIDER_ID


def test_resolve_falls_back_to_default_when_nothing_detected() -> None:
    """INV4 unchanged: with nothing present, the DEFAULT provider is constructed."""
    registry = ProviderRegistry()
    registry.register(_fixed_presence_provider("amazonq", present=False))
    registry.register(_fixed_presence_provider(DEFAULT_PROVIDER_ID, present=False))

    assert registry.detect() == []
    assert registry.resolve().id == DEFAULT_PROVIDER_ID


def test_resolve_explicit_id_wins_over_present_reference() -> None:
    """An explicit ``agent_id`` overrides auto-detection even when the reference is present."""
    registry = ProviderRegistry()
    registry.register(_fixed_presence_provider("amazonq", present=True))
    registry.register(_fixed_presence_provider(DEFAULT_PROVIDER_ID, present=True))

    assert registry.resolve("amazonq").id == "amazonq"


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


# ── auto-discovery fails loud (a broken provider must NOT hide behind a green sweep) ─


def _write_pkg(root: Path, name: str, modules: dict[str, str]) -> object:
    """Create an importable package ``name`` under ``root`` with the given submodules."""
    import importlib

    pkg_dir = root / name
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("", encoding="utf-8")
    for mod_name, body in modules.items():
        (pkg_dir / f"{mod_name}.py").write_text(body, encoding="utf-8")
    return importlib.import_module(name)


def test_discovery_surfaces_import_errors(tmp_path: Path, monkeypatch) -> None:
    """A provider module that fails to import must raise, not be logged-and-skipped."""
    from memrelay.providers import registry

    monkeypatch.syspath_prepend(str(tmp_path))
    pkg = _write_pkg(tmp_path, "brokenprov", {"boom": "raise ImportError('boom')\n"})

    with pytest.raises(ImportError):
        registry._import_provider_modules(pkg)


def test_discovery_imports_every_submodule(tmp_path: Path, monkeypatch) -> None:
    """The happy path imports each submodule (so their ``@register`` decorators run)."""
    import importlib

    from memrelay.providers import registry

    monkeypatch.syspath_prepend(str(tmp_path))
    pkg = _write_pkg(tmp_path, "goodprov", {"mod_a": "IMPORTED = True\n"})

    registry._import_provider_modules(pkg)  # must not raise

    assert importlib.import_module("goodprov.mod_a").IMPORTED is True
