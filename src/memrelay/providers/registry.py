"""Provider registry + auto-detect (SPEC §2.1, E12-S2).

memrelay resolves an :class:`~memrelay.providers.base.AgentProvider` three ways:

* **by explicit id** — :meth:`ProviderRegistry.create` (e.g. the CLI's ``--copilot-home``).
* **by auto-detect** — :meth:`ProviderRegistry.detect` returns every provider whose agent
  is present on this machine (``is_present()``); :meth:`ProviderRegistry.resolve` picks the
  first detected, falling back to :data:`DEFAULT_PROVIDER_ID` when nothing is detected so
  today's Copilot-only behavior is preserved.
* **as the default** — :data:`DEFAULT_PROVIDER_ID` (the reference, zero-key agent).

**Extensibility contract (why the Claude Code PR touches disjoint files).** A provider joins
the registry by decorating its class with :func:`register`::

    from memrelay.providers.registry import register

    @register
    class ClaudeCodeProvider(AgentProvider):
        id = "claude"
        ...

:func:`get_registry` lazily imports every sibling module in ``memrelay.providers`` (via
``pkgutil``), so a brand-new ``providers/claude_code.py`` self-registers with **no edit to any
central list**. ``providers/__init__`` already imports ``copilot``, so the reference provider
is registered even before discovery runs (belt-and-suspenders). Registration is idempotent
(keyed by ``cls.id``), so importing a module twice is harmless.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil

from memrelay.providers.base import AgentProvider

logger = logging.getLogger(__name__)

#: The reference/zero-key provider used as the default when nothing is auto-detected.
DEFAULT_PROVIDER_ID = "copilot"

#: Package submodules that never define a provider (skipped by auto-discovery).
_SKIP_MODULES = frozenset({"base", "registry"})


class ProviderRegistry:
    """A mapping of provider id → :class:`AgentProvider` subclass, with detection.

    Instances are independent, so tests can build a throwaway registry without touching
    the process-wide default one returned by :func:`get_registry`.
    """

    def __init__(self) -> None:
        self._providers: dict[str, type[AgentProvider]] = {}

    def register(self, provider_cls: type[AgentProvider]) -> type[AgentProvider]:
        """Register ``provider_cls`` under its ``id`` (idempotent); returns the class.

        Usable directly (``registry.register(CopilotProvider)``) or as a decorator. The
        class is returned unchanged so ``@registry.register`` works too.
        """
        agent_id = getattr(provider_cls, "id", None)
        if not isinstance(agent_id, str) or not agent_id:
            raise ValueError(f"{provider_cls!r} must define a non-empty str `id` to register")
        self._providers[agent_id] = provider_cls
        return provider_cls

    def ids(self) -> list[str]:
        """Registered provider ids, sorted for deterministic iteration."""
        return sorted(self._providers)

    def create(self, agent_id: str, *, home: str | None = None) -> AgentProvider:
        """Construct the provider registered as ``agent_id`` via its ``from_home``.

        ``home`` overrides the agent home (``None`` → the provider's own default). Raises
        :class:`KeyError` for an unknown id.
        """
        try:
            provider_cls = self._providers[agent_id]
        except KeyError:
            raise KeyError(
                f"no provider registered with id {agent_id!r}; known: {self.ids()}"
            ) from None
        return provider_cls.from_home(home)

    def detect(self) -> list[AgentProvider]:
        """Return a default-constructed provider for every agent present on this machine.

        Each provider is built with its own default home (``from_home()``) and kept when
        ``is_present()`` is True. A provider whose detection raises is skipped (logged),
        never crashing detection for the others.
        """
        present: list[AgentProvider] = []
        for agent_id in self.ids():
            provider = self._providers[agent_id].from_home()
            try:
                detected = provider.is_present()
            except Exception as exc:  # noqa: BLE001 - detection must never crash the sweep
                logger.debug("provider %r detection failed: %s", agent_id, exc)
                continue
            if detected:
                present.append(provider)
        return present

    def resolve(self, agent_id: str | None = None, *, home: str | None = None) -> AgentProvider:
        """Resolve a provider: explicit ``agent_id`` → first auto-detected → default.

        With ``agent_id`` set this is just :meth:`create`. Otherwise the first
        auto-detected provider wins; if none are present, :data:`DEFAULT_PROVIDER_ID` is
        constructed so behavior is unchanged on machines where detection can't see the
        agent home (``home`` is applied to that fallback).
        """
        if agent_id is not None:
            return self.create(agent_id, home=home)
        detected = self.detect()
        if detected:
            return detected[0]
        return self.create(DEFAULT_PROVIDER_ID, home=home)


# ── process-wide default registry ────────────────────────────────────────────

_REGISTRY = ProviderRegistry()
_discovered = False


def register(provider_cls: type[AgentProvider]) -> type[AgentProvider]:
    """Decorator/function: register ``provider_cls`` into the default registry."""
    return _REGISTRY.register(provider_cls)


def _discover() -> None:
    """Import every sibling provider module once so their ``@register`` decorators run."""
    global _discovered
    if _discovered:
        return
    _discovered = True
    import memrelay.providers as pkg

    for info in pkgutil.iter_modules(pkg.__path__):
        if info.name in _SKIP_MODULES:
            continue
        try:
            importlib.import_module(f"{pkg.__name__}.{info.name}")
        except Exception as exc:  # noqa: BLE001 - one bad provider must not break the rest
            logger.warning("failed to import provider module %r: %s", info.name, exc)


def get_registry() -> ProviderRegistry:
    """Return the default registry, having imported all provider modules once."""
    _discover()
    return _REGISTRY
