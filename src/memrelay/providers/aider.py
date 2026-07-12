"""Aider provider — ingest-only (SPEC §2.1, E12-S5 #71).

Aider records an append-only analytics event log (its ``--analytics-log`` file, default
``~/.aider/analytics.jsonl``). Each line is one event ``{event, properties?, time}`` —
``launched``, ``message_send_starting``, ``message_send``, ``exit`` and friends. The
installed traceforge ``aider.yaml`` mapping reads these **directly** (no preprocessor;
``type`` discriminator is the ``event`` field, timestamp is ``time``), so
:meth:`make_adapter` is a single ``MappedJsonAdapter.from_yaml("aider.yaml", session_id)``.

**Serving:** Aider is not an MCP host, so it is **ingest-only** — the MCP hooks below raise
and no config is ever mutated.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Iterator
from importlib import resources
from pathlib import Path
from typing import Any

from memrelay.providers.base import AgentProvider, LLMStrategyHint, SessionRef
from memrelay.providers.registry import register

#: The single traceforge mapping for Aider (no preprocessor; direct field mapping).
AIDER_MAPPING = "aider.yaml"

DEFAULT_AIDER_HOME = "~/.aider"
ANALYTICS_FILENAME = "analytics.jsonl"

#: Env var that overrides the Aider home (mirrors ``MEMRELAY_COPILOT_HOME``).
AIDER_HOME_ENV = "MEMRELAY_AIDER_HOME"

#: Aider has no key-less host-borrow path in memrelay today; advertise the honest default.
LLM_STRATEGY = "byo-key"


def mapping_path(name: str) -> str:
    """Resolve a packaged traceforge mapping YAML to a filesystem path (see reference providers)."""
    resource = resources.files("traceforge.mappings").joinpath(name)
    return str(resource)


class AiderSource:
    """A replay source: iterate an analytics log as raw JSONL lines.

    Blank lines are skipped; each yielded line is a JSON object string ready for
    :meth:`make_adapter`'s ``parse``.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def __iter__(self) -> Iterator[str]:
        with open(self.path, encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip()
                if stripped:
                    yield stripped


@register
class AiderProvider(AgentProvider):
    """:class:`~memrelay.providers.base.AgentProvider` for Aider."""

    id = "aider"

    def __init__(self, aider_home: str | Path = DEFAULT_AIDER_HOME) -> None:
        self.aider_home = Path(aider_home).expanduser()

    # ── construction / detection (registry seam) ─────────────────────────────

    @classmethod
    def from_home(cls, home: str | Path | None = None) -> AiderProvider:
        """Build a provider, resolving the Aider home (``MEMRELAY_AIDER_HOME`` → ``~/.aider``)."""
        if home is None:
            home = os.environ.get(AIDER_HOME_ENV) or DEFAULT_AIDER_HOME
        return cls(home)

    def is_present(self) -> bool:
        """True when the analytics log exists (Aider has run and logged in this home)."""
        return self.analytics_log.is_file()

    # ── discovery ────────────────────────────────────────────────────────────

    @property
    def analytics_log(self) -> Path:
        return self.aider_home / ANALYTICS_FILENAME

    def discover_sessions(self) -> Iterable[SessionRef]:
        """Yield one :class:`SessionRef` for the analytics log, if present.

        Aider appends every run's events to a single analytics log rather than one file per
        session, so discovery surfaces that log as a single ref (session id = file stem).
        """
        log = self.analytics_log
        if log.is_file():
            yield SessionRef(session_id=log.stem, agent_id=self.id, path=str(log))

    # ── (1) source + mapping ─────────────────────────────────────────────────

    def make_adapter(self, session_id: str) -> Any:
        """Build the ``aider.yaml`` adapter scoped to ``session_id`` (no preprocessor)."""
        from traceforge import MappedJsonAdapter

        return MappedJsonAdapter.from_yaml(mapping_path(AIDER_MAPPING), session_id)

    def read_raw(self, ref: SessionRef) -> Iterator[str]:
        """Yield raw JSONL lines from the analytics log; each line feeds ``adapter.parse``."""
        path = Path(ref.path) if ref.path else self.analytics_log
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield line

    def make_source(
        self, session_id: str | None = None, *, path: str | Path | None = None
    ) -> AiderSource:
        """Return a replay :class:`AiderSource` over an analytics log.

        Scope it by an explicit ``path`` or by ``session_id`` (resolved to the home's
        ``analytics.jsonl``).
        """
        if path is not None:
            log_path = Path(path)
        elif session_id is not None:
            log_path = self.analytics_log
        else:
            raise ValueError("make_source requires a session_id or an explicit path")
        return AiderSource(log_path)

    # ── (2) LLM strategy ─────────────────────────────────────────────────────

    def llm_strategy(self) -> LLMStrategyHint:
        """Advertise Aider's default: bring-your-own-key (metadata only)."""
        return LLMStrategyHint(strategy=LLM_STRATEGY, host=None)

    # ── (3) serving / registration — ingest-only ─────────────────────────────

    @property
    def mcp_config_path(self) -> Path:
        """Aider is ingest-only in memrelay (Aider is not an MCP host)."""
        raise NotImplementedError("aider is ingest-only: Aider is not an MCP host")

    def mcp_server_entry(
        self, *, command: str = "memrelay", args: Iterable[str] = ("mcp",)
    ) -> dict[str, Any]:
        raise NotImplementedError("aider is ingest-only: memrelay does not serve MCP to it")

    def register(self, *, command: str = "memrelay", args: Iterable[str] = ("mcp",)) -> Path:
        raise NotImplementedError("aider is ingest-only: memrelay does not register MCP for it")
