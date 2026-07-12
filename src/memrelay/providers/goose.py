"""Goose provider — ingest-only (SPEC §2.1, E12-S5 #71).

Block's Goose stores conversations in a SQLite ``messages`` table under its data dir. Each
row is a message ``{role, content_json, created_timestamp}`` (``content_json`` holds the
serialized content blocks — text, ``toolRequest``, ``toolResponse``). The installed
traceforge ``goose.yaml`` mapping declares ``preprocessor: goose`` (auto-applied inside
``MappedJsonAdapter.parse``), so :meth:`make_adapter` is a single
``MappedJsonAdapter.from_yaml("goose.yaml", session_id)`` fed one message row at a time.

**Replay vs live intake.** memrelay's canonical, hermetic intake here is a **normalized
row replay** — the ``messages`` rows as JSONL, one row object per line (this is exactly the
conformance fixture, and what :meth:`make_source` / :meth:`read_raw` iterate). Reading the
live SQLite database directly is the observation-daemon seam (traceforge's ``SqliteSource``
over ``goose.yaml``); it is intentionally not wired into this replay provider, mirroring the
Copilot reference (canonical JSONL replay + a separate SQLite path).

**Serving:** Goose's MCP registry is YAML, so it is **ingest-only** — the MCP hooks below
raise and no config is ever mutated.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Iterator
from importlib import resources
from pathlib import Path
from typing import Any

from memrelay.providers.base import AgentProvider, LLMStrategyHint, SessionRef
from memrelay.providers.registry import register

#: The single traceforge mapping for Goose; its ``preprocessor: goose`` auto-applies.
GOOSE_MAPPING = "goose.yaml"

DEFAULT_GOOSE_HOME = "~/.local/share/goose"
SESSIONS_DIR = "sessions"

#: Env var that overrides the Goose data dir (mirrors ``MEMRELAY_COPILOT_HOME``).
GOOSE_HOME_ENV = "MEMRELAY_GOOSE_HOME"

#: Goose has no key-less host-borrow path in memrelay today; advertise the honest default.
LLM_STRATEGY = "byo-key"


def mapping_path(name: str) -> str:
    """Resolve a packaged traceforge mapping YAML to a filesystem path (see reference providers)."""
    resource = resources.files("traceforge.mappings").joinpath(name)
    return str(resource)


class GooseSource:
    """A replay source over a Goose session's normalized ``messages`` rows.

    Iterates a normalized JSONL trace (one ``messages`` row object per line); blank lines
    are skipped and each yielded line is ready for :meth:`make_adapter`'s ``parse``.
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
class GooseProvider(AgentProvider):
    """:class:`~memrelay.providers.base.AgentProvider` for Goose."""

    id = "goose"

    def __init__(self, goose_home: str | Path = DEFAULT_GOOSE_HOME) -> None:
        self.goose_home = Path(goose_home).expanduser()

    # ── construction / detection (registry seam) ─────────────────────────────

    @classmethod
    def from_home(cls, home: str | Path | None = None) -> GooseProvider:
        """Build a provider, resolving the data dir (env ``MEMRELAY_GOOSE_HOME`` or default)."""
        if home is None:
            home = os.environ.get(GOOSE_HOME_ENV) or DEFAULT_GOOSE_HOME
        return cls(home)

    def is_present(self) -> bool:
        """True when the Goose ``sessions`` dir exists (Goose has recorded here)."""
        return self.sessions_root.is_dir()

    # ── discovery ────────────────────────────────────────────────────────────

    @property
    def sessions_root(self) -> Path:
        return self.goose_home / SESSIONS_DIR

    def discover_sessions(self) -> Iterable[SessionRef]:
        """Yield a :class:`SessionRef` per normalized ``*.jsonl`` row-replay trace, if any.

        Goose's live sessions live in a SQLite ``messages`` table; enumerating them from the
        database is the observation-daemon seam (traceforge ``SqliteSource``). This replay
        provider surfaces normalized row-replay traces (``sessions/*.jsonl``) when present,
        and otherwise yields nothing rather than guessing the live DB schema.
        """
        root = self.sessions_root
        if not root.is_dir():
            return
        for trace in sorted(root.glob("*.jsonl")):
            if trace.is_file():
                yield SessionRef(session_id=trace.stem, agent_id=self.id, path=str(trace))

    def _resolve_session_path(self, session_id: str) -> Path | None:
        candidate = self.sessions_root / f"{session_id}.jsonl"
        return candidate if candidate.is_file() else None

    # ── (1) source + mapping ─────────────────────────────────────────────────

    def make_adapter(self, session_id: str) -> Any:
        """Build the ``goose.yaml`` adapter scoped to ``session_id`` (preprocessor auto-applies)."""
        from traceforge import MappedJsonAdapter

        return MappedJsonAdapter.from_yaml(mapping_path(GOOSE_MAPPING), session_id)

    def read_raw(self, ref: SessionRef) -> Iterator[str]:
        """Yield normalized ``messages`` rows as JSON strings, ready for ``adapter.parse``."""
        if ref.path:
            path: Path | None = Path(ref.path)
        else:
            path = self._resolve_session_path(ref.session_id)
        if path is None:
            raise FileNotFoundError(f"no Goose session replay found for {ref.session_id!r}")
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield line

    def make_source(
        self, session_id: str | None = None, *, path: str | Path | None = None
    ) -> GooseSource:
        """Return a replay :class:`GooseSource` over a normalized row trace.

        Scope it by an explicit ``path`` or by ``session_id`` (resolved under the data dir's
        ``sessions/<id>.jsonl``).
        """
        if path is not None:
            trace_path: Path | None = Path(path)
        elif session_id is not None:
            trace_path = self._resolve_session_path(session_id)
            if trace_path is None:
                raise FileNotFoundError(f"no Goose session replay found for {session_id!r}")
        else:
            raise ValueError("make_source requires a session_id or an explicit path")
        return GooseSource(trace_path)

    # ── (2) LLM strategy ─────────────────────────────────────────────────────

    def llm_strategy(self) -> LLMStrategyHint:
        """Advertise Goose's default: bring-your-own-key (metadata only)."""
        return LLMStrategyHint(strategy=LLM_STRATEGY, host=None)

    # ── (3) serving / registration — ingest-only ─────────────────────────────

    @property
    def mcp_config_path(self) -> Path:
        """Goose is ingest-only in memrelay (its MCP registry is YAML — not written here)."""
        raise NotImplementedError(
            "goose is ingest-only: its MCP registry is YAML, which memrelay does not write here"
        )

    def mcp_server_entry(
        self, *, command: str = "memrelay", args: Iterable[str] = ("mcp",)
    ) -> dict[str, Any]:
        raise NotImplementedError("goose is ingest-only: memrelay does not serve MCP to it")

    def register(self, *, command: str = "memrelay", args: Iterable[str] = ("mcp",)) -> Path:
        raise NotImplementedError("goose is ingest-only: memrelay does not register MCP for it")
