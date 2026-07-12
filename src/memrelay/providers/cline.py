"""Cline provider — MCP-serving (SPEC §2.1, E12-S5 #71).

Cline (the VS Code agent, extension id ``saoudrizwan.claude-dev``) records each task as a
**JSON array** of UI messages::

    <globalStorage>/saoudrizwan.claude-dev/tasks/<task-id>/ui_messages.json

Each array element is one UI message ``{ts, type, say|ask, text}`` where ``type`` is
``say`` or ``ask`` and the ``say``/``ask`` field is the sub-kind (``task``, ``text``,
``tool``, ``completion_result`` …). The installed traceforge ``cline.yaml`` mapping declares
``preprocessor: cline`` (auto-applied inside ``MappedJsonAdapter.parse``) which resolves that
compound ``type``/``say``/``ask`` shape, so this provider loads the array and yields each
element to a single ``MappedJsonAdapter.from_yaml("cline.yaml", session_id)``.

**Serving:** Cline's MCP registry is JSON (``settings/cline_mcp_settings.json``), so memrelay
**registers** into it with the same non-destructive JSON merge as the reference providers.

The default home is a stand-in (``~/.cline``); a real install lives under the VS Code
``globalStorage`` dir for ``saoudrizwan.claude-dev`` — set ``MEMRELAY_CLINE_HOME`` to point at
it (e.g. ``%APPDATA%/Code/User/globalStorage/saoudrizwan.claude-dev`` on Windows).
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

#: The single traceforge mapping for Cline; its ``preprocessor: cline`` auto-applies.
CLINE_MAPPING = "cline.yaml"

DEFAULT_CLINE_HOME = "~/.cline"
TASKS_DIR = "tasks"
UI_MESSAGES_FILENAME = "ui_messages.json"

#: Cline's JSON MCP registry (under the extension's ``settings`` dir) and the memrelay key.
MCP_CONFIG_RELPATH = ("settings", "cline_mcp_settings.json")
MCP_SERVER_KEY = "memrelay"

#: Env var that overrides the Cline home (mirrors ``MEMRELAY_COPILOT_HOME``).
CLINE_HOME_ENV = "MEMRELAY_CLINE_HOME"

#: Cline has no key-less host-borrow path in memrelay today; advertise the honest default.
LLM_STRATEGY = "byo-key"


def mapping_path(name: str) -> str:
    """Resolve a packaged traceforge mapping YAML to a filesystem path (see reference providers)."""
    resource = resources.files("traceforge.mappings").joinpath(name)
    return str(resource)


class ClineSource:
    """A replay source over a Cline task's ``ui_messages.json`` array.

    The task log is a whole JSON array (not line-delimited), so this reads it and yields
    each element as a JSON string ready for :meth:`make_adapter`'s ``parse``. A non-list
    document degrades to yielding the single object.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def __iter__(self) -> Iterator[str]:
        with open(self.path, encoding="utf-8") as fh:
            obj = json.load(fh)
        items = obj if isinstance(obj, list) else [obj]
        for item in items:
            yield json.dumps(item)


@register
class ClineProvider(AgentProvider):
    """:class:`~memrelay.providers.base.AgentProvider` for Cline."""

    id = "cline"

    def __init__(self, cline_home: str | Path = DEFAULT_CLINE_HOME) -> None:
        self.cline_home = Path(cline_home).expanduser()

    # ── construction / detection (registry seam) ─────────────────────────────

    @classmethod
    def from_home(cls, home: str | Path | None = None) -> ClineProvider:
        """Build a provider, resolving the home (``MEMRELAY_CLINE_HOME`` → ``~/.cline``)."""
        if home is None:
            home = os.environ.get(CLINE_HOME_ENV) or DEFAULT_CLINE_HOME
        return cls(home)

    def is_present(self) -> bool:
        """True when the Cline ``tasks`` dir exists (Cline has recorded tasks in this home)."""
        return self.tasks_root.is_dir()

    # ── discovery ────────────────────────────────────────────────────────────

    @property
    def tasks_root(self) -> Path:
        return self.cline_home / TASKS_DIR

    def discover_sessions(self) -> Iterable[SessionRef]:
        """Yield a :class:`SessionRef` per task ``ui_messages.json`` (id = task dir name)."""
        root = self.tasks_root
        if not root.is_dir():
            return
        for task_dir in sorted(root.iterdir()):
            log = task_dir / UI_MESSAGES_FILENAME
            if log.is_file():
                yield SessionRef(session_id=task_dir.name, agent_id=self.id, path=str(log))

    def _resolve_session_path(self, session_id: str) -> Path | None:
        candidate = self.tasks_root / session_id / UI_MESSAGES_FILENAME
        return candidate if candidate.is_file() else None

    # ── (1) source + mapping ─────────────────────────────────────────────────

    def make_adapter(self, session_id: str) -> Any:
        """Build the ``cline.yaml`` adapter scoped to ``session_id`` (preprocessor auto-applies)."""
        from traceforge import MappedJsonAdapter

        return MappedJsonAdapter.from_yaml(mapping_path(CLINE_MAPPING), session_id)

    def read_raw(self, ref: SessionRef) -> Iterator[str]:
        """Yield each ``ui_messages.json`` array element as a JSON string for ``adapter.parse``."""
        if ref.path:
            path: Path | None = Path(ref.path)
        else:
            path = self._resolve_session_path(ref.session_id)
        if path is None:
            raise FileNotFoundError(f"no Cline task log found for {ref.session_id!r}")
        yield from ClineSource(path)

    def make_source(
        self, session_id: str | None = None, *, path: str | Path | None = None
    ) -> ClineSource:
        """Return a replay :class:`ClineSource` over a task log.

        Scope it by an explicit ``path`` or by ``session_id`` (resolved under
        ``<home>/tasks/<id>/ui_messages.json``).
        """
        if path is not None:
            log_path: Path | None = Path(path)
        elif session_id is not None:
            log_path = self._resolve_session_path(session_id)
            if log_path is None:
                raise FileNotFoundError(f"no Cline task log found for {session_id!r}")
        else:
            raise ValueError("make_source requires a session_id or an explicit path")
        return ClineSource(log_path)

    # ── (2) LLM strategy ─────────────────────────────────────────────────────

    def llm_strategy(self) -> LLMStrategyHint:
        """Advertise Cline's default: bring-your-own-key (metadata only)."""
        return LLMStrategyHint(strategy=LLM_STRATEGY, host=None)

    # ── (3) serving / registration ───────────────────────────────────────────

    @property
    def mcp_config_path(self) -> Path:
        """Path to Cline's JSON MCP registry (``<home>/settings/cline_mcp_settings.json``)."""
        return self.cline_home.joinpath(*MCP_CONFIG_RELPATH)

    def mcp_server_entry(
        self, *, command: str = "memrelay", args: Iterable[str] = ("mcp",)
    ) -> dict[str, Any]:
        """The stdio ``memrelay mcp`` entry Cline spawns (``type: stdio``)."""
        return {
            "type": "stdio",
            "command": command,
            "args": list(args),
            "env": {},
        }

    def register(self, *, command: str = "memrelay", args: Iterable[str] = ("mcp",)) -> Path:
        """Merge the memrelay stdio server into Cline's ``cline_mcp_settings.json``.

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
