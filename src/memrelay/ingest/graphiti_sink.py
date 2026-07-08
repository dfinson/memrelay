"""GraphitiSink + observe runner: visible ``SessionEvent``s → episode records → Spool.

Wave-3 Session C (E1 observe + minimal E2 assembly). This module owns the
*observation* half of memrelay's memory pipeline:

* :class:`GraphitiSink` — a traceforge ``StorageSink`` whose async ``on_event`` maps
  each **visible** ``SessionEvent`` carrying conversational text to an *episode
  record* (built via session B's ``EpisodeRecord.new``, the frozen 8-field schema B
  owns) and appends it to the durable **Spool**. ``flush``/``close`` are no-ops: the
  spool is durable, so there is no buffer to drain (SPEC §3.4).
* :func:`run_observe` — build and drive an ``EventPipeline`` (``Enricher``, no
  governance) over one discovered session's events, writing episode records into a
  Spool.

De-risk deltas, verified live against the installed traceforge 0.1.0 (documented in
the PR body; the E0 spike is in ``docs/e0-spike.md``):

* ``SessionEvent`` (a Pydantic model in ``traceforge.types``) has **no**
  ``.content``/``.text`` attribute. Conversational text lives in
  ``event.payload["content"]`` — a *kind-specific* dict key present only for
  ``message.*`` kinds. Non-message kinds (tool/hook/permission/turn/file/session)
  carry structured payloads with no message text.
* ``event.id`` is a fresh UUID on every parse; the **stable** identifier is
  ``event.raw_event["id"]`` (the agent's own wire id). It is used as ``event_id`` so
  re-observing a session yields the same ``idempotency_key`` and never double-ingests.
* ``event.metadata.visibility`` is assigned by the pipeline's ``Enricher`` — raw
  ``adapter.parse`` marks everything ``visible`` — so the visible-only filter must run
  **here**, downstream of enrichment.

Session B's spool + episode helpers (``Spool``, ``EpisodeRecord.new``,
``make_idempotency_key``) are imported **lazily** (inside functions) so this module,
and its unit tests which inject a fake spool + fake record factory, import cleanly
before B lands.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from traceforge import StorageSink

if TYPE_CHECKING:
    from traceforge import SessionEvent

    from memrelay.config import Config

#: Conversational message kinds worth remembering. Non-message kinds carry no
#: ``payload["content"]`` and are dropped by the content filter regardless; this
#: default additionally keeps system-prompt / harness noise (``message.system``) out
#: of memory while remaining fully configurable (issue #9).
DEFAULT_ALLOW_KINDS: frozenset[str] = frozenset({"message.user", "message.assistant"})

#: Provenance stamped on every episode record (the reference agent).
DEFAULT_SOURCE = "copilot"

#: ``(session_id, event_id, content) -> idempotency_key`` (session B's helper).
IdempotencyFn = Callable[[str | None, str | None, str], str]

#: Builds an episode record from the 8 frozen fields (session B's ``EpisodeRecord.new``).
RecordFactory = Callable[..., Any]


class SpoolLike(Protocol):
    """Duck type for session B's ``Spool`` (or a test fake): idempotent ``append``."""

    def append(self, record: dict[str, Any]) -> None:  # pragma: no cover - protocol
        ...


def _extract_content(event: SessionEvent) -> str:
    """Return *event*'s conversational text, or ``""`` when it carries none.

    The content-field delta: text lives in ``payload["content"]`` (only ``message.*``
    kinds have it), never on a top-level attribute.
    """
    payload = getattr(event, "payload", None)
    if not isinstance(payload, Mapping):
        return ""
    content = payload.get("content")
    return content.strip() if isinstance(content, str) else ""


def _stable_event_id(event: SessionEvent) -> str | None:
    """The agent's own wire id (``raw_event['id']``) — stable across re-parses.

    ``event.id`` is a fresh UUID per parse and must **not** be used for idempotency.
    """
    raw = getattr(event, "raw_event", None)
    if isinstance(raw, Mapping):
        value = raw.get("id")
        if isinstance(value, str):
            return value
    return None


def _default_idempotency_fn(session_id: str | None, event_id: str | None, content: str) -> str:
    # Lazy: session B owns ``ingest/episode.py`` and may not be merged yet.
    from memrelay.ingest.episode import make_idempotency_key

    return make_idempotency_key(session_id, event_id, content)


