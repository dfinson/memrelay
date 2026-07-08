"""Agent providers (SPEC §2.1). Copilot CLI is the E0 reference provider."""

from __future__ import annotations

from memrelay.providers.base import AgentProvider, SessionRef
from memrelay.providers.copilot import CopilotProvider

__all__ = ["AgentProvider", "SessionRef", "CopilotProvider"]
