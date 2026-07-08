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

from collections.abc import Iterable, Iterator
from importlib import resources
from pathlib import Path
from typing import Any

from memrelay.providers.base import SessionRef

CANONICAL_MAPPING = "copilot.yaml"
FALLBACK_MAPPING = "copilot_markdown.yaml"

DEFAULT_COPILOT_HOME = "~/.copilot"
SESSION_STATE_DIR = "session-state"
EVENTS_FILENAME = "events.jsonl"
SESSION_STORE_DB = "session-store.db"


def mapping_path(name: str) -> str:
    """Resolve a packaged traceforge mapping YAML to a filesystem path.

    traceforge's ``from_yaml`` wants a real path, and ``traceforge.mappings`` has
    no name→path resolver, so we locate the file via ``importlib.resources``.
    """
    resource = resources.files("traceforge.mappings").joinpath(name)
    return str(resource)


class CopilotProvider:
    """Reference :class:`~memrelay.providers.base.AgentProvider` for Copilot CLI."""

    id = "copilot"

    def __init__(self, copilot_home: str | Path = DEFAULT_COPILOT_HOME) -> None:
        self.copilot_home = Path(copilot_home).expanduser()

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

    def make_source(self) -> Any:
        """The *live* source is a daemon-epic concern; E0 replays via ``read_raw``."""
        raise NotImplementedError(
            "live Copilot source (file-watch) is implemented in the daemon epic; "
            "E0 replays sessions via read_raw()/events.jsonl"
        )

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