def _default_record_factory(**fields: Any) -> Any:
    # Lazy: session B owns ``ingest/episode.py``'s ``EpisodeRecord`` and may not be merged
    # yet. Delegating construction to B keeps C's records schema-locked to B's 8 fields.
    from memrelay.ingest.episode import EpisodeRecord

    return EpisodeRecord.new(**fields)


def build_episode_record(
    event: SessionEvent,
    *,
    namespace: str,
    repo: str | None,
    content: str,
    source: str = DEFAULT_SOURCE,
    idempotency_fn: IdempotencyFn | None = None,
    record_factory: RecordFactory | None = None,
) -> Any:
    """Assemble one episode record (session B's frozen schema) for *event*.

    Construction is delegated to session B's ``EpisodeRecord.new`` (lazy-imported, or an
    injected fake in tests) so C's records stay schema-locked to B's 8 frozen fields:
    ``content, namespace, repo, source, session_id, event_id, ts, idempotency_key``.
    ``content`` is passed in already-extracted/validated by the caller; ``event_id`` is
    the *stable* wire id and ``idempotency_key`` is computed via session B's
    ``make_idempotency_key`` (or an injected fake).
    """
    make_key = idempotency_fn or _default_idempotency_fn
    factory = record_factory or _default_record_factory
    session_id = getattr(event, "session_id", None)
    event_id = _stable_event_id(event)
    timestamp = getattr(event, "timestamp", None)
    ts_iso = timestamp.isoformat() if timestamp is not None else ""
    return factory(
        content=content,
        namespace=namespace,
        repo=repo,
        source=source,
        session_id=session_id,
        event_id=event_id,
        ts=ts_iso,
        idempotency_key=make_key(session_id, event_id, content),
    )


class GraphitiSink(StorageSink):
    """Map visible, content-bearing ``SessionEvent``s to episode records on the spool.

    Args:
        spool: session B's ``Spool`` (or a duck-typed fake) — ``append(record)`` is
            idempotent, so re-observation is safe.
        namespace: shared-memory scope for this session (from ``resolve_context``).
        repo: ``owner/name`` provenance, or ``None`` for a local/no-remote session.
        source: provenance tag stamped on every record (default ``"copilot"``).
        allow_kinds: if not ``None``, only these ``SessionEvent`` kinds may become
            episodes. Defaults to :data:`DEFAULT_ALLOW_KINDS`; pass ``None`` to allow
            every kind and rely solely on visibility + non-empty content.
        deny_kinds: kinds to always drop (applied before ``allow_kinds``).
        idempotency_fn: override for ``make_idempotency_key`` (injected in unit tests
            to stay decoupled from session B).
        record_factory: override for session B's ``EpisodeRecord.new`` (injected in unit
            tests to stay decoupled from session B).
    """

    def __init__(
        self,
        spool: SpoolLike,
        *,
        namespace: str,
        repo: str | None,
        source: str = DEFAULT_SOURCE,
        allow_kinds: Iterable[str] | None = DEFAULT_ALLOW_KINDS,
        deny_kinds: Iterable[str] = (),
        idempotency_fn: IdempotencyFn | None = None,
        record_factory: RecordFactory | None = None,
    ) -> None:
        self._spool = spool
        self._namespace = namespace
        self._repo = repo
        self._source = source
        self._allow_kinds = frozenset(allow_kinds) if allow_kinds is not None else None
        self._deny_kinds = frozenset(deny_kinds)
        self._idempotency_fn = idempotency_fn or _default_idempotency_fn
        self._record_factory = record_factory or _default_record_factory
        self.appended = 0
        self.skipped = 0

    def _kind_allowed(self, kind: str) -> bool:
        if kind in self._deny_kinds:
            return False
        return self._allow_kinds is None or kind in self._allow_kinds

    def _is_visible(self, event: SessionEvent) -> bool:
        meta = getattr(event, "metadata", None)
        return str(getattr(meta, "visibility", "")) == "visible"

    async def on_event(self, event: SessionEvent) -> None:
        # SPEC §3.4: only ``visible`` events (system/collapsed are dropped), then the
        # configurable kind allow/deny list (issue #9), then non-empty content.
        if not self._is_visible(event) or not self._kind_allowed(str(getattr(event, "kind", ""))):
            self.skipped += 1
            return
        content = _extract_content(event)
        if not content:
            self.skipped += 1
            return
        record = build_episode_record(
            event,
            namespace=self._namespace,
            repo=self._repo,
            content=content,
            source=self._source,
            idempotency_fn=self._idempotency_fn,
            record_factory=self._record_factory,
        )
        self._spool.append(record)
        self.appended += 1

    async def flush(self) -> None:  # noqa: D102 - durable spool: nothing to drain
        return None

    async def close(self) -> None:  # noqa: D102 - durable spool: nothing to close
        return None


