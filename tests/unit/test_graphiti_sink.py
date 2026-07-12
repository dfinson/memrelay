"""Unit tests for :mod:`memrelay.ingest.graphiti_sink` — the E2 episode assembler.

These tests inject a duck-typed fake spool, a fake ``idempotency_fn``, and a fake
``record_factory`` so they run green independently of session B's ``ingest/spool.py`` +
``ingest/episode.py``. Every ``SessionEvent`` is constructed directly so kind, payload,
visibility, and boundary metadata are under full control — the real pipeline/enricher is
exercised in the ``run_observe`` test and the integration suite, not here. Coroutines are
driven with ``asyncio.run`` (the suite does not depend on pytest-asyncio).

Coverage map:
* #26 kind→content renderers: user (verbatim), assistant (decision, empty-tolerant),
  tool.call.completed (intent+outcome+files+truncated result), file.edited.
* #25 buffering + structural flush: one composed episode per boundary signal
  (tool.call.completed / turn.ended / session.idle / session.ended), NOT per event;
  metadata boundary/activity_id change flushes the prior unit; end-of-stream drain via
  ``flush``/``close`` (idempotent — safe to call twice).
* #27 deterministic session summary on session.ended.
* Composed-episode idempotency stability across re-observation.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from traceforge.classify.workflow import Phase
from traceforge.types import EventMetadata, SessionEvent, ToolMotivation

from memrelay.ingest.graphiti_sink import (
    DEFAULT_SOURCE,
    MAX_RESULT_CHARS,
    TRUNCATION_MARKER,
    GraphitiSink,
    _extract_content,
    _render_file,
    _render_tool,
    build_episode_record,
    resolve_session_cwd,
    run_observe,
)

TS = datetime(2026, 6, 29, 17, 47, 20, 904000, tzinfo=UTC)


class FakeSpool:
    """Duck type for session B's ``Spool`` — collects appended records for asserts."""

    def __init__(self) -> None:
        self.records: list[dict] = []

    def append(self, record: dict) -> None:
        self.records.append(record)


def _fake_idem(session_id: str | None, event_id: str | None, content: str) -> str:
    """Deterministic stand-in for session B's ``make_idempotency_key``."""
    return f"K|{session_id}|{event_id}|{content}"


def _fake_factory(**fields: object) -> dict:
    """Stand-in for session B's ``EpisodeRecord.new`` — returns the plain field dict."""
    return dict(fields)


def _event(
    *,
    kind: str = "message.user",
    content: object = "hello world",
    visibility: str = "visible",
    session_id: str = "sess-1",
    raw_id: str | None = "wire-3",
    ts: datetime = TS,
    payload: dict | None = None,
    boundary: str | None = None,
    activity_id: str | None = None,
    motivation_intent: str | None = None,
) -> SessionEvent:
    """Construct a fully-controlled ``SessionEvent`` for the assembler under test."""
    if payload is None:
        payload = {} if content is None else {"content": content}
    raw_event = None if raw_id is None else {"id": raw_id}
    meta_kwargs: dict = {"visibility": visibility}
    if boundary is not None:
        meta_kwargs["boundary"] = boundary
    if activity_id is not None:
        meta_kwargs["activity_id"] = activity_id
    if motivation_intent is not None:
        meta_kwargs["motivation"] = ToolMotivation(intent=motivation_intent)
    return SessionEvent(
        kind=kind,
        session_id=session_id,
        timestamp=ts,
        payload=payload,
        raw_event=raw_event,
        metadata=EventMetadata(**meta_kwargs),
    )


def _tool(
    *,
    tool_name: str = "pytest",
    success: bool = True,
    result: str = "42 passed",
    arguments: dict | None = None,
    intent: str | None = None,
    raw_id: str = "wire-tool",
    visibility: str = "visible",
) -> SessionEvent:
    payload = {"tool_name": tool_name, "success": success, "result": result}
    if arguments is not None:
        payload["arguments"] = arguments
    return _event(
        kind="tool.call.completed",
        payload=payload,
        raw_id=raw_id,
        visibility=visibility,
        motivation_intent=intent,
    )


def _sink(spool: FakeSpool, **kwargs) -> GraphitiSink:
    kwargs.setdefault("namespace", "acme")
    kwargs.setdefault("repo", "acme/widgets")
    kwargs.setdefault("idempotency_fn", _fake_idem)
    kwargs.setdefault("record_factory", _fake_factory)
    return GraphitiSink(spool, **kwargs)


