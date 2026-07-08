"""Agent provider abstraction (SPEC §2.1).

An :class:`AgentProvider` supplies three things for one agent: a traceforge
``Source`` + ``Adapter`` (normalization to ``SessionEvent``), an LLM strategy, and
MCP registration. Everything below ``SessionEvent`` is agent-agnostic.

E0 exercises only the *source + mapping* responsibility for Copilot CLI (the
reference provider); ``llm_strategy`` / ``register`` land in later epics.

Note: traceforge 0.1.0 ships **no** ``SessionRef`` type (SPEC §3.1 implies one),
so memrelay defines its own lightweight :class:`SessionRef` here. See
``docs/e0-spike.md`` (delta #5).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class SessionRef:
    """A discovered agent session (memrelay-owned; traceforge has no equivalent).

    Attributes:
        session_id: the agent's own session identifier (drives mapping scoping).
        agent_id: provider id, e.g. ``"copilot"``.
        path: on-disk location of the session's raw trace (a file or DB), when the
            source is file/sqlite based.
    """

    session_id: str
    agent_id: str
    path: str | None = None


@runtime_checkable
class AgentProvider(Protocol):
    """The single abstraction memrelay ingests from and serves to (SPEC §2.1)."""

    id: str

    def make_source(self) -> Any:
        """Return the traceforge ``Source`` that reads this agent's trace."""
        ...

    def make_adapter(self, session_id: str) -> Any:
        """Return a traceforge ``Adapter`` (``MappedJsonAdapter`` + optional pre-parser)."""
        ...

    def discover_sessions(self) -> Iterable[SessionRef]:
        """Enumerate sessions currently present for this agent."""
        ...

    def read_raw(self, ref: SessionRef) -> Iterator[Any]:
        """Yield raw records for ``ref``, ready to feed the adapter."""
        ...
