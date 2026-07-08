"""Agent provider abstraction (SPEC §2.1) — the memrelay ↔ agent seam.

An :class:`AgentProvider` supplies everything memrelay needs for *one* agent, split
into the three SPEC §2.1 responsibilities:

1. **Source + mapping** — the traceforge ``Source`` + ``Adapter`` that normalize the
   agent's raw trace to ``SessionEvent`` (:meth:`~AgentProvider.make_source`,
   :meth:`~AgentProvider.make_adapter`, :meth:`~AgentProvider.discover_sessions`,
   :meth:`~AgentProvider.read_raw`).
2. **LLM strategy** — how Graphiti's extraction LLM is satisfied, *advertised* as a
   lightweight :class:`LLMStrategyHint` (:meth:`~AgentProvider.llm_strategy`). This is
   pure metadata; it does **not** build graphiti ``LLMClient``s — that lives in
   ``memrelay.engine.llm`` and is intentionally **not** duplicated here. The hint's
   ``strategy``/``host`` map directly onto ``config.llm.{strategy,host}``.
3. **Serving / registration** — how the agent discovers the memrelay MCP server
   (:attr:`~AgentProvider.mcp_config_path`, :meth:`~AgentProvider.mcp_server_entry`,
   :meth:`~AgentProvider.register`).

Everything below ``SessionEvent`` (episode assembly, spool, graph, retrieval) is written
once, agent-agnostic.

**Frozen cross-session contract.** A *separate* session implements the second provider
(Claude Code, #70) against this interface, so the method names + signatures below are a
contract. :class:`AgentProvider` is an :class:`abc.ABC` (not a ``Protocol``) precisely so
conformance is enforced at instantiation: a subclass that omits any method cannot be
constructed or registered. A provider is added to the registry with the
``@memrelay.providers.registry.register`` decorator and built through
:meth:`~AgentProvider.from_home`; see ``registry.py``.

Note: traceforge 0.1.0 ships **no** ``SessionRef`` type (SPEC §3.1 implies one), so
memrelay defines its own lightweight :class:`SessionRef` here (see ``docs/e0-spike.md``
delta #5).
"""

from __future__ import annotations

import abc
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any


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


@dataclass(frozen=True)
class LLMStrategyHint:
    """An agent's *default* LLM strategy, advertised as plain metadata (SPEC §2.1 #2).

    This is deliberately **not** an engine ``LLMStrategy`` (which builds graphiti
    ``LLMClient``s in ``memrelay.engine.llm``). A provider only *advertises* which
    key-less-friendly default fits its agent; the engine remains the single owner of
    client construction and the ``config.llm`` fallback chain. The two fields map
    one-to-one onto :class:`memrelay.config.LLMConfig`:

    Attributes:
        strategy: ``"borrow-host"`` | ``"byo-key"`` | ``"local"`` (→ ``config.llm.strategy``).
        host: for ``borrow-host`` only, the host CLI command whose model is borrowed
            (e.g. ``"copilot"``) (→ ``config.llm.host``); ``None`` for strategies that
            don't borrow a host.
    """

    strategy: str
    host: str | None = None


class AgentProvider(abc.ABC):
    """The single abstraction memrelay ingests from and serves to (SPEC §2.1).

    See the module docstring for the responsibility breakdown and the cross-session
    contract note. Concrete providers set :attr:`id`, are decorated with
    ``@registry.register``, and are constructed via :meth:`from_home`.
    """

    #: Stable provider id, e.g. ``"copilot"``, ``"claude"``. Also the registry key.
    id: str

    # ── construction / detection (registry seam) ─────────────────────────────

    @classmethod
    @abc.abstractmethod
    def from_home(cls, home: str | Path | None = None) -> AgentProvider:
        """Build a provider, resolving its agent home.

        ``home`` overrides the agent's default location; ``None`` means "resolve the
        default" (a provider may consult its own env var, e.g. Copilot reads
        ``MEMRELAY_COPILOT_HOME`` before falling back to ``~/.copilot``). This is the
        uniform constructor the registry uses so heterogeneous ``__init__`` signatures
        stay private to each provider.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def is_present(self) -> bool:
        """Return True if this agent appears installed/active on this machine.

        Drives auto-detection (SPEC §2.1). Must be cheap (a filesystem check) and must
        not raise for a merely-absent agent — e.g. Copilot checks whether
        ``~/.copilot/session-state`` exists.
        """
        raise NotImplementedError

    # ── (1) source + mapping ─────────────────────────────────────────────────

    @abc.abstractmethod
    def make_source(self, session_id: str | None = None, *, path: str | Path | None = None) -> Any:
        """Return the traceforge ``Source`` that reads this agent's trace.

        Scoped by ``session_id`` (resolved under the agent's home) or an explicit
        ``path``. Yields raw records ready for :meth:`make_adapter`'s ``parse``.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def make_adapter(self, session_id: str) -> Any:
        """Return a traceforge ``Adapter`` (``MappedJsonAdapter`` + optional pre-parser)."""
        raise NotImplementedError

    @abc.abstractmethod
    def discover_sessions(self) -> Iterable[SessionRef]:
        """Enumerate sessions currently present for this agent."""
        raise NotImplementedError

    @abc.abstractmethod
    def read_raw(self, ref: SessionRef) -> Iterator[Any]:
        """Yield raw records for ``ref``, ready to feed the adapter."""
        raise NotImplementedError

    # ── (2) LLM strategy ─────────────────────────────────────────────────────

    @abc.abstractmethod
    def llm_strategy(self) -> LLMStrategyHint:
        """Advertise this agent's default :class:`LLMStrategyHint` (metadata only)."""
        raise NotImplementedError

    # ── (3) serving / registration ───────────────────────────────────────────

    @property
    @abc.abstractmethod
    def mcp_config_path(self) -> Path:
        """Path to the agent's MCP registry file that :meth:`register` writes."""
        raise NotImplementedError

    @abc.abstractmethod
    def mcp_server_entry(
        self, *, command: str = "memrelay", args: Iterable[str] = ("mcp",)
    ) -> dict[str, Any]:
        """The stdio ``memrelay mcp`` server entry this agent should spawn."""
        raise NotImplementedError

    @abc.abstractmethod
    def register(self, *, command: str = "memrelay", args: Iterable[str] = ("mcp",)) -> Path:
        """Merge the memrelay MCP server into the agent's config; return the path written."""
        raise NotImplementedError
