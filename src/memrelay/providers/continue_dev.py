"""Continue / Cursor provider — ingest-only (SPEC §2.1, E12-S5 #71).

Continue (the open-source VS Code / JetBrains assistant, which also backs Cursor's
Continue-derived session format) persists each session as a **single JSON document** under
its global dir::

    ~/.continue/sessions/<session-id>.json     (override: ``$CONTINUE_GLOBAL_DIR``)

The document carries a ``history`` array of turn items (``{message: {role, content,
toolCalls?}}``). The installed traceforge ``continue_dev.yaml`` mapping declares
``preprocessor: continue`` which flattens that ``history`` into per-message records, so
memrelay feeds the **whole session object** to a single
``MappedJsonAdapter.from_yaml("continue_dev.yaml", session_id)`` and the preprocessor fans
it out.

TraceForge ships **one** mapping for this family (``continue_dev``); there is no separate
Cursor mapping, so Cursor is covered *as* Continue (shared session shape) — one provider,
``id = "continue"``. The module is named ``continue_dev`` because ``continue`` is a Python
keyword.

**Serving:** Continue's MCP registry is YAML. memrelay does not write it here (entry shapes
differ and this avoids scope creep), so Continue is **ingest-only** — the MCP hooks below
raise and no config is ever mutated.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Iterator
from importlib import resources
from pathlib import Path
from typing import Any

from memrelay.providers.base import AgentProvider, LLMStrategyHint, SessionRef
from memrelay.providers.registry import register

#: The single traceforge mapping for Continue/Cursor; its ``preprocessor: continue`` auto-applies.
CONTINUE_MAPPING = "continue_dev.yaml"

DEFAULT_CONTINUE_HOME = "~/.continue"
SESSIONS_DIR = "sessions"
SESSION_GLOB = "*.json"

#: Env var that overrides the Continue home (mirrors ``MEMRELAY_COPILOT_HOME``).
CONTINUE_HOME_ENV = "MEMRELAY_CONTINUE_HOME"

#: Continue has no key-less host-borrow path in memrelay today; advertise the honest default.
LLM_STRATEGY = "byo-key"


def mapping_path(name: str) -> str:
    """Resolve a packaged traceforge mapping YAML to a filesystem path (see reference providers)."""
    resource = resources.files("traceforge.mappings").joinpath(name)
    return str(resource)


class ContinueSource:
    """A replay source over a Continue session's single JSON document.

    Continue stores one session as a whole JSON object (with a ``history`` array), not
    line-delimited JSON, so this reads the file and yields the object **once** as a JSON
    string; ``continue_dev.yaml``'s ``preprocessor: continue`` fans ``history`` into
    per-message events inside ``adapter.parse``.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def __iter__(self) -> Iterator[str]:
        with open(self.path, encoding="utf-8") as fh:
            obj = json.load(fh)
        yield json.dumps(obj)


@register
class ContinueProvider(AgentProvider):
    """:class:`~memrelay.providers.base.AgentProvider` for Continue (and Cursor)."""

    id = "continue"

    def __init__(self, continue_home: str | Path = DEFAULT_CONTINUE_HOME) -> None:
        self.continue_home = Path(continue_home).expanduser()

    # ── construction / detection (registry seam) ─────────────────────────────

    @classmethod
    def from_home(cls, home: str | Path | None = None) -> ContinueProvider:
        """Build a provider, resolving the home (``MEMRELAY_CONTINUE_HOME`` → ``~/.continue``)."""
        if home is None:
            home = os.environ.get(CONTINUE_HOME_ENV) or DEFAULT_CONTINUE_HOME
        return cls(home)

    def is_present(self) -> bool:
        """True when ``~/.continue/sessions`` exists (Continue has recorded here)."""
        return self.sessions_root.is_dir()

    # ── discovery ────────────────────────────────────────────────────────────

    @property
    def sessions_root(self) -> Path:
        return self.continue_home / SESSIONS_DIR

    def discover_sessions(self) -> Iterable[SessionRef]:
        """Yield a :class:`SessionRef` per session ``*.json`` (session id = file stem).

        Skips a ``sessions.json`` index file if the dir carries one — only per-session
        documents are surfaced.
        """
        root = self.sessions_root
        if not root.is_dir():
            return
        for doc in sorted(root.glob(SESSION_GLOB)):
            if doc.is_file() and doc.name != "sessions.json":
                yield SessionRef(session_id=doc.stem, agent_id=self.id, path=str(doc))

    def _resolve_session_path(self, session_id: str) -> Path | None:
        candidate = self.sessions_root / f"{session_id}.json"
        return candidate if candidate.is_file() else None

    # ── (1) source + mapping ─────────────────────────────────────────────────

    def make_adapter(self, session_id: str) -> Any:
        """Build the ``continue_dev.yaml`` adapter for ``session_id`` (preprocessor runs)."""
        from traceforge import MappedJsonAdapter

        return MappedJsonAdapter.from_yaml(mapping_path(CONTINUE_MAPPING), session_id)

    def read_raw(self, ref: SessionRef) -> Iterator[str]:
        """Yield the session document as a single JSON string, ready for ``adapter.parse``."""
        if ref.path:
            path: Path | None = Path(ref.path)
        else:
            path = self._resolve_session_path(ref.session_id)
        if path is None:
            raise FileNotFoundError(f"no Continue session found for {ref.session_id!r}")
        with open(path, encoding="utf-8") as fh:
            obj = json.load(fh)
        yield json.dumps(obj)

    def make_source(
        self, session_id: str | None = None, *, path: str | Path | None = None
    ) -> ContinueSource:
        """Return a replay :class:`ContinueSource` over a session document.

        Scope it by an explicit ``path`` or by ``session_id`` (resolved under
        ``~/.continue/sessions/<id>.json``).
        """
        if path is not None:
            doc_path: Path | None = Path(path)
        elif session_id is not None:
            doc_path = self._resolve_session_path(session_id)
            if doc_path is None:
                raise FileNotFoundError(f"no Continue session found for {session_id!r}")
        else:
            raise ValueError("make_source requires a session_id or an explicit path")
        return ContinueSource(doc_path)

    # ── (2) LLM strategy ─────────────────────────────────────────────────────

    def llm_strategy(self) -> LLMStrategyHint:
        """Advertise Continue's default: bring-your-own-key (metadata only)."""
        return LLMStrategyHint(strategy=LLM_STRATEGY, host=None)

    # ── (3) serving / registration — ingest-only ─────────────────────────────

    @property
    def mcp_config_path(self) -> Path:
        """Continue is ingest-only in memrelay (its MCP registry is YAML — not written here)."""
        raise NotImplementedError(
            "continue is ingest-only: its MCP registry is YAML, which memrelay does not write here"
        )

    def mcp_server_entry(
        self, *, command: str = "memrelay", args: Iterable[str] = ("mcp",)
    ) -> dict[str, Any]:
        raise NotImplementedError("continue is ingest-only: memrelay does not serve MCP to it")

    def register(self, *, command: str = "memrelay", args: Iterable[str] = ("mcp",)) -> Path:
        raise NotImplementedError("continue is ingest-only: memrelay does not register MCP for it")
