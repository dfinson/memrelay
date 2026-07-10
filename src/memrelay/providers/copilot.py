"""Copilot CLI provider — the reference agent (SPEC §2.1, §3.2).

Two ingestion paths, both verified live in the E0 spike (see ``docs/e0-spike.md``):

* **Canonical (primary):** the ``copilot.yaml`` mapping over
  ``~/.copilot/session-state/<id>/events.jsonl`` — high fidelity (real tool-call
  ids, hooks, turns, permissions). This is what traceforge itself recommends.
* **Fallback:** ``SqliteSource`` over ``~/.copilot/session-store.db`` ``turns`` →
  :class:`CopilotPreParser` → the ``copilot_markdown`` mapping. Lower fidelity
  (tool calls inferred from markdown) and ``forge_trajectory_events`` may be empty.

The wiring here uses the *actually installed* traceforge 0.1.0 API, which differs
from the SPEC §3.2 snippet in several places (documented as deltas in the spike
report): the pre-parser import path, ``from_yaml`` taking a filesystem path,
``parse_dict`` for dict records, and ``SqliteSource`` being async.
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

CANONICAL_MAPPING = "copilot.yaml"
FALLBACK_MAPPING = "copilot_markdown.yaml"

DEFAULT_COPILOT_HOME = "~/.copilot"
SESSION_STATE_DIR = "session-state"
EVENTS_FILENAME = "events.jsonl"
SESSION_STORE_DB = "session-store.db"

#: Copilot CLI's MCP registry file and the memrelay entry key (SPEC §2 Registration).
#: NOTE (de-risk delta, see docs/e6e7-skeleton-notes.md): the installed Copilot CLI
#: uses ``"type": "local"`` for stdio subprocess servers, not SPEC's ``"stdio"``.
MCP_CONFIG_FILENAME = "mcp-config.json"
MCP_SERVER_KEY = "memrelay"

#: Env var (already honored by the CLI) that overrides the Copilot home; consulted by
#: :meth:`CopilotProvider.from_home` so auto-detect is testable and CLI-consistent.
COPILOT_HOME_ENV = "MEMRELAY_COPILOT_HOME"

#: Copilot's key-less default: borrow the host agent's own model (SPEC §6.2). Advertised
#: via :meth:`CopilotProvider.llm_strategy`; maps onto ``config.llm.{strategy,host}``.
LLM_STRATEGY = "borrow-host"
LLM_HOST = "copilot"


def mapping_path(name: str) -> str:
    """Resolve a packaged traceforge mapping YAML to a filesystem path.

    traceforge's ``from_yaml`` wants a real path, and ``traceforge.mappings`` has
    no name→path resolver, so we locate the file via ``importlib.resources``.
    """
    resource = resources.files("traceforge.mappings").joinpath(name)
    return str(resource)


class CopilotSource:
    """A replay source: iterate a session's ``events.jsonl`` as raw JSONL lines.

    Mirrors :meth:`CopilotProvider.read_raw` but as a reusable, session-scoped
    iterable — the seam the observation daemon (later epic) extends with live
    tailing. Blank lines are skipped; each yielded line is ready for ``adapter.parse``.
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
class CopilotProvider(AgentProvider):
    """Reference :class:`~memrelay.providers.base.AgentProvider` for Copilot CLI."""

    id = "copilot"

    def __init__(self, copilot_home: str | Path = DEFAULT_COPILOT_HOME) -> None:
        self.copilot_home = Path(copilot_home).expanduser()

    # ── construction / detection (registry seam) ─────────────────────────────

    @classmethod
    def from_home(cls, home: str | Path | None = None) -> CopilotProvider:
        """Build a provider, resolving the Copilot home.

        ``home`` overrides it; ``None`` resolves ``MEMRELAY_COPILOT_HOME`` (the env var
        the CLI already honors) before falling back to ``~/.copilot``. The bare
        ``CopilotProvider()`` constructor is unchanged and still defaults to ``~/.copilot``.
        """
        if home is None:
            home = os.environ.get(COPILOT_HOME_ENV) or DEFAULT_COPILOT_HOME
        return cls(home)

    def is_present(self) -> bool:
        """True when a ``session-state`` dir exists (Copilot CLI has run in this home)."""
        return self.session_state_root.is_dir()

    # ── discovery ────────────────────────────────────────────────────────────

    @property
    def session_state_root(self) -> Path:
        return self.copilot_home / SESSION_STATE_DIR

    @property
    def session_store_db(self) -> Path:
        return self.copilot_home / SESSION_STORE_DB

    def discover_sessions(self) -> Iterable[SessionRef]:
        """Yield a :class:`SessionRef` for each session with an ``events.jsonl``."""
        root = self.session_state_root
        if not root.is_dir():
            return
        for child in sorted(root.iterdir()):
            events = child / EVENTS_FILENAME
            if events.is_file():
                yield SessionRef(session_id=child.name, agent_id=self.id, path=str(events))

    # ── canonical (primary) path ─────────────────────────────────────────────

    def make_adapter(self, session_id: str) -> Any:
        """Build the canonical ``copilot.yaml`` adapter scoped to ``session_id``."""
        from traceforge import MappedJsonAdapter

        return MappedJsonAdapter.from_yaml(mapping_path(CANONICAL_MAPPING), session_id)

    def read_raw(self, ref: SessionRef) -> Iterator[str]:
        """Yield raw JSONL lines from a session's ``events.jsonl``.

        Each line is a JSON object string, fed directly to ``adapter.parse``.
        """
        if ref.path:
            path = Path(ref.path)
        else:
            path = self.session_state_root / ref.session_id / EVENTS_FILENAME
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield line

    def make_source(
        self, session_id: str | None = None, *, path: str | Path | None = None
    ) -> CopilotSource:
        """Return a replay :class:`CopilotSource` over a session's ``events.jsonl``.

        Yields raw JSONL lines (ready for :meth:`make_adapter`'s ``parse``) from a
        session's canonical trace. Scope it by ``session_id`` (resolved under
        ``~/.copilot/session-state/<id>/events.jsonl``) or an explicit ``path``.

        This is the *replay* capture that backs ``memrelay observe``. Live
        file-watch tailing of an in-progress session is the deferred daemon epic
        (#8/#11); the canonical trace + ``copilot.yaml`` remain the source of truth.
        """
        if path is not None:
            events_path = Path(path)
        elif session_id is not None:
            events_path = self.session_state_root / session_id / EVENTS_FILENAME
        else:
            raise ValueError("make_source requires a session_id or an explicit path")
        return CopilotSource(events_path)

    def make_filewatch_source(
        self,
        session_id: str | None = None,
        *,
        path: str | Path | None = None,
        start_at: str = "beginning",
    ) -> Any:
        """Return a live traceforge ``FileWatchSource`` tailing a session's ``events.jsonl``.

        The daemon's real-time intake (#11): a long-lived, watchdog-backed source that,
        with the default ``start_at="beginning"``, drains the file 0→EOF once and then
        tails appended lines as OS filesystem events arrive — one continuous
        replay-then-tail source. Scope it by ``session_id`` (resolved under
        ``~/.copilot/session-state/<id>/events.jsonl``) or an explicit ``path``, exactly
        like :meth:`make_source`. Each yielded ``RawRecord.payload`` is a raw JSONL line,
        ready for :meth:`make_adapter`'s ``parse``.

        This is the **best-effort latency** intake: it is paired with the periodic
        :func:`~memrelay.ingest.graphiti_sink.run_observe` replay backstop and the
        idempotent spool, so the tail itself needs no crash-durable offset —
        losslessness is owned by replay + spool dedupe, not the tail. traceforge's
        ``FileWatchSource`` is watchdog-backed but crosses back to the asyncio loop with
        ``loop.call_soon_threadsafe`` only; all reads/records happen on the loop thread.
        """
        if path is not None:
            events_path = Path(path)
        elif session_id is not None:
            events_path = self.session_state_root / session_id / EVENTS_FILENAME
        else:
            raise ValueError("make_filewatch_source requires a session_id or an explicit path")
        from traceforge.sources import FileWatchSource

        return FileWatchSource(str(events_path), self.id, start_at=start_at)

    # ── fallback (SQLite + markdown) path ────────────────────────────────────

    def make_fallback_source(
        self,
        *,
        session_filter: str | None = None,
        start_at: str = "beginning",
    ) -> Any:
        """Build the documented ``SqliteSource`` fallback over ``turns``.

        traceforge's ``SqliteSource`` is async, needs a ``name``, does not expand
        ``~``, and defaults to ``start_at="end"`` (new rows only) — we pass an
        already-expanded path and ``start_at="beginning"`` to read history.
        """
        from traceforge.sources import SqliteSource

        return SqliteSource(
            str(self.session_store_db),
            self.id,
            session_filter=session_filter,
            start_at=start_at,
        )

    def make_fallback_adapter(self, session_id: str) -> tuple[Any, Any]:
        """Return ``(pre_parser, adapter)`` for the SQLite markdown fallback."""
        from traceforge import MappedJsonAdapter
        from traceforge.parsers.copilot import CopilotPreParser

        pre = CopilotPreParser()
        adapter = MappedJsonAdapter.from_yaml(mapping_path(FALLBACK_MAPPING), session_id)
        return pre, adapter

    # ── (2) LLM strategy (SPEC §2.1 #2, §6.2) ────────────────────────────────

    def llm_strategy(self) -> LLMStrategyHint:
        """Advertise Copilot's default: borrow the host Copilot model (zero API keys).

        Metadata only — the engine (``memrelay.engine.llm``) still owns building the
        graphiti ``LLMClient`` and the ``config.llm`` fallback chain.
        """
        return LLMStrategyHint(strategy=LLM_STRATEGY, host=LLM_HOST)

    # ── MCP registration (E7-S6, SPEC §2 Registration) ───────────────────────

    @property
    def mcp_config_path(self) -> Path:
        """Path to Copilot CLI's MCP registry (``~/.copilot/mcp-config.json``)."""
        return self.copilot_home / MCP_CONFIG_FILENAME

    def mcp_server_entry(
        self, *, command: str = "memrelay", args: Iterable[str] = ("mcp",)
    ) -> dict[str, Any]:
        """The stdio ``memrelay mcp`` entry Copilot spawns (``type: local``)."""
        return {
            "type": "local",
            "command": command,
            "args": list(args),
            "tools": ["*"],
            "env": {},
        }

    def register(self, *, command: str = "memrelay", args: Iterable[str] = ("mcp",)) -> Path:
        """Merge the memrelay stdio server into Copilot's ``mcp-config.json``.

        Idempotent and non-destructive: existing servers under ``mcpServers`` are
        preserved; only the ``memrelay`` key is (re)written. Raises if the file
        exists but is not valid JSON, rather than clobbering a user's config.
        """
        path = self.mcp_config_path
        path.parent.mkdir(parents=True, exist_ok=True)

        data: dict[str, Any] = {}
        if path.exists():
            text = path.read_text(encoding="utf-8")
            if text.strip():
                try:
                    loaded = json.loads(text)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{path} is not valid JSON; refusing to overwrite it") from exc
                if isinstance(loaded, dict):
                    data = loaded

        servers = data.get("mcpServers")
        if not isinstance(servers, dict):
            servers = {}
        servers[MCP_SERVER_KEY] = self.mcp_server_entry(command=command, args=args)
        data["mcpServers"] = servers

        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return path