def resolve_session_cwd(events_path: str | Path) -> str | None:
    """Read a session's working directory from its first ``session.start`` record.

    Copilot's ``session.start`` carries ``data.context.cwd`` — the directory the agent
    ran in, whose git remote drives the namespace (issue #10). Returns ``None`` if the
    file is unreadable or has no ``session.start`` with a string ``cwd``.
    """
    try:
        with open(Path(events_path), encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, Mapping) and record.get("type") == "session.start":
                    data = record.get("data")
                    context = data.get("context") if isinstance(data, Mapping) else None
                    cwd = context.get("cwd") if isinstance(context, Mapping) else None
                    return cwd if isinstance(cwd, str) else None
    except OSError:
        return None
    return None


@dataclass
class ObserveResult:
    """Outcome of observing one session."""

    session_id: str
    namespace: str
    repo: str | None
    parsed: int = 0
    appended: int = 0
    skipped: int = 0


async def run_observe(
    events_path: str | Path,
    session_id: str,
    *,
    spool: SpoolLike,
    source: str = DEFAULT_SOURCE,
    namespace_map: Mapping[str, str] | None = None,
    config: Config | None = None,
    cwd: str | None = None,
    allow_kinds: Iterable[str] | None = DEFAULT_ALLOW_KINDS,
    deny_kinds: Iterable[str] = (),
    idempotency_fn: IdempotencyFn | None = None,
    record_factory: RecordFactory | None = None,
    provider: Any | None = None,
) -> ObserveResult:
    """Observe one session: adapter → ``EventPipeline`` → :class:`GraphitiSink` → spool.

    The namespace/repo are resolved **once** from the session's own cwd (``cwd``
    override, else read from ``session.start``) via ``mcp.namespace.resolve_context`` —
    the exact function ``memory_recall`` uses, so ingested memory is findable. The ML
    inferencer flags come from :class:`~memrelay.config.IngestConfig` (default off);
    ``governance=None`` keeps observation opt-out (SPEC §3.3).
    """
    from traceforge import Enricher, EventPipeline

    from memrelay.config import load_config
    from memrelay.mcp.namespace import resolve_context
    from memrelay.providers.copilot import CopilotProvider

    cfg = config if config is not None else load_config()
    provider = provider if provider is not None else CopilotProvider()

    resolved_cwd = cwd if cwd is not None else resolve_session_cwd(events_path)
    namespace, repo = resolve_context(resolved_cwd, namespace_map)

    sink = GraphitiSink(
        spool,
        namespace=namespace,
        repo=repo,
        source=source,
        allow_kinds=allow_kinds,
        deny_kinds=deny_kinds,
        idempotency_fn=idempotency_fn,
        record_factory=record_factory,
    )
    pipeline = EventPipeline(
        sinks=[sink],
        enricher=Enricher(),
        governance=None,
        enable_phase=cfg.ingest.enable_phase,
        enable_boundary=cfg.ingest.enable_boundary,
    )
    adapter = provider.make_adapter(session_id)
    result = ObserveResult(session_id=session_id, namespace=namespace, repo=repo)
    try:
        for line in provider.make_source(session_id, path=str(events_path)):
            for event in adapter.parse(line):
                result.parsed += 1
                await pipeline.push(event)
        await pipeline.flush()
    finally:
        # Mirror the fixture_runner lifecycle: always close, even if push/flush raised.
        await pipeline.close()
    result.appended = sink.appended
    result.skipped = sink.skipped
    return result