def _with_phase(event: SessionEvent, phase: Phase) -> SessionEvent:
    """Stamp ``metadata.phase`` on a constructed event, mimicking the live pipeline.

    traceforge's ``EventPipeline`` stamps ``metadata.phase`` on each event *before*
    fan-out to the sink; these tests reproduce that stamp directly (no model) so the
    sink's derivation (F1) is exercised hermetically.
    """
    meta = event.metadata.model_copy(update={"phase": phase})
    return event.model_copy(update={"metadata": meta})


def _drive(sink: GraphitiSink, *events: SessionEvent, flush: bool = False) -> None:
    """Push events through the sink's async ``on_event``; optionally drain at the end."""

    async def _run() -> None:
        for event in events:
            await sink.on_event(event)
        if flush:
            await sink.flush()

    asyncio.run(_run())


# --------------------------------------------------------------------------- #26 renderers


def test_user_message_renders_verbatim() -> None:
    spool = FakeSpool()
    sink = _sink(spool)
    _drive(sink, _event(content="remember the API key rotation"), flush=True)

    assert sink.appended == 1
    record = spool.records[0]
    assert record["content"] == "remember the API key rotation"
    assert record["namespace"] == "acme"
    assert record["repo"] == "acme/widgets"
    assert record["source"] == "copilot"
    assert record["session_id"] == "sess-1"


def test_assistant_decision_is_rendered() -> None:
    spool = FakeSpool()
    sink = _sink(spool)
    _drive(sink, _event(kind="message.assistant", content="I will cache the token"), flush=True)
    assert spool.records[0]["content"] == "I will cache the token"


def test_assistant_empty_content_contributes_nothing() -> None:
    spool = FakeSpool()
    sink = _sink(spool)
    _drive(
        sink,
        _event(content="do the thing", raw_id="u1"),
        _event(kind="message.assistant", content="", raw_id="a1"),
        flush=True,
    )
    # Only the user text made it into the single composed work-unit.
    assert len(spool.records) == 1
    assert spool.records[0]["content"] == "do the thing"


def test_tool_render_includes_intent_outcome_files_and_result() -> None:
    spool = FakeSpool()
    sink = _sink(spool)
    _drive(
        sink,
        _tool(
            tool_name="edit",
            success=True,
            result="wrote 3 lines",
            arguments={"path": "src/app.py"},
            intent="add the retry loop",
        ),
    )  # tool.call.completed flushes on its own
    assert sink.appended == 1
    content = spool.records[0]["content"]
    assert "Tool: edit" in content
    assert "Intent: add the retry loop" in content
    assert "Outcome: succeeded" in content
    assert "Files: src/app.py" in content
    assert "Result: wrote 3 lines" in content


def test_tool_failure_outcome_and_missing_intent() -> None:
    spool = FakeSpool()
    sink = _sink(spool)
    _drive(sink, _tool(tool_name="shell", success=False, result="boom", intent=None))
    content = spool.records[0]["content"]
    assert "Tool: shell" in content
    assert "Outcome: failed" in content
    assert "Intent:" not in content  # absent motivation → no Intent line


def test_tool_result_is_truncated() -> None:
    spool = FakeSpool()
    sink = _sink(spool)
    big = "x" * (MAX_RESULT_CHARS + 500)
    _drive(sink, _tool(result=big))
    content = spool.records[0]["content"]
    assert TRUNCATION_MARKER in content
    # The Result line is bounded: full content is name+outcome+bounded-result, well under 2x.
    assert len(content) < MAX_RESULT_CHARS + 200


def test_file_edited_renders_path_and_operation() -> None:
    spool = FakeSpool()
    sink = _sink(spool)
    _drive(
        sink,
        _event(kind="file.edited", payload={"path": "a/b.py", "operation": "create"}),
        flush=True,
    )
    assert spool.records[0]["content"] == "Changed file: a/b.py (create)"


def test_touched_files_mined_from_list_arguments() -> None:
    spool = FakeSpool()
    sink = _sink(spool)
    _drive(sink, _tool(arguments={"files": ["one.py", "two.py", "one.py"]}))
    content = spool.records[0]["content"]
    assert "Files: one.py, two.py" in content  # ordered-unique


# ----------------------------------------------------------------- #25 buffering + flush


