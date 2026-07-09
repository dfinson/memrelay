"""GraphitiSink + observe runner: a ``SessionEvent`` stream → composed episodes → Spool.

This module owns the *observation* half of memrelay's memory pipeline and, as of
Epic E2 (Episode Assembly, #25/#26/#27), the memrelay-specific **episode assembler**
that turns a normalized event stream into *coherent* episodic memory:

* :class:`GraphitiSink` — a traceforge ``StorageSink`` whose async ``on_event``
  **buffers** a run of events into one coherent work-unit and **flushes** a single
  *composed* episode record on structural boundary signals (``tool.call.completed``,
  ``turn.ended``, ``session.idle``, ``session.ended`` — additionally honoring
  ``metadata.boundary``/``activity_id`` when present). It renders *each relevant kind*
  into content (#26: user/assistant messages, tool executions, file changes), and on
  ``session.ended`` emits one additional deterministic **summary** episode (#27). The
  buffer is drained by ``flush``/``close`` at end-of-stream, so the final partial
  work-unit is never lost; both are idempotent (safe to call twice).
* :func:`run_observe` — build and drive an ``EventPipeline`` (``Enricher``, no
  governance) over one discovered session's events, writing composed episode records
  into a Spool.

**Boundary strategy (the E2 design fork, resolved to structural-from-kinds):**
memrelay keeps traceforge's boundary/phase ML classifiers *off* by default
(``IngestConfig.enable_boundary``/``enable_phase`` = False), so ``metadata.boundary``
and ``activity_id`` are unpopulated in practice — and flipping them on requires an
optional embedding model that is absent in the headless/offline ingest environment.
The assembler therefore derives boundaries from *event kinds*, which the adapter
always emits, keeping ingestion deterministic and hermetic (no LLM/ML at ingest time).
``metadata.boundary``/``activity_id`` are still honored *additionally* when present.

**Composed idempotency:** a composed episode spans many events, so its stable
``idempotency_key`` is derived from the ordered stable wire ids of the buffered span
(via :func:`_segment_id` → the injected ``idempotency_fn``). Re-observing the same
session yields identical keys and appends zero new spool rows (the spool dedups on the
key).

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

import hashlib
import json
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from traceforge import StorageSink

if TYPE_CHECKING:
    from traceforge import SessionEvent

    from memrelay.config import Config

#: Kinds the assembler renders into episode **content** (#26). Non-message kinds now
#: contribute too: ``tool.call.completed`` (intent + success + touched files + result)
#: and ``file.edited`` (path + operation). ``message.system`` (harness noise) stays out.
#: Boundary-signal kinds that carry no content (``turn.ended`` etc.) are *not* listed —
#: their flush role is structural and independent of this content allow-list (issue #9).
DEFAULT_ALLOW_KINDS: frozenset[str] = frozenset(
    {"message.user", "message.assistant", "tool.call.completed", "file.edited"}
)

#: Provenance stamped on every episode record (the reference agent).
DEFAULT_SOURCE = "copilot"

#: Structural signals that close the current work-unit and flush a composed episode
#: (#25). Always emitted by the adapter, so boundary detection needs no ML classifier.
BOUNDARY_KINDS: frozenset[str] = frozenset(
    {"tool.call.completed", "turn.ended", "session.idle", "session.ended"}
)

#: The end-of-session signal that additionally triggers the summary episode (#27).
SESSION_END_KIND = "session.ended"

#: ``arguments`` keys whose string value names a single touched file (best-effort,
#: adapter-agnostic — real file edits arrive as filesystem tool calls, not
#: ``file.edited`` events, so touched files are mined from the tool ``arguments``).
_FILE_ARG_KEYS: tuple[str, ...] = ("path", "file_path", "filepath", "filename", "file")
#: ``arguments`` keys whose value is a list of touched files.
_FILE_LIST_KEYS: tuple[str, ...] = ("paths", "files")

#: Truncation bounds keeping composed episodes lean and offline (tool ``result`` can be
#: 20KB+). Applied deterministically so re-observation yields byte-identical content.
MAX_INTENT_CHARS = 280
#: Tool-result bound. Sized above the empirical median real-session result (~797 chars)
#: so the typical tool outcome survives whole, while pathological 20KB+ dumps stay bounded.
MAX_RESULT_CHARS = 800
MAX_DECISION_CHARS = 280
MAX_EPISODE_CHARS = 4000
MAX_SUMMARY_CHARS = 4000
#: Appended when a string is truncated.
TRUNCATION_MARKER = " …[truncated]"

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


def _event_phase(event: SessionEvent) -> str | None:
    """Read traceforge's stamped workflow phase off an event, as a plain string.

    ``metadata.phase`` is a :class:`traceforge.classify.workflow.Phase` (a ``StrEnum``)
    when ``enable_phase`` is on, else ``None``. Returns ``None`` when phase is off or
    unset, so the phase-off path stays exactly as it was (E2-S6 #98).
    """
    meta = getattr(event, "metadata", None)
    phase = getattr(meta, "phase", None)
    return None if phase is None else str(phase)


def _derive_phase(phases: Iterable[str | None]) -> str | None:
    """Reduce a span's per-event phases to one episode phase: dominant, ties→last.

    The composed episode spans many events that can carry different phases, so the
    episode's phase is *derived* (F1): the majority phase over the span's
    content-bearing events, breaking ties in favour of the most recent. This is a
    pure read-only reduction over the already-composed span — it never re-segments
    the buffer, so idempotency keys (derived from the span's ids, not its phases)
    are untouched. Returns ``None`` when no event carried a phase (phase-off).
    """
    present = [p for p in phases if p]
    if not present:
        return None
    counts = Counter(present)
    top = max(counts.values())
    tied = {p for p, c in counts.items() if c == top}
    for phase in reversed(present):
        if phase in tied:
            return phase
    return None  # unreachable: ``tied`` is non-empty and drawn from ``present``


def _default_idempotency_fn(session_id: str | None, event_id: str | None, content: str) -> str:
    # Lazy: session B owns ``ingest/episode.py`` and may not be merged yet.
    from memrelay.ingest.episode import make_idempotency_key

    return make_idempotency_key(session_id, event_id, content)


def _default_record_factory(**fields: Any) -> dict[str, Any]:
    # Lazy: session B owns ``ingest/episode.py``'s ``EpisodeRecord``. ``EpisodeRecord.new``
    # returns an ``EpisodeRecord``; the spool's ``append`` wants the plain dict, so
    # serialise via ``.to_dict()``. Delegating construction to B keeps C's records
    # schema-locked to B's frozen ``EPISODE_FIELDS`` (which now includes ``phase``, #98).
    from memrelay.ingest.episode import EpisodeRecord

    return EpisodeRecord.new(**fields).to_dict()


def _assemble_record(
    *,
    content: str,
    namespace: str,
    repo: str | None,
    source: str,
    session_id: str | None,
    event_id: str | None,
    ts_iso: str,
    idempotency_fn: IdempotencyFn | None,
    record_factory: RecordFactory | None,
    phase: str | None = None,
) -> Any:
    """Build one episode record from explicit fields via the injected seams.

    Shared by the single-event :func:`build_episode_record` and the composed/summary
    paths in :class:`GraphitiSink`, so every record — regardless of how many events it
    spans — is schema-locked to session B's frozen fields and keyed the same way.

    ``phase`` (E2-S6 #98) is the derived episode phase (or ``None`` when phase is off).
    It rides along as a sidecar field and is deliberately **not** an input to the
    idempotency key: the key is still computed from the phase-free ``content``, so a
    record's key is byte-identical whether or not phase enrichment is enabled.
    """
    make_key = idempotency_fn or _default_idempotency_fn
    factory = record_factory or _default_record_factory
    return factory(
        content=content,
        namespace=namespace,
        repo=repo,
        source=source,
        session_id=session_id,
        event_id=event_id,
        ts=ts_iso,
        idempotency_key=make_key(session_id, event_id, content),
        phase=phase,
    )


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
    injected fake in tests) so C's records stay schema-locked to B's frozen fields:
    ``content, namespace, repo, source, session_id, event_id, ts, idempotency_key`` and
    the opt-in ``phase`` (#98, left ``None`` on this single-event path — phase is
    *derived* per composed episode by :class:`GraphitiSink`, not per raw event).
    ``content`` is passed in already-extracted/validated by the caller; ``event_id`` is
    the *stable* wire id and ``idempotency_key`` is computed via session B's
    ``make_idempotency_key`` (or an injected fake).
    """
    timestamp = getattr(event, "timestamp", None)
    return _assemble_record(
        content=content,
        namespace=namespace,
        repo=repo,
        source=source,
        session_id=getattr(event, "session_id", None),
        event_id=_stable_event_id(event),
        ts_iso=timestamp.isoformat() if timestamp is not None else "",
        idempotency_fn=idempotency_fn,
        record_factory=record_factory,
    )


def _truncate(text: str, limit: int) -> str:
    """Deterministically cap *text* at *limit* chars, marking any truncation."""
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + TRUNCATION_MARKER


def _extract_touched_files(arguments: Any) -> list[str]:
    """Best-effort ordered-unique file paths named in a tool call's ``arguments``.

    Real Copilot file edits arrive as filesystem tool calls (the path lives in the
    tool ``arguments``), *not* as ``file.edited`` events, so the tool renderer and the
    session summary mine touched files from here. Adapter-agnostic and value-only: it
    never inspects file *contents*.
    """
    if not isinstance(arguments, Mapping):
        return []
    found: list[str] = []
    for key in _FILE_ARG_KEYS:
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            found.append(value.strip())
    for key in _FILE_LIST_KEYS:
        value = arguments.get(key)
        if isinstance(value, (list, tuple)):
            found.extend(item.strip() for item in value if isinstance(item, str) and item.strip())
    seen: set[str] = set()
    unique: list[str] = []
    for path in found:
        if path not in seen:
            seen.add(path)
            unique.append(path)
    return unique


def _segment_id(stable_ids: Iterable[str | None]) -> str:
    """A deterministic id for a composed span: a hash of its ordered stable wire ids.

    Re-observing the same session replays the same wire ids in the same order, so the
    segment id — and therefore the derived ``idempotency_key`` — is identical across
    runs and the spool appends zero new rows.
    """
    hasher = hashlib.sha256()
    for stable_id in stable_ids:
        hasher.update((stable_id or "").encode("utf-8"))
        hasher.update(b"\x00")
    return hasher.hexdigest()


def _summary_segment_id(session_id: str | None, stable_ids: Iterable[str | None]) -> str:
    """A stable segment id for the summary episode, distinct from any work-unit id."""
    hasher = hashlib.sha256()
    hasher.update(b"summary\x00")
    hasher.update((session_id or "").encode("utf-8"))
    hasher.update(b"\x00")
    for stable_id in stable_ids:
        hasher.update((stable_id or "").encode("utf-8"))
        hasher.update(b"\x00")
    return hasher.hexdigest()


def _motivation_intent(event: SessionEvent) -> str | None:
    """The agent's stated intent for a tool call (``metadata.motivation.intent``)."""
    meta = getattr(event, "metadata", None)
    motivation = getattr(meta, "motivation", None)
    intent = getattr(motivation, "intent", None)
    if isinstance(intent, str) and intent.strip():
        return intent.strip()
    return None


def _render_tool(event: SessionEvent) -> str:
    """Render a ``tool.call.completed`` into content (#26): intent + success + files + result.

    The enricher pairs ``tool.call.started`` → ``tool.call.completed`` and merges the
    result/success/arguments onto the completed event, so this single kind carries
    everything the step produced. Always returns a non-empty block (at minimum the tool
    name), since a tool completion is a coherent unit worth remembering.
    """
    payload = getattr(event, "payload", None)
    payload = payload if isinstance(payload, Mapping) else {}
    tool_name = payload.get("tool_name")
    tool_name = tool_name.strip() if isinstance(tool_name, str) and tool_name.strip() else "tool"
    lines = [f"Tool: {tool_name}"]
    intent = _motivation_intent(event)
    if intent:
        lines.append(f"Intent: {_truncate(intent, MAX_INTENT_CHARS)}")
    success = payload.get("success")
    if isinstance(success, bool):
        lines.append(f"Outcome: {'succeeded' if success else 'failed'}")
    files = _extract_touched_files(payload.get("arguments"))
    if files:
        lines.append(f"Files: {', '.join(files)}")
    result = payload.get("result")
    if isinstance(result, str) and result.strip():
        lines.append(f"Result: {_truncate(result.strip(), MAX_RESULT_CHARS)}")
    return "\n".join(lines)


def _tool_outcome_line(event: SessionEvent) -> str:
    """A one-line tool outcome for the session summary: ``name status — intent``."""
    payload = getattr(event, "payload", None)
    payload = payload if isinstance(payload, Mapping) else {}
    tool_name = payload.get("tool_name")
    tool_name = tool_name.strip() if isinstance(tool_name, str) and tool_name.strip() else "tool"
    success = payload.get("success")
    status = "succeeded" if success is True else "failed" if success is False else "completed"
    line = f"{tool_name} {status}"
    intent = _motivation_intent(event)
    if intent:
        line += f" — {_truncate(intent, MAX_INTENT_CHARS)}"
    return line


def _render_file(event: SessionEvent) -> str | None:
    """Render a ``file.edited`` into content (#26): the path + operation, no file body."""
    payload = getattr(event, "payload", None)
    payload = payload if isinstance(payload, Mapping) else {}
    path = payload.get("path")
    if not isinstance(path, str) or not path.strip():
        return None
    operation = payload.get("operation")
    operation = operation.strip() if isinstance(operation, str) and operation.strip() else "changed"
    return f"Changed file: {path.strip()} ({operation})"


def _render_content_piece(event: SessionEvent, kind: str) -> str | None:
    """Map one event to its episode-content text (#26), or ``None`` if it carries none.

    * ``message.user`` → its content **verbatim** (a lone-user work-unit is exactly the
      message, so recall over single-message sessions is unchanged).
    * ``message.assistant`` → its content as a decision/outcome line; empty (tool-only
      turns) contributes nothing — decisions/outcomes are favored over absent prose.
    * ``tool.call.completed`` / ``file.edited`` → the structured renderers above.
    * any other allowed kind → its ``payload['content']`` verbatim, if present.
    """
    if kind == "tool.call.completed":
        return _render_tool(event)
    if kind == "file.edited":
        return _render_file(event)
    return _extract_content(event) or None


class GraphitiSink(StorageSink):
    """Assemble a ``SessionEvent`` stream into coherent, composed episode records.

    Rather than emitting one episode per event, the sink **buffers** a run of events
    into a work-unit and **flushes** a single composed episode on the next structural
    boundary (:data:`BOUNDARY_KINDS`, plus ``metadata.boundary``/``activity_id`` when
    present). Each relevant kind is rendered into content (#26); on ``session.ended`` an
    additional deterministic summary episode is emitted (#27). The trailing partial
    work-unit is drained by :meth:`flush`/:meth:`close` at end-of-stream.

    Args:
        spool: session B's ``Spool`` (or a duck-typed fake) — ``append(record)`` is
            idempotent on ``idempotency_key``, so re-observation appends no new rows.
        namespace: shared-memory scope for this session (from ``resolve_context``).
        repo: ``owner/name`` provenance, or ``None`` for a local/no-remote session.
        source: provenance tag stamped on every record (default ``"copilot"``).
        allow_kinds: kinds whose **content** may enter a work-unit. Defaults to
            :data:`DEFAULT_ALLOW_KINDS`; pass ``None`` to buffer content from every
            visible kind. Structural flush signals fire by kind regardless of this list.
        deny_kinds: kinds whose content is always dropped (applied before ``allow_kinds``).
        idempotency_fn: override for ``make_idempotency_key`` (injected in unit tests).
        record_factory: override for ``EpisodeRecord.new`` (injected in unit tests).
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
        # --- current work-unit buffer ---
        self._buffer_pieces: list[str] = []
        self._buffer_ids: list[str | None] = []
        self._buffer_phases: list[str | None] = []
        self._buffer_ts: str | None = None
        self._buffer_session_id: str | None = None
        self._activity_id: str | None = None
        # --- session-level accumulators for the summary episode (#27) ---
        self._all_ids: list[str | None] = []
        self._all_phases: list[str | None] = []
        self._decisions: list[str] = []
        self._tool_outcomes: list[str] = []
        self._touched_files: list[str] = []
        self._touched_seen: set[str] = set()
        self._seen_content = False
        self._summary_emitted = False

    def _kind_allowed(self, kind: str) -> bool:
        if kind in self._deny_kinds:
            return False
        return self._allow_kinds is None or kind in self._allow_kinds

    def _is_visible(self, event: SessionEvent) -> bool:
        meta = getattr(event, "metadata", None)
        return str(getattr(meta, "visibility", "")) == "visible"

    async def on_event(self, event: SessionEvent) -> None:
        kind = str(getattr(event, "kind", ""))
        meta = getattr(event, "metadata", None)

        # (1) Defensive metadata boundary (no-op with the classifier off): an event that
        # *opens* a new activity/step closes the prior work-unit before its content joins
        # the new one. See traceforge.boundary — ``boundary`` is set on the opening event.
        boundary = getattr(meta, "boundary", None)
        activity_id = getattr(meta, "activity_id", None)
        if boundary is not None or (activity_id is not None and activity_id != self._activity_id):
            self._flush_segment()
        if activity_id is not None:
            self._activity_id = activity_id

        # (2) Buffer this event's rendered content, gated by visibility + allow/deny.
        # Structural signals (step 3) still fire even when an event carries no content.
        piece = None
        if self._is_visible(event) and self._kind_allowed(kind):
            piece = _render_content_piece(event, kind)
        if piece:
            self._buffer(event, kind, piece)
        else:
            self.skipped += 1

        # (3) Structural flush: close the work-unit on a boundary kind; on session end
        # additionally emit the deterministic summary episode.
        if kind in BOUNDARY_KINDS:
            self._flush_segment()
            if kind == SESSION_END_KIND:
                self._emit_summary(event)

    def _buffer(self, event: SessionEvent, kind: str, piece: str) -> None:
        if not self._buffer_pieces:
            ts = getattr(event, "timestamp", None)
            self._buffer_ts = ts.isoformat() if ts is not None else ""
            self._buffer_session_id = getattr(event, "session_id", None)
        stable_id = _stable_event_id(event)
        phase = _event_phase(event)
        self._buffer_pieces.append(piece)
        self._buffer_ids.append(stable_id)
        self._buffer_phases.append(phase)
        self._all_ids.append(stable_id)
        self._all_phases.append(phase)
        self._seen_content = True
        self._accumulate_summary(event, kind, piece)

    def _accumulate_summary(self, event: SessionEvent, kind: str, piece: str) -> None:
        """Feed the session-level decision/outcome/file accumulators (#27)."""
        if kind == "message.assistant":
            self._decisions.append(_truncate(piece, MAX_DECISION_CHARS))
        elif kind == "tool.call.completed":
            self._tool_outcomes.append(_tool_outcome_line(event))
            payload = getattr(event, "payload", None)
            payload = payload if isinstance(payload, Mapping) else {}
            for path in _extract_touched_files(payload.get("arguments")):
                self._add_touched_file(path)
        elif kind == "file.edited":
            payload = getattr(event, "payload", None)
            payload = payload if isinstance(payload, Mapping) else {}
            path = payload.get("path")
            if isinstance(path, str) and path.strip():
                self._add_touched_file(path.strip())

    def _add_touched_file(self, path: str) -> None:
        if path not in self._touched_seen:
            self._touched_seen.add(path)
            self._touched_files.append(path)

    def _flush_segment(self) -> None:
        """Emit one composed episode for the buffered work-unit, then clear it.

        A no-op when the buffer is empty, so end-of-stream ``flush``/``close`` (which the
        pipeline invokes twice) is idempotent.
        """
        if not self._buffer_pieces:
            return
        content = _truncate("\n\n".join(self._buffer_pieces), MAX_EPISODE_CHARS)
        record = _assemble_record(
            content=content,
            namespace=self._namespace,
            repo=self._repo,
            source=self._source,
            session_id=self._buffer_session_id,
            event_id=_segment_id(self._buffer_ids),
            ts_iso=self._buffer_ts or "",
            idempotency_fn=self._idempotency_fn,
            record_factory=self._record_factory,
            phase=_derive_phase(self._buffer_phases),
        )
        self._spool.append(record)
        self.appended += 1
        self._buffer_pieces = []
        self._buffer_ids = []
        self._buffer_phases = []
        self._buffer_ts = None
        self._buffer_session_id = None

    def _emit_summary(self, event: SessionEvent) -> None:
        """Emit one bounded, deterministic summary episode on ``session.ended`` (#27)."""
        if self._summary_emitted or not self._seen_content:
            return
        body = self._compose_summary()
        if not body:
            return
        ts = getattr(event, "timestamp", None)
        session_id = getattr(event, "session_id", None)
        record = _assemble_record(
            content=_truncate(body, MAX_SUMMARY_CHARS),
            namespace=self._namespace,
            repo=self._repo,
            source=self._source,
            session_id=session_id,
            event_id=_summary_segment_id(session_id, self._all_ids),
            ts_iso=ts.isoformat() if ts is not None else "",
            idempotency_fn=self._idempotency_fn,
            record_factory=self._record_factory,
            phase=_derive_phase(self._all_phases),
        )
        self._spool.append(record)
        self.appended += 1
        self._summary_emitted = True

    def _compose_summary(self) -> str:
        """Deterministic structural compression: decisions + tool outcomes + files.

        No LLM/ML — ingestion runs headless/offline, so the summary is a plain,
        order-preserving concatenation, identical byte-for-byte across re-observation.
        """
        sections: list[str] = []
        if self._decisions:
            sections.append("Decisions:\n" + "\n".join(f"- {d}" for d in self._decisions))
        if self._tool_outcomes:
            sections.append("Tools:\n" + "\n".join(f"- {t}" for t in self._tool_outcomes))
        if self._touched_files:
            sections.append("Files touched:\n" + "\n".join(f"- {f}" for f in self._touched_files))
        if not sections:
            return ""
        return "Session summary\n\n" + "\n\n".join(sections)

    async def flush(self) -> None:
        # Drain the trailing partial work-unit; idempotent (second call sees empty buffer).
        self._flush_segment()

    async def close(self) -> None:
        await self.flush()


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
    """Outcome of observing one session.

    ``appended`` counts **composed** episodes written to the spool — one per coherent
    work-unit plus at most one session summary — not one per event (Epic E2). ``parsed``
    still counts raw events read from the source; ``skipped`` counts events that
    contributed no content to any work-unit (harness noise, empty turns, bare boundary
    signals).
    """

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
    phase_resolver: Callable[[Any], tuple[bool, Any]] | None = None,
) -> ObserveResult:
    """Observe one session: adapter → ``EventPipeline`` → :class:`GraphitiSink` → spool.

    The namespace/repo are resolved **once** from the session's own cwd (``cwd``
    override, else read from ``session.start``) via ``mcp.namespace.resolve_context`` —
    the exact function ``memory_recall`` uses, so ingested memory is findable.
    ``enable_phase``/``enable_boundary`` come from :class:`~memrelay.config.IngestConfig`
    (default off). Phase is opt-in and routed through :func:`phase_guard.resolve_phase`
    (injectable via ``phase_resolver`` for tests): on success the warm inferencer is
    handed to the pipeline; if the model bundle or ML deps are missing it logs and runs
    this pass phase-off rather than crashing. ``governance=None`` keeps observation
    opt-out (SPEC §3.3).
    """
    from traceforge import Enricher, EventPipeline

    from memrelay.config import load_config
    from memrelay.ingest.phase_guard import resolve_phase
    from memrelay.mcp.namespace import resolve_context
    from memrelay.providers.registry import DEFAULT_PROVIDER_ID, get_registry

    cfg = config if config is not None else load_config()
    provider = provider if provider is not None else get_registry().create(DEFAULT_PROVIDER_ID)

    resolved_cwd = cwd if cwd is not None else resolve_session_cwd(events_path)
    namespace, repo = resolve_context(resolved_cwd, namespace_map)

    resolve = phase_resolver or resolve_phase
    phase_enabled, phase_inferencer = resolve(cfg)

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
        phase_inferencer=phase_inferencer,
        enable_phase=phase_enabled,
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
