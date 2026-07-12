"""Amazon Q Developer CLI provider — MCP-serving (SPEC §2.1, E12-S5 #71).

The Amazon Q Developer CLI persists conversations in a local **SQLite** database
(``data.sqlite3``), one row per workspace, whose ``value`` column holds a JSON document
``{conversation_id, messages: [...]}``. The installed traceforge ``amazonq.yaml`` mapping
declares ``preprocessor: amazonq`` (auto-applied inside ``MappedJsonAdapter.parse``) which
unwraps that ``value`` JSON and fans the ``messages`` (with their ``text`` / ``tool_use`` /
``tool_result`` content blocks) out into normalized events.

memrelay treats the agent's live SQLite intake as a **daemon seam** (traceforge ships a
``SqliteSource`` for tailing the DB) and does not read the binary DB here. Instead this
provider replays **normalized rows** — one JSON row per line, each the ``{conversation_id,
value}`` shape traceforge's preprocessor consumes — which is exactly what the conformance
matrix and the daemon's normalized-trace path exercise. This mirrors copilot's
"canonical JSONL + separate SQLite fallback" split without guessing the binary schema.

**Serving:** Amazon Q's MCP registry is JSON (``mcp.json``), so memrelay **registers** into
it with the same non-destructive JSON merge as the reference providers. The canonical AWS
location is ``~/.aws/amazonq/mcp.json``; ``MEMRELAY_AMAZONQ_HOME`` overrides the base dir
memrelay uses (and, in tests, keeps registration hermetic).
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

#: The single traceforge mapping for Amazon Q; its ``preprocessor: amazonq`` auto-applies.
AMAZONQ_MAPPING = "amazonq.yaml"

#: Canonical AWS config home (``mcp.json`` lives directly under it).
DEFAULT_AMAZONQ_HOME = "~/.aws/amazonq"
SESSIONS_DIR = "sessions"

#: Amazon Q's JSON MCP registry filename (under the config home) and the memrelay key.
MCP_CONFIG_FILENAME = "mcp.json"
MCP_SERVER_KEY = "memrelay"

#: Env var that overrides the Amazon Q home (mirrors ``MEMRELAY_COPILOT_HOME``).
AMAZONQ_HOME_ENV = "MEMRELAY_AMAZONQ_HOME"

#: Amazon Q has no key-less host-borrow path in memrelay today; advertise the honest default.
LLM_STRATEGY = "byo-key"


def mapping_path(name: str) -> str:
    """Resolve a packaged traceforge mapping YAML to a filesystem path (see reference providers)."""
    resource = resources.files("traceforge.mappings").joinpath(name)
    return str(resource)


class AmazonQSource:
    """A replay source over Amazon Q normalized rows (one ``{conversation_id, value}`` per line).

    Each line is a JSON object whose ``value`` is the JSON document the ``amazonq``
    preprocessor unwraps. Blank lines are skipped; each surviving line is yielded verbatim as
    a JSON string for :meth:`make_adapter`'s ``parse``.
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
class AmazonQProvider(AgentProvider):
    """:class:`~memrelay.providers.base.AgentProvider` for the Amazon Q Developer CLI."""

    id = "amazonq"

    def __init__(self, amazonq_home: str | Path = DEFAULT_AMAZONQ_HOME) -> None:
        self.amazonq_home = Path(amazonq_home).expanduser()

    # ── construction / detection (registry seam) ─────────────────────────────

    @classmethod
    def from_home(cls, home: str | Path | None = None) -> AmazonQProvider:
        """Build a provider, resolving the home (``MEMRELAY_AMAZONQ_HOME`` → ``~/.aws/amazonq``)."""
        if home is None:
            home = os.environ.get(AMAZONQ_HOME_ENV) or DEFAULT_AMAZONQ_HOME
        return cls(home)

    def is_present(self) -> bool:
        """True when the Amazon Q config home exists (the CLI is configured on this machine)."""
        return self.amazonq_home.is_dir()

    # ── discovery ────────────────────────────────────────────────────────────

    @property
    def sessions_root(self) -> Path:
        return self.amazonq_home / SESSIONS_DIR

    def discover_sessions(self) -> Iterable[SessionRef]:
        """Yield a :class:`SessionRef` per normalized ``sessions/*.jsonl`` replay trace, if present.

        Live SQLite (``data.sqlite3``) intake is the daemon's ``SqliteSource`` seam; this
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
        """Build the ``amazonq.yaml`` adapter for ``session_id`` (preprocessor runs)."""
        from traceforge import MappedJsonAdapter

        return MappedJsonAdapter.from_yaml(mapping_path(AMAZONQ_MAPPING), session_id)

    def read_raw(self, ref: SessionRef) -> Iterator[str]:
        """Yield each normalized row as a JSON string, ready for ``adapter.parse``."""
        if ref.path:
            path: Path | None = Path(ref.path)
        else:
            path = self._resolve_session_path(ref.session_id)
        if path is None:
            raise FileNotFoundError(f"no Amazon Q trace found for {ref.session_id!r}")
        yield from AmazonQSource(path)

    def make_source(
        self, session_id: str | None = None, *, path: str | Path | None = None
    ) -> AmazonQSource:
        """Return a replay :class:`AmazonQSource` over a normalized row trace.

        Scope it by an explicit ``path`` or by ``session_id`` (resolved under
        ``<home>/sessions/<id>.jsonl``).
        """
        if path is not None:
            trace_path: Path | None = Path(path)
        elif session_id is not None:
            trace_path = self._resolve_session_path(session_id)
            if trace_path is None:
                raise FileNotFoundError(f"no Amazon Q trace found for {session_id!r}")
        else:
            raise ValueError("make_source requires a session_id or an explicit path")
        return AmazonQSource(trace_path)

    # ── (2) LLM strategy ─────────────────────────────────────────────────────

    def llm_strategy(self) -> LLMStrategyHint:
        """Advertise Amazon Q's default: bring-your-own-key (metadata only)."""
        return LLMStrategyHint(strategy=LLM_STRATEGY, host=None)

    # ── (3) serving / registration ───────────────────────────────────────────

    @property
    def mcp_config_path(self) -> Path:
        """Path to Amazon Q's JSON MCP registry (``<home>/mcp.json``)."""
        return self.amazonq_home / MCP_CONFIG_FILENAME

    def mcp_server_entry(
        self, *, command: str = "memrelay", args: Iterable[str] = ("mcp",)
    ) -> dict[str, Any]:
        """The stdio ``memrelay mcp`` entry Amazon Q spawns."""
        return {
            "command": command,
            "args": list(args),
            "env": {},
        }

    def register(self, *, command: str = "memrelay", args: Iterable[str] = ("mcp",)) -> Path:
        """Merge the memrelay server into Amazon Q's ``mcp.json``.

        Idempotent and non-destructive: existing servers under ``mcpServers`` are preserved;
        only the ``memrelay`` key is (re)written. Refuses to overwrite the file if it exists
        but is not valid JSON, rather than clobbering a user's config.
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
