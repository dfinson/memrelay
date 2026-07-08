"""Agent providers (SPEC §2.1). Copilot CLI is the reference provider.

The :class:`~memrelay.providers.base.AgentProvider` interface (source+mapping, LLM strategy,
serving/registration) plus the :mod:`~memrelay.providers.registry` (``@register`` +
``get_registry`` auto-detect) form the seam a second agent (e.g. Claude Code) plugs into.
"""

from __future__ import annotations

from memrelay.providers.base import AgentProvider, LLMStrategyHint, SessionRef
from memrelay.providers.copilot import CopilotProvider
from memrelay.providers.registry import (
    DEFAULT_PROVIDER_ID,
    ProviderRegistry,
    get_registry,
    register,
)

__all__ = [
    "DEFAULT_PROVIDER_ID",
    "AgentProvider",
    "CopilotProvider",
    "LLMStrategyHint",
    "ProviderRegistry",
    "SessionRef",
    "get_registry",
    "register",
]
