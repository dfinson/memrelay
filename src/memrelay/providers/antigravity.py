"""Antigravity provider — ingest-only (SPEC §2.1, E12-S5 #71).

Antigravity's agent SDK records a conversation history as JSONL — one ``Step`` per line
(``{type, source?, content?, thinking?, tool_calls?, step_index}``). The installed
traceforge ``antigravity.yaml`` mapping declares ``preprocessor: antigravity`` (auto-applied
inside ``MappedJsonAdapter.parse``), so :meth:`make_adapter` is a single
``MappedJsonAdapter.from_yaml("antigravity.yaml", session_id)``.

**Serving:** Antigravity exposes no standard memrelay-writable MCP registry, so it is
**ingest-only** — the MCP hooks below raise and no config is ever mutated.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Iterator
from importlib import resources
from pathlib import Path
from typing import Any

from memrelay.providers.base import AgentProvider, LLMStrategyHint, SessionRef
from memrelay.providers.registry import register

#: The single traceforge mapping for Antigravity; its ``preprocessor: antigravity`` auto-applies.
ANTIGRAVITY_MAPPING = "antigravity.yaml"

DEFAULT_ANTIGRAVITY_HOME = "~/.antigravity"
SESSIONS_DIR = "sessions"
SESSION_GLOB = "*.jsonl"

#: Env var that overrides the Antigravity home (mirrors ``MEMRELAY_COPILOT_HOME``).
ANTIGRAVITY_HOME_ENV = "MEMRELAY_ANTIGRAVITY_HOME"

#: Antigravity has no key-less host-borrow path in memrelay today; advertise the honest default.
LLM_STRATEGY = "byo-key"


def mapping_path(name: str) -> str:
    """Resolve a packaged traceforge mapping YAML to a filesystem path (see reference providers)."""
    resource = resources.files("traceforge.mappings").joinpath(name)
    return str(resource)


class AntigravitySource:
    """A replay source: iterate a conversation history as raw JSONL ``Step`` lines.

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
class AntigravityProvider(AgentProvider):
    """:class:`~memrelay.providers.base.AgentProvider` for Antigravity."""

    id = "antigravity"

    def __init__(self, antigravity_home: str | Path = DEFAULT_ANTIGRAVITY_HOME) -> None:
        self.antigravity_home = Path(antigravity_home).expanduser()

    # ── construction / detection (registry seam) ─────────────────────────────

    @classmethod
    def from_home(cls, home: str | Path | None = None) -> AntigravityProvider:
        """Build a provider, resolving the home (env ``MEMRELAY_ANTIGRAVITY_HOME`` or default)."""
        if home is None:
            home = os.environ.get(ANTIGRAVITY_HOME_ENV) or DEFAULT_ANTIGRAVITY_HOME
        return cls(home)

    def is_present(self) -> bool:
        """True when ``~/.antigravity/sessions`` exists (Antigravity has recorded here)."""
        return self.sessions_root.is_dir()

    # ── discovery ────────────────────────────────────────────────────────────

    @property
    def sessions_root(self) -> Path:
        return self.antigravity_home / SESSIONS_DIR

    def discover_sessions(self) -> Iterable[SessionRef]:
        """Yield a :class:`SessionRef` per conversation ``*.jsonl`` (session id = file stem)."""
        root = self.sessions_root
        if not root.is_dir():
            return
        for log in sorted(root.glob(SESSION_GLOB)):
            if log.is_file():
                yield SessionRef(session_id=log.stem, agent_id=self.id, path=str(log))

    def _resolve_session_path(self, session_id: str) -> Path | None:
        candidate = self.sessions_root / f"{session_id}.jsonl"
        return candidate if candidate.is_file() else None

    # ── (1) source + mapping ─────────────────────────────────────────────────

    def make_adapter(self, session_id: str) -> Any:
        """Build the ``antigravity.yaml`` adapter for ``session_id`` (preprocessor runs)."""
        from traceforge import MappedJsonAdapter

        return MappedJsonAdapter.from_yaml(mapping_path(ANTIGRAVITY_MAPPING), session_id)

    def read_raw(self, ref: SessionRef) -> Iterator[str]:
        """Yield raw JSONL ``Step`` lines from a conversation; each feeds ``adapter.parse``."""
        if ref.path:
            path: Path | None = Path(ref.path)
        else:
            path = self._resolve_session_path(ref.session_id)
        if path is None:
            raise FileNotFoundError(f"no Antigravity history found for {ref.session_id!r}")
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield line

    def make_source(
        self, session_id: str | None = None, *, path: str | Path | None = None
    ) -> AntigravitySource:
        """Return a replay :class:`AntigravitySource` over a conversation history.

        Scope it by an explicit ``path`` or by ``session_id`` (resolved under
        ``~/.antigravity/sessions/<id>.jsonl``).
        """
        if path is not None:
            log_path: Path | None = Path(path)
        elif session_id is not None:
            log_path = self._resolve_session_path(session_id)
            if log_path is None:
                raise FileNotFoundError(f"no Antigravity history found for {session_id!r}")
        else:
            raise ValueError("make_source requires a session_id or an explicit path")
        return AntigravitySource(log_path)

    # ── (2) LLM strategy ─────────────────────────────────────────────────────

    def llm_strategy(self) -> LLMStrategyHint:
        """Advertise Antigravity's default: bring-your-own-key (metadata only)."""
        return LLMStrategyHint(strategy=LLM_STRATEGY, host=None)

    # ── (3) serving / registration — ingest-only ─────────────────────────────

    @property
    def mcp_config_path(self) -> Path:
        """Antigravity is ingest-only in memrelay (no standard memrelay-writable MCP registry)."""
        raise NotImplementedError(
            "antigravity is ingest-only: no standard memrelay-writable MCP registry"
        )

    def mcp_server_entry(
        self, *, command: str = "memrelay", args: Iterable[str] = ("mcp",)
    ) -> dict[str, Any]:
        raise NotImplementedError("antigravity is ingest-only: memrelay does not serve MCP to it")

    def register(self, *, command: str = "memrelay", args: Iterable[str] = ("mcp",)) -> Path:
        raise NotImplementedError(
            "antigravity is ingest-only: memrelay does not register MCP for it"
        )
