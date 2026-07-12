"""SWE-agent provider — ingest-only (SPEC §2.1, E12-S5 #71).

SWE-agent writes one **trajectory** JSON file per run (``*.traj``) whose ``history`` array
holds the message turns (``{role, content}`` with ``role`` in ``system``/``user``/
``assistant``/``tool``). The installed traceforge ``sweagent.yaml`` mapping reads those turn
records **directly** (no preprocessor; ``type`` discriminator is the ``role`` field), so this
provider loads the trajectory and yields each ``history`` entry to a single
``MappedJsonAdapter.from_yaml("sweagent.yaml", session_id)``.

**Serving:** SWE-agent is not an MCP host, so it is **ingest-only** — the MCP hooks below
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

#: The single traceforge mapping for SWE-agent (no preprocessor; direct ``role`` mapping).
SWEAGENT_MAPPING = "sweagent.yaml"

DEFAULT_SWEAGENT_HOME = "~/.swe-agent"
SESSION_GLOB = "*.traj"

#: Env var that overrides the SWE-agent home (mirrors ``MEMRELAY_COPILOT_HOME``).
SWEAGENT_HOME_ENV = "MEMRELAY_SWEAGENT_HOME"

#: SWE-agent has no key-less host-borrow path in memrelay today; advertise the honest default.
LLM_STRATEGY = "byo-key"


def mapping_path(name: str) -> str:
    """Resolve a packaged traceforge mapping YAML to a filesystem path (see reference providers)."""
    resource = resources.files("traceforge.mappings").joinpath(name)
    return str(resource)


def _iter_history(path: Path) -> Iterator[str]:
    """Load a ``*.traj`` and yield each ``history`` entry as a JSON string for ``adapter.parse``."""
    with open(path, encoding="utf-8") as fh:
        obj = json.load(fh)
    history = obj.get("history", []) if isinstance(obj, dict) else []
    for entry in history:
        yield json.dumps(entry)


class SweAgentSource:
    """A replay source over a SWE-agent trajectory's ``history`` turns.

    A ``*.traj`` is a whole JSON document, not line-delimited, so this reads it and yields
    each ``history`` entry (a ``{role, content}`` turn) as a JSON string.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def __iter__(self) -> Iterator[str]:
        yield from _iter_history(self.path)


@register
class SweAgentProvider(AgentProvider):
    """:class:`~memrelay.providers.base.AgentProvider` for SWE-agent."""

    id = "sweagent"

    def __init__(self, sweagent_home: str | Path = DEFAULT_SWEAGENT_HOME) -> None:
        self.sweagent_home = Path(sweagent_home).expanduser()

    # ── construction / detection (registry seam) ─────────────────────────────

    @classmethod
    def from_home(cls, home: str | Path | None = None) -> SweAgentProvider:
        """Build a provider, resolving the home (``MEMRELAY_SWEAGENT_HOME`` → ``~/.swe-agent``)."""
        if home is None:
            home = os.environ.get(SWEAGENT_HOME_ENV) or DEFAULT_SWEAGENT_HOME
        return cls(home)

    def is_present(self) -> bool:
        """True when the SWE-agent home dir exists (trajectories may be recorded here)."""
        return self.sweagent_home.is_dir()

    # ── discovery ────────────────────────────────────────────────────────────

    def discover_sessions(self) -> Iterable[SessionRef]:
        """Yield a :class:`SessionRef` per ``*.traj`` trajectory (session id = file stem).

        SWE-agent nests trajectories under per-run output dirs, so we glob recursively.
        """
        root = self.sweagent_home
        if not root.is_dir():
            return
        for traj in sorted(root.rglob(SESSION_GLOB)):
            if traj.is_file():
                yield SessionRef(session_id=traj.stem, agent_id=self.id, path=str(traj))

    def _resolve_session_path(self, session_id: str) -> Path | None:
        root = self.sweagent_home
        if not root.is_dir():
            return None
        for traj in sorted(root.rglob(SESSION_GLOB)):
            if traj.is_file() and traj.stem == session_id:
                return traj
        return None

    # ── (1) source + mapping ─────────────────────────────────────────────────

    def make_adapter(self, session_id: str) -> Any:
        """Build the ``sweagent.yaml`` adapter scoped to ``session_id`` (no preprocessor)."""
        from traceforge import MappedJsonAdapter

        return MappedJsonAdapter.from_yaml(mapping_path(SWEAGENT_MAPPING), session_id)

    def read_raw(self, ref: SessionRef) -> Iterator[str]:
        """Yield each trajectory ``history`` turn as a JSON string, ready for ``adapter.parse``."""
        if ref.path:
            path: Path | None = Path(ref.path)
        else:
            path = self._resolve_session_path(ref.session_id)
        if path is None:
            raise FileNotFoundError(f"no SWE-agent trajectory found for {ref.session_id!r}")
        yield from _iter_history(path)

    def make_source(
        self, session_id: str | None = None, *, path: str | Path | None = None
    ) -> SweAgentSource:
        """Return a replay :class:`SweAgentSource` over a trajectory.

        Scope it by an explicit ``path`` or by ``session_id`` (resolved by scanning
        ``~/.swe-agent/**/<id>.traj``).
        """
        if path is not None:
            traj_path: Path | None = Path(path)
        elif session_id is not None:
            traj_path = self._resolve_session_path(session_id)
            if traj_path is None:
                raise FileNotFoundError(f"no SWE-agent trajectory found for {session_id!r}")
        else:
            raise ValueError("make_source requires a session_id or an explicit path")
        return SweAgentSource(traj_path)

    # ── (2) LLM strategy ─────────────────────────────────────────────────────

    def llm_strategy(self) -> LLMStrategyHint:
        """Advertise SWE-agent's default: bring-your-own-key (metadata only)."""
        return LLMStrategyHint(strategy=LLM_STRATEGY, host=None)

    # ── (3) serving / registration — ingest-only ─────────────────────────────

    @property
    def mcp_config_path(self) -> Path:
        """SWE-agent is ingest-only in memrelay (SWE-agent is not an MCP host)."""
        raise NotImplementedError("sweagent is ingest-only: SWE-agent is not an MCP host")

    def mcp_server_entry(
        self, *, command: str = "memrelay", args: Iterable[str] = ("mcp",)
    ) -> dict[str, Any]:
        raise NotImplementedError("sweagent is ingest-only: memrelay does not serve MCP to it")

    def register(self, *, command: str = "memrelay", args: Iterable[str] = ("mcp",)) -> Path:
        raise NotImplementedError("sweagent is ingest-only: memrelay does not register MCP for it")