@pytest.mark.parametrize(
    "boundary_event",
    [
        _event(kind="turn.ended", content=None, raw_id="te", visibility="system"),
        _event(kind="session.idle", content=None, raw_id="si", visibility="system"),
        _event(kind="session.ended", content=None, raw_id="se", visibility="system"),
    ],
)
def test_flush_on_each_structural_boundary(boundary_event: SessionEvent) -> None:
    # Real boundary events (turn/session lifecycle) carry SYSTEM visibility. The visible-only
    # filter must gate CONTENT RENDERING only — the structural flush signal fires on kind
    # regardless of visibility, or the buffered work-unit would be silently dropped.
    spool = FakeSpool()
    sink = _sink(spool)
    _drive(sink, _event(content="a decision was made", raw_id="u1"), boundary_event)
    # The buffered user message is flushed by the boundary — before any end-of-stream drain.
    assert spool.records[0]["content"] == "a decision was made"
    assert spool.records[0]["idempotency_key"].startswith("K|sess-1|")


def test_system_visibility_turn_ended_still_flushes() -> None:
    """CRITICAL (visibility ⟂ flush): a SYSTEM-visibility ``turn.ended`` flushes the unit.

    Guards against a regression where the visible-only filter early-returns on non-visible
    events and swallows the boundary signal — which would strand the buffered work-unit.
    """
    spool = FakeSpool()
    sink = _sink(spool)
    _drive(
        sink,
        _event(content="buffered work", raw_id="u1"),
        _event(kind="turn.ended", content=None, raw_id="te", visibility="system"),
    )
    assert sink.appended == 1
    assert spool.records[0]["content"] == "buffered work"


def test_tool_call_completed_is_itself_a_boundary() -> None:
    spool = FakeSpool()
    sink = _sink(spool)
    # No turn.ended needed: the tool completion closes the work-unit.
    _drive(sink, _event(content="ctx", raw_id="u1"), _tool())
    assert sink.appended == 1
    assert "Tool: pytest" in spool.records[0]["content"]


def test_non_boundary_events_buffer_into_one_episode() -> None:
    spool = FakeSpool()
    sink = _sink(spool)
    _drive(
        sink,
        _event(content="first", raw_id="u1"),
        _event(kind="message.assistant", content="second", raw_id="a1"),
        _event(content="third", raw_id="u2"),
    )
    # Nothing flushed yet — no boundary signal seen.
    assert spool.records == []
    _drive(sink, flush=True)
    assert len(spool.records) == 1
    assert spool.records[0]["content"] == "first\n\nsecond\n\nthird"


def test_activity_id_change_flushes_prior_unit() -> None:
    spool = FakeSpool()
    sink = _sink(spool)
    _drive(
        sink,
        _event(content="unit A", raw_id="u1", activity_id="A"),
        _event(content="unit B", raw_id="u2", activity_id="B"),
        flush=True,
    )
    assert [r["content"] for r in spool.records] == ["unit A", "unit B"]


def test_boundary_metadata_flag_flushes_prior_unit() -> None:
    spool = FakeSpool()
    sink = _sink(spool)
    _drive(
        sink,
        _event(content="before", raw_id="u1"),
        _event(content="after", raw_id="u2", boundary="activity-boundary"),
        flush=True,
    )
    assert [r["content"] for r in spool.records] == ["before", "after"]


def test_end_of_stream_drain_and_double_flush_is_idempotent() -> None:
    spool = FakeSpool()
    sink = _sink(spool)

    async def _run() -> tuple:
        await sink.on_event(_event(content="tail", raw_id="u1"))
        first = await sink.flush()
        second = await sink.flush()
        third = await sink.close()
        return first, second, third

    results = asyncio.run(_run())
    assert results == (None, None, None)
    # Drained exactly once; the second flush + close find an empty buffer.
    assert len(spool.records) == 1
    assert spool.records[0]["content"] == "tail"


def test_empty_sink_flush_and_close_emit_nothing() -> None:
    spool = FakeSpool()
    sink = _sink(spool)

    async def _run() -> None:
        await sink.flush()
        await sink.close()

    asyncio.run(_run())
    assert spool.records == []
    assert sink.appended == 0


def test_non_visible_content_is_skipped_but_boundary_still_fires() -> None:
    spool = FakeSpool()
    sink = _sink(spool)
    # A system-visibility tool completion contributes no content, but still closes the unit.
    _drive(
        sink,
        _event(content="visible ctx", raw_id="u1"),
        _tool(visibility="system", raw_id="t-sys"),
    )
    assert sink.appended == 1
    assert spool.records[0]["content"] == "visible ctx"  # tool content excluded (system)


