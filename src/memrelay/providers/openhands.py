"""OpenHands provider — ingest-only (SPEC §2.1, E12-S5 #71).

OpenHands (formerly OpenDevin) records each session as a **directory of per-event JSON
files**::

    ~/.openhands/sessions/<session-id>/events/event-*.json   (override: home env)

Each ``event-*.json`` is one event object (``MessageEvent`` / ``ActionEvent`` /
``ObservationEvent`` …). The installed traceforge ``openhands.yaml`` mapping declares
``preprocessor: openhands`` (auto-applied inside ``MappedJsonAdapter.parse``), so
:meth:`make_adapter` is a single ``MappedJsonAdapter.from_yaml("openhands.yaml", session_id)``
fed one event object at a time.

The replay :class:`OpenHandsSource` is dual-mode: pointed at a real session's ``events``
directory it reads each ``event-*.json`` in order; pointed at a normalized JSONL trace (the
conformance fixture) it iterates lines. Either way each yielded item is one event object
ready for ``adapter.parse``.

**Serving:** OpenHands' MCP registry is TOML, which memrelay cannot write without a new
dependency, so it is **ingest-only** — the MCP hooks below raise and no config is mutated.
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

#: The single traceforge mapping for OpenHands; its ``preprocessor: openhands`` auto-applies.
OPENHANDS_MAPPING = "openhands.yaml"

DEFAULT_OPENHANDS_HOME = "~/.openhands"
SESSIONS_DIR = "sessions"
EVENTS_DIR = "events"
EVENT_GLOB = "event-*.json"

#: Env var that overrides the OpenHands home (mirrors ``MEMRELAY_COPILOT_HOME``).
OPENHANDS_HOME_ENV = "MEMRELAY_OPENHANDS_HOME"

#: OpenHands has no key-less host-borrow path in memrelay today; advertise the honest default.
LLM_STRATEGY = "byo-key"


def mapping_path(name: str) -> str:
    """Resolve a packaged traceforge mapping YAML to a filesystem path (see reference providers)."""
    resource = resources.files("traceforge.mappings").joinpath(name)
    return str(resource)


class OpenHandsSource:
    """A dual-mode replay source over an OpenHands session.

    If ``path`` is a directory it is treated as a session's ``events`` dir and each
    ``event-*.json`` is read in sorted order; if ``path`` is a file it is treated as a
    normalized JSONL trace and iterated line by line. Each yielded item is a single event
    object (JSON string) ready for :meth:`make_adapter`'s ``parse``.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def __iter__(self) -> Iterator[str]:
        if self.path.is_dir():
            for event_file in sorted(self.path.glob(EVENT_GLOB)):
                if event_file.is_file():
                    with open(event_file, encoding="utf-8") as fh:
                        obj = json.load(fh)
                    yield json.dumps(obj)
        else:
            with open(self.path, encoding="utf-8") as fh:
                for line in fh:
                    stripped = line.strip()
                    if stripped:
                        yield stripped


@register
class OpenHandsProvider(AgentProvider):
    """:class:`~memrelay.providers.base.AgentProvider` for OpenHands."""

    id = "openhands"

    def __init__(self, openhands_home: str | Path = DEFAULT_OPENHANDS_HOME) -> None:
        self.openhands_home = Path(openhands_home).expanduser()

    # ── construction / detection (registry seam) ─────────────────────────────

    @classmethod
    def from_home(cls, home: str | Path | None = None) -> OpenHandsProvider:
        """Build a provider, resolving the home (``MEMRELAY_OPENHANDS_HOME`` → ``~/.openhands``)."""
        if home is None:
            home = os.environ.get(OPENHANDS_HOME_ENV) or DEFAULT_OPENHANDS_HOME
        return cls(home)

    def is_present(self) -> bool:
        """True when ``~/.openhands/sessions`` exists (OpenHands has run in this home)."""
        return self.sessions_root.is_dir()

    # ── discovery ────────────────────────────────────────────────────────────

    @property
    def sessions_root(self) -> Path:
        return self.openhands_home / SESSIONS_DIR

    def discover_sessions(self) -> Iterable[SessionRef]:
        """Yield a :class:`SessionRef` per session dir with an ``events`` folder.

        The ref's path is the session's ``events`` dir (what :meth:`read_raw` /
        :meth:`make_source` iterate); the session id is the session dir name.
        """
        root = self.sessions_root
        if not root.is_dir():
            return
        for session_dir in sorted(root.iterdir()):
            events = session_dir / EVENTS_DIR
            if events.is_dir():
                yield SessionRef(session_id=session_dir.name, agent_id=self.id, path=str(events))

    def _resolve_events_dir(self, session_id: str) -> Path | None:
        events = self.sessions_root / session_id / EVENTS_DIR
        return events if events.is_dir() else None

    # ── (1) source + mapping ─────────────────────────────────────────────────

    def make_adapter(self, session_id: str) -> Any:
        """Build the ``openhands.yaml`` adapter for ``session_id`` (preprocessor runs)."""
        from traceforge import MappedJsonAdapter

        return MappedJsonAdapter.from_yaml(mapping_path(OPENHANDS_MAPPING), session_id)

    def read_raw(self, ref: SessionRef) -> Iterator[str]:
        """Yield each event object as a JSON string, ready for ``adapter.parse``."""
        if ref.path:
            events_dir: Path | None = Path(ref.path)
        else:
            events_dir = self._resolve_events_dir(ref.session_id)
        if events_dir is None:
            raise FileNotFoundError(f"no OpenHands events found for {ref.session_id!r}")
        yield from OpenHandsSource(events_dir)

    def make_source(
        self, session_id: str | None = None, *, path: str | Path | None = None
    ) -> OpenHandsSource:
        """Return a replay :class:`OpenHandsSource` over a session.

        Scope it by an explicit ``path`` (an ``events`` dir or a normalized JSONL trace) or
        by ``session_id`` (resolved to ``~/.openhands/sessions/<id>/events``).
        """
        if path is not None:
            target: Path | None = Path(path)
        elif session_id is not None:
            target = self._resolve_events_dir(session_id)
            if target is None:
                raise FileNotFoundError(f"no OpenHands events found for {session_id!r}")
        else:
            raise ValueError("make_source requires a session_id or an explicit path")
        return OpenHandsSource(target)

    # ── (2) LLM strategy ─────────────────────────────────────────────────────

    def llm_strategy(self) -> LLMStrategyHint:
        """Advertise OpenHands' default: bring-your-own-key (metadata only)."""
        return LLMStrategyHint(strategy=LLM_STRATEGY, host=None)

    # ── (3) serving / registration — ingest-only ─────────────────────────────

    @property
    def mcp_config_path(self) -> Path:
        """OpenHands is ingest-only in memrelay (its MCP registry is TOML — not writable here)."""
        raise NotImplementedError(
            "openhands is ingest-only: its MCP registry is TOML, which memrelay cannot write "
            "without a new dependency"
        )

    def mcp_server_entry(
        self, *, command: str = "memrelay", args: Iterable[str] = ("mcp",)
    ) -> dict[str, Any]:
        raise NotImplementedError("openhands is ingest-only: memrelay does not serve MCP to it")

    def register(self, *, command: str = "memrelay", args: Iterable[str] = ("mcp",)) -> Path:
        raise NotImplementedError("openhands is ingest-only: memrelay does not register MCP for it")
