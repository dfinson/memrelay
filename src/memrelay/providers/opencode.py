"""OpenCode provider — MCP-serving (SPEC §2.1, E12-S5 #71).

OpenCode (>=1.17) is event-sourced into a local **SQLite** store
(``~/.local/share/opencode/opencode.db``, table ``event``). Each row is
``{id, aggregate_id=sessionID, seq, type, data}`` where ``type`` carries a trailing
version suffix (e.g. ``message.part.updated.1``) and ``data`` is a JSON payload. The
installed traceforge ``opencode.yaml`` mapping declares ``preprocessor: opencode``
(auto-applied inside ``MappedJsonAdapter.parse``) which strips the version suffix, routes
``message.updated`` by role and ``message.part.updated`` by part type / tool state, and
derives the timestamp.

memrelay treats the agent's live SQLite intake as a **daemon seam** (traceforge ships a
``SqliteSource`` for tailing the DB) and does not read the binary DB here. Instead this
provider replays **normalized rows** — one ``event``-table row per JSON line, exactly the
shape traceforge's preprocessor consumes — which is what the conformance matrix and the
daemon's normalized-trace path exercise. This mirrors copilot's "canonical JSONL +
separate SQLite fallback" split without guessing the binary schema.

**Serving:** OpenCode's MCP registry is JSON (``opencode.json``, servers under the ``mcp``
key with ``type: local`` and an array ``command``), so memrelay **registers** into it with
the same non-destructive JSON merge as the reference providers. The canonical global
config is ``~/.config/opencode/opencode.json``; ``MEMRELAY_OPENCODE_HOME`` overrides the
base dir memrelay uses (and, in tests, keeps registration hermetic).
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

#: The single traceforge mapping for OpenCode; its ``preprocessor: opencode`` auto-applies.
OPENCODE_MAPPING = "opencode.yaml"

#: Canonical global config home (``opencode.json`` lives directly under it).
DEFAULT_OPENCODE_HOME = "~/.config/opencode"
SESSIONS_DIR = "sessions"

#: OpenCode's JSON config filename (MCP servers live under its ``mcp`` key) and the memrelay key.
MCP_CONFIG_FILENAME = "opencode.json"
MCP_SERVER_KEY = "memrelay"
#: OpenCode nests MCP servers under the top-level ``mcp`` key (not ``mcpServers``).
MCP_SERVER_KEY_CONTAINER = "mcp"

#: Env var that overrides the OpenCode home (mirrors ``MEMRELAY_COPILOT_HOME``).
OPENCODE_HOME_ENV = "MEMRELAY_OPENCODE_HOME"

#: OpenCode has no key-less host-borrow path in memrelay today; advertise the honest default.
LLM_STRATEGY = "byo-key"


def mapping_path(name: str) -> str:
    """Resolve a packaged traceforge mapping YAML to a filesystem path (see reference providers)."""
    resource = resources.files("traceforge.mappings").joinpath(name)
    return str(resource)


class OpenCodeSource:
    """A replay source over OpenCode **normalized rows** (one ``event``-table row per line).

    Each line is a JSON object ``{type, data}`` the ``opencode`` preprocessor consumes.
    Blank lines are skipped; each surviving line is yielded verbatim as a JSON string for
    :meth:`make_adapter`'s ``parse``.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def __iter__(self) -> Iterator[str]:
        with open(self.path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield line


@register
class OpenCodeProvider(AgentProvider):
    """:class:`~memrelay.providers.base.AgentProvider` for OpenCode."""

    id = "opencode"

    def __init__(self, opencode_home: str | Path = DEFAULT_OPENCODE_HOME) -> None:
        self.opencode_home = Path(opencode_home).expanduser()

    # ── construction / detection (registry seam) ─────────────────────────────

    @classmethod
    def from_home(cls, home: str | Path | None = None) -> OpenCodeProvider:
        """Build a provider, resolving the home (env ``MEMRELAY_OPENCODE_HOME`` or default)."""
        if home is None:
            home = os.environ.get(OPENCODE_HOME_ENV) or DEFAULT_OPENCODE_HOME
        return cls(home)

    def is_present(self) -> bool:
        """True when the OpenCode config home exists (OpenCode is configured on this machine)."""
        return self.opencode_home.is_dir()

    # ── discovery ────────────────────────────────────────────────────────────

    @property
    def sessions_root(self) -> Path:
        return self.opencode_home / SESSIONS_DIR

    def discover_sessions(self) -> Iterable[SessionRef]:
        """Yield a :class:`SessionRef` per normalized ``sessions/*.jsonl`` replay trace, if present.

        Live SQLite (``opencode.db``) intake is the daemon's ``SqliteSource`` seam; this
        honest discovery enumerates only pre-normalized row traces and yields nothing when
        none exist (no binary-schema guessing).
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
        """Build the ``opencode.yaml`` adapter for ``session_id`` (preprocessor runs)."""
        from traceforge import MappedJsonAdapter

        return MappedJsonAdapter.from_yaml(mapping_path(OPENCODE_MAPPING), session_id)

    def read_raw(self, ref: SessionRef) -> Iterator[str]:
        """Yield each normalized row as a JSON string, ready for ``adapter.parse``."""
        if ref.path:
            path: Path | None = Path(ref.path)
        else:
            path = self._resolve_session_path(ref.session_id)
        if path is None:
            raise FileNotFoundError(f"no OpenCode trace found for {ref.session_id!r}")
        yield from OpenCodeSource(path)

    def make_source(
        self, session_id: str | None = None, *, path: str | Path | None = None
    ) -> OpenCodeSource:
        """Return a replay :class:`OpenCodeSource` over a normalized row trace.

        Scope it by an explicit ``path`` or by ``session_id`` (resolved under
        ``<home>/sessions/<id>.jsonl``).
        """
        if path is not None:
            trace_path: Path | None = Path(path)
        elif session_id is not None:
            trace_path = self._resolve_session_path(session_id)
            if trace_path is None:
                raise FileNotFoundError(f"no OpenCode trace found for {session_id!r}")
        else:
            raise ValueError("make_source requires a session_id or an explicit path")
        return OpenCodeSource(trace_path)

    # ── (2) LLM strategy ─────────────────────────────────────────────────────

    def llm_strategy(self) -> LLMStrategyHint:
        """Advertise OpenCode's default: bring-your-own-key (metadata only)."""
        return LLMStrategyHint(strategy=LLM_STRATEGY, host=None)

    # ── (3) serving / registration ───────────────────────────────────────────

    @property
    def mcp_config_path(self) -> Path:
        """Path to OpenCode's JSON MCP config (``<home>/opencode.json``)."""
        return self.opencode_home / MCP_CONFIG_FILENAME

    def mcp_server_entry(
        self, *, command: str = "memrelay", args: Iterable[str] = ("mcp",)
    ) -> dict[str, Any]:
        """The local ``memrelay mcp`` entry OpenCode spawns (``type: local``, array ``command``)."""
        return {
            "type": "local",
            "command": [command, *args],
            "enabled": True,
        }

    def register(self, *, command: str = "memrelay", args: Iterable[str] = ("mcp",)) -> Path:
        """Merge the memrelay server into OpenCode's ``opencode.json`` (under the ``mcp`` key).

        Idempotent and non-destructive: existing servers under ``mcp`` are preserved; only the
        ``memrelay`` key is (re)written. Refuses to overwrite the file if it exists but is not
        valid JSON, rather than clobbering a user's config.
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

        servers = data.get(MCP_SERVER_KEY_CONTAINER)
        if not isinstance(servers, dict):
            servers = {}
        servers[MCP_SERVER_KEY] = self.mcp_server_entry(command=command, args=args)
        data[MCP_SERVER_KEY_CONTAINER] = servers

        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return path