# --------------------------------------------------------------------------- #27 summary


def _session_with_summary() -> tuple[SessionEvent, ...]:
    return (
        _event(content="please add retries", raw_id="u1"),
        _event(kind="message.assistant", content="I will add a retry loop", raw_id="a1"),
        _tool(
            tool_name="edit",
            success=True,
            arguments={"path": "app.py"},
            intent="edit app",
            raw_id="t1",
        ),
        _event(kind="turn.ended", content=None, raw_id="te1"),
        _event(kind="session.ended", content=None, raw_id="se1", visibility="system"),
    )


def test_session_ended_emits_summary_episode() -> None:
    spool = FakeSpool()
    sink = _sink(spool)
    _drive(sink, *_session_with_summary())
    # work-unit (user+assistant+tool) flushes at the tool; turn.ended drains nothing;
    # session.ended emits the summary → 2 records total.
    assert sink.appended == 2
    summary = spool.records[-1]["content"]
    assert summary.startswith("Session summary")
    assert "Decisions:" in summary and "I will add a retry loop" in summary
    assert "Tools:" in summary and "edit succeeded" in summary
    assert "Files touched:" in summary and "app.py" in summary


def test_system_visibility_session_ended_still_summarizes() -> None:
    """CRITICAL (visibility ⟂ summary): the #27 summary keys off ``session.ended``, which

    is SYSTEM-visibility in real sessions. The end-of-stream ``flush`` drains the last
    buffer either way, but ONLY the ``session.ended`` signal emits the summary — so if the
    visible-only filter swallowed it, #27 would silently never fire.
    """
    spool = FakeSpool()
    sink = _sink(spool)
    _drive(
        sink,
        _event(kind="message.assistant", content="decided to ship it", raw_id="a1"),
        _event(kind="session.ended", content=None, raw_id="se", visibility="system"),
    )
    # The assistant work-unit flushes at session.ended, then the summary is emitted.
    assert sink.appended == 2
    assert spool.records[-1]["content"].startswith("Session summary")
    assert "decided to ship it" in spool.records[-1]["content"]


def test_summary_is_deterministic_across_runs() -> None:
    def run() -> dict:
        spool = FakeSpool()
        _drive(_sink(spool), *_session_with_summary())
        return spool.records[-1]

    a, b = run(), run()
    assert a["content"] == b["content"]
    assert a["idempotency_key"] == b["idempotency_key"]


def test_no_summary_without_session_ended() -> None:
    spool = FakeSpool()
    sink = _sink(spool)
    _drive(
        sink,
        _event(content="ask", raw_id="u1"),
        _tool(raw_id="t1"),
        _event(kind="turn.ended", content=None, raw_id="te1"),
        flush=True,
    )
    assert all(not r["content"].startswith("Session summary") for r in spool.records)


# ------------------------------------------------------------- composed-episode idempotency


def test_composed_idempotency_stable_across_reobservation() -> None:
    events = _session_with_summary()

    def keys() -> list[str]:
        spool = FakeSpool()
        _drive(_sink(spool), *events)
        return [r["idempotency_key"] for r in spool.records]

    first, second = keys(), keys()
    assert first == second
    # The composed key threads the segment id (a hash of the span's wire ids).
    assert all(k.startswith("K|sess-1|") for k in first)


def test_different_wire_ids_change_the_composed_key() -> None:
    def key_for(uid: str) -> str:
        spool = FakeSpool()
        _drive(_sink(spool), _event(content="same text", raw_id=uid), flush=True)
        return spool.records[0]["idempotency_key"]

    assert key_for("wire-A") != key_for("wire-B")


# ------------------------------------------------------------------ pure helper + pipeline


def test_build_episode_record_shape() -> None:
    """The single-event record builder is a pure mapping over the frozen episode schema."""
    record = build_episode_record(
        _event(content="note this"),
        namespace="acme",
        repo=None,
        content="note this",
        idempotency_fn=_fake_idem,
        record_factory=_fake_factory,
    )
    assert record == {
        "content": "note this",
        "namespace": "acme",
        "repo": None,
        "source": DEFAULT_SOURCE,
        "session_id": "sess-1",
        "event_id": "wire-3",
        "ts": TS.isoformat(),
        "idempotency_key": "K|sess-1|wire-3|note this",
        "phase": None,
    }


