"""Claude Code provider — the second agent that validates the abstraction (SPEC §2.1, #70).

Claude Code (Anthropic's coding CLI) writes one JSONL session log per run under a
per-project directory::

    ~/.claude/projects/<path-encoded-cwd>/<session-uuid>.jsonl

This is a **two-level** layout (sessions nested under per-project dirs), unlike Copilot's
flat ``~/.copilot/session-state/<id>/events.jsonl``. Each line is a wire-format record
``{type: "user"|"assistant"|"result"|"system"|..., message: {content: ...}}``; real CLI
logs also carry wrapper fields (``parentUuid``, ``uuid``, ``cwd``, ``gitBranch`` …) and
control lines (e.g. ``queue-operation``) that map to nothing and drop harmlessly.

**Ingestion is 100% the installed traceforge 0.1.0 mapping** — no memrelay parsing code:
``mappings/claude.yaml`` (``framework: claude``) declares ``preprocessor: claude``
*inside* the mapping, and ``MappedJsonAdapter.parse`` applies that registered preprocessor
(``preprocessors/claude.py``, which flattens content blocks into per-block dicts with a
synthesized ``block_type``) automatically. So :meth:`make_adapter` is a **single**
``MappedJsonAdapter.from_yaml("claude.yaml", session_id)`` — Claude needs *no* separate
pre-parser (Copilot only used ``CopilotPreParser`` for its lower-fidelity SQLite fallback).
Verified empirically: the real ``claude.yaml`` over a real ``~/.claude`` session yields
``message.*`` / ``tool.call.*`` / ``llm.thinking.chunk`` events.

Two Claude-vs-Copilot asymmetries worth naming (both real, not bugs):

* **MCP entry type.** Claude registers stdio servers with ``"type": "stdio"`` (memrelay
  finding H4), whereas Copilot uses ``"type": "local"``. We do **not** normalize them.
* **cwd → namespace.** ``memrelay.ingest.graphiti_sink.resolve_session_cwd`` reads the cwd
  from a Copilot ``session.start`` record; Claude has no such record (its cwd lives on the
  top-level ``cwd`` of each turn). So the plain ``memrelay observe`` path can't yet derive a
  repo namespace from a Claude log and falls back to the default namespace. Deriving the
  namespace from Claude's per-record ``cwd`` is a separate observe-seam follow-up, out of
  scope for #70 (which only requires the mapping wired, sessions discovered, memory served,
  and a recall roundtrip). See the cross-agent recall test, which passes an explicit ``cwd``.
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

#: The single traceforge mapping for Claude; its ``preprocessor: claude`` is auto-applied.
CLAUDE_MAPPING = "claude.yaml"

DEFAULT_CLAUDE_HOME = "~/.claude"
PROJECTS_DIR = "projects"
SESSION_GLOB = "*.jsonl"

#: Env var that overrides the Claude home (mirrors Copilot's ``MEMRELAY_COPILOT_HOME``), so
#: auto-detect is testable without touching the real ``~/.claude``.
CLAUDE_HOME_ENV = "MEMRELAY_CLAUDE_HOME"

#: Claude Code's user-scope MCP registry. Grounded against the real file on this machine:
#: ``~/.claude.json`` is a large live state document (``projects``, ``oauthAccount``, caches,
#: …) with **no** top-level ``mcpServers`` by default; user-scope stdio servers live under a
#: top-level ``mcpServers`` map. :meth:`register` merges non-destructively into it.
MCP_CONFIG_FILENAME = ".claude.json"
MCP_SERVER_KEY = "memrelay"

#: Claude's key-less default: borrow the host Claude Code model (its own subscription auth,
#: no API key), advertised via :meth:`llm_strategy`. NOTE (known follow-up): this is
#: *metadata only* per E12 — the engine has no ``ClaudeHostProcess`` yet, so a config that
#: selects ``borrow-host``/``claude`` cannot actually drive the ``claude`` CLI at recall time
#: (the only borrow-host client today, ``CopilotHostProcess``, speaks Copilot's protocol).
#: Honoring it needs a ``ClaudeHostProcess`` (or a host-generalized process) — tracked
#: separately; not in scope for #70.
LLM_STRATEGY = "borrow-host"
LLM_HOST = "claude"


def mapping_path(name: str) -> str:
    """Resolve a packaged traceforge mapping YAML to a filesystem path.

    traceforge's ``from_yaml`` wants a real path and ``traceforge.mappings`` has no
    name→path resolver, so we locate the file via ``importlib.resources`` (mirrors the
    Copilot provider; the mapping ships inside the installed traceforge package).
    """
    resource = resources.files("traceforge.mappings").joinpath(name)
    return str(resource)


class ClaudeSource:
    """A replay source: iterate a session's ``*.jsonl`` as raw JSONL lines.

    Mirrors :meth:`ClaudeCodeProvider.read_raw` but as a reusable, session-scoped
    iterable — the seam ``memrelay observe`` drives. Blank lines are skipped; each yielded
    line is a JSON object string ready for :meth:`make_adapter`'s ``parse``.
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
class ClaudeCodeProvider(AgentProvider):
    """Second :class:`~memrelay.providers.base.AgentProvider`, for Anthropic Claude Code."""

    id = "claude"

    def __init__(self, claude_home: str | Path = DEFAULT_CLAUDE_HOME) -> None:
        self.claude_home = Path(claude_home).expanduser()

    # ── construction / detection (registry seam) ─────────────────────────────

    @classmethod
    def from_home(cls, home: str | Path | None = None) -> ClaudeCodeProvider:
        """Build a provider, resolving the Claude home.

        ``home`` overrides it; ``None`` resolves ``MEMRELAY_CLAUDE_HOME`` before falling
        back to ``~/.claude``. The bare ``ClaudeCodeProvider()`` constructor is unchanged
        and still defaults to ``~/.claude``.
        """
        if home is None:
            home = os.environ.get(CLAUDE_HOME_ENV) or DEFAULT_CLAUDE_HOME
        return cls(home)

    def is_present(self) -> bool:
        """True when ``~/.claude/projects`` exists (Claude Code has run in this home).

        Matches traceforge's own ``sources.auto_detect._detect_claude`` well-known path.
        """
        return self.projects_root.is_dir()

    # ── discovery ────────────────────────────────────────────────────────────

    @property
    def projects_root(self) -> Path:
        return self.claude_home / PROJECTS_DIR

    def discover_sessions(self) -> Iterable[SessionRef]:
        """Yield a :class:`SessionRef` for each ``*.jsonl`` session log.

        Recurses the two-level ``projects/<path-encoded-cwd>/<session-uuid>.jsonl`` layout;
        the session id is the file stem (the Claude session UUID). Ordered by
        (project dir, file name) for deterministic iteration.
        """
        root = self.projects_root
        if not root.is_dir():
            return
        for project in sorted(root.iterdir()):
            if not project.is_dir():
                continue
            for log in sorted(project.glob(SESSION_GLOB)):
                if log.is_file():
                    yield SessionRef(session_id=log.stem, agent_id=self.id, path=str(log))

    def _resolve_session_path(self, session_id: str) -> Path | None:
        """Find the ``*.jsonl`` for ``session_id`` by scanning project dirs (first match)."""
        root = self.projects_root
        if not root.is_dir():
            return None
        for project in sorted(root.iterdir()):
            if not project.is_dir():
                continue
            candidate = project / f"{session_id}.jsonl"
            if candidate.is_file():
                return candidate
        return None

    # ── (1) source + mapping ─────────────────────────────────────────────────

    def make_adapter(self, session_id: str) -> Any:
        """Build the ``claude.yaml`` adapter scoped to ``session_id``.

        Single adapter: its declared ``preprocessor: claude`` is applied inside
        ``MappedJsonAdapter.parse`` — no separate pre-parser needed.
        """
        from traceforge import MappedJsonAdapter

        return MappedJsonAdapter.from_yaml(mapping_path(CLAUDE_MAPPING), session_id)

    def read_raw(self, ref: SessionRef) -> Iterator[str]:
        """Yield raw JSONL lines from a session log; each line feeds ``adapter.parse``."""
        if ref.path:
            path: Path | None = Path(ref.path)
        else:
            path = self._resolve_session_path(ref.session_id)
        if path is None:
            raise FileNotFoundError(f"no Claude session log found for {ref.session_id!r}")
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield line

    def make_source(
        self, session_id: str | None = None, *, path: str | Path | None = None
    ) -> ClaudeSource:
        """Return a replay :class:`ClaudeSource` over a session log.

        Scope it by an explicit ``path`` or by ``session_id`` (resolved by scanning
        ``~/.claude/projects/*/<session_id>.jsonl``). Live file-watch tailing is the
        deferred daemon epic; the on-disk JSONL log + ``claude.yaml`` are the source of truth.
        """
        if path is not None:
            log_path: Path | None = Path(path)
        elif session_id is not None:
            log_path = self._resolve_session_path(session_id)
            if log_path is None:
                raise FileNotFoundError(f"no Claude session log found for {session_id!r}")
        else:
            raise ValueError("make_source requires a session_id or an explicit path")
        return ClaudeSource(log_path)

    # ── (2) LLM strategy (SPEC §2.1 #2) ──────────────────────────────────────

    def llm_strategy(self) -> LLMStrategyHint:
        """Advertise Claude's default: borrow the host Claude Code model (zero API keys).

        Metadata only. See the ``LLM_STRATEGY`` note: the engine cannot yet *honor*
        ``borrow-host``/``claude`` (no ``ClaudeHostProcess``) — that's a tracked follow-up.
        """
        return LLMStrategyHint(strategy=LLM_STRATEGY, host=LLM_HOST)

    # ── (3) serving / registration ───────────────────────────────────────────

    @property
    def mcp_config_path(self) -> Path:
        """Path to Claude Code's user-scope MCP registry (``~/.claude.json``)."""
        return self.claude_home / MCP_CONFIG_FILENAME

    def mcp_server_entry(
        self, *, command: str = "memrelay", args: Iterable[str] = ("mcp",)
    ) -> dict[str, Any]:
        """The stdio ``memrelay mcp`` entry Claude spawns (``type: stdio``).

        Note the asymmetry with Copilot (``type: local``) — both are correct for their
        respective CLIs and are intentionally not normalized.
        """
        return {
            "type": "stdio",
            "command": command,
            "args": list(args),
            "env": {},
        }

    def register(self, *, command: str = "memrelay", args: Iterable[str] = ("mcp",)) -> Path:
        """Merge the memrelay stdio server into Claude's ``~/.claude.json``.

        Non-destructive and idempotent: ``~/.claude.json`` is a large live state file, so we
        load the whole document, add/replace only the ``memrelay`` key under a top-level
        ``mcpServers`` map, and preserve every other key. Refuses to overwrite the file if it
        exists but is not valid JSON, rather than clobbering a user's real config.
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
