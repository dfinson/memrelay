"""Unit tests for ``cli._resolve_provider`` precedence (#171).

These exercise the CLI resolution seam that both ``init`` and ``observe`` route through,
in isolation from any real ``~/.memrelay`` / agent home. A throwaway
:class:`~memrelay.providers.registry.ProviderRegistry` of fake providers (with fixed
``is_present()`` results) is injected, so the outcome never depends on which agents happen
to be installed on the test machine.

Precedence under test (highest first):

1. explicit ``--copilot-home`` (INV3);
2. a configured ``[llm] host`` naming an *installed* provider (INV2);
3. reference-preferred auto-detect, else the ``DEFAULT_PROVIDER_ID`` fallback (INV1/INV4).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from memrelay.cli import _resolve_provider
from memrelay.config import Config, LLMConfig
from memrelay.providers.base import AgentProvider, LLMStrategyHint
from memrelay.providers.registry import DEFAULT_PROVIDER_ID, ProviderRegistry


def _fake_provider(agent_id: str, *, present: bool) -> type[AgentProvider]:
    """A minimal but complete provider with a fixed ``id`` + ``is_present()``."""

    class _Fake(AgentProvider):
        id = agent_id

        @classmethod
        def from_home(cls, home: str | Path | None = None) -> _Fake:
            return cls()

        def is_present(self) -> bool:
            return present

        def make_source(self, session_id: str | None = None, *, path: Any = None) -> Any:
            return None

        def make_adapter(self, session_id: str) -> Any:
            return None

        def discover_sessions(self) -> Iterable[Any]:
            return []

        def read_raw(self, ref: Any) -> Iterator[Any]:
            return iter(())

        def llm_strategy(self) -> LLMStrategyHint:
            return LLMStrategyHint("local")

        @property
        def mcp_config_path(self) -> Path:
            return Path("nonexistent")

        def mcp_server_entry(self, *, command: str = "memrelay", args: Iterable[str] = ("mcp",)):
            return {}

        def register(self, *, command: str = "memrelay", args: Iterable[str] = ("mcp",)) -> Path:
            return Path("nonexistent")

    return _Fake


def _registry(*providers: type[AgentProvider]) -> ProviderRegistry:
    registry = ProviderRegistry()
    for provider_cls in providers:
        registry.register(provider_cls)
    return registry


def _config(host: str) -> Config:
    return Config(llm=LLMConfig(host=host))


# ── INV2: a configured host that names an installed agent is honored ─────────


def test_configured_host_overrides_reference_preference() -> None:
    """A configured ``[llm] host`` for an *installed* agent beats reference-preference.

    Both amazonq and copilot are present, so bare auto-detect would prefer copilot; but the
    config pins amazonq, and ``observe`` must honor it (so the agent chosen at ``init`` is not
    silently swapped underneath the user — the #171 live smoke).
    """
    registry = _registry(
        _fake_provider("amazonq", present=True),
        _fake_provider(DEFAULT_PROVIDER_ID, present=True),
    )
    resolved = _resolve_provider(None, _config("amazonq"), registry=registry)
    assert resolved.id == "amazonq"


def test_configured_host_ignored_when_agent_absent() -> None:
    """The ``is_present()`` guard: a configured host that isn't installed is NOT honored.

    ``config.llm.host`` defaults to ``"copilot"`` even when unwritten, so honoring it blindly
    would hijack a box where only another agent is installed. Here the configured agent
    (amazonq) is absent, so resolution falls through to reference-preferred detection.
    """
    registry = _registry(
        _fake_provider("amazonq", present=False),
        _fake_provider(DEFAULT_PROVIDER_ID, present=True),
    )
    resolved = _resolve_provider(None, _config("amazonq"), registry=registry)
    assert resolved.id == DEFAULT_PROVIDER_ID


def test_configured_host_unknown_id_falls_through() -> None:
    """A host that matches no registered provider id falls through to auto-detect."""
    registry = _registry(_fake_provider(DEFAULT_PROVIDER_ID, present=True))
    resolved = _resolve_provider(None, _config("not-a-real-provider"), registry=registry)
    assert resolved.id == DEFAULT_PROVIDER_ID


# ── INV3: an explicit --copilot-home overrides everything ────────────────────


def test_explicit_copilot_home_overrides_configured_host() -> None:
    registry = _registry(
        _fake_provider("amazonq", present=True),
        _fake_provider(DEFAULT_PROVIDER_ID, present=True),
    )
    resolved = _resolve_provider("/some/copilot/home", _config("amazonq"), registry=registry)
    assert resolved.id == DEFAULT_PROVIDER_ID


# ── INV1/INV4 through the CLI seam: reference-preference + fallback ───────────


def test_no_config_prefers_reference_when_several_present() -> None:
    registry = _registry(
        _fake_provider("amazonq", present=True),
        _fake_provider(DEFAULT_PROVIDER_ID, present=True),
    )
    assert _resolve_provider(None, None, registry=registry).id == DEFAULT_PROVIDER_ID


def test_default_config_host_does_not_hijack_when_reference_absent() -> None:
    """A default (unwritten) ``host="copilot"`` must not steal an amazonq-only box.

    ``Config()`` carries the ``host="copilot"`` default; with copilot absent the guard skips
    it, and the sole present agent is resolved — no regression for single-agent machines.
    """
    registry = _registry(
        _fake_provider("amazonq", present=True),
        _fake_provider(DEFAULT_PROVIDER_ID, present=False),
    )
    assert _resolve_provider(None, Config(), registry=registry).id == "amazonq"


def test_nothing_detected_falls_back_to_default() -> None:
    registry = _registry(
        _fake_provider("amazonq", present=False),
        _fake_provider(DEFAULT_PROVIDER_ID, present=False),
    )
    assert _resolve_provider(None, None, registry=registry).id == DEFAULT_PROVIDER_ID