def test_run_observe_over_fixture_composes_episodes(copilot_fixture) -> None:
    """End-to-end through the real pipeline: fixture → composed episodes + a summary.

    The fixture's cwd is redacted, so the namespace resolves via the OS-username
    fallback — deterministic without touching git. Namespace resolution from a real git
    remote is covered in the integration test.
    """
    spool = FakeSpool()
    result = asyncio.run(
        run_observe(
            copilot_fixture,
            "fixture-session",
            spool=spool,
            idempotency_fn=_fake_idem,
            record_factory=_fake_factory,
        )
    )

    # The single-turn fixture composes into two work-units (a message+tool step and the
    # file change) plus one session summary — not one episode per event.
    assert result.appended == 3
    assert len(spool.records) == 3
    assert result.parsed == 14
    assert result.skipped >= 1
    # Composition: the first work-unit carries the previously-dropped tool detail.
    assert "Tool:" in spool.records[0]["content"]
    # The last record is the deterministic session summary.
    assert spool.records[-1]["content"].startswith("Session summary")
    for record in spool.records:
        assert record["source"] == "copilot"
        assert record["session_id"] == "fixture-session"
        assert record["content"]
        assert record["repo"] is None


# ---------------------------------------------------------- phase derivation (E2-S6 #98)


def test_single_phase_span_derives_that_phase() -> None:
    spool = FakeSpool()
    sink = _sink(spool)
    _drive(
        sink,
        _with_phase(_event(kind="message.user", content="plan the work"), Phase.PLANNING),
        _with_phase(_event(kind="message.assistant", content="here is the plan"), Phase.PLANNING),
        flush=True,
    )
    assert sink.appended == 1
    assert spool.records[0]["phase"] == "planning"


def test_multi_phase_span_derives_majority() -> None:
    spool = FakeSpool()
    sink = _sink(spool)
    _drive(
        sink,
        _with_phase(_event(kind="message.user", content="a"), Phase.PLANNING),
        _with_phase(_event(kind="message.assistant", content="b"), Phase.IMPLEMENTATION),
        _with_phase(_event(kind="message.assistant", content="c"), Phase.IMPLEMENTATION),
        flush=True,
    )
    # 2 implementation vs 1 planning -> dominant phase wins.
    assert spool.records[0]["phase"] == "implementation"


def test_tie_breaks_to_most_recent_phase() -> None:
    spool = FakeSpool()
    sink = _sink(spool)
    _drive(
        sink,
        _with_phase(_event(kind="message.user", content="a"), Phase.PLANNING),
        _with_phase(_event(kind="message.assistant", content="b"), Phase.IMPLEMENTATION),
        flush=True,
    )
    # 1 vs 1 -> tie broken in favour of the most-recent phase (implementation).
    assert spool.records[0]["phase"] == "implementation"


def test_phase_off_leaves_phase_none() -> None:
    spool = FakeSpool()
    sink = _sink(spool)
    _drive(
        sink,
        _event(kind="message.user", content="plan the work"),
        _event(kind="message.assistant", content="here is the plan"),
        flush=True,
    )
    assert spool.records[0]["phase"] is None


def test_phase_is_a_pure_sidecar_content_and_key_unchanged() -> None:
    """Toggling phase must not change composed content or the idempotency key (F2).

    Same events, once phase-off and once phase-on: the sole difference is the sidecar
    ``phase`` field. ``content`` and ``idempotency_key`` are byte-identical, so enabling
    phase can never double-ingest or drift an episode's key.
    """
    events = (
        ("message.user", "plan it", "wire-1"),
        ("message.assistant", "do it", "wire-2"),
    )

    off_spool = FakeSpool()
    _drive(
        _sink(off_spool),
        *(_event(kind=k, content=c, raw_id=r) for k, c, r in events),
        flush=True,
    )
    on_spool = FakeSpool()
    _drive(
        _sink(on_spool),
        _with_phase(
            _event(kind="message.user", content="plan it", raw_id="wire-1"), Phase.PLANNING
        ),
        _with_phase(
            _event(kind="message.assistant", content="do it", raw_id="wire-2"),
            Phase.IMPLEMENTATION,
        ),
        flush=True,
    )

    off, on = off_spool.records[0], on_spool.records[0]
    assert off["phase"] is None and on["phase"] == "implementation"
    assert on["content"] == off["content"], "phase must not alter composed content"
    assert on["idempotency_key"] == off["idempotency_key"], "phase must not alter the key"


