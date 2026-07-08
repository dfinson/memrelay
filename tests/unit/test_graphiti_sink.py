"""Unit tests for :mod:`memrelay.ingest.graphiti_sink` (fake spool, no session B).

These tests inject a duck-typed fake spool and a fake ``idempotency_fn`` so they run
green independently of session B's ``ingest/spool.py`` + ``ingest/episode.py`` (which
may not be merged yet). Every ``SessionEvent`` is constructed directly so visibility,
kind, and payload are under full control — the pipeline/enricher is exercised in the
``run_observe`` test and the integration test, not here. Coroutines are driven with
``asyncio.run`` (the suite does not depend on pytest-asyncio).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from traceforge.types import EventMetadata, SessionEvent

from memrelay.ingest.graphiti_sink import (
    DEFAULT_SOURCE,
    GraphitiSink,
    build_episode_record,
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


def _event(
    *,
    kind: str = "message.user",
    content: object = "hello world",
    visibility: str = "visible",
    session_id: str = "sess-1",
    raw_id: str | None = "wire-3",
    ts: datetime = TS,
    payload: dict | None = None,
) -> SessionEvent:
    """Construct a fully-controlled ``SessionEvent`` for the sink under test."""
    if payload is None:
        payload = {} if content is None else {"content": content}
    raw_event = None if raw_id is None else {"id": raw_id}
    return SessionEvent(
        kind=kind,
        session_id=session_id,
        timestamp=ts,
        payload=payload,
        raw_event=raw_event,
        metadata=EventMetadata(visibility=visibility),
    )


def _sink(spool: FakeSpool, **kwargs) -> GraphitiSink:
    kwargs.setdefault("namespace", "acme")
    kwargs.setdefault("repo", "acme/widgets")
    kwargs.setdefault("idempotency_fn", _fake_idem)
    return GraphitiSink(spool, **kwargs)


def _drive(sink: GraphitiSink, *events: SessionEvent) -> None:
    """Push events through the sink's async ``on_event`` synchronously."""

    async def _run() -> None:
        for event in events:
            await sink.on_event(event)

    asyncio.run(_run())


def test_visible_message_becomes_exact_record() -> None:
    spool = FakeSpool()
    sink = _sink(spool)

    _drive(sink, _event(content="remember the API key rotation"))

    assert spool.records == [
        {
            "content": "remember the API key rotation",
            "namespace": "acme",
            "repo": "acme/widgets",
            "source": "copilot",
            "session_id": "sess-1",
            "event_id": "wire-3",
            "ts": TS.isoformat(),
            "idempotency_key": "K|sess-1|wire-3|remember the API key rotation",
        }
    ]
    assert sink.appended == 1
    assert sink.skipped == 0


def test_content_is_stripped() -> None:
    spool = FakeSpool()
    sink = _sink(spool)
    _drive(sink, _event(content="  padded  "))
    assert spool.records[0]["content"] == "padded"


@pytest.mark.parametrize("visibility", ["system", "collapsed"])
def test_non_visible_events_are_skipped(visibility: str) -> None:
    spool = FakeSpool()
    sink = _sink(spool)
    _drive(sink, _event(visibility=visibility))
    assert spool.records == []
    assert sink.skipped == 1
    assert sink.appended == 0


def test_empty_or_missing_content_is_skipped() -> None:
    spool = FakeSpool()
    sink = _sink(spool)
    _drive(
        sink,
        _event(content="   "),  # whitespace-only
        _event(content=None),  # no content key
        _event(content=42),  # non-string
    )
    assert spool.records == []
    assert sink.skipped == 3


def test_kind_not_in_allowlist_is_skipped() -> None:
    """``message.system`` is visible + has content, but is harness noise (issue #9)."""
    spool = FakeSpool()
    sink = _sink(spool)
    _drive(sink, _event(kind="message.system", content="you are a helpful..."))
    assert spool.records == []
    assert sink.skipped == 1


def test_assistant_kind_is_allowed_by_default() -> None:
    spool = FakeSpool()
    sink = _sink(spool)
    _drive(sink, _event(kind="message.assistant", content="here's the fix"))
    assert [r["content"] for r in spool.records] == ["here's the fix"]


def test_deny_kinds_overrides_allow() -> None:
    spool = FakeSpool()
    sink = _sink(spool, deny_kinds={"message.user"})
    _drive(sink, _event(kind="message.user"))
    assert spool.records == []
    assert sink.skipped == 1


def test_allow_kinds_none_falls_back_to_visibility_and_content() -> None:
    """With ``allow_kinds=None`` any visible, content-bearing kind is recorded."""
    spool = FakeSpool()
    sink = _sink(spool, allow_kinds=None)
    _drive(sink, _event(kind="tool.call.completed", content="ran pytest"))
    assert [r["content"] for r in spool.records] == ["ran pytest"]


def test_event_id_uses_stable_wire_id_not_parse_uuid() -> None:
    spool = FakeSpool()
    sink = _sink(spool)
    event = _event(raw_id="wire-99")

    _drive(sink, event)

    assert spool.records[0]["event_id"] == "wire-99"
    # ``event.id`` is a fresh per-parse UUID and must never leak into the record.
    assert spool.records[0]["event_id"] != str(event.id)


def test_event_id_is_none_without_raw_id() -> None:
    spool = FakeSpool()
    sink = _sink(spool)
    _drive(sink, _event(raw_id=None))
    assert spool.records[0]["event_id"] is None
    assert spool.records[0]["idempotency_key"] == "K|sess-1|None|hello world"


def test_idempotency_key_is_stable_across_reobservation() -> None:
    """Re-observing the same event yields an identical key (so B's spool de-dupes)."""
    spool = FakeSpool()
    sink = _sink(spool)
    _drive(sink, _event(), _event())
    keys = {r["idempotency_key"] for r in spool.records}
    assert len(spool.records) == 2
    assert keys == {"K|sess-1|wire-3|hello world"}


def test_flush_and_close_are_noops() -> None:
    spool = FakeSpool()
    sink = _sink(spool)

    async def _run() -> tuple:
        return (await sink.flush(), await sink.close())

    assert asyncio.run(_run()) == (None, None)
    assert spool.records == []


def test_build_episode_record_shape() -> None:
    """The record builder is a pure mapping over the frozen episode schema."""
    record = build_episode_record(
        _event(content="note this"),
        namespace="acme",
        repo=None,
        content="note this",
        idempotency_fn=_fake_idem,
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
    }


def test_run_observe_over_fixture_with_fake_spool(copilot_fixture) -> None:
    """End-to-end through the real pipeline: fixture -> visible-only episode records.

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
        )
    )

    # The fixture carries exactly one visible user message with non-empty content;
    # message.system is filtered by kind, message.assistant is empty in the fixture.
    assert result.appended == 1
    assert len(spool.records) == 1
    record = spool.records[0]
    assert record["source"] == "copilot"
    assert record["session_id"] == "fixture-session"
    assert record["content"]  # non-empty
    assert record["repo"] is None
    assert result.parsed == 14
    assert result.skipped >= 1
