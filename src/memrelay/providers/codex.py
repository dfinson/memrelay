"""Codex CLI provider (OpenAI Codex) — ingest-only (SPEC §2.1, E12-S5 #71).

OpenAI's Codex CLI records each run as a JSONL *rollout* under its home::

    ~/.codex/sessions/**/rollout-*.jsonl        (override: ``$CODEX_HOME``)

Each line is one wire record ``{timestamp, type, payload:{...}}`` where ``type`` is
``event_msg`` (user/agent turns) or ``response_item`` (function calls + outputs). The
installed traceforge ``codex.yaml`` mapping declares ``preprocessor: codex`` (auto-applied
inside ``MappedJsonAdapter.parse``), so :meth:`make_adapter` is a **single**
``MappedJsonAdapter.from_yaml("codex.yaml", session_id)`` — no memrelay parsing code.

**Serving:** Codex's MCP registry is TOML (``~/.codex/config.toml``). memrelay cannot write
TOML without a new dependency (Python 3.12 ``tomllib`` is read-only), so Codex is wired
**ingest-only** for now — the MCP hooks below raise, and no config is ever mutated. Ingest
+ recall are fully functional; only self-registration is deferred.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Iterator
from importlib import resources
from pathlib import Path
from typing import Any

from memrelay.providers.base import AgentProvider, LLMStrategyHint, SessionRef
from memrelay.providers.registry import register

#: The single traceforge mapping for Codex; its ``preprocessor: codex`` is auto-applied.
CODEX_MAPPING = "codex.yaml"

DEFAULT_CODEX_HOME = "~/.codex"
SESSIONS_DIR = "sessions"
SESSION_GLOB = "rollout-*.jsonl"

#: Env var that overrides the Codex home (mirrors ``MEMRELAY_COPILOT_HOME``) so
#: auto-detect is testable without touching a real ``~/.codex``.
CODEX_HOME_ENV = "MEMRELAY_CODEX_HOME"

#: Codex has no key-less host-borrow path in memrelay's engine today, so it advertises the
#: honest bring-your-own-key default (metadata only; maps onto ``config.llm.strategy``).
LLM_STRATEGY = "byo-key"


def mapping_path(name: str) -> str:
    """Resolve a packaged traceforge mapping YAML to a filesystem path.

    traceforge's ``from_yaml`` wants a real path and ``traceforge.mappings`` has no
    name→path resolver, so we locate the file via ``importlib.resources`` (mirrors the
    reference providers; the mapping ships inside the installed traceforge package).
    """
    resource = resources.files("traceforge.mappings").joinpath(name)
    return str(resource)


class CodexSource:
    """A replay source: iterate a session's ``rollout-*.jsonl`` as raw JSONL lines.

    Mirrors :meth:`CodexProvider.read_raw` but as a reusable, session-scoped iterable —
    the seam ``memrelay observe`` drives. Blank lines are skipped; each yielded line is a
    JSON object string ready for :meth:`make_adapter`'s ``parse``.
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
class CodexProvider(AgentProvider):
    """:class:`~memrelay.providers.base.AgentProvider` for the OpenAI Codex CLI."""

    id = "codex"

    def __init__(self, codex_home: str | Path = DEFAULT_CODEX_HOME) -> None:
        self.codex_home = Path(codex_home).expanduser()

    # ── construction / detection (registry seam) ─────────────────────────────

    @classmethod
    def from_home(cls, home: str | Path | None = None) -> CodexProvider:
        """Build a provider, resolving the Codex home.

        ``home`` overrides it; ``None`` resolves ``MEMRELAY_CODEX_HOME`` before falling
        back to ``~/.codex``.
        """
        if home is None:
            home = os.environ.get(CODEX_HOME_ENV) or DEFAULT_CODEX_HOME
        return cls(home)

    def is_present(self) -> bool:
        """True when ``~/.codex/sessions`` exists (Codex CLI has run in this home)."""
        return self.sessions_root.is_dir()

    # ── discovery ────────────────────────────────────────────────────────────

    @property
    def sessions_root(self) -> Path:
        return self.codex_home / SESSIONS_DIR

    def discover_sessions(self) -> Iterable[SessionRef]:
        """Yield a :class:`SessionRef` per ``rollout-*.jsonl`` rollout log.

        Codex nests rollouts under date dirs (``sessions/YYYY/MM/DD/rollout-*.jsonl``), so
        we glob recursively. The session id is the file stem; refs are ordered by path for
        deterministic iteration.
        """
        root = self.sessions_root
        if not root.is_dir():
            return
        for log in sorted(root.rglob(SESSION_GLOB)):
            if log.is_file():
                yield SessionRef(session_id=log.stem, agent_id=self.id, path=str(log))

    def _resolve_session_path(self, session_id: str) -> Path | None:
        """Find the ``rollout-*.jsonl`` whose stem matches ``session_id`` (first match)."""
        root = self.sessions_root
        if not root.is_dir():
            return None
        for log in sorted(root.rglob(SESSION_GLOB)):
            if log.is_file() and log.stem == session_id:
                return log
        return None

    # ── (1) source + mapping ─────────────────────────────────────────────────

    def make_adapter(self, session_id: str) -> Any:
        """Build the ``codex.yaml`` adapter scoped to ``session_id``.

        Single adapter: its declared ``preprocessor: codex`` runs inside
        ``MappedJsonAdapter.parse`` — no separate pre-parser needed.
        """
        from traceforge import MappedJsonAdapter

        return MappedJsonAdapter.from_yaml(mapping_path(CODEX_MAPPING), session_id)

    def read_raw(self, ref: SessionRef) -> Iterator[str]:
        """Yield raw JSONL lines from a rollout log; each line feeds ``adapter.parse``."""
        if ref.path:
            path: Path | None = Path(ref.path)
        else:
            path = self._resolve_session_path(ref.session_id)
        if path is None:
            raise FileNotFoundError(f"no Codex rollout found for {ref.session_id!r}")
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield line

    def make_source(
        self, session_id: str | None = None, *, path: str | Path | None = None
    ) -> CodexSource:
        """Return a replay :class:`CodexSource` over a rollout log.

        Scope it by an explicit ``path`` or by ``session_id`` (resolved by scanning
        ``~/.codex/sessions/**/rollout-*.jsonl``). Live tailing is the deferred daemon epic.
        """
        if path is not None:
            log_path: Path | None = Path(path)
        elif session_id is not None:
            log_path = self._resolve_session_path(session_id)
            if log_path is None:
                raise FileNotFoundError(f"no Codex rollout found for {session_id!r}")
        else:
            raise ValueError("make_source requires a session_id or an explicit path")
        return CodexSource(log_path)

    # ── (2) LLM strategy ─────────────────────────────────────────────────────

    def llm_strategy(self) -> LLMStrategyHint:
        """Advertise Codex's default: bring-your-own-key (metadata only)."""
        return LLMStrategyHint(strategy=LLM_STRATEGY, host=None)

    # ── (3) serving / registration — ingest-only ─────────────────────────────

    @property
    def mcp_config_path(self) -> Path:
        """Codex is ingest-only in memrelay (its MCP registry is TOML — not writable here)."""
        raise NotImplementedError(
            "codex is ingest-only: its MCP registry is TOML (~/.codex/config.toml), which "
            "memrelay cannot write without a new dependency"
        )

    def mcp_server_entry(
        self, *, command: str = "memrelay", args: Iterable[str] = ("mcp",)
    ) -> dict[str, Any]:
        raise NotImplementedError("codex is ingest-only: memrelay does not serve MCP to it")

    def register(self, *, command: str = "memrelay", args: Iterable[str] = ("mcp",)) -> Path:
        raise NotImplementedError("codex is ingest-only: memrelay does not register MCP for it")