def test_summary_uses_session_dominant_phase() -> None:
    """The #27 summary episode gets the session-wide dominant phase, not the last segment's."""
    spool = FakeSpool()
    sink = _sink(spool)
    _drive(
        sink,
        _with_phase(_event(kind="message.assistant", content="d1"), Phase.IMPLEMENTATION),
        _with_phase(_tool(result="ok", raw_id="wire-tool"), Phase.IMPLEMENTATION),  # flush seg 1
        _with_phase(_event(kind="message.assistant", content="d2"), Phase.PLANNING),
        _with_phase(_event(kind="session.ended", content=None), Phase.PLANNING),  # flush + summary
    )

    assert len(spool.records) == 3
    assert spool.records[0]["phase"] == "implementation", "seg 1: two implementation events"
    assert spool.records[1]["phase"] == "planning", "seg 2: the trailing planning message"
    summary = spool.records[2]
    assert summary["content"].startswith("Session summary")
    # Session content phases = [impl, impl, planning] -> dominant is implementation, even
    # though the *last* composed segment was planning. Proves session-wide derivation.
    assert summary["phase"] == "implementation"


# ------------------------------------------------------- renderer/guard branch coverage


def test_extract_content_returns_empty_for_non_mapping_payload() -> None:
    # Defensive boundary: a malformed event whose payload isn't a Mapping yields no content
    # rather than raising (the guard at the top of _extract_content).
    assert _extract_content(SimpleNamespace(payload=["not", "a", "mapping"])) == ""
    assert _extract_content(SimpleNamespace()) == ""  # payload attribute entirely absent


def test_render_tool_omits_outcome_when_success_is_not_boolean() -> None:
    # A non-bool ``success`` is ambiguous, so the Outcome line is dropped rather than guessed;
    # the rest of the tool block is still rendered.
    rendered = _render_tool(
        _event(
            kind="tool.call.completed",
            payload={"tool_name": "grep", "success": None, "result": "3 hits"},
        )
    )

    assert rendered.startswith("Tool: grep")
    assert "Outcome:" not in rendered
    assert "Result: 3 hits" in rendered


def test_render_tool_omits_result_when_blank_or_missing() -> None:
    blank = _render_tool(
        _event(kind="tool.call.completed", payload={"tool_name": "ls", "result": "   "})
    )
    missing = _render_tool(_event(kind="tool.call.completed", payload={"tool_name": "ls"}))

    assert "Result:" not in blank
    assert "Result:" not in missing
    assert blank == "Tool: ls"  # only the name survives when nothing else is carried


def test_render_file_returns_none_for_blank_or_missing_path() -> None:
    assert _render_file(_event(kind="file.edited", payload={"path": "   "})) is None
    assert _render_file(_event(kind="file.edited", payload={"operation": "created"})) is None


def test_denied_kind_content_is_dropped() -> None:
    # deny_kinds is applied before allow_kinds: a denied kind contributes no content and is
    # counted as skipped, so no episode is composed for it.
    spool = FakeSpool()
    sink = _sink(spool, deny_kinds={"message.user"})

    _drive(sink, _event(kind="message.user", content="secret note"), flush=True)

    assert sink.appended == 0
    assert sink.skipped == 1
    assert spool.records == []


# ------------------------------------------------------- summary emission guards (#27)


def test_session_ended_without_prior_content_emits_no_summary() -> None:
    # The summary guard short-circuits when no content was ever seen.
    spool = FakeSpool()
    sink = _sink(spool)

    _drive(sink, _event(kind="session.ended", content=None))

    assert sink.appended == 0
    assert spool.records == []


def test_user_only_session_emits_episode_but_no_summary() -> None:
    # Content WAS seen (guard passes) but a lone user message yields no decisions/tools/files,
    # so the composed summary body is empty and no summary episode is emitted.
    spool = FakeSpool()
    sink = _sink(spool)

    _drive(
        sink,
        _event(content="just a note", raw_id="u1"),
        _event(kind="session.ended", content=None, raw_id="e1"),
    )

    assert [r["content"] for r in spool.records] == ["just a note"]


def test_summary_emitted_at_most_once() -> None:
    # A second session.ended must not emit a duplicate summary (the _summary_emitted latch).
    spool = FakeSpool()
    sink = _sink(spool)

    _drive(
        sink,
        *_session_with_summary(),
        _event(kind="session.ended", content=None, raw_id="se2"),
    )

    summaries = [r for r in spool.records if r["content"].startswith("Session summary")]
    assert len(summaries) == 1


def test_summary_dedupes_repeated_touched_file() -> None:
    # The same file touched by a tool and then a file.edited is listed once (dedup early-out
    # in _add_touched_file), not duplicated in the Files touched section.
    spool = FakeSpool()
    sink = _sink(spool)

    _drive(
        sink,
        _event(kind="message.assistant", content="patch it", raw_id="a1"),
        _tool(tool_name="edit", arguments={"path": "app.py"}, result="ok", raw_id="t1"),
        _event(
            kind="file.edited",
            payload={"path": "app.py", "operation": "modified"},
            raw_id="f1",
        ),
        _event(kind="session.ended", content=None, raw_id="e1"),
    )

    summary = next(
        r["content"] for r in spool.records if r["content"].startswith("Session summary")
    )
    assert summary.count("app.py") == 1


# ------------------------------------------------------- resolve_session_cwd robustness (#10)


def test_resolve_session_cwd_skips_noise_and_returns_first_start_cwd(tmp_path) -> None:
    # Blank, malformed, and non-session.start lines are all skipped before the real
    # session.start record supplies data.context.cwd.
    path = tmp_path / "events.jsonl"
    path.write_text(
        "\n"
        "{not valid json\n"
        + json.dumps({"type": "session.idle"})
        + "\n"
        + json.dumps({"type": "session.start", "data": {"context": {"cwd": "/work/repo"}}})
        + "\n",
        encoding="utf-8",
    )

    assert resolve_session_cwd(path) == "/work/repo"


def test_resolve_session_cwd_none_when_no_session_start(tmp_path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text(json.dumps({"type": "message.user"}) + "\n", encoding="utf-8")

    assert resolve_session_cwd(path) is None


def test_resolve_session_cwd_none_when_cwd_not_a_string(tmp_path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text(
        json.dumps({"type": "session.start", "data": {"context": {"cwd": 123}}}) + "\n",
        encoding="utf-8",
    )

    assert resolve_session_cwd(path) is None


def test_resolve_session_cwd_none_for_unreadable_file(tmp_path) -> None:
    # A missing file raises OSError internally, which is swallowed to None.
    assert resolve_session_cwd(tmp_path / "does-not-exist.jsonl") is None


# ---------------------------------------------- file-refactor provenance (E9-S3 #60)


class _FakeProc:
    """A completed subprocess stand-in exposing just the surface ``_run_git`` reads."""

    def __init__(self, stdout: str, returncode: int = 0) -> None:
        self._stdout = stdout
        self.returncode = returncode

    def communicate(self) -> tuple[str, str]:
        return (self._stdout, "")


class FakeGit:
    """Injectable ``popen`` that dispatches on the git argv (no real subprocess).

    ``rev-parse HEAD`` yields ``sha`` (or a non-zero exit when ``fail`` is set); ``diff
    --numstat`` yields the raw numstat text configured for the pathspec (``argv[-1]``),
    defaulting to empty output (i.e. no churn) for unconfigured paths. Every call's argv is
    recorded so tests can assert both *what* git was asked and *how often*.
    """

    def __init__(
        self,
        *,
        sha: str = "sha-new",
        numstat: dict[str, str] | None = None,
        fail: bool = False,
    ) -> None:
        self.sha = sha
        self.numstat = numstat or {}
        self.fail = fail
        self.calls: list[list[str]] = []

    def __call__(self, argv, **_kwargs) -> _FakeProc:
        argv = list(argv)
        self.calls.append(argv)
        if "rev-parse" in argv:
            if self.fail:
                return _FakeProc("", returncode=1)
            return _FakeProc(f"{self.sha}\n")
        if "diff" in argv:
            return _FakeProc(self.numstat.get(argv[-1], ""))
        return _FakeProc("")

    @property
    def rev_parse_calls(self) -> int:
        return sum(1 for argv in self.calls if "rev-parse" in argv)


def test_refactor_provenance_stamped_when_enabled() -> None:
    # Knob on + a cwd + a touched file → the composed record carries the HEAD sha and the
    # file's changed-line magnitude (added + deleted from numstat).
    spool = FakeSpool()
    git = FakeGit(sha="sha-new", numstat={"src/a.py": "120\t30\tsrc/a.py"})
    sink = _sink(spool, cwd="/repo", refactor_lines=100, popen=git)

    _drive(sink, _tool(result="ok", arguments={"path": "src/a.py"}))

    assert sink.appended == 1
    record = spool.records[0]
    assert record["last_commit_sha"] == "sha-new"
    assert record["file_change_lines"] == {"src/a.py": 150}


def test_provenance_absent_and_git_unused_when_disabled() -> None:
    # The zero-config default (refactor_lines == 0) must never spawn git and must leave the
    # record byte-identical: the two provenance keys are simply absent (never forwarded).
    spool = FakeSpool()
    git = FakeGit(numstat={"src/a.py": "120\t30\tsrc/a.py"})
    sink = _sink(spool, cwd="/repo", popen=git)  # refactor_lines defaults to 0

    _drive(sink, _tool(result="ok", arguments={"path": "src/a.py"}))

    record = spool.records[0]
    assert "last_commit_sha" not in record
    assert "file_change_lines" not in record
    assert git.calls == [], "disabled knob must never spawn git"


def test_provenance_absent_when_no_cwd() -> None:
    # Even with the knob on, no session cwd means no repo to interrogate → no provenance and
    # no git invocation.
    spool = FakeSpool()
    git = FakeGit()
    sink = _sink(spool, refactor_lines=100, popen=git)  # cwd is None

    _drive(sink, _tool(result="ok", arguments={"path": "src/a.py"}))

    record = spool.records[0]
    assert "last_commit_sha" not in record
    assert "file_change_lines" not in record
    assert git.calls == []


def test_head_sha_resolved_once_across_segments() -> None:
    # HEAD is constant for the session's repo state, so it is resolved once and cached even
    # as multiple work-units flush; per-file churn is still resolved per path.
    spool = FakeSpool()
    git = FakeGit(sha="sha-new", numstat={"a.py": "5\t5\ta.py", "b.py": "9\t1\tb.py"})
    sink = _sink(spool, cwd="/repo", refactor_lines=1, popen=git)

    _drive(
        sink,
        _tool(result="ok", arguments={"path": "a.py"}, raw_id="w1"),
        _tool(result="ok", arguments={"path": "b.py"}, raw_id="w2"),
    )

    assert sink.appended == 2
    assert spool.records[0]["file_change_lines"] == {"a.py": 10}
    assert spool.records[1]["file_change_lines"] == {"b.py": 10}
    assert git.rev_parse_calls == 1, "HEAD sha resolved once and cached for the session"


def test_zero_churn_touched_file_still_records_generation() -> None:
    # A touched file with no churn in HEAD's commit is still stamped with the sha (magnitude
    # 0), recording this episode's generation so a *later* big refactor can supersede it. The
    # changed-line threshold gate lives in the engine, not the sink.
    spool = FakeSpool()
    git = FakeGit(sha="sha-new", numstat={})  # no numstat line for the path → churn 0
    sink = _sink(spool, cwd="/repo", refactor_lines=100, popen=git)

    _drive(sink, _tool(result="ok", arguments={"path": "src/a.py"}))

    record = spool.records[0]
    assert record["last_commit_sha"] == "sha-new"
    assert record["file_change_lines"] == {"src/a.py": 0}


def test_summary_episode_also_carries_file_provenance() -> None:
    # The #27 session summary is a file-scoped episode too (it lists touched files), so it
    # carries the same provenance over the session-wide touched-file set.
    spool = FakeSpool()
    git = FakeGit(sha="sha-new", numstat={"src/a.py": "200\t0\tsrc/a.py"})
    sink = _sink(spool, cwd="/repo", refactor_lines=100, popen=git)

    _drive(
        sink,
        _tool(result="ok", arguments={"path": "src/a.py"}, raw_id="w1"),
        _event(kind="session.ended", content=None, raw_id="e1"),
    )

    summary = spool.records[-1]
    assert summary["content"].startswith("Session summary")
    assert summary["last_commit_sha"] == "sha-new"
    assert summary["file_change_lines"] == {"src/a.py": 200}


def test_git_failure_yields_no_provenance() -> None:
    # A git that cannot resolve HEAD (root commit, not a repo, git absent) must never crash
    # ingest: the flush proceeds with no provenance stamped.
    spool = FakeSpool()
    git = FakeGit(fail=True)  # rev-parse exits non-zero
    sink = _sink(spool, cwd="/repo", refactor_lines=100, popen=git)

    _drive(sink, _tool(result="ok", arguments={"path": "src/a.py"}))

    assert sink.appended == 1
    record = spool.records[0]
    assert "last_commit_sha" not in record
    assert "file_change_lines" not in record
